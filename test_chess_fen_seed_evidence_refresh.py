from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ChessFenSeedEvidenceRefreshTests(unittest.TestCase):
    def test_seed_evidence_refresh_keeps_historical_fen_review_only(self) -> None:
        from scripts.prepare_chess_fen_seed_evidence_refresh import prepare_chess_fen_seed_evidence_refresh
        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(b"fake-crop-bytes")
            labels_path = root / "old_seed.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "old_label",
                        "crop_path": str(crop_path),
                        "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                        "verified_by": "legacy-human",
                        "verified_at": "2026-05-26",
                        "notes": "Manually transcribed from crop.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = prepare_chess_fen_seed_evidence_refresh(labels_path, output_dir=root / "refresh")
            draft_path = Path(result["draft_path"])
            draft_rows = [json.loads(line) for line in draft_path.read_text(encoding="utf-8").splitlines()]
            validation = validate_chess_fen_labels(draft_path)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["historical_fen_count"], 1)
        self.assertEqual(result["historical_fen_valid_count"], 1)
        self.assertEqual(draft_rows[0]["workflow_state"], "manual_draft")
        self.assertEqual(draft_rows[0]["historical_fen"], "8/8/8/3k4/8/8/4K3/8 w - - 0 1")
        self.assertEqual(draft_rows[0]["fen"], "")
        self.assertEqual(draft_rows[0]["manual_fen"], "")
        self.assertFalse(draft_rows[0]["human_verified"])
        self.assertFalse(draft_rows[0]["square_diff_ack"])
        self.assertEqual(validation["status"], "failed")
        self.assertTrue(any(issue["code"] == "fen_missing" for issue in validation["issues"]))

    def test_completed_refresh_draft_promotes_only_after_manual_fen(self) -> None:
        from scripts.prepare_chess_fen_seed_evidence_refresh import prepare_chess_fen_seed_evidence_refresh
        from scripts.promote_chess_fen_label_draft import promote_chess_fen_label_draft
        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(b"fake-crop-bytes")
            labels_path = root / "old_seed.jsonl"
            fen = "8/8/8/3k4/8/8/4K3/8 w - - 0 1"
            labels_path.write_text(
                json.dumps({"id": "old_label", "crop_path": str(crop_path), "fen": fen, "verified_by": "legacy-human", "verified_at": "2026-05-26"})
                + "\n",
                encoding="utf-8",
            )
            result = prepare_chess_fen_seed_evidence_refresh(labels_path, output_dir=root / "refresh")
            draft_path = Path(result["draft_path"])
            draft_row = json.loads(draft_path.read_text(encoding="utf-8").strip())
            draft_row["manual_fen"] = draft_row["historical_fen"]
            draft_row["human_verified"] = True
            draft_row["square_diff_ack"] = True
            completed_draft = root / "completed_refresh.jsonl"
            completed_draft.write_text(json.dumps(draft_row, ensure_ascii=False) + "\n", encoding="utf-8")
            verified_labels = root / "verified.jsonl"

            promotion = promote_chess_fen_label_draft(
                completed_draft,
                output_path=verified_labels,
                verified_by="unit-test",
                verified_at="2026-06-22",
            )
            validation = validate_chess_fen_labels(verified_labels)
            promoted_row = json.loads(verified_labels.read_text(encoding="utf-8").strip())

        self.assertEqual(promotion["status"], "passed")
        self.assertEqual(promotion["promoted_count"], 1)
        self.assertEqual(validation["status"], "passed")
        self.assertEqual(promoted_row["fen"], fen)
        self.assertEqual(promoted_row["verification_source"], "human_visual")
        self.assertTrue(promoted_row["human_verified"])
        self.assertTrue(promoted_row["square_diff_ack"])
        self.assertTrue(promoted_row["crop_sha256"])


if __name__ == "__main__":
    unittest.main()
