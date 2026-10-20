# Before You Send (BYS)

Web app (PWA) per capire un messaggio ricevuto e controllare il tono di uno da inviare.

- **Decode**: analizza un messaggio ricevuto da testo incollato, da un vocale o da uno screenshot: verdetto, lettura veloce, 3 segnali, 2 avvertenze, toni e 3 risposte pronte.
- **SendScore**: dà un punteggio 0-100 a una bozza, indica cosa migliorare e propone 4 riscritture (più chiara, più calda, più ferma, più breve).
- Lingue: italiano, inglese, spagnolo.

## Come è fatta

| Parte | Dove | Note |
|---|---|---|
| Frontend | `public/` | `index.html`, `app.js`, `styles.css`, `sw.js`, `locales/`, `assets/`. È l'unica cartella servita, montata su `/`. |
| Backend | `backend/`, `app.py` | FastAPI, avviato con `uvicorn app:app`. |
| Analisi | Claude (API Anthropic) | SDK ufficiale `anthropic`, output strutturato (JSON schema), modello e effort da variabili d'ambiente. |
| Vocali | Groq | `whisper-large-v3-turbo` tramite l'endpoint compatibile OpenAI. Senza `GROQ_API_KEY` l'upload audio si disattiva; la trascrizione incollata funziona comunque. |
| Accesso | `data/bys.db` (SQLite) | Codici personali con quota (prova da 30 analisi o piano a pagamento), più un codice tester condiviso facoltativo. Il DB conserva solo l'hash dei codici. |

Privacy: il server non salva testi, immagini o audio; i log contengono solo metadati (modello, id richiesta, token). La cronologia resta nel browser dell'utente.

Sicurezza in breve: gate sempre attivo salvo `BYS_GATE_ENABLED=false` (e allora il server lo segnala all'avvio); il server non parte senza un `BYS_GATE_SECRET` di almeno 32 caratteri; cookie firmato HttpOnly; limiti per IP, per dimensione delle richieste e per quota; CSP e header di sicurezza su ogni risposta; nessun dettaglio d'errore dei provider arriva al browser.

## Configurazione

Le variabili si leggono dall'ambiente o da un file `.env` nella cartella del progetto (vedi `.env.example`; i valori già presenti nell'ambiente hanno la precedenza).

| Variabile | Default | A cosa serve |
|---|---|---|
| `ANTHROPIC_API_KEY` | (vuota) | Chiave Anthropic per testi e screenshot. Senza chiave l'analisi risulta non disponibile. |
| `BYS_MODEL` | `claude-sonnet-5-5` | Modello Claude. Deve supportare output strutturato ed effort (es. `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-haiku-5-5`). |
| `BYS_EFFORT` | `low` | `low`, `medium`, `high`, `xhigh` o `max`. |
| `BYS_MAX_TOKENS` | `16000` | Tetto dei token in uscita (ragionamento incluso). |
| `BYS_AI_TIMEOUT_SECONDS` | `60` | Timeout di ogni tentativo verso Anthropic. |
| `BYS_AI_MAX_RETRIES` | `2` | Nuovi tentativi automatici dell'SDK (429, 5xx, rete). |
| `GROQ_API_KEY` | (vuota) | Chiave Groq per i vocali; vuota = vocali disattivati. |
| `BYS_TRANSCRIBE_MODEL` | `whisper-large-v3-turbo` | Modello di trascrizione Groq. |
| `BYS_TRANSCRIBE_TIMEOUT_SECONDS` | `60` | Timeout della trascrizione. |
| `BYS_GATE_ENABLED` | attivo | Si spegne solo con `false`, `0`, `no` o `off`. |
| `BYS_GATE_SECRET` | (obbligatoria) | Almeno 32 caratteri casuali; firma i cookie. Senza, il server non parte. |
| `BYS_GATE_EPOCH` | `1` | Fa parte della firma: cambiarlo disconnette tutti. |
| `BYS_ACCESS_CODE` | (vuota) | Codice tester condiviso, senza limiti. Facoltativo. |
| `BYS_ACCESS_PAGE_URL` | `https://bb1studio.com/before-you-send/access/` | Pagina "opzioni di accesso" mostrata nell'app e nei messaggi di prova terminata. |
| `BYS_ACCESS_COOKIE_DAYS` | `30` | Durata del cookie di accesso. |
| `BYS_ACCESS_COOKIE_NAME` | `bys_access` | Nome del cookie. |
| `BYS_DB_PATH` | `./data/bys.db` | Database dei codici; i percorsi relativi partono dalla cartella del progetto. |
| `BYS_TRIAL_QUOTA` | `30` | Analisi incluse in un codice di prova. |
| `BYS_ADMIN_TOKEN` | (vuota) | Token per `POST /admin/codes` (almeno 32 caratteri); vuoto = endpoint disattivato. |
| `BYS_MAX_IMAGE_MB` | `5` | Dimensione massima di uno screenshot. |
| `BYS_MAX_AUDIO_MB` | `15` | Dimensione massima di un vocale. |
| `BYS_RL_UNLOCK_PER_MINUTE` / `_PER_HOUR` | `5` / `30` | Tentativi di sblocco per IP. |
| `BYS_RL_AI_PER_MINUTE` / `_PER_DAY` | `10` / `200` | Analisi per IP. `0` disattiva quel limite. |
| `BYS_LOG_LEVEL` | `INFO` | Livello dei log. |
| `BYS_PORT` | `8110` | Solo per `run_local.py`. |

