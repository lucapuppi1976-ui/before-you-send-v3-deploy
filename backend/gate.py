"""Access gate: unlock-code checks and the signed access cookie.

The cookie carries only the code id and an expiry, signed with BYS_GATE_SECRET.
BYS_GATE_EPOCH is part of the signed material, so changing it revokes every cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Mapping, Optional

from .codes import CodeRecord, CodeStore, normalize_code
from .config import Settings

SHARED_CODE_ID = 0  # the optional BYS_ACCESS_CODE tester code: unlimited and not stored in the DB
TOKEN_VERSION = "v2"
_NUMBER = re.compile(r"[0-9]{1,12}")
_SIGNATURE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Access:
    """Who is calling. code_id is None when the gate is disabled, 0 for the shared tester code."""

    code_id: Optional[int]

    @property
    def metered(self) -> bool:
        return self.code_id is not None and self.code_id != SHARED_CODE_ID


OPEN_ACCESS = Access(code_id=None)


@dataclass(frozen=True)
class UnlockResult:
    status: str  # "ok", "invalid" or "ended" (valid code that is revoked, expired or used up)
    access: Optional[Access] = None
    record: Optional[CodeRecord] = None


class Gate:
    def __init__(self, settings: Settings, store: CodeStore) -> None:
        self.settings = settings
        self.store = store
        self._secret = settings.gate_secret.encode("utf-8")
        shared = normalize_code(settings.access_code)
        self._shared_digest = hashlib.sha256(shared.encode("utf-8")).digest() if shared else None

    @property
    def enabled(self) -> bool:
        return self.settings.gate_enabled

    def check_code(self, raw_code: str, now: Optional[float] = None) -> UnlockResult:
        normalized = normalize_code(raw_code)
        if not normalized:
            return UnlockResult("invalid")
        if self._shared_digest is not None:
            # Constant-time comparison of the normalized codes (as fixed-length SHA-256 digests).
            candidate = hashlib.sha256(normalized.encode("utf-8")).digest()
            if hmac.compare_digest(candidate, self._shared_digest):
                return UnlockResult("ok", Access(SHARED_CODE_ID))
        record = self.store.find(raw_code)
        if record is None:
            return UnlockResult("invalid")
        if record.status(now) != "active":
            return UnlockResult("ended", record=record)
        return UnlockResult("ok", Access(record.id), record)

    def _signature(self, code_id: int, expires_at: int) -> str:
        # Tokens for the shared tester code are also bound to that code, so changing it revokes them.
        bound = self._shared_digest.hex() if code_id == SHARED_CODE_ID and self._shared_digest else ""
        material = json.dumps(
            ["bys-gate", TOKEN_VERSION, code_id, expires_at, self.settings.gate_epoch, bound], separators=(",", ":")
        )
        return hmac.new(self._secret, material.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_token(self, code_id: int, now: Optional[float] = None) -> str:
        expires_at = int(time.time() if now is None else now) + self.settings.cookie_days * 86400
        return f"{TOKEN_VERSION}.{code_id}.{expires_at}.{self._signature(code_id, expires_at)}"

    def verify_token(self, token: Optional[str], now: Optional[float] = None) -> Optional[Access]:
        if not token or len(token) > 200:
            return None
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != TOKEN_VERSION:
            return None
        raw_id, raw_expiry, signature = parts[1], parts[2], parts[3]
        if not (_NUMBER.fullmatch(raw_id) and _NUMBER.fullmatch(raw_expiry) and _SIGNATURE.fullmatch(signature)):
            return None
        code_id, expires_at = int(raw_id), int(raw_expiry)
        if code_id == SHARED_CODE_ID and self._shared_digest is None:
            return None
        if not hmac.compare_digest(signature, self._signature(code_id, expires_at)):
            return None
        if expires_at < int(time.time() if now is None else now):
            return None
        return Access(code_id)

    def access_from_cookies(self, cookies: Mapping[str, str]) -> Optional[Access]:
        """The caller's Access, or None when the gate is on and the cookie is missing or invalid."""
        if not self.enabled:
            return OPEN_ACCESS
        return self.verify_token(cookies.get(self.settings.cookie_name))

    def session(self, cookies: Mapping[str, str]) -> tuple[Optional[Access], Optional[CodeRecord]]:
        """Access plus its DB record, for status and config. Revoked, expired or deleted codes give (None, None)."""
        access = self.access_from_cookies(cookies)
        if access is None or not access.metered:
            return access, None
        record = self.store.get(access.code_id)
        if record is None or record.status() in ("revoked", "expired"):
            return None, None
        return access, record
