# `/convert` API Contract

Related Linear scope: VAT-203.

This is the stable local HTTP contract for the async conversion flow used by the browser UI, tests, and automation. Runtime code may add fields, but clients should not depend on undocumented fields.

## Common Rules

- Base URL: `http://kindlemaster.localhost:5001/`; fallback: `http://127.0.0.1:5001/`.
- Successful JSON responses include `success: true`.
- JSON error responses include `success: false`, `error`, and `error_code` where the endpoint can produce structured JSON.
- Successful EPUB downloads return `application/epub+zip`; download failures return JSON.
- Polling and job-history responses use `Cache-Control: no-store, max-age=0`.
- UI-facing `error` messages must not include local filesystem paths, raw tracebacks, environment variables, or command lines with user-local secrets.

## Error Envelope

Use this shape for non-EPUB error responses:

```json
{
  "success": false,
  "error": "Safe user-facing message.",
  "error_code": "conversion_failed",
  "job_id": "optional-job-id",
  "phase": "conversion",
  "retryable": false
}
```

Stable `error_code` values:

| Code | Phase | Typical HTTP status | Meaning |
| --- | --- | --- | --- |
| `upload_failed` | `upload` | `400`, `413` | Missing file, unsupported extension, oversized upload, or upload persistence failure. |
| `queue_failed` | `queue` | `429`, `500`, `503` | The server accepted the request path but could not create or persist a queued job. |
| `conversion_timeout` | `conversion` | `200` status payload or `504` start/worker failure | The job exceeded the configured conversion/watchdog budget. |
| `conversion_failed` | `conversion` | `200` status payload or `500` | Conversion failed before a downloadable EPUB was available. |
| `validation_failed` | `validation` | `200` status/quality payload or `422` if blocking at request time | EPUB validation found blocking structural issues. |
| `missing_output` | `download` | `500` | A job reached `ready`, but its EPUB file is missing or unreadable. |
| `application_restart` | `recovery` | `200` status payload | A previously active job was marked failed after app restart because local workers cannot resume. |

Compatibility note: older notes may refer to `app_restart`; treat it as a documentation alias of `application_restart`.

## `POST /convert/start`

Starts an async PDF/DOCX conversion.

Request: `multipart/form-data`

| Field | Required | Notes |
| --- | --- | --- |
| `file` or `pdf` | yes | PDF or DOCX upload. |
| `profile` | no | Defaults to `auto-premium`. |
| `ocr` | no | String boolean; `true` forces OCR where supported. |
| `language` | no | Defaults to `pl`. |
| `heading_repair` | no | String boolean; `true` enables heading/TOC repair. |
| `route_model_mode` | no | `off`, `shadow`, or `assist`; defaults to `shadow`. `shadow` reports ML route decisions without changing the selected profile. |

Success: `202 Accepted`

```json
{
  "success": true,
  "job_id": "9f8b...",
  "status": "queued",
  "source_type": "pdf",
  "message": "Konwersja wystartowala. Trwa przygotowanie EPUB.",
  "poll_after_ms": 1500
}
```

Structured failures use `upload_failed` for upload validation/persistence and `queue_failed` for job-store or queue creation failures. Oversized uploads should return JSON with `upload_failed` when the request accepts JSON error handling.

## `GET /convert/status/<job_id>`

Returns the current conversion state.

Success: `200 OK`

```json
{
  "success": true,
  "job_id": "9f8b...",
  "status": "running",
  "message": "Konwertuje PDF do EPUB...",
  "source_type": "pdf",
  "filename": "source.pdf",
  "error": "",
  "error_code": "",
  "conversion": null,
  "download_url": null,
  "poll_after_ms": 5000,
  "elapsed_seconds": 91,
  "output_size_bytes": null,
  "quality_state": {},
  "quality_state_url": "/convert/quality/9f8b..."
}
```

Stable statuses:

| Status | Meaning | Client behavior |
| --- | --- | --- |
| `queued` | Upload accepted and waiting for worker execution. | Poll after `poll_after_ms`. |
| `running` | Conversion is active. | Continue polling. |
| `repairing_headings` | Heading/TOC repair is active. | Continue polling; expect a short poll interval. |
| `ready` | EPUB exists and can be downloaded. | Use `download_url`; inspect `quality_state`. |
| `failed` | Terminal failure. | Stop polling; display safe `error`; use `error_code` for recovery. |

