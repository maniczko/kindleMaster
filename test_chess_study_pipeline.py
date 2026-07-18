from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

import chess_study_export
from chess_study_export import (
    ChessStudyConfig,
    YUSUPOV_CHAPTERS,
    build_ai_assisted_quality_eval,
    build_ai_fen_candidates,
    build_ai_pgn_candidates,
    build_chess_fen_manual_review,
    build_chess_fen_templates,
    build_chess_pgn_review,
    build_study_pgn,
    build_study_positions,
    detect_study_diagrams,
    evaluate_chess_fen_profile,
    extract_study_notation_fragments,
    ingest_study_pdf,
    run_chess_study_export,
    sort_study_layout_elements,
)


class _FakeFenProvider:
    name = "fake-openai-fen"
    model = "fake-gpt"

    def propose_chess_fen_from_crop(self, context):
        return {
            "status": "ai_suggested",
            "provider": self.name,
            "model": self.model,
            "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "side_to_move": "w",
            "confidence": 0.97,
            "uncertain_squares": [],
            "reason": "synthetic two-king test position",
            "needs_review": False,
            "estimated_cost_usd": 0.001,
        }


class _FakeDeepSeekProvider:
    name = "fake-deepseek"

    class config:
        model = "fake-deepseek-model"

    def review_pgn_glyph_clusters(self, context):
        return {
            "status": "reviewed",
            "token_clusters": [{"token": "@e4", "count": 1}],
            "candidate_mappings": [{"token": "@e4", "candidates": ["e4"], "status": "draft"}],
            "near_accepted_records": context.get("near_accepted_rows") or [],
            "next_review_actions": ["confirm @e4 manually before mapping"],
        }


class _FakePgnProvider:
    name = "fake-openai-pgn"
    model = "fake-gpt-pgn"

    def propose_pgn_repair(self, context):
        return {
            "status": "ai_suggested",
            "provider": self.name,
            "model": self.model,
            "candidate_pgn": (
                '[Event "Source page 1"]\n'
                '[Site "?"]\n'
                '[Date "????.??.??"]\n'
                '[Round "?"]\n'
                '[White "?"]\n'
                '[Black "?"]\n'
                '[Result "*"]\n'
                '[SourcePage "1"]\n'
                '[SourceDiagram "Notation 1"]\n\n'
                "1. e4 *"
            ),
            "confidence": 0.9,
            "reason": "synthetic repair candidate",
            "warnings": [],
            "estimated_cost_usd": 0.002,
        }


def _make_minimal_study_pdf(path: Path) -> None:
    doc = fitz.open()
    for chapter_no, title in YUSUPOV_CHAPTERS:
        page = doc.new_page(width=360, height=240)
        page.insert_text((36, 48), f"{chapter_no} {title}", fontsize=14)
        page.insert_text((36, 84), f"Ex. {chapter_no}-1", fontsize=10)
    page = doc.new_page(width=360, height=240)
    page.insert_text((36, 48), "Final Test F-1", fontsize=14)
    page = doc.new_page(width=360, height=240)
    page.insert_text((36, 48), "Index of Games", fontsize=14)
    page = doc.new_page(width=360, height=240)
    page.insert_text((36, 48), "Recommended Books", fontsize=14)
    doc.save(path)
    doc.close()


