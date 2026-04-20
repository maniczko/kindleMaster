# KindleMaster

KindleMaster is a local-first PDF-to-EPUB and DOCX-to-EPUB conversion toolkit focused on high-quality Kindle reading output.

## What it does

- detects source type and publication type before conversion
- supports separate flows for books, magazines, and chess/training books
- supports reflowable DOCX input driven by document structure and styles
- rebuilds EPUB structure, navigation, and reading order for better reflow
- runs semantic cleanup for headings, TOC, notation, and PDF artifact reduction
- serves a local Flask UI for upload and conversion

## Main files

- `app.py` – local Flask server and upload API
- `converter.py` – main conversion pipeline
- `docx_conversion.py` – DOCX structural parsing and publication-model mapping
- `publication_analysis.py` – PDF profiling and route selection
- `publication_pipeline.py` – publication-aware EPUB assembly helpers
- `premium_reflow.py` – book/chess-oriented reflow logic
- `magazine_kindle_reflow.py` – magazine-oriented reflow logic
- `kindle_semantic_cleanup.py` – final EPUB cleanup and Kindle normalization
- `pymupdf_chess_extractor.py` – chess diagram extraction and packaging
- `scripts/verify_magazine_epub_quality.py` – quality checks for magazine EPUB output

## Local setup

```powershell
python kindlemaster.py bootstrap
python kindlemaster.py serve
```

The local app runs on `http://127.0.0.1:5001/` by default. You can override the port with `PORT`.

The web UI accepts both PDF and DOCX. PDF keeps page preview and crop tools; DOCX uses structure analysis mode without page preview.

## Standard commands

```powershell
python kindlemaster.py test --suite quick
python kindlemaster.py convert path\to\input.docx --output output\result.epub
python kindlemaster.py smoke --mode quick
python kindlemaster.py validate path\to\file.epub
python kindlemaster.py audit path\to\file.epub
python kindlemaster.py workflow baseline path\to\input.pdf --change-area reference
python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>
```

Use `workflow baseline/verify` when you are fixing a real defect and need the standard engineering loop:
`reproduce -> isolate -> fix -> validate -> compare before/after`.

Use raw `test`, `smoke`, `validate`, and `audit` when you only need one slice of verification rather than the full tracked workflow.

For DOCX work, use the same standard entrypoints:
- `python kindlemaster.py convert path\to\input.docx --output output\result.epub`
- `python kindlemaster.py workflow baseline path\to\input.docx --change-area converter`
- `python kindlemaster.py workflow verify path\to\input.docx --run-id <run_id>`

## Codex project config

Repo-local Codex defaults live in `.codex/config.toml`.

Use that file for KindleMaster-specific defaults such as:
- preferred model and reasoning level,
- approval policy,
- repo-specific integrations,
- standard operational commands and restrictions.

Keep personal machine-wide preferences in `~/.codex/config.toml` and repo-specific behavior in `.codex/config.toml`.

## Notes

- This repository intentionally ignores generated EPUBs, logs, temporary inspection folders, and local tool downloads.
- The current codebase is Python-first; old Vite/Supabase app assets were removed as part of the KindleMaster migration.
