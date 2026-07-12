# KindleMaster

KindleMaster is a local-first PDF-to-EPUB and DOCX-to-EPUB conversion toolkit focused on high-quality Kindle reading output.

## Quick Start

```powershell
python kindlemaster.py bootstrap
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite quality-critical
python kindlemaster.py test --suite corpus
python kindlemaster.py status
python kindlemaster.py test --suite browser
python kindlemaster.py test --suite runtime
python kindlemaster.py test --suite release
npm run build:ui
npm run test:ui
```

The supported toolchain matrix lives in [docs/toolchain-matrix.md](docs/toolchain-matrix.md).

## Authority Map

- `kindlemaster.py` is the executable source of truth for the CLI command surface, including subcommands, flags, defaults, and exit behavior.
- `AGENTS.md` is the canonical human-readable authority map for standard command policy, workflow artifacts, and which docs are authoritative versus derived.
- `docs/toolchain-matrix.md` is authoritative for supported local toolchains and `test --suite` lane expectations.
- `docs/product-scope.md` is authoritative for the v1 product boundary, benchmark classification, and Score 9 product criteria.
- `docs/v2-reader-workflow-roadmap.md` defines deferred v2 Send-to-Kindle, notes/highlights, and Obsidian/Readwise export workflows without changing the v1 release-grade converter scope.
- `docs/local-bootstrap-toolchain.md` is the operator runbook for setup, `doctor`, and environment-versus-quality failure classification.
- `docs/conversion-pipeline.md` maps the current PDF/DOCX to EPUB pipeline, responsible modules, fallback reporting, and stage-level tests.
- `docs/conversion-profiles.md` explains the UI conversion profiles and when to use each route.
- `docs/send-to-kindle-handoff.md` explains the manual post-conversion handoff and when an EPUB is safe to send.
- `docs/github-autopilot-orchestration.md` defines GitHub Issues as the task truth for local Codex autopilot, including labels, issue contract, and `orchestrate` commands.
- `docs/kindle-previewer-validation.md` defines the manual Kindle Previewer and Send to Kindle evidence checklist.
- `docs/text-artifact-rate.md` documents reader-facing text artifact rate thresholds used by quality reports.
- `docs/sprint3-ai-quality-intelligence.md` documents the AI OCR cleanup and AI TOC detection contract, including fallback, confidence, and cost reporting.
- `docs/sprint4-ui-modernization.md` documents the React/Vite UI shell, `/app` route, shadcn-style primitives, and migration rule.
- `docs/deployment-vercel-railway.md` documents the split Vercel frontend + Railway backend deployment.
- `reference_inputs/golden_epub_expectations.json` defines golden EPUB feature expectations for representative conversion classes.
- `docs/source-of-truth-matrix.md` mirrors the control-plane authority model for status, GitHub Issues, reports, and release truth.
- `docs/independent-audit-mode.md` explains standalone EPUB artifact audit versus full project status.
- `docs/premium-epub-release-checklist.md` is the agent-facing release-readiness checklist for premium EPUB output.
- `.github/ISSUE_TEMPLATE/kindlemaster_task.yml` is the reusable GitHub Issue form for agent-executable KindleMaster tasks; `.github/ISSUE_TEMPLATE/agent_task.yml` is a generic Codex compatibility alias.
- `.codex/orchestration.json` mirrors the native `python kindlemaster.py orchestrate` contract for global Codex tooling. The native orchestrator remains preferred.
- `docs/linear-issue-template.md` is retained for historical VAT/Linear migration only.
- `.codex/config.toml` is authoritative only for active repo-local Codex settings; its comments are convenience mirrors, not an independent policy source.
- `AGENTS.md` Section 34A defines the Codex plugin auto-routing policy for Browser, GitHub, Linear, OpenAI Developers, and Build Web Apps.
- `AGENTS.md` Section 34B plus the `prompt-engineer` skill define prompt auto-normalization: complex prompts are reviewed, rewritten into the standard execution brief, and then executed.
- Generated files under `reports/` and `output/` are derived artifacts, not governance authority.

