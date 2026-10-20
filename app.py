"""Before You Send: ASGI entry point for uvicorn (`uvicorn app:app`).

The application itself lives in the backend/ package.
"""
from __future__ import annotations

import sys

from backend.config import ROOT, ConfigError, load_env_file
from backend.server import create_app

load_env_file(ROOT / ".env")

try:
    app = create_app()
except ConfigError as exc:
    sys.stderr.write(f"\n[BYS] Startup refused: {exc}\n\n")
    raise SystemExit(1) from None
