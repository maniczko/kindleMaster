# Sprint 4 UI Modernization

Sprint 4 introduces a React/Vite UI workspace while keeping the Flask API stable.

## Routes

- `/` always redirects to `/app`.
- `/app` serves the React shell after `npm run build:ui`.
- `python kindlemaster.py serve` builds the React shell automatically when `static/react/index.html` is missing or stale.
- If the React build is missing because build was skipped or failed, `/app` returns `503` with a clear build-missing message.
- `/legacy` redirects to `/app` by default. Set `KINDLEMASTER_ENABLE_LEGACY_UI=1` only for explicit legacy debug access.
- `static/react/` is generated build output and is ignored by Git.

## Commands

```powershell
npm run dev:ui
npm run build:ui
npm run test:ui
npm run test:contracts:regression
npm run test:e2e
```

The Vite dev server proxies `/convert` and `/analyze` to `http://127.0.0.1:5001`.

## UI Contract

The React shell is an operational dashboard, not a landing page. It contains:

- upload and profile controls,
- pipeline status,
- job details,
- quality report view,
- artifact/download panel,
- debug panel with Sentry event context.

The shared quality-state adapter consumes:

- `score`
- `sendable`
- `kindle_ready`
- `premium_ready`
- `status`
- `blockers`
- `warnings`
- `reports`
- `artifacts`
- `sentry_event_id`

## Migration Rule

Do not remove the legacy template until the Sprint 4 React route has browser and runtime coverage in CI. The root route no longer falls back to legacy; legacy access is debug-only behind `KINDLEMASTER_ENABLE_LEGACY_UI=1`.
