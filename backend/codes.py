"""Per-user access codes with quotas, stored in SQLite (stdlib sqlite3).

Only the SHA-256 hash of the normalized code is stored; the plain code is shown once
when it is created. Every analysis consumes one unit atomically before the provider
is called, and the unit is refunded if the provider fails.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

CODE_PREFIX = "BYS"
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
CODE_RANDOM_LENGTH = 10
PLANS = ("trial", "paid")

_SEPARATORS = re.compile(r"[\s\-_.·]+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    plan TEXT NOT NULL CHECK (plan IN ('trial', 'paid')),
    quota INTEGER CHECK (quota IS NULL OR quota >= 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    revoked_at INTEGER,
    last_used_at INTEGER
);
"""


def normalize_code(raw: Optional[str]) -> str:
    """Uppercase and drop spaces, dashes, dots and underscores, so typing variations still match."""
    return _SEPARATORS.sub("", unicodedata.normalize("NFKC", raw or "")).upper()


def hash_code(raw: str) -> str:
    normalized = normalize_code(raw)
    if len(normalized) == CODE_RANDOM_LENGTH:
        normalized = CODE_PREFIX + normalized  # the user typed the code without its BYS- prefix
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_code() -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_RANDOM_LENGTH))
    return f"{CODE_PREFIX}-{body[:5]}-{body[5:]}"


@dataclass(frozen=True)
class CodeRecord:
    id: int
    label: Optional[str]
    plan: str
    quota: Optional[int]
    used_count: int
    created_at: int
    expires_at: Optional[int]
    revoked_at: Optional[int]
    last_used_at: Optional[int]

    @property
    def remaining(self) -> Optional[int]:
        return None if self.quota is None else max(0, self.quota - self.used_count)

    def status(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        if self.quota is not None and self.used_count >= self.quota:
            return "exhausted"
        return "active"


@dataclass(frozen=True)
class ConsumeOutcome:
    ok: bool
    record: Optional[CodeRecord]
    reason: Optional[str] = None  # "missing", "revoked", "expired" or "exhausted" when not ok


def _record(row: sqlite3.Row) -> CodeRecord:
    return CodeRecord(
        id=row["id"],
        label=row["label"],
        plan=row["plan"],
        quota=row["quota"],
        used_count=row["used_count"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
    )


_COLUMNS = "id, label, plan, quota, used_count, created_at, expires_at, revoked_at, last_used_at"


class CodeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation: safe across worker threads.
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    def create(
        self,
        *,
        plan: str,
        quota: Optional[int],
        label: Optional[str] = None,
        expires_at: Optional[int] = None,
        now: Optional[float] = None,
    ) -> tuple[str, CodeRecord]:
        if plan not in PLANS:
            raise ValueError(f"plan must be one of {PLANS}")
        if plan == "trial" and quota is None:
            raise ValueError("trial codes need a quota")
        created_at = int(time.time() if now is None else now)
        for _ in range(5):
            code = generate_code()
            try:
                with self._connect() as conn:
                    cursor = conn.execute(
                        "INSERT INTO access_codes (code_hash, label, plan, quota, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (hash_code(code), label, plan, quota, created_at, expires_at),
                    )
                    row = conn.execute(f"SELECT {_COLUMNS} FROM access_codes WHERE id = ?", (cursor.lastrowid,)).fetchone()
            except sqlite3.IntegrityError:
                continue  # hash collision with an existing code: draw again
            return code, _record(row)
        raise RuntimeError("could not generate a unique access code")

    def get(self, code_id: int) -> Optional[CodeRecord]:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM access_codes WHERE id = ?", (code_id,)).fetchone()
        return _record(row) if row else None

    def find(self, raw_code: str) -> Optional[CodeRecord]:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM access_codes WHERE code_hash = ?", (hash_code(raw_code),)).fetchone()
        return _record(row) if row else None

    def list(self) -> list[CodeRecord]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM access_codes ORDER BY id").fetchall()
        return [_record(row) for row in rows]

    def consume(self, code_id: int, now: Optional[float] = None) -> ConsumeOutcome:
        """Atomically use one analysis, in a single transaction, if the code allows it."""
        now_ts = int(time.time() if now is None else now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                updated = conn.execute(
                    "UPDATE access_codes SET used_count = used_count + 1, last_used_at = ? "
                    "WHERE id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?) "
                    "AND (quota IS NULL OR used_count < quota)",
                    (now_ts, code_id, now_ts),
                ).rowcount
                row = conn.execute(f"SELECT {_COLUMNS} FROM access_codes WHERE id = ?", (code_id,)).fetchone()
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        if row is None:
            return ConsumeOutcome(False, None, "missing")
        record = _record(row)
        if updated == 1:
            return ConsumeOutcome(True, record)
        return ConsumeOutcome(False, record, record.status(now_ts))

    def refund(self, code_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE access_codes SET used_count = used_count - 1 WHERE id = ? AND used_count > 0", (code_id,))

    def revoke(self, code_id: int, now: Optional[float] = None) -> bool:
        now_ts = int(time.time() if now is None else now)
        with self._connect() as conn:
            return conn.execute(
                "UPDATE access_codes SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now_ts, code_id)
            ).rowcount == 1

    def set_expiry(self, code_id: int, expires_at: Optional[int]) -> bool:
        with self._connect() as conn:
            return conn.execute("UPDATE access_codes SET expires_at = ? WHERE id = ?", (expires_at, code_id)).rowcount == 1

    def convert_to_paid(self, code_id: int, quota: Optional[int] = None) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "UPDATE access_codes SET plan = 'paid', quota = ? WHERE id = ? AND plan = 'trial'", (quota, code_id)
            ).rowcount == 1
