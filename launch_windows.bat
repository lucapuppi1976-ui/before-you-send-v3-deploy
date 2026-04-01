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
  copy .env.example .env >nul
  echo.
  echo HO CREATO IL FILE .env
  echo Aprilo adesso e incolla la tua OPENAI_API_KEY.
  echo Poi riesegui questo file.
  pause
  exit /b 0
)

echo [4/4] Avvio il server...
.venv\Scripts\python.exe run_local.py
exit /b 0

:error
echo.
echo C'e' stato un errore. Controlla Python, pip e i permessi.
pause
exit /b 1
