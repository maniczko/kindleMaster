# Sprint 4 UI Modernization

Sprint 4 introduces a React/Vite UI workspace while keeping the Flask API stable.

## Routes

- `/` opens the React shell when the production build exists.
- `/app` serves the React shell after `npm run build:ui`.
- If the React build is missing, `/app` returns a small unbuilt-state page with the local commands.
- If the React build is missing, `/` falls back to the legacy static control panel.
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

Do not remove the legacy template until the Sprint 4 React route has browser and runtime coverage in CI. The root route now prefers React when `static/react/index.html` exists, while retaining the legacy template as the clean-checkout fallback.
