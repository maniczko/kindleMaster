# Durable conversion job queue

Issue: #375

`durable_job_queue.py` defines the durable state-machine boundary for hosted conversion work. The implementation uses SQLite WAL on the shared Railway `/data` volume and supports atomic multi-process claiming. `durable_runtime_integration.py` replaces process-local API execution with enqueueing, while `kindlemaster_worker.py` executes the existing conversion pipeline in an independent service process.

## Guarantees

- owner-scoped idempotency keys;
- one active lease per job;
- worker ID, attempt number, heartbeat and lease expiry;
- deterministic abandoned-lease recovery;
- bounded retry with exponential backoff and dead-letter state;
- owner-scoped reads, cancellation and terminal deletion;
- fail-closed state transitions;
- durable source copy retained across worker crashes and retries;
- final `ready` state only after the existing conversion pipeline has published artifacts and quality evidence;
- API status and library views are overlaid from durable queue state instead of relying only on process memory.

## Required hosted configuration

```text
KINDLEMASTER_DURABLE_QUEUE_PATH=/data/queue/conversion_jobs.sqlite3
KINDLEMASTER_DURABLE_SOURCE_DIR=/data/queue/sources
KINDLEMASTER_DURABLE_LEASE_SECONDS=180
KINDLEMASTER_DURABLE_HEARTBEAT_SECONDS=15
KINDLEMASTER_DURABLE_POLL_SECONDS=1
KINDLEMASTER_DURABLE_MAX_ATTEMPTS=3
```

SQLite is suitable for a small Railway deployment where every API and worker process mounts the same durable POSIX volume. Horizontal deployment across hosts without one shared volume must use a PostgreSQL or managed-workflow implementation of the same queue contract.

## Railway services

Use the same image and volume for two services:

### API service

```text
python production_server.py
```

The API validates ownership, stores the upload and input artifact, moves a retry-safe source copy under `/data/queue/sources`, then enqueues the conversion. It does not execute OCR or EPUB conversion in a request-serving thread.

### Worker service

```text
python kindlemaster_worker.py
```

Run at least one worker. All workers must mount the same `/data` volume and use the same queue path. A worker claims one job atomically, starts the existing conversion pipeline, mirrors progress into SQLite, refreshes its lease and publishes the terminal state.

## Worker lifecycle

1. API calls `enqueue()` with the verified owner and optional `Idempotency-Key`.
2. Worker calls `claim()` inside an atomic transaction.
3. Worker calls `start()` and refreshes the lease with a mirrored job snapshot.
4. Success calls `complete()` only after final artifacts and quality evidence are durable.
5. Retryable failure requeues the durable source with exponential backoff.
6. Exhausted retries enter `dead_letter`.
7. Every worker loop calls `requeue_expired()` before claiming new work.
8. Terminal completion removes the durable source copy; abandoned attempts retain it for recovery.

## Idempotency

Clients should send a stable `Idempotency-Key` for start and retry requests. Keys are scoped to the verified account or opaque anonymous owner. Repeating the same key returns the existing job and does not create another source copy or worker execution.

## Validation

```powershell
python -m unittest \
  test_durable_job_queue.py \
  test_durable_runtime_store.py \
  test_durable_runtime_integration.py
```

The contract tests cover owner-scoped idempotency, concurrent claims, lease recovery, retry/dead-letter behavior, payload mirroring, status overlay and duplicate start requests.

## Deployment acceptance

Do not close #375 on unit tests alone. Before production rollout:

1. deploy API and worker services with the same persistent volume;
2. start a conversion and restart only the API;
3. restart the worker during OCR and confirm lease recovery;
4. run two workers against one queued job and confirm one execution;
5. repeat a start request with one idempotency key;
6. confirm final artifacts and quality reports remain downloadable after both services restart;
7. confirm ownership isolation from #341 remains effective.
