from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from chess_fen_conflict_audit import audit_fen_conflicts_files, audit_fen_conflicts_records


SOURCE_SHA = "a" * 64
EMPTY = [""] * 64


def _placement(cells: list[str]) -> str:
    ranks: list[str] = []
    for start in range(0, 64, 8):
        rank = ""
        empty = 0
        for piece in cells[start : start + 8]:
            if not piece:
                empty += 1
            else:
                if empty:
                    rank += str(empty)
                    empty = 0
                rank += piece
        if empty:
            rank += str(empty)
        ranks.append(rank)
    return "/".join(ranks)


def _current(diagram_id: str, model: list[str], template: list[str], *, bbox=None, comparison="conflict"):
    return {
        "id": diagram_id,
        "page_number": 10,
        "bbox": bbox or [10, 10, 90, 90],
        "placement": _placement(template),
        "model_runtime": {"placement": _placement(model), "template_comparison": comparison},
    }


def _label(diagram_id: str, cells: list[str], *, trusted=True, bbox=None):
    return {
        "diagram_id": diagram_id,
        "page": 10,
        "bbox": bbox or [10, 10, 90, 90],
        "source_document_sha256": SOURCE_SHA,
        "label_status": "verified" if trusted else "needs_piece_labels",
        "human_verified": trusted,
        "piece_labels_verified": trusted,
        "square_labels": cells,
    }


class FenConflictAuditTests(unittest.TestCase):
    def test_links_trusted_label_and_adjudicates_model_win(self) -> None:
        gold = EMPTY.copy()
        gold[4] = "k"
        gold[60] = "K"
        template = gold.copy()
        template[4] = "q"

        result = audit_fen_conflicts_records(
            current_rows=[_current("diagram-1", gold, template)],
            label_rows=[_label("diagram-1", gold)],
            source_document_sha256=SOURCE_SHA,
        )

        report = result["report"]
        self.assertEqual(report["counts"]["trusted_linked_boards"], 1)
        self.assertEqual(report["conflict_adjudication"], {"model_correct_template_wrong": 1})
        self.assertEqual(report["king_conflict_adjudication"], {"model_correct_template_wrong": 1})
        self.assertEqual(report["evaluation"]["model"]["square_accuracy"], 1.0)
        self.assertEqual(report["evaluation"]["template"]["king_focus"]["black_king"]["recall"], 0.0)
        self.assertTrue(result["conflicts"][0]["king_related"])
        self.assertEqual(
            result["conflicts"][0]["disagreement_squares"],
            [{"square": "e8", "model": "k", "template": "q", "gold": "k"}],
        )

    def test_draft_label_links_but_does_not_become_ground_truth(self) -> None:
        model = EMPTY.copy()
        model[0] = "K"
        template = EMPTY.copy()
        template[0] = "R"

        result = audit_fen_conflicts_records(
            current_rows=[_current("diagram-1", model, template)],
            label_rows=[_label("diagram-1", model, trusted=False)],
            source_document_sha256=SOURCE_SHA,
        )

        report = result["report"]
        self.assertEqual(report["counts"]["linked_label_rows"], 1)
        self.assertEqual(report["counts"]["trusted_linked_boards"], 0)
        self.assertEqual(report["counts"]["unadjudicated_conflicts"], 1)
        self.assertEqual(result["conflicts"][0]["verdict"], "unadjudicated_no_trusted_label")

    def test_geometry_fallback_requires_one_to_one_match(self) -> None:
        result = audit_fen_conflicts_records(
            current_rows=[_current("new-id", EMPTY, EMPTY, comparison="exact")],
            label_rows=[_label("old-id", EMPTY)],
            source_document_sha256=SOURCE_SHA,
        )

        self.assertEqual(result["report"]["counts"]["linked_label_rows"], 1)
        self.assertEqual(result["linkage"][0]["link_status"], "page_bbox_one_to_one")

    def test_rejected_label_is_never_used_even_when_piece_flag_is_true(self) -> None:
        label = _label("diagram-1", EMPTY)
        label["label_status"] = "rejected"

        result = audit_fen_conflicts_records(
            current_rows=[_current("diagram-1", EMPTY, EMPTY, comparison="exact")],
            label_rows=[label],
            source_document_sha256=SOURCE_SHA,
        )

        self.assertEqual(result["report"]["counts"]["trusted_linked_boards"], 0)

    def test_file_audit_rejects_labels_from_another_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            source.write_bytes(b"source")
            manifest = root / "current.json"
            manifest.write_text(
                json.dumps({"records": [_current("diagram-1", EMPTY, EMPTY)]}),
                encoding="utf-8",
            )
            labels = root / "labels.jsonl"
            label = _label("diagram-1", EMPTY)
            label["source_document_sha256"] = sha256(b"other").hexdigest()
            labels.write_text(json.dumps(label) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "label_source_sha256_mismatch"):
                audit_fen_conflicts_files(
                    current_manifest=manifest,
                    piece_labels=labels,
                    source_pdf=source,
                    output_dir=root / "out",
                )


if __name__ == "__main__":
    unittest.main()
