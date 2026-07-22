from __future__ import annotations

import unittest
from collections import defaultdict

from chess_exercise_navigation import (
    NAVIGATION_SCHEMA,
    build_navigation_report,
    validate_internal_links,
)


def _accepted_exercise(
    index: int,
    *,
    exercise_document: str = "reader.xhtml",
    solution_document: str = "reader.xhtml",
) -> dict[str, object]:
    exercise_id = f"ex_1_{index}"
    return {
        "exercise_id": exercise_id,
        "exercise_number": f"1-{index}",
        "exercise_document": exercise_document,
        "solution_document": solution_document,
        "game": {
            "raw_title": f"Player {index} - Opponent {index}, Test 2026",
            "normalized_title": f"player {index} opponent {index} test 2026",
        },
        "diagram": {"diagram_id": f"diagram-{index}"},
        "solution": {"raw_text": f"1. Nf{(index % 8) + 1} *"},
        "solution_match": {
            "status": "exact",
            "selected_solution_id": f"solution-{index}",
            "production_blocked": False,
        },
        "solution_integrity": {"status": "accepted", "strict_blocked": False},
    }


class ExerciseSolutionNavigationTests(unittest.TestCase):
    def test_same_document_links_are_generated_from_one_record(self) -> None:
        report = build_navigation_report([_accepted_exercise(7)])
        record = report.records[0]

        self.assertEqual(report.schema, NAVIGATION_SCHEMA)
        self.assertTrue(record.accepted)
        self.assertEqual(record.exercise_anchor, "exercise-ex-1-7")
        self.assertEqual(record.solution_anchor, "solution-ex-1-7")
        self.assertEqual(record.forward_href, "#solution-ex-1-7")
        self.assertEqual(record.backlink_href, "#exercise-ex-1-7")
        self.assertIn("1-7", record.forward_text)
        self.assertIn("1-7", record.backlink_text)
        self.assertFalse(report.production_blocked)

    def test_cross_file_links_resolve_without_orphans(self) -> None:
        report = build_navigation_report(
            [
                _accepted_exercise(
                    12,
                    exercise_document="exercises/chapter-01.xhtml",
                    solution_document="solutions/chapter-01.xhtml",
                )
            ]
        )
        record = report.records[0]
        self.assertEqual(record.forward_href, "../solutions/chapter-01.xhtml#solution-ex-1-12")
        self.assertEqual(record.backlink_href, "../exercises/chapter-01.xhtml#exercise-ex-1-12")

        documents = {
            record.exercise_document: (
                f'<article id="{record.exercise_anchor}">'
                f'<a href="{record.forward_href}">{record.forward_text}</a></article>'
            ),
            record.solution_document: (
                f'<section id="{record.solution_anchor}">'
                f'<a href="{record.backlink_href}">{record.backlink_text}</a></section>'
            ),
        }
        validation = validate_internal_links(documents)
        self.assertTrue(validation.valid)
        self.assertEqual(validation.href_count, 2)
        self.assertEqual(validation.orphan_hrefs, ())

    def test_unverified_or_incomplete_identity_never_gets_href(self) -> None:
        cases = {
            "missing-diagram": {"diagram": {}},
            "missing-solution": {"solution": {}},
            "unverified-match": {
                "solution_match": {
                    "status": "mismatch",
                    "selected_solution_id": "",
                    "production_blocked": True,
                }
            },
            "blocked-integrity": {
                "solution_integrity": {
                    "status": "blocked",
                    "strict_blocked": True,
                }
            },
            "missing-title": {"game": {"raw_title": "", "normalized_title": ""}},
        }
        for label, patch in cases.items():
            with self.subTest(label=label):
                record = _accepted_exercise(5)
                record.update(patch)
                decision = build_navigation_report([record]).records[0]
                self.assertFalse(decision.accepted)
                payload = decision.to_dict()
                self.assertEqual(payload["forward_href"], "")
                self.assertEqual(payload["backlink_href"], "")

    def test_duplicate_identity_blocks_both_directions(self) -> None:
        report = build_navigation_report([_accepted_exercise(8), _accepted_exercise(8)])
        self.assertTrue(report.production_blocked)
        self.assertTrue(all(not record.accepted for record in report.records))
        codes = {
            finding.code
            for record in report.records
            for finding in record.findings
        }
        self.assertIn("DUPLICATE_EXERCISE_IDENTITY", codes)
        self.assertIn("DUPLICATE_EXERCISE_TARGET", codes)
        self.assertIn("DUPLICATE_SOLUTION_TARGET", codes)

    def test_internal_validator_reports_missing_cross_file_target(self) -> None:
        validation = validate_internal_links(
            {
                "exercises/chapter.xhtml": (
                    '<article id="exercise-ex-1-1">'
                    '<a href="../solutions/chapter.xhtml#solution-ex-1-1">Open solution</a>'
                    "</article>"
                )
            }
        )
        self.assertFalse(validation.valid)
        self.assertEqual(len(validation.orphan_hrefs), 1)

    def test_1128_pair_fixture_has_exact_bidirectional_coverage(self) -> None:
        exercises = []
        for index in range(1, 1129):
            group = (index - 1) // 100 + 1
            exercises.append(
                _accepted_exercise(
                    index,
                    exercise_document=f"exercises/set-{group:02d}.xhtml",
                    solution_document=f"solutions/set-{group:02d}.xhtml",
                )
            )
        report = build_navigation_report(exercises)
        payload = report.to_dict()

        self.assertEqual(payload["summary"]["record_count"], 1128)
        self.assertEqual(payload["summary"]["accepted_count"], 1128)
        self.assertEqual(payload["summary"]["forward_link_count"], 1128)
        self.assertEqual(payload["summary"]["backlink_count"], 1128)
        self.assertEqual(payload["summary"]["orphan_count"], 0)
        self.assertFalse(payload["summary"]["production_blocked"])

        chunks: dict[str, list[str]] = defaultdict(list)
        for record in report.records:
            chunks[record.exercise_document].append(
                f'<article id="{record.exercise_anchor}">'
                f'<a href="{record.forward_href}">{record.forward_text}</a></article>'
            )
            chunks[record.solution_document].append(
                f'<section id="{record.solution_anchor}">'
                f'<a href="{record.backlink_href}">{record.backlink_text}</a></section>'
            )
        validation = validate_internal_links(
            {document: "\n".join(parts) for document, parts in chunks.items()}
        )
        self.assertTrue(validation.valid)
        self.assertEqual(validation.anchor_count, 2256)
        self.assertEqual(validation.href_count, 2256)


if __name__ == "__main__":
    unittest.main()
