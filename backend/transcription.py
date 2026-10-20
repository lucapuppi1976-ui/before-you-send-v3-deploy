"""Voice-note transcription through Groq's OpenAI-compatible endpoint (plain `requests`).

Upstream error bodies are never returned to the browser; only the status code and
error type are logged.
"""
from __future__ import annotations

import logging
from pathlib import PurePath
from typing import Optional

import requests

log = logging.getLogger("bys.transcription")

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Extensions Groq accepts, and the audio type for each (used when the browser sends no type).
_GROQ_EXTENSIONS = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}
_TYPE_EXTENSIONS = {
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
}


class TranscriptionError(Exception):
    """Transcription failed. Messages are for logs and never include user content."""


class TranscriptionBusy(TranscriptionError):
    """Groq is rate limiting the request."""


class TranscriptionFailed(TranscriptionError):
    """Network or API failure, or an unreadable response."""


class TranscriptionEmpty(TranscriptionError):
    """Groq answered, but the transcript is empty."""


class TranscriptionTooLong(TranscriptionError):
    """The transcript is longer than the text limit, so it is not sent for analysis."""


def resolve_audio_upload(filename: Optional[str], content_type: Optional[str]) -> Optional[tuple[str, str]]:
    """Return (upload filename, audio type) for an audio upload, or None if it is not audio.

    Only audio/* types are accepted; a missing or generic type is accepted only for a
    known audio extension. The user's own filename is not forwarded.
    """
    extension = PurePath(filename or "").suffix.lower()
    if extension == ".opus":
        extension = ".ogg"  # WhatsApp voice notes are Ogg Opus files
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not media_type.startswith("audio/"):
        if media_type not in ("", "application/octet-stream") or extension not in _GROQ_EXTENSIONS:
            return None
        media_type = _GROQ_EXTENSIONS[extension]
    if extension not in _GROQ_EXTENSIONS:
        extension = _TYPE_EXTENSIONS.get(media_type, extension or ".audio")
    return f"voice{extension}", media_type


def _error_type(response: requests.Response) -> str:
    try:
        error = response.json().get("error") or {}
        return str(error.get("code") or error.get("type") or "-")[:60]
    except (ValueError, AttributeError):
        return "-"


def transcribe(
    audio: bytes,
    *,
    filename: str,
    content_type: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    language: Optional[str] = None,
) -> str:
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language
    try:
        response = requests.post(
            GROQ_TRANSCRIPTION_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, audio, content_type)},
            data=data,
            timeout=(10, timeout_seconds),
        )
    except requests.RequestException as exc:
        log.warning("groq transcription network error kind=%s", type(exc).__name__)
        raise TranscriptionFailed("groq network error") from exc

    request_id = response.headers.get("x-request-id")
    if response.status_code != 200:
        log.warning(
            "groq transcription error status=%s type=%s req=%s", response.status_code, _error_type(response), request_id
        )
        if response.status_code == 429:
            raise TranscriptionBusy("groq rate limited")
        raise TranscriptionFailed("groq api error")
    try:
        payload = response.json()
    except ValueError as exc:
        log.warning("groq transcription returned invalid JSON req=%s", request_id)
        raise TranscriptionFailed("groq invalid response") from exc
    transcript = payload.get("text") if isinstance(payload, dict) else None
    transcript = transcript.strip() if isinstance(transcript, str) else ""
    if not transcript:
        log.info("groq transcription empty req=%s", request_id)
        raise TranscriptionEmpty("empty transcript")
    log.info("groq transcription ok model=%s req=%s bytes=%d", model, request_id, len(audio))
    return transcript
