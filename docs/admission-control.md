# Public API admission control

Issue: #376

`admission_control.py` defines a distributed request, capacity and upload-validation boundary for the hosted conversion API. Counters are stored in SQLite WAL on the shared Railway volume, so limits are consistent across API processes on the same deployment volume.

## Controls

- separate anonymous and authenticated request budgets;
- owner-scoped active and queued job quotas;
- global capacity and minimum-free-disk admission checks;
- file-size and PDF page-count limits;
- extension, declared MIME and magic-byte agreement;
- stable decision codes with `429`, `413`, `415` and `503` behavior;
- persisted owner hashes only—no raw tokens, filenames or document content.

## Production environment

```text
KINDLEMASTER_ADMISSION_DB=/data/runtime/admission.sqlite3
KINDLEMASTER_RATE_WINDOW_SECONDS=60
KINDLEMASTER_ANON_REQUESTS_PER_WINDOW=10
KINDLEMASTER_AUTH_REQUESTS_PER_WINDOW=30
KINDLEMASTER_MAX_ACTIVE_JOBS_PER_OWNER=2
KINDLEMASTER_MAX_QUEUED_JOBS_PER_OWNER=5
KINDLEMASTER_MAX_GLOBAL_JOBS=20
KINDLEMASTER_MIN_FREE_DISK_BYTES=2147483648
KINDLEMASTER_MAX_FILE_BYTES=104857600
KINDLEMASTER_MAX_PDF_PAGES=1200
```

The API integration should resolve the verified owner through the authorization boundary from #341, call `check_request()` before route work, call `check_job_admission()` before accepting a new conversion and call `validate_upload()` before OCR or parser initialization.

## Response contract

Blocked responses must include a stable code and safe retry guidance. `Retry-After` should be set when `retry_after_seconds` is non-zero. Responses must not reveal worker counts, free capacity, tokens or filenames.

## Validation

```powershell
python -m unittest test_admission_control.py
```

Tests cover counters shared by two controller instances, authenticated/anonymous limits, owner/global capacity, low disk, file-size limits, page limits and MIME/extension/magic-byte mismatch.

## Remaining integration

This module establishes the shared enforcement contract. Issue #376 remains open until the Flask routes use the controller, PDF/DOCX structural/decompression-bomb checks are added and a hosted bounded-abuse test passes.
