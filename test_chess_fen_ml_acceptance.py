from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

VALID_KINGS_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _squares_from_fen(fen: str, *, confidence: float = 0.99) -> list[dict]:
    from chess_fen_hardening import fen_to_cells

    cells = fen_to_cells(fen)
    rows: list[dict] = []
    for index, piece in enumerate(cells):
        square = f"{'abcdefgh'[index % 8]}{'87654321'[index // 8]}"
        label = piece or "empty"
        rows.append(
            {
                "square": square,
                "class": label,
                "piece": piece,
                "confidence": confidence,
                "alternatives": [
                    {"class": label, "piece": piece, "confidence": confidence, "source": "model_centroid"},
                    {"class": "empty", "piece": "", "confidence": 0.01, "source": "model_centroid"},
                    {"class": "P", "piece": "P", "confidence": 0.005, "source": "model_centroid"},
                ],
            }
        )
    return rows


class ChessFenMlAcceptanceTests(unittest.TestCase):
    def test_build_deterministic_ensemble_candidate_requires_square_alternatives(self) -> None:
        from chess_fen_ml_acceptance import build_deterministic_ensemble_candidates

        prediction = {
            "diagram_id": "p001_d001",
            "fen_candidate": VALID_KINGS_FEN,
            "global_confidence": 0.99,
            "source_crop_hash": "sha256:crop",
            "squares": _squares_from_fen(VALID_KINGS_FEN),
        }

        rows = build_deterministic_ensemble_candidates(
            diagrams=[
                {
                    "diagram_id": "p001_d001",
                    "page": 1,
                    "source_crop_hash": "sha256:crop",
                    "side_to_move": "w",
                    "side_to_move_status": "explicit",
                    "side_to_move_evidence": "marker",
                    "side_marker_symbol": "\u25b3",
                    "side_marker_status": "trusted_marker",
                    "side_marker_source": "marker",
                    "side_marker_confidence": 0.94,
                    "marker_crop_quality": "pass",
                    "marker_bbox": [10.0, 20.0, 30.0, 40.0],
                    "selected_marker_zone": "right",
                    "marker_crop_quality_gate": {"decision": "pass", "component_count": 1, "reasons": []},
                }
            ],
            model_predictions={"p001_d001": prediction},
            template_candidates={},
            min_confidence=0.90,
            min_score_margin=0.05,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "deterministic_ensemble")
        self.assertEqual(row["fen"], VALID_KINGS_FEN)
        self.assertTrue(row["evidence"]["square_alternatives_checked"])
        self.assertTrue(row["evidence"]["local_model_candidate"])
        self.assertEqual(row["machine_acceptance"]["runtime_status"], "FEN_MACHINE_ACCEPTED")

    def test_build_deterministic_ensemble_blocks_missing_alternatives_and_crop_hash(self) -> None:
        from chess_fen_ml_acceptance import build_deterministic_ensemble_candidates

        prediction = {
            "diagram_id": "p001_d001",
            "fen_candidate": VALID_KINGS_FEN,
            "global_confidence": 0.99,
            "squares": [
                {key: value for key, value in square.items() if key != "alternatives"}
                for square in _squares_from_fen(VALID_KINGS_FEN)
            ],
        }

        rows = build_deterministic_ensemble_candidates(
            diagrams=[{"diagram_id": "p001_d001", "page": 1}],
            model_predictions={"p001_d001": prediction},
            template_candidates={},
            min_confidence=0.90,
            min_score_margin=0.05,
        )

        blockers = {blocker["code"] for blocker in rows[0]["machine_acceptance"]["acceptance_blockers"]}
        self.assertEqual(rows[0]["machine_acceptance"]["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertIn("source_crop_hash_missing", blockers)
        self.assertIn("square_alternatives_not_checked", blockers)

    def test_apply_runtime_accepted_fen_updates_book_and_diagrams_only_for_machine_accepted(self) -> None:
        from chess_fen_ml_acceptance import apply_runtime_accepted_fen

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [
                        {
                            "page": 1,
                            "diagrams": [{"diagram_id": "p001_d001", "validation_status": "needs_review"}],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )
            _write_json(out / "data" / "diagrams.json", {"diagrams": [{"diagram_id": "p001_d001"}]})
            _write_json(
                out / "fen" / "fen_candidates.json",
                {
                    "items": [
                        {
                            "id": "p001_d001",
                            "runtime_status": "FEN_MACHINE_ACCEPTED",
                            "status": "FEN_MACHINE_ACCEPTED",
                            "selected_value": VALID_KINGS_FEN,
                            "acceptance_trace": {"source": "deterministic_ensemble"},
                        }
                    ]
                },
            )

            result = apply_runtime_accepted_fen(out)

            self.assertEqual(result["applied_count"], 1)
            book = json.loads((out / "data" / "book.json").read_text(encoding="utf-8"))
            diagrams = json.loads((out / "data" / "diagrams.json").read_text(encoding="utf-8"))
            self.assertEqual(book["pages"][0]["diagrams"][0]["fen"], VALID_KINGS_FEN)
            self.assertEqual(book["pages"][0]["diagrams"][0]["runtime_status"], "FEN_MACHINE_ACCEPTED")
            self.assertEqual(diagrams["diagrams"][0]["validation_status"], "accepted")

    def test_verified_labels_are_not_loaded_as_runtime_template_candidates(self) -> None:
        from chess_fen_ml_acceptance import load_template_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_jsonl(
                out / "review" / "fen_verified_labels.jsonl",
                [{"diagram_id": "p001_d001", "fen": VALID_KINGS_FEN, "label_status": "verified"}],
            )

            self.assertEqual(load_template_candidates(out), {})

    def test_runtime_template_candidates_keep_undertrained_profile_review_only(self) -> None:
        from chess_fen_ml_acceptance import build_runtime_template_candidates, load_template_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            crop_path = out / "assets" / "diagrams" / "p001_d001.png"
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            crop_path.write_bytes(b"template-crop")
            _write_json(
                out / "data" / "diagrams.json",
                {
                    "diagrams": [
                        {
                            "diagram_id": "p001_d001",
                            "page": 1,
                            "image_path": "assets/diagrams/p001_d001.png",
                            "caption": "Diagram 1-1",
                        }
                    ]
                },
            )
            _write_json(out / "reports" / "fen_template_build.json", {"promoted_label_count": 14})

            def fake_recognizer(_crop_path: Path, _diagram: dict) -> dict:
                return {
                    "fen": VALID_KINGS_FEN,
                    "confidence": 0.99,
                    "warnings": [],
                    "requires_review": False,
                    "squares": _squares_from_fen(VALID_KINGS_FEN, confidence=0.99),
                }

            summary = build_runtime_template_candidates(
                out,
                recognizer=fake_recognizer,
                min_verified_labels=50,
            )

            self.assertEqual(summary["template_candidate_count"], 1)
            self.assertFalse(summary["profile_ready"])
            rows = load_template_candidates(out)
            row = rows["p001_d001"]
            self.assertIn("template_profile_not_ready", row["warnings"])
            self.assertEqual(row["profile_status"], "template_profile_not_ready")

    def test_template_profile_not_ready_blocks_deterministic_ensemble_acceptance(self) -> None:
        from chess_fen_ml_acceptance import build_deterministic_ensemble_candidates

        template_candidate = {
            "diagram_id": "p001_d001",
            "fen": VALID_KINGS_FEN,
            "confidence": 0.99,
            "source_crop_hash": "sha256:template",
            "warnings": ["template_profile_not_ready"],
            "squares": _squares_from_fen(VALID_KINGS_FEN, confidence=0.99),
        }

        rows = build_deterministic_ensemble_candidates(
            diagrams=[{"diagram_id": "p001_d001", "page": 1, "source_crop_hash": "sha256:template"}],
            model_predictions={},
            template_candidates={"p001_d001": template_candidate},
            min_confidence=0.90,
            min_score_margin=0.05,
        )

        row = rows[0]
        self.assertEqual(row["machine_acceptance"]["runtime_status"], "FEN_REVIEW_REQUIRED")
        blockers = {blocker["code"] for blocker in row["machine_acceptance"]["acceptance_blockers"]}
        self.assertIn("template_profile_not_ready", blockers)
        self.assertEqual(row["next_action"], "add_verified_fen_labels")

    def test_square_prediction_alternatives_take_top_three_probabilities(self) -> None:
        from chess_study_export import _square_prediction_alternatives

        alternatives = _square_prediction_alternatives(
            {
                "class": "N",
                "label": "N",
                "confidence": 0.70,
                "probabilities": {"empty": 0.10, "N": 0.70, "B": 0.15, "Q": 0.05},
            },
            top_n=3,
        )

        self.assertEqual([item["class"] for item in alternatives], ["N", "B", "empty"])
        self.assertEqual(alternatives[0]["piece"], "N")
        self.assertEqual(alternatives[0]["source"], "model_centroid")


if __name__ == "__main__":
    unittest.main()
