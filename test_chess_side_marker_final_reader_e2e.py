from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw

from chess_study_export import run_chess_study_export


VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
VALID_PLACEMENT = VALID_FEN.split()[0]


def _write_image_pdf(path: Path, image: Image.Image) -> None:
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    doc = fitz.open()
    try:
        page = doc.new_page(width=image.width, height=image.height)
        page.insert_image(fitz.Rect(0, 0, image.width, image.height), stream=payload.getvalue())
        doc.save(path)
    finally:
        doc.close()


def _side_marker_e2e_page() -> Image.Image:
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    for x0 in (100, 360):
        draw.rectangle((x0, 120, x0 + 150, 270), outline="black", width=3)
        step = 150 / 8
        for index in range(1, 8):
            offset = round(x0 + step * index)
            draw.line((offset, 120, offset, 270), fill="black", width=1)
            draw.line((x0, round(120 + step * index), x0 + 150, round(120 + step * index)), fill="black", width=1)
    draw.line([(266, 155), (255, 187), (277, 187), (266, 155)], fill="black", width=2, joint="curve")
    draw.polygon([(526, 155), (515, 187), (537, 187)], fill="black")
    return image


def _diagram_crop(image: Image.Image, bbox: list[int], target: Path) -> str:
    x0, y0, width, height = bbox
    target.parent.mkdir(parents=True, exist_ok=True)
    image.crop((x0, y0, x0 + width, y0 + height)).save(target, format="WEBP")
    return str(target)


class ChessSideMarkerFinalReaderE2ETests(unittest.TestCase):
    def test_pdf_side_markers_propagate_to_final_reader_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            page_image = _side_marker_e2e_page()
            pdf_path = root / "side-marker-e2e.pdf"
            _write_image_pdf(pdf_path, page_image)
            crop_dir = root / "detector-crops"
            first_bbox = [100, 120, 150, 150]
            second_bbox = [360, 120, 150, 150]
            first_crop = _diagram_crop(page_image, first_bbox, crop_dir / "e2e_w.webp")
            second_crop = _diagram_crop(page_image, second_bbox, crop_dir / "e2e_b.webp")
            degraded_html = root / "degraded-source.html"
            degraded_html.write_text(
                """
                <section class="chess-book-page" data-page="1">
                  <div class="book-text" data-reading-order="1" style="left:80px;top:70px;width:120px;height:14px">Diagram 1-1</div>
                  <div class="book-diagram" data-reading-order="2" style="left:100px;top:120px;width:150px;height:150px"><img src="" alt=""></div>
                  <div class="book-text" data-reading-order="3" style="left:340px;top:70px;width:120px;height:14px">Diagram 1-2</div>
                  <div class="book-diagram" data-reading-order="4" style="left:360px;top:120px;width:150px;height:150px"><img src="" alt=""></div>
                </section>
                """,
                encoding="utf-8",
            )
            fake_manifest = {
                "diagram_count": 2,
                "sampled_pages": [1],
                "low_confidence_review_count": 0,
                "diagrams": [
                    {
                        "diagram_id": "e2e_w",
                        "page": 1,
                        "bbox": [100, 120, 250, 270],
                        "pixel_bbox": first_bbox,
                        "image_path": first_crop,
                        "confidence": 0.99,
                        "fen_confidence": 0.99,
                        "fen": "",
                        "fen_candidate": VALID_FEN,
                        "full_fen": VALID_FEN,
                        "placement": VALID_PLACEMENT,
                        "warnings": ["side_to_move_inferred"],
                        "status": "needs_review",
                    },
                    {
                        "diagram_id": "e2e_b",
                        "page": 1,
                        "bbox": [360, 120, 510, 270],
                        "pixel_bbox": second_bbox,
                        "image_path": second_crop,
                        "confidence": 0.99,
                        "fen_confidence": 0.99,
                        "fen": "",
                        "fen_candidate": VALID_FEN,
                        "full_fen": VALID_FEN,
                        "placement": VALID_PLACEMENT,
                        "warnings": ["side_to_move_inferred"],
                        "status": "needs_review",
                    },
                ],
            }

            with patch("chess_study_export.detect_chess_diagrams", return_value=fake_manifest):
                run_chess_study_export(
                    pdf_path,
                    html_path=degraded_html,
                    out_dir=out,
                    diagram_pages=1,
                    diagram_dpi=72,
                    min_grid_confidence=0.90,
                )

            side_marker_report = json.loads((out / "reports" / "chess_fen" / "side_marker_assignment.json").read_text(encoding="utf-8"))
            diagrams_payload = json.loads((out / "chess_diagrams.json").read_text(encoding="utf-8"))
            positions_payload = json.loads((out / "positions.json").read_text(encoding="utf-8"))
            source_gate = json.loads((out / "reports" / "source_html_quality_gate.json").read_text(encoding="utf-8"))
            health_gate = json.loads((out / "reports" / "final_reader_health_gate.json").read_text(encoding="utf-8"))
            final_html = (out / "index.html").read_text(encoding="utf-8")

            summary = side_marker_report["summary"]
            diagrams = diagrams_payload["diagrams"]
            positions = positions_payload["positions"]
            visible_marker_diagram_count = 2
            trusted_marker_count = int(summary["trusted_marker_count"])
            side_unknown_count = len([item for item in positions if item.get("side_to_move_code") not in {"w", "b"}])
            empty_img_src_count = final_html.count('src=""')
            diagnostics = {
                "marker_detection_failure": trusted_marker_count < visible_marker_diagram_count,
                "empty_image_asset_failure": empty_img_src_count > 0,
                "source_html_overwrite_failure": bool(source_gate.get("used_as_final_reader")),
                "full_fen_gate_failure": int(summary.get("full_fen_accepted_count") or 0) < visible_marker_diagram_count,
                "visible_marker_diagram_count": visible_marker_diagram_count,
                "trusted_marker_count": trusted_marker_count,
                "side_unknown_count": side_unknown_count,
                "empty_img_src_count": empty_img_src_count,
            }

            self.assertGreater(summary["side_marker_crop_count"], 0, diagnostics)
            self.assertGreaterEqual(trusted_marker_count / visible_marker_diagram_count, 0.80, diagnostics)
            self.assertEqual(side_unknown_count, 0, diagnostics)
            self.assertEqual(empty_img_src_count, 0, diagnostics)
            self.assertEqual(health_gate["decision"], "pass", diagnostics)
            self.assertEqual(health_gate["side_unknown_count"], 0, diagnostics)
            self.assertEqual(health_gate["empty_img_src_count"], 0, diagnostics)
            self.assertGreaterEqual(health_gate["trusted_marker_count"], visible_marker_diagram_count, diagnostics)
            self.assertGreater(health_gate["side_marker_crop_count"], 0, diagnostics)
            self.assertFalse(source_gate["used_as_final_reader"], diagnostics)
            self.assertEqual({item["side_marker_status"] for item in diagrams}, {"trusted_marker"}, diagnostics)
            self.assertEqual({item["side_to_move"] for item in diagrams}, {"w", "b"}, diagnostics)
            self.assertIn("Side to move: white", final_html)
            self.assertIn("Side to move: black", final_html)
            self.assertIn("side marker crop", final_html)


if __name__ == "__main__":
    unittest.main()
