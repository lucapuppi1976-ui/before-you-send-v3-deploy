"""FastAPI application: routes, middleware stack and the public/ static files."""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Mapping, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
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
from .config import (
    MAX_CODE_CHARS,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    MAX_TEXT_CHARS,
    MB,
    PUBLIC_DIR,
    ConfigError,
    Settings,
    load_settings,
    validate_settings,
)
from .gate import SHARED_CODE_ID, Access, Gate
from .i18n import SUPPORTED_LANGS, canonical_lang, text
from .middleware import PACK_PATH, BodySizeLimitMiddleware, GuardMiddleware, SecurityHeadersMiddleware
from .payments import CODE_FIELD, TRANSACTION_ID, Paddle, PaymentUnavailable
from .ratelimit import DAY, HOUR, MINUTE, RateLimiter
from .transcription import TranscriptionBusy, TranscriptionEmpty, TranscriptionError, TranscriptionTooLong

log = logging.getLogger("bys")

LOCKED = "Access locked. Enter your access code to continue."
MULTIPART_OVERHEAD = 64 * 1024  # room for form boundaries and the small text fields next to a file
SHARE_TARGET_PATH = "/share-target"  # the share_target action in public/manifest.webmanifest
# Paddle's notifications of a paid pack (signed with the secret of the notification destination, once BYS sells).
WEBHOOK_PATH = "/api/paddle/webhook"
WEBHOOK_EVENTS = ("transaction.paid", "transaction.completed")


class TextPayload(BaseModel):
    text: str = Field(max_length=MAX_TEXT_CHARS)
    lang: Optional[str] = Field(default="it", max_length=16)


class ScorePayload(TextPayload):
    # Optional: the message the draft replies to, so the score can take it into account.
    reply_to: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)


class Turn(BaseModel):
    # One earlier message of the conversation: «them» wrote it to the user, «me» is what the user answered.
    who: Literal["them", "me"]
    text: str = Field(max_length=MAX_TEXT_CHARS)


History = Annotated[list[Turn], Field(max_length=MAX_HISTORY_TURNS)]
HISTORY_FORM = TypeAdapter(History)  # the same list, sent as JSON in a form field with a voice note or a screenshot


class DecodePayload(TextPayload):
    # The earlier messages of the same conversation, oldest first (Luca, 30/10: «lo storico, come in Plantatam»).
    history: History = Field(default_factory=list)


class RepliesPayload(BaseModel):
    # The ready replies as Claude wrote them (in `lang`), the language to write them in, and the message they answer.
    replies: list[Annotated[str, Field(max_length=1000)]] = Field(min_length=1, max_length=3)
    to: str = Field(pattern=r"^[a-z]{2,3}$")
    lang: Optional[str] = Field(default="it", max_length=16)
    context: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)


class AccessPayload(BaseModel):
    code: str = Field(max_length=MAX_CODE_CHARS)
    lang: Optional[str] = Field(default=None, max_length=16)


