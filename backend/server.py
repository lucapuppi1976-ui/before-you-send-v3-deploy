"""FastAPI application: routes, middleware stack and the public/ static files."""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response
from starlette.types import Scope

from . import transcription
from .analysis import (
    AnalysisBusy,
    AnalysisError,
    AnalysisRefused,
    AnalysisUnavailable,
    Analyzer,
    sniff_image_type,
)
from .codes import CodeStore
from .config import MAX_CODE_CHARS, MAX_TEXT_CHARS, MB, PUBLIC_DIR, ConfigError, Settings, load_settings, validate_settings
from .gate import SHARED_CODE_ID, Access, Gate
from .i18n import SUPPORTED_LANGS, canonical_lang, text
from .middleware import BodySizeLimitMiddleware, GuardMiddleware, SecurityHeadersMiddleware
from .ratelimit import DAY, HOUR, MINUTE, RateLimiter
from .transcription import TranscriptionBusy, TranscriptionEmpty, TranscriptionError

log = logging.getLogger("bys")

LOCKED = "Access locked. Enter your access code to continue."
MULTIPART_OVERHEAD = 64 * 1024  # room for form boundaries and the small text fields next to a file


class TextPayload(BaseModel):
    text: str = Field(max_length=MAX_TEXT_CHARS)
    lang: Optional[str] = Field(default="it", max_length=16)


class AccessPayload(BaseModel):
    code: str = Field(max_length=MAX_CODE_CHARS)
    lang: Optional[str] = Field(default=None, max_length=16)


class AdminCodePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["trial", "paid"] = "trial"
    label: Optional[str] = Field(default=None, max_length=200)
    quota: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    days: Optional[int] = Field(default=None, ge=1, le=3650)


