# Toolchain Matrix

This document defines which local toolchains KindleMaster treats as core, which ones are optional, and how `bootstrap`, `doctor`, and `test` interpret missing capabilities.

For the operator setup sequence and failure classification guidance, see [local-bootstrap-toolchain.md](local-bootstrap-toolchain.md). This matrix remains the authority for support levels and verification surfaces.

## Bootstrap Profiles

| Profile | Command | Installs | What it supports |
| --- | --- | --- | --- |
| `runtime_only` | `python kindlemaster.py bootstrap --runtime-only` | `requirements.txt` | conversion, validation, smoke, Flask serving, `quick`, and the core `release` lane |
| `developer` | `python kindlemaster.py bootstrap` | `requirements.txt` + `requirements-dev.txt` plus local `.githooks` setup | everything in `runtime_only` plus pytest/coverage/ruff/pip-audit/Waitress/Playwright and `scikit-learn` support for governance, browser, runtime, and ML training lanes |

Bootstrap manages Python packages and local Git hook configuration for the developer profile. It does not install Java, EPUBCheck, Tesseract, Ghostscript, qpdf, PDFBox, or Chromium.

ML runtime inference is local JSON math and belongs to the runtime footprint. Training uses `scikit-learn` from `requirements-dev.txt` only:

```powershell
python kindlemaster.py ml dataset
python kindlemaster.py ml train
python kindlemaster.py ml evaluate
```

## Verification Surfaces

| Surface | Support level | Minimal command | Required local toolchain | Degradation behavior |
| --- | --- | --- | --- | --- |
| `quick` | core | `python kindlemaster.py test --suite quick` | runtime bootstrap only | hard-fails if runtime Python deps are missing; includes Sprint 2 runtime job and artifact storage contract tests plus Sprint 3 AI quality contract fixtures |
| `corpus` | core | `python kindlemaster.py test --suite corpus` | runtime bootstrap only | hard-fails if runtime Python deps are missing; writes derived corpus gate reports and benchmark summaries under `reports/corpus/` |
| `release` | core | `python kindlemaster.py test --suite release` | runtime bootstrap only | runs bounded release-specific unit shards plus the standard corpus gate; browser/runtime follow-ups are skipped when their optional toolchains are missing |
| `full` | diagnostic | `python kindlemaster.py test --suite full` | runtime bootstrap plus any optional dependencies used by discovered tests | runs `unittest discover -p test*.py` across explicit and intentionally discover-only tests; use as an all-discovery diagnostic lane, not as a bounded release gate |
| `browser` | optional | `python kindlemaster.py test --suite browser` | developer bootstrap + Chromium | returns a clear unavailable report if Playwright or Chromium is missing |
| `runtime` | optional | `python kindlemaster.py test --suite runtime` | developer bootstrap + Chromium | returns a clear unavailable report if Waitress, Playwright, or Chromium is missing; includes Sprint 2 Playwright upload/status/quality/download smoke |

## GitHub Governance Matrix

The GitHub READY workflow defines the external compatibility policy for CI:

| Lane | OS | Python | Purpose |
| --- | --- | --- | --- |
| `ready-governance` | Ubuntu | Python 3.12, 3.13, and 3.14 | Supported Python matrix for static-quality, dependency consistency, and governance coverage |
| `ready-governance` | Windows | Python 3.14 | Windows canary for local-first operator compatibility |
| `ready-quick` | Ubuntu | Python 3.14, Node 22 when `package.json` exists | Mirrors `python kindlemaster.py test --suite quick`, runs Sprint 1 QA regressions, and runs `npm run test:contracts:regression` from the React/Vite workspace; keep an equivalent `pnpm run test:contracts:regression` hook if the workspace later moves to pnpm |
| `ready-release` | Ubuntu | Python 3.14 | Mirrors `python kindlemaster.py test --suite release` |
| `ready-gate` | Ubuntu | n/a | Stable branch-protection aggregate over governance, quick, and release lanes |

Governance CI runs `ruff` with correctness-only rules (`E9,F63,F7,F82`) over governance/control-plane files, `test_agent_config_contracts.py` for agent readiness contracts, `pip check`, one `pip-audit` dependency audit on Ubuntu Python 3.14, a coverage threshold of `75` for deterministic command/status governance paths (`kindlemaster.py` and `scripts/generate_project_status.py`), and a core conversion coverage threshold of `45` on Ubuntu Python 3.14. Quick CI runs the Python quick suite, `test_sprint1_quality_gates.py`, and the Node/Vitest contract hook (`npm run test:contracts:regression`, pnpm equivalent acceptable after migration) when a Node workspace exists. Sprint 4 UI work additionally uses `npm run build:ui` and `npm run test:ui` locally before browser/runtime verification. Quick and release jobs upload derived `reports/` and `output/` artifacts for review.

Install Chromium for Playwright-backed surfaces with:

```powershell
python -m playwright install chromium
```

## Optional External Tools

| Capability | Support level | Required local tools | Notes |
| --- | --- | --- | --- |
| EPUBCheck validation | optional | Java + `epubcheck.jar` | KindleMaster still runs internal validators when EPUBCheck is unavailable |
| OCRmyPDF pipeline | optional | Tesseract + OCRmyPDF + Ghostscript + qpdf | falls back to direct Tesseract OCR when OCRmyPDF system dependencies are incomplete |
| PDFBox helpers | optional | Java + `pdfbox-app*.jar` | used for optional extraction/diagnostic flows |

## Doctor Output

Use:

```powershell
python kindlemaster.py doctor
```

The report is intended to answer three questions:

1. Which Python bootstrap profiles are currently installed?
2. Which verification surfaces are `supported`, `degraded`, `unsupported`, or `unavailable`?
3. Which optional external capabilities are present versus missing?

Key sections:

- `bootstrap`: the supported Python bootstrap profiles, their missing modules, and manual follow-up steps.
- `agent_readiness`: Codex config, pinned Playwright MCP, required plugins, KindleMaster skills, `.githooks`, and stale local agent settings.
- `verification_surfaces`: the local status of `quick`, `corpus`, `release`, `browser`, and `runtime`.
- `conversion_capabilities`: whether optional EPUBCheck/OCR/PDFBox enhancements are available.

## Operating Guidance

1. Start with `python kindlemaster.py bootstrap` for a standard developer workstation.
2. Run `python kindlemaster.py doctor` after machine setup changes to confirm what is actually available.
3. Use `quick` for routine Python-only changes.
4. Use `corpus` when you need the expanded fixture bank plus a derived corpus gate and benchmark report.
5. Use `release` after `quick` when you want the bounded release-specific gate without making browser/runtime tooling mandatory; it reports `passed_with_warnings` when corpus/manual-review evidence is not fully clean.
6. Use `full` only when you need a diagnostic all-discovery sweep of every `test*.py`, including tests intentionally kept out of explicit suites.
7. Use `browser` or `runtime` only when the change area actually touches those surfaces.
