"""Configuration, read from environment variables (optionally from a local .env file).

Unsafe configurations raise ConfigError so the server refuses to start instead of
running with a gate that silently lets everyone in.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT / "public"

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
DEFAULT_ACCESS_PAGE_URL = "https://bb1studio.com/before-you-send/access/"
DEFAULT_DB_PATH = "./data/bys.db"

MIN_SECRET_LENGTH = 32
MAX_TEXT_CHARS = 6000
MAX_CODE_CHARS = 128
MB = 1024 * 1024

_FALSE_VALUES = {"0", "false", "no", "off"}
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class ConfigError(RuntimeError):
    """The configuration is unsafe or invalid; the server must not start."""


@dataclass(frozen=True)
class Settings:
    # Claude (Anthropic API)
    anthropic_api_key: str = ""
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    max_tokens: int = 16000
    ai_timeout_seconds: float = 60.0
    ai_max_retries: int = 2
    # Groq (voice-note transcription)
    groq_api_key: str = ""
    transcribe_model: str = DEFAULT_TRANSCRIBE_MODEL
    transcribe_timeout_seconds: float = 60.0
    # Access gate
    gate_enabled: bool = True
    gate_secret: str = ""
    gate_epoch: str = "1"
    access_code: str = ""
    access_page_url: str = DEFAULT_ACCESS_PAGE_URL
    cookie_name: str = "bys_access"
    cookie_days: int = 30
    db_path: Path = ROOT / "data" / "bys.db"
    trial_quota: int = 30
    admin_token: str = ""
    # Abuse and cost limits
    max_image_bytes: int = 5 * MB
    max_audio_bytes: int = 15 * MB
    max_json_bytes: int = 64 * 1024
    unlock_per_minute: int = 5
    unlock_per_hour: int = 30
    ai_per_minute: int = 10
    ai_per_day: int = 200
    log_level: str = "INFO"

    @property
    def analysis_ready(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def voice_ready(self) -> bool:
        return bool(self.anthropic_api_key and self.groq_api_key)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding real environment variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _text(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = (env.get(name) or "").strip()
    return value or default


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number (got {raw!r}).") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum} (got {value}).")
    return value


def _number(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number (got {raw!r}).") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum} (got {value}).")
    return value


def _path(raw: str) -> Path:
    path = Path(raw).expanduser()
    # Relative paths are resolved from the project folder, not from the service's working directory.
    return path if path.is_absolute() else (ROOT / path).resolve()


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if environ is None else environ
    gate_flag = _text(env, "BYS_GATE_ENABLED").lower()
    return Settings(
        anthropic_api_key=_text(env, "ANTHROPIC_API_KEY"),
        model=_text(env, "BYS_MODEL", DEFAULT_MODEL),
        effort=_text(env, "BYS_EFFORT", DEFAULT_EFFORT).lower(),
        max_tokens=_integer(env, "BYS_MAX_TOKENS", 16000, minimum=1024),
        ai_timeout_seconds=_number(env, "BYS_AI_TIMEOUT_SECONDS", 60.0, minimum=5.0),
        ai_max_retries=_integer(env, "BYS_AI_MAX_RETRIES", 2, minimum=0),
        groq_api_key=_text(env, "GROQ_API_KEY"),
        transcribe_model=_text(env, "BYS_TRANSCRIBE_MODEL", DEFAULT_TRANSCRIBE_MODEL),
        transcribe_timeout_seconds=_number(env, "BYS_TRANSCRIBE_TIMEOUT_SECONDS", 60.0, minimum=5.0),
        # The gate is ON unless BYS_GATE_ENABLED is explicitly false.
        gate_enabled=gate_flag not in _FALSE_VALUES,
        gate_secret=_text(env, "BYS_GATE_SECRET"),
        gate_epoch=_text(env, "BYS_GATE_EPOCH", "1"),
        access_code=_text(env, "BYS_ACCESS_CODE"),
        access_page_url=_text(env, "BYS_ACCESS_PAGE_URL", DEFAULT_ACCESS_PAGE_URL),
        cookie_name=_text(env, "BYS_ACCESS_COOKIE_NAME", "bys_access"),
        cookie_days=_integer(env, "BYS_ACCESS_COOKIE_DAYS", 30, minimum=1),
        db_path=_path(_text(env, "BYS_DB_PATH", DEFAULT_DB_PATH)),
        trial_quota=_integer(env, "BYS_TRIAL_QUOTA", 30, minimum=1),
        admin_token=_text(env, "BYS_ADMIN_TOKEN"),
        max_image_bytes=_integer(env, "BYS_MAX_IMAGE_MB", 5, minimum=1) * MB,
        max_audio_bytes=_integer(env, "BYS_MAX_AUDIO_MB", 15, minimum=1) * MB,
        unlock_per_minute=_integer(env, "BYS_RL_UNLOCK_PER_MINUTE", 5),
        unlock_per_hour=_integer(env, "BYS_RL_UNLOCK_PER_HOUR", 30),
        ai_per_minute=_integer(env, "BYS_RL_AI_PER_MINUTE", 10),
        ai_per_day=_integer(env, "BYS_RL_AI_PER_DAY", 200),
        log_level=_text(env, "BYS_LOG_LEVEL", "INFO").upper(),
    )


def validate_settings(settings: Settings) -> list[str]:
    """Raise ConfigError for unsafe settings; return warnings worth logging at startup."""
    if settings.gate_enabled and len(settings.gate_secret) < MIN_SECRET_LENGTH:
        raise ConfigError(
            "BYS_GATE_SECRET must be set to a random value of at least "
            f"{MIN_SECRET_LENGTH} characters while the access gate is enabled. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if settings.admin_token and len(settings.admin_token) < MIN_SECRET_LENGTH:
        raise ConfigError(
            f"BYS_ADMIN_TOKEN must be at least {MIN_SECRET_LENGTH} characters long, or unset to disable /admin/codes."
        )
    if settings.effort not in EFFORT_LEVELS:
        raise ConfigError(f"BYS_EFFORT must be one of {', '.join(EFFORT_LEVELS)} (got {settings.effort!r}).")
    if settings.log_level not in _LOG_LEVELS:
        raise ConfigError(f"BYS_LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}.")
    if not settings.access_page_url.startswith(("https://", "http://")):
        raise ConfigError("BYS_ACCESS_PAGE_URL must be an http(s) URL.")

    warnings: list[str] = []
    if not settings.gate_enabled:
        warnings.append(
            "SECURITY WARNING: BYS_GATE_ENABLED is false, so the access gate is OFF. Anyone who can reach "
            "this server can use the AI endpoints and spend your API credit. Use this only for local development."
        )
    if settings.access_code and len(settings.access_code) < 8:
        warnings.append("BYS_ACCESS_CODE is shorter than 8 characters; use a longer shared tester code.")
    if not settings.anthropic_api_key:
        warnings.append("ANTHROPIC_API_KEY is not set: text and screenshot analysis are unavailable.")
    if not settings.groq_api_key:
        warnings.append("GROQ_API_KEY is not set: voice-note transcription is unavailable.")
    return warnings
