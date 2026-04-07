BYS APP — MASTER PACK v7 GATE V1

This is the cumulative root-ready pack for the BYS app repository.
It already includes:
- complete public deploy base pack
- corrective patch v2
- scroll fix v4
- splash fix v5
- final app.py capture/reply/localization hardening
- Gate v1 with server-side unlock cookie
- render.yaml updated with gate env placeholders
- .gitignore restored
- release marker preserved

How to use:
1. Extract this zip.
2. Upload ALL files in this folder into the root of the repo before-you-send-v3-deploy.
3. Do not upload .env or __pycache__.
4. Keep OPENAI_API_KEY only as a Render environment variable.
5. Add BYS_ACCESS_CODE and BYS_GATE_SECRET in Render environment.
6. Commit and deploy.

Important:
The gate is enabled only when BYS_ACCESS_CODE is set.
