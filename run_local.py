from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.getenv('BYS_PORT', '8000'))


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


if __name__ == '__main__':
    ip = local_ip()
    print('\nBefore You Send — Live AI')
    print(f'Local:   http://127.0.0.1:{PORT}')
    print(f'Phone:   http://{ip}:{PORT}')
    print('Apri il link Phone dal telefono sulla stessa Wi‑Fi.\n')
    subprocess.run([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '0.0.0.0', '--port', str(PORT)], cwd=str(ROOT), check=False)
