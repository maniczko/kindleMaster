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

## Repeatable Toolchain Container

Use the container path when release-quality work needs a repeatable toolchain rather than the operator's current Windows setup.

Build:

```powershell
docker build -f Dockerfile.toolchain -t kindlemaster-toolchain .
```

Run `doctor`:

```powershell
docker run --rm -it -v ${PWD}:/workspace -w /workspace kindlemaster-toolchain python kindlemaster.py doctor
```

Run the standard proof lanes:

```powershell
docker run --rm -it -v ${PWD}:/workspace -w /workspace kindlemaster-toolchain python kindlemaster.py test --suite quick
docker run --rm -it -v ${PWD}:/workspace -w /workspace kindlemaster-toolchain python kindlemaster.py test --suite corpus
docker run --rm -it -v ${PWD}:/workspace -w /workspace kindlemaster-toolchain python kindlemaster.py test --suite release
```

The same image is used by `.devcontainer/devcontainer.json`. Open the folder in a devcontainer to get the full verification environment with:

- Python runtime and developer dependencies from `requirements-dev.txt`,
- Java and EPUBCheck,
- Tesseract, OCRmyPDF, Ghostscript, and qpdf for OCR/fallback validation,
- Playwright Chromium from the Playwright base image.

Runtime-only work does not require Docker. If the container reports a degraded capability, treat it as an environment/toolchain issue first and confirm with `python kindlemaster.py doctor` before blaming EPUB quality.

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

## Acceptance For Toolchain Work

- The supported setup path is documented.
- Required versus optional tools are separated.
- `doctor` is the inspection command for availability.
- Environment/toolchain failures are not confused with EPUB-quality failures.
- Docker/devcontainer is available for full release-quality verification, while local runtime-only setup remains supported.
