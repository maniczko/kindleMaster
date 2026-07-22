# Production admission guardrails

Public conversion endpoints are protected by process-safe admission controls installed by `production_server.py`. Counters, queue state and concurrency decisions use the same SQLite database on the persistent `/data` volume.

These controls limit accidental overload and automated abuse. They are not a replacement for job ownership authorization from #341.

## Default limits

| Control | Authenticated | Guest |
| --- | ---: | ---: |
| New conversions per minute | 12 | 4 |
| Retry requests per minute | 10 | 3 |
| Active jobs per owner | 3 | 1 |
| Mutations per minute | 30 | 30 |
| Polling/read requests per minute | 180 | 180 |

Global defaults:

- 4 active queued/running/retrying jobs;
- 75 MiB upload body, also enforced as Flask `MAX_CONTENT_LENGTH` before multipart parsing;
- 1,200 PDF pages;
- 250,000 PDF xref objects;
- 5,000 DOCX archive members;
- 300 MiB total uncompressed DOCX content;
- 100 MiB per DOCX member;
- archive expansion ratio not greater than 200:1;
- 100 million pixels per embedded DOCX image;
- at least 2 GiB and 10% free disk space;
- at least 256 MiB and 10% available memory before accepting new conversion work.

Every value has an environment override. Production limits should later be calibrated from #381 rather than increased without measurement.

## Environment variables

```text
KINDLEMASTER_AUTH_STARTS_PER_MINUTE=12
KINDLEMASTER_GUEST_STARTS_PER_MINUTE=4
KINDLEMASTER_AUTH_RETRIES_PER_MINUTE=10
KINDLEMASTER_GUEST_RETRIES_PER_MINUTE=3
KINDLEMASTER_MUTATIONS_PER_MINUTE=30
KINDLEMASTER_POLLING_PER_MINUTE=180
KINDLEMASTER_AUTH_ACTIVE_JOBS=3
KINDLEMASTER_GUEST_ACTIVE_JOBS=1
KINDLEMASTER_GLOBAL_ACTIVE_JOBS=4
KINDLEMASTER_MAX_UPLOAD_BYTES=78643200
KINDLEMASTER_MAX_PDF_PAGES=1200
KINDLEMASTER_MAX_PDF_XREF_OBJECTS=250000
KINDLEMASTER_MAX_DOCX_MEMBERS=5000
KINDLEMASTER_MAX_DOCX_UNCOMPRESSED_BYTES=314572800
KINDLEMASTER_MAX_DOCX_MEMBER_BYTES=104857600
KINDLEMASTER_MAX_ARCHIVE_RATIO=200
KINDLEMASTER_MAX_IMAGE_PIXELS=100000000
KINDLEMASTER_MIN_DISK_FREE_BYTES=2147483648
KINDLEMASTER_MIN_DISK_FREE_RATIO=0.10
KINDLEMASTER_MEMORY_ADMISSION=1
KINDLEMASTER_MIN_MEMORY_AVAILABLE_BYTES=268435456
KINDLEMASTER_MIN_MEMORY_AVAILABLE_RATIO=0.10
KINDLEMASTER_RATE_LIMIT_SECRET=<optional-long-random-secret>
KINDLEMASTER_TRUST_GUEST_CAPABILITY=0
```

When no rate-limit secret is configured, production startup creates a 0600 capability file next to the durable SQLite database. Raw bearer tokens, guest capabilities, IP addresses, filenames and document content are not stored as limiter keys.

## Capacity admission

New conversions are rejected before file parsing or queue creation when:

- global or owner concurrency is exhausted;
- persistent storage is below either the byte or ratio threshold;
- container memory is below either the byte or ratio threshold.

Memory measurement prefers cgroup v2 (`memory.max` and `memory.current`), then cgroup v1, then `/proc/meminfo`. This keeps the decision aligned with a container limit rather than host-wide free memory. Missing memory telemetry is reported as unavailable but does not make local development unusable.

## Input validation

Validation runs before the file is saved for conversion:

- extension and MIME type are checked against magic bytes;
- malformed and password-protected PDFs are rejected;
- PDF page and object counts are bounded;
- DOCX is verified as an OOXML ZIP containing required parts;
- path traversal, oversized members, excessive decompressed size and suspicious compression ratios are rejected;
- embedded image pixel count is bounded when Pillow can inspect the image.

Stable errors include:

```text
rate_limit_exceeded
owner_concurrency_exceeded
global_capacity_exceeded
storage_capacity_exceeded
memory_capacity_exceeded
upload_size_limit
upload_magic_mismatch
upload_mime_mismatch
malformed_pdf
password_protected_pdf
pdf_page_limit
pdf_object_limit
malformed_docx
invalid_docx_structure
docx_path_traversal
docx_member_limit
docx_member_size_limit
docx_uncompressed_limit
archive_expansion_limit
image_pixel_limit
```

`429` and `503` responses include `Retry-After`. Rate-limited responses expose `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` without exposing internal queue capacity details.

## Anonymous identity boundary

The preferred guest identifier is the server-issued `X-KindleMaster-Guest-Capability` from #341. It is deliberately ignored while `KINDLEMASTER_TRUST_GUEST_CAPABILITY=0`, because an arbitrary client-supplied value would allow rate-limit rotation. Enable the flag only after #341 validates and binds the capability to the anonymous browser owner.

Until then, unauthenticated requests share a pseudonymous HMAC key derived from the connection address. User-Agent changes and random invalid Bearer tokens do not create authenticated identities or fresh limiter buckets. This fallback is admission-only and must never authorize access to a job or artifact.

## Validation

```powershell
python -m unittest -v production_tests.test_production_capacity production_tests.test_production_guardrails
python -m py_compile production_capacity_guard.py production_guardrails.py production_server.py
python kindlemaster.py test --suite runtime
python kindlemaster.py test --suite release
```

Before public rollout, run a bounded staging abuse test covering burst starts, invalid-token rotation, capability rotation, polling floods, queue saturation, low disk, low memory, MIME mismatch, malformed PDF, DOCX path traversal and archive expansion.
