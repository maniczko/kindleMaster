# KindleMaster tooling strategy

This document records the current tool decisions for the Kindle premium
converter target. It is intentionally narrower than a SaaS platform plan.

## P0 control plane

Codex, GitHub, and Linear are the engineering control plane.

- Codex owns local implementation, verification, and agent workflows.
- GitHub owns version control, pull requests, and READY enforcement.
- Linear owns planning and issue truth.

Do not fragment task truth into Notion, no-code builders, or ad hoc trackers
unless product scope changes deliberately.

## P0 release truth

Playwright, Vitest, EPUBCheck, and corpus gates are release truth.

- Playwright protects browser/runtime confidence.
- Vitest protects static UI/API contract surfaces.
- EPUBCheck protects EPUB technical validity.
- Corpus gates protect cross-document Kindle quality.

Do not treat a single successful conversion as release evidence when corpus or
strict audit evidence disagrees.

## P1 observability

Sentry stays enabled for backend conversion failures. The local verification
path is:

```powershell
python scripts/check_sentry_config.py
python scripts/send_sentry_smoke_event.py --send
```

The smoke script emits one tagged info event and writes evidence under
`reports/sentry/`. It is for controlled ingest checks, not routine test loops.

## P1 async worker path

Trigger.dev is an optional async worker path. Local runtime fallback remains the
canonical v1 execution model.

```powershell
npm run trigger:check
```

Trigger.dev must not become required for local conversion, tests, or CI until
the product deliberately moves beyond local-first.

## P2 cloud persistence

Postgres, Neon, Supabase, and object storage such as R2 are deferred until a
hosted cloud library is approved.

- Use Postgres/Neon or Supabase only when persistent cloud library state is in
  scope.
- Use R2 or equivalent object storage only when hosted EPUB/report artifacts
  need durable cloud storage.

Local filesystem artifacts remain the source for v1.

## P3 isolated experiments

Surya stays isolated. Do not install it into the main Python environment because
it can pull heavy ML dependencies and pin image-processing packages.

No-code builders are acceptable for visual exploration only. They should not
own the production conversion pipeline or replace the current Flask/Python
quality stack.

## AI quality posture

GPT/OpenAI is configured as an optional live quality provider, not as a
mandatory conversion dependency. The current AI quality layer is
provider-injected and offline-safe:

- OCR and TOC AI providers can be injected explicitly.
- `KINDLEMASTER_OPENAI_QUALITY=1` enables the OpenAI provider.
- Without a provider, KindleMaster records fallback/skipped audit evidence.
- Deterministic output remains the source of truth unless a future accepted AI
  application path is added deliberately.
- `KINDLEMASTER_AI_FEEDBACK_RECORD=1` records local learning signals under
  `reports/ai-quality-feedback/`.

This keeps premium quality gates auditable while allowing controlled AI review
when local credentials are configured.
