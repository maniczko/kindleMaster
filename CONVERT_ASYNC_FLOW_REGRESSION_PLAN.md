# KindleMaster - Regression Plan for `/convert/start -> /convert/status -> /convert/download`

## Scope

This document defines the final regression and release checklist for the async conversion flow served by:

- `POST /convert/start`
- `GET /convert/status/<job_id>`
- `GET /convert/download/<job_id>`

It covers both:

- current automated coverage that already exists in the repository,
- missing high-risk scenarios that still require browser automation or manual verification.

The goal is to separate:

- real user-visible failures,
- expected transient network issues with retry/backoff,
- browser privacy/storage noise that should not be misclassified as an application bug.

## Owning layers

- Backend contract and job lifecycle: `app.py`
- Browser polling, retry, timeout and download UX: `templates/index.html`
- Fixed-layout budget heuristics: `fixed_layout_builder_v2.py`
- Existing automated contract coverage: `test_app_async_convert.py`
- Existing fixed-layout heuristic coverage: `test_fixed_layout_render_budget.py`

## Known current behavior

### Backend

- `/convert/start` returns `202` with `job_id`, `status=queued`, and `poll_after_ms`.
- `/convert/status/<job_id>` returns:
  - `404` for unknown job,
  - `ready` with `download_url` and `conversion`,
  - `failed` with `error`,
  - non-terminal states with `poll_after_ms` and `elapsed_seconds`.
- `/convert/download/<job_id>` returns:
  - `404` for unknown job,
  - `409` if the EPUB is not ready,
  - `500` if the job claims `ready` but the file is missing,
  - `200` with the EPUB file when ready.

### Browser

- request timeout for status fetches: `15000 ms`
- request timeout for start: `30000 ms`
- max transient poll errors before hard fail: `3`
- max whole conversion wait: `20 min`
- transient retry classification currently includes:
  - `Failed to fetch`
  - `NetworkError`
  - `load failed`
  - `timed out`
  - `limit czasu`
  - `status chwilowo nie odpowiada`
- retry/backoff uses increasing delay bounded by `1500 ms .. 5000 ms`

### Known open gaps

- real browser privacy/storage warnings may still end as `no_repro` on some local machines; diagnostics now classify them explicitly instead of treating them as an untracked open question
- runtime verification is local-production-like via `Waitress`, not a remote deploy target
- class thresholds are now source-of-truth JSON data; new classes still require explicit budget maintenance in `reference_inputs/size_budgets.json`

## Existing automated coverage

### Already covered by repository tests

1. `ASYNC-API-001` Start -> ready -> download roundtrip
   - File: `test_app_async_convert.py`
   - Verifies:
     - `202` from `/convert/start`
     - returned `job_id`
     - returned `poll_after_ms`
     - `ready` status payload
     - `download_url`
     - `output_size_bytes`
     - successful `/convert/download`
     - expected response headers

2. `ASYNC-API-002` Unknown status job
   - File: `test_app_async_convert.py`
   - Verifies `404` and error payload for missing job.

3. `ASYNC-API-003` Running state with poll hint
   - File: `test_app_async_convert.py`
   - Verifies `running`, no `download_url`, no `conversion`, adaptive `poll_after_ms`.

4. `ASYNC-API-004` Failed state without download
   - File: `test_app_async_convert.py`
   - Verifies `failed`, error propagation, no `download_url`, `poll_after_ms = 0`.

5. `ASYNC-API-005` Oversized EPUB warning metadata
   - File: `test_app_async_convert.py`
   - Verifies `output_size_bytes` is attached and oversized warning is emitted.

6. `FIXED-BUDGET-001` Render-budget classification is derived from publication signals
   - File: `test_fixed_layout_render_budget.py`
   - Verifies stable `analysis -> render_budget_class` mapping.

7. `FIXED-BUDGET-002` Render settings come from class policy and still keep a page-count fallback
   - File: `test_fixed_layout_render_budget.py`
   - Verifies `primary`/`fallback` presets and legacy page-count fallback.

