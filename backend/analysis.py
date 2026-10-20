"""Claude analysis: request building, the Messages API call, and result normalization.

The web app and tools/compare_models.py both use this module, so prompts and
normalization stay identical between production and the model comparison.
Logs carry metadata only (model, request id, token counts), never user content.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from . import prompts
from .config import MAX_TEXT_CHARS, Settings
from .i18n import canonical_lang, label_for_score, text

log = logging.getLogger("bys.analysis")

# Server-side refusal fallback. When a safety classifier declines a request, the API can
# retry it on another model inside the same call. The models in FALLBACK_DEFAULT_MODELS are
# documented to support the "default" form (Anthropic picks the fallback by refusal
# category) on the Claude API, which needs this exact beta header. Any other model either
# has no server-side fallback or is not documented for it, so it goes to the regular
# endpoint without the parameter.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_DEFAULT_MODELS = frozenset({"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"})

IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


class AnalysisError(Exception):
    """Claude did not produce a usable analysis. Messages are for logs and never include user content."""


class AnalysisUnavailable(AnalysisError):
    """No Anthropic API key is configured."""


class AnalysisRefused(AnalysisError):
    """The request was declined (stop_reason == "refusal")."""


class AnalysisBusy(AnalysisError):
    """Anthropic is rate limiting or overloaded, even after the SDK's retries."""


class AnalysisFailed(AnalysisError):
    """Any other failure: API error, network error, truncated or invalid output."""


@dataclass(frozen=True)
class ClaudeRequest:
    system: str
    messages: list[dict[str, Any]]
    schema: dict[str, Any]


@dataclass(frozen=True)
class ClaudeResult:
    data: dict[str, Any]
    model: str
    request_id: Optional[str]
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    latency_ms: int
    fallback_used: bool


def create_client(api_key: str, timeout_seconds: float, max_retries: int) -> anthropic.Anthropic:
    # The SDK retries connection errors, 408, 409, 429 and 5xx with backoff. Timeouts are
    # retried too, so the worst case is roughly timeout * (max_retries + 1).
    return anthropic.Anthropic(
        api_key=api_key,
        timeout=anthropic.Timeout(timeout_seconds, connect=10.0),
        max_retries=max_retries,
    )


# --- Requests --------------------------------------------------------------------------


def decode_text_request(message: str, lang: str) -> ClaudeRequest:
    return ClaudeRequest(
        system=prompts.decode_system(lang),
        messages=[{"role": "user", "content": prompts.user_text("decode", lang, message)}],
        schema=prompts.decode_schema(),
    )


def decode_image_request(image: bytes, media_type: str, lang: str) -> ClaudeRequest:
    if media_type not in IMAGE_MEDIA_TYPES:
        raise ValueError(f"unsupported image media type: {media_type}")
    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(image).decode("ascii")},
        },
        {"type": "text", "text": prompts.image_instruction(lang)},
    ]
    return ClaudeRequest(
        system=prompts.vision_system(lang),
        messages=[{"role": "user", "content": content}],
        schema=prompts.decode_schema(include_extracted=True),
    )


def score_request(draft: str, lang: str) -> ClaudeRequest:
    return ClaudeRequest(
        system=prompts.score_system(lang),
        messages=[{"role": "user", "content": prompts.user_text("score", lang, draft)}],
        schema=prompts.score_schema(lang),
    )


# --- The API call ----------------------------------------------------------------------


def _log_status_error(exc: anthropic.APIStatusError, model: str) -> None:
    hint = ""
    if exc.status_code in (401, 403):
        hint = " (check ANTHROPIC_API_KEY)"
    elif exc.status_code == 404:
        hint = " (check BYS_MODEL)"
    log.warning(
        "claude api error model=%s status=%s type=%s req=%s%s detail=%s",
        model, exc.status_code, exc.type, exc.request_id, hint, str(exc.message)[:300],
    )


