from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_ai_consensus_fen_promotion_queue import build_ai_consensus_fen_promotion_queue, main


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"
ALT_FEN = "8/8/8/8/8/8/3K4/4k3 b - - 0 1"


class AiConsensusFenPromotionQueueTests(unittest.TestCase):
    def test_ai_only_rows_become_non_human_auto_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = self._write_crop(root / "crop.png")
            source = self._write_source(
                root / "source.json",
                [
                    {
                        "diagram_id": "ai-only",
                        "status": "ai_consensus",
                        "ai_consensus_fen": FULL_FEN,
                        "crop_path": str(crop),
                    }
                ],
            )
            output_jsonl = root / "queue.jsonl"
            report_json = root / "report.json"

            report = build_ai_consensus_fen_promotion_queue(source, output_jsonl=output_jsonl, output_report_json=report_json)

            rows = self._read_jsonl(output_jsonl)
            self.assertEqual(report["auto_candidate_count"], 1)
            self.assertEqual(report["auto_verified_count"], 0)
            self.assertEqual(rows[0]["label_status"], "auto_candidate")
            self.assertEqual(rows[0]["verification_source"], "ai_consensus_review_only")
            self.assertIs(rows[0]["human_verified"], False)
            self.assertNotIn("verified_by", rows[0])
            self.assertNotIn("verified_at", rows[0])
            self.assertIn("deterministic_proof_missing", rows[0]["blockers"])
            self.assertIn("strict_no_regression_missing", rows[0]["blockers"])
            self.assertEqual(report["human_verified_true_count"], 0)
            self.assertEqual(report["human_visual_source_count"], 0)

    def test_deterministic_consensus_with_crop_hash_and_strict_gate_becomes_auto_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = self._write_crop(root / "crop.png")
            source = self._write_source(
                root / "source.json",
                [
                    {
                        "diagram_id": "safe",
                        "source": "ai_consensus",
                        "ai_candidate_fen": FULL_FEN,
                        "crop_path": str(crop),
                        "deterministic_consensus": True,
                        "strict_regression_gate": {"status": "passed"},
                    }
                ],
            )

            build_ai_consensus_fen_promotion_queue(
                source,
                output_jsonl=root / "queue.jsonl",
                output_report_json=root / "report.json",
            )

            row = self._read_jsonl(root / "queue.jsonl")[0]
            self.assertEqual(row["label_status"], "auto_verified")
            self.assertEqual(row["verification_source"], "deterministic_consensus")
            self.assertIs(row["human_verified"], False)
            self.assertEqual(row["blockers"], [])
            self.assertTrue(row["crop_sha256"])

    def test_duplicate_crop_rows_are_idempotent_and_keep_stronger_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = self._write_crop(root / "crop.png")
            source = self._write_source(
                root / "source.json",
                [
                    {
                        "diagram_id": "candidate",
                        "status": "ai_consensus",
                        "ai_consensus_fen": FULL_FEN,
                        "crop_path": str(crop),
                    },
                    {
                        "diagram_id": "verified",
                        "source": "ai_consensus",
                        "ai_candidate_fen": FULL_FEN,
                        "crop_path": str(crop),
                        "deterministic_match": True,
                        "strict_no_regression_passed": True,
                    },
                ],
            )

            report = build_ai_consensus_fen_promotion_queue(
                source,
                output_jsonl=root / "queue.jsonl",
                output_report_json=root / "report.json",
            )

            rows = self._read_jsonl(root / "queue.jsonl")
            self.assertEqual(len(rows), 1)
            self.assertEqual(report["duplicate_count"], 1)
            self.assertEqual(rows[0]["diagram_id"], "verified")
            self.assertEqual(rows[0]["label_status"], "auto_verified")

    def test_invalid_fen_is_rejected_and_zero_promotions_report_has_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._write_source(
                root / "source.json",
                [{"diagram_id": "bad", "status": "ai_consensus", "ai_consensus_fen": "not a fen"}],
            )

            report = build_ai_consensus_fen_promotion_queue(
                source,
                output_jsonl=root / "queue.jsonl",
                output_report_json=root / "report.json",
            )

            self.assertEqual(self._read_jsonl(root / "queue.jsonl"), [])
            self.assertEqual(report["queue_count"], 0)
            self.assertEqual(report["rejected_count"], 1)
            self.assertTrue(report["next_actions"])
            self.assertEqual(json.loads((root / "report.json").read_text(encoding="utf-8"))["status"], "completed")

    def test_cli_writes_queue_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = self._write_crop(root / "crop.png")
            source = self._write_source(
                root / "source.json",
                [{"diagram_id": "cli", "method": "ai_consensus", "candidate_fen": ALT_FEN, "crop_path": str(crop)}],
            )
            output_jsonl = root / "queue.jsonl"
            report_json = root / "report.json"

            exit_code = main([str(source), "--output-jsonl", str(output_jsonl), "--output-report-json", str(report_json)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(self._read_jsonl(output_jsonl)), 1)
            self.assertEqual(json.loads(report_json.read_text(encoding="utf-8"))["auto_candidate_count"], 1)

    def _write_source(self, path: Path, records: list[dict[str, object]]) -> Path:
        path.write_text(json.dumps({"quality_report": {"chess_fen": {"records": records}}}), encoding="utf-8")
        return path

    def _write_crop(self, path: Path) -> Path:
        path.write_bytes(b"fake crop bytes")
        return path

    def _read_jsonl(self, path: Path) -> list[dict[str, object]]:
        text = path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
