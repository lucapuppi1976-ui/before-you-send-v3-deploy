@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creo ambiente virtuale...
  py -3 -m venv .venv || goto :error
)

echo [2/4] Attivo ambiente virtuale...
call .venv\Scripts\activate.bat || goto :error

echo [3/4] Installo dipendenze...
pip install -r requirements.txt || goto :error

if not exist ".env" (
  echo [4/4] Creo .env da .env.example ...
  copy .env.example .env >nul || goto :error
  .venv\Scripts\python.exe -c "import secrets; print('BYS_GATE_SECRET=' + secrets.token_urlsafe(48))" >> .env || goto :error
  echo.
  echo HO CREATO IL FILE .env con un BYS_GATE_SECRET casuale.
  echo Aprilo adesso e incolla la tua ANTHROPIC_API_KEY, e GROQ_API_KEY se vuoi i vocali.
  echo Poi crea un codice di accesso con:  .venv\Scripts\python.exe tools\codes.py create
  echo e riesegui questo file.
  pause
  exit /b 0
)

echo [4/4] Avvio il server su http://127.0.0.1:8110 ...
.venv\Scripts\python.exe run_local.py
exit /b 0

:error
echo.
echo C'e' stato un errore. Controlla Python, pip e i permessi.
pause
exit /b 1
