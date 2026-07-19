from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chess_semantic_release_gate import (
    DEFAULT_ALLOWED_WARNINGS,
    evaluate_semantic_release_gate,
    run_output_semantic_release_gate,
    write_semantic_release_gate_reports,
)


def accepted_exercise(index: int = 1) -> dict[str, object]:
    return {
        "exercise_id": f"ex_1_{index}",
        "source": {"page_number": 10 + index, "bounding_box": [1, 2, 3, 4]},
        "game": {"normalized_title": f"white black event {index}"},
        "diagram": {
            "diagram_id": f"d-{index}",
            "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
            "fen_status": "available",
            "review_status": "verified",
        },
        "solution": {"raw_text": "1. Kf3", "normalized_notation": "1. Kf3"},
        "solution_match": {"status": "exact", "production_blocked": False},
        "solution_integrity": {"status": "accepted", "strict_blocked": False, "findings": []},
        "navigation": {
            "status": "accepted",
            "accepted": True,
            "exercise_number": f"1-{index}",
            "exercise_anchor": f"exercise-ex-1-{index}",
            "solution_anchor": f"solution-ex-1-{index}",
            "forward_href": f"#solution-ex-1-{index}",
            "backlink_href": f"#exercise-ex-1-{index}",
            "forward_text": f"Open solution for Exercise 1-{index}",
            "backlink_text": f"Back to Exercise 1-{index}",
            "findings": [],
        },
        "validation": {"confidence": 1.0, "warnings": []},
    }


def book(*exercises: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "kindlemaster.chess_reader.semantic_book.v1",
        "book_title": "Test Chess Book",
        "exercises": list(exercises),
        "pages": [],
        "exercise_model_warnings": [],
    }


