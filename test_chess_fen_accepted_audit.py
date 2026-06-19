from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


class ChessFenAcceptedAuditReadinessTests(unittest.TestCase):
    def test_ready_check_fails_when_required_audit_summary_missing(self) -> None:
        result = self._check_with_audit(Path("missing_accepted_audit_summary.json"))

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertFalse(result["release_ready"])
        self.assertIn("accepted_audit_summary_missing", {issue["code"] for issue in result["issues"]})

    def test_ready_check_fails_when_critical_risk_count_exceeds_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit = _write_audit(root / "accepted_audit_summary.json", critical_risk_count=1)

            result = self._check_with_audit(audit, root=root)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertIn("accepted_audit_critical_risk_count_exceeded", {issue["code"] for issue in result["issues"]})

    def test_ready_check_fails_when_high_risk_count_exceeds_default_unless_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit = _write_audit(root / "accepted_audit_summary.json", high_risk_count=1)

            blocked = self._check_with_audit(audit, root=root)
            allowed = self._check_with_audit(audit, root=root, max_high_risk_count=1)

        self.assertEqual(blocked["status"], "failed")
        self.assertIn("accepted_audit_high_risk_count_exceeded", {issue["code"] for issue in blocked["issues"]})
        self.assertEqual(allowed["status"], "ready")
        self.assertTrue(allowed["accepted_for_corpus"])
        self.assertTrue(allowed["release_ready"])

    def test_ready_check_passes_with_ok_audit_and_zero_high_or_critical_risks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit = _write_audit(root / "accepted_audit_summary.json")

            result = self._check_with_audit(audit, root=root)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["accepted_audit_summary"]["status"], "ok")
        self.assertEqual(result["accepted_audit_summary"]["critical_risk_count"], 0)
        self.assertEqual(result["accepted_audit_summary"]["high_risk_count"], 0)
        self.assertTrue(result["accepted_for_corpus"])
        self.assertTrue(result["release_ready"])

    def _check_with_audit(
        self,
        audit_path: Path,
        *,
        root: Path | None = None,
        max_high_risk_count: int = 0,
    ) -> dict[str, object]:
        from scripts.check_chess_fen_profile_ready import check_chess_fen_profile_ready

        owned_temp = None
        if root is None:
            owned_temp = tempfile.TemporaryDirectory()
            root = Path(owned_temp.name)
        try:
            manifest_case, _ = _write_ready_inputs(root)
            holdout = _write_holdout(root / "holdout.json")
            with mock.patch(
                "scripts.check_chess_fen_profile_ready.evaluate_chess_fen_recognizer",
                return_value={
                    "status": "passed",
                    "case_count": 1,
                    "fen_count": 1,
                    "exact_fen_count": 1,
                    "exact_fen_accuracy": 1.0,
                    "false_positive_count": 0,
                    "false_positive_rate": 0.0,
                    "square_accuracy": 1.0,
                },
            ):
                return check_chess_fen_profile_ready(
                    manifest_case,
                    template_dir=root / "templates",
                    min_seed_labels=1,
                    build_templates=False,
                    holdout_eval_path=holdout,
                    accepted_audit_summary_path=audit_path,
                    max_high_risk_count=max_high_risk_count,
                )
        finally:
            if owned_temp is not None:
                owned_temp.cleanup()


def _write_ready_inputs(root: Path) -> tuple[Path, Path]:
    source_pdf = root / "profile.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    crop = root / "board.png"
    Image.new("L", (32, 32), 255).save(crop)
    crop_sha256 = hashlib.sha256(crop.read_bytes()).hexdigest()
    labels = root / "labels.jsonl"
    labels.write_text(
        json.dumps(
            {
                "id": "ready_001",
                "crop_path": str(crop),
                "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                "crop_sha256": crop_sha256,
                "sha256": crop_sha256,
                "human_verified": True,
                "square_diff_ack": True,
                "verification_source": "human_visual",
                "verified_by": "unit-test",
                "verified_at": "2026-06-18",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = root / "manifest_case.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "profile",
                "document_class": "diagram_training_book",
                "input_type": "pdf",
                "source": str(source_pdf),
                "target": str(source_pdf),
                "chess_fen_seed_labels": str(labels),
                "chess_fen_template_profile": "profile",
            }
        ),
        encoding="utf-8",
    )
    return manifest, labels


def _write_holdout(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "status": "passed",
                "fold_count": 5,
                "holdout_fold": 0,
                "holdout_eval": {
                    "status": "passed",
                    "case_count": 1,
                    "fen_count": 1,
                    "exact_fen_count": 1,
                    "exact_fen_accuracy": 1.0,
                    "false_positive_count": 0,
                    "false_positive_rate": 0.0,
                    "square_accuracy": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_audit(
    path: Path,
    *,
    status: str = "ok",
    critical_risk_count: int = 0,
    high_risk_count: int = 0,
) -> Path:
    path.write_text(
        json.dumps(
            {
                "status": status,
                "accepted_count": 1,
                "audited_count": 1,
                "critical_risk_count": critical_risk_count,
                "high_risk_count": high_risk_count,
            }
        ),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
