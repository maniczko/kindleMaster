# Issue 341 — conversion job ownership evidence

## Security boundary

Conversion ownership is enforced at the shared `ConversionJobStore` boundary rather than only in selected Flask routes.

- Authenticated jobs are owned by the verified Supabase user ID.
- Anonymous jobs are owned by a browser-generated opaque identifier; only its SHA-256-derived owner ID is persisted.
- Public upload and library collection routes require either a verified account or anonymous session identity.
- Ownerless legacy jobs remain available only for the localhost compatibility workflow.
- A `job_id` alone never authorizes read or mutation.
- Expiring HMAC links authorize read-only `GET`/`HEAD` access to generated artifacts.
- Signed read access does not authorize delete, retry, repair, feedback, profile changes or delivery.

## Covered abuse cases

- User A attempts to delete User B's conversion.
- User A attempts to retry User B's retained input artifact.
- Anonymous browser A attempts to list or delete browser B's conversion.
- A signed artifact token is reused against a write endpoint.
- A public anonymous request starts an upload without an owner identity.
- An invalid or expired signed token attempts to enumerate an existing job.

## Automated evidence

Focused workflow:

```text
python -m unittest -v \
  tests.security.test_conversion_job_access \
  tests.security.test_job_ownership_security \
  test_app_async_convert
npm run typecheck
npm run test:contracts:regression
```

The focused workflow runs on Python 3.12 and 3.14 and captures test logs as retained CI artifacts. The standard READY workflow additionally runs governance, quality-critical, quick, release, browser and corpus gates.

Normal API fetches transport the anonymous capability in the `X-KindleMaster-Guest-Id` header. Only browser-opened artifact links receive `km_guest`; component tests opt into an anonymous session explicitly, while production browser smoke validates automatic creation and propagation.

## Production configuration gate

Railway must define:

```text
KINDLEMASTER_JOB_ACCESS_SECRET=<long-random-backend-only-secret>
KINDLEMASTER_JOB_ACCESS_TTL_SECONDS=900
KINDLEMASTER_ALLOW_LEGACY_LOCAL_GUEST=0
```

Proxy, application, analytics and error-reporting logs must redact `km_guest` and `access` query values.