Repo-local Codex defaults are `gpt-5.5` with `xhigh` reasoning, `on-request` approvals, multi-agent support, GitHub/Linear-as-mirror/Build Web Apps/Browser Use/OpenAI Developers plugins, and pinned Playwright MCP for browser verification.

Plugin routing summary: use Browser for local UI/runtime verification, GitHub for branches, PRs, CI, and issue-backed autopilot work, Linear only when explicitly requested as a mirror, OpenAI Developers only for OpenAI API or `ai_quality` work, and Build Web Apps for frontend/UI tasks. The detailed policy lives in `AGENTS.md`.

Prompt routing summary: use `prompt-engineer` automatically for large, ambiguous, high-impact, or under-specified prompts. It follows `Prompt -> Review -> Rewrite -> Execute`; Polish implementation prompts are normalized around `Cel`, `Kontekst`, `Zakres`, `Kryteria akceptacji`, `Walidacja`, and `Raport końcowy` before execution. Explicit work modes are `TRYB: DEBUG`, `TRYB: IMPLEMENT`, `TRYB: REVIEW`, `TRYB: AUDIT`, `TRYB: UI POLISH`, and `TRYB: EPUB QUALITY AUDIT`.

`python kindlemaster.py doctor` reports these contracts under `agent_readiness.quality_gate`, `agent_readiness.checks.agent_quality_gate`, `agent_readiness.checks.plugin_routing_policy`, `agent_readiness.checks.prompt_engineering_policy`, and `agent_readiness.checks.prompt_engineer_skill`. The collaboration quality threshold is `9.0`.

Local Codex governance also includes tracked Git hooks under `.githooks/`. Developer bootstrap installs them automatically unless `CI=true`, `--runtime-only`, or `KINDLEMASTER_SKIP_GIT_HOOKS=1` is set. To check or repair manually:

```powershell
python scripts/install_git_hooks.py --check
python scripts/install_git_hooks.py --install
```

`python kindlemaster.py doctor` reports `agent_readiness` for Codex config, pinned MCP, enabled plugins, KindleMaster skills, local hook setup, and stale local agent settings.

## Local Setup

The preferred local UI runs on `http://kindlemaster.localhost:5001/`.

The server still binds safely to loopback on `127.0.0.1:5001`, so `http://127.0.0.1:5001/` remains the fallback address for tools that do not resolve the branded hostname.

If you only need the app server:

```powershell
python kindlemaster.py serve
```

If you need browser coverage or the live runtime gate, install the Python browser stack as described in the toolchain matrix.

For a repeatable release-quality environment with Java/EPUBCheck, OCRmyPDF, Tesseract, Ghostscript, qpdf, and Playwright Chromium:

```powershell
docker build -f Dockerfile.toolchain -t kindlemaster-toolchain .
docker run --rm -it -v ${PWD}:/workspace -w /workspace kindlemaster-toolchain python kindlemaster.py doctor
```

The same image is wired through `.devcontainer/devcontainer.json`. Details live in [docs/local-bootstrap-toolchain.md](docs/local-bootstrap-toolchain.md).

For hosted testing, use the split deployment described in [docs/deployment-vercel-railway.md](docs/deployment-vercel-railway.md): Vercel serves the React UI and Railway runs the Dockerized conversion API.

The async HTTP flow keeps the existing `/convert/start -> /convert/status/<job_id> -> /convert/download/<job_id>` contract and now also exposes normalized quality state at `GET /convert/quality/<job_id>`. `GET /convert/status/<job_id>` includes the same payload under `quality_state` plus a `quality_state_url`.

The preferred local UI at `http://127.0.0.1:5001/` uses the Sprint 4 React shell. `python kindlemaster.py serve` builds the local React shell automatically when needed; the direct route is `http://127.0.0.1:5001/app`. During development, use `npm run dev:ui` and open `http://127.0.0.1:5173/`; Vite proxies the existing Flask API. The legacy Flask panel is debug-only behind `KINDLEMASTER_ENABLE_LEGACY_UI=1`.

## Core Commands

