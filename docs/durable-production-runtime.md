# Durable production runtime

KindleMaster keeps the existing process-local thread runtime for local development. Hosted Railway deployments use independent supervisor, API and worker processes backed by SQLite on the persistent `/data` volume.

## Runtime processes

`python production_server.py` is the container's supervisor process. It starts and independently monitors:

- one `production_api.py` child running Waitress and the HTTP application;
- one or more `production_worker.py` conversion children;
- a shared SQLite database containing conversion job state, queue commands and leases.

If the API child exits, the supervisor restarts only the API; running workers continue. If one worker exits, only that worker is restarted while the API and other workers continue. A supervisor/container restart stops all child processes, but durable state and retained input on `/data` allow lease-based recovery after startup.

The API never executes expensive conversion work when `KINDLEMASTER_DURABLE_RUNTIME=1`. It persists the job and enqueues a replayable command. Workers claim commands atomically and call the existing conversion pipeline outside the API process. Hosted `POST /convert` is disabled; clients use `/convert/start`.

## Required Railway configuration

```text
KINDLEMASTER_DURABLE_RUNTIME=1
KINDLEMASTER_DURABLE_DB_PATH=/data/kindlemaster/runtime.sqlite3
KINDLEMASTER_WORKER_PROCESSES=1
KINDLEMASTER_JOB_MAX_ATTEMPTS=3
KINDLEMASTER_JOB_LEASE_SECONDS=180
KINDLEMASTER_JOB_HEARTBEAT_SECONDS=10
KINDLEMASTER_JOB_RETRY_BACKOFF_SECONDS=10
```

A persistent Railway volume must be mounted at `/data`. Do not enable multiple Railway replicas with independent volumes; the SQLite contract coordinates multiple processes that share one volume, not separate hosts with isolated filesystems.

## State contract

Queue states:

```text
queued -> leased -> running -> succeeded
                         |-> retry_wait -> leased
                         |-> failed
                         |-> dead_letter
queued/retry_wait        |-> cancelled
```

A worker owns a time-bounded lease. Heartbeats extend it. Another worker may reclaim an expired `leased` or `running` command. Attempts are audited and retry delay uses exponential backoff. Non-retryable validation/input errors do not retry.

The canonical job payload and the command queue are stored in the same SQLite database. WAL and `BEGIN IMMEDIATE` transactions prevent two worker processes from claiming the same command.

Existing code paths that read a job from `_CONVERSION_JOBS` and mutate the returned dictionary remain durable through write-through compatibility. The compatibility layer performs a three-way merge onto the freshest SQLite record so a stale API view cannot overwrite a newer worker status, heartbeat or artifact.

## Idempotency

Authenticated callers may send:

```http
Idempotency-Key: <opaque-client-generated-key>
```

The key is scoped to the authenticated owner. Repeating a start or retry request returns the canonical job rather than scheduling duplicate work. Raw bearer tokens and cloud access tokens are never persisted in queue payloads.

Anonymous ownership and cross-browser isolation are finalized by #341. Until that change is merged, queue owner metadata for guests is job-scoped and is not an authorization mechanism.

## Cancellation

Hosted runtime exposes:

```http
POST /convert/cancel/<job_id>
```

Cancellation is supported only while the queue record is `queued` or `retry_wait`, when no conversion process owns the job. The queue and public job record become `cancelled` and cloud metadata is synchronized.

A `leased` or `running` job returns HTTP 409 with `active_cancellation_unsupported`. The system deliberately fails closed rather than marking a job cancelled while its conversion thread continues writing. True active-stage cancellation requires explicit cancellation hooks inside extraction, OCR, assembly, validation and packaging and is tracked separately from this safe pre-execution contract.

## Recovery behavior

- API child restart: workers continue and the supervisor restarts only `production_api.py`.
- Worker crash: the supervisor restarts only the failed worker; the expired lease is reclaimed.
- Supervisor/container restart: all processes restart, while the database and retained input survive on `/data`; a worker reclaims the job after lease expiry.
- Source path missing: the worker restores the input from the retained input artifact before retry.
- Duplicate completion: only the current lease owner may complete the queue record.
- Exhausted retry: the command enters `dead_letter` and stays visible for operator review.
- Worker failure: cloud status is synchronized through the server-side path without retaining a browser token.
- Legacy JSON history: records are migrated once without overwriting newer SQLite state.

## Local development

Continue to use:

```powershell
python kindlemaster.py serve
```

This keeps the current local thread behavior. To exercise the hosted runtime locally, configure a writable database path and run:

```powershell
$env:KINDLEMASTER_DURABLE_RUNTIME="1"
$env:KINDLEMASTER_DURABLE_DB_PATH="output/runtime.sqlite3"
python production_server.py
```

## Validation

```powershell
python -m unittest -v production_tests.test_durable_job_queue production_tests.test_production_runtime production_tests.test_production_process_supervisor
python kindlemaster.py test --suite runtime
python kindlemaster.py test --suite release
```

Hosted acceptance must additionally execute `docs/qa/scenarios/durable-runtime-restart-recovery.md`, kill the API and worker children during active jobs, and confirm one canonical artifact, one terminal state and no duplicate execution.
