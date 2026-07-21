# Production admission security events

KindleMaster emits one structured warning event when a production admission or upload guard rejects a request.

Example schema:

```json
{
  "timestamp": "2026-07-21T18:00:00Z",
  "event": "production_admission_denied",
  "environment": "production",
  "owner_class": "authenticated",
  "route_class": "conversion_start",
  "method": "POST",
  "rule_code": "rate_limit_exceeded",
  "status_code": 429,
  "retryable": true
}
```

## Bounded fields

Allowed values are deliberately low-cardinality:

- `owner_class`: `authenticated` or `guest`;
- `route_class`: conversion start, retry, cancel, delete, mutation or read;
- `rule_code`: one stable admission/input policy code;
- HTTP method and status;
- environment and timestamp.

## Prohibited data

Admission events must never include:

- bearer tokens or guest capabilities;
- Supabase user IDs;
- IP addresses or User-Agent values;
- job IDs;
- filenames;
- signed artifact URLs;
- document text, metadata, FEN, PGN or crop paths.

Dynamic URLs are reduced to a route class before logging, so `/convert/status/<job_id>` never records the identifier.

## Covered decisions

Events are emitted for:

- rate limit rejection;
- owner/global concurrency rejection;
- low disk or memory headroom;
- upload-size, MIME or magic mismatch;
- malformed/password-protected/oversized PDF;
- malformed, traversing, oversized or suspiciously compressed DOCX;
- oversized embedded images.

Unrelated application failures are not mislabeled as admission-security events.

## Validation

```powershell
python -m unittest -v production_tests.test_production_security_events
```

Staging acceptance must inspect captured logs and confirm that tokens, addresses, user/job IDs, filenames and source content are absent.