The supported first-class command set is `bootstrap`, `doctor`, `prepare-reference-inputs`, `serve`, `convert`, `process`, `validate`, `report`, `review`, `smoke`, `corpus`, `status`, `ml`, `test`, `audit`, `chess-study`, and `workflow`.

```powershell
python kindlemaster.py doctor
python kindlemaster.py prepare-reference-inputs
python kindlemaster.py convert path\to\input.docx --output output\result.epub
python kindlemaster.py smoke --mode micro
python kindlemaster.py smoke --mode quick
python kindlemaster.py corpus
python kindlemaster.py status
python kindlemaster.py ml dataset
python kindlemaster.py ml retrain-all --from-feedback --evaluate --promote-if-better --dry-run
python kindlemaster.py ml train
python kindlemaster.py ml evaluate
python kindlemaster.py ml promote --candidate models\candidates\route_classifier_YYYYMMDD_HHMMSS.json --dry-run
python kindlemaster.py ml rollback --model route_classifier --to-version route-classifier-v1
python kindlemaster.py test --suite quality-critical
python kindlemaster.py test --suite corpus
python kindlemaster.py validate path\to\file.epub
python kindlemaster.py process path\to\chess.pdf --out output\chess_auto --mode auto
python kindlemaster.py process path\to\chess.pdf --out output\chess_auto --mode auto --resume
python kindlemaster.py validate output\chess_auto --strict
python kindlemaster.py report output\chess_auto
python kindlemaster.py review output\chess_auto
python kindlemaster.py audit path\to\file.epub
python kindlemaster.py chess-study run-all --pdf path\to\chess.pdf --html path\to\current.html --out output\yusupov_study --quality-profile default
python kindlemaster.py chess-study run-all --pdf path\to\chess.pdf --html path\to\current.html --out output\yusupov_study_audit --quality-profile smoke --render-pages
python kindlemaster.py workflow baseline path\to\input.pdf --change-area reference
python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>
python kindlemaster.py orchestrate doctor
python kindlemaster.py orchestrate sync --issues-json reports/github/issues.json
```

Chess-study FEN quality loop commands are also available under the same entrypoint:

```powershell
python kindlemaster.py chess-study quality-baseline --out output\yusupov_study
python kindlemaster.py chess-study two-crop-performance --job-output output\chess_auto --report-dir reports\performance\chess_two_crop
python kindlemaster.py chess-study preprocess-boards --out output\yusupov_study
python kindlemaster.py chess-study build-square-dataset --out output\yusupov_study --labels output\yusupov_study\review\fen_verified_labels.jsonl
python kindlemaster.py chess-study train-fen-classifier --out output\yusupov_study
python kindlemaster.py chess-study recognize-fen-local --out output\yusupov_study
python kindlemaster.py chess-study evaluate-fen-ensemble --out output\yusupov_study
python kindlemaster.py chess-study export-fen-corpus-manifest --out output\yusupov_study
python scripts\audit_chess_fen_false_positives.py output\yusupov_study\review\ai_fen_candidates.jsonl output\yusupov_study\review\fen_verified_labels.jsonl --output output\yusupov_study\reports\fen_false_positive_audit.json
```

`two-crop-performance` reads an existing job output and writes timing, candidate-count,
artifact-I/O, and semantic-digest evidence. It never substitutes generated fixtures for
missing real-corpus output and does not package source PDFs or crop bytes in the report.

`process --resume` reuses only compatible, atomically checkpointed two-crop pages. Omitting
the flag always starts a cold two-crop run and ignores existing checkpoints.

`python kindlemaster.py test --suite full` is a diagnostic all-discovery lane. It delegates to `unittest discover -p test*.py`, so it also runs tests intentionally kept out of the explicit `quick`, `release`, `corpus`, `browser`, and `runtime` suite registry.

`python kindlemaster.py test --suite release` uses the local `standard` corpus proof by default. The standard/full proof profiles require release-grade chess FEN corpus evidence: at least two scanned FEN profiles, at least 20 manually verified seed labels per profile, exact FEN accuracy above the configured threshold, and `false_positive_count == 0`. GitHub READY sets `KINDLEMASTER_RELEASE_PROOF_PROFILE=ci` so clean runners can enforce release units and bounded corpus evidence without pretending to have the full local OCR/PDF premium toolchain.

