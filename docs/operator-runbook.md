# Operator Runbook

Related Linear scope: VAT-210.

This runbook covers local recovery for async `/convert` incidents: hangs, timeouts, restarts, cleanup, missing output, and validation failures. The API contract lives in [api-convert-contract.md](api-convert-contract.md).

## Fast Triage

Confirm the app is up:

```powershell
Get-NetTCPConnection -LocalPort 5001 -State Listen
Invoke-WebRequest http://127.0.0.1:5001/ -UseBasicParsing
```

List recent conversion jobs:

```powershell
Invoke-RestMethod "http://127.0.0.1:5001/convert/jobs?limit=25" | ConvertTo-Json -Depth 8
```

Inspect one job:

```powershell
Invoke-RestMethod "http://127.0.0.1:5001/convert/status/<job_id>" | ConvertTo-Json -Depth 10
Invoke-RestMethod "http://127.0.0.1:5001/convert/quality/<job_id>" | ConvertTo-Json -Depth 12
```

Check the generated project status only as derived evidence:

```powershell
python kindlemaster.py status
```

## Incident Matrix

| Symptom | Likely `error_code` | Checks | Recovery |
| --- | --- | --- | --- |
| Upload rejected or oversized | `upload_failed` | Confirm extension is `.pdf` or `.docx`; check file size against the local upload cap. | Re-upload a supported file; use CLI conversion for controlled local experiments. |
| Job never leaves `queued` | `queue_failed` if terminal, otherwise none | Check `/convert/jobs`; confirm port `5001` is served by the expected process. | Restart the local app if the worker is not progressing; re-submit the conversion. |
| Job stays `running` or `repairing_headings` too long | `conversion_timeout` if watchdog fails it, otherwise none | Compare `elapsed_seconds`, `updated_at` in `/convert/jobs`, and server logs for phase changes. | Keep polling while `updated_at` advances; if stale, restart and re-submit. |
| Job fails quickly for a very large PDF | `interactive_runtime_budget_exceeded` | Check page count/profile in `/convert/status/<job_id>` error; this is expected for extreme diagram/fixed-layout jobs in the browser UI. | Use a shorter sample for UI testing or run the full source as an offline CLI/batch conversion. |
| Job failed during conversion | `conversion_failed` | Inspect `/convert/status/<job_id>` and `/convert/quality/<job_id>`; check logs by `job_id`. | Fix the input/toolchain issue, then run the job again. |
| App restarted during active job | `application_restart` | `/convert/jobs` or `/convert/status/<job_id>` shows `failed` after restart. | Re-submit the source file; local workers are not resumable. |
| Ready job cannot download | `missing_output` | `GET /convert/download/<job_id>` returns JSON `500`; inspect temp directory only after confirming job is terminal. | Re-run conversion; do not claim output was produced unless the EPUB downloads. |
| Quality or EPUB validation blocks release | `validation_failed` | Inspect quality blockers and validator details. | Download the EPUB if available, run validators, then repair or re-convert. |

## Hang and Timeout Recovery

1. Read `/convert/status/<job_id>`.
2. If `status` is `queued`, `running`, or `repairing_headings`, read `/convert/jobs?limit=100` and compare `updated_at`.
3. If `updated_at` is moving, keep polling at `poll_after_ms`.
4. If the job is stale and no worker logs are advancing, restart the app:

```powershell
python kindlemaster.py serve
```

After restart, active jobs should be marked `failed` with `app_restart` or the legacy alias `application_restart`. Re-submit the source file; do not edit persisted job JSON by hand.

## Missing Output Recovery

When a ready job cannot download:

```powershell
Invoke-RestMethod "http://127.0.0.1:5001/convert/download/<job_id>" | ConvertTo-Json -Depth 6
Get-ChildItem "$env:TEMP\kindlemaster" | Sort-Object LastWriteTime -Descending | Select-Object -First 20
```

Expected behavior:

