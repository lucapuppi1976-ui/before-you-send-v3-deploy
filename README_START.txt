Before You Send — V3 Public Deploy (Render)

What this pack is
- A public-deploy branch built from V2 LOCKED.
- Same product flow, but prepared for Render as a single public FastAPI web service.

Files to care about
- app.py
- index.html
- app.js
- styles.css
- locales/it.json
- locales/en.json
- locales/es.json
- requirements.txt
- .python-version
- render.yaml

Fastest deploy path
1. Create a new GitHub repo.
2. Upload the contents of this folder to the repo root.
3. In Render, create a new Blueprint or Web Service from that repo.
4. Add OPENAI_API_KEY as an environment variable.
5. Deploy.

If deploying manually on Render
- Build Command: pip install -r requirements.txt
- Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
- Health Check Path: /api/health

Recommended env vars
- OPENAI_API_KEY=your key
- BYS_PUBLIC_DEPLOY=true
- PYTHON_VERSION=3.14.3

Local smoke test
- python -m venv .venv
- .venv\Scripts\activate
- pip install -r requirements.txt
- copy .env.example .env
- add OPENAI_API_KEY to .env
- python run_local.py

Notes
- This is one service: frontend static files + FastAPI backend together.
- The API key stays server-side.
- Free Render instances are fine for test/public preview, not for serious production.