class PublicFiles(StaticFiles):
    """Files from public/ only; dotfiles are never served."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        parts = [part for part in path.replace("\\", "/").split("/") if part not in ("", ".")]
        if any(part.startswith(".") for part in parts):
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        if "cache-control" not in response.headers:
            # Icons and demo media rarely change; app code and locales are revalidated on every load.
            response.headers["Cache-Control"] = "public, max-age=86400" if parts[:1] == ["assets"] else "no-cache"
        return response


def api_error(status: int, code: str, message: str, headers: Optional[Mapping[str, str]] = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=dict(headers or {}))


def _iso(timestamp: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def _configure_logging(level: str) -> None:
    logger = logging.getLogger("bys")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)


def create_app(settings: Optional[Settings] = None, *, public_dir: Path = PUBLIC_DIR) -> FastAPI:
    settings = load_settings() if settings is None else settings
    warnings = validate_settings(settings)  # raises ConfigError: refuse to start
    _configure_logging(settings.log_level)
    for warning in warnings:
        log.warning(warning)

    store = CodeStore(settings.db_path)
    try:
        store.initialize()
    except (OSError, sqlite3.Error) as exc:
        raise ConfigError(f"Cannot open the access-code database at {settings.db_path}: {exc}") from exc
    gate = Gate(settings, store)
    analyzer = Analyzer.from_settings(settings)
    access_page = settings.access_page_url
    log.info(
        "BYS starting: gate=%s model=%s effort=%s voice=%s db=%s",
        "on" if settings.gate_enabled else "OFF", settings.model, settings.effort,
        "on" if settings.voice_ready else "off", settings.db_path,
    )

    app = FastAPI(title="Before You Send", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.gate = gate
    app.state.analyzer = analyzer

    # --- helpers -----------------------------------------------------------------------

    def require_analysis(lang: str) -> None:
        if not analyzer.available:
            raise api_error(503, "unavailable", text(lang, "error_ai_unavailable"))

    def trial_ended(lang: str) -> HTTPException:
        return HTTPException(
            status_code=403,
            detail={"code": "trial_ended", "message": text(lang, "trial_ended", url=access_page), "accessPage": access_page},
            headers={"X-BYS-Remaining": "0"},
        )

    def provider_error(exc: Exception, lang: str) -> HTTPException:
        # Generic, localized messages only: upstream error text never reaches the browser.
        if isinstance(exc, AnalysisRefused):
            return api_error(422, "refused", text(lang, "error_refused"))
        if isinstance(exc, (AnalysisBusy, TranscriptionBusy)):
            return api_error(503, "busy", text(lang, "error_busy"), {"Retry-After": "20"})
        if isinstance(exc, AnalysisUnavailable):
            return api_error(503, "unavailable", text(lang, "error_ai_unavailable"))
        if isinstance(exc, TranscriptionEmpty):
            return api_error(422, "empty_audio", text(lang, "error_transcription_empty"))
        if isinstance(exc, TranscriptionError):
            return api_error(502, "transcription_failed", text(lang, "error_transcription"))
        return api_error(502, "analysis_failed", text(lang, "error_generic"))

    def read_upload(upload: Optional[UploadFile], max_bytes: int, lang: str, too_large_key: str) -> Optional[bytes]:
        if upload is None:
            return None
        data = upload.file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise api_error(413, "too_large", text(lang, too_large_key, mb=max_bytes // MB))
        return data or None

    def run_metered(request: Request, lang: str, work: Callable[[], dict[str, Any]]) -> JSONResponse:
        """Consume one analysis before calling the providers; refund it if they fail."""
        access: Optional[Access] = request.scope.get("state", {}).get("bys_access")
        if access is None:  # GuardMiddleware sets it for every AI path
            raise HTTPException(status_code=401, detail=LOCKED)
        remaining: Optional[int] = None
        if access.metered:
            outcome = store.consume(access.code_id)
            if not outcome.ok:
                if outcome.reason == "missing":
                    raise HTTPException(status_code=401, detail=LOCKED)
                raise trial_ended(lang)
            remaining = outcome.record.remaining if outcome.record else None
        try:
            result = work()
        except (AnalysisError, TranscriptionError) as exc:
            if access.metered:
                store.refund(access.code_id)
            raise provider_error(exc, lang) from None
        except Exception:
            if access.metered:
                store.refund(access.code_id)
            raise
        headers = {"X-BYS-Remaining": str(remaining)} if remaining is not None else None
        return JSONResponse(result, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = exc.body
        raw_lang = body.get("lang") if hasattr(body, "get") else None
        lang = canonical_lang(raw_lang if isinstance(raw_lang, str) else None)
        text_fields = {"text", "transcript", "extracted_text"}
        too_long = any(
            error.get("type") == "string_too_long" and text_fields.intersection(map(str, error.get("loc", ())))
            for error in exc.errors()
        )
        if too_long:
            detail = {"code": "too_long", "message": text(lang, "error_text_too_long", max=MAX_TEXT_CHARS)}
        else:
            detail = {"code": "invalid_input", "message": text(lang, "error_invalid_input")}
        return JSONResponse({"detail": detail}, status_code=422)

    # --- public and access endpoints ---------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/access/status")
    def access_status(request: Request) -> dict[str, Any]:
        access, _ = gate.session(request.cookies)
        return {"enabled": gate.enabled, "unlocked": access is not None, "accessPage": access_page}

    @app.post("/api/access/unlock")
    def access_unlock(payload: AccessPayload, request: Request) -> JSONResponse:
        if not gate.enabled:
            return JSONResponse({"ok": True, "enabled": False, "unlocked": True, "accessPage": access_page})
        lang = canonical_lang(payload.lang)
        result = gate.check_code(payload.code)
        if result.status == "invalid":
            log.info("unlock rejected: unknown code")
            raise HTTPException(status_code=403, detail="Invalid access code.")
        if result.status == "ended" or result.access is None:
            log.info("unlock rejected: code id=%s is %s", result.record.id if result.record else "-",
                     result.record.status() if result.record else "-")
            raise trial_ended(lang)
        log.info("unlock ok code id=%s", result.access.code_id)
        response = JSONResponse({"ok": True, "enabled": True, "unlocked": True, "accessPage": access_page})
        response.set_cookie(
            key=settings.cookie_name,
            value=gate.issue_token(result.access.code_id),
            max_age=settings.cookie_days * 86400,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
        )  # no Domain attribute: host-only cookie
        return response

    @app.post("/api/access/logout")
    def access_logout(request: Request) -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            key=settings.cookie_name, path="/", httponly=True, samesite="lax", secure=request.url.scheme == "https"
        )
        return response

    @app.get("/api/config")
    def app_config(request: Request) -> dict[str, Any]:
        access, record = gate.session(request.cookies)
        if access is None:
            raise HTTPException(status_code=401, detail=LOCKED)
        if access.code_id is None:
            plan, remaining = "open", None
        elif access.code_id == SHARED_CODE_ID:
            plan, remaining = "tester", None
        else:
            plan, remaining = (record.plan, record.remaining) if record else ("trial", 0)
        ready = analyzer.available
        return {
            "supports": {"text": ready, "voice": ready and bool(settings.groq_api_key), "image": ready, "shareCard": True},
            "supportedLanguages": sorted(SUPPORTED_LANGS),
            "demoSamples": True,
            "access": {"plan": plan, "remaining": remaining},
        }

    # --- AI endpoints (gate + rate limit already checked by GuardMiddleware) ------------

    @app.post("/api/decode/text")
    def decode_text(request: Request, payload: TextPayload) -> JSONResponse:
        lang = canonical_lang(payload.lang)
        message = payload.text.strip()
        if not message:
            raise api_error(400, "empty_text", text(lang, "error_empty_text"))
        require_analysis(lang)

        def work() -> dict[str, Any]:
            result, _ = analyzer.decode_text(message, lang)
            result.update(source="text", input=message, lang=lang)
            return result

        return run_metered(request, lang, work)

    @app.post("/api/score/text")
    def score_text(request: Request, payload: TextPayload) -> JSONResponse:
        lang = canonical_lang(payload.lang)
        draft = payload.text.strip()
        if not draft:
            raise api_error(400, "empty_text", text(lang, "error_empty_send"))
        require_analysis(lang)

        def work() -> dict[str, Any]:
            result, _ = analyzer.score_text(draft, lang)
            result.update(input=draft, lang=lang)
            return result

        return run_metered(request, lang, work)

    @app.post("/api/decode/audio")
    def decode_audio(
        request: Request,
        file: Optional[UploadFile] = File(default=None),
        transcript: Optional[str] = Form(default=None, max_length=MAX_TEXT_CHARS),
        lang: Optional[str] = Form(default="it", max_length=16),
    ) -> JSONResponse:
        lang = canonical_lang(lang)
        typed = (transcript or "").strip()
        if typed:
            require_analysis(lang)

            def work_typed() -> dict[str, Any]:
                result, _ = analyzer.decode_text(typed, lang)
                result.update(source="voice", input=typed, lang=lang, transcript=typed)
                return result

            return run_metered(request, lang, work_typed)

        if file is None:
            raise api_error(400, "missing_input", text(lang, "error_audio_missing"))
        if not settings.groq_api_key:
            raise api_error(503, "voice_unavailable", text(lang, "error_voice_unavailable"))
        require_analysis(lang)
        upload = transcription.resolve_audio_upload(file.filename, file.content_type)
        if upload is None:
            raise api_error(415, "unsupported_audio", text(lang, "error_audio_type"))
        audio = read_upload(file, settings.max_audio_bytes, lang, "error_audio_too_large")
        if audio is None:
            raise api_error(400, "missing_input", text(lang, "error_audio_missing"))
        upload_name, media_type = upload

        def work() -> dict[str, Any]:
            # No language hint: a received voice note is not necessarily in the UI language.
            heard = transcription.transcribe(
                audio,
                filename=upload_name,
                content_type=media_type,
                api_key=settings.groq_api_key,
                model=settings.transcribe_model,
                timeout_seconds=settings.transcribe_timeout_seconds,
            )
            result, _ = analyzer.decode_text(heard, lang)
            result.update(source="voice", input=heard, lang=lang, transcript=heard)
            return result

        return run_metered(request, lang, work)

    @app.post("/api/decode/image")
    def decode_image(
        request: Request,
        file: Optional[UploadFile] = File(default=None),
        extracted_text: Optional[str] = Form(default=None, max_length=MAX_TEXT_CHARS),
        lang: Optional[str] = Form(default="it", max_length=16),
    ) -> JSONResponse:
        lang = canonical_lang(lang)
        typed = (extracted_text or "").strip()
        image = read_upload(file, settings.max_image_bytes, lang, "error_image_too_large")
        if image is None:
            if not typed:
                raise api_error(400, "missing_input", text(lang, "error_image_missing"))
            require_analysis(lang)

            def work_typed() -> dict[str, Any]:
                result, _ = analyzer.decode_text(typed, lang)
                result.update(source="image", input=typed, lang=lang, extracted_text=typed, extraction_confidence=100)
                return result

            return run_metered(request, lang, work_typed)

        require_analysis(lang)
        media_type = sniff_image_type(image)
        declared = (file.content_type or "").split(";", 1)[0].strip().lower() if file else ""
        if media_type is None or (declared not in ("", "application/octet-stream") and not declared.startswith("image/")):
            raise api_error(415, "unsupported_image", text(lang, "error_image_type"))

        def work() -> dict[str, Any]:
            result, _ = analyzer.decode_image(image, media_type, lang)
            result.update(source="image", input=result["extracted_text"], lang=lang)
            return result

        return run_metered(request, lang, work)

    # --- admin (GuardMiddleware allows only 127.0.0.1, unproxied, with the bearer token) --

    @app.post("/admin/codes", status_code=201)
    def admin_create_code(payload: AdminCodePayload) -> dict[str, Any]:
        quota = payload.quota if payload.quota is not None else (settings.trial_quota if payload.plan == "trial" else None)
        expires_at = int(time.time()) + payload.days * 86400 if payload.days else None
        code, record = store.create(plan=payload.plan, quota=quota, label=payload.label, expires_at=expires_at)
        log.info("admin created code id=%s plan=%s quota=%s", record.id, record.plan, record.quota)
        return {
            "id": record.id,
            "code": code,  # shown only in this response; the database keeps a hash
            "plan": record.plan,
            "quota": record.quota,
            "label": record.label,
            "expiresAt": _iso(record.expires_at),
        }

    # --- static files ------------------------------------------------------------------

    favicon = public_dir / "assets" / "icon-192.png"

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> FileResponse:
        return FileResponse(favicon, media_type="image/png")

    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_json_bytes,
        path_limits={
            "/api/decode/image": settings.max_image_bytes + MULTIPART_OVERHEAD,
            "/api/decode/audio": settings.max_audio_bytes + MULTIPART_OVERHEAD,
        },
    )
    app.add_middleware(
        GuardMiddleware,
        gate=gate,
        ai_limiter=RateLimiter([(settings.ai_per_minute, MINUTE), (settings.ai_per_day, DAY)]),
        unlock_limiter=RateLimiter([(settings.unlock_per_minute, MINUTE), (settings.unlock_per_hour, HOUR)]),
        admin_token=settings.admin_token,
    )
    app.add_middleware(SecurityHeadersMiddleware)  # outermost, so every response gets the headers
    app.mount("/", PublicFiles(directory=str(public_dir), html=True), name="public")
    return app