- API response uses `missing_output`.
- The job is terminal `failed`.
- The UI sees a safe message only.
- Logs include `job_id`, `phase: download`, `error_code: missing_output`, and a safe diagnostic message.

Recovery is to re-run conversion. Manual file recreation in the temp directory is not a valid release artifact.

## Validation Failure Recovery

For a downloaded EPUB:

```powershell
python kindlemaster.py validate path\to\file.epub
python epub_quality_recovery.py path\to\file.epub
```

Use `/convert/quality/<job_id>` to decide whether the issue is a release blocker, warning, or manual-review item. Validation blockers should remain visible through `quality_state.quality_blockers` and `quality_state.alerts`.

## Runtime Quality Gate

Every web/CLI conversion now runs the runtime gate on the final EPUB bytes after optional heading repair. The gate writes:

- `premium_scoring`: deterministic premium score, Kindle-ready flags, blockers, and top quality issues.
- `ai_quality_verification` / `quality_policy_verifier`: local deterministic policy verdict with confidence, model version, feature hash, and reason codes. Treat it as policy automation unless `trained_quality_model_status=trained`.
- `quality_gate_mode`: `draft` by default.

Default `draft` mode never blocks the file download or explicit SMTP transport, but it blocks release publication. The UI should show `Nie publikuj`, `send_to_kindle_ready=false`, and visible warnings until the operator reviews the report. Use `--quality-gate-mode off` only for diagnostic comparisons, not for release evidence.

Use `docs/premium-warning-policy.md` when interpreting `passed_with_warnings`.
That state is a bounded review state, not a premium release state, and must not set
`premium_ready=true`.

## ML Route Operations

KindleMaster ML V1 is local-first and audit-only by default. Runtime conversion uses `route_model_mode=shadow`, so the heuristic route remains selected while `route_decision` records the JSON model prediction, confidence, model version, and input feature hash.

```powershell
python kindlemaster.py ml dataset
python kindlemaster.py ml evaluate
```

Dataset generation now writes a readiness report and a feature-collision report:

```powershell
python kindlemaster.py ml dataset --fail-on-collisions
```

Training is blocked unless `dataset_readiness.status=ready`, every main route class has enough examples, and there are zero `features_hash` collisions across different labels. `scikit-learn` is required only for training; runtime JSON inference must continue to work without it.

To log local human feedback for a completed CLI conversion, first keep the conversion report:

```powershell
python kindlemaster.py convert path\to\input.pdf --output output\book.epub --report-json reports\book-conversion.json
python kindlemaster.py ml feedback --report-json reports\book-conversion.json --feedback-status accepted --quality-label usable --quality-score 4 --route-label book_reflow --issue-tag headings --reviewer operator --include-in-training --notes "Usable after TOC review"
python kindlemaster.py ml dataset --feedback-log reports\ml\feedback\conversion_feedback.jsonl
```

Feedback records are append-only JSONL under `reports/ml/feedback/conversion_feedback.jsonl` by default. They never update `models/route_classifier_v1.json` or change route selection. A feedback record becomes a route dataset row only when the operator explicitly uses `--include-in-training` and provides a valid `--route-label`, `--quality-label`, at least one `--issue-tag`, and `--reviewer`.

Training and promotion are two separate steps:

```powershell
python kindlemaster.py ml train
python kindlemaster.py ml promote --candidate models\candidates\route_classifier_YYYYMMDD_HHMMSS.json
```

`ml train` writes candidates under `models/candidates/` and model cards beside them. It never overwrites the runtime model automatically. `ml promote` blocks if holdout metrics, protected-class recall, dataset readiness, or corpus hard negatives (`magazine_layout_heavy`, `diagram_chess`) fail.

For the full offline learning loop, use the batch workflow:

```powershell
python kindlemaster.py ml retrain-all --from-feedback --evaluate --promote-if-better --dry-run
python kindlemaster.py ml retrain-all --from-feedback --evaluate --promote-if-better --write-ledger
```

