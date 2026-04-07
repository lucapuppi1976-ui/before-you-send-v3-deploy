BYS Gate v1 — Deploy steps (checkpoint clean)

1. Upload all files from this pack into the root of the BYS repo.
2. Commit and push.
3. In Render keep/add these env vars:
   - OPENAI_API_KEY = your active OpenAI key
   - BYS_ACCESS_CODE = your private early-access code
   - BYS_GATE_SECRET = any long private random string
   - BYS_GATE_ENABLED = true
4. Optional:
   - BYS_ACCESS_PAGE_URL = https://bb1studio.com/before-you-send/access/
5. Let Render auto-deploy or trigger Manual Deploy -> Deploy latest commit.
6. Test in a NEW incognito window:
   - the app must open on the gate screen
   - wrong code must fail
   - correct code must open the app
   - Settings -> Lock must bring the gate back
   - the gate text must be translated correctly in IT / EN / ES

One-time cleanup on the current GitHub repo:
delete any BB1 leftovers still present in the BYS repo:
- bys/
- privacy.html
- terms.html
- robots.txt
- sitemap.xml
