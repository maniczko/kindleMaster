# Sprint 2 Playwright Smoke Scaffold

This QA scaffold verifies the browser-visible conversion path without adding a
hard dependency from the quick suite to a live browser.

## Scope

- Upload a reference PDF through the real UI.
- Exercise `/convert/start`, `/convert/status/<job_id>`,
  `/convert/quality/<job_id>`, and `/convert/download/<job_id>` through
  Playwright route fixtures.
- Assert that quality/audit evidence renders before classifying the smoke as
  passed.
- Represent terminal outcomes as `passed`, `failed`, `blocked`, or
  `unavailable`.
- Protect explicit regression cases for heavy PDFs, OCR failure, missing output,
  retryable failure without retry evidence, and timeout states.
- The test is registered in `python kindlemaster.py test --suite runtime`.

## Runtime And Storage Contract

Sprint 2 also adds local-first runtime/storage foundations:

- `runtime_job_adapter.py` records provider, retry policy, timeout, status,
  external id, and replay metadata for each conversion job.
- When `KINDLEMASTER_TRIGGER_ENABLED=1` and `TRIGGER_SECRET_KEY` are set,
  `runtime_job_adapter.py` submits the replay metadata to the real
  `kindlemaster-conversion` Trigger.dev task through
  `scripts/trigger_conversion_job.mjs`. Without those variables, local fallback
  stays active for developer and CI runs.
- `artifact_storage.py` stores input/output/report/log artifacts through a
  local fallback by default and exposes an R2/S3-compatible metadata contract
  when remote storage is configured.
- `/convert/start`, `/convert/status/<job_id>`, `/convert/quality/<job_id>`,
  and `/convert/download/<job_id>` preserve the existing public API while
  exposing `runtime`, `artifacts`, and `artifact_storage` metadata.
- `/convert/download/<job_id>` redirects to a signed output artifact URL when
  one is available; otherwise it keeps the existing local `send_file` path.

## Commands

Contract-only checks:

```bash
python -B -m unittest test_sprint2_playwright_smoke.Sprint2PlaywrightSmokeContractTests
```

Full scaffold, including live Playwright when local tooling is present:

```bash
python -B -m unittest test_sprint2_playwright_smoke.py
```

If Python Playwright, Chromium, Waitress, or the reference fixture is missing,
the live class skips clearly. The contract tests still run and protect the
status mapping without requiring browser tooling.

## Notes

The live browser smoke uses mocked conversion responses against the served UI.
It proves the upload, polling/status, quality/audit rendering, and download
handoff contract, but it does not prove the real conversion backend generated
the EPUB. Keep full conversion evidence in the existing runtime and corpus
lanes.
