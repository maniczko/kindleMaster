from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.export_chess_fen_accepted_audit import (
    export_chess_fen_accepted_audit,
    select_audit_records,
)


GOOD_FEN = "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"
P010_BAD_FEN = "6k1/p4p1p/3p1p2/2p1p3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class AcceptedFenAuditTests(unittest.TestCase):
    def _write_report(self, root: Path, records: list[dict]) -> Path:
        report = {
            "cases": [
                {
                    "id": "synthetic_chess",
                    "quality_report": {
                        "chess_fen": {
                            "diagram_count": len(records),
                            "fen_count": sum(1 for record in records if record.get("fen")),
                            "records": records,
                        }
                    },
                }
            ]
        }
        path = root / "smoke.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def _write_profile_eval(self, root: Path, *, seed_count: int = 100, accuracy: float = 0.99, false_positives: int = 0) -> Path:
        path = root / "corpus_eval.json"
        payload = {
            "profiles": [
                {
                    "id": "stable_profile",
                    "seed_label_count": seed_count,
                    "exact_fen_accuracy": accuracy,
                    "false_positive_count": false_positives,
                    "status": "passed",
                }
            ]
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_crop(self, root: Path, filename: str = "board.png") -> Path:
        crop_dir = root / "crops_source"
        crop_dir.mkdir()
        image_path = crop_dir / filename
        Image.new("RGB", (80, 80), "white").save(image_path)
        return crop_dir

    def _accepted_record(self, record_id: str, *, filename: str = "board.png", fen: str = GOOD_FEN, **extra: object) -> dict:
        record = {
            "id": record_id,
            "page": 10,
            "filename": filename,
            "fen": fen,
            "confidence": 0.98,
            "requires_review": False,
            "method": "image-template-board",
            "profile_id": "stable_profile",
            "squares": [{"square": "a1", "piece": "R", "confidence": 0.99}],
        }
        record.update(extra)
        return record

    def test_requires_review_records_are_always_exported(self) -> None:
        records = [
            {
                "id": "review_case",
                "page": 1,
                "filename": "review.png",
                "placement": "8/8/8/8/8/8/8/8",
                "confidence": 0.2,
                "requires_review": True,
            }
        ]

        queue = select_audit_records(records, sample_rate=0.0, max_accepted_sample=0)

        self.assertEqual([item["id"] for item in queue], ["review_case"])
        self.assertEqual(queue[0]["audit_category"], "requires_review")

    def test_high_confidence_accepted_record_is_sampled_deterministically(self) -> None:
        records = [
            self._accepted_record("accepted_a", filename="a.png", page=1),
            self._accepted_record("accepted_b", filename="b.png", page=2),
        ]
        profile = {"stable_profile": {"id": "stable_profile", "seed_label_count": 100, "exact_fen_accuracy": 1.0, "false_positive_count": 0, "status": "passed"}}

        first = select_audit_records(records, profile_info=profile, sample_rate=0.0, max_accepted_sample=1)
        second = select_audit_records(records, profile_info=profile, sample_rate=0.0, max_accepted_sample=1)

        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["audit_category"], "sampled_accepted")

    def test_high_risk_warning_forces_inclusion_without_sampling(self) -> None:
        records = [self._accepted_record("side_inferred", warnings=["side_to_move_inferred"])]

        queue = select_audit_records(records, sample_rate=0.0, max_accepted_sample=0)

        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["audit_category"], "accepted_high_risk")
        self.assertIn("side_to_move_inferred", queue[0]["risk_reasons"])

    def test_known_bad_p010_d002_is_critical(self) -> None:
        records = [self._accepted_record("p010_d002", fen=P010_BAD_FEN)]

        queue = select_audit_records(records, sample_rate=0.0, max_accepted_sample=0)

        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["risk_level"], "critical")
        self.assertIn("known_bad_p010_d002", queue[0]["risk_reasons"])
        self.assertEqual(queue[0]["square_diffs"][0]["square"], "e5")
        self.assertEqual(queue[0]["square_diff_text"], ["p010_d002: e5 black rook, not black pawn"])

    def test_arbiter_approved_without_human_verification_is_high_risk(self) -> None:
        records = [self._accepted_record("arbiter_case", arbiter_approved=True)]

        queue = select_audit_records(records, sample_rate=0.0, max_accepted_sample=0)

        self.assertEqual(queue[0]["audit_category"], "accepted_high_risk")
        self.assertIn("arbiter_approved_without_human_verification", queue[0]["risk_reasons"])

    def test_ai_approved_without_human_verification_is_high_risk(self) -> None:
        records = [self._accepted_record("ai_case", ai_approved=True)]

        queue = select_audit_records(records, sample_rate=0.0, max_accepted_sample=0)

        self.assertEqual(queue[0]["audit_category"], "accepted_high_risk")
        self.assertIn("ai_approved_without_human_verification", queue[0]["risk_reasons"])

    def test_low_corpus_profile_forces_inclusion(self) -> None:
        records = [self._accepted_record("low_corpus")]
        profile = {"stable_profile": {"id": "stable_profile", "seed_label_count": 2, "exact_fen_accuracy": 0.8, "false_positive_count": 1, "status": "failed"}}

        queue = select_audit_records(records, profile_info=profile, sample_rate=0.0, max_accepted_sample=0)

        self.assertEqual(queue[0]["audit_category"], "accepted_high_risk")
        self.assertIn("low_corpus_seed_label_count", queue[0]["risk_reasons"])
        self.assertIn("low_corpus_exact_accuracy", queue[0]["risk_reasons"])
        self.assertIn("profile_has_false_positives", queue[0]["risk_reasons"])

    def test_missing_crop_is_reported_but_export_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = self._write_report(root, [self._accepted_record("missing_crop", filename="missing.png")])
            profile_eval = self._write_profile_eval(root)
            output_dir = root / "audit"

            summary = export_chess_fen_accepted_audit(
                report,
                output_dir=output_dir,
                corpus_eval=profile_eval,
                sample_rate=1.0,
                max_accepted_sample=1,
            )

            self.assertEqual(summary["missing_crop_count"], 1)
            self.assertTrue((output_dir / "accepted_audit_queue.json").is_file())
            self.assertTrue((output_dir / "accepted_audit_queue.jsonl").is_file())
            self.assertTrue((output_dir / "accepted_audit_review.html").is_file())
            self.assertTrue((output_dir / "accepted_audit_summary.json").is_file())

    def test_report_artifacts_include_crop_overlay_and_manual_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            crop_source = self._write_crop(root, "board.png")
            report = self._write_report(root, [self._accepted_record("accepted_sample", filename="board.png", fen=START_FEN)])
            profile_eval = self._write_profile_eval(root)
            output_dir = root / "audit"

            summary = export_chess_fen_accepted_audit(
                report,
                output_dir=output_dir,
                crop_source_dirs=[crop_source],
                corpus_eval=profile_eval,
                sample_rate=1.0,
                max_accepted_sample=1,
            )

            html = (output_dir / "accepted_audit_review.html").read_text(encoding="utf-8")
            queue = json.loads((output_dir / "accepted_audit_queue.json").read_text(encoding="utf-8"))["queue"]
            self.assertEqual(summary["copied_crop_count"], 1)
            self.assertEqual(summary["overlay_count"], 1)
            self.assertIn("accepted_sample", html)
            self.assertIn("manual_label", html)
            self.assertIn("manual_fen", html)
            self.assertIn("board.png", html)
            self.assertEqual(queue[0]["crop_path"], "crops/board.png")
            self.assertTrue(str(queue[0]["overlay_path"]).startswith("overlays/"))


if __name__ == "__main__":
    unittest.main()
