BYS V3 Deploy Public Copy Patch

Cosa sostituisce
- locales/it.json
- locales/en.json
- locales/es.json
- manifest.webmanifest
- app.py

Cosa aggiunge
- .gitignore

Perché esiste
Questa patch rimuove i residui da demo interna/prototipo privato nella versione pubblica:
- "DEMO INTERATTIVA" / "PROTOTIPO PRIVATO"
- riferimenti al server locale sul computer dell'utente
- manifesto PWA con descrizione da prototype
- titolo FastAPI ancora da prototype

Istruzioni rapide
1. Nel repo GitHub, sostituisci i file sopra con questi.
2. Aggiungi anche .gitignore alla root del repo.
3. Fai commit con un messaggio tipo: "Public copy patch for deploy".
4. Su Render, usa Manual sync oppure aspetta il sync automatico del Blueprint.
5. Quando il deploy è completato, verifica home + onboarding in IT/EN/ES.
