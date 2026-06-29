from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import fitz

from chess_pgn_extractor import (
    annotate_records_with_replayed_fens,
    build_chess_glyph_diagnostics_payload,
    build_combined_pgn,
    extract_chess_pgn_records_from_text,
    summarize_chess_pgn_records,
)
from converter import ConversionConfig
from deepseek_quality_provider import DeepSeekAuditConfig, DeepSeekAuditProvider
from pymupdf_chess_extractor import (
    _normalize_chess_span_text,
    _pdf_text_segment_from_span,
    _scan_chess_pgn_extra_artifacts,
    extract_chess_notation_pdf_reflow,
)


class ChessGlyphDiagnosticsTests(unittest.TestCase):
    def test_rawdict_span_captures_unmapped_glyph_context_before_text_normalization(self) -> None:
        chars = [
            {"c": char, "bbox": (10 + index, 20, 11 + index, 31), "origin": (10 + index, 30)}
            for index, char in enumerate("1. \ufffd \"'t!;>g4\"")
        ]
        segment = _pdf_text_segment_from_span(
            {
                "font": "CustomChess-Regular",
                "size": 11,
                "bbox": (10, 20, 80, 31),
                "chars": chars,
            },
            page_num=2,
            block_index=3,
            line_index=4,
            span_index=5,
        )

        _normalize_chess_span_text(segment)

        self.assertIn("unmapped_chess_glyphs", segment["warnings"])
        diagnostic = segment["glyph_diagnostics"][0]
        self.assertEqual(diagnostic["page"], 3)
        self.assertEqual(diagnostic["block_index"], 3)
        self.assertEqual(diagnostic["line_index"], 4)
        self.assertEqual(diagnostic["span_index"], 5)
        self.assertEqual(diagnostic["font_name"], "CustomChess-Regular")
        self.assertEqual(diagnostic["bbox"], [10.0, 20.0, 80.0, 31.0])
        self.assertIn("replacement_char", diagnostic["reasons"])
        self.assertIn("mojibake_token", diagnostic["reasons"])
        self.assertIn({"char_index": 3, "char": "\ufffd", "codepoint": "U+FFFD", "synthetic": False, "bbox": [13.0, 20.0, 14.0, 31.0], "origin": [13.0, 30.0]}, diagnostic["codepoints"])

    def test_pgn_record_keeps_glyph_diagnostics_and_blocks_strict_export(self) -> None:
        glyph_diagnostic = {
            "page": 1,
            "font_name": "CustomChess-Regular",
            "span_index": 7,
            "bbox": [12.0, 24.0, 88.0, 36.0],
            "reasons": ["mojibake_token"],
            "raw_text": "1. e4 e5 2. Nf3 Nc6 \"'t!;>g4\" *",
            "codepoints": [{"char_index": 22, "char": ">", "codepoint": "U+003E"}],
        }

        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 \"'t!;>g4\" *",
                page_num=0,
                source_title="Glyph diagnostics",
                ocr_confidence=1.0,
                glyph_diagnostics=[glyph_diagnostic],
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertEqual(records[0].final_fen, "")
        self.assertIn("unmapped_chess_glyphs", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")
        self.assertEqual(records[0].to_dict()["glyph_diagnostics"][0]["font_name"], "CustomChess-Regular")

        summary = summarize_chess_pgn_records(records)
        self.assertEqual(summary["unmapped_glyphs"]["diagnostic_count"], 1)
        self.assertEqual(summary["unmapped_glyphs"]["diagnostic_by_font"]["CustomChess-Regular"], 1)

        payload = build_chess_glyph_diagnostics_payload(records, source_title="Glyph diagnostics")
        self.assertEqual(payload["diagnostic_count"], 1)
        self.assertEqual(payload["records"][0]["diagnostics"][0]["span_index"], 7)

    def test_chess_route_emits_glyph_diagnostics_json_artifact_for_review_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "glyph-diagnostics.pdf"
            doc = fitz.open()
            page = doc.new_page(width=360, height=240)
            page.insert_text((36, 48), "1. e4 e5 2. Nf3 Nc6 \"'t!;>g4\" *", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            content = extract_chess_notation_pdf_reflow(
                str(pdf_path),
                ConversionConfig(pdf_layout_preview_dpi=72),
                {"title": "Glyph diagnostics"},
            )

        artifacts = {artifact["key"]: artifact for artifact in content["extra_artifacts"]}
        self.assertIn("chess_glyph_diagnostics", artifacts)
        self.assertIn("chess_pgn_html", artifacts)
        self.assertIn("pdf_layout_preview", artifacts)
        self.assertNotIn("chess_pgn", artifacts)

        payload = json.loads(artifacts["chess_glyph_diagnostics"]["data"].decode("utf-8"))
        self.assertEqual(payload["diagnostic_count"], 1)
        self.assertGreaterEqual(payload["record_count"], 1)
        self.assertIn("Helvetica", payload["by_font"])

    def test_chess_route_emits_deepseek_audit_artifact_when_provider_enabled(self) -> None:
        def fake_transport(_url, _headers, payload, _timeout):
            audit_type = json.loads(payload["messages"][1]["content"])["audit_type"]
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "evidence_only": True,
                                    "requires_human_confirmation": True,
                                    "audit_type": audit_type,
                                    "glyph_clusters": [],
                                    "suspected_mappings": [],
                                    "layout_warnings": [],
                                    "next_measurements": [],
                                }
                            )
                        }
                    }
                ],
                "usage": {},
            }

        provider = DeepSeekAuditProvider(DeepSeekAuditConfig(api_key="deepseek-test"), transport=fake_transport)
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "deepseek-glyph-audit.pdf"
            doc = fitz.open()
            page = doc.new_page(width=360, height=240)
            page.insert_text((36, 48), "1. e4 e5 2. Nf3 Nc6 \"'t!;>g4\" *", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            with mock.patch(
                "deepseek_quality_provider.build_deepseek_audit_provider_from_env",
                return_value=provider,
            ):
                content = extract_chess_notation_pdf_reflow(
                    str(pdf_path),
                    ConversionConfig(pdf_layout_preview_dpi=72),
                    {"title": "DeepSeek glyph audit"},
                )

        artifacts = {artifact["key"]: artifact for artifact in content["extra_artifacts"]}
        self.assertIn("deepseek_audit", artifacts)
        self.assertIn("chess_glyph_diagnostics", artifacts)
        self.assertNotIn("chess_pgn", artifacts)
        payload = json.loads(artifacts["deepseek_audit"]["data"].decode("utf-8"))
        self.assertTrue(payload["evidence_only"])
        self.assertTrue(payload["requires_human_confirmation"])
        self.assertFalse(payload["mutates_output"])
        self.assertIn("glyph_diagnostics", payload["sections"])

    def test_scan_pgn_extra_artifacts_emit_layout_html_for_diagrams(self) -> None:
        artifacts = _scan_chess_pgn_extra_artifacts(
            [],
            source_title="Diagram artifact",
            diagrams=[
                {
                    "id": "diagram-1",
                    "page_index": 0,
                    "page_number": 1,
                    "bbox": [10, 20, 110, 120],
                    "caption": "Diagram 1-2",
                    "image_data_uri": "data:image/png;base64,AA==",
                    "fen_candidate": "8/8/8/8/8/8/8/8 w - - 0 1",
                }
            ],
            book_layout_pages=[
                {
                    "page_index": 0,
                    "page_number": 1,
                    "width": 360,
                    "height": 480,
                    "background_image_data_uri": "data:image/jpeg;base64,AA==",
                    "elements": [
                        {
                            "type": "diagram",
                            "bbox": [10, 20, 110, 120],
                            "reading_order": 1,
                            "image_data_uri": "data:image/png;base64,AA==",
                            "title": "Diagram 1-2",
                        },
                        {
                            "type": "fen",
                            "bbox": [10, 126, 220, 146],
                            "reading_order": 2,
                            "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
                        },
                    ],
                }
            ],
        )

        by_key = {artifact["key"]: artifact for artifact in artifacts}
        self.assertIn("chess_pgn_html", by_key)
        self.assertIn("pdf_layout_preview", by_key)
        self.assertNotIn("chess_pgn", by_key)
        html = by_key["chess_pgn_html"]["data"].decode("utf-8")
        preview_html = by_key["pdf_layout_preview"]["data"].decode("utf-8")
        self.assertIn("Detected chess diagrams / FEN", html)
        self.assertIn("diagram-1", html)
        self.assertNotIn('data-km-view="chess-book-review"', html)
        self.assertNotIn("book-diagram", html)
        self.assertIn('data-km-view="chess-book-review"', preview_html)
        self.assertIn('class="chess-book-page"', preview_html)
        self.assertIn('class="book-page-bg"', preview_html)
        self.assertIn("book-diagram", preview_html)
        self.assertIn("book-fen", preview_html)
        self.assertIn("Diagram 1-2", preview_html)
        self.assertIn("To nie jest finalny reader szachowy", preview_html)
        self.assertIn("chess_pgn_html", preview_html)


if __name__ == "__main__":
    unittest.main()
