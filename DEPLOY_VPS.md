# Deploy su VPS Windows: bys.bb1studio.com

Destinazione: Windows Server 2022, Python 3.14 in `C:\Program Files\Python314`, Caddy come servizio `CaddyBB1` con configurazione in `C:\caddy\Caddyfile.production`. BYS gira come servizio separato, in ascolto solo su `127.0.0.1:8110`, con **1 worker** (la RAM libera è circa 2,5 GB).

Sulla stessa macchina girano n8n (servizio `n8n`, `127.0.0.1:5678`) e il software di trading MT5:

- non toccare MT5 e non riavviare n8n;
- non usare le porte già occupate: 5678, 5690-5691, 8090-8097, 8780;
- il servizio BYS gira con un account locale dedicato e senza privilegi, mai come LocalSystem o Administrator.

## 1. Account e cartelle

In PowerShell da amministratore:

```powershell
$pw = Read-Host -AsSecureString "Password per svc-bys"
New-LocalUser -Name "svc-bys" -Password $pw -PasswordNeverExpires -UserMayNotChangePassword -Description "Before You Send"
# Non aggiungerlo al gruppo Administrators.

New-Item -ItemType Directory C:\bys\app, C:\bys\data, C:\bys\logs | Out-Null
```

Permessi limitati alle cartelle di BYS: codice in sola lettura, dati e log in scrittura.

```powershell
icacls C:\bys\app  /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "svc-bys:(OI)(CI)RX"
icacls C:\bys\data /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "svc-bys:(OI)(CI)M"
icacls C:\bys\logs /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "svc-bys:(OI)(CI)M"
```

Controlla che `svc-bys` non abbia accesso alle cartelle di MT5 e di n8n (di solito stanno nei profili di altri utenti, che un utente standard non può leggere).

## 2. Installazione

```powershell
cd C:\bys\app
git clone https://github.com/lucapuppi1976-ui/before-you-send-v3-deploy.git .
& "C:\Program Files\Python314\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Le versioni in `requirements.txt` sono fissate e si installano da wheel su Python 3.14 per Windows x64.

## 3. Variabili d'ambiente

Mettile in `C:\bys\app\.env`: grazie ai permessi del punto 1 lo leggono solo gli amministratori e `svc-bys`. Le variabili impostate con NSSM finiscono nel registro dei servizi, che è più esposto.

| Variabile | Valore |
|---|---|
| `ANTHROPIC_API_KEY` | Chiave dedicata a BYS (vedi sotto). |
| `BYS_MODEL` | `claude-sonnet-5-5` (default, lo stesso modello dell'assistente WhatsApp). |
| `BYS_EFFORT` | `low` |
| `GROQ_API_KEY` | Chiave Groq (meglio una chiave separata da quella dell'assistente WhatsApp). Vuota = vocali disattivati. |
| `BYS_TRANSCRIBE_MODEL` | `whisper-large-v3-turbo` |
| `BYS_GATE_SECRET` | Almeno 32 caratteri casuali: `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `BYS_GATE_EPOCH` | `1` (cambiandolo si disconnettono tutti) |
| `BYS_DB_PATH` | `C:\bys\data\bys.db` |
| `BYS_TRIAL_QUOTA` | `30` |
| `BYS_ACCESS_PAGE_URL` | `https://bb1studio.com/before-you-send/access/` |
| `BYS_ADMIN_TOKEN` | Almeno 32 caratteri casuali, solo se n8n deve creare codici. |
| `BYS_ACCESS_CODE` | Facoltativo: codice tester condiviso e illimitato. |

L'elenco completo è nel `README.md`. Il server non parte se il gate è attivo e `BYS_GATE_SECRET` manca o è più corto di 32 caratteri: l'errore è scritto nel log `bys.err.log`.

**Chiave Anthropic separata.** Nella Console Anthropic crea un workspace solo per BYS, imposta un limite di spesa mensile sul workspace e genera lì la chiave usata da BYS. Così un abuso o un errore resta confinato e non tocca la chiave dell'assistente WhatsApp.

Primo codice di prova:

```powershell
.\.venv\Scripts\python.exe tools\codes.py create --plan trial --label "test"
```

## 4. Servizio Windows con NSSM

Comando da eseguire (lo lancia NSSM):

```
uvicorn app:app --host 127.0.0.1 --port 8110 --proxy-headers --forwarded-allow-ips 127.0.0.1 --workers 1
```

`--proxy-headers` con `--forwarded-allow-ips 127.0.0.1` fa sì che uvicorn accetti `X-Forwarded-For` e `X-Forwarded-Proto` solo da Caddy sulla stessa macchina: i limiti per IP vedono l'IP vero del client e il cookie riceve `Secure` sulle richieste https.

```powershell
nssm install BYS "C:\bys\app\.venv\Scripts\python.exe" "-m uvicorn app:app --host 127.0.0.1 --port 8110 --proxy-headers --forwarded-allow-ips 127.0.0.1 --workers 1"
nssm set BYS AppDirectory C:\bys\app
nssm set BYS DisplayName "Before You Send"
nssm set BYS ObjectName .\svc-bys "<password di svc-bys>"
nssm set BYS Start SERVICE_AUTO_START
nssm set BYS AppExit Default Restart
nssm set BYS AppRestartDelay 5000
nssm set BYS AppThrottle 10000
nssm set BYS AppStdout C:\bys\logs\bys.out.log
nssm set BYS AppStderr C:\bys\logs\bys.err.log
nssm set BYS AppRotateFiles 1
nssm set BYS AppRotateOnline 1
nssm set BYS AppRotateBytes 10485760
sc.exe failure BYS reset= 86400 actions= restart/5000/restart/5000/restart/60000
nssm start BYS
```