8. `FIXED-BUDGET-003` Fixed-layout reruns once before hard failing on size budget
   - File: `test_converter_fixed_layout_budget_enforcement.py`
   - Verifies:
     - `primary -> fallback` rerun
     - controlled `size_budget_exceeded`
     - final metadata for attempt/status

9. `ASYNC-BROWSER-001` Poll fetch reject recovers before retry limit
   - Files: `test_browser_polling_runtime_harness.py`, `test_browser_polling_e2e.py`
   - Verifies:
     - transient `Failed to fetch`
     - retry/backoff progression
     - eventual `ready` result
     - successful download and human-readable size copy

10. `ASYNC-BROWSER-002` Failed status ends the flow without download
   - Files: `test_browser_polling_runtime_harness.py`, `test_browser_polling_e2e.py`
   - Verifies:
     - terminal `failed` status
     - visible backend error in UI
     - no stray download

11. `ASYNC-BROWSER-003` Timeout scenarios surface controlled browser errors
   - Files: `test_browser_polling_runtime_harness.py`, `test_browser_polling_e2e.py`
   - Verifies:
     - timed-out polling request
     - timed-out `/convert/start`
     - controlled final error state instead of hanging UI

12. `ASYNC-BROWSER-DIAG-001` Privacy-noise console warnings do not mask a healthy flow
   - Files: `test_browser_polling_runtime_harness.py`, `test_browser_polling_e2e.py`
   - Verifies:
     - Tracking Prevention / storage warnings are classified as noise
     - console warning capture stays separate from user-visible failure state
     - successful convert/poll/download still completes when privacy warnings are present

13. `ASYNC-BROWSER-DIAG-002` Cross-browser privacy diagnostics classify `browser_noise` vs `app_bug`
   - File: `test_browser_privacy_diagnostics.py`
   - Verifies:
     - Chromium and any locally available secondary browsers are probed
     - console/page/request diagnostics are collected
     - storage access exceptions are separated from privacy-noise warnings
     - no executed browser is classified as `app_bug`

14. `ASYNC-RUNTIME-001` Waitress runtime passes live async convert roundtrip
   - File: `test_runtime_waitress_smoke.py`
   - Verifies:
     - `python kindlemaster.py serve --runtime waitress`
     - live `start -> status -> download`
     - no-store headers and usable download payload

15. `ASYNC-TTL-001` TTL cleanup removes expired jobs and stale temp artifacts
   - File: `test_conversion_cleanup_ttl_contract.py`
   - Verifies:
     - expired terminal jobs are removed
     - expired source/output temp files are removed
     - active jobs and their files are preserved

16. `SMOKE-GATE-001` Class-based EPUB size gates cover generated and fixture EPUBs
   - Files: `scripts/run_smoke_tests.py`, `test_fixed_layout_render_budget.py`
   - Verifies:
     - explicit per-class size budget
     - `scan_probe` and `ocr_probe` small probes are still judged against declared thresholds
     - missing class budget is treated as a smoke failure

## Required regression matrix

### A. Contract and lifecycle

#### `ASYNC-REG-001` Fresh job creation

- Type: automated
- Layer: API
- Setup: upload valid small PDF and valid small DOCX
- Steps:
  1. Call `/convert/start`
  2. Read JSON payload
- Expected:
  - `202`
  - `success=true`
  - non-empty `job_id`
  - `status=queued`
  - valid `source_type`
  - `poll_after_ms >= 1500`
  - cache headers disable client caching

#### `ASYNC-REG-002` Unknown job on status

- Type: automated
- Layer: API
- Setup: synthetic random `job_id`
- Steps:
  1. Call `/convert/status/<missing>`
- Expected:
  - `404`
  - stable JSON error
  - no HTML fallback

#### `ASYNC-REG-003` Unknown job on download

- Type: automated
- Layer: API
- Setup: synthetic random `job_id`
- Steps:
  1. Call `/convert/download/<missing>`
