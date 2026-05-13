# Sentry setup

KindleMaster enables Sentry when `SENTRY_DSN` is present in the process
environment or in local `.env.local`.

## Local setup

1. Create a Sentry project for the Python/Flask backend.
2. Copy `.env.example` to `.env.local`.
3. Replace `SENTRY_DSN` with the project DSN.
4. Set:

```text
SENTRY_ENVIRONMENT=development
SENTRY_RELEASE=kindlemaster-local
SENTRY_TRACES_SAMPLE_RATE=0.0
```

5. Validate without sending an event:

```bash
python scripts/check_sentry_config.py
```

6. Send one controlled smoke event when you need end-to-end ingest evidence:

```bash
python scripts/send_sentry_smoke_event.py --send
```

The script writes local evidence to `reports/sentry/smoke_event_latest.json`
and emits a tagged info event named `KindleMaster controlled Sentry smoke
event`. Do not use it for routine test loops.

7. Restart the app:

```bash
python kindlemaster.py serve
```

## Production setup

Set the same variables in the runtime environment:

```text
SENTRY_DSN=<backend project dsn>
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=<release version or commit sha>
SENTRY_TRACES_SAMPLE_RATE=0.0
```

Do not commit `.env.local` or real DSNs.

## Captured context

Conversion failures attach:

- `job_id`
- `input_type`
- `source_type`
- `profile`
- `quality_score`
- `premium_ready`
- `release`
- `environment`

The API and UI expose `sentry_event_id` for failed conversion jobs when Sentry
returns an event id.