Ready jobs include `conversion` metadata, `download_url`, and `output_size_bytes`. Failed jobs include a stable non-empty `error_code`. Unknown jobs return `404` JSON with an error envelope.

## `GET /convert/quality/<job_id>`

Returns normalized quality state for a job. This endpoint is additive and is the preferred surface for UI quality/cockpit state.

Success: `200 OK`

```json
{
  "success": true,
  "job_id": "9f8b...",
  "quality_state": {
    "status": "ready",
    "phase": "completed",
    "quality_available": true,
    "download_available": true,
    "reading_verdict": "ready",
    "release_verdict": "release_ready",
    "release_blocked": false,
    "alerts": [],
    "quality_blockers": []
  }
}
```

Consumers may rely on `status`, `phase`, `quality_available`, `download_available`, `reading_verdict`, `release_verdict`, `release_blocked`, `alerts`, and `quality_blockers`. `reading_verdict` is one of `ready`, `ready_with_review`, or `failed`. `release_verdict` is one of `release_ready`, `ready_with_review`, `release_blocked`, or `failed`. Other nested fields are additive diagnostics. Unknown jobs return `404` JSON with an error envelope.

## `GET /convert/download/<job_id>`

Downloads the generated EPUB.

Success: `200 OK`

- `Content-Type: application/epub+zip`
- `Content-Disposition: attachment`
- Metadata headers such as `X-Source-Type`, `X-Publication-Profile`, `X-EPUB-Validation`, `X-Heading-Repair-Status`, and render-budget headers when available.

Failures:

| Condition | HTTP status | `error_code` |
| --- | --- | --- |
| Unknown job | `404` | `missing_output` only if an output was expected; otherwise `upload_failed` or omitted for legacy compatibility. |
| Job not ready | `409` | `queue_failed` for queued/active state conflicts, or omitted for legacy compatibility. |
| Ready job has no readable EPUB | `500` | `missing_output` |

Download error bodies must be JSON and must not include the local `output_path`.

## `GET /user/profile`

Returns non-secret user defaults used by the React console. Guest mode uses the local profile under `%APPDATA%\KindleMaster\profile.json` unless `KINDLEMASTER_USER_PROFILE_PATH` overrides it for tests or local automation. Authenticated Supabase sessions prefer `public.user_profiles` and cache the same non-secret profile locally for the runtime SMTP path.

Success: `200 OK`

```json
{
  "success": true,
  "profile": {
    "conversion": {
      "default_profile": "auto-premium",
      "default_language": "pl",
      "force_ocr": false,
      "heading_repair": true
    },
    "email_delivery": {
      "enabled": false,
      "host": "",
      "port": 587,
      "security": "starttls",
      "username": "",
      "from_address": "",
      "default_recipient": "",
      "max_attachment_bytes": 52428800,
      "secret_configured": false
    }
  },
  "profile_scope": "local",
  "cloud_sync": {"status": "local", "provider": "local"},
  "authenticated": false,
  "profile_path_configured": false,
  "profile_path": "C:\\Users\\user\\AppData\\Roaming\\KindleMaster\\profile.json"
}
```

The response never includes SMTP passwords or API keys. It may include `email_delivery.default_recipient`, which is the user's saved Send-to-Kindle address; per-job delivery attempts still persist only `masked_recipient` and `recipient_hash`.

## `PUT /user/profile`

Persists profile defaults. Guest mode writes only the local profile; authenticated Supabase sessions upsert `conversion_defaults` and `smtp_defaults` in `public.user_profiles` through the user's Bearer token and RLS. Unknown fields are ignored, and secret-like fields such as `password`, `api_key`, `secret`, and `token` are not stored.

Request:

```json
{
  "conversion": {
    "default_profile": "magazine",
    "default_language": "en",
    "force_ocr": false,
    "heading_repair": true
  },
  "email_delivery": {
    "enabled": true,
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "username": "user-or-apikey",
    "from_address": "kindlemaster@example.com",
    "default_recipient": "device@kindle.com",
    "max_attachment_bytes": 52428800
  }
}
```

Failures:

