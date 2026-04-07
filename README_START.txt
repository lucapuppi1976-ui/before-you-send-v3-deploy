BYS V3 Deploy Public — Checkpoint v8

Questo pacchetto è completo e pronto da caricare su GitHub e poi deployare su Render.

Cosa contiene:
- Frontend multilingua IT / EN / ES
- Backend FastAPI con AI multilingua
- Gate v1 con codice di accesso verificato lato server
- Traduzioni gate corrette in tutte le lingue
- Asset demo completi (icone, screenshot, vocale)
- render.yaml aggiornato per Render
- .gitignore per evitare __pycache__ e .env

Come usarlo:
1. Carica TUTTI i file di questa cartella nella root del repo BYS.
2. Non caricare .env con la chiave.
3. Su Render lascia OPENAI_API_KEY come env privata.
4. Lascia/aggiungi anche BYS_ACCESS_CODE, BYS_GATE_SECRET e BYS_GATE_ENABLED=true.
5. Deploy.

Nota:
Se il repo GitHub corrente contiene ancora file del sito BB1, rimuovili dopo l'upload.