- Expected:
  - `404`
  - stable JSON error

#### `ASYNC-REG-004` Download before ready

- Type: automated
- Layer: API
- Setup: inject `queued` or `running` job
- Steps:
  1. Call `/convert/download/<job_id>`
- Expected:
  - `409`
  - readable JSON error
  - no partial binary file

#### `ASYNC-REG-005` Ready job with missing output file

- Type: automated
- Layer: API
- Setup: inject `ready` job with non-existent `output_path`
- Steps:
  1. Call `/convert/download/<job_id>`
- Expected:
  - `500`
  - job is downgraded to `failed`
  - follow-up `/convert/status/<job_id>` reports failed state

### B. Failed status semantics

#### `ASYNC-REG-006` Business failure from conversion pipeline

- Type: automated
- Layer: API + browser
- Setup: make conversion worker set `status=failed`, `error="..."`.
- Steps:
  1. Start conversion
  2. Poll until failed payload is returned
- Expected:
  - no download triggered
  - visible final error in UI
  - retry/backoff is not used after terminal `failed`

#### `ASYNC-REG-007` Failed status payload is user-readable

- Type: manual
- Layer: browser UX
- Setup: conversion failure with explicit backend error
- Steps:
  1. Trigger conversion failure
  2. Observe final status box
- Expected:
  - message explains failure
  - user is not misled into waiting longer
  - no stale success summary remains on screen

### C. Retry, backoff and transient network faults

#### `ASYNC-REG-008` Poll fetch reject recovers before retry limit

- Type: browser E2E required
- Layer: browser
- Setup:
  - mock `/convert/status/<job_id>`
  - first request rejects with `Failed to fetch`
  - second request rejects with `Failed to fetch`
  - third request returns `ready`
- Steps:
  1. Start conversion with mocked successful `/convert/start`
  2. Observe polling behavior
- Expected:
  - UI shows retry message `Ponawiam probe (1/3)` and `Ponawiam probe (2/3)`
  - no hard-fail message before retry budget is exhausted
  - final response triggers download and success summary

#### `ASYNC-REG-009` Poll fetch reject exceeds retry limit

- Type: browser E2E required
- Layer: browser
- Setup:
  - mock `/convert/status/<job_id>` to reject 3 consecutive times
- Steps:
  1. Start conversion
  2. Let all retries fail
- Expected:
  - UI stops retrying after the third transient failure
  - final error says local conversion connection was interrupted
  - no download is triggered

#### `ASYNC-REG-010` Poll timeout recovers before retry limit

- Type: browser E2E required
- Layer: browser
- Setup:
  - delay `/convert/status/<job_id>` beyond `15000 ms` for the first attempt
  - return `ready` on the next attempt
- Steps:
  1. Start conversion
  2. Observe timeout handling
- Expected:
  - timeout becomes a transient error
  - retry/backoff is visible
  - second attempt succeeds
  - final download still happens

#### `ASYNC-REG-011` Start timeout fails immediately

- Type: browser E2E required
- Layer: browser
- Setup:
  - delay `/convert/start` beyond `30000 ms`
- Steps:
  1. Attempt conversion
- Expected:
  - no polling starts
  - final message explains start timeout
  - no ghost job state persists in UI

#### `ASYNC-REG-012` Poll returns HTTP 5xx

- Type: browser E2E required
- Layer: browser
- Setup:
  - mock `/convert/status/<job_id>` to return `503` JSON or text
- Steps:
  1. Start conversion
  2. Observe behavior
- Expected:
  - actual current behavior must be documented
  - if the UI hard-fails instead of retrying, mark as accepted current behavior or open bug
- Release note:
  - this case must not be silently misreported as success

#### `ASYNC-REG-013` Poll returns malformed non-JSON body

- Type: browser E2E recommended
- Layer: browser
- Setup:
  - mock `/convert/status/<job_id>` to return HTML or plain text with failure status
