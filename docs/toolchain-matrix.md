# Toolchain Matrix

This document defines which local toolchains KindleMaster treats as core, which ones are optional, and how `bootstrap`, `doctor`, and `test` interpret missing capabilities.

For the operator setup sequence and failure classification guidance, see [local-bootstrap-toolchain.md](local-bootstrap-toolchain.md). This matrix remains the authority for support levels and verification surfaces.

## Bootstrap Profiles

| Profile | Command | Installs | What it supports |
| --- | --- | --- | --- |
| `runtime_only` | `python kindlemaster.py bootstrap --runtime-only` | `requirements.txt` | conversion, validation, smoke, Flask serving, `quick`, and the core `release` lane |
| `developer` | `python kindlemaster.py bootstrap` | `requirements.txt` + `requirements-dev.txt` | everything in `runtime_only` plus pytest/coverage/ruff/pip-audit/Waitress/Playwright support for governance, browser, and runtime verification lanes |

Bootstrap only manages Python packages. It does not install Java, EPUBCheck, Tesseract, Ghostscript, qpdf, PDFBox, or Chromium.

## Verification Surfaces

| Surface | Support level | Minimal command | Required local toolchain | Degradation behavior |
| --- | --- | --- | --- | --- |
| `quick` | core | `python kindlemaster.py test --suite quick` | runtime bootstrap only | hard-fails if runtime Python deps are missing |
| `corpus` | core | `python kindlemaster.py test --suite corpus` | runtime bootstrap only | hard-fails if runtime Python deps are missing; writes derived corpus gate reports and benchmark summaries under `reports/corpus/` |
| `release` | core | `python kindlemaster.py test --suite release` | runtime bootstrap only | runs bounded release-specific unit shards plus the standard corpus gate; browser/runtime follow-ups are skipped when their optional toolchains are missing |
| `browser` | optional | `python kindlemaster.py test --suite browser` | developer bootstrap + Chromium | returns a clear unavailable report if Playwright or Chromium is missing |
| `runtime` | optional | `python kindlemaster.py test --suite runtime` | developer bootstrap + Chromium | returns a clear unavailable report if Waitress, Playwright, or Chromium is missing |

## GitHub Governance Matrix

The GitHub READY workflow defines the external compatibility policy for CI:

| Lane | OS | Python | Purpose |
| --- | --- | --- | --- |
| `ready-governance` | Ubuntu | Python 3.12, 3.13, and 3.14 | Supported Python matrix for static-quality, dependency consistency, and governance coverage |
| `ready-governance` | Windows | Python 3.14 | Windows canary for local-first operator compatibility |
| `ready-quick` | Ubuntu | Python 3.14 | Mirrors `python kindlemaster.py test --suite quick` |
| `ready-release` | Ubuntu | Python 3.14 | Mirrors `python kindlemaster.py test --suite release` |
| `ready-gate` | Ubuntu | n/a | Stable branch-protection aggregate over governance, quick, and release lanes |

Governance CI runs `ruff` with correctness-only rules (`E9,F63,F7,F82`) over governance/control-plane files, `pip check`, one `pip-audit` dependency audit on Ubuntu Python 3.14, a coverage threshold of `75` for deterministic command/status governance paths (`kindlemaster.py` and `scripts/generate_project_status.py`), and a core conversion coverage threshold of `45` on Ubuntu Python 3.14. Quick and release jobs upload derived `reports/` and `output/` artifacts for review.

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
- `verification_surfaces`: the local status of `quick`, `corpus`, `release`, `browser`, and `runtime`.
- `conversion_capabilities`: whether optional EPUBCheck/OCR/PDFBox enhancements are available.

## Operating Guidance

1. Start with `python kindlemaster.py bootstrap` for a standard developer workstation.
2. Run `python kindlemaster.py doctor` after machine setup changes to confirm what is actually available.
3. Use `quick` for routine Python-only changes.
4. Use `corpus` when you need the expanded fixture bank plus a derived corpus gate and benchmark report.
5. Use `release` after `quick` when you want the bounded release-specific gate without making browser/runtime tooling mandatory; it reports `passed_with_warnings` when corpus/manual-review evidence is not fully clean.
6. Use `browser` or `runtime` only when the change area actually touches those surfaces.
