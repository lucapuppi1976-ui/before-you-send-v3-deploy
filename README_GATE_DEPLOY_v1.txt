BYS Gate v1 — Deploy steps

1. Upload all files from this pack into the root of the BYS repo.
2. Commit and push.
3. In Render add these env vars:
   - OPENAI_API_KEY = your active OpenAI key
   - BYS_ACCESS_CODE = your private early-access code
   - BYS_GATE_SECRET = any long private random string
4. Optional:
   - BYS_ACCESS_PAGE_URL = https://bb1studio.com/before-you-send/access/
5. Manual Deploy -> Deploy latest commit.
6. Test in incognito:
   - the app must open on the gate screen
   - wrong code must fail
   - correct code must open the app
   - Settings -> Lock must bring the gate back