- Steps:
  1. Start conversion
  2. Observe failure rendering
- Expected:
  - UI surfaces a readable error
  - no infinite retry loop

### D. Retry/backoff behavior itself

#### `ASYNC-REG-014` Backoff stays within declared bounds

- Type: automated browser or unit-style JS harness
- Layer: browser logic
- Setup: simulate repeated transient failures
- Steps:
  1. Record actual retry delays
- Expected:
  - retry delays never go below `1500 ms`
  - retry delays never exceed `5000 ms`
  - delay grows between attempts unless server-suggested poll delay overrides

#### `ASYNC-REG-015` Server suggested poll interval is respected

- Type: automated
- Layer: API + browser
- Setup:
  - mocked status responses with `poll_after_ms` values such as `1200`, `2500`, `5000`
- Steps:
  1. Poll across multiple non-terminal states
- Expected:
  - browser follows bounded value returned by server
  - terminal states always return `poll_after_ms = 0`

### E. Tiny files and copy accuracy

#### `ASYNC-REG-016` Very small EPUB shows success even if size text is tiny

- Type: manual and automated contract
- Layer: API + browser
- Setup:
  - ready job with tiny `output_size_bytes`
- Steps:
  1. Finish conversion
  2. Observe success summary and downloaded file
- Expected:
  - flow succeeds
  - size label may show `0.00 MB` or `0 MB`
  - this is treated as cosmetic, not as failure

#### `ASYNC-REG-017` Tiny size label does not block download confidence

- Type: manual
- Layer: browser UX
- Setup:
  - tiny EPUB output
- Steps:
  1. Trigger conversion
  2. Observe post-success message
- Expected:
  - user still sees clear success status
  - there is no warning implying file corruption purely because the size is very small

### F. Heavy fixed-layout documents

#### `ASYNC-REG-018` Heavy fixed-layout conversion finishes with bounded quality degradation

- Type: corpus/manual plus automated heuristic checks
- Layer: converter + browser
- Setup:
  - representative 120+, 240+, and 360+ page layout-heavy PDFs
- Steps:
  1. Convert each through async flow
  2. Observe output size and perceived responsiveness
- Expected:
  - conversion finishes without browser stall
  - oversized warning appears when expected
  - resulting EPUB remains openable on target reader
  - render budget matches heuristic tier

#### `ASYNC-REG-019` Large fixed-layout warns operator about big EPUB

- Type: automated and manual
- Layer: API + browser
- Setup:
  - output larger than `25 MB`
- Steps:
  1. Finish conversion
- Expected:
  - warning list contains large-file warning
  - UI still allows successful download
  - message is advisory, not terminal

#### `ASYNC-REG-020` Corpus budget gate

- Type: currently missing; should become automated release gate
- Layer: corpus regression
- Setup:
  - class-based fixed-layout corpus
- Steps:
  1. Convert representative files
  2. Compare against expected class envelope
- Expected:
  - class-specific max EPUB size and open-time targets are enforced
- Current status:
  - not implemented
  - remains a release risk

### G. Cleanup TTL and stale job hygiene

#### `ASYNC-REG-021` Stale `_CONVERSION_JOBS` cleanup

- Type: currently missing
- Layer: backend lifecycle
- Setup:
  - create multiple old jobs with old timestamps
- Steps:
  1. Trigger cleanup routine or maintenance hook
  2. Inspect in-memory registry
- Expected:
  - old jobs are removed after TTL
  - recent jobs remain intact

#### `ASYNC-REG-022` Stale temp file cleanup

- Type: currently missing
- Layer: backend filesystem hygiene
- Setup:
  - leave old uploaded source files and old EPUB outputs in temp dir
- Steps:
  1. Trigger cleanup routine
  2. Inspect temp directory
- Expected:
  - stale files are deleted
  - active job outputs are not deleted early

#### `ASYNC-REG-023` Download after TTL expiry

- Type: future design validation
- Layer: backend lifecycle + UX
- Setup:
  - let a ready job expire by TTL
