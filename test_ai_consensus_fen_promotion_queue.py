from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_ai_consensus_fen_promotion_queue import (
    MISSING_ARTIFACT,
    build_ai_consensus_fen_promotion_queue,
    write_jsonl,
    write_markdown,
)
from scripts.import_ai_consensus_fen_verification import import_ai_consensus_fen_verification


FULL_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
ALT_FEN = "4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1"
HASH = "a" * 64


class AiConsensusFenPromotionQueueTests(unittest.TestCase):
    def test_ai_consensus_enters_queue_and_never_sets_strict_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_report = root / "ai.json"
            current_report = root / "current.json"
            ai_report.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "diagram_id": "d1",
                                "page": 12,
                                "crop_path": "reference_inputs/chess_fen/crops/d1.png",
                                "ai_category": "ai_consensus",
                                "ai_fen": FULL_FEN,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            current_report.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "diagram_id": "d1",
                                "page": 12,
                                "runtime_status": "FEN_REVIEW_REQUIRED",
                                "candidate_fen": ALT_FEN,
                                "selected_placement": ALT_FEN.split()[0],
                                "requires_review": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = build_ai_consensus_fen_promotion_queue(ai_report, current_report)

        self.assertEqual(payload["summary"]["queue_count"], 1)
        record = payload["records"][0]
        self.assertEqual(record["diagram_id"], "d1")
        self.assertEqual(record["ai_category"], "ai_consensus")
        self.assertEqual(record["ai_fen"], FULL_FEN)
        self.assertTrue(record["requires_human_verification"])
        self.assertEqual(record["recommended_action"], "verify_ai_consensus_against_crop")
        self.assertEqual(record["output_label_candidate"]["label_status"], "needs_verification")
        self.assertNotEqual(record["output_label_candidate"]["label_status"], "verified")
        self.assertEqual(record["current_strict_status"], "requires_review")
        self.assertGreaterEqual(len(record["placement_diff"]), 1)

    def test_ai_consensus_currently_strict_accepted_is_not_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_report = root / "ai.json"
            current_report = root / "current.json"
            ai_report.write_text(
                json.dumps({"records": [{"diagram_id": "d1", "ai_category": "ai_consensus", "ai_fen": FULL_FEN}]}),
                encoding="utf-8",
            )
            current_report.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "diagram_id": "d1",
                                "runtime_status": "FEN_MACHINE_ACCEPTED",
                                "selected_value": FULL_FEN,
                                "requires_review": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = build_ai_consensus_fen_promotion_queue(ai_report, current_report)

        self.assertEqual(payload["summary"]["queue_count"], 0)
        self.assertEqual(payload["summary"]["skipped"]["already_strict_accepted"], 1)

    def test_known_ninety_ai_consensus_records_are_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_report = root / "ai.json"
            current_report = root / "current.json"
            records = [
                {
                    "diagram_id": f"d{index:03d}",
                    "page": index,
                    "filename": f"d{index:03d}.png",
                    "ai_category": "ai_consensus",
                    "ai_fen": FULL_FEN,
                }
                for index in range(90)
            ]
            records.append({"diagram_id": "best", "ai_category": "ai_best_effort", "ai_fen": FULL_FEN})
            records.append({"diagram_id": "unreadable", "ai_category": "ai_unreadable", "ai_fen": FULL_FEN})
            ai_report.write_text(json.dumps({"records": records}), encoding="utf-8")
            current_report.write_text(json.dumps({"records": []}), encoding="utf-8")

            payload = build_ai_consensus_fen_promotion_queue(ai_report, current_report)

        self.assertEqual(payload["summary"]["queue_count"], 90)
        self.assertEqual(payload["summary"]["skipped"]["ai_best_effort"], 1)
        self.assertEqual(payload["summary"]["skipped"]["ai_unreadable"], 1)
        self.assertTrue(all(record["requires_human_verification"] is True for record in payload["records"]))

    def test_missing_crop_path_is_marked_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_report = root / "ai.json"
            current_report = root / "current.json"
            ai_report.write_text(
                json.dumps({"records": [{"diagram_id": "d1", "ai_category": "ai_consensus", "ai_fen": FULL_FEN}]}),
                encoding="utf-8",
            )
            current_report.write_text(json.dumps({"records": []}), encoding="utf-8")

            payload = build_ai_consensus_fen_promotion_queue(ai_report, current_report)

        self.assertEqual(payload["records"][0]["crop_path"], MISSING_ARTIFACT)
        self.assertEqual(payload["summary"]["missing_artifact_count"], 1)

    def test_writer_outputs_jsonl_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "ai_coverage_path": "ai.json",
                "current_report_path": "current.json",
                "summary": {"queue_count": 1, "requires_human_verification_count": 1, "missing_artifact_count": 0},
                "records": [
                    {
                        "diagram_id": "d1",
                        "page": 1,
                        "crop_path": "crop.png",
                        "current_strict_status": "requires_review",
                        "placement_diff": [],
                        "recommended_action": "verify_ai_consensus_against_crop",
                    }
                ],
            }
            jsonl = root / "queue.jsonl"
            md = root / "queue.md"

            write_jsonl(payload, jsonl)
            write_markdown(payload, md)

            self.assertEqual(len(jsonl.read_text(encoding="utf-8").splitlines()), 1)
            self.assertIn("AI Consensus FEN Promotion Queue", md.read_text(encoding="utf-8"))


