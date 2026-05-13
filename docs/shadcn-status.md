# Shadcn status

KindleMaster now has an official `components.json` registry configuration for
the Vite/React workspace.

## What is active

- `components.json` points shadcn aliases at `frontend/src`.
- `@/*` alias is configured in `tsconfig.json` and `vite.config.ts`.
- Existing UI primitives remain locally styled source components under
  `frontend/src/components/ui`.

## What is intentionally not changed yet

The current Sprint 4 shell still uses KindleMaster CSS variables and local
classes. It is therefore best described as:

```text
official shadcn registry config + shadcn-style local primitives
```

The full Tailwind/shadcn visual migration should be a separate UI-only change
with Playwright visual verification, because switching the styling substrate can
change spacing, typography, and responsive behavior across the whole app.

## Useful commands

```bash
npx shadcn@latest info --json
npx shadcn@latest add button card dialog progress tabs badge --dry-run
```
