# Vercel + Railway deployment

KindleMaster uses a split deployment model:

- Vercel hosts the React/Vite UI.
- Railway hosts the Flask conversion API and native PDF/OCR/EPUB toolchain in Docker.
- Supabase can provide account history, login, cloud library metadata, and artifact storage metadata.

## Production resource names

Use these names consistently in dashboards, alerts, and release notes:

- Vercel project: `kindlemaster` (public web application).
- Railway project: `kindlemaster-production`.
- Railway service: `kindlemaster-api`.
- Supabase project display name: `kindlemaster-production`.
- Supabase Storage bucket: `kindlemaster-artifacts`.

Do not name the Railway service `kindlemaster`: the name would be ambiguous with
the Vercel project and makes incident routing harder.

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

Do not configure backend-only values in Vercel client environments. Backend-only values include Supabase service-role credentials, SMTP credentials, R2/S3 credentials, and external AI provider API keys.

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

Mount a Railway volume at `/data` so queued job metadata, uploads, and artifacts survive restarts.

Optional Supabase environment:

```text
KINDLEMASTER_AUTH_PROVIDER=supabase
KINDLEMASTER_REQUIRE_LOGIN=0
SUPABASE_URL=https://PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
SUPABASE_SERVICE_ROLE_KEY=<set-in-railway-only>
SUPABASE_ARTIFACT_BUCKET=kindlemaster-artifacts
```

`SUPABASE_PUBLISHABLE_KEY` is browser-safe. `SUPABASE_SERVICE_ROLE_KEY` is backend-only and must be configured only in Railway or another trusted server runtime.

Optional observability environment:

```text
SENTRY_DSN=<set-in-railway-only>
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=<git-sha-or-release-tag>
SENTRY_TRACES_SAMPLE_RATE=0.0
```

Optional Send to Kindle SMTP environment:

```text
KINDLEMASTER_EMAIL_DELIVERY=0
KINDLEMASTER_SMTP_HOST=smtp.example.com
KINDLEMASTER_SMTP_PORT=587
KINDLEMASTER_SMTP_SECURITY=starttls
KINDLEMASTER_SMTP_USERNAME=sender@example.com
KINDLEMASTER_SMTP_FROM=sender@example.com
KINDLEMASTER_SMTP_PASSWORD=<set-in-railway-only>
KINDLEMASTER_EMAIL_MAX_ATTACHMENT_BYTES=52428800
```

Optional R2/S3-compatible artifact storage environment:

```text
KINDLEMASTER_ARTIFACT_STORAGE=r2
R2_BUCKET=kindlemaster-artifacts
R2_ENDPOINT_URL=https://example.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<set-in-railway-only>
R2_SECRET_ACCESS_KEY=<set-in-railway-only>
R2_REGION=auto
ARTIFACT_SIGNED_URL_EXPIRES_SECONDS=900
```

Remote R2/S3 storage requires the backend image to include `boto3`; otherwise the runtime reports remote storage as unavailable and should continue using local `/data` storage.

## Production hardening checklist

Before promoting a public deployment:

- Confirm Railway build context excludes `.env`, `.env.local`, generated PDFs/EPUBs, `output/`, `reports/`, and `node_modules` through `.dockerignore`.
- Confirm Railway has a persistent `/data` volume mounted.
- Confirm `KINDLEMASTER_ALLOW_LOCAL_DEV_CORS=0` and `KINDLEMASTER_ALLOWED_ORIGINS` lists only production Vercel origins.
- Confirm `SENTRY_RELEASE` is a release tag or commit SHA, not `kindlemaster-local`.
- Confirm backend-only values exist only in Railway or trusted server environments.
- Confirm GitHub branch protection requires the `ready-gate` check before merging to `main`.

## Verification

Before promoting the deployment:

```powershell
npm ci
npm run build:ui:vercel
npm run typecheck --if-present
python -m unittest test_app_runtime_services.py test_sprint4_ui_contracts.py
python kindlemaster.py doctor
```

After Railway deploys, check `/auth/config`, then upload a small PDF through the Vercel UI and confirm `/convert/status/<job_id>` reaches `ready` and the EPUB download succeeds.
