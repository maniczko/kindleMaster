from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_chess_full_automation_ready import check_chess_full_automation_ready


class ChessFullAutomationReadyTests(unittest.TestCase):
    def test_one_profile_manifest_fails_with_second_profile_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root, profile_count=1)

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("fen_corpus_has_two_real_scanned_profiles", _failed_ids(result))
        self.assertIn("Add 1 second real scanned chess FEN profile", "\n".join(result["next_required_actions"]))

    def test_missing_holdout_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root)
            paths["holdout_eval_paths"] = []

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("holdout_evals_passed", _failed_ids(result))

    def test_accepted_audit_critical_risk_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root, accepted_audit_overrides={"critical_risk_count": 1})

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("accepted_fen_audit_zero_high_or_critical", _failed_ids(result))

    def test_pgn_replay_blocker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root, pgn_overrides={"pgn_replay_errors": 1})

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("pgn_strict_export_replay_accepted_only", _failed_ids(result))

    def test_happy_synthetic_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root)

            result = _run_checker(root, paths)
            output_json = root / "ready.json"
            output_md = root / "ready.md"
            self.assertTrue(output_json.exists())
            self.assertTrue(output_md.exists())

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["release_ready"])
        self.assertEqual(result["answer"], "yes")

    def test_ai_only_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root, fen_corpus_overrides={"ai_suggested_fen_promoted": True})

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("no_ai_or_arbiter_authority_path", _failed_ids(result))


def _run_checker(root: Path, paths: dict[str, object]) -> dict[str, object]:
    return check_chess_full_automation_ready(
        corpus_gate_path=paths["corpus_gate_path"],
        fen_corpus_path=paths["fen_corpus_path"],
        profile_readiness_paths=paths["profile_readiness_paths"],
        holdout_eval_paths=paths["holdout_eval_paths"],
        accepted_audit_summary_paths=paths["accepted_audit_summary_paths"],
        pgn_eval_path=paths["pgn_eval_path"],
        reading_order_audit_path=paths["reading_order_audit_path"],
        auto_strict_validation_path=paths["auto_strict_validation_path"],
        python_chess_status_path=paths["python_chess_status_path"],
        epub_validation_path=paths["epub_validation_path"],
        output_json=root / "ready.json",
        output_md=root / "ready.md",
    )


def _write_evidence_pack(
    root: Path,
    *,
    profile_count: int = 2,
    fen_corpus_overrides: dict[str, object] | None = None,
    accepted_audit_overrides: dict[str, object] | None = None,
    pgn_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    corpus_gate = _write_json(root / "corpus_gate.json", {"overall_status": "passed", "proof_profile": "standard"})
    cases = [
        {
            "id": f"profile_{index}",
            "label_validation": {"valid_label_count": 20},
        }
        for index in range(profile_count)
    ]
    fen_corpus_payload = {
        "status": "passed",
        "evaluated_case_count": profile_count,
        "missing_profile_count": max(0, 2 - profile_count),
        "overall_exact_fen_accuracy": 0.95,
        "total_false_positive_count": 0,
        "cases": cases,
    }
    fen_corpus_payload.update(fen_corpus_overrides or {})
    fen_corpus = _write_json(root / "fen_corpus.json", fen_corpus_payload)
    profile_readiness_paths = [
        _write_json(
            root / f"profile_ready_{index}.json",
            {
                "status": "ready",
                "accepted_for_corpus": True,
                "valid_label_count": 20,
                "exact_fen_accuracy": 0.95,
                "false_positive_count": 0,
            },
        )
        for index in range(profile_count)
    ]
    holdout_eval_paths = [
        _write_json(
            root / f"holdout_{index}.json",
            {
                "status": "passed",
                "holdout_eval": {
                    "status": "passed",
                    "exact_fen_accuracy": 0.95,
                    "false_positive_count": 0,
                },
            },
        )
        for index in range(profile_count)
    ]
    audit_payload = {
        "status": "ok",
        "critical_risk_count": 0,
        "high_risk_count": 0,
    }
    audit_payload.update(accepted_audit_overrides or {})
    accepted_audit = _write_json(root / "accepted_audit_summary.json", audit_payload)
    pgn_payload = {
        "status": "passed",
        "valid_pgn_count": 3,
        "exported_pgn_count": 3,
        "pgn_replay_errors": 0,
        "strict_export_replay_accepted_only": True,
    }
    pgn_payload.update(pgn_overrides or {})
    pgn_eval = _write_json(root / "pgn_eval.json", pgn_payload)
    reading_order = _write_json(root / "reading_order.json", {"status": "ok", "warnings": [], "high_severity_warning_count": 0})
    auto_strict = _write_json(root / "auto_strict.json", {"status": "passed", "release_ready": True})
    python_chess = _write_json(root / "python_chess.json", {"status": "available", "available": True})
    epub_validation = _write_json(root / "epub_validation.json", {"status": "passed"})
    return {
        "corpus_gate_path": corpus_gate,
        "fen_corpus_path": fen_corpus,
        "profile_readiness_paths": profile_readiness_paths,
        "holdout_eval_paths": holdout_eval_paths,
        "accepted_audit_summary_paths": [accepted_audit],
        "pgn_eval_path": pgn_eval,
        "reading_order_audit_path": reading_order,
        "auto_strict_validation_path": auto_strict,
        "python_chess_status_path": python_chess,
        "epub_validation_path": epub_validation,
    }


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _failed_ids(result: dict[str, object]) -> set[str]:
    return {str(check["id"]) for check in result["checks"] if isinstance(check, dict) and check.get("status") == "failed"}


if __name__ == "__main__":
    unittest.main()