Se l'avvio fallisce con un errore di accesso, concedi a `svc-bys` il diritto "Accesso come servizio" (`secpol.msc`, Criteri locali, Assegnazione diritti utente). I log sono in `C:\bys\logs` e contengono IP e percorsi delle richieste, mai il contenuto dei messaggi. La porta 8110 non va aperta nel firewall: uvicorn ascolta solo su 127.0.0.1.

Verifica locale: `curl.exe -s http://127.0.0.1:8110/api/health` deve rispondere `{"ok":true}`.

## 5. Caddy

Aggiungi questo blocco, come nuovo sito, in `C:\caddy\Caddyfile.production` senza modificare gli altri:

```caddyfile
bys.bb1studio.com {
	encode zstd gzip

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		-Server
	}

	# L'API admin è solo per strumenti locali: dal sito pubblico risponde 404.
	@admin path /admin /admin/*
	respond @admin 404

	request_body {
		max_size 20MB
	}

	reverse_proxy 127.0.0.1:8110
}
```

Valida e poi ricarica senza riavviare il servizio (reload graduale tramite l'API admin di Caddy):

```powershell
caddy validate --config C:\caddy\Caddyfile.production --adapter caddyfile
caddy reload --config C:\caddy\Caddyfile.production --adapter caddyfile
```

Se `validate` segnala errori non ricaricare. Non riavviare `CaddyBB1` e non riavviare `n8n`. Se nel Caddyfile l'API admin è disattivata (`admin off`), `caddy reload` non funziona: in quel caso pianifica un riavvio di `CaddyBB1` in un momento tranquillo.

## 6. DNS

Nella zona `bb1studio.com`:

| Tipo | Nome | Valore |
|---|---|---|
| A | `bys` | IPv4 pubblico della VPS |
| AAAA | `bys` | IPv6 della VPS, solo se la VPS lo ha e Caddy è raggiungibile in IPv6 |

- Elimina il vecchio record di `bys` che punta a Render (un CNAME verso `bys-v3-public.onrender.com`): un nome non può avere sia CNAME sia A.
- Se la zona ha record CAA, devono autorizzare `letsencrypt.org` (e `zerossl.com`, l'altra autorità usata da Caddy).
- Durante il passaggio usa un TTL basso (300 s). Caddy ottiene il certificato da solo quando il DNS punta alla VPS (porte 80 e 443 già aperte per Caddy).

## 7. Smoke test

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" https://bys.bb1studio.com/app.py          # 404
curl.exe -s https://bys.bb1studio.com/api/health                                   # {"ok":true}
curl.exe -s -o NUL -w "%{http_code}`n" https://bys.bb1studio.com/admin/codes       # 404 (bloccato da Caddy)
curl.exe -sI https://bys.bb1studio.com/ | findstr /i "content-security-policy strict-transport-security"

# Codice sbagliato: 403 per cinque volte, poi 429 (vale per il tuo IP per un minuto)
'{"code":"BYS-WRONG-00000"}' | Set-Content -Encoding ascii wrong.json
1..6 | ForEach-Object { curl.exe -s -o NUL -w "%{http_code} " -X POST -H "Content-Type: application/json" --data-binary "@wrong.json" https://bys.bb1studio.com/api/access/unlock }
```

Codice corretto: apri `https://bys.bb1studio.com` in una finestra privata, inserisci il codice creato al punto 3, premi "Prova subito" e controlla che arrivi l'analisi e che "analisi rimaste" scenda di uno.

## 8. Creare codici da n8n

n8n gira sulla stessa macchina, quindi chiama BYS direttamente (non tramite `https://bys.bb1studio.com`, dove `/admin` è bloccato):

```
POST http://127.0.0.1:8110/admin/codes
Authorization: Bearer <BYS_ADMIN_TOKEN>
Content-Type: application/json

{"plan": "trial", "label": "cliente@example.com"}
```

Risposta `201`: `{"id": 7, "code": "BYS-XXXXX-XXXXX", "plan": "trial", "quota": 30, "label": "...", "expiresAt": null}`. Il codice appare solo in questa risposta. Campi facoltativi: `"plan": "paid"`, `"quota"`, `"days"` (scadenza). L'endpoint risponde soltanto a richieste da 127.0.0.1 senza `X-Forwarded-For` e con il token giusto; senza `BYS_ADMIN_TOKEN` è disattivato.

## 9. Manutenzione

Aggiornare:

```powershell
cd C:\bys\app
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
nssm restart BYS
```

Backup del database (si può fare con il servizio acceso):

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; sqlite3.connect(r'C:\bys\data\bys.db').backup(sqlite3.connect(r'C:\bys\data\bys-backup.db'))"
```

Revocare un codice: `tools\codes.py revoke ID`. Disconnettere tutti: cambia `BYS_GATE_EPOCH` e riavvia il servizio.

## 10. Passaggio da Render

- `render.yaml` ha `autoDeploy: true`: **ogni merge su `main` fa un deploy su Render**. Prima del merge imposta su Render `ANTHROPIC_API_KEY`, `BYS_GATE_SECRET` (almeno 32 caratteri, altrimenti la nuova versione non parte), `BYS_GATE_EPOCH` ed eventualmente `GROQ_API_KEY`; togli `OPENAI_API_KEY` e `BYS_PUBLIC_DEPLOY`.
- Su Render il disco non è persistente: il database dei codici si azzera a ogni deploy. Finché il DNS punta a Render usa `BYS_ACCESS_CODE`.
- Quando il DNS punta alla VPS e gli smoke test passano, sospendi il servizio su Render.