def call_claude(client: Any, request: ClaudeRequest, *, model: str, effort: str, max_tokens: int) -> ClaudeResult:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": request.system,
        "messages": request.messages,
        # `thinking` is omitted so the model runs its default adaptive thinking; effort is the
        # latency and cost control. The JSON schema makes the reply a structured output.
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": request.schema}},
    }
    started = time.monotonic()
    try:
        if model in FALLBACK_DEFAULT_MODELS:
            # Opt into the server-side refusal fallback ("default" form, see FALLBACK_BETA).
            response = client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            response = client.messages.create(**params)
    except (anthropic.RateLimitError, anthropic.OverloadedError, anthropic.ServiceUnavailableError) as exc:
        _log_status_error(exc, model)
        raise AnalysisBusy("anthropic busy") from exc
    except anthropic.APIStatusError as exc:
        _log_status_error(exc, model)
        raise AnalysisFailed("anthropic api error") from exc
    except anthropic.APIConnectionError as exc:
        log.warning("claude connection error model=%s kind=%s", model, type(exc).__name__)
        raise AnalysisFailed("anthropic connection error") from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    request_id = getattr(response, "_request_id", None)
    # Check stop_reason before touching content: a refusal may come with empty or partial content.
    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None) if response.stop_details else None
        log.warning("claude refusal model=%s served_by=%s req=%s category=%s", model, response.model, request_id, category)
        raise AnalysisRefused("refused")
    if response.stop_reason == "max_tokens":
        log.warning("claude output truncated model=%s req=%s max_tokens=%s", model, request_id, max_tokens)
        raise AnalysisFailed("output truncated")

    raw = next((block.text for block in response.content if block.type == "text"), None)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        log.warning("claude returned no usable JSON model=%s req=%s stop=%s", model, request_id, response.stop_reason)
        raise AnalysisFailed("invalid structured output")

    usage = response.usage
    fallback_used = any(getattr(entry, "type", None) == "fallback_message" for entry in (getattr(usage, "iterations", None) or []))
    result = ClaudeResult(
        data=data,
        model=response.model or model,
        request_id=request_id,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        latency_ms=latency_ms,
        fallback_used=fallback_used,
    )
    log.info(
        "claude ok model=%s served_by=%s req=%s in=%d out=%d ms=%d fallback=%s",
        model, result.model, request_id, result.input_tokens, result.output_tokens, latency_ms, fallback_used,
    )
    return result


# --- Normalization (schema shape only; the model's wording is kept) ----------------------

_WHITESPACE = re.compile(r"\s+")