Use `workflow baseline/verify` when you are fixing a real defect and need the standard engineering loop:
`reproduce -> isolate -> fix -> validate -> compare before/after`.

Workflow artifacts are written under `reports/workflows/<run_id>/` and `output/workflows/<run_id>/`; `AGENTS.md` defines the required filenames and contract.

Use `orchestrate` when a GitHub Issue should become an agent-executable contract:
`doctor` validates the local governance files, `sync` reports ready/blocked issues, `claim` prepares a `codex/issue-...` branch, `execute` emits the local agent handoff payload, and `report` creates a PR-ready evidence summary.

The corpus-wide proof lane writes derived reports under `reports/corpus/` and `output/corpus/`, including benchmark timing/class summaries for representative fixtures:
- `reports/corpus/corpus_gate.json`
- `reports/corpus/corpus_gate.md`
- `reports/corpus/premium_corpus_smoke_report.json`
- `reports/corpus/premium_corpus_smoke_report.md`

Golden EPUB regression checks compare structural features, not byte-identical archives:

```powershell
python scripts/run_golden_epub_regression.py --artifact-root output/corpus/smoke
```

They write:
- `reports/golden_epub_regression/golden_epub_regression.json`
- `reports/golden_epub_regression/golden_epub_regression.md`

The derived project status lane reads existing evidence and writes:
- `reports/project_status.json`
- `reports/project_status.md`

The governance evidence lanes are refreshed by:
- `python kindlemaster.py doctor` -> `reports/governance/doctor.json`
- `python kindlemaster.py test --suite quick` -> `reports/governance/quick.json`
- `python kindlemaster.py test --suite quality-critical` -> `reports/governance/quality-critical.json`
- `python kindlemaster.py test --suite release` -> `reports/governance/release.json`

## Coverage

Use this workflow for reliable local coverage (without recursive `kindlemaster.py` subprocesses):

```powershell
python -m coverage erase
python scripts/run_test_coverage.py --suite quick
```

To run the CI-style core conversion coverage gate locally:

```powershell
python -m coverage erase
python scripts/run_test_coverage.py --suite core --include="converter.py,docx_conversion.py,text_cleanup_engine.py,text_normalization.py,kindle_semantic_cleanup.py,epub_validation.py" --fail-under=45
```

To keep focus on the two files from the recent regression area:

```powershell
python -m coverage erase
python scripts/run_test_coverage.py --suite custom test_app_runtime_services.py test_epub_validation.py --include="app_runtime_services.py,epub_validation.py"
```

Use [docs/independent-audit-mode.md](docs/independent-audit-mode.md) when evaluating one EPUB artifact independently from the whole-project status surface.

## Troubleshooting

- `quick` should remain Python-only. If it starts failing on browser dependencies, check that `kindlemaster.py` still excludes browser suites from `QUICK_TESTS`.
- `quality-critical` should remain conversion-focused. If it slows down too much, remove unrelated tests rather than lowering coverage gates.
- `corpus` is the standard rerunnable proof lane for the expanded fixture bank; it runs full smoke plus premium corpus reporting and writes derived status under `reports/corpus/`.
- `full` is diagnostic all-discovery, not a bounded release gate. Prefer explicit suites for routine validation and use `full` when you need to expose hidden or discover-only test drift.
- `status` reads existing evidence under `reports/` and generates one derived project status instead of another hand-maintained summary.
- `browser` requires Python Playwright and Chromium, but it does not need the live Waitress gate.
- `runtime` requires Playwright plus Waitress because it exercises the live HTTP flow before browser smoke.
- If Chromium is missing, run `python -m playwright install chromium`.
- If Waitress is missing, reinstall dev dependencies with `python kindlemaster.py bootstrap` or `python -m pip install -r requirements-dev.txt`.

## Notes

- This repository intentionally ignores generated EPUBs, logs, temporary inspection folders, and local tool downloads.
- The current codebase is Python-first; old Vite/Supabase app assets were removed as part of the KindleMaster migration.