class PackPayload(BaseModel):
    # The transaction Paddle's checkout reports when the pack is paid (checkout.completed).
    transaction_id: str = Field(max_length=80)
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
    paddle = Paddle.from_settings(settings)
    log.info(
        "BYS starting: gate=%s model=%s effort=%s voice=%s db=%s pack=%s",
        "on" if settings.gate_enabled else "OFF", settings.model, settings.effort,
        "on" if settings.voice_ready else "off", settings.db_path,
        f"paddle {settings.paddle_environment}" if paddle.enabled else "off",
    )

    app = FastAPI(title="Before You Send", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.gate = gate
    app.state.analyzer = analyzer
    app.state.paddle = paddle

    # --- helpers -----------------------------------------------------------------------

    def require_analysis(lang: str) -> None:
        if not analyzer.available:
            raise api_error(503, "unavailable", text(lang, "error_ai_unavailable"))

    def trial_ended(lang: str, reason: Optional[str]) -> HTTPException:
        # reason ("exhausted", "expired" or "revoked") lets the app say whether the analyses are used up or the
        # code is no longer active.
        return HTTPException(
            status_code=403,
            detail={
                "code": "trial_ended",
                "reason": reason if reason in ("exhausted", "expired") else "revoked",
                "message": text(lang, "trial_ended", url=access_page),
                "accessPage": access_page,
            },
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
        if isinstance(exc, TranscriptionTooLong):
            return api_error(422, "voice_too_long", text(lang, "error_voice_too_long", max=MAX_TEXT_CHARS))
        if isinstance(exc, TranscriptionError):
            return api_error(502, "transcription_failed", text(lang, "error_transcription"))
        return api_error(502, "analysis_failed", text(lang, "error_generic"))

    def history_of(turns: list[Turn], lang: str) -> list[dict[str, str]]:
        history = [{"who": turn.who, "text": turn.text.strip()} for turn in turns if turn.text.strip()]
        if sum(len(turn["text"]) for turn in history) > MAX_HISTORY_CHARS:
            raise api_error(422, "too_long", text(lang, "error_text_too_long", max=MAX_HISTORY_CHARS))
        return history

    def history_from_form(raw: Optional[str], lang: str) -> list[dict[str, str]]:
        if not raw:
            return []
        try:
            turns = HISTORY_FORM.validate_json(raw)
        except ValidationError:
            raise api_error(422, "invalid_input", text(lang, "error_invalid_input")) from None
        return history_of(turns, lang)

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
                raise trial_ended(lang, outcome.reason)
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
        text_fields = {"text", "transcript", "extracted_text", "reply_to"}
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
        # With no analyses left the code still opens the app while the pack is for sale: to buy more.
        result = gate.check_code(payload.code, allow_used_up=paddle.enabled)
        if result.status == "invalid":
            log.info("unlock rejected: unknown code")
            raise HTTPException(status_code=403, detail="Invalid access code.")
        if result.status == "ended" or result.access is None:
            status = result.record.status() if result.record else None
            log.info("unlock rejected: code id=%s is %s", result.record.id if result.record else "-", status or "-")
            raise trial_ended(lang, status)
        log.info("unlock ok code id=%s", result.access.code_id)
        response = JSONResponse({"ok": True, "enabled": True, "unlocked": True, "accessPage": access_page})
        set_access_cookie(response, request, result.access.code_id)
        return response

    def set_access_cookie(response: Response, request: Request, code_id: int) -> None:
        response.set_cookie(
            key=settings.cookie_name,
            value=gate.issue_token(code_id),
            max_age=settings.cookie_days * 86400,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
        )  # no Domain attribute: host-only cookie

    @app.post("/api/access/logout")
    def access_logout(request: Request) -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            key=settings.cookie_name, path="/", httponly=True, samesite="lax", secure=request.url.scheme == "https"
        )
        return response

    @app.get("/api/config")
    def app_config(request: Request) -> JSONResponse:
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
        # The pack is for sale, when Paddle is configured, to codes with a quota: the app sends the code id to Paddle
        # as custom data.
        payable = paddle.enabled and access.metered and remaining is not None
        response = JSONResponse({
            "supports": {"text": ready, "voice": ready and bool(settings.groq_api_key), "image": ready, "shareCard": True},
            "supportedLanguages": sorted(SUPPORTED_LANGS),
            "demoSamples": True,
            "access": {"plan": plan, "remaining": remaining, **({"codeId": access.code_id} if payable else {})},
            "payment": paddle.public_config() if payable else {"enabled": False},
        })
        # The app asks for its config every time it opens: an access cookie older than a day starts its days again.
        if access.code_id is not None and gate.needs_renewal(request.cookies):
            set_access_cookie(response, request, access.code_id)
        return response

    # --- the pack of analyses, paid with Paddle (gate + rate limit checked by GuardMiddleware) ----------------

    @app.post(PACK_PATH)
    def pack_paid(payload: PackPayload, request: Request) -> JSONResponse:
        """After the checkout: Paddle confirms the transaction, then its analyses join the code that asked."""
        lang = canonical_lang(payload.lang)
        access: Optional[Access] = request.scope.get("state", {}).get("bys_access")
        if access is None:
            raise HTTPException(status_code=401, detail=LOCKED)
        record = store.get(access.code_id) if access.metered else None
        if record is None or record.quota is None:
            raise api_error(409, "not_payable", text(lang, "error_not_payable"))
        if not paddle.enabled:
            raise api_error(503, "payment_unavailable", text(lang, "error_payment_unavailable"))
        if not TRANSACTION_ID.fullmatch(payload.transaction_id):
            raise api_error(400, "invalid_input", text(lang, "error_invalid_input"))
        try:
            transaction = paddle.transaction(payload.transaction_id)
        except PaymentUnavailable:
            raise api_error(502, "payment_check_failed", text(lang, "error_payment_check")) from None
        check = paddle.check(transaction or {}, code_id=record.id)
        if check.status == "pending":
            # The app asks again in a moment: Paddle confirms most payments within seconds.
            return JSONResponse(
                {"detail": {"code": "payment_pending", "message": text(lang, "error_payment_pending")}},
                status_code=202, headers={"Retry-After": "2"},
            )
        if check.status != "paid":
            log.warning("pack refused code id=%s txn=%s: %s", record.id, payload.transaction_id,
                        check.reason if transaction is not None else "unknown transaction")
            raise api_error(402, "payment_mismatch", text(lang, "error_payment_mismatch"))
        analyses = paddle.analyses * check.packs
        outcome = store.add_pack(record.id, payload.transaction_id, analyses)
        if outcome.status == "refused" or outcome.record is None:
            log.warning("pack refused code id=%s txn=%s: %s", record.id, payload.transaction_id, outcome.reason)
            raise api_error(402, "payment_mismatch", text(lang, "error_payment_mismatch"))
        log.info("pack %s code id=%s txn=%s analyses=%s remaining=%s", outcome.status, record.id,
                 payload.transaction_id, analyses, outcome.record.remaining)
        remaining = outcome.record.remaining
        return JSONResponse({"status": outcome.status, "remaining": remaining},
                            headers={"X-BYS-Remaining": str(remaining)})

    @app.post(WEBHOOK_PATH, include_in_schema=False)
    async def paddle_webhook(request: Request) -> dict[str, bool]:
        """Paddle tells us about a paid pack, so its analyses arrive even if the app is never opened again."""
        raw = await request.body()
        if not paddle.verify_webhook(raw, request.headers.get("paddle-signature")):
            raise HTTPException(status_code=401, detail="Invalid signature.")
        try:
            event = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON.") from None
        if not isinstance(event, dict) or event.get("event_type") not in WEBHOOK_EVENTS:
            return {"ok": True}  # other events, or the payments of the other apps on the same Paddle account
        data = event.get("data")
        if not isinstance(data, dict):
            return {"ok": True}
        try:
            code_id = int(str((data.get("custom_data") or {}).get(CODE_FIELD, "")))
        except ValueError:
            return {"ok": True}  # not a pack of Before You Send
        check = paddle.check(data, code_id=code_id)
        transaction_id = str(data.get("id", ""))
        if check.status != "paid" or not TRANSACTION_ID.fullmatch(transaction_id):
            log.info("webhook %s code id=%s not counted: %s", event.get("event_type"), code_id,
                     check.reason or check.status or "transaction id")
            return {"ok": True}
        analyses = paddle.analyses * check.packs
        outcome = store.add_pack(code_id, transaction_id, analyses)
        remaining = outcome.record.remaining if outcome.record else None
        log.info("pack %s by webhook code id=%s txn=%s analyses=%s remaining=%s%s", outcome.status, code_id,
                 transaction_id, analyses, remaining, f" ({outcome.reason})" if outcome.reason else "")
        return {"ok": True}

    # --- AI endpoints (gate + rate limit already checked by GuardMiddleware) ------------

    @app.post("/api/decode/text")
    def decode_text(request: Request, payload: DecodePayload) -> JSONResponse:
        lang = canonical_lang(payload.lang)
        message = payload.text.strip()
        if not message:
            raise api_error(400, "empty_text", text(lang, "error_empty_text"))
        history = history_of(payload.history, lang)
        require_analysis(lang)

        def work() -> dict[str, Any]:
            result, _ = analyzer.decode_text(message, lang, history)
            result.update(source="text", input=message, lang=lang)
            return result

        return run_metered(request, lang, work)

    @app.post("/api/score/text")
    def score_text(request: Request, payload: ScorePayload) -> JSONResponse:
        lang = canonical_lang(payload.lang)
        draft = payload.text.strip()
        reply_to = (payload.reply_to or "").strip() or None
        if not draft:
            raise api_error(400, "empty_text", text(lang, "error_empty_send"))
        require_analysis(lang)

        def work() -> dict[str, Any]:
            result, _ = analyzer.score_text(draft, lang, reply_to)
            result.update(input=draft, lang=lang)
            return result

        return run_metered(request, lang, work)

    @app.post("/api/replies/translate")
    def translate_replies(payload: RepliesPayload) -> JSONResponse:
        """The ready replies in the language the user picks (Luca, 30/10: «lo stesso che Plantatam»). Like «Leer en»
        there, no analysis is used; the access code and the per-IP limits still apply (GuardMiddleware)."""
        lang = canonical_lang(payload.lang)
        replies = [reply.strip() for reply in payload.replies]
        if not all(replies):
            raise api_error(400, "empty_text", text(lang, "error_invalid_input"))
        if payload.to == lang:
            return JSONResponse({"replies": replies, "to": payload.to})
        require_analysis(lang)
        try:
            translated = analyzer.translate_replies(replies, lang, payload.to, (payload.context or "").strip())
        except AnalysisError as exc:
            raise provider_error(exc, lang) from None
        log.info("replies translated lang=%s to=%s count=%d", lang, payload.to, len(translated))
        return JSONResponse({"replies": translated, "to": payload.to})

    @app.post("/api/decode/audio")
    def decode_audio(
        request: Request,
        file: Optional[UploadFile] = File(default=None),
        transcript: Optional[str] = Form(default=None, max_length=MAX_TEXT_CHARS),
        lang: Optional[str] = Form(default="it", max_length=16),
        history: Optional[str] = Form(default=None, max_length=2 * MAX_HISTORY_CHARS),
    ) -> JSONResponse:
        lang = canonical_lang(lang)
        typed = (transcript or "").strip()
        earlier = history_from_form(history, lang)
        if typed:
            require_analysis(lang)

            def work_typed() -> dict[str, Any]:
                result, _ = analyzer.decode_text(typed, lang, earlier)
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
            # Automatic language first (a received voice note is not necessarily in the UI language); if Whisper
            # picks another language, a second pass in the UI language keeps the more confident transcript.
            heard = transcription.transcribe_best(
                audio,
                filename=upload_name,
                content_type=media_type,
                api_key=settings.groq_api_key,
                model=settings.transcribe_model,
                timeout_seconds=settings.transcribe_timeout_seconds,
                ui_lang=lang,
            ).text
            # Same limit as typed text: a very long recording is not sent to Claude (the unit is refunded).
            if len(heard) > MAX_TEXT_CHARS:
                raise TranscriptionTooLong("transcript too long")
            result, _ = analyzer.decode_text(heard, lang, earlier)
            result.update(source="voice", input=heard, lang=lang, transcript=heard)
            return result

        return run_metered(request, lang, work)

    @app.post("/api/decode/image")
    def decode_image(
        request: Request,
        file: Optional[UploadFile] = File(default=None),
        extracted_text: Optional[str] = Form(default=None, max_length=MAX_TEXT_CHARS),
        lang: Optional[str] = Form(default="it", max_length=16),
        history: Optional[str] = Form(default=None, max_length=2 * MAX_HISTORY_CHARS),
    ) -> JSONResponse:
        lang = canonical_lang(lang)
        typed = (extracted_text or "").strip()
        earlier = history_from_form(history, lang)
        image = read_upload(file, settings.max_image_bytes, lang, "error_image_too_large")
        if image is None:
            if not typed:
                raise api_error(400, "missing_input", text(lang, "error_image_missing"))
            require_analysis(lang)

            def work_typed() -> dict[str, Any]:
                result, _ = analyzer.decode_text(typed, lang, earlier)
                result.update(source="image", input=typed, lang=lang, extracted_text=typed, extraction_confidence=100)
                return result

            return run_metered(request, lang, work_typed)

        require_analysis(lang)
        media_type = sniff_image_type(image)
        declared = (file.content_type or "").split(";", 1)[0].strip().lower() if file else ""
        if media_type is None or (declared not in ("", "application/octet-stream") and not declared.startswith("image/")):
            raise api_error(415, "unsupported_image", text(lang, "error_image_type"))

        def work() -> dict[str, Any]:
            result, _ = analyzer.decode_image(image, media_type, lang, earlier)
            result.update(source="image", input=result["extracted_text"], lang=lang)
            return result

        return run_metered(request, lang, work)

    # --- admin (GuardMiddleware allows only 127.0.0.1, unproxied, with the bearer token) --

    # For the n8n monitor: the service answers, and analysis.blocked says whether Anthropic is refusing every call
    # ("credit": credit or spend limit used up, "key": key refused, "model": model not available) since the last success.
    @app.get("/admin/status")
    def admin_status() -> dict[str, Any]:
        return {"ok": True, "analysis": analyzer.health(), "voice": settings.voice_ready}

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

    # "Share → Before You Send" (Web Share Target of the installed app) posts here. The service worker answers it and
    # hands the shared file or text to the page; if it is not running, the body is never read and the app opens.
    @app.post(SHARE_TARGET_PATH, include_in_schema=False)
    def share_target_without_service_worker() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_json_bytes,
        path_limits={
            "/api/decode/image": settings.max_image_bytes + MULTIPART_OVERHEAD,
            "/api/decode/audio": settings.max_audio_bytes + MULTIPART_OVERHEAD,
            SHARE_TARGET_PATH: max(settings.max_image_bytes, settings.max_audio_bytes) + MULTIPART_OVERHEAD,
        },
    )
    app.add_middleware(
        GuardMiddleware,
        gate=gate,
        ai_limiter=RateLimiter([(settings.ai_per_minute, MINUTE), (settings.ai_per_day, DAY)]),
        unlock_limiter=RateLimiter([(settings.unlock_per_minute, MINUTE), (settings.unlock_per_hour, HOUR)]),
        admin_token=settings.admin_token,
    )
    # Outermost, so every response gets the headers; Paddle's checkout only while the pack can be bought.
    app.add_middleware(SecurityHeadersMiddleware, payments=paddle.enabled)
    app.mount("/", PublicFiles(directory=str(public_dir), html=True), name="public")
    return app