class AiConsensusFenVerificationImportTests(unittest.TestCase):
    def test_verified_import_without_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.jsonl"
            review.write_text(
                json.dumps(
                    {
                        "diagram_id": "d1",
                        "page": 1,
                        "fen": FULL_FEN,
                        "human_verified": True,
                        "verification_source": "human_visual",
                        "square_diff_ack": True,
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = import_ai_consensus_fen_verification(review, output_jsonl=root / "labels.jsonl")

        self.assertEqual(payload["accepted_count"], 0)
        self.assertEqual(payload["rejected_count"], 1)
        codes = {issue["code"] for issue in payload["rejected"][0]["issues"]}
        self.assertIn("crop_sha256_missing", codes)

    def test_verified_import_without_human_provenance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.jsonl"
            review.write_text(
                json.dumps(
                    {
                        "diagram_id": "d1",
                        "page": 1,
                        "fen": FULL_FEN,
                        "crop_sha256": HASH,
                        "human_verified": False,
                        "verification_source": "openai",
                        "square_diff_ack": True,
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = import_ai_consensus_fen_verification(review, output_jsonl=root / "labels.jsonl")

        self.assertEqual(payload["accepted_count"], 0)
        codes = {issue["code"] for issue in payload["rejected"][0]["issues"]}
        self.assertIn("ai_only_verification_source", codes)
        self.assertIn("human_verified_missing", codes)

    def test_verified_import_requires_square_diff_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.jsonl"
            review.write_text(
                json.dumps(
                    {
                        "diagram_id": "d1",
                        "page": 1,
                        "fen": FULL_FEN,
                        "crop_sha256": HASH,
                        "human_verified": True,
                        "verification_source": "human_visual",
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = import_ai_consensus_fen_verification(review, output_jsonl=root / "labels.jsonl")

        self.assertEqual(payload["accepted_count"], 0)
        codes = {issue["code"] for issue in payload["rejected"][0]["issues"]}
        self.assertIn("square_diff_ack_missing", codes)

    def test_verified_import_with_required_human_fields_passes_and_can_write_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.jsonl"
            labels = root / "labels.jsonl"
            review.write_text(
                json.dumps(
                    {
                        "diagram_id": "d1",
                        "page": 1,
                        "crop_path": "reference_inputs/chess_fen/crops/d1.png",
                        "fen": FULL_FEN,
                        "crop_sha256": HASH,
                        "human_verified": True,
                        "verification_source": "human_visual",
                        "square_diff_ack": True,
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = import_ai_consensus_fen_verification(review, output_jsonl=labels, apply_changes=True)

            self.assertTrue(labels.exists())
            written = json.loads(labels.read_text(encoding="utf-8").strip())

        self.assertEqual(payload["accepted_count"], 1)
        self.assertEqual(payload["rejected_count"], 0)
        self.assertEqual(written["fen"], FULL_FEN)
        self.assertEqual(written["crop_sha256"], HASH)
        self.assertTrue(written["human_verified"])
        self.assertEqual(written["verification_source"], "human_visual")
        self.assertEqual(written["label_status"], "verified")


if __name__ == "__main__":
    unittest.main()
