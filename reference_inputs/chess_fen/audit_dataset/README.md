# Chess FEN Audit Dataset

This directory is reserved for verified diagnostic samples used to measure false positives, cropped boards, low-confidence boards, and negative non-board cases.

Do not add synthetic or guessed labels to satisfy release gates.

## Expected JSONL schema

Each row in an audit dataset JSONL file should be an object with:

- `id`: stable case id.
- `sample_type`: one of `false_positive`, `cropped_board`, `low_confidence_board`, `negative_non_board`.
- `crop_path`: local crop evidence path.
- `expected_rejection_reason`: deterministic blocker expected from the runtime.
- `expected_placement`: required for `cropped_board` and `low_confidence_board`; must be empty for `false_positive` and `negative_non_board`.
- `notes`: optional reviewer notes.

## Validation

```powershell
python scripts/validate_chess_fen_audit_dataset.py reference_inputs/chess_fen/audit_dataset/<dataset>.jsonl
```

The validator is schema-only plus evidence-path checks. It does not create labels and does not affect runtime acceptance.
