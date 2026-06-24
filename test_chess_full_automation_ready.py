from __future__ import annotations

import json
import os
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

    def test_missing_fen_audit_cases_fails_release_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "fen": {
                        "case_count": 0,
                        "diagram_detected_count": 0,
                        "placement_exact_count": 0,
                        "runtime_fen_present_count": 0,
                        "top_blockers": {},
                    }
                },
            )

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("fen_audit_cases_evaluated", _failed_ids(result))
        self.assertIn("fen_audit_top_blockers_known", _failed_ids(result))

    def test_missing_pgn_cases_fails_release_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "pgn": {
                        "case_count": 0,
                        "feasible_count": 0,
                        "infeasible_count": 0,
                        "exportable_count": 0,
                    }
                },
            )

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("pgn_cases_evaluated", _failed_ids(result))

    def test_dataset_release_readiness_false_fails_release_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "dataset_release_readiness": {
                        "accepted_for_release_proof": False,
                        "status": "review_required",
                        "blockers": [
                            {
                                "code": "pgn_ground_truth_missing",
                                "message": "Release proof requires at least one human-reviewed PGN feasibility case.",
                            }
                        ],
                    }
                },
            )

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("audit_dataset_release_ready", _failed_ids(result))
        check = next(check for check in result["checks"] if check["id"] == "audit_dataset_release_ready")
        self.assertEqual(check["dataset_release_status"], "review_required")
        self.assertIn("pgn_ground_truth_missing", check["dataset_release_blocker_codes"])
        self.assertFalse(result["metrics"]["audit_dataset_accepted_for_release_proof"])
        self.assertIn("pgn_ground_truth_missing", result["metrics"]["audit_dataset_release_blockers"])

    def test_missing_negative_samples_fails_release_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "negative": {
                        "case_count": 0,
                        "evaluable_count": 0,
                        "false_positive_candidate_count": 0,
                        "false_positive_runtime_count": 0,
                    }
                },
            )

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("negative_samples_evaluated", _failed_ids(result))

    def test_missing_pgn_and_negative_rows_report_intake_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "pgn": {
                        "case_count": 0,
                        "feasible_count": 0,
                        "infeasible_count": 0,
                        "exportable_count": 0,
                    },
                    "negative": {
                        "case_count": 0,
                        "evaluable_count": 0,
                        "false_positive_candidate_count": 0,
                        "false_positive_runtime_count": 0,
                    },
                },
            )
            paths["pgn_intake_summary_path"] = _write_json(
                root / "pgn_intake_summary.json",
                {
                    "row_count": 12,
                    "candidate_counts": {
                        "rows": 12,
                        "feasible_suggested": 12,
                        "with_candidate_movetext": 12,
                    },
                    "template": "reports/chess_fen/pgn_ground_truth_intake/audit_2026_06/pgn_ground_truth_template.jsonl",
                    "candidate_review": "reports/chess_fen/pgn_ground_truth_intake/audit_2026_06/candidate_pgn_ground_truth_review.jsonl",
                    "target_pgn_ground_truth": "reference_inputs/chess_fen/audit_2026_06/labels/pgn_ground_truth.jsonl",
                },
            )
            paths["negative_intake_summary_path"] = _write_json(
                root / "negative_intake_summary.json",
                {
                    "row_count": 8,
                    "candidate_counts": {
                        "rows": 8,
                        "with_candidate_crop_path": 8,
                        "with_canonical_crop_path": 0,
                    },
                    "template": "reports/chess_fen/negative_sample_intake/audit_2026_06/negative_samples_template.jsonl",
                    "candidate_review": "reports/chess_fen/negative_sample_intake/audit_2026_06/candidate_negative_samples_review.jsonl",
                    "target_negative_samples": "reference_inputs/chess_fen/audit_2026_06/labels/negative_samples.jsonl",
                },
            )

            result = _run_checker(root, paths)
            markdown = (root / "ready.md").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "failed")
        actions = "\n".join(result["next_required_actions"])
        self.assertIn("pgn_ground_truth_template.jsonl", actions)
        self.assertIn("12 review candidate(s), 12 suggested feasible", actions)
        self.assertIn("candidate_pgn_ground_truth_review.jsonl", actions)
        self.assertIn("labels/pgn_ground_truth.jsonl", actions)
        self.assertIn("negative_samples_template.jsonl", actions)
        self.assertIn("8 review candidate(s), 8 candidate crop(s), 0 canonical crop(s)", actions)
        self.assertIn("candidate_negative_samples_review.jsonl", actions)
        self.assertIn("labels/negative_samples.jsonl", actions)
        self.assertIn("apply_chess_audit_dataset_intake.py", actions)
        self.assertIn("--apply", actions)
        self.assertNotIn("append human-verified rows", actions)
        self.assertEqual(result["metrics"]["pgn_intake_candidate_count"], 12)
        self.assertEqual(result["metrics"]["pgn_intake_feasible_suggested_count"], 12)
        self.assertEqual(result["metrics"]["negative_intake_candidate_count"], 8)
        self.assertEqual(result["metrics"]["negative_intake_candidate_crop_count"], 8)
        self.assertEqual(result["metrics"]["negative_intake_canonical_crop_count"], 0)
        self.assertIn("pgn_ground_truth_template.jsonl", markdown)
        self.assertIn("negative_samples_template.jsonl", markdown)
        self.assertIn("apply_chess_audit_dataset_intake.py", markdown)
        self.assertIn("PGN intake candidates waiting for review: `12`", markdown)
        self.assertIn("negative intake candidates waiting for review: `8`", markdown)

    def test_stale_fen_corpus_evidence_conflicts_with_profile_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root)
            stale_profile = {
                "status": "failed",
                "accepted_for_corpus": False,
                "label_validation": {"valid_label_count": 0, "status": "failed"},
                "profile_readiness_breakdown": {"valid_label_count": 0},
            }
            Path(paths["profile_readiness_paths"][0]).write_text(json.dumps(stale_profile), encoding="utf-8")

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("fen_profile_evidence_consistent", _failed_ids(result))
        check = next(check for check in result["checks"] if check["id"] == "fen_profile_evidence_consistent")
        self.assertIn(20, check["fen_corpus_valid_label_counts"])
        self.assertIn(0, check["profile_readiness_valid_label_counts"])
        self.assertIn("stale corpus proof", "\n".join(result["next_required_actions"]))

    def test_negative_runtime_false_positive_fails_release_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "negative": {
                        "case_count": 1,
                        "evaluable_count": 1,
                        "false_positive_candidate_count": 1,
                        "false_positive_runtime_count": 1,
                    }
                },
            )

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("negative_runtime_false_positive_gate", _failed_ids(result))

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

    def test_markdown_uses_execution_pack_readiness_report_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                profile_count=1,
                pgn_overrides={
                    "fen": {
                        "case_count": 5,
                        "diagram_detected_count": 3,
                        "crop_present_count": 5,
                        "crop_correct_evidence_count": 4,
                        "crop_correct_known_count": 4,
                        "grid_measured_count": 4,
                        "grid_correct_known_count": 2,
                        "grid_confidence_average": 0.72,
                        "placement_exact_count": 2,
                        "full_fen_syntax_valid_count": 1,
                        "runtime_fen_present_count": 1,
                        "runtime_accepted_count": 1,
                    },
                    "negative": {
                        "case_count": 2,
                        "evaluable_count": 1,
                        "false_positive_candidate_count": 1,
                        "false_positive_runtime_count": 1,
                        "top_blockers": {"negative_runtime_false_positive": 1},
                    },
                    "pgn": {
                        "case_count": 4,
                        "feasible_count": 3,
                        "infeasible_count": 1,
                        "ocr_text_present_count": 3,
                        "candidate_blocks_found_count": 2,
                        "san_tokens_present_count": 2,
                        "san_token_count": 14,
                        "parse_clean_count": 2,
                        "replay_legal_count": 1,
                        "final_fen_present_count": 1,
                        "exportable_count": 2,
                    }
                },
            )

            _run_checker(root, paths)
            markdown = (root / "ready.md").read_text(encoding="utf-8")

        self.assertIn("# FEN/PGN Automatic Readiness Report", markdown)
        self.assertIn("## Executive summary", markdown)
        self.assertIn("- FEN placement automatic:", markdown)
        self.assertIn("- PGN automatic for feasible cases:", markdown)
        self.assertIn("## FEN funnel", markdown)
        self.assertIn("## PGN funnel", markdown)
        self.assertIn("- OCR text present: `3`", markdown)
        self.assertIn("- candidate blocks: `2`", markdown)
        self.assertIn("- SAN tokens: `14` total (`2` cases)", markdown)
        self.assertIn("- parse clean: `2`", markdown)
        self.assertIn("- replay legal: `1`", markdown)
        self.assertIn("- final FEN: `1`", markdown)
        self.assertIn("## Decision", markdown)
        self.assertIn("- decision: `no merge`", markdown)
        self.assertIn("- FEN cases: `5`", markdown)
        self.assertIn("- diagram detected: `3`", markdown)
        self.assertIn("- crop present: `5`", markdown)
        self.assertIn("- crop correctness evidence: `4`", markdown)
        self.assertIn("- crop correct verified: `4`", markdown)
        self.assertIn("- grid correct: `2` verified (`4` measured, avg confidence `0.72`)", markdown)
        self.assertIn("- placement exact: `2`", markdown)
        self.assertIn("- full FEN valid: `1` syntax-valid", markdown)
        self.assertIn("- runtime FEN present: `1`", markdown)
        self.assertIn("- runtime accepted: `1`", markdown)
        self.assertIn("- negative samples: `2`", markdown)
        self.assertIn("- negative blockers: `{'negative_runtime_false_positive': 1}`", markdown)
        self.assertIn("`audit_dataset_release_status`: `ready`", markdown)
        self.assertIn("`audit_dataset_accepted_for_release_proof`: `True`", markdown)
        self.assertIn("Mamy automatycznie rozpoznany", markdown)

    def test_ai_only_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root, fen_corpus_overrides={"ai_suggested_fen_promoted": True})

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("no_ai_or_arbiter_authority_path", _failed_ids(result))

    def test_accepted_inferred_side_to_move_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                fen_corpus_overrides={
                    "cases": [
                        {"id": "profile_0", "label_validation": {"valid_label_count": 20}},
                        {"id": "profile_1", "label_validation": {"valid_label_count": 20}},
                    ],
                    "accepted_records": [
                        {
                            "runtime_status": "FEN_MACHINE_ACCEPTED",
                            "side_to_move_status": "inferred",
                            "warnings": ["side_to_move_inferred"],
                        }
                    ],
                },
            )

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "failed")
        self.assertIn("side_to_move_inferred_not_full_fen_accepted", _failed_ids(result))

    def test_auto_flow_pgn_validation_schema_counts_as_strict_export_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(
                root,
                pgn_overrides={
                    "schema": "kindlemaster.auto_chess.pgn_validation.v1",
                    "strict_export_replay_accepted_only": False,
                    "summary": {
                        "runtime_machine_accepted": 2,
                        "failed": 0,
                        "pgn_feasible_count": 2,
                        "top_blocker_counts": {},
                    },
                    "pgn": {
                        "case_count": 2,
                        "feasible_count": 2,
                        "infeasible_count": 0,
                        "exportable_count": 2,
                    },
                },
            )
            # The schema itself is the strict replay proof; no manual override required.
            pgn_payload = json.loads(Path(paths["pgn_eval_path"]).read_text(encoding="utf-8"))
            pgn_payload.pop("strict_export_replay_accepted_only", None)
            pgn_payload.pop("valid_pgn_count", None)
            pgn_payload.pop("exported_pgn_count", None)
            Path(paths["pgn_eval_path"]).write_text(json.dumps(pgn_payload), encoding="utf-8")

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["metrics"]["pgn_feasible_count"], 2)
        self.assertEqual(result["metrics"]["pgn_valid_count"], 2)

    def test_auto_strict_overall_status_passed_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_evidence_pack(root)
            auto_payload = json.loads(Path(paths["auto_strict_validation_path"]).read_text(encoding="utf-8"))
            auto_payload.pop("status", None)
            auto_payload["overall_status"] = "passed"
            Path(paths["auto_strict_validation_path"]).write_text(json.dumps(auto_payload), encoding="utf-8")

            result = _run_checker(root, paths)

        self.assertEqual(result["status"], "passed")
        self.assertNotIn("auto_strict_validation_passed", _failed_ids(result))

    def test_default_evidence_paths_use_standard_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            paths = _write_evidence_pack(source)
            _copy_json(Path(paths["corpus_gate_path"]), root / "reports/corpus/corpus_gate.json")
            _copy_json(Path(paths["fen_corpus_path"]), root / "reports/corpus/fen_corpus_90.json")
            for index, profile_path in enumerate(paths["profile_readiness_paths"]):
                _copy_json(Path(profile_path), root / f"reports/chess_fen/evals/profile_{index}_profile_ready.json")
            for index, holdout_path in enumerate(paths["holdout_eval_paths"]):
                _copy_json(Path(holdout_path), root / f"reports/chess_fen/evals/profile_{index}_holdout_latest.json")
            _copy_json(Path(paths["accepted_audit_summary_paths"][0]), root / "reports/chess_fen/fundamenty_latest_accepted_audit_summary.json")
            _copy_json(Path(paths["pgn_eval_path"]), root / "reports/chess_audit/latest/audit_summary.json")
            _copy_json(Path(paths["reading_order_audit_path"]), root / "reports/html_reading_order_report.json")
            _copy_json(Path(paths["auto_strict_validation_path"]), root / "reports/auto_strict_validation.json")
            _copy_json(Path(paths["python_chess_status_path"]), root / "reports/python_chess_status.json")
            _copy_json(Path(paths["epub_validation_path"]), root / "reports/epub_validation.json")

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                result = check_chess_full_automation_ready(
                    output_json=root / "ready.json",
                    output_md=root / "ready.md",
                )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result["status"], "passed")
        evidence = result["evidence"]
        self.assertEqual(evidence["corpus_gate"]["path"], "reports\\corpus\\corpus_gate.json")
        self.assertEqual(evidence["fen_corpus"]["path"], "reports\\corpus\\fen_corpus_90.json")
        self.assertEqual(evidence["pgn_eval"]["path"], "reports\\chess_audit\\latest\\audit_summary.json")
        self.assertEqual(evidence["python_chess"]["path"], "reports\\python_chess_status.json")


