# Local Bootstrap and Toolchain Runbook

Related Linear scope: VAT-126.

`docs/toolchain-matrix.md` is authoritative for supported toolchains and verification surfaces. This runbook gives operators the exact local sequence for setup, diagnosis, and separating environment problems from EPUB-quality problems.

## Fresh Setup

```powershell
python kindlemaster.py bootstrap
python kindlemaster.py doctor
python kindlemaster.py test --suite quick
```

Use runtime-only setup when browser/runtime verification is not needed:

```powershell
python kindlemaster.py bootstrap --runtime-only
python kindlemaster.py doctor
```

## Expected Runtime

- Python runtime dependencies come from `requirements.txt`.
- Developer verification dependencies come from `requirements-dev.txt`.
- The local app defaults to `http://kindlemaster.localhost:5001/`.
- The safe loopback fallback is `http://127.0.0.1:5001/`.
- `bootstrap` installs Python packages only. It does not install Java, EPUBCheck, Tesseract, Ghostscript, qpdf, PDFBox, or Chromium.

## Diagnose Toolchain State

```powershell
python kindlemaster.py doctor
```

Use the output to classify failures:

| Doctor area | If red/degraded | Treat as |
| --- | --- | --- |
| `bootstrap` | Missing Python modules | setup failure |
| `verification_surfaces.quick` | unsupported | core environment failure |
| `verification_surfaces.corpus` | unsupported | core environment failure |
| `verification_surfaces.browser` | unavailable | optional browser-tooling gap |
| `verification_surfaces.runtime` | unavailable | optional runtime-tooling gap |
| `conversion_capabilities.epubcheck` | missing | validation degraded, not automatic EPUB failure |
| `conversion_capabilities.ocr_pipeline` | degraded | OCR capability degraded, not automatic EPUB failure |

## Browser and Runtime Follow-Ups

Install Chromium only when browser/runtime lanes are needed:

```powershell
python -m playwright install chromium
```

If Waitress or Playwright Python packages are missing, rerun:

```powershell
python kindlemaster.py bootstrap
```

## Local App Verification

```powershell
python kindlemaster.py serve
```

Open:

```text
http://kindlemaster.localhost:5001/
```

If hostname resolution fails, open:

```text
http://127.0.0.1:5001/
```

After runtime code changes, restart the server and verify freshness as required by `AGENTS.md`.

## Acceptance For VAT-126

- The supported setup path is documented.
- Required versus optional tools are separated.
- `doctor` is the inspection command for availability.
- Environment/toolchain failures are not confused with EPUB-quality failures.