class ChessStudyPipelineTests(unittest.TestCase):
    def test_layout_ordering_sorts_by_page_y_x_with_line_tolerance(self) -> None:
        items = [
            {"type": "text", "page": 2, "bbox": [10, 5, 40, 15], "reading_order": 0, "source_kind": "pdf"},
            {"type": "diagram", "page": 1, "bbox": [90, 20, 130, 60], "reading_order": 3, "source_kind": "detector"},
            {"type": "text", "page": 1, "bbox": [12, 20.4, 60, 28], "reading_order": 2, "source_kind": "pdf"},
            {"type": "text", "page": 1, "bbox": [10, 8, 60, 14], "reading_order": 1, "source_kind": "pdf"},
        ]

        ordered = sort_study_layout_elements(items, line_tolerance=3.0)

        self.assertEqual([item["page"] for item in ordered], [1, 1, 1, 2])
        self.assertEqual([item["reading_order"] for item in ordered[:3]], [1, 2, 3])

    def test_pdf_ingest_reuses_cached_page_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            doc = fitz.open()
            page = doc.new_page(width=180, height=120)
            page.insert_text((20, 40), "1 Mating motifs", fontsize=12)
            doc.save(pdf_path)
            doc.close()
            config = ChessStudyConfig(pdf=pdf_path, html=None, out=root / "out", diagram_dpi=72, render_pages=True)

            first = ingest_study_pdf(config)
            second = ingest_study_pdf(config)

        self.assertEqual(first["summary"]["page_images"], 1)
        self.assertEqual(first["summary"]["page_image_cache_misses"], 1)
        self.assertEqual(second["summary"]["page_images"], 1)
        self.assertEqual(second["summary"]["page_image_cache_hits"], 1)

    def test_pdf_ingest_filters_html_audit_junk_from_book_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "scan.pdf"
            doc = fitz.open()
            doc.new_page(width=180, height=120)
            doc.save(pdf_path)
            doc.close()
            html_path = root / "current.html"
            html_path.write_text(
                '<section class="chess-book-page" data-page="1">'
                '<p>move_number_jump pgn_replay_errors unmapped_chess_glyphs side_to_move_mismatch</p>'
                '<p>This is readable book text about a mating motif.</p>'
                '</section>',
                encoding="utf-8",
            )
            config = ChessStudyConfig(pdf=pdf_path, html=html_path, out=root / "out", render_pages=False)

            payload = ingest_study_pdf(config)

        page_text = payload["pages"][0]["normalized_text"]
        self.assertIn("readable book text", page_text)
        self.assertNotIn("move_number_jump", page_text)

    def test_notation_glyph_diagnostics_capture_raw_context_and_block_pgn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=180)
            page.insert_text((24, 48), "1. @g7 2. gld7t@f6 3. Nf3", fontsize=11)
            doc.save(pdf_path)
            doc.close()
            config = ChessStudyConfig(pdf=pdf_path, html=None, out=root / "out", render_pages=False)

            page_model = ingest_study_pdf(config)
            notation = extract_study_notation_fragments(page_model, {"positions": []}, root / "out")

            block_diagnostics = page_model["pages"][0]["blocks"][0]["glyph_diagnostics"]
            self.assertTrue(block_diagnostics)
            self.assertEqual(block_diagnostics[0]["source"], "pymupdf-rawdict")
            self.assertTrue(block_diagnostics[0]["font_name"])
            self.assertIn("U+0040", block_diagnostics[0]["codepoints"])
            self.assertTrue(block_diagnostics[0]["bbox"])

            fragment = notation["fragments"][0]
            self.assertEqual(fragment["status"], "needs_review")
            self.assertIn("unmapped_chess_glyphs", fragment["warnings"])
            self.assertTrue(fragment["glyph_diagnostics"])
            glyph_audit = json.loads((root / "out" / "notation_glyph_diagnostics.json").read_text(encoding="utf-8"))
            self.assertGreater(glyph_audit["diagnostic_count"], 0)
            self.assertTrue(glyph_audit["evidence_only"])
            self.assertTrue(glyph_audit["raw_glyph_context_available"])
            self.assertGreaterEqual(glyph_audit["glyph_mapping_candidate_count"], 1)
            self.assertTrue((root / "out" / "review" / "glyph_mapping_candidates.json").is_file())
            self.assertTrue((root / "out" / "review" / "glyph_mapping_review.html").is_file())

    def test_manual_diagram_labels_do_not_count_false_positive_as_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "diagram.webp"
            Image.new("RGB", (80, 80), "white").save(crop, format="WEBP")
            labels = root / "diagram_review.csv"
            labels.write_text(
                "diagram_id,manual_label,reviewer_notes\n"
                "p010_d01,correct_diagram,looks good\n"
                "p010_d02,false_positive,not a board\n",
                encoding="utf-8",
            )
            fake_manifest = {
                "diagram_count": 2,
                "sampled_pages": [10],
                "low_confidence_review_count": 0,
                "diagrams": [
                    {
                        "diagram_id": "p010_d01",
                        "page": 10,
                        "bbox": [10, 10, 90, 90],
                        "image_path": str(crop),
                        "confidence": 0.91,
                        "status": "needs_review",
                        "fen": "",
                    },
                    {
                        "diagram_id": "p010_d02",
                        "page": 10,
                        "bbox": [110, 10, 190, 90],
                        "image_path": str(crop),
                        "confidence": 0.88,
                        "status": "needs_review",
                        "fen": "",
                    },
                ],
            }
            config = ChessStudyConfig(
                pdf=root / "study.pdf",
                html=None,
                out=root / "out",
                diagram_review_labels=labels,
                diagram_alignment_review=True,
            )

            with patch("chess_study_export.detect_chess_diagrams", return_value=fake_manifest):
                diagrams = detect_study_diagrams(config)

            segments = {"pages": [{"page": 10, "chapter_no": 1, "labels": ["Ex. 1-1"]}]}
            positions = build_study_positions(diagrams, segments, root / "out")

            self.assertEqual(diagrams["diagram_labels_imported"], 2)
            self.assertEqual(diagrams["correct_diagrams"], 1)
            self.assertEqual(diagrams["false_positive_diagrams"], 1)
            self.assertEqual(diagrams["strict_diagram_count_after_review"], 1)
            self.assertEqual(diagrams["alignment_review"]["candidate_count"], 1)
            self.assertTrue((root / "out" / "review" / "diagram_alignment_review.html").is_file())
            self.assertEqual(len(positions["positions"]), 1)
            self.assertIn("p010_d01", positions["positions"][0]["source_crop"])

    def test_manual_ocr_mapping_can_unlock_pgn_only_after_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mapping = root / "glyph_mapping_manual.json"
            mapping.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "token": "@e4",
                                "replacement": "e4",
                                "scope": "ocr_only",
                                "status": "accepted",
                                "examples": ["1. @e4"],
                                "reviewer_note": "manual test mapping",
                            },
                            {
                                "token": "@d4",
                                "replacement": "d4",
                                "scope": "ocr_only",
                                "status": "draft",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            diagnostic = {
                "warning": "unmapped_chess_glyphs",
                "source": "html-assist-no-raw-glyph-context",
                "page": 1,
                "font_name": "html-assist",
                "bbox": [10, 10, 80, 20],
                "raw_text": "1. @e4",
                "context": "1. @e4",
                "codepoints": ["U+0031", "U+002E", "U+0040"],
                "chars": [],
                "reasons": ["at_sign_in_notation", "raw_char_context_unavailable"],
                "mapping_status": "unmapped",
            }
            page_model = {
                "pages": [
                    {
                        "page": 1,
                        "blocks": [
                            {
                                "text": "1. @e4",
                                "normalized_text": "1. @e4",
                                "bbox": [10, 10, 80, 20],
                                "reading_order": 1,
                                "glyph_diagnostics": [diagnostic],
                            }
                        ],
                    }
                ]
            }
            positions = {"positions": [{"id": "p001_d01", "diagram_page": 1, "fen": ""}]}

            notation = extract_study_notation_fragments(page_model, positions, root / "out", glyph_mapping_file=mapping)
            pgn_payload = build_study_pgn(positions, root / "out", notation_fragments=notation)

            fragment = notation["fragments"][0]
            self.assertEqual(fragment["status"], "accepted")
            self.assertEqual(fragment["raw_glyph_context_mode"], "ocr_only")
            self.assertEqual(fragment["ocr_token_mappings_applied"], 1)
            self.assertEqual(fragment["unmapped_token_blockers"], [])
            self.assertNotIn("unmapped_chess_glyphs", fragment["warnings"])
            self.assertEqual(notation["ocr_token_mappings_loaded"], 1)
            self.assertEqual(pgn_payload["accepted_pgn_count"], 1)
            self.assertIn("1. e4", (root / "out" / "book.pgn").read_text(encoding="utf-8"))
            self.assertTrue((root / "out" / "review" / "glyph_mapping_manual.template.json").is_file())
            self.assertTrue((root / "out" / "review" / "unmapped_token_blockers.json").is_file())

    def test_draft_ocr_mapping_keeps_fragment_in_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mapping = root / "glyph_mapping_manual.json"
            mapping.write_text(
                json.dumps({"mappings": [{"token": "@e4", "replacement": "e4", "scope": "ocr_only", "status": "draft"}]}),
                encoding="utf-8",
            )
            page_model = {
                "pages": [
                    {
                        "page": 1,
                        "blocks": [
                            {
                                "text": "1. @e4",
                                "normalized_text": "1. @e4",
                                "bbox": [10, 10, 80, 20],
                                "reading_order": 1,
                            }
                        ],
                    }
                ]
            }
            positions = {"positions": [{"id": "p001_d01", "diagram_page": 1, "fen": ""}]}

            notation = extract_study_notation_fragments(page_model, positions, root / "out", glyph_mapping_file=mapping)
            pgn_payload = build_study_pgn(positions, root / "out", notation_fragments=notation)

            fragment = notation["fragments"][0]
            self.assertEqual(fragment["status"], "needs_review")
            self.assertIn("unmapped_ocr_tokens", fragment["warnings"])
            self.assertIn("unmapped_chess_glyphs", fragment["warnings"])
            self.assertEqual(pgn_payload["accepted_pgn_count"], 0)

    def test_fen_manual_review_queue_exports_crop_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop_dir = out / "assets" / "diagrams"
            crop_dir.mkdir(parents=True)
            Image.new("RGB", (80, 80), "white").save(crop_dir / "p010_d001.png")
            Image.new("RGB", (80, 80), "white").save(crop_dir / "p020_d001.png")
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page": 10,
                                "diagrams": [
                                    {
                                        "id": "p010_d001",
                                        "page": 10,
                                        "reading_order": 3,
                                        "bbox": [10, 20, 80, 80],
                                        "caption": "Diagram 1-3",
                                        "image_path": "assets/diagrams/p010_d001.png",
                                        "validation_status": "needs-human-review",
                                        "review_reason": "fen_not_recognized",
                                    }
                                ],
                                "pgn_records": [],
                            },
                            {
                                "page": 20,
                                "diagrams": [
                                    {
                                        "id": "p020_d001",
                                        "page": 20,
                                        "reading_order": 4,
                                        "bbox": [10, 20, 80, 80],
                                        "caption": "Diagram 2-1",
                                        "image_path": "assets/diagrams/p020_d001.png",
                                        "validation_status": "needs-human-review",
                                        "review_reason": "fen_not_recognized",
                                    }
                                ],
                                "pgn_records": [],
                            }
                        ],
                        "pgn_records": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_chess_fen_manual_review(out, page_ranges="10")

            rows = [
                json.loads(line)
                for line in (out / "review" / "fen_manual_draft.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(payload["diagram_count"], 1)
            self.assertEqual(payload["sampled_pages"], [10])
            self.assertEqual(rows[0]["diagram_id"], "p010_d001")
            self.assertIn("manual_fen", rows[0])
            self.assertTrue(Path(rows[0]["crop_path"]).is_file())
            self.assertTrue((out / "review" / "fen_manual_review.html").is_file())
            self.assertTrue((out / "reports" / "chess_quality_dashboard.html").is_file())
            self.assertTrue((out / "review" / "iteration_status.md").is_file())

    def test_fen_review_page_range_extends_to_min_count_with_later_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop_dir = out / "assets" / "diagrams"
            crop_dir.mkdir(parents=True)
            pages = []
            for page in [10, 20, 30]:
                crop = crop_dir / f"p{page:03d}_d001.png"
                Image.new("RGB", (80, 80), "white").save(crop)
                pages.append(
                    {
                        "page": page,
                        "diagrams": [
                            {
                                "id": f"p{page:03d}_d001",
                                "page": page,
                                "reading_order": 1,
                                "bbox": [10, 20, 80, 80],
                                "caption": f"Diagram {page}",
                                "image_path": f"assets/diagrams/p{page:03d}_d001.png",
                                "validation_status": "needs-human-review",
                            }
                        ],
                        "pgn_records": [],
                    }
                )
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(json.dumps({"pages": pages, "pgn_records": []}), encoding="utf-8")

            payload = build_chess_fen_manual_review(out, page_ranges="10", min_count=2)

            self.assertEqual(payload["diagram_count"], 2)
            self.assertEqual(payload["sampled_pages"], [10, 20])
            self.assertEqual(payload["auto_extended_pages"], [20])

    def test_verified_fen_labels_build_templates_and_holdout_without_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop1 = root / "crop1.png"
            crop2 = root / "crop2.png"
            Image.new("RGB", (128, 128), "white").save(crop1)
            Image.new("RGB", (128, 128), "gray").save(crop2)
            labels = root / "fen_manual_draft.jsonl"
            valid_fen = "8/8/8/8/8/8/8/4K2k w - - 0 1"
            labels.write_text(
                "\n".join(
                    [
                        json.dumps({"diagram_id": "d1", "crop_path": str(crop1), "manual_fen": valid_fen, "manual_label": "correct_diagram", "label_status": "verified"}),
                        json.dumps({"diagram_id": "d2", "crop_path": str(crop2), "manual_fen": valid_fen, "manual_label": "cropped_diagram", "label_status": "verified"}),
                        json.dumps({"diagram_id": "draft", "crop_path": str(crop2), "manual_fen": valid_fen, "label_status": "draft"}),
                        json.dumps({"diagram_id": "draft_correct", "crop_path": str(crop2), "manual_fen": valid_fen, "manual_label": "correct_diagram", "label_status": "draft"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            out = root / "out"

            build_payload = build_chess_fen_templates(labels, out_dir=out, profile="test_profile")
            eval_payload = evaluate_chess_fen_profile(labels, out_dir=out, profile="test_profile", fold_count=2, holdout_fold=0)

            self.assertEqual(build_payload["promoted_label_count"], 2)
            self.assertEqual(build_payload["template_summary"]["boards_processed"], 2)
            promoted_rows = [
                json.loads(line)
                for line in (out / "review" / "fen_verified_labels.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(promoted_rows), 2)
            self.assertTrue(all(row.get("verified_by") for row in promoted_rows))
            self.assertTrue(all(row.get("verified_at") for row in promoted_rows))
            self.assertEqual(eval_payload["train_label_count"], 1)
            self.assertEqual(eval_payload["holdout_label_count"], 1)
            self.assertEqual(eval_payload["policy"], "templates_built_from_train_split_only")
            self.assertTrue((out / "review" / "diagram_alignment_notes.jsonl").is_file())
            self.assertTrue((out / "review" / "iteration_status.md").is_file())

    def test_pgn_review_uses_manual_mapping_but_blocks_until_replay_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps(
                    {
                        "pages": [],
                        "pgn_records": [
                            {
                                "id": "pgn_1",
                                "logical_page": 1,
                                "source_page": 1,
                                "label": "Notation 1",
                                "raw_text": "1. @e4",
                                "visible_review_text": "1. @e4",
                                "warnings": ["unmapped_chess_glyphs"],
                                "status": "needs-human-review",
                                "pgn": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            mapping = root / "glyph_mapping_manual.json"
            mapping.write_text(
                json.dumps({"mappings": [{"token": "@e4", "replacement": "e4", "scope": "ocr_only", "status": "accepted"}]}),
                encoding="utf-8",
            )

            payload = build_chess_pgn_review(out, glyph_mapping_file=mapping)

            rows = [
                json.loads(line)
                for line in (out / "review" / "pgn_lattice_review.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(payload["ocr_token_mappings_loaded"], 1)
            self.assertEqual(payload["ocr_token_mappings_applied"], 1)
            self.assertEqual(rows[0]["unmapped_token_blockers"], [])
            self.assertEqual(rows[0]["status"], "accepted")
            self.assertIn("1. e4", rows[0]["pgn"])
            self.assertTrue((out / "reports" / "pgn_lattice_eval.json").is_file())
            self.assertTrue((out / "reports" / "chess_quality_dashboard.html").is_file())
            self.assertTrue((out / "review" / "pgn_replay_blockers_top10.md").is_file())
            self.assertTrue((out / "review" / "iteration_status.md").is_file())

    def test_pgn_review_blocks_diagram_source_without_accepted_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page": 1,
                                "diagrams": [
                                    {
                                        "id": "p001_d001",
                                        "caption": "Diagram 1-1",
                                        "page": 1,
                                        "validation_status": "needs-human-review",
                                        "fen": "",
                                    }
                                ],
                            }
                        ],
                        "pgn_records": [
                            {
                                "id": "pgn_1",
                                "logical_page": 1,
                                "source_page": 1,
                                "label": "Diagram 1-1",
                                "raw_text": "1. e4",
                                "visible_review_text": "1. e4",
                                "warnings": [],
                                "status": "needs-human-review",
                                "pgn": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_chess_pgn_review(out)
            rows = [
                json.loads(line)
                for line in (out / "review" / "pgn_lattice_review.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(payload["accepted_pgn"], 0)
            self.assertEqual(rows[0]["status"], "needs_review")
            self.assertTrue(rows[0]["requires_source_fen"])
            self.assertIn("source_fen_not_accepted", rows[0]["warnings"])

    def test_ai_fen_candidates_are_review_only_and_do_not_mark_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop = out / "assets" / "diagrams" / "p001_d001.png"
            crop.parent.mkdir(parents=True)
            Image.new("RGB", (96, 96), "white").save(crop)
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page": 1,
                                "diagrams": [
                                    {
                                        "id": "p001_d001",
                                        "page": 1,
                                        "caption": "Diagram 1-1",
                                        "image_path": "assets/diagrams/p001_d001.png",
                                        "validation_status": "needs-human-review",
                                        "fen": "",
                                    }
                                ],
                            }
                        ],
                        "pgn_records": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_ai_fen_candidates(out, provider=_FakeFenProvider())

            rows = [
                json.loads(line)
                for line in (out / "review" / "ai_fen_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            queue_rows = [
                json.loads(line)
                for line in (out / "review" / "ai_verified_candidate_queue.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            source_book = json.loads((out / "data" / "book.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["accepted_fen_changed"], 0)
            self.assertEqual(payload["ai_validated_candidate_count"], 1)
            self.assertEqual(payload["deterministic_valid"], 0)
            self.assertEqual(payload["verified_candidate_queue_count"], 0)
            self.assertEqual(rows[0]["status"], "ai_validated_candidate")
            self.assertTrue(rows[0]["deterministic_validation"]["valid"])
            self.assertEqual(queue_rows, [])
            self.assertEqual(source_book["pages"][0]["diagrams"][0]["validation_status"], "needs-human-review")
            self.assertTrue((out / "reports" / "ai_fen_candidates_eval.json").is_file())
            self.assertTrue((out / "reports" / "ai_cost_report.json").is_file())

    def test_ai_fen_candidate_conflicts_with_verified_label_stays_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop = out / "assets" / "diagrams" / "p010_d002.png"
            crop.parent.mkdir(parents=True)
            Image.new("RGB", (96, 96), "white").save(crop)
            (out / "data").mkdir(parents=True)
            (out / "review").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "page": 10,
                                "diagrams": [
                                    {
                                        "id": "p010_d002",
                                        "page": 10,
                                        "caption": "Diagram p010_d002",
                                        "image_path": "assets/diagrams/p010_d002.png",
                                        "validation_status": "needs-human-review",
                                        "fen": "",
                                    }
                                ],
                            }
                        ],
                        "pgn_records": [],
                    }
                ),
                encoding="utf-8",
            )
            (out / "review" / "fen_verified_labels.jsonl").write_text(
                json.dumps(
                    {
                        "diagram_id": "p010_d002",
                        "fen": "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1",
                        "crop_path": str(crop),
                        "label_status": "verified",
                        "verified_by": "unit-test",
                        "verified_at": "2026-06-14",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_ai_fen_candidates(out, provider=_FakeFenProvider())
            rows = [
                json.loads(line)
                for line in (out / "review" / "ai_fen_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            queue_text = (out / "review" / "ai_verified_candidate_queue.jsonl").read_text(encoding="utf-8")

        self.assertEqual(payload["ai_cv_conflict"], 1)
        self.assertEqual(payload["verified_candidate_queue_count"], 0)
        self.assertEqual(rows[0]["status"], "ai_cv_conflict")
        self.assertNotEqual(rows[0]["ai_fen_candidate"], rows[0]["verified_fen_candidate"])
        self.assertFalse(rows[0]["verified_label_agrees"])
        self.assertEqual(queue_text, "")

    def test_ai_fen_candidates_dry_run_writes_request_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop = out / "assets" / "diagrams" / "p001_d001.png"
            crop.parent.mkdir(parents=True)
            Image.new("RGB", (96, 96), "white").save(crop)
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps({"pages": [{"page": 1, "diagrams": [{"id": "p001_d001", "page": 1, "image_path": "assets/diagrams/p001_d001.png"}]}]}),
                encoding="utf-8",
            )

            payload = build_ai_fen_candidates(out, dry_run=True)
            rows = [
                json.loads(line)
                for line in (out / "review" / "ai_fen_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertTrue(payload["dry_run"])
            self.assertEqual(rows[0]["status"], "ai_suggested")
            self.assertFalse(rows[0]["input_image_uploaded"])
            self.assertEqual(rows[0]["reason"], "dry_run_request_manifest")

    def test_ai_pgn_candidates_are_review_only_even_when_replay_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps(
                    {
                        "pages": [],
                        "pgn_records": [
                            {
                                "id": "pgn_1",
                                "logical_page": 1,
                                "source_page": 1,
                                "label": "Notation 1",
                                "raw_text": "1. @e4",
                                "visible_review_text": "1. @e4",
                                "warnings": ["unmapped_chess_glyphs"],
                                "status": "needs-human-review",
                                "pgn": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_ai_pgn_candidates(
                out,
                deepseek_provider=_FakeDeepSeekProvider(),
                pgn_provider=_FakePgnProvider(),
            )
            rows = [
                json.loads(line)
                for line in (out / "review" / "ai_pgn_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            games_pgn = (out / "data" / "games.pgn").read_text(encoding="utf-8")
            clusters = json.loads((out / "review" / "deepseek_glyph_clusters.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["accepted_pgn_changed"], 0)
            self.assertEqual(payload["deterministic_replay_clean"], 1)
            self.assertEqual(rows[0]["status"], "deterministic_valid")
            self.assertTrue(rows[0]["deterministic_replay_clean"])
            self.assertEqual(games_pgn, "")
            self.assertEqual(clusters["candidate_mapping_count"], 1)
            self.assertTrue((out / "review" / "gpt_pgn_repair_candidates.jsonl").is_file())

    def test_ai_quality_eval_aggregates_candidate_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            (out / "data").mkdir(parents=True)
            (out / "data" / "book.json").write_text(
                json.dumps({"pages": [], "pgn_records": []}),
                encoding="utf-8",
            )
            (out / "reports").mkdir(parents=True)
            (out / "review").mkdir(parents=True)
            (out / "reports" / "ai_fen_candidates_eval.json").write_text(
                json.dumps({"status": "ok", "diagram_count": 1, "ai_suggested": 1, "ai_cv_agree": 0, "ai_cv_conflict": 0, "verified_candidate_queue_count": 0}),
                encoding="utf-8",
            )
            (out / "reports" / "ai_pgn_candidates_eval.json").write_text(
                json.dumps({"status": "ok", "records_sent_for_gpt_repair": 1, "ai_suggested_pgn": 1, "deterministic_replay_clean": 0}),
                encoding="utf-8",
            )
            (out / "review" / "deepseek_glyph_clusters.json").write_text(
                json.dumps({"status": "reviewed", "cluster_count": 2, "candidate_mapping_count": 1}),
                encoding="utf-8",
            )

            payload = build_ai_assisted_quality_eval(out)
            dashboard = json.loads((out / "reports" / "chess_quality_dashboard.json").read_text(encoding="utf-8"))

            self.assertFalse(payload["accepted_changed_by_ai"])
            self.assertEqual(payload["ai_fen"]["ai_suggested"], 1)
            self.assertEqual(payload["ai_pgn"]["ai_suggested_pgn"], 1)
            self.assertEqual(dashboard["ai_assisted_status"], "ok")

    def test_run_all_generates_expected_static_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_minimal_study_pdf(pdf_path)
            html_path = root / "current.html"
            html_path.write_text('<section class="chess-book-page" data-page="1"></section>', encoding="utf-8")
            out = root / "out"

            report = run_chess_study_export(
                pdf_path,
                html_path=html_path,
                out_dir=out,
                diagram_pages=1,
                diagram_page_ranges="2-3",
                diagram_dpi=96,
                min_grid_confidence=0.95,
            )

            required = [
                "index.html",
                "standalone.html",
                "standalone_audit.html",
                "kindle.html",
                "book.pgn",
                "games_with_comments.pgn",
                "book_text.md",
                "book_text.jsonl",
                "pages.jsonl",
                "pages_summary.json",
                "diagrams.jsonl",
                "diagrams.csv",
                "notation_fragments.jsonl",
                "notation_fragments.csv",
                "audit_summary.json",
                "audit_report.md",
                "positions.json",
                "chapters.json",
                "exercises.json",
                "final_test.json",
                "qa_report.json",
                "qa_report.html",
                "styles.css",
                "app.js",
                "data/book.json",
                "data/diagrams.json",
                "data/artifact_manifest.json",
                "data/games.pgn",
                "reports/conversion-audit.md",
                "reports/source_html_quality_gate.json",
                "reports/chess_reader/semantic_book.json",
                "reports/chess_reader/semantic_book.md",
                "reports/chess_reader/chess_exercises.json",
                "reports/fen-review.csv",
                "reports/pgn-review.csv",
                "reports/ocr-issues.md",
            ]
            for filename in required:
                self.assertTrue((out / filename).is_file(), filename)
            self.assertTrue((out / "assets" / "page_images").is_dir())
            self.assertTrue((out / "assets" / "diagram_crops").is_dir())
            self.assertTrue((out / "assets" / "diagram_svg").is_dir())
            self.assertTrue((out / "assets" / "diagram_png").is_dir())
            self.assertTrue((out / "diagrams" / "source").is_dir())
            self.assertTrue((out / "review").is_dir())
            self.assertTrue((out / "review" / "glyph_mapping_candidates.json").is_file())
            self.assertTrue((out / "review" / "glyph_mapping_review.html").is_file())

            qa = json.loads((out / "qa_report.json").read_text(encoding="utf-8"))
            audit = json.loads((out / "audit_summary.json").read_text(encoding="utf-8"))
            pages_summary = json.loads((out / "pages_summary.json").read_text(encoding="utf-8"))
            semantic_book = json.loads(
                (out / "reports" / "chess_reader" / "semantic_book.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["summary"]["expected_chapters"], 24)
            self.assertEqual(qa["summary"]["expected_chapters"], 24)
            self.assertEqual(semantic_book["schema"], chess_study_export.SEMANTIC_BOOK_SCHEMA)
            self.assertIn("pages", semantic_book)
            self.assertIn("exercises", semantic_book)
            self.assertIn(qa["status"], {"PASS", "PASS_WITH_REVIEW_ITEMS", "FAIL"})
            self.assertEqual(pages_summary["page_count"], 27)
            self.assertGreater(pages_summary["pages_with_extractable_text"], 0)
            self.assertEqual(audit["page_images"], 0)
            self.assertIn("strict_diagrams_total", audit)
            self.assertIn("low_confidence_review_candidates", audit)
            self.assertEqual(audit["sampled_diagram_pages"], [2, 3])
            self.assertEqual(audit["strict_diagrams_sampled"], 0)
            self.assertEqual(audit["low_confidence_candidates_sampled"], 0)
            self.assertEqual(audit["ordering"], "page-y-x-reading_order")
            self.assertEqual(pages_summary["page_images"], 0)
            self.assertEqual((out / "book.pgn").read_text(encoding="utf-8"), "")
            source_book = json.loads((out / "data" / "book.json").read_text(encoding="utf-8"))
            artifact_manifest = json.loads((out / "data" / "artifact_manifest.json").read_text(encoding="utf-8"))
            health_gate = json.loads((out / "reports" / "final_reader_health_gate.json").read_text(encoding="utf-8"))
            source_index = (out / "index.html").read_text(encoding="utf-8")
            self.assertEqual(source_book["summary"]["html_pages"], 1)
            source_gate = json.loads((out / "reports" / "source_html_quality_gate.json").read_text(encoding="utf-8"))
            self.assertTrue(source_gate["used_as_final_reader"])
            self.assertEqual(artifact_manifest["artifact_type"], "final_pdf_two_crop_reader")
            self.assertEqual(artifact_manifest["pipeline_mode"], "source_html_semantic_reader")
            self.assertEqual(artifact_manifest["source_html_quality_gate"]["decision"], "use_source_html_as_final_reader")
            self.assertEqual(artifact_manifest["side_unknown_count"], 0)
            self.assertEqual(health_gate["artifact_type"], "final_pdf_two_crop_reader")
            self.assertEqual(health_gate["empty_img_src_count"], 0)
            self.assertIn("Artifact type:", source_index)
            self.assertIn('data-artifact-type="final_pdf_two_crop_reader"', source_index)
            self.assertIn('data-pipeline-mode="source_html_semantic_reader"', source_index)
            self.assertNotIn("localhost", source_index)
            self.assertNotIn("fen_not_recognized", source_index)

    def test_semantic_reader_uses_component_statuses_not_raw_technical_tokens(self) -> None:
        source_index = chess_study_export._semantic_source_index_html(
            {
                "title": "Reader split sample",
                "summary": {"html_pages": 1, "diagrams_total": 1, "fen_accepted": 0, "accepted_pgn": 0},
                "artifact_manifest": {
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "source_html_semantic_reader",
                },
                "chapters": [{"title": "Chapter 1", "start_page": 1}],
                "pages": [
                    {
                        "page": 1,
                        "text_chunks": [
                            {"text": "A clean reader paragraph.", "reading_order": 1},
                            {"text": "fen_not_recognized board_crop_quality=fail", "reading_order": 2},
                        ],
                        "diagrams": [
                            {
                                "id": "p001_d001",
                                "page": 1,
                                "reading_order": 3,
                                "caption": "Diagram 1",
                                "validation_status": "needs-human-review",
                                "review_reason": "fen_not_recognized marker_crop_quality=fail",
                                "side_to_move": "",
                                "fen": "",
                                "fen_candidate": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                                "image_path": "",
                            }
                        ],
                        "pgn_records": [],
                    }
                ],
            }
        )

        self.assertIn("Reader", source_index)
        self.assertIn("Study", source_index)
        self.assertIn("Audit", source_index)
        self.assertIn("FEN unavailable", source_index)
        self.assertIn("Unknown side to move", source_index)
        self.assertIn("Send to review", source_index)
        self.assertNotIn("Side to move: unknown", source_index)
        self.assertNotIn("fen_not_recognized", source_index)
        self.assertNotIn("board_crop_quality=fail", source_index)
        self.assertNotIn("marker_crop_quality=fail", source_index)

    def test_source_reader_resolves_diagram_assets_and_uses_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_minimal_study_pdf(pdf_path)
            source_asset_dir = root / "assets"
            source_asset_dir.mkdir()
            Image.new("RGB", (16, 16), "white").save(source_asset_dir / "relative-diagram.png")
            data_buffer = io.BytesIO()
            Image.new("RGB", (12, 12), "white").save(data_buffer, format="PNG")
            data_uri = "data:image/png;base64," + base64.b64encode(data_buffer.getvalue()).decode("ascii")
            html_path = root / "current.html"
            html_path.write_text(
                f"""
                <section class="chess-book-page" data-page="1">
                  <div class="book-text" data-reading-order="1" style="left:10px;top:10px;width:90px;height:12px">Diagram 1-1 White to move</div>
                  <div class="book-diagram" data-reading-order="2" style="left:10px;top:30px;width:80px;height:80px"><img src="assets/relative-diagram.png" alt="Diagram 1-1"></div>
                  <div class="book-text" data-reading-order="3" style="left:120px;top:10px;width:90px;height:12px">Diagram 1-2 White to move</div>
                  <div class="book-diagram" data-reading-order="4" style="left:120px;top:30px;width:80px;height:80px"><img src="{data_uri}" alt="Diagram 1-2"></div>
                  <div class="book-text" data-reading-order="5" style="left:230px;top:10px;width:90px;height:12px">Diagram 1-3 White to move</div>
                  <div class="book-diagram" data-reading-order="6" style="left:230px;top:30px;width:80px;height:80px"><img src="" alt="Diagram 1-3"></div>
                </section>
                """,
                encoding="utf-8",
            )
            out = root / "out"

            run_chess_study_export(
                pdf_path,
                html_path=html_path,
                out_dir=out,
                diagram_pages=0,
                diagram_dpi=96,
                min_grid_confidence=0.95,
            )

            diagrams_payload = json.loads((out / "data" / "diagrams.json").read_text(encoding="utf-8"))
            source_book = json.loads((out / "data" / "book.json").read_text(encoding="utf-8"))
            exercise_model = json.loads(
                (out / "reports" / "chess_reader" / "chess_exercises.json").read_text(encoding="utf-8")
            )
            gate = json.loads((out / "reports" / "source_html_quality_gate.json").read_text(encoding="utf-8"))
            final_index = (out / "index.html").read_text(encoding="utf-8")

            self.assertTrue((out / "assets" / "diagrams" / "p001_d001.png").is_file())
            self.assertTrue((out / "assets" / "diagrams" / "p001_d002.png").is_file())
            self.assertEqual(diagrams_payload["summary"]["resolved_diagram_image_count"], 2)
            self.assertEqual(diagrams_payload["summary"]["empty_diagram_image_count"], 1)
            self.assertEqual(source_book["summary"]["resolved_diagram_image_count"], 2)
            self.assertEqual(source_book["summary"]["empty_diagram_image_count"], 1)
            self.assertEqual(exercise_model["summary"]["exercise_count"], 3)
            self.assertEqual(
                [item["exercise_id"] for item in exercise_model["exercises"]],
                ["ex_1_1", "ex_1_2", "ex_1_3"],
            )
            self.assertEqual(exercise_model["exercises"][2]["diagram"]["asset_missing_reason"], "empty_src")
            self.assertEqual(gate["summary"]["resolved_diagram_image_count"], 2)
            self.assertEqual(gate["summary"]["empty_diagram_image_count"], 1)
            self.assertNotIn('src=""', final_index)
            self.assertIn('data-asset-missing-reason="empty_src"', final_index)

    def test_resolved_source_html_without_side_evidence_stays_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_minimal_study_pdf(pdf_path)
            source_asset_dir = root / "assets"
            source_asset_dir.mkdir()
            Image.new("RGB", (16, 16), "white").save(source_asset_dir / "diagram-1.png")
            Image.new("RGB", (16, 16), "white").save(source_asset_dir / "diagram-2.png")
            html_path = root / "current.html"
            html_path.write_text(
                """
                <section class="chess-book-page" data-page="1">
                  <div class="book-text" data-reading-order="1" style="left:10px;top:10px;width:90px;height:12px">Diagram 1-1</div>
                  <div class="book-diagram" data-reading-order="2" style="left:10px;top:30px;width:80px;height:80px"><img src="assets/diagram-1.png" alt="Diagram 1-1"></div>
                  <div class="book-text" data-reading-order="3" style="left:120px;top:10px;width:90px;height:12px">Diagram 1-2</div>
                  <div class="book-diagram" data-reading-order="4" style="left:120px;top:30px;width:80px;height:80px"><img src="assets/diagram-2.png" alt="Diagram 1-2"></div>
                </section>
                """,
                encoding="utf-8",
            )
            out = root / "out"

            run_chess_study_export(
                pdf_path,
                html_path=html_path,
                out_dir=out,
                diagram_pages=0,
                diagram_dpi=96,
                min_grid_confidence=0.95,
            )

            gate = json.loads((out / "reports" / "source_html_quality_gate.json").read_text(encoding="utf-8"))
            source_book_exists = (out / "data" / "book.json").exists()
            evidence_html = (out / gate["source_html_evidence_path"]).read_text(encoding="utf-8")
            evidence_manifest = json.loads((out / "reports" / "source_html_evidence_manifest.json").read_text(encoding="utf-8"))

        self.assertFalse(gate["used_as_final_reader"])
        self.assertTrue(gate["source_html_evidence_only"])
        self.assertEqual(gate["decision"], "reject_degraded_source_html")
        self.assertEqual(gate["source_html_evidence_manifest_path"], "reports/source_html_evidence_manifest.json")
        self.assertIn("source_html_lacks_fen_marker_or_crop_evidence", gate["reasons"])
        self.assertIn("all_or_most_diagrams_have_unknown_side_and_no_accepted_fen", gate["reasons"])
        self.assertEqual(gate["summary"]["resolved_diagram_image_count"], 2)
        self.assertEqual(gate["summary"]["side_unknown_count"], 2)
        self.assertEqual(evidence_manifest["artifact_type"], "source_html_evidence_only")
        self.assertEqual(evidence_manifest["source_html_quality_gate"]["decision"], "reject_degraded_source_html")
        self.assertEqual(evidence_manifest["side_unknown_count"], 2)
        self.assertIn("Artifact type:", evidence_html)
        self.assertIn("source_html_evidence_only", evidence_html)
        self.assertIn('data-artifact-type="source_html_evidence_only"', evidence_html)
        self.assertIn("Evidence-only source HTML", evidence_html)
        self.assertFalse(source_book_exists)

    def test_run_all_keeps_degraded_source_html_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_minimal_study_pdf(pdf_path)
            html_path = root / "current.html"
            html_path.write_text(
                """
                <section class="chess-book-page" data-page="1">
                  <div class="book-text" data-reading-order="1" style="left:10px;top:10px;width:80px;height:12px">Diagram 1-1</div>
                  <div class="book-diagram" data-reading-order="2" style="left:10px;top:30px;width:80px;height:80px"><img src="" alt=""></div>
                  <div class="book-text" data-reading-order="3" style="left:120px;top:10px;width:80px;height:12px">Diagram 1-2</div>
                  <div class="book-diagram" data-reading-order="4" style="left:120px;top:30px;width:80px;height:80px"><img src="http://127.0.0.1:5001/artifact/missing.png" alt=""></div>
                </section>
                """,
                encoding="utf-8",
            )
            out = root / "out"

            run_chess_study_export(
                pdf_path,
                html_path=html_path,
                out_dir=out,
                diagram_pages=0,
                diagram_dpi=96,
                min_grid_confidence=0.95,
            )

            gate = json.loads((out / "reports" / "source_html_quality_gate.json").read_text(encoding="utf-8"))
            final_index = (out / "index.html").read_text(encoding="utf-8")
            final_manifest = json.loads((out / "data" / "artifact_manifest.json").read_text(encoding="utf-8"))
            evidence_file_exists = (out / gate["source_html_evidence_path"]).is_file()
            evidence_manifest = json.loads((out / "reports" / "source_html_evidence_manifest.json").read_text(encoding="utf-8"))
            source_book_exists = (out / "data" / "book.json").exists()

        self.assertFalse(gate["used_as_final_reader"])
        self.assertTrue(gate["source_html_evidence_only"])
        self.assertEqual(gate["decision"], "reject_degraded_source_html")
        self.assertIn("diagram_image_sources_degraded", gate["reasons"])
        self.assertIn("source_html_lacks_fen_marker_or_crop_evidence", gate["reasons"])
        self.assertEqual(gate["summary"]["diagrams_total"], 2)
        self.assertEqual(gate["summary"]["source_img_empty_count"], 1)
        self.assertEqual(gate["summary"]["source_img_localhost_count"], 1)
        self.assertEqual(gate["summary"]["fen_accepted"], 0)
        self.assertEqual(gate["summary"]["side_unknown_count"], 2)
        self.assertEqual(final_manifest["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(final_manifest["pipeline_mode"], "pdf_two_crop_reader")
        self.assertEqual(final_manifest["source_html_quality_gate"]["decision"], "reject_degraded_source_html")
        self.assertEqual(evidence_manifest["artifact_type"], "source_html_evidence_only")
        self.assertEqual(evidence_manifest["empty_img_src_count"], 1)
        self.assertIn("Artifact type:", final_index)
        self.assertIn('data-artifact-type="final_pdf_two_crop_reader"', final_index)
        self.assertIn('data-source-html-gate-decision="reject_degraded_source_html"', final_index)
        self.assertTrue(evidence_file_exists)
        self.assertFalse(source_book_exists)
        self.assertNotIn("FEN recognition is not available for this extracted diagram crop yet.", final_index)

    def test_artifact_manifest_records_commit_sha_fallback_reason(self) -> None:
        with patch("chess_study_export._current_git_commit", return_value=""):
            manifest = chess_study_export._build_artifact_manifest(
                artifact_type="final_pdf_two_crop_reader",
                pipeline_mode="pdf_two_crop_reader",
                summary={"diagrams_total": 1, "fen_accepted": 0},
            )

        self.assertEqual(manifest["commit_sha"], "")
        self.assertEqual(manifest["commit_sha_reason"], "git_rev_parse_head_unavailable")
        self.assertEqual(manifest["artifact_type"], "final_pdf_two_crop_reader")

    def test_masterkindle_profile_fails_when_quality_thresholds_are_not_met(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_minimal_study_pdf(pdf_path)
            out = root / "out"

            report = run_chess_study_export(
                pdf_path,
                out_dir=out,
                diagram_pages=1,
                diagram_dpi=96,
                min_grid_confidence=0.95,
                quality_profile="masterkindle",
            )
            pages_summary = json.loads((out / "pages_summary.json").read_text(encoding="utf-8"))

            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(
                any(problem.get("code") == "quality_threshold_not_met" for problem in report["problems"])
            )
            self.assertEqual(pages_summary["page_images"], 27)
            self.assertEqual(report["summary"]["page_images"], 27)


if __name__ == "__main__":
    unittest.main()
