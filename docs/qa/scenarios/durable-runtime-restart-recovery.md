# Durable runtime — restart and recovery scenarios

Related issue: #375  
Implementation PR: #386  
Follow-up fault injection: #377

## Purpose

Verify that the hosted API and conversion workers use one durable job state, survive process failure and never publish duplicate or partial canonical artifacts.

## Preconditions

- Staging uses the same Railway image and resource class as production.
- A persistent Railway volume is mounted at `/data`.
- `KINDLEMASTER_DURABLE_RUNTIME=1`.
- `KINDLEMASTER_DURABLE_DB_PATH=/data/kindlemaster/runtime.sqlite3`.
- At least one test PDF that takes long enough to interrupt during OCR or packaging.
- Browser viewport: 1440 × 1000 desktop.
- Capture API response payloads, job timeline, worker logs and final artifact SHA-256.

## Scenario 1 — normal durable conversion

1. Open the Vercel staging UI.
2. Upload a valid PDF.
3. Start conversion through `/convert/start`.
4. Record `job_id` and the `Idempotency-Key` used by the client.
5. Observe states until completion.
6. Download the final EPUB and quality report.

Expected:

- One canonical `job_id`.
- Queue path includes `queued`, `leased`, `running`, `succeeded`.
- Exactly one final EPUB artifact.
- `ready` appears only after the artifact and quality evidence are durable.
- No browser access token is stored in the queue payload.

Evidence:

- start response JSON;
- queue row timeline;
- final job JSON;
- artifact SHA-256;
- screenshot of the completed quality state.

## Scenario 2 — duplicate start request

1. Submit the same valid file twice with the same authenticated owner and identical `Idempotency-Key`.
2. Send the second request before the first job leaves `queued`.
3. Compare both responses and queue contents.

Expected:

- Both responses identify the same canonical `job_id`.
- The replay response contains `idempotent_replay=true`.
- Only one queue command and one conversion attempt exist.
- The duplicate temporary source file is removed.
- Only one final artifact is published.

## Scenario 3 — duplicate retry request

1. Produce or select a failed retryable job.
2. Submit two retry requests with the same `Idempotency-Key`.
3. Compare the responses and queue records.

Expected:

- Both requests return the canonical retry job ID.
- The replay response contains `idempotent_replay=true`.
- `retry_of` references the original failed job.
- Only one new queued command exists.
- Earlier attempt evidence is preserved.

## Scenario 4 — API process restart during conversion

1. Start a long-running conversion.
2. Wait until the worker reports `running`.
3. Restart only the API/Waitress process while leaving the worker alive.
4. Reload the browser and poll the existing job.

Expected:

- The worker continues processing.
- The job remains visible after API restart.
- Status polling resumes against the same `job_id`.
- No new attempt is created.
- Exactly one final artifact is produced.

## Scenario 5 — worker crash and lease recovery

1. Start a long-running conversion.
2. Record current worker ID, attempt and lease expiry.
3. Kill the worker process.
4. Confirm the supervisor starts a replacement worker.
5. Wait for the old lease to expire.
6. Observe the replacement worker reclaim the job.

Expected:

- The job is not claimed concurrently before lease expiry.
- The replacement worker increments the audited attempt number.
- Retained input is restored when the original temporary source path is missing.
- One canonical final artifact exists.
- The old attempt remains visible in evidence.

## Scenario 6 — two workers race for one queued job

1. Configure two supervised worker processes.
2. Pause both before claim.
3. Enqueue one job.
4. Release both workers simultaneously.

Expected:

- Exactly one worker receives the lease.
- Attempt remains `1` after the initial race.
- The other worker receives no job.
- No duplicate pipeline execution or artifact is created.

## Scenario 7 — exhausted retry and dead letter

1. Inject a retryable failure for every attempt.
2. Allow retries until `KINDLEMASTER_JOB_MAX_ATTEMPTS` is reached.

Expected:

- Retry delay follows exponential backoff.
- Every attempt is audited separately.
- The final queue status is `dead_letter`.
- The public job status is terminal and explains that operator intervention is required.
- No incomplete artifact is presented as canonical.

## Scenario 8 — non-retryable validation failure

1. Submit a validly uploaded source that fails a non-retryable release/input validation.
2. Observe the queue and public status.

Expected:

- No automatic retry is scheduled.
- Queue status becomes `failed`, not `retry_wait`.
- Stable error code is preserved.
- Diagnostic evidence remains accessible to the owner.

## Scenario 9 — legacy JSON history migration

1. Prepare a staging volume containing an existing `conversion_jobs.json` with ready, failed and timed-out records.
2. Deploy the durable runtime for the first time.
3. Open the library and inspect SQLite state.
4. Restart the service a second time.

Expected:

- Legacy jobs are imported exactly once.
- Existing SQLite records are not overwritten.
- Library history remains visible.
- Migration logs report migrated, preserved and failed counts without filenames or document content.

## Scenario 10 — synchronous endpoint disabled

1. Send `POST /convert` in hosted production mode.
2. Send the equivalent file through `POST /convert/start`.

Expected:

- `/convert` returns HTTP 409 and `synchronous_conversion_disabled` with `start_url=/convert/start`.
- `/convert/start` accepts the job asynchronously.
- Expensive conversion never executes in an API request thread.

## Exit criteria

The hosted runtime may be accepted for merge/deployment only when:

- all scenarios pass or have a named external blocker;
- duplicate execution count is zero;
- orphan canonical artifact count is zero;
- source and output ownership remain correct;
- the tested worker count and Railway resource class are recorded;
- evidence is attached to #375 and #377.
