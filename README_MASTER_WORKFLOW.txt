BYS APP — CHECKPOINT v8 (gate + locales + cleanup)

This is the cumulative root-ready pack for the BYS app repository.
It already includes:
- complete public deploy base pack
- corrective patch v2
- scroll fix v4
- splash fix v5
- final app.py capture/reply/localization hardening
- Gate v1 with server-side unlock cookie
- gate locale keys for IT / EN / ES
- service-worker cache bump for the gate locale fix
- render.yaml updated with explicit BYS_GATE_ENABLED
- .gitignore restored
- release marker preserved

How to use:
1. Extract this zip.
2. Upload ALL files in this folder into the root of the repo before-you-send-v3-deploy.
3. Do not upload .env or __pycache__.
4. Keep OPENAI_API_KEY only as a Render environment variable.
5. Keep/add BYS_ACCESS_CODE, BYS_GATE_SECRET and BYS_GATE_ENABLED in Render.
6. Commit and deploy.

Important:
This checkpoint assumes the gate must stay enabled on the public app.
