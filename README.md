# KindleMaster

KindleMaster is a local-first PDF-to-EPUB and DOCX-to-EPUB conversion toolkit focused on high-quality Kindle reading output.

## Quick Start

```powershell
python kindlemaster.py bootstrap
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite corpus
python kindlemaster.py status
python kindlemaster.py test --suite browser
python kindlemaster.py test --suite runtime
python kindlemaster.py test --suite release
```

The supported toolchain matrix lives in [docs/toolchain-matrix.md](docs/toolchain-matrix.md).

## Authority Map

- `kindlemaster.py` is the executable source of truth for the CLI command surface, including subcommands, flags, defaults, and exit behavior.
- `AGENTS.md` is the canonical human-readable authority map for standard command policy, workflow artifacts, and which docs are authoritative versus derived.
- `docs/toolchain-matrix.md` is authoritative for supported local toolchains and `test --suite` lane expectations.
- `.codex/config.toml` is authoritative only for active repo-local Codex settings; its comments are convenience mirrors, not an independent policy source.
- Generated files under `reports/` and `output/` are derived artifacts, not governance authority.

## Local Setup

The preferred local UI runs on `http://kindlemaster.localhost:5001/`.

The server still binds safely to loopback on `127.0.0.1:5001`, so `http://127.0.0.1:5001/` remains the fallback address for tools that do not resolve the branded hostname.

If you only need the app server:

```powershell
python kindlemaster.py serve
```

If you need browser coverage or the live runtime gate, install the Python browser stack as described in the toolchain matrix.

The async HTTP flow keeps the existing `/convert/start -> /convert/status/<job_id> -> /convert/download/<job_id>` contract and now also exposes normalized quality state at `GET /convert/quality/<job_id>`. `GET /convert/status/<job_id>` includes the same payload under `quality_state` plus a `quality_state_url`.

## Core Commands

The supported first-class command set is `bootstrap`, `doctor`, `prepare-reference-inputs`, `serve`, `convert`, `validate`, `smoke`, `corpus`, `status`, `test`, `audit`, and `workflow`.

```powershell
python kindlemaster.py doctor
python kindlemaster.py prepare-reference-inputs
python kindlemaster.py convert path\to\input.docx --output output\result.epub
python kindlemaster.py smoke --mode quick
python kindlemaster.py corpus
python kindlemaster.py status
python kindlemaster.py test --suite corpus
python kindlemaster.py validate path\to\file.epub
python kindlemaster.py audit path\to\file.epub
python kindlemaster.py workflow baseline path\to\input.pdf --change-area reference
python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>
```

Use `workflow baseline/verify` when you are fixing a real defect and need the standard engineering loop:
`reproduce -> isolate -> fix -> validate -> compare before/after`.

Workflow artifacts are written under `reports/workflows/<run_id>/` and `output/workflows/<run_id>/`; `AGENTS.md` defines the required filenames and contract.

The corpus-wide proof lane writes derived reports under `reports/corpus/` and `output/corpus/`, including:
- `reports/corpus/corpus_gate.json`
- `reports/corpus/corpus_gate.md`
- `reports/corpus/premium_corpus_smoke_report.json`
- `reports/corpus/premium_corpus_smoke_report.md`

The derived project status lane reads existing evidence and writes:
- `reports/project_status.json`
- `reports/project_status.md`

## Troubleshooting

- `quick` should remain Python-only. If it starts failing on browser dependencies, check that `kindlemaster.py` still excludes browser suites from `QUICK_TESTS`.
- `corpus` is the standard rerunnable proof lane for the expanded fixture bank; it runs full smoke plus premium corpus reporting and writes derived status under `reports/corpus/`.
- `status` reads existing evidence under `reports/` and generates one derived project status instead of another hand-maintained summary.
- `browser` requires Python Playwright and Chromium, but it does not need the live Waitress gate.
- `runtime` requires Playwright plus Waitress because it exercises the live HTTP flow before browser smoke.
- If Chromium is missing, run `python -m playwright install chromium`.
- If Waitress is missing, reinstall dev dependencies with `python kindlemaster.py bootstrap` or `python -m pip install -r requirements-dev.txt`.

## Notes

- This repository intentionally ignores generated EPUBs, logs, temporary inspection folders, and local tool downloads.
- The current codebase is Python-first; old Vite/Supabase app assets were removed as part of the KindleMaster migration.
