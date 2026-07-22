from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster


def accepted_book() -> dict[str, object]:
    return {
        "schema": "kindlemaster.chess_reader.semantic_book.v1",
        "book_title": "CLI Book",
        "exercises": [
            {
                "exercise_id": "ex_1_1",
                "source": {"page_number": 11},
                "game": {"normalized_title": "white black event"},
                "diagram": {
                    "diagram_id": "d-1",
                    "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                    "fen_status": "available",
                },
                "solution": {"raw_text": "1. Kf3", "normalized_notation": "1. Kf3"},
                "solution_match": {"status": "exact", "production_blocked": False},
                "solution_integrity": {"status": "accepted", "strict_blocked": False, "findings": []},
                "navigation": {
                    "status": "accepted",
                    "accepted": True,
                    "exercise_number": "1-1",
                    "exercise_anchor": "exercise-ex-1-1",
                    "solution_anchor": "solution-ex-1-1",
                    "forward_href": "#solution-ex-1-1",
                    "backlink_href": "#exercise-ex-1-1",
                    "forward_text": "Open solution for Exercise 1-1",
                    "backlink_text": "Back to Exercise 1-1",
                    "findings": [],
                },
                "validation": {"warnings": []},
            }
        ],
        "pages": [],
        "exercise_model_warnings": [],
    }


class SemanticReleaseGateCliTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict[str, object]) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_release_cli_returns_zero_with_complete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            semantic = self._write(root, "semantic.json", accepted_book())
            counts = self._write(root, "counts.json", {"exercise_count": 1, "solution_count": 1})
            metadata = self._write(root, "metadata.json", {"title": "CLI Book", "language": "en", "identifier": "urn:cli"})
            toc = self._write(root, "toc.json", {"status": "approved"})
            fen = self._write(
                root,
                "fen.json",
                {"status": "passed", "summary": {"false_accepted_full_fen_count": 0, "published_full_fen_coverage": 1.0}},
            )
            reports = root / "reports"
            with patch.object(
                sys,
                "argv",
                [
                    "kindlemaster.py",
                    "chess-release-gate",
                    str(semantic),
                    "--mode",
                    "release",
                    "--reports-dir",
                    str(reports),
                    "--expected-counts-json",
                    str(counts),
                    "--metadata-json",
                    str(metadata),
                    "--toc-report-json",
                    str(toc),
                    "--fen-release-report-json",
                    str(fen),
                ],
            ):
                returncode = kindlemaster.main()
            self.assertEqual(returncode, 0)
            self.assertTrue((reports / "semantic_release_gate.json").is_file())

    def test_strict_cli_returns_nonzero_for_blocked_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = accepted_book()
            payload["exercises"][0]["solution_match"] = {"status": "mismatch", "production_blocked": True}  # type: ignore[index]
            semantic = self._write(root, "semantic.json", payload)
            with patch.object(
                sys,
                "argv",
                ["kindlemaster.py", "chess-release-gate", str(semantic), "--mode", "strict", "--reports-dir", str(root / "reports")],
            ):
                returncode = kindlemaster.main()
            self.assertEqual(returncode, 1)


if __name__ == "__main__":
    unittest.main()
