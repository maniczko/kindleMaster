# Vercel + Railway deployment

KindleMaster uses a split deployment model:

- Vercel hosts the React/Vite UI.
- Railway hosts the Flask conversion API and native PDF/OCR/EPUB toolchain in Docker.
- Supabase can still provide account history and profile storage.

Do not deploy the full converter as Vercel Functions. PDF to EPUB conversion can be long-running and depends on Java/EPUBCheck, OCRmyPDF, Tesseract, Ghostscript, qpdf, poppler, and durable artifacts.

## Vercel frontend

Project settings:

- Framework preset: Other
- Install command: `npm ci`
- Build command: `npm run build:ui:vercel`
- Output directory: `dist/vercel`

Required environment:

```text
VITE_KINDLEMASTER_API_BASE_URL=https://your-kindlemaster-api.up.railway.app
```

`vercel.json` keeps SPA routing pointed at `index.html`.

## Railway backend

Railway should build from `Dockerfile.railway` through `railway.json`.

Required environment:

```text
KINDLEMASTER_BIND_HOST=0.0.0.0
KINDLEMASTER_PUBLIC_BASE_URL=https://your-kindlemaster-api.up.railway.app
KINDLEMASTER_ALLOWED_ORIGINS=https://your-kindlemaster.vercel.app
KINDLEMASTER_ALLOW_LOCAL_DEV_CORS=0
KINDLEMASTER_UPLOAD_DIR=/data/uploads
KINDLEMASTER_ARTIFACT_ROOT=/data/output/artifacts
```

Mount a Railway volume at `/data` so queued job metadata and artifacts survive restarts.

Optional environment:

```text
KINDLEMASTER_SUPABASE_AUTH=1
KINDLEMASTER_SUPABASE_URL=https://PROJECT_REF.supabase.co
KINDLEMASTER_SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
KINDLEMASTER_SMTP_PASSWORD=REPLACE_WITH_SECRET
SENTRY_DSN=https://PUBLIC_KEY@oORG_ID.ingest.sentry.io/PROJECT_ID
```

## Verification

Before promoting the deployment:

```powershell
npm run build:ui:vercel
python -m unittest test_app_runtime_services.py test_sprint4_ui_contracts.py
python kindlemaster.py doctor
```

After Railway deploys, check `/auth/config`, then upload a small PDF through the Vercel UI and confirm `/convert/status/<job_id>` reaches `ready` and the EPUB download succeeds.