def _run_checker(root: Path, paths: dict[str, object]) -> dict[str, object]:
    return check_chess_full_automation_ready(
        corpus_gate_path=paths["corpus_gate_path"],
        fen_corpus_path=paths["fen_corpus_path"],
        profile_readiness_paths=paths["profile_readiness_paths"],
        holdout_eval_paths=paths["holdout_eval_paths"],
        accepted_audit_summary_paths=paths["accepted_audit_summary_paths"],
        pgn_eval_path=paths["pgn_eval_path"],
        pgn_intake_summary_path=paths.get("pgn_intake_summary_path"),
        negative_intake_summary_path=paths.get("negative_intake_summary_path"),
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
        "dataset_release_readiness": {
            "accepted_for_release_proof": True,
            "status": "ready",
            "blockers": [],
        },
        "fen": {
            "case_count": 20,
            "diagram_detected_count": 18,
            "crop_present_count": 20,
            "crop_correct_evidence_count": 20,
            "crop_correct_known_count": 20,
            "grid_measured_count": 20,
            "grid_correct_known_count": 20,
            "grid_confidence_average": 0.9,
            "placement_exact_count": 20,
            "full_fen_syntax_valid_count": 20,
            "full_fen_legal_valid_count": 20,
            "runtime_fen_present_count": 20,
            "runtime_accepted_count": 20,
            "top_blockers": {"accepted": 20},
        },
        "pgn": {
            "case_count": 3,
            "feasible_count": 3,
            "infeasible_count": 0,
            "ocr_text_present_count": 3,
            "candidate_blocks_found_count": 3,
            "san_tokens_present_count": 3,
            "san_token_count": 18,
            "parse_clean_count": 3,
            "replay_legal_count": 3,
            "final_fen_present_count": 3,
            "exportable_count": 3,
            "top_blockers": {},
        },
        "negative": {
            "case_count": 1,
            "evaluable_count": 1,
            "false_positive_candidate_count": 0,
            "false_positive_runtime_count": 0,
            "top_blockers": {"negative_correctly_rejected": 1},
        },
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _copy_json(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _failed_ids(result: dict[str, object]) -> set[str]:
    return {str(check["id"]) for check in result["checks"] if isinstance(check, dict) and check.get("status") == "failed"}


if __name__ == "__main__":
    unittest.main()
