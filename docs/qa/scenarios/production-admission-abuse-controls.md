# Production admission — rate limits and upload abuse scenarios

Related issue: #376  
Implementation PR: #390  
Dependencies: #341, #375  
Capacity calibration: #381

## Purpose

Verify that public conversion endpoints reject abusive or unsafe traffic before expensive PDF/OCR/EPUB processing begins, while preserving valid user workflows.

## Preconditions

- Deploy PR #386 and PR #390 to a staging environment.
- Use production-equivalent Railway resources and persistent `/data`.
- Record all configured limits and environment overrides.
- Keep `KINDLEMASTER_TRUST_GUEST_CAPABILITY=0` until #341 is deployed and verified.
- Browser viewport: 1440 × 1000 desktop plus 390 × 844 mobile for user-visible error states.
- Capture response status, stable error code, rate-limit headers, queue counts, CPU/memory and disk usage.

## Scenario 1 — authenticated start burst

1. Authenticate one staging user.
2. Submit valid small PDFs until the configured authenticated start limit is reached.
3. Submit one additional request within the same minute.

Expected:

- Requests up to the limit are evaluated normally.
- The next request returns HTTP 429 with `rate_limit_exceeded`.
- `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` are present.
- No extra queue command or source artifact is created for the rejected request.

## Scenario 2 — guest start burst

1. Use an unauthenticated browser/session.
2. Submit valid small files until the guest start limit is reached.
3. Submit one additional request.

Expected:

- Guest limit is lower than authenticated limit.
- Rejected request returns HTTP 429.
- Changing User-Agent does not create a fresh limiter bucket.
- The response does not reveal internal queue or worker capacity.

## Scenario 3 — invalid Bearer-token rotation

1. Send repeated conversion starts from the same source address.
2. Change the invalid `Authorization: Bearer ...` value for every request.

Expected:

- Invalid tokens never receive authenticated limits.
- Token rotation does not create unlimited authenticated buckets.
- Raw token values do not appear in logs or SQLite limiter keys.

## Scenario 4 — arbitrary guest-capability rotation before #341

1. Keep `KINDLEMASTER_TRUST_GUEST_CAPABILITY=0`.
2. Send repeated guest starts.
3. Change `X-KindleMaster-Guest-Capability` on every request.

Expected:

- Arbitrary capability values are ignored for identity.
- Capability rotation does not bypass the guest limit.
- The capability is never treated as job authorization.

## Scenario 5 — validated capability after #341

Run only after #341 is merged and its server-issued capability is verified.

1. Set `KINDLEMASTER_TRUST_GUEST_CAPABILITY=1`.
2. Open two separate anonymous browser profiles.
3. Confirm each receives a distinct validated capability.
4. Saturate the rate limit in profile A.
5. Submit a normal request from profile B.

Expected:

- A and B have independent guest limiter buckets.
- An arbitrary or malformed capability is rejected or mapped to the fallback identity.
- Capability A cannot access jobs or artifacts owned by B.

## Scenario 6 — per-owner concurrency quota

1. Start long-running jobs for one authenticated user until the active-job quota is reached.
2. Submit one more conversion for the same owner.
3. Submit a conversion as a different authenticated user.

Expected:

- Extra request from the saturated owner returns HTTP 429 with `owner_concurrency_exceeded`.
- Another owner can use remaining global capacity.
- Rejected request creates no durable job.

## Scenario 7 — global capacity saturation

1. Fill all configured global active slots with several owners.
2. Submit another conversion.

Expected:

- New request returns HTTP 503 with `global_capacity_exceeded` and `Retry-After`.
- Already accepted jobs continue.
- API and worker processes remain healthy.
- No accepted job is silently dropped.

## Scenario 8 — polling flood

1. Start one long-running job.
2. Poll status repeatedly above the configured read limit.

Expected:

- Polling is allowed at a higher but bounded rate.
- Excess requests return HTTP 429.
- The conversion continues independently.
- Frontend backs off and presents a recoverable status rather than starting a new job.

## Scenario 9 — low-disk admission

1. Configure or simulate free space below either the byte or ratio threshold.
2. Submit a valid PDF.

Expected:

- Request returns HTTP 503 with `storage_capacity_exceeded` before source persistence and OCR.
- No queue command is created.
- Existing jobs and artifacts remain accessible.
- Operator alerting can use the same threshold in #378.

## Scenario 10 — extension, MIME and magic mismatch

Test at least:

- text file renamed to `.pdf`;
- ZIP file renamed to `.pdf`;
- PDF uploaded with an incompatible MIME type;
- random bytes renamed to `.docx`.

Expected:

- HTTP 400/422 as defined by the stable error contract.
- `upload_magic_mismatch` or `upload_mime_mismatch`.
- Rejection happens before queueing.

## Scenario 11 — malformed and password-protected PDF

1. Upload a truncated PDF.
2. Upload a password-protected PDF.
3. Upload a zero-page or structurally invalid PDF when a legal fixture is available.

Expected:

- Stable `malformed_pdf`, `password_protected_pdf` or `empty_pdf` error.
- Error is non-retryable.
- No OCR process starts.

## Scenario 12 — PDF complexity limits

1. Generate a legal synthetic PDF above the page limit.
2. Generate or use a synthetic PDF above the xref-object limit.

Expected:

- HTTP 413.
- `pdf_page_limit` or `pdf_object_limit`.
- No durable job or source artifact is retained.

## Scenario 13 — DOCX traversal and invalid OOXML

Test:

- ZIP missing `[Content_Types].xml`;
- ZIP missing `word/document.xml`;
- member named `../escape.txt`;
- absolute member path.

Expected:

- `invalid_docx_structure` or `docx_path_traversal`.
- No file is written outside the upload root.
- No conversion job is created.

## Scenario 14 — DOCX archive expansion and oversized members

Test synthetic fixtures for:

- excessive member count;
- one member above the member limit;
- total decompressed bytes above the limit;
- compression ratio above the configured threshold;
- embedded image above the pixel limit.

Expected:

- Stable limit-specific error code.
- HTTP 413 where appropriate.
- Memory and CPU remain bounded during validation.
- No OCR/conversion worker is engaged.

## Scenario 15 — valid regression path

1. Upload a normal small PDF.
2. Upload a normal small DOCX.
3. Complete both conversions.

Expected:

- Guardrails do not reject valid files.
- Queue ownership and quality flow remain unchanged.
- Final EPUB and quality evidence are downloadable.

## Exit criteria

The guardrails may be accepted only when:

- every rejection occurs before expensive processing;
- invalid token, User-Agent and arbitrary capability rotation cannot bypass limits;
- limiter state is shared across API processes on the same volume;
- valid PDF/DOCX regression scenarios pass;
- configured staging limits and observed resource usage are attached to #376;
- public rollout remains blocked until #341 ownership isolation is verified.
