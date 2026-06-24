from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_chess_fen_runtime_regression import audit_chess_fen_runtime_regression


class ChessFenRuntimeRegressionAuditTests(unittest.TestCase):
    def test_buckets_side_to_move_evidence_loss_between_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline = _write_report(
                root / "baseline.json",
                [
                    {
                        "filename": "scan_chess_p010_01.png",
                        "page": 10,
                        "fen": "8/8/8/8/8/8/4K3/4k3 b - - 0 1",
                        "placement": "8/8/8/8/8/8/4K3/4k3",
                        "requires_review": False,
                        "side_to_move_status": "explicit",
                        "side_to_move_evidence": "marker",
                        "warnings": ["side_to_move_marker_detected"],
                    },
                    {
                        "filename": "scan_chess_p011_01.png",
                        "page": 11,
                        "fen": "",
                        "placement": "8/8/8/8/8/8/4K3/4k3",
                        "requires_review": True,
                        "warnings": ["side_to_move_inferred"],
                    },
                ],
            )
            side = _write_report(
                root / "side.json",
                [
                    {
                        "filename": "scan_chess_p010_01.png",
                        "page": 10,
                        "fen": "8/8/8/8/8/8/4K3/4k3 b - - 0 1",
                        "placement": "8/8/8/8/8/8/4K3/4k3",
                        "requires_review": False,
                        "side_to_move_status": "explicit",
                        "side_to_move_evidence": "marker",
                        "warnings": ["side_to_move_marker_detected"],
                    },
                    {
                        "filename": "scan_chess_p011_01.png",
                        "page": 11,
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "placement": "8/8/8/8/8/8/4K3/4k3",
                        "requires_review": False,
                        "side_to_move_status": "explicit",
                        "side_to_move_evidence": "caption",
                        "warnings": ["side_to_move_caption_detected"],
                    },
                ],
            )
            current = _write_report(
                root / "current.json",
                [
                    {
                        "filename": "scan_chess_p010_01.png",
                        "page": 10,
                        "fen": "",
                        "full_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "placement": "8/8/8/8/8/8/4K3/4k3",
                        "requires_review": True,
                        "side_to_move_status": "unknown",
                        "side_to_move_evidence": "none",
                        "warnings": ["side_to_move_marker_detected"],
                    },
                    {
                        "filename": "scan_chess_p011_01.png",
                        "page": 11,
                        "fen": "",
                        "full_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "placement": "8/8/8/8/8/8/4K3/4k3",
                        "requires_review": True,
                        "side_to_move_status": "inferred",
                        "side_to_move_evidence": "inferred",
                        "warnings": ["side_to_move_inferred"],
                    },
                ],
            )

            result = audit_chess_fen_runtime_regression(
                baseline_path=baseline,
                side_evidence_path=side,
                current_path=current,
                output_json=root / "regression.json",
                output_md=root / "regression.md",
            )

        transition = result["transitions"]["side_evidence_to_current"]
        self.assertEqual(transition["before_accepted_count"], 2)
        self.assertEqual(transition["after_accepted_count"], 0)
        self.assertEqual(transition["regression_count"], 2)
        self.assertEqual(transition["regression_buckets"]["side_to_move_evidence_lost_or_not_applied"], 1)
        self.assertEqual(transition["regression_buckets"]["side_to_move_inferred_gate"], 1)
        self.assertEqual(
            result["summary"]["conclusion"],
            "regression_is_real_and_dominated_by_side_to_move_evidence_mapping",
        )


def _write_report(path: Path, records: list[dict[str, object]]) -> Path:
    fen_count = sum(1 for record in records if record.get("fen") and record.get("requires_review") is not True)
    payload = {
        "quality_report": {
            "chess_fen": {
                "diagram_count": len(records),
                "fen_count": fen_count,
                "manual_review_count": len(records) - fen_count,
                "records": records,
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
