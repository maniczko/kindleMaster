# Durable production runtime

KindleMaster keeps the existing process-local thread runtime for local development. Hosted Railway deployments use a separate API/worker process boundary backed by SQLite on the persistent `/data` volume.

## Runtime processes

`python production_server.py` starts:

- the Waitress API process;
- one or more supervised `production_worker.py` processes;
- a shared SQLite database containing conversion job state, queue commands, leases and rate-limit state.

The API never executes expensive conversion work when `KINDLEMASTER_DURABLE_RUNTIME=1`. It persists the job and enqueues a replayable command. Workers claim commands atomically and call the existing conversion pipeline outside the API process.

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
                         |-> cancelled
```

A worker owns a time-bounded lease. Heartbeats extend it. Another worker may reclaim an expired `leased` or `running` command. Attempts are audited and retry delay uses exponential backoff. Non-retryable validation/input errors do not retry.

The canonical job payload and the command queue are stored in the same SQLite database. WAL and `BEGIN IMMEDIATE` transactions prevent two local worker processes from claiming the same command.

## Idempotency

Authenticated callers may send:

```http
Idempotency-Key: <opaque-client-generated-key>
```

The key is scoped to the authenticated owner. Repeating the request returns the canonical job rather than scheduling duplicate work. Raw bearer tokens and cloud access tokens are never persisted in queue payloads.

Anonymous ownership and cross-browser isolation are finalized by #341. Until that change is merged, queue owner metadata for guests is job-scoped and is not an authorization mechanism.

## Recovery behavior

- API restart: workers continue because they are separate processes; job state remains in SQLite.
- Container restart: the database and retained input artifact survive on `/data`; a new worker reclaims the job after lease expiry.
- Worker crash: the supervisor restarts the worker; the expired lease is reclaimed.
- Source path missing: the worker restores the input from the retained input artifact before retry.
- Duplicate completion: only the lease owner may complete the queue record.
- Exhausted retry: the command enters `dead_letter` and stays visible for operator review.

## Local development

Continue to use:

```powershell
python kindlemaster.py serve
```

This keeps the current local thread behavior. To exercise the hosted runtime locally, configure a writable database path and run:

```powershell
$env:KINDLEMASTER_DURABLE_DB_PATH="output/runtime.sqlite3"
python production_server.py
```

## Validation

```powershell
python -m unittest test_durable_job_queue.py test_production_runtime.py
python kindlemaster.py test --suite runtime
python kindlemaster.py test --suite release
```

Hosted acceptance must additionally kill the API and worker during active jobs and confirm one canonical artifact, one terminal state and no duplicate execution.
