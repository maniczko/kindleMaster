# Sprint 4 UI Modernization

Sprint 4 introduces a React/Vite UI workspace while keeping the Flask API stable.

## Routes

- `/` serves the React shell after `npm run build:ui`.
- `/app` serves the same React shell for direct workspace links.
- `/legacy` keeps the old Flask/static control panel as a rollback surface.
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

- `Convert`: upload, default route controls, active job, and one next action.
- `Library`: recent jobs and recovery after reload.
- `Quality`: release gate, pipeline status, quality report, and advanced debug details only when needed.
- `Delivery`: download/report links, SMTP readiness, Send-to-Kindle action, and blockers.
- `Settings`: local conversion defaults and non-secret SMTP profile settings.

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

Do not remove the legacy template until the React shell has browser and runtime coverage in CI. The current rollback route is `/legacy`; keep root `/` aligned with the React shell so the default local URL opens the premium console.
