from __future__ import annotations

import io
import unittest
import zipfile

from PIL import Image, ImageDraw

from scripts.run_smoke_tests import (
    _build_case_benchmark,
    _build_smoke_markdown,
    _build_smoke_summary,
    _empty_chess_quality_metrics,
    _evaluate_chess_asset_quality_gate,
    _evaluate_chess_fen_acceptance_gate,
    _inspect_epub_chess_quality,
)


class SmokeChessQualityTests(unittest.TestCase):
    def _png_bytes(self, size: tuple[int, int]) -> bytes:
        output = io.BytesIO()
        Image.new("RGB", size, "white").save(output, format="PNG")
        return output.getvalue()

    def _board_png_bytes(self, size: tuple[int, int]) -> bytes:
        image = Image.new("L", size, 255)
        draw = ImageDraw.Draw(image)
        cell = max(1, min(size) // 8)
        for row in range(8):
            for col in range(8):
                fill = 255 if (row + col) % 2 == 0 else 32
                draw.rectangle(
                    (col * cell, row * cell, min(size[0], (col + 1) * cell), min(size[1], (row + 1) * cell)),
                    fill=fill,
                )
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def _build_epub_bytes(self, files: dict[str, str | bytes]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                payload = content.encode("utf-8") if isinstance(content, str) else content
                archive.writestr(archive_path, payload, compress_type=compress_type)
        return output.getvalue()

    def test_chess_quality_metrics_and_asset_gate_are_reported(self) -> None:
        large_board = self._board_png_bytes((700, 500))
        small_board = self._board_png_bytes((120, 120))
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "EPUB/chapter_001.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <p>PUA: \ue06d Figurine: \u265e</p>
    <p>FEN: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1</p>
    <p>FEN: wymaga review</p>
    <div class="chess-position" data-fen="8/8/8/8/8/8/8/8 w - - 0 1">accepted container</div>
    <img class="chess-diagram primary" data-fen="8/8/8/8/8/8/8/8 w - - 0 1" src="images/board_large.png" alt="board"/>
    <img class="chess-diagram" src="images/board_large.png" alt="duplicate"/>
    <figure class="chess-diagram"><figcaption>diagram wrapper</figcaption></figure>
    <img class="chess-diagram" src="images/board_small.png" alt="small"/>
  </body>
</html>
""",
                "EPUB/images/board_large.png": large_board,
                "EPUB/images/board_small.png": small_board,
            }
        )

        metrics = _inspect_epub_chess_quality(epub_bytes)
        asset_gate = _evaluate_chess_asset_quality_gate(metrics)
        row = {
            "id": "diagram_book",
            "analysis": {"profile": "premium"},
            "validation": {"summary": {"status": "passed"}},
            "size_gate": {"status": "passed", "inspection": {"image_count": 2}},
            "epub_size_bytes": len(epub_bytes),
            "chess_quality": metrics,
            "asset_quality_gate": asset_gate,
        }

        benchmark = _build_case_benchmark(row=row, elapsed_seconds=0.5)
        summary = _build_smoke_summary([row])

        self.assertEqual(metrics["chess_diagram_tag_count"], 4)
        self.assertEqual(metrics["data_fen_count"], 2)
        self.assertEqual(metrics["visible_fen_count"], 1)
        self.assertEqual(metrics["fen_review_count"], 1)
        self.assertEqual(metrics["unique_src_count"], 2)
        self.assertEqual(metrics["duplicate_src_count"], 1)
        self.assertEqual(metrics["pua_count"], 1)
        self.assertEqual(metrics["unicode_figurine_count"], 1)
        self.assertEqual(metrics["max_chess_image_edge_px"], 700)
        self.assertEqual(metrics["oversize_count_gt_640"], 1)
        self.assertEqual(metrics["largest_chess_asset_bytes"], max(len(large_board), len(small_board)))
        self.assertEqual(metrics["total_chess_asset_bytes"], len(large_board) + len(small_board))
        self.assertEqual(metrics["avg_chess_asset_bytes"], round((len(large_board) + len(small_board)) / 2, 2))
        self.assertGreater(metrics["chess_quality_score_min"], 0)
        self.assertGreaterEqual(metrics["chess_quality_score_avg"], metrics["chess_quality_score_min"])
        self.assertGreater(metrics["contrast_range_min"], 0)
        self.assertGreaterEqual(metrics["contrast_range_avg"], metrics["contrast_range_min"])
        self.assertGreater(metrics["edge_mean_min"], 0)
        self.assertGreaterEqual(metrics["edge_mean_avg"], metrics["edge_mean_min"])
        self.assertEqual(benchmark["validation_status"], "passed")
        self.assertEqual(benchmark["metrics_missing"], [])
        self.assertEqual(benchmark["chess_quality"], metrics)
        self.assertEqual(benchmark["fen_acceptance_gate"]["status"], "failed")
        self.assertEqual(benchmark["fen_acceptance_gate"]["fen_coverage"], 0.5)
        self.assertEqual(asset_gate["status"], "passed_with_warnings")
        self.assertEqual(asset_gate["budget"], "chess_crisp_low_size")
        self.assertEqual(benchmark["asset_quality_gate"], asset_gate)
        self.assertEqual(summary["overall_status"], "passed_with_warnings")
        self.assertEqual(summary["asset_warning_cases"], 1)

        row["fen_acceptance_gate"] = benchmark["fen_acceptance_gate"]
        markdown = _build_smoke_markdown({"mode": "quick", "summary": {"cases_run": 1}, "cases": [row]})
        self.assertIn("data FEN", markdown)
        self.assertIn("visible FEN", markdown)
        self.assertIn("FEN review", markdown)
        self.assertIn("total asset", markdown)
        self.assertIn("avg asset", markdown)
        self.assertIn("quality min/avg", markdown)
        self.assertIn("contrast min/avg", markdown)
        self.assertIn("edge mean min/avg", markdown)
        self.assertIn("Asset quality gate", markdown)
        self.assertIn("FEN acceptance gate", markdown)

    def test_chess_fen_acceptance_gate_requires_80_percent_coverage(self) -> None:
        row = {
            "quality_report": {
                "chess_fen": {
                    "diagram_count": 100,
                    "fen_count": 79,
                    "manual_review_count": 21,
                }
            }
        }

        failed = _evaluate_chess_fen_acceptance_gate(row)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["fen_coverage"], 0.79)
        self.assertEqual(failed["fen_acceptance_min"], 0.80)

        row["quality_report"]["chess_fen"]["fen_count"] = 80
        row["quality_report"]["chess_fen"]["manual_review_count"] = 20
        warning = _evaluate_chess_fen_acceptance_gate(row)
        self.assertEqual(warning["status"], "passed_with_warnings")
        self.assertEqual(warning["fen_coverage"], 0.8)

        row["quality_report"]["chess_fen"]["fen_count"] = 100
        row["quality_report"]["chess_fen"]["manual_review_count"] = 0
        passed = _evaluate_chess_fen_acceptance_gate(row)
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(passed["fen_coverage"], 1.0)

    def test_chess_fen_acceptance_gate_participates_in_smoke_summary(self) -> None:
        row = {
            "id": "diagram_book",
            "validation": {"summary": {"status": "passed"}},
            "size_gate": {"status": "passed"},
            "asset_quality_gate": {"status": "passed"},
            "fen_acceptance_gate": {
                "status": "failed",
                "fen_coverage": 0.79,
                "fen_acceptance_min": 0.80,
            },
        }

        summary = _build_smoke_summary([row])

        self.assertEqual(summary["overall_status"], "failed")
        self.assertEqual(summary["fen_failed_cases"], 1)

    def test_empty_chess_quality_metrics_include_fen_counts(self) -> None:
        metrics = _empty_chess_quality_metrics()

        for key in ("data_fen_count", "visible_fen_count", "fen_review_count"):
            self.assertIn(key, metrics)
            self.assertEqual(metrics[key], 0)

    def test_chess_quality_metrics_default_to_zero_without_chess_assets(self) -> None:
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "EPUB/chapter_001.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body><p>No diagrams here.</p></body>
</html>
""",
            }
        )

        metrics = _inspect_epub_chess_quality(epub_bytes)

        for key in (
            "total_chess_asset_bytes",
            "avg_chess_asset_bytes",
            "chess_quality_score_min",
            "chess_quality_score_avg",
            "contrast_range_min",
            "contrast_range_avg",
            "edge_mean_min",
            "edge_mean_avg",
        ):
            self.assertEqual(metrics[key], 0)
        self.assertEqual(_evaluate_chess_asset_quality_gate(metrics)["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
