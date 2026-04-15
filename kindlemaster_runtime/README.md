# Kindle Master Runtime

This directory is the dedicated local runtime space for `kindle master`.

Purpose:
- keep Kindle Master output separate from any foreign frontend runtime
- provide a stable place for baseline EPUB, final EPUB, and local reports
- avoid using frontend runtime folders or Vite assumptions

Expected subfolders created at runtime:
- `output/baseline_epub/`
- `output/final_epub/`
- `output/web_epub/`
- `output/reports/`

Preferred local commands:
- `start-kindlemaster-local.ps1`
- `run-kindlemaster-e2e.ps1`
