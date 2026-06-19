# Chess Study Data Contracts

This document records the release-grade evidence contract for chess FEN/PGN automation.

## Full Automation Proof

`scripts/check_chess_full_automation_ready.py` answers whether KindleMaster can claim full chess FEN/PGN automation for release. It is a proof aggregator only; it must not change EPUB, FEN, PGN, label, or corpus runtime behavior.

Default outputs:

- `reports/chess_full_automation_ready.json`
- `reports/chess_full_automation_ready.md`

Required evidence:

- corpus gate output
- FEN corpus output
- profile readiness reports
- holdout eval reports
- accepted FEN audit summaries
- PGN replay/auto-repair eval
- reading-order audit report
- auto-strict validation report
- python-chess availability
- EPUB validation status

## Authority Rules

AI, arbiter approval, high confidence, or model agreement are review evidence only. They must never create verified labels, corpus acceptance, runtime accepted FEN, or strict exported PGN by themselves.

Canonical verified FEN labels require explicit human evidence: `fen`/`manual_fen`, `human_verified=true`, `verification_source=human_visual`, `verified_by`, `verified_at`, crop evidence, crop hash, and square-diff acknowledgement.

Strict PGN release proof requires parser/replay accepted records only. Review-only PGN may remain visible as review evidence, but it must not become downloadable strict PGN.