| Condition | HTTP status | `error_code` |
| --- | --- | --- |
| Request body is not a JSON object | `400` | `invalid_profile_request` |
| Authenticated Supabase profile save fails | `503` | `cloud_profile_save_failed` |

## `GET /convert/delivery/config`

Reports whether optional SMTP email delivery is enabled and configured. This endpoint never returns SMTP secrets.

Success: `200 OK`

```json
{
  "success": true,
  "delivery": {
    "enabled": true,
    "configured": true,
    "provider": "smtp",
    "host_configured": true,
    "from_configured": true,
    "secret_configured": true,
    "profile_configured": true,
    "config_source": "env+profile",
    "port": 587,
    "security": "starttls",
    "max_attachment_bytes": 52428800,
    "missing_config": []
  }
}
```

Configuration is merged from the local user profile and env vars. Env vars win for every field they provide. `KINDLEMASTER_SMTP_PASSWORD` is always env-only and is required for a configured sender; the profile may store only non-secret SMTP defaults. Supported env vars are `KINDLEMASTER_EMAIL_DELIVERY=1`, `KINDLEMASTER_SMTP_HOST`, `KINDLEMASTER_SMTP_USERNAME`, `KINDLEMASTER_SMTP_PASSWORD`, `KINDLEMASTER_SMTP_FROM`, and optional port/security/size-limit env vars.

## `POST /convert/delivery/<job_id>/email`

Explicitly sends a ready EPUB as an email attachment through the configured SMTP provider. This is intended for Send-to-Kindle email handoff and is never triggered automatically by conversion.

Request:

```json
{
  "to": "device@kindle.com",
  "subject": "optional",
  "message": "optional"
}
```

Success requires all of:

- job status is `ready`,
- EPUB output file exists locally,
- SMTP is configured and the recipient email is valid,
- attachment size is at or below `KINDLEMASTER_EMAIL_MAX_ATTACHMENT_BYTES`.

Quality state is advisory for SMTP delivery. If `release_verdict` is not `release_ready`
or `send_to_kindle_ready` is not `true`, the email action may still send the generated
EPUB and includes `delivery.quality_gate.warning_only=true` with the remaining quality
blockers. The quality gate should block publication claims, not the explicit email
transport requested by the user.

Success: `200 OK`

```json
{
  "success": true,
  "job_id": "9f8b...",
  "delivery": {
    "status": "sent",
    "channel": "email",
    "target": "send_to_kindle",
    "masked_recipient": "d***@kindle.com",
    "recipient_hash": "sha256...",
    "sent_at": "2026-05-19T12:00:00Z",
    "attachment_filename": "book.epub",
    "attachment_size_bytes": 123456,
    "diagnostics": {
      "smtp": {
        "host": "smtp.example.com",
        "port": 587,
        "security": "starttls",
        "accepted_by_smtp": true,
        "from_matches_smtp_username": true,
        "refused_recipient_count": 0
      },
      "message": {
        "from": "d***@example.com",
        "to": "d***@kindle.com",
        "content_type": "multipart/mixed",
        "has_plain_text_body": true,
        "attachment_count": 1,
        "raw_mime_logged": false
      },
      "attachment": {
        "filename": "book.epub",
        "content_type": "application/epub+zip",
        "content_disposition": "attachment",
        "content_transfer_encoding": "base64",
        "size_bytes": 123456,
        "sha256": "..."
      }
    },
    "quality_gate": {
      "delivery_allowed": true,
      "warning_only": true,
      "release_verdict": "release_blocked",
      "send_to_kindle_ready": false,
      "send_to_kindle_blockers": []
    }
  }
}
```

The job stores only the masked recipient and hash, never the full recipient address.

Failures:

| Condition | HTTP status | `error_code` |
| --- | --- | --- |
| Invalid request or email address | `400` | `invalid_delivery_request` |
| Unknown job | `404` | `delivery_not_ready` |
| Job, output, or size not ready | `409` | `delivery_not_ready` |
| SMTP delivery disabled or incomplete | `503` | `delivery_unavailable` |
| SMTP send failure | `502` | `delivery_failed` |

## `GET /convert/jobs`

Lists recent jobs for local history, operator recovery, and UI refresh after reload.

Query parameters:

| Parameter | Default | Notes |
| --- | --- | --- |
| `limit` | `25` | Clamped to `1..100`; invalid values use the default. |

