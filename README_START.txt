BYS V3 Deploy Public — Gate v1 Master Pack

Questo pacchetto è completo e pronto da caricare su GitHub e poi deployare su Render.

Cosa contiene:
- Frontend multilingua IT / EN / ES
- Backend FastAPI con AI multilingua
- Gate v1 con codice di accesso verificato lato server
- Asset demo completi (icone, screenshot, vocale)
- render.yaml aggiornato per Render
- .gitignore per evitare __pycache__ e .env

Come usarlo:
1. Carica TUTTI i file di questa cartella nella root del repo BYS.
2. Non caricare .env con la chiave.
3. Su Render lascia OPENAI_API_KEY come env privata.
4. Aggiungi anche BYS_ACCESS_CODE e BYS_GATE_SECRET come env private.
5. Deploy.

Nota:
Il gate si attiva solo se BYS_ACCESS_CODE è valorizzata.
