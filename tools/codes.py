"""Manage Before You Send access codes (owner CLI).

Each new code is printed once; the database keeps only its SHA-256 hash.

Examples:
  python tools/codes.py create --plan trial --label "mario@example.com"
  python tools/codes.py create --plan paid --days 365 --count 3
  python tools/codes.py list
  python tools/codes.py revoke 12
  python tools/codes.py extend 12 --days 30
  python tools/codes.py convert 12 --quota 500
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, TextIO

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.codes import CodeRecord, CodeStore  # noqa: E402
from backend.config import ConfigError, load_env_file, load_settings  # noqa: E402


def _when(timestamp: Optional[int]) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M") if timestamp else "-"


def _quota(record: CodeRecord) -> str:
    return "unlimited" if record.quota is None else str(record.quota)


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codes.py", description="Manage Before You Send access codes.")
    parser.add_argument("--db", help="SQLite file (default: BYS_DB_PATH, or ./data/bys.db in the project folder)")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create codes and print each one once")
    create.add_argument("--plan", choices=("trial", "paid"), default="trial")
    create.add_argument("--quota", type=_positive, help="analyses allowed (trial: BYS_TRIAL_QUOTA by default; paid: unlimited)")
    create.add_argument("--label", help="label or email of the person who gets the code")
    create.add_argument("--days", type=_positive, help="expire after N days (default: never)")
    create.add_argument("--count", type=_positive, default=1, help="how many codes to create (max 100)")

    commands.add_parser("list", help="list codes with their usage")

    revoke = commands.add_parser("revoke", help="revoke a code immediately")
    revoke.add_argument("id", type=int)

    extend = commands.add_parser("extend", help="extend or change the expiry of a code")
    extend.add_argument("id", type=int)
    when = extend.add_mutually_exclusive_group(required=True)
    when.add_argument("--days", type=_positive, help="add N days to the current expiry (or to now if already expired)")
    when.add_argument("--until", help="new expiry date, YYYY-MM-DD (the code works until the end of that day, UTC)")
    when.add_argument("--never", action="store_true", help="remove the expiry")

    convert = commands.add_parser("convert", help="convert a trial code to paid")
    convert.add_argument("id", type=int)
    convert.add_argument("--quota", type=_positive, help="analyses allowed on the paid plan (default: unlimited)")
    return parser


def main(argv: Optional[list[str]] = None, out: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    load_env_file(ROOT / ".env")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    store = CodeStore(Path(args.db).resolve() if args.db else settings.db_path)
    store.initialize()
    now = int(time.time())

    if args.command == "create":
        if args.count > 100:
            print("error: --count must be 100 or less", file=sys.stderr)
            return 2
        quota = args.quota if args.quota is not None else (settings.trial_quota if args.plan == "trial" else None)
        expires_at = now + args.days * 86400 if args.days else None
        print(f"Created {args.count} code(s). Each code is shown only now: send it to the user and keep no copy.", file=out)
        for _ in range(args.count):
            code, record = store.create(plan=args.plan, quota=quota, label=args.label, expires_at=expires_at, now=now)
            print(
                f"  id {record.id:<5} {code}   plan {record.plan}, quota {_quota(record)}, "
                f"expires {_when(record.expires_at)}, label {record.label or '-'}",
                file=out,
            )
        return 0

    if args.command == "list":
        records = store.list()
        if not records:
            print("No codes yet. Create one with: python tools/codes.py create", file=out)
            return 0
        print(f"{'id':>4}  {'plan':<5}  {'used':>9}  {'left':>9}  {'status':<9}  {'created':<16}  {'expires':<16}  {'last used':<16}  label", file=out)
        for record in records:
            left = "unlimited" if record.remaining is None else str(record.remaining)
            print(
                f"{record.id:>4}  {record.plan:<5}  {record.used_count:>4}/{_quota(record):<4}  {left:>9}  "
                f"{record.status(now):<9}  {_when(record.created_at):<16}  {_when(record.expires_at):<16}  "
                f"{_when(record.last_used_at):<16}  {record.label or '-'}",
                file=out,
            )
        return 0

    record = store.get(args.id)
    if record is None:
        print(f"error: no code with id {args.id}", file=sys.stderr)
        return 1

    if args.command == "revoke":
        if not store.revoke(args.id, now=now):
            print(f"Code {args.id} was already revoked.", file=out)
            return 0
        print(f"Revoked code {args.id}. Its cookies stop working on the next request.", file=out)
        return 0

    if args.command == "extend":
        if args.never:
            expires_at = None
        elif args.until:
            try:
                day = datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                print("error: --until must look like 2026-12-31", file=sys.stderr)
                return 2
            expires_at = int((day + timedelta(days=1)).timestamp())
        else:
            base = max(now, record.expires_at or now)
            expires_at = base + args.days * 86400
        store.set_expiry(args.id, expires_at)
        print(f"Code {args.id} now expires: {_when(expires_at) if expires_at else 'never'}.", file=out)
        return 0

    if args.command == "convert":
        if not store.convert_to_paid(args.id, quota=args.quota):
            print(f"error: code {args.id} is not a trial code", file=sys.stderr)
            return 1
        print(f"Code {args.id} is now paid, quota {args.quota or 'unlimited'} (used so far: {record.used_count}).", file=out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
