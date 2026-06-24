from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.validate_chess_fen_labels import validate_chess_fen_labels  # noqa: E402


SCHEMA = "kindlemaster.chess_fen.label_inventory.v1"


def evaluate_chess_fen_label_inventory(
    labels_dir: str | Path,
    *,
    target_per_profile: int = 100,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(labels_dir)
    files = sorted(root.glob("*.jsonl")) if root.exists() else []
    profiles: list[dict[str, Any]] = []
    total_label_count = 0
    total_valid_label_count = 0
    for path in files:
        validation = validate_chess_fen_labels(path)
        label_count = int(validation.get("label_count") or 0)
        valid_count = int(validation.get("valid_label_count") or 0)
        total_label_count += label_count
        total_valid_label_count += valid_count
        profiles.append(
            {
                "profile_id": _profile_id_from_labels(path),
                "labels_path": str(path),
                "label_count": label_count,
                "valid_human_verified_label_count": valid_count,
                "status": validation.get("status") or "failed",
                "issue_count": int(validation.get("issue_count") or 0),
                "target_per_profile": int(target_per_profile),
                "missing_to_target": max(0, int(target_per_profile) - valid_count),
                "ready_for_expanded_profile": valid_count >= int(target_per_profile),
            }
        )
    payload = {
        "schema": SCHEMA,
        "labels_dir": str(root),
        "target_per_profile": int(target_per_profile),
        "summary": {
            "profile_count": len(profiles),
            "total_label_count": total_label_count,
            "total_valid_human_verified_label_count": total_valid_label_count,
            "profiles_meeting_target": len([item for item in profiles if item["ready_for_expanded_profile"]]),
            "profiles_missing_target": len([item for item in profiles if not item["ready_for_expanded_profile"]]),
        },
        "profiles": profiles,
        "next_actions": _next_actions(profiles),
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _profile_id_from_labels(path: Path) -> str:
    name = path.stem
    for suffix in ("_seed_positions", "_verified_crop_labels"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _next_actions(profiles: list[dict[str, Any]]) -> list[str]:
    actions = []
    for profile in profiles:
        missing = int(profile.get("missing_to_target") or 0)
        if missing > 0:
            actions.append(
                f"{profile.get('profile_id')}: add {missing} human-verified labels to reach target."
            )
    return actions or ["All profiles meet the configured verified-label target."]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report verified chess FEN label inventory per profile.")
    parser.add_argument("labels_dir", nargs="?", default="reference_inputs/chess_fen/labels")
    parser.add_argument("--target-per-profile", type=int, default=100)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    payload = evaluate_chess_fen_label_inventory(
        args.labels_dir,
        target_per_profile=args.target_per_profile,
        output_path=args.output or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
