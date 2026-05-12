# Trigger.dev setup

KindleMaster has a real Trigger.dev v4 task scaffold and a Python adapter that
submits conversion replay metadata when Trigger.dev is explicitly enabled.

## Files

- `trigger.config.ts` - Trigger.dev project config.
- `trigger/kindlemaster.ts` - `kindlemaster-conversion` task.
- `scripts/trigger_conversion_job.mjs` - local Node bridge used by Flask/Python.
- `runtime_job_adapter.py` - selects Trigger.dev when configured, otherwise local fallback.

## Local fallback

No Trigger.dev credentials are required for normal development:

```bash
python kindlemaster.py serve
```

The runtime provider remains `local`.

## Enable Trigger.dev submission

Set these variables outside git:

```text
TRIGGER_PROJECT_REF=<trigger project ref>
TRIGGER_SECRET_KEY=<trigger secret key>
KINDLEMASTER_TRIGGER_ENABLED=1
KINDLEMASTER_TRIGGER_TASK_ID=kindlemaster-conversion
```

Then start the app:

```bash
python kindlemaster.py serve
```

New conversion jobs include `runtime.provider=trigger.dev` and the Trigger.dev
run id as `runtime.external_id`.

## Trigger.dev commands

```bash
npm run trigger:check
npm run trigger:dev
npm run trigger:deploy
```

## Current execution model

The public Flask API remains stable. Trigger.dev receives replay metadata,
retry policy, timeout, and job context. Local fallback stays active for CI and
developer runs so tests do not require cloud credentials.