- Steps:
  1. Open stale `download_url`
- Expected:
  - user gets explicit message that artifact expired
  - no broken binary response

### H. Privacy noise versus real application bug

This area now has an automated browser diagnostic as well as the manual release checklist items below. Use the automated coverage first; use the manual steps only when the console warning itself is the thing you need to inspect.

#### `ASYNC-REG-024` Tracking prevention or storage warning without flow break

- Type: manual
- Layer: browser diagnosis
- Setup:
  - browser with aggressive privacy mode
- Steps:
  1. Perform successful conversion
  2. Watch console
- Expected:
  - if a storage/tracking warning appears but conversion, polling, and download still work, classify as privacy noise
  - do not file it as product bug unless user-visible behavior breaks

#### `ASYNC-REG-025` Privacy noise plus actual broken flow

- Type: manual
- Layer: browser diagnosis
- Setup:
  - privacy warning appears and flow also fails
- Steps:
  1. Reproduce with network tab and console open
  2. Verify whether requests fail or UI state breaks
- Expected:
  - only classify as app bug if there is real functional break:
    - start never returns,
    - polling never progresses,
    - download does not trigger,
    - UI enters inconsistent state

## Manual release checklist

Run this checklist before calling the async flow release-ready.

1. Start a fresh local server on `http://127.0.0.1:5001/`.
2. Upload a small valid PDF and verify `start -> status -> download`.
3. Upload a small valid DOCX and verify `start -> status -> download`.
4. Confirm unknown job returns `404` on status.
5. Confirm non-ready job returns `409` on download.
6. Force one failed conversion and verify terminal `failed` UX.
7. Force transient polling interruption and verify retry/backoff copy.
8. Force polling timeout and verify the UI does not spin forever.
9. Convert one tiny-output case and confirm `0.00 MB` does not break flow.
10. Convert one heavy fixed-layout case and confirm oversized warning and successful download.
11. Check browser console:
    - separate privacy/storage noise from real network/request failure
    - confirm no misleading success state appears after a failure
12. Inspect temp area manually if TTL cleanup is still not implemented.

## Automation backlog to add next

These are the highest-value missing automated scenarios.

1. Browser E2E for `fetch` reject during `/convert/status`.
2. Browser E2E for delayed `/convert/status` timeout.
3. Browser E2E for delayed `/convert/start` timeout.
4. Browser E2E for retry counter and backoff messaging.
5. API test for `ready` job with missing file on `/convert/download`.
6. API or lifecycle test for TTL cleanup once cleanup exists.
7. Corpus gate for fixed-layout output size by document class.

## Release risks that remain even after current implementation

1. The async flow is contract-tested, but not fully browser-proven for hostile network behavior.
2. Privacy warnings in the console may distract debugging and be mistaken for product regressions.
3. Tiny size labels can look suspicious even though the flow is valid.
4. Fixed-layout heuristics reduce risk but do not prove corpus-wide size control.
5. Lack of TTL cleanup can cause long-lived local temp accumulation.
6. Flask dev server is still a development server, so passing this checklist is not the same as production hardening.

## Pass criteria for this flow

Do not call the async flow fully verified unless all of the following are true:

1. Contract tests for start/status/download pass.
2. Browser E2E proves at least one transient retry success case.
3. Browser E2E proves one terminal network failure case.
4. Failed status is visibly terminal and never triggers download.
5. Heavy fixed-layout case completes with operator-visible warning when oversized.
6. Privacy-noise-only scenario is documented and not treated as an app bug.
7. TTL behavior is either implemented and tested or explicitly listed as open risk.

## Short release summary template

Use this wording style when reporting final QA for the async flow:

- what passed at API level
- what passed in a real browser
- which hostile network scenarios were simulated
- whether retry/backoff was observed
- whether heavy fixed-layout stayed within acceptable envelope
- whether TTL cleanup is implemented or still open
- whether any browser warning was noise or a real functional defect