Le richieste JSON sono limitate a 64 KB e i testi a 6000 caratteri.

## Avvio in locale

Windows: doppio clic su `launch_windows.bat` (crea `.venv`, installa i pacchetti, crea `.env` con un `BYS_GATE_SECRET` casuale; poi aggiungi le chiavi API e rilancialo).

A mano (Python 3.10 o superiore; testato su 3.13 e 3.14):

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt     # su Windows: .venv\Scripts\python.exe
cp .env.example .env                                     # poi inserisci le chiavi e BYS_GATE_SECRET
.venv/bin/python tools/codes.py create --label "prova"   # stampa un codice BYS-XXXXX-XXXXX
.venv/bin/python run_local.py                            # http://127.0.0.1:8110
```

`run_local.py` ascolta solo su 127.0.0.1.

## Codici di accesso

```bash
python tools/codes.py create --plan trial --label "mario@example.com"   # 30 analisi
python tools/codes.py create --plan paid --days 365                     # illimitato per un anno
python tools/codes.py list
python tools/codes.py revoke 12
python tools/codes.py extend 12 --days 30        # oppure --until 2026-12-31 / --never
python tools/codes.py convert 12 --quota 500     # da prova a pagamento
```

Ogni codice viene mostrato una sola volta. Ogni analisi (anche un vocale) consuma un'unità prima di chiamare il provider; se il provider fallisce l'unità viene restituita. A quota finita, o con codice scaduto o revocato, l'app mostra "Prova terminata" con il link alla pagina di accesso.

Per n8n sulla stessa macchina c'è `POST http://127.0.0.1:8110/admin/codes` (dettagli in `DEPLOY_VPS.md`).

## Test

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
node --check public/app.js && node --check public/sw.js
```

I test non usano la rete: il client Anthropic e `requests.post` (Groq) sono sostituiti da finti.

## Confronto tra modelli

```bash
python tools/compare_models.py          # stampa solo la stima dei costi
python tools/compare_models.py --yes    # esegue (a pagamento, serve ANTHROPIC_API_KEY)
```

Usa gli stessi prompt e la stessa normalizzazione dell'app su `tools/samples.json` (24 esempi) con Opus, Sonnet e Haiku attuali, e scrive un report Markdown e HTML in `tools/reports/`.

## Deploy

- VPS Windows dietro Caddy: vedi [`DEPLOY_VPS.md`](DEPLOY_VPS.md).
- Render (transitorio): `render.yaml` ha `autoDeploy: true`, quindi **ogni merge su `main` fa partire un deploy su Render**. Prima del merge imposta su Render `ANTHROPIC_API_KEY`, `BYS_GATE_SECRET` (almeno 32 caratteri, altrimenti la nuova versione non parte), `BYS_GATE_EPOCH` ed eventualmente `GROQ_API_KEY`; togli `OPENAI_API_KEY` e `BYS_PUBLIC_DEPLOY`. Il disco di Render non è persistente: lì i codici personali spariscono a ogni deploy, quindi usa `BYS_ACCESS_CODE`.

I file `*.txt` nella cartella principale sono note storiche del vecchio flusso (OpenAI e upload su Render) e non vanno più seguiti.
