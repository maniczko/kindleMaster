# Side-to-move audit export automation prompt

Use this prompt when a local conversion has completed but the generated marker/side-to-move reports are hard to locate or need to be handed off to ChatGPT for analysis.

```text
Repo: maniczko/kindleMaster
Local path: C:\Users\user\Desktop\kindleMaster

Mode: implement a small export/handoff utility. Do not change marker recognition logic, classifier thresholds, FEN gates, or reader UI behavior.

Problem:
After a local conversion, ChatGPT cannot directly read files from the user's local Windows filesystem or localhost. The side-to-move runtime reports may exist under repo output paths or %TEMP%\kindlemaster, but the user has to find and upload them manually.

Goal:
Add a safe, explicit export command that finds the latest conversion job with side-to-move/chess diagnostic reports, prints the key metrics, and creates a ZIP bundle that the user can upload to ChatGPT.

Required command:

python kindlemaster.py chess export-side-to-move-audit --latest

Optional parameters:

--job-output <path>        explicit conversion output directory
--out <zip path>           output ZIP path
--include-html             include HTML diagnostics; default yes
--json-summary <path>      write a small standalone summary JSON next to the ZIP

Search roots for --latest:

- ./output
- ./output/artifacts
- %TEMP%/kindlemaster
- any existing local app artifact root used by conversion jobs

Files to include when present:

reports/chess_fen/why_side_to_move_not_trusted.json
reports/chess_fen/why_side_to_move_not_trusted.md
reports/chess_fen/why_side_to_move_not_trusted.html
reports/chess_fen/side_to_move_coverage_dashboard.json
reports/chess_fen/side_to_move_coverage_dashboard.md
reports/chess_fen/side_to_move_coverage_dashboard.html
reports/chess_fen/crop_qa_regression_diff.json
reports/chess_fen/crop_qa_regression_diff.md
reports/chess_fen/two_crop_quality_metrics.json
reports/chess_fen/two_crop_quality_metrics.md
reports/chess_fen/side_marker_assignment.json
reports/chess_fen/side_marker_blocker_attribution.json
chess_diagrams.json
positions.json
reports/chess_reader/semantic_book.json

Safety rules:

- Do not include source PDF.
- Do not include full page images.
- Do not include board crop images or marker crop images by default.
- Do not include user credentials, environment files, or raw uploads.
- If adding --include-crops later, keep it off by default and warn that it may include copyrighted visual material.

Expected console output:

STATUS:
- selected job output:
- found why_side_to_move_not_trusted: yes/no
- found coverage dashboard: yes/no
- found crop QA diff: yes/no
- zip path:

KEY METRICS:
- diagram_count:
- side_unknown_count:
- marker_search_zone_coverage_rate:
- marker_bbox_detection_rate:
- marker_crop_generation_rate:
- marker_crop_quality_pass_rate:
- trusted_marker_rate:
- side_to_move_coverage_rate:
- trusted_side_to_move_rate:
- full_fen_safe_acceptance_rate:

TOP BLOCKERS:
1.
2.
3.
4.
5.

Acceptance criteria:

- Running `python kindlemaster.py chess export-side-to-move-audit --latest` creates a ZIP if any side-to-move/chess diagnostics exist.
- If no diagnostics exist, the command prints a clear explanation and candidate directories checked.
- The ZIP contains only safe diagnostic JSON/MD/HTML files by default.
- The command prints key metrics without requiring the user to run PowerShell JSON parsing.
- Add unit tests for:
  - latest job discovery,
  - ZIP creation,
  - missing-report diagnostic output,
  - metric extraction from why_side_to_move_not_trusted and coverage dashboard.

Validation:

python -m unittest test_chess_side_to_move_audit_export.py
python kindlemaster.py test --suite quick
```
