# Durable runtime — queued cancellation scenarios

Related issue: #375  
Implementation PR: #386

## Purpose

Verify that cancellation is real and safe: queued work may be cancelled before leasing, while active work is rejected until stage-aware cancellation exists.

## Preconditions

- Durable production runtime is enabled.
- Use an authenticated owner or validated anonymous owner from #341.
- Capture queue row, public job JSON and worker logs.

## Scenario 1 — cancel queued job

1. Pause workers or set worker count to zero in staging.
2. Submit a valid conversion through `/convert/start`.
3. Confirm queue status `queued`.
4. Send `POST /convert/cancel/<job_id>` as the owning user/session.
5. Resume workers.

Expected:

- HTTP 200.
- Public and queue status are `cancelled`.
- No worker may claim the job after cancellation.
- No conversion attempt, source processing or final artifact is created.
- Cloud/library metadata shows the cancelled state.

## Scenario 2 — cancel retry-wait job

1. Inject a retryable failure.
2. Confirm queue status `retry_wait` and a future `available_at`.
3. Send the cancellation request before the retry becomes available.

Expected:

- HTTP 200.
- Retry is removed from claimable work.
- Attempt history and last error remain available for audit.
- No later worker claim occurs.

## Scenario 3 — active cancellation fails closed

1. Start a long-running conversion.
2. Confirm queue status `leased` or `running`.
3. Send `POST /convert/cancel/<job_id>`.

Expected:

- HTTP 409.
- Stable error code `active_cancellation_unsupported`.
- `cancellation_requested` remains false.
- Public job is not falsely marked cancelled.
- The active worker continues to a normal terminal state.

## Scenario 4 — cross-owner cancellation

1. Create a queued job as owner A.
2. Attempt cancellation as owner B.

Expected:

- Non-enumerating unauthorized/not-found response according to #341.
- Queue and public status remain unchanged.
- No ownership details are leaked.

## Exit criteria

- queued and retry-wait cancellation creates zero execution;
- active cancellation never produces a false cancelled state;
- owner isolation is verified after #341;
- evidence is attached to #375.
