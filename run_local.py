"""Run Before You Send on this computer only: http://127.0.0.1:8110 (set BYS_PORT to change)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"  # local only: the server is never exposed to the network
PORT = int(os.getenv("BYS_PORT", "8110"))

if __name__ == "__main__":
    print("\nBefore You Send")
    print(f"Locale: http://{HOST}:{PORT}\n")
    command = [sys.executable, "-m", "uvicorn", "app:app", "--host", HOST, "--port", str(PORT)]
    raise SystemExit(subprocess.run(command, cwd=str(ROOT), check=False).returncode)