def clamp_0_100(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if math.isnan(number):
        return 0
    if math.isinf(number):
        return 100 if number > 0 else 0
    return max(0, min(100, int(round(number))))


def _clip(value: str, max_len: int) -> str:
    return value if len(value) <= max_len else value[: max_len - 1].rstrip() + "…"


def single_line(value: Any, max_len: int) -> str:
    return _clip(_WHITESPACE.sub(" ", value).strip(), max_len) if isinstance(value, str) else ""


def multi_line(value: Any, max_len: int) -> str:
    return _clip(value.strip(), max_len) if isinstance(value, str) else ""


def fixed_list(items: Any, count: int, fill: list[str], max_len: int) -> list[str]:
    """Exactly `count` distinct, non-empty strings, padded from `fill` when the model gave fewer."""
    out: list[str] = []
    for item in items if isinstance(items, list) else []:
        value = single_line(item, max_len)
        if value and value not in out:
            out.append(value)
        if len(out) == count:
            return out
    for value in fill:
        if len(out) == count:
            break
        if value not in out:
            out.append(value)
    return out


def _normalize_replies(items: Any, lang: str) -> list[dict[str, str]]:
    styles = text(lang, "reply_styles")
    fill = text(lang, "reply_fallbacks")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        reply = multi_line(item.get("text"), 1000)
        if not reply or reply.lower() in seen:
            continue
        seen.add(reply.lower())
        out.append({"style": single_line(item.get("style"), 40) or styles[len(out)], "text": reply})
        if len(out) == 3:
            return out
    for index in range(len(out), 3):
        out.append({"style": styles[index], "text": fill[index]})
    return out


def normalize_decode(data: dict[str, Any], lang: str, *, include_extracted: bool = False) -> dict[str, Any]:
    lang = canonical_lang(lang)
    tones = data.get("tones") if isinstance(data.get("tones"), dict) else {}
    result: dict[str, Any] = {}
    if include_extracted:
        result["extracted_text"] = multi_line(data.get("extracted_text"), MAX_TEXT_CHARS)
        result["extraction_confidence"] = clamp_0_100(data.get("extraction_confidence"))
    result.update(
        verdict=single_line(data.get("verdict"), 120) or text(lang, "verdict_fallback"),
        meaning=single_line(data.get("meaning"), 600) or text(lang, "meaning_fallback"),
        flags=fixed_list(data.get("flags"), 3, text(lang, "flag_fallbacks"), 60),
        guardrails=fixed_list(data.get("guardrails"), 2, text(lang, "guardrail_fallbacks"), 240),
        tones={key: clamp_0_100(tones.get(key)) for key in prompts.TONE_KEYS},
        replies=_normalize_replies(data.get("replies"), lang),
    )
    return result


def normalize_score(data: dict[str, Any], lang: str, draft: str) -> dict[str, Any]:
    lang = canonical_lang(lang)
    score = clamp_0_100(data.get("score"))
    breakdown = data.get("breakdown") if isinstance(data.get("breakdown"), dict) else {}
    raw_rewrites = data.get("rewrites") if isinstance(data.get("rewrites"), dict) else {}
    rewrites = {key: multi_line(raw_rewrites.get(key), MAX_TEXT_CHARS) for key in prompts.REWRITE_KEYS}
    # A missing rewrite falls back to another rewrite, or to the draft itself, never to canned text.
    filler = next((value for value in rewrites.values() if value), "") or multi_line(draft, MAX_TEXT_CHARS)
    return {
        "score": score,
        "label": single_line(data.get("label"), 40) or label_for_score(score, lang),
        "issue": single_line(data.get("issue"), 300) or text(lang, "issue_fallback"),
        "breakdown": {key: clamp_0_100(breakdown.get(key)) for key in prompts.BREAKDOWN_KEYS},
        "rewrites": {key: value or filler for key, value in rewrites.items()},
    }


# --- Public entry point ------------------------------------------------------------------


class Analyzer:
    """Runs the three BYS analyses on Claude. `model` and `effort` can be overridden per call."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: str,
        max_tokens: int,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._client = client
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, client: Any = None) -> Analyzer:
        return cls(
            api_key=settings.anthropic_api_key,
            model=settings.model,
            effort=settings.effort,
            max_tokens=settings.max_tokens,
            timeout_seconds=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
            client=client,
        )

    @property
    def available(self) -> bool:
        return self._client is not None or bool(self._api_key)

    def client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise AnalysisUnavailable("ANTHROPIC_API_KEY is not set")
            with self._lock:
                if self._client is None:
                    self._client = create_client(self._api_key, self._timeout_seconds, self._max_retries)
        return self._client

    def run(self, request: ClaudeRequest, *, model: Optional[str] = None, effort: Optional[str] = None) -> ClaudeResult:
        return call_claude(
            self.client(), request, model=model or self.model, effort=effort or self.effort, max_tokens=self.max_tokens
        )

    def decode_text(self, message: str, lang: str, **overrides: Any) -> tuple[dict[str, Any], ClaudeResult]:
        call = self.run(decode_text_request(message, lang), **overrides)
        return normalize_decode(call.data, lang), call

    def decode_image(self, image: bytes, media_type: str, lang: str, **overrides: Any) -> tuple[dict[str, Any], ClaudeResult]:
        call = self.run(decode_image_request(image, media_type, lang), **overrides)
        return normalize_decode(call.data, lang, include_extracted=True), call

    def score_text(self, draft: str, lang: str, **overrides: Any) -> tuple[dict[str, Any], ClaudeResult]:
        call = self.run(score_request(draft, lang), **overrides)
        return normalize_score(call.data, lang, draft), call


def sniff_image_type(data: bytes) -> Optional[str]:
    """Detect png/jpeg/webp/gif from magic bytes; the browser-declared type is not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
