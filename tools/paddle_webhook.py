"""Paddle (sandbox in the test copy, live once Paddle approves bys.bb1studio.com): the notification destination towards
the webhook of Before You Send.

Paddle signs every notification with the secret of the destination (pdl_ntfset_…); the server checks it
(backend/payments.py, Paddle.verify_webhook) and adds the analyses of a paid pack even if the app is never opened again.
The destination gets transaction.paid and transaction.completed of the whole Paddle account (the payments of
Plantatam and Vistobueno too: the webhook ignores what is not a pack of Before You Send). A second run finds the
destination by its address and creates nothing. With --write-env the secret goes into .env as
BYS_PADDLE_WEBHOOK_SECRET; it is never printed. The same script as Plantatam's (3/11/2026).
  python tools/paddle_webhook.py [--url https://bys.bb1studio.com/api/paddle/webhook] [--write-env]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional, TextIO

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import ConfigError, load_env_file, load_settings  # noqa: E402
from backend.payments import API_URLS  # noqa: E402
from tools.paddle_catalogo import Call, http_call, problem, write_env  # noqa: E402

DEFAULT_URL = "https://bys.bb1studio.com/api/paddle/webhook"
EVENTS = ["transaction.paid", "transaction.completed"]  # as WEBHOOK_EVENTS in backend/server.py
SECRET_ENV = "BYS_PADDLE_WEBHOOK_SECRET"


def main(argv: Optional[list[str]] = None, out: TextIO = sys.stdout, call: Optional[Call] = None,
         env_path: Path = ROOT / ".env") -> int:
    parser = argparse.ArgumentParser(prog="paddle_webhook.py", description="Create the webhook destination of Before You Send.")
    parser.add_argument("--url", default=DEFAULT_URL, help="the https address of /api/paddle/webhook")
    parser.add_argument("--write-env", action="store_true", help="write the signing secret into .env")
    args = parser.parse_args(argv)
    if not args.url.startswith("https://") or not args.url.endswith("/api/paddle/webhook"):
        print("error: --url must be the https address of /api/paddle/webhook", file=sys.stderr)
        return 2
    load_env_file(env_path)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if call is None:
        if not settings.paddle_api_key:
            print("error: BYS_PADDLE_API_KEY is not set in .env", file=sys.stderr)
            return 2
        call = http_call(API_URLS[settings.paddle_environment], settings.paddle_api_key)
    print(f"Paddle {settings.paddle_environment}", file=out)

    status, body = call("GET", "/notification-settings", None)
    if status != 200:
        print(f"error: notification settings {problem(status, body)}", file=sys.stderr)
        return 1
    found: Optional[dict[str, Any]] = next(
        (item for item in body.get("data", []) if item.get("destination") == args.url), None)
    if found is None:
        status, body = call("POST", "/notification-settings", {
            "description": "Before You Send: pacchetti di analisi pagati",
            "type": "url",
            "destination": args.url,
            "subscribed_events": EVENTS,
            "include_sensitive_fields": False,
        })
        if status not in (200, 201):
            print(f"error: notification setting {problem(status, body)}", file=sys.stderr)
            return 1
        found = body["data"]
        print(f"  destination created: {found['id']} {args.url}", file=out)
    else:
        print(f"  destination already there: {found['id']} {args.url}", file=out)
    secret = found.get("endpoint_secret_key") or ""
    if not secret:
        print("error: Paddle did not give the signing secret of the destination", file=sys.stderr)
        return 1
    if args.write_env:
        write_env(env_path, {SECRET_ENV: secret})
        print(f"Written into .env: {SECRET_ENV} (not printed)", file=out)
    else:
        print(f"The signing secret was not printed; run again with --write-env to put it in .env as {SECRET_ENV}.",
              file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
