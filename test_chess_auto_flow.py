from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ChessAutoFlowTests(unittest.TestCase):
    def test_check_python_chess_available_reports_missing_chess(self) -> None:
        from chess_auto_flow import check_python_chess_available

        def fake_import(name: str):
            if name == "chess":
                raise ImportError("missing chess")
            raise AssertionError(f"unexpected import: {name}")

        with patch("chess_auto_flow.importlib.import_module", side_effect=fake_import):
            payload = check_python_chess_available()

        self.assertFalse(payload["available"])
        self.assertEqual(payload["error_code"], "python_chess_missing")
        self.assertIn("python_chess_unavailable", payload["blockers"])
        self.assertTrue(payload["manual_review_required"])

    def test_auto_flow_does_not_apply_fen_when_side_to_move_is_only_inferred(self) -> None:
        from chess_position_recognizer import ChessFenResult
        from pymupdf_chess_extractor import _attach_fen_to_chess_image

        result = ChessFenResult(
            fen="8/8/8/3k4/8/8/4K3/8 w - - 0 1",
            placement="8/8/8/3k4/8/8/4K3/8",
            full_fen="8/8/8/3k4/8/8/4K3/8 w - - 0 1",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
            side_to_move_status="inferred",
            side_to_move_evidence="inferred",
        )
        chess_img: dict = {}

        payload = _attach_fen_to_chess_image(chess_img, result)

        self.assertEqual(payload["fen"], "")
        self.assertEqual(payload["full_fen"], "8/8/8/3k4/8/8/4K3/8 w - - 0 1")
        self.assertTrue(payload["requires_review"])
        self.assertNotIn("fen", chess_img)

    def test_strict_validation_fails_on_high_severity_reading_order_warning(self) -> None:
        from chess_auto_flow import validate_auto_chess_output
        from chess_reading_order_audit import audit_chess_reading_order

        report = audit_chess_reading_order(
            pages=[
                {
                    "page": 1,
                    "elements": [
                        {
                            "id": "pgn-review",
                            "type": "pgn",
                            "source_order": 1,
                            "text": "1. Qh5",
                            "status": "requires_review",
                        },
                    ],
                }
            ]
        )

        strict_payload = validate_auto_chess_output({"reading_order_report": report.to_dict()}, strict=True)
        non_strict_payload = validate_auto_chess_output({"reading_order_report": report.to_dict()}, strict=False)

        self.assertEqual(strict_payload["status"], "failed")
        self.assertIn("high_severity_reading_order_warnings", strict_payload["blockers"])
        self.assertNotEqual(non_strict_payload["status"], "failed")
        self.assertGreater(non_strict_payload["high_severity_reading_order_warning_count"], 0)

    def test_auto_flow_writes_accepted_fen_audit_artifacts(self) -> None:
        from chess_auto_flow import build_auto_chess_flow_artifacts

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "board.png"
            crop.write_bytes(b"crop")
            output = build_auto_chess_flow_artifacts(
                {
                    "quality_report": {
                        "chess_fen": {
                            "records": [
                                {
                                    "id": "fen-1",
                                    "page": 1,
                                    "filename": "board.png",
                                    "crop_path": str(crop),
                                    "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                                    "requires_review": False,
                                    "confidence": 0.97,
                                    "warnings": [],
                                }
                            ]
                        }
                    }
                },
                output_dir=root / "report",
            )

            artifact_paths = {artifact["key"]: Path(artifact["path"]) for artifact in output["artifacts"]}
            self.assertEqual(output["accepted_fen_audit_summary"]["accepted_count"], 1)
            self.assertIn("fen_accepted_audit_queue_json", artifact_paths)
            self.assertIn("fen_accepted_audit_queue_jsonl", artifact_paths)
            self.assertIn("fen_accepted_audit_summary_json", artifact_paths)
            self.assertIn("fen_accepted_audit_review_html", artifact_paths)
            self.assertTrue(all(path.exists() for path in artifact_paths.values()))
            self.assertEqual(
                json.loads(artifact_paths["fen_accepted_audit_summary_json"].read_text(encoding="utf-8"))["exported_count"],
                1,
            )

    def test_strict_validation_fails_on_accepted_fen_audit_high_or_critical_risk(self) -> None:
        from chess_auto_flow import validate_auto_chess_output

        payload = {
            "reading_order_report": {"status": "ok", "warnings": []},
            "accepted_fen_audit_summary": {
                "status": "ok",
                "critical_risk_count": 1,
                "high_risk_count": 0,
            },
        }

        strict_payload = validate_auto_chess_output(payload, strict=True)
        non_strict_payload = validate_auto_chess_output(payload, strict=False)

        self.assertEqual(strict_payload["status"], "failed")
        self.assertIn("accepted_fen_audit_unresolved_high_or_critical_risks", strict_payload["blockers"])
        self.assertEqual(non_strict_payload["status"], "passed_with_warnings")
        self.assertEqual(non_strict_payload["accepted_fen_audit_artifact_path"], "")

    def test_auto_flow_accepted_audit_reports_missing_crop_without_crashing(self) -> None:
        from chess_auto_flow import build_auto_chess_flow_artifacts, validate_auto_chess_output

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = build_auto_chess_flow_artifacts(
                {
                    "quality_report": {
                        "chess_fen": {
                            "records": [
                                {
                                    "id": "fen-missing-crop",
                                    "page": 1,
                                    "crop_path": str(root / "missing.png"),
                                    "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                                    "requires_review": False,
                                    "confidence": 0.98,
                                    "warnings": [],
                                }
                            ]
                        }
                    }
                },
                output_dir=root / "report",
            )
            queue_path = next(Path(artifact["path"]) for artifact in output["artifacts"] if artifact["key"] == "fen_accepted_audit_queue_json")
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            validation = validate_auto_chess_output(output, strict=False)

        self.assertEqual(output["accepted_fen_audit_summary"]["high_risk_count"], 1)
        self.assertEqual(queue[0]["risk_level"], "high")
        self.assertIn("accepted_audit_crop_missing", queue[0]["warnings"])
        self.assertEqual(validation["status"], "passed_with_warnings")
        self.assertIn("fen_accepted_audit", validation["accepted_fen_audit_artifact_path"])

    def test_auto_flow_writes_not_applicable_audit_when_no_accepted_fen_records_exist(self) -> None:
        from chess_auto_flow import build_auto_chess_flow_artifacts

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = build_auto_chess_flow_artifacts(
                {"quality_report": {"chess_fen": {"records": []}}},
                output_dir=root / "report",
            )
            summary_path = next(
                Path(artifact["path"])
                for artifact in output["artifacts"]
                if artifact["key"] == "fen_accepted_audit_summary_json"
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(output["accepted_fen_audit_summary"]["status"], "not_applicable")
        self.assertEqual(summary["exported_count"], 0)
        self.assertEqual(summary["reason"], "no_accepted_fen_records")

    def test_auto_flow_accepted_audit_is_report_only_and_does_not_mutate_book_json(self) -> None:
        from chess_auto_flow import build_auto_chess_flow_artifacts

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            book_path = root / "book.json"
            book_payload = {
                "quality_report": {
                    "chess_fen": {
                        "records": [
                            {
                                "id": "fen-1",
                                "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                                "requires_review": False,
                                "confidence": 0.99,
                            }
                        ]
                    }
                }
            }
            original_text = json.dumps(book_payload, sort_keys=True)
            book_path.write_text(original_text, encoding="utf-8")

            build_auto_chess_flow_artifacts(book_payload, output_dir=root / "report")

            self.assertEqual(book_path.read_text(encoding="utf-8"), original_text)
            self.assertEqual(book_payload["quality_report"]["chess_fen"]["records"][0]["fen"], "8/8/8/3k4/8/8/4K3/8 w - - 0 1")


if __name__ == "__main__":
    unittest.main()
