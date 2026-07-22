# Durable conversion job queue

Issue: #375

`durable_job_queue.py` defines the durable state-machine boundary for hosted conversion work. The initial implementation uses SQLite WAL on the shared Railway `/data` volume and supports atomic multi-process claiming.

## Guarantees

- owner-scoped idempotency keys;
- one active lease per job;
- worker ID, attempt number, heartbeat and lease expiry;
- deterministic abandoned-lease recovery;
- bounded retry with backoff and dead-letter state;
- owner-scoped reads and cancellation;
- fail-closed state transitions;
- final `ready` state only after an explicit worker completion call.

## Required hosted configuration

```text
KINDLEMASTER_DURABLE_JOB_DB=/data/runtime/durable_jobs.sqlite3
KINDLEMASTER_WORKER_LEASE_SECONDS=120
KINDLEMASTER_JOB_MAX_ATTEMPTS=3
```

SQLite is suitable for a small Railway deployment where every API and worker process mounts the same durable volume. Horizontal deployment across hosts without a shared POSIX volume must use a PostgreSQL or managed workflow implementation of the same contract.

## Worker lifecycle

1. API calls `enqueue()` with the verified owner and optional idempotency key.
2. Worker calls `claim()` inside an atomic transaction.
3. Worker calls `start()` and refreshes the lease with `heartbeat()`.
4. Success calls `complete()` only after final artifacts and quality evidence are durable.
5. Retryable failure calls `fail(..., retryable=True)` and is requeued with backoff.
6. Exhausted retries enter `dead_letter`.
7. A recovery process periodically calls `requeue_expired()`.

## Validation

```powershell
python -m unittest test_durable_job_queue.py
```

The contract tests cover owner-scoped idempotency, concurrent claims, lease recovery, retry/dead-letter behavior, cancellation and invalid transitions.

## Integration boundary

The queue is intentionally independent from Flask and conversion implementation details. Follow-up integration must connect the existing `/convert/start`, retry, status, cancellation and worker execution flow to this state machine without weakening the ownership guard from #341.