`ml retrain-all` runs `feedback -> dataset version -> train candidate -> evaluate -> gate -> promote` as one operator workflow. `--dry-run` writes `reports/ml/retrain_all/retrain_all_report.json` without modifying `models/route_classifier_v1.json`. Promotion is blocked unless dataset readiness, candidate metric gates, current-model comparison, protected-class recall, and corpus gate all pass; successful promotion writes a rollback snapshot under `models/rollback/`.

For web/runtime integration, conversion-quality events are recorded automatically after metadata assembly. User feedback is recorded separately through:

```text
POST /convert/feedback/<job_id>
```

This endpoint appends local JSONL feedback and explicitly does not train or mutate models. CLI operators can export the same feedback stream with:

```powershell
python kindlemaster.py ml feedback-export
```

Use `assist` only for controlled experiments:

```powershell
python kindlemaster.py convert path\to\input.pdf --output output\ml-assist.epub --route-model-mode assist
```

Do not treat a model as release-enabling unless `reports/ml/datasets/completeness_report.json` is `ready`, the candidate passes promotion gates, and corpus output is unchanged in default `shadow` mode.

Every `python kindlemaster.py ml dataset` run keeps the legacy dataset files in
`reports/ml/datasets/` for training compatibility and also writes an immutable
snapshot under `reports/ml/datasets/versions/<dataset_version>/`. Use
`reports/ml/datasets/latest_readiness.html` or
`reports/ml/datasets/latest_readiness.json` as the operator-facing answer to
"can I train now?"; `reports/ml/datasets/latest.json` points to the current
version, dataset card, and readiness report.

## Cleanup Guidance

- Conversion job history is local, derived runtime state.
- Temporary files live under `$env:TEMP\kindlemaster`.
- Do not delete files for active jobs.
- Cleanup runs during `/convert` route activity and removes expired terminal jobs/files.
- If manual cleanup is needed, first confirm the job is absent from `/convert/jobs` or terminal for longer than the retention window.

## Structured Recovery Logging

Server-side recovery logs should be structured JSON or key-value records with these fields where feasible:

| Field | Requirement |
| --- | --- |
| `timestamp` | UTC ISO-8601. |
| `level` | `info`, `warning`, or `error`. |
| `event` | Stable event name, for example `convert.job.failed`. |
| `job_id` | Required when a job exists. |
| `phase` | `upload`, `queue`, `conversion`, `validation`, `download`, `cleanup`, or `recovery`. |
| `status` | Current job status when available. |
| `error_code` | One of the stable `/convert` error codes. |
| `safe_message` | User-safe summary with no local paths or traceback text. |
| `source_type` | `pdf` or `docx` when known. |
| `elapsed_seconds` | Numeric duration when available. |
| `output_size_bytes` | Numeric output size when available. |
| `exception_class` | Server-side exception type only; no traceback in API JSON. |

Recommended event names:

| Event | When |
| --- | --- |
| `convert.job.created` | `/convert/start` persists a queued job. |
| `convert.job.phase` | Worker changes phase/status. |
| `convert.job.failed` | A terminal failure is recorded. |
| `convert.job.restart_interrupted` | Startup marks active jobs failed after restart. |
| `convert.job.download_missing` | Download path is missing for a ready job. |
| `convert.cleanup.completed` | Expired jobs/files cleanup completes. |

Tracebacks may be written to server logs for developers, but API responses and browser-visible messages must only contain the safe summary and stable `error_code`.

## Verification

For docs-only runbook changes, run:

```powershell
python -B -m unittest test_project_status.py
python -B kindlemaster.py status
```

For runtime recovery changes, run:

```powershell
python -B -m unittest test_app_async_convert.py test_app_runtime_services.py test_project_status.py
python -B kindlemaster.py test --suite runtime
python -B kindlemaster.py status
```