class SemanticReleaseGateTests(unittest.TestCase):
    def test_release_passes_with_all_required_evidence(self) -> None:
        html = (
            '<article id="exercise-ex-1-1"><a href="#solution-ex-1-1">solution</a></article>'
            '<section id="solution-ex-1-1"><a href="#exercise-ex-1-1">back</a></section>'
        )
        report = evaluate_semantic_release_gate(
            book(accepted_exercise()),
            mode="release",
            expected_counts={"exercise_count": 1, "solution_count": 1},
            publication_metadata={"title": "Test Chess Book", "language": "en", "identifier": "urn:test"},
            toc_report={"status": "approved"},
            fen_release_report={
                "status": "passed",
                "summary": {"false_accepted_full_fen_count": 0, "published_full_fen_coverage": 1.0},
            },
            documents={"reader.xhtml": html},
        )
        self.assertEqual(report.status, "passed")
        self.assertEqual(report.exit_code, 0)

    def test_development_reports_p0_but_does_not_fail(self) -> None:
        broken = accepted_exercise()
        broken["solution"] = None
        report = evaluate_semantic_release_gate(book(broken), mode="development")
        self.assertTrue(any(item.code == "MISSING_SOLUTION" for item in report.findings))
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.status, "passed_with_findings")

    def test_strict_returns_nonzero_for_semantic_p0(self) -> None:
        broken = accepted_exercise()
        broken["navigation"] = {"status": "blocked", "accepted": False}
        report = evaluate_semantic_release_gate(book(broken), mode="strict")
        self.assertEqual(report.exit_code, 1)
        self.assertTrue(any(item.code == "NAVIGATION_NOT_ACCEPTED" for item in report.blocking_findings))

    def test_release_requires_existing_fixed_edition_gate(self) -> None:
        report = evaluate_semantic_release_gate(
            book(accepted_exercise()),
            mode="release",
            expected_counts={"exercise_count": 1, "solution_count": 1},
            publication_metadata={"title": "Test", "language": "en", "identifier": "urn:test"},
            toc_report={"status": "approved"},
        )
        self.assertTrue(any(item.code == "FIXED_EDITION_FEN_GATE_NOT_PASSED" for item in report.findings))
        self.assertEqual(report.exit_code, 1)

    def test_release_rejects_unallowlisted_warning(self) -> None:
        exercise = accepted_exercise()
        exercise["validation"] = {
            "confidence": 0.8,
            "warnings": [{"code": "NEW_UNKNOWN_WARNING", "severity": "warning", "message": "review"}],
        }
        report = evaluate_semantic_release_gate(
            book(exercise),
            mode="strict",
            allowed_warnings=DEFAULT_ALLOWED_WARNINGS,
        )
        self.assertTrue(any(item.code == "UNALLOWLISTED_WARNING" for item in report.findings))
        self.assertEqual(report.exit_code, 1)

    def test_allowlisted_warning_is_accepted(self) -> None:
        exercise = accepted_exercise()
        exercise["solution_integrity"] = {
            "status": "warning",
            "strict_blocked": False,
            "findings": [{"code": "SHORT_SOLUTION_REVIEW", "severity": "warning"}],
        }
        report = evaluate_semantic_release_gate(book(exercise), mode="strict")
        self.assertFalse(any(item.code == "UNALLOWLISTED_WARNING" for item in report.findings))

    def test_duplicate_ids_and_anchors_are_blocked(self) -> None:
        first = accepted_exercise(1)
        second = accepted_exercise(2)
        second["exercise_id"] = first["exercise_id"]
        second["navigation"] = dict(first["navigation"])
        report = evaluate_semantic_release_gate(book(first, second), mode="strict")
        codes = {item.code for item in report.findings}
        self.assertIn("DUPLICATE_EXERCISE_ID", codes)
        self.assertIn("DUPLICATE_NAVIGATION_TARGET", codes)

    def test_orphan_semantic_block_is_reported(self) -> None:
        payload = book(accepted_exercise())
        payload["pages"] = [{"page_number": 99, "blocks": [{"type": "solution", "exercise_id": "ex_9_9"}]}]
        report = evaluate_semantic_release_gate(payload, mode="strict")
        self.assertTrue(any(item.code == "ORPHAN_SEMANTIC_CONTENT" for item in report.findings))

    def test_orphan_fragment_is_blocked(self) -> None:
        report = evaluate_semantic_release_gate(
            book(accepted_exercise()),
            mode="strict",
            documents={"reader.xhtml": '<a id="exercise-ex-1-1" href="#missing">bad</a>'},
        )
        self.assertTrue(any(item.code == "ORPHAN_INTERNAL_FRAGMENT" for item in report.findings))

    def test_chess_move_list_is_blocked(self) -> None:
        report = evaluate_semantic_release_gate(
            book(accepted_exercise()),
            mode="strict",
            documents={"reader.xhtml": "<ol><li>1. Nf3 Nf6</li></ol>"},
        )
        self.assertTrue(any(item.code == "CHESS_NOTATION_RENDERED_AS_HTML_LIST" for item in report.findings))

    def test_report_contains_location_and_coordinates(self) -> None:
        broken = accepted_exercise()
        broken["solution"] = None
        report = evaluate_semantic_release_gate(book(broken), mode="strict")
        finding = next(item for item in report.findings if item.code == "MISSING_SOLUTION")
        self.assertEqual(finding.exercise_number, "1-1")
        self.assertEqual(finding.page, 11)
        self.assertEqual(finding.coordinates, (1.0, 2.0, 3.0, 4.0))

    def test_output_hook_writes_report_and_uses_development_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_output_semantic_release_gate(
                book(accepted_exercise()),
                out_dir=tmp,
                book_payload={"title": "Test Chess Book"},
                documents={"index.html": '<div id="exercise-ex-1-1"></div><div id="solution-ex-1-1"></div>'},
            )
            self.assertEqual(report.exit_code, 0)
            self.assertTrue((Path(tmp) / "reports" / "chess_reader" / "semantic_release_gate.json").is_file())

    def test_reports_are_written(self) -> None:
        report = evaluate_semantic_release_gate(book(accepted_exercise()), mode="development")
        with tempfile.TemporaryDirectory() as tmp:
            json_path, md_path = write_semantic_release_gate_reports(report, tmp)
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            self.assertIn("Chess Semantic Release Gate", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
