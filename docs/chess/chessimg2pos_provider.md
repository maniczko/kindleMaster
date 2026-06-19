# `chessimg2pos` External Provider

`mdicio/chessimg2pos` can be enabled as an optional scanned-chess FEN provider.

## Safety Model

- Disabled by default.
- Never changes `/convert` inputs or outputs.
- Never bypasses local KindleMaster publish rules.
- Runs only for scanned-board crops that remain `requires_review` or stay near the local confidence boundary.
- Runs on at most two controlled crop variants: `best` and `reader_visible`.
- Exact crop labels still win first.
- External output is treated as an additional candidate, not as trusted ground truth.
- Auto-promotion requires evidence, not just raw external confidence:
  - local/external placement agreement, or
  - agreement between two external crop variants, or
  - replay-backed proof from downstream PGN/exercise flow.

## Configuration

Set environment variables before running `python kindlemaster.py convert ...`:

```powershell
$env:KINDLEMASTER_CHESSIMG2POS_ENABLED = "1"
$env:KINDLEMASTER_CHESSIMG2POS_MODE = "auto"
$env:KINDLEMASTER_CHESSIMG2POS_PYTHON = "C:\path\to\provider-env\python.exe"
$env:KINDLEMASTER_CHESSIMG2POS_MODEL_PATH = "C:\path\to\chessimg2pos-model"
$env:KINDLEMASTER_CHESSIMG2POS_TIMEOUT_MS = "4000"
```

Supported modes:

- `auto`: try in-process import first, then subprocess if `KINDLEMASTER_CHESSIMG2POS_PYTHON` is configured.
- `import`: require `chessimg2pos` in the active KindleMaster Python environment.
- `subprocess`: run the provider in a separate Python environment.

Subprocess mode uses:

```text
scripts/run_chessimg2pos_provider.py
```

That helper returns a stable JSON schema matching the adapter contract used by KindleMaster.

## Runtime Behavior

Provider order inside scanned-chess:

1. exact verified crop label
2. local recognizer
3. local recognition-only recovery
4. `chessimg2pos` external provider on `best` and optionally `reader_visible`
5. provider-aware consensus and replay-backed promotion
6. current KindleMaster candidate selection and safety checks

External candidates carry audit metadata such as:

- `effective_confidence`
- `variant_role`
- `agreement_count`
- `agrees_with_local_placement`
- `agrees_with_other_external`
- `king_structure_agreement`
- `replay_legal_from_fen`
- `publishable_before_replay`

The provider result is cached separately under:

```text
output/cache/scanned_chess/external_provider/
```

Cache key components:

- crop SHA-256
- provider name
- provider version
- provider mode
- provider payload shape version
- variant role
- selected preprocess variant
- display variant used

## Warnings

Review JSON/HTML can include:

- `external_fen_provider_used`
- `external_fen_candidate_added`
- `external_fen_provider_failed`
- `external_fen_provider_timeout`
- `external_fen_agrees_with_local`
- `external_fen_conflicts_with_local`
- `external_fen_consensus_used`
- `external_fen_multi_crop_agreement`
- `external_fen_multi_crop_conflict`
- `external_fen_promoted_by_replay`
- `external_fen_rejected_by_replay`
- `external_fen_rejected_by_king_structure`

These are audit-only warnings. They do not weaken strict FEN acceptance.
