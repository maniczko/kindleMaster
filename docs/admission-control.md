# Public API admission control

Issue: #376

`admission_control.py` defines the distributed request, capacity and upload-validation policy. `admission_runtime_integration.py` enforces it before Flask routes execute. Counters are stored in SQLite WAL on the same Railway volume as the durable queue, so limits are consistent across API processes sharing that volume.

## Enforced controls

- separate anonymous and authenticated mutation budgets;
- separate, higher polling budgets so normal status refresh does not consume upload quota;
- bounded read budgets for library, reports and artifact routes;
- owner-scoped active and queued job quotas;
- global queue capacity and minimum-free-disk admission checks;
- upload-size, PDF page-count and PDF object-count limits;
- DOCX archive entry, expanded-size and compression-ratio limits;
- password/encryption rejection before conversion;
- extension, declared MIME and magic-byte agreement;
- normalized route keys so random job IDs do not create unbounded metric cardinality;
- stable decision codes with `400`, `413`, `415`, `422`, `429` and `503` behavior;
- `Retry-After` for retryable overload and rate decisions;
- persisted owner hashes only—never raw tokens, guest capabilities, filenames or document text.

## Protected routes

The production integration runs before these route groups:

- mutation: conversion start, retry, delete, repair and delivery;
- polling: status, quality and progress;
- read: jobs, library, archive, search, reports, artifacts, downloads and previews.

For `/convert/start`, the order is:

1. distributed request limit;
2. owner/global queue and disk admission;
3. file-size, magic-byte and MIME validation;
4. safe PDF/DOCX structural inspection;
5. only then the existing route saves input artifacts or enqueues expensive work.

## Production environment

```text
KINDLEMASTER_ADMISSION_DB_PATH=/data/queue/admission.sqlite3
KINDLEMASTER_RATE_WINDOW_SECONDS=60
KINDLEMASTER_ANON_REQUESTS_PER_WINDOW=10
KINDLEMASTER_AUTH_REQUESTS_PER_WINDOW=30
KINDLEMASTER_ANON_POLLS_PER_WINDOW=120
KINDLEMASTER_AUTH_POLLS_PER_WINDOW=240
KINDLEMASTER_ANON_READS_PER_WINDOW=60
KINDLEMASTER_AUTH_READS_PER_WINDOW=180
KINDLEMASTER_MAX_ACTIVE_JOBS_PER_OWNER=2
KINDLEMASTER_MAX_QUEUED_JOBS_PER_OWNER=5
KINDLEMASTER_MAX_GLOBAL_JOBS=20
KINDLEMASTER_MIN_FREE_DISK_BYTES=2147483648
KINDLEMASTER_MAX_FILE_BYTES=104857600
KINDLEMASTER_MAX_PDF_PAGES=1200
KINDLEMASTER_MAX_PDF_OBJECTS=200000
KINDLEMASTER_MAX_ARCHIVE_ENTRIES=5000
KINDLEMASTER_MAX_ARCHIVE_UNCOMPRESSED_BYTES=536870912
KINDLEMASTER_MAX_ARCHIVE_RATIO=100
```

The anonymous and authenticated owner identity must come from #341. Until that ownership boundary is deployed, public anonymous production remains blocked even when rate limits are active.

## Response contract

Blocked responses contain only:

- a stable `error_code`;
- a safe public message;
- HTTP status;
- retryability and optional retry delay.

The HTTP response never reveals worker counts, free disk, internal paths, raw owner identity, tokens or filenames. Detailed decision metrics are written only to structured backend logs with the owner class, not the owner value.

## Validation

```powershell
python -m unittest \
  test_admission_control.py \
  test_admission_runtime_integration.py
```

Coverage includes counters shared by two controller instances, separate poll budgets, owner/global capacity, low disk, file and PDF structural limits, DOCX expansion/encryption controls, Flask pre-route blocking and queue quota enforcement.

## Deployment acceptance

Do not close #376 on unit tests alone. Before unrestricted public access:

1. deploy the admission database on the shared persistent volume;
2. run bounded upload, retry and polling bursts from separate anonymous and authenticated owners;
3. confirm limits remain consistent across at least two API processes;
4. test queue saturation and low-disk behavior in staging;
5. test high-page/high-object PDF and high-expansion DOCX fixtures;
6. confirm valid small PDF and DOCX conversions remain successful;
7. inspect logs for filenames, guest capabilities, bearer tokens and document text;
8. confirm #341 ownership and #375 durable queue are both active.