Success: `200 OK`

```json
{
  "success": true,
  "jobs": [
    {
      "job_id": "9f8b...",
      "status": "ready",
      "message": "EPUB gotowy do pobrania.",
      "source_type": "pdf",
      "filename": "source.pdf",
      "created_at": "2026-04-28T12:00:00Z",
      "updated_at": "2026-04-28T12:01:30Z",
      "elapsed_seconds": 90,
      "output_size_bytes": 123456,
      "quality_state_url": "/convert/quality/9f8b...",
      "download_url": "/convert/download/9f8b..."
    }
  ],
  "count": 1,
  "total": 1
}
```

Ready jobs include `download_url`. Failed jobs include `error` and `error_code`. Active jobs omit `download_url`.

When `Authorization: Bearer <Supabase access token>` is present and Supabase is configured, `/convert/jobs` returns account-scoped cloud history. Without a token, it keeps the local guest history behavior.

## `GET /convert/library`

Lists generated EPUB artifacts as a library. Guests see the local read-only library. Authenticated users see account-scoped Supabase history with the same item shape where possible. It is the richer counterpart to `/convert/jobs` and includes quality verdicts, report links, and optional search metadata.

Query parameters:

| Parameter | Default | Notes |
| --- | --- | --- |
| `limit` | `25` | Clamped to `1..100`. |
| `q` or `query` | empty | Matches title, filename, source type, document class, verdicts, blockers, issues, and optional text excerpts. |
| `status` | empty | Filters by job status, for example `ready`, `failed`, `timed_out`. |
| `release_verdict` or `verdict` | empty | Filters by `release_ready`, `ready_with_review`, `release_blocked`, or `failed`. |
| `include_text` | `false` | When true, extracts a bounded EPUB text excerpt for local search evidence. |

Each item includes `job_id`, `title`, `filename`, `status`, `source_type`, `document_class`, `release_verdict`, `reading_verdict`, `release_blocked`, `download_available`, optional `download_url`, `quality_state_url`, `report_json_url`, `report_markdown_url`, `quality_blockers`, and bounded `text_excerpt` when requested.

Authenticated payloads add `library_scope="account"` when Supabase history is available. If cloud persistence is temporarily unavailable, the API may return `library_scope="local_fallback"` plus `cloud_sync.status="failed"` while keeping local conversion usable.

## `GET /auth/config`

Returns non-secret account configuration for React:

```json
{
  "success": true,
  "auth": {
    "enabled": true,
    "configured": true,
    "provider": "supabase",
    "supabase_url": "https://project.supabase.co",
    "publishable_key": "sb_publishable_...",
    "require_login": false,
    "missing_config": []
  }
}
```

`SUPABASE_SERVICE_ROLE_KEY` is never returned.

## `GET /auth/me`

Validates `Authorization: Bearer <access_token>` through Supabase Auth and returns only sanitized identity fields:

```json
{
  "success": true,
  "auth": {
    "authenticated": true,
    "user_id": "uuid",
    "email_masked": "r***@example.com"
  }
}
```

No token returns `authenticated=false`. Invalid tokens return an auth error without echoing the token.

## `POST /user/library/import-local`

Requires a valid Bearer token. Imports terminal local jobs with existing EPUB artifacts into the authenticated Supabase account. It never imports automatically after login; React shows `Importuj lokalną historię` and calls this endpoint only after explicit user action.

## `GET /convert/archive`

Alias of `/convert/library` for automation that wants archive terminology. Same parameters and response shape.

## `GET /convert/search`

Searches the local conversion archive with `include_text=true` by default, so generated EPUB text can match queries without building a reader UI.

## `GET /convert/report/<job_id>.json|md`

Exports a single conversion quality report. JSON returns the library item plus full `quality_state`. Markdown returns an operator-friendly report containing job metadata, release verdict, blockers, text excerpt when available, and raw quality state JSON.

## Verification

Contract changes should run:

```powershell
python -B -m unittest test_conversion_api_contracts.py test_app_async_convert.py test_app_runtime_services.py
python -B kindlemaster.py test --suite quick
```

If only this document changes, run the closest docs/governance lane:

```powershell
python -B -m unittest test_project_status.py
python -B kindlemaster.py status
```
