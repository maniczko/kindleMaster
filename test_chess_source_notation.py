from __future__ import annotations

import unittest

from chess_source_notation import (
    SourceGlyph,
    SourceNotationLine,
    _group_glyphs_into_lines,
    _order_notation_lines_by_columns,
    _printed_exercise_label,
    _resolve_unambiguous_label_candidates,
    _source_glyphs,
    load_source_glyph_maps,
    looks_like_decoded_notation_line,
    normalize_decoded_notation,
    replay_source_notation_blocks,
    segment_source_notation_blocks,
    validate_san_line,
)


class _TracePage:
    def __init__(
        self,
        raw: list[tuple[int, str]],
        *,
        font_name: str = "Fd545813",
    ) -> None:
        self._raw = raw
        self._font_name = font_name

    def get_texttrace(self) -> list[dict[str, object]]:
        chars = []
        x = 10.0
        source_origin = None
        for glyph_id, value in self._raw:
            if glyph_id >= 0:
                source_origin = (x, 20.0)
            origin = source_origin if glyph_id < 0 else (x, 20.0)
            chars.append(
                (
                    ord(value),
                    glyph_id,
                    origin,
                    (x, 10.0, x + 5.0, 22.0),
                )
            )
            if glyph_id >= 0:
                x += 6.0
        return [{"font": self._font_name, "chars": chars}]


class ChessSourceNotationTests(unittest.TestCase):
    def test_printed_exercise_label_rejects_results_and_references(
        self,
    ) -> None:
        self.assertEqual(_printed_exercise_label("> Ex. 3-2-("), "3-2")
        self.assertEqual(
            _printed_exercise_label("[[gid:519]]Ex.18-1<'"),
            "18-1",
        )
        self.assertEqual(_printed_exercise_label("0-1"), "")
        self.assertEqual(_printed_exercise_label("1-0"), "")
        self.assertEqual(_printed_exercise_label("Diagram Ex. 3-2"), "")
        self.assertEqual(_printed_exercise_label("See also Ex. 4-2"), "")

    def test_vision_label_requires_independent_deterministic_consensus(
        self,
    ) -> None:
        assignments = [
            {
                "exercise_id": "1-1",
                "source_page": 14,
                "status": "exact",
                "diagram_id": "diagram-1",
                "source": "source_text_geometry",
                "auto_accepted": True,
            },
            {
                "exercise_id": "1-2",
                "source_page": 14,
                "status": "candidate",
                "diagram_id": "diagram-2",
                "source": "tesseract_label_crop",
                "confidence": 0.94,
                "auto_accepted": False,
                "blockers": [
                    "vision_candidate_requires_deterministic_consensus"
                ],
            },
        ]
        diagrams = [
            {"id": "diagram-1", "bbox": [10, 40, 120, 150]},
            {"id": "diagram-2", "bbox": [10, 180, 120, 290]},
        ]

        _resolve_unambiguous_label_candidates(
            assignments,
            diagrams,
            page_widths={14: 600.0},
            known_exercise_ids={"1-1", "1-2"},
        )

        candidate = assignments[1]
        self.assertEqual(candidate["status"], "consensus")
        self.assertTrue(candidate["auto_accepted"])
        self.assertNotIn(
            "vision_candidate_requires_deterministic_consensus",
            candidate["blockers"],
        )

    def test_vision_label_without_source_chapter_anchor_stays_candidate(
        self,
    ) -> None:
        assignments = [
            {
                "exercise_id": "1-2",
                "source_page": 14,
                "status": "candidate",
                "diagram_id": "diagram-2",
                "source": "tesseract_label_crop",
                "confidence": 0.99,
                "auto_accepted": False,
                "blockers": [
                    "vision_candidate_requires_deterministic_consensus"
                ],
            }
        ]

        _resolve_unambiguous_label_candidates(
            assignments,
            [{"id": "diagram-2", "bbox": [10, 180, 120, 290]}],
            page_widths={14: 600.0},
            known_exercise_ids={"1-2"},
        )

        self.assertEqual(assignments[0]["status"], "candidate")
        self.assertFalse(assignments[0]["auto_accepted"])

    def test_default_source_map_is_font_fingerprinted_and_fail_closed(self) -> None:
        maps = load_source_glyph_maps()
        fingerprint = (
            "025747eea238431fe09c0b7abadf4e1a8f2c32b65fe7ecba99369c498bd99942"
        )

        self.assertIn(fingerprint, maps)
        self.assertTrue(maps[fingerprint]["strict"])
        self.assertGreaterEqual(len(maps[fingerprint]["glyphs"]), 200)
        self.assertEqual(maps[fingerprint]["glyphs"][240], "1.R")
        self.assertEqual(maps[fingerprint]["glyphs"][198], "Q")
        prose_fingerprint = (
            "98d071429d809c388c0b4fd7f91cff5e66946b72d5bb6b62e32d941957e78849"
        )
        alternate_fingerprint = (
            "450ca9059c457586d7f06423620ec504c246b038a348eb279a9f03744d24276f"
        )
        self.assertEqual(
            {
                glyph: maps[prose_fingerprint]["glyphs"][glyph]
                for glyph in (55, 59, 341, 649, 1225, 1273)
            },
            {
                55: "+",
                59: "1",
                341: "Q",
                649: "R",
                1225: "K",
                1273: "N",
            },
        )
        self.assertEqual(
            {
                glyph: maps[alternate_fingerprint]["glyphs"][glyph]
                for glyph in (29, 31, 65, 102, 137)
            },
            {29: "K", 31: "Q", 65: "B", 102: "R", 137: "N"},
        )
        self.assertEqual(
            maps[alternate_fingerprint]["sequences"],
            (
                ((618, 467, 275), "1.R"),
                ((813, 826), ".K"),
            ),
        )

    def test_groups_source_bound_glyphs_and_keeps_unknown_gid_blocking(self) -> None:
        glyphs = [
            self._glyph(glyph_id=240, raw="U", decoded="1.R", x=10),
            self._glyph(glyph_id=-1, raw="\ufffd", decoded="", x=10),
            self._glyph(glyph_id=11, raw="d", decoded="d", x=24),
            self._glyph(glyph_id=29, raw="8", decoded="8", x=30),
            self._glyph(glyph_id=999, raw="?", decoded="[[gid:999]]", x=36),
        ]

        lines = _group_glyphs_into_lines(
            glyphs,
            page_number=9,
            baseline_tolerance=3.2,
        )

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].raw_text, "U\ufffdd8?")
        self.assertEqual(lines[0].decoded_text, "1.Rd8[[gid:999]]")
        self.assertEqual(lines[0].status, "needs_review")
        self.assertEqual(
            lines[0].blockers,
            (
                "unmapped_source_glyph:"
                + "a" * 64
                + ":999",
            ),
        )

    def test_printable_but_corrupt_ligature_remains_blocking(self) -> None:
        fingerprint = (
            "98d071429d809c388c0b4fd7f91cff5e66946b72d5bb6b62e32d941957e78849"
        )
        page = _TracePage(
            [
                (1, "1"),
                (2, "."),
                (3, "."),
                (4, "."),
                (5, "K"),
                (6, "g"),
                (7, "7"),
                (8, " "),
                (9, "2"),
                (10, "."),
                (999, "l"),
                (-1, "t"),
                (-1, "J"),
                (11, "x"),
                (12, "f"),
                (13, "8"),
            ]
        )
        glyphs = _source_glyphs(
            page,
            font_fingerprints={"Fd545813": fingerprint},
            mappings=load_source_glyph_maps(),
            clip=None,
        )

        lines = _group_glyphs_into_lines(
            glyphs,
            page_number=16,
            baseline_tolerance=3.2,
        )

        self.assertEqual(lines[0].status, "needs_review")
        self.assertEqual(
            lines[0].blockers,
            ("suspicious_decoded_ligature:ltJ",),
        )

    def test_page_nine_lines_replay_from_the_reconstructed_positions(self) -> None:
        diagram_one_fen = (
            "6k1/8/p7/r1r2Pp1/3R2Pp/1p6/1P4P1/3R2K1 w - - 0 1"
        )
        diagram_two_fen = (
            "r2r2k1/pb6/1p2pQ2/n1qP4/2P5/8/P4PPP/3RR1K1 w - - 0 1"
        )
        lines = [
            (
                diagram_one_fen,
                "1. Rd8+ Kg7 2. R1d7+ Kf6 3. Rf8+ Ke5 "
                "4. Re8+ Kf4 5. Rd4+ Kg3 6. Re3#",
                11,
            ),
            (
                diagram_one_fen,
                "1. Rd8+ Kg7 2. R1d7+ Kh6 3. Rh8#",
                5,
            ),
            (
                diagram_two_fen,
                "1. Re5 Rd7 2. Rg5+ Kh7 3. Qg6+ Kh8 "
                "4. Rh5 Rh7 5. Rxh7#",
                9,
            ),
            (
                diagram_two_fen,
                "1. Re5 Rf8 2. Qg6+ Kh8 3. Rh5#",
                5,
            ),
            (
                diagram_two_fen,
                "1. Re5 Qc7 2. Rg5+ Kh7 3. Qg6+ Kh8 "
                "4. Rh5 Qh7 5. Rxh7#",
                9,
            ),
        ]

        for fen, notation, move_count in lines:
            with self.subTest(notation=notation):
                result = validate_san_line(fen, notation)
                self.assertEqual(result["status"], "valid")
                self.assertEqual(result["moves_replayed"], move_count)
                self.assertIn("CHECKMATE", result["outcome"])

    def test_normalizes_only_source_decoded_notation_spacing(self) -> None:
        self.assertEqual(
            normalize_decoded_notation(
                "If 4 ... Kf6, then 5.Re6#. 3 ...K h8 5.Rd4 +"
            ),
            "If 4...Kf6, then 5.Re6#. 3...Kh8 5.Rd4+",
        )
        self.assertEqual(
            normalize_decoded_notation("1...Kg82.Rxg7#"),
            "1...Kg8 2.Rxg7#",
        )

    def test_selects_moves_without_treating_board_coordinates_as_notation(
        self,
    ) -> None:
        self.assertTrue(
            looks_like_decoded_notation_line(
                "1.Rd8+ Kg7 2.R1d7+ Kf6"
            )
        )
        self.assertTrue(
            looks_like_decoded_notation_line(
                "b) 1...Rf8 2.Qg6+ Kh8 3.Rh5#"
            )
        )
        self.assertTrue(
            looks_like_decoded_notation_line(
                "12...[[gid:999]]e7 13.Rxe7+"
            )
        )
        for notation in (
            "1.Qxh6+! gxh6",
            "2.Rhxh6#",
            "Or 2.gxh3 Qxh2#.",
            "23...Rde8+!",
        ):
            with self.subTest(notation=notation):
                self.assertTrue(looks_like_decoded_notation_line(notation))
        self.assertFalse(
            looks_like_decoded_notation_line("a b c d e f g h")
        )
        self.assertFalse(
            looks_like_decoded_notation_line("White finds a forced mate.")
        )

    def test_separates_board_rank_from_notation_on_the_same_baseline(self) -> None:
        glyphs = [
            self._glyph(glyph_id=103, raw="5", decoded="5", x=32),
            self._glyph(glyph_id=240, raw="U", decoded="1.R", x=220),
            self._glyph(glyph_id=11, raw="d", decoded="d", x=234),
            self._glyph(glyph_id=29, raw="8", decoded="8", x=240),
        ]

        lines = _group_glyphs_into_lines(
            glyphs,
            page_number=9,
            baseline_tolerance=3.2,
        )

        self.assertEqual(
            [line.decoded_text for line in lines],
            ["5", "1.Rd8"],
        )

    def test_separates_adjacent_notation_columns_on_the_same_baseline(
        self,
    ) -> None:
        glyphs = [
            self._glyph(glyph_id=1, raw="1", decoded="1", x=10),
            self._glyph(glyph_id=2, raw=".", decoded=".", x=16),
            self._glyph(glyph_id=3, raw="e", decoded="e4", x=22),
            self._glyph(glyph_id=4, raw="2", decoded="2", x=40),
            self._glyph(glyph_id=5, raw=".", decoded=".", x=46),
            self._glyph(glyph_id=6, raw="Q", decoded="Qh5", x=52),
        ]

        lines = _group_glyphs_into_lines(
            glyphs,
            page_number=16,
            baseline_tolerance=3.2,
        )

        self.assertEqual(
            [line.decoded_text for line in lines],
            ["1.e4", "2.Qh5"],
        )

    def test_decodes_page_sixteen_composite_figurines_by_source_gid(
        self,
    ) -> None:
        fingerprint = (
            "98d071429d809c388c0b4fd7f91cff5e66946b72d5bb6b62e32d941957e78849"
        )
        raw = [
            (59, "l"),
            (87, "."),
            (341, "W"),
            (-1, "i"),
            (204, "x"),
            (61, "h"),
            (42, "7"),
            (55, "t"),
            (202, "?"),
            (2384, " "),
            (1225, "I"),
            (-1, "i"),
            (-1, ">"),
            (204, "x"),
            (61, "h"),
            (42, "7"),
            (2384, " "),
            (40, "2"),
            (12, "."),
            (649, "1"),
            (-1, '"'),
            (-1, "l"),
            (61, "h"),
            (59, "l"),
            (55, "t"),
            (2384, " "),
            (1224, "I"),
            (-1, "i"),
            (-1, ">"),
            (15, "g"),
            (34, "6"),
            (116, "!"),
            (2384, " "),
            (192, "3"),
            (10, "."),
            (1273, "c"),
            (-1, "!"),
            (-1, "L"),
            (-1, "J"),
            (7, "e"),
            (42, "7"),
            (55, "t"),
            (2384, " "),
            (1225, "I"),
            (-1, "i"),
            (-1, ">"),
            (141, "f"),
            (34, "6"),
        ]
        page = _TracePage(raw)
        glyphs = _source_glyphs(
            page,
            font_fingerprints={"Fd545813": fingerprint},
            mappings=load_source_glyph_maps(),
            clip=None,
        )
        lines = _group_glyphs_into_lines(
            glyphs,
            page_number=16,
            baseline_tolerance=3.2,
        )

        self.assertEqual(len(lines), 1)
        self.assertEqual(
            lines[0].decoded_text,
            "1.Qxh7+? Kxh7 2.Rh1+ Kg6! 3.Ne7+ Kf6",
        )
        self.assertTrue(looks_like_decoded_notation_line(lines[0].decoded_text))
        self.assertNotRegex(lines[0].decoded_text, r"Wix|Ii>|c!LJ|1\"l")

    def test_decodes_multi_gid_rook_without_removing_global_punctuation(
        self,
    ) -> None:
        fingerprint = (
            "450ca9059c457586d7f06423620ec504c246b038a348eb279a9f03744d24276f"
        )
        page = _TracePage(
            [
                (618, "U"),
                (467, ":"),
                (275, "!"),
                (26, "h"),
                (17, "8"),
                (18, "t"),
                (20, "!"),
            ],
            font_name="Fd562974",
        )
        glyphs = _source_glyphs(
            page,
            font_fingerprints={"Fd562974": fingerprint},
            mappings=load_source_glyph_maps(),
            clip=None,
        )

        self.assertEqual(
            "".join(glyph.decoded_text for glyph in glyphs),
            "1.Rh8+!",
        )
        self.assertEqual(glyphs[0].status, "source_bound_mapping")
        self.assertEqual(glyphs[1].status, "synthetic_to_unicode_continuation")

    def test_orders_two_column_notation_in_reading_order(self) -> None:
        lines = [
            self._line("1.RightTop", x=260, baseline=20),
            self._line("1.LeftTop", x=20, baseline=25),
            self._line("2.RightBottom", x=260, baseline=40),
            self._line("2.LeftBottom", x=20, baseline=45),
        ]

        ordered = _order_notation_lines_by_columns(lines, page_width=500)

        self.assertEqual(
            [line.decoded_text for line in ordered],
            ["1.LeftTop", "2.LeftBottom", "1.RightTop", "2.RightBottom"],
        )

    def test_segments_exercises_and_carries_notation_to_next_column(self) -> None:
        lines = [
            self._line("Ex. 1-1", x=20, baseline=20),
            self._line("1.e4 e5", x=20, baseline=35),
            self._line("Ex. 1-2", x=20, baseline=100),
            self._line("1.d4 d5", x=20, baseline=115),
            self._line("2.c4 e6", x=280, baseline=20),
            self._line("Ex. 1-3", x=280, baseline=70),
            self._line("1.Nf3 Nf6", x=280, baseline=85),
        ]

        blocks = segment_source_notation_blocks(lines, page_width=500)

        self.assertEqual(
            [block.exercise_id for block in blocks],
            ["1-1", "1-2", "1-3"],
        )
        self.assertEqual(blocks[0].notation_text, "1.e4 e5")
        self.assertEqual(
            blocks[1].notation_text,
            "1.d4 d5\n2.c4 e6",
        )
        self.assertEqual(blocks[2].notation_text, "1.Nf3 Nf6")

    def test_replay_accepts_only_exact_human_verified_fen_binding(
        self,
    ) -> None:
        source_payload = {
            "schema": "kindlemaster.source_bound_chess_notation.v1",
            "pages": {
                "16": {
                    "page_number": 16,
                    "solution_blocks": [
                        {
                            "exercise_id": "1-1",
                            "page_number": 16,
                            "status": "decoded",
                            "decoded_text": "1.e4 e5 2.Nf3 Nc6",
                            "notation_text": "1.e4 e5 2.Nf3 Nc6",
                            "blockers": [],
                        }
                    ],
                }
            },
        }
        diagrams = [
            {
                "id": "diagram-1-1",
                "exercise_id": "1-1",
                "page_number": 14,
                "full_fen": (
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/"
                    "RNBQKBNR w KQkq - 0 1"
                ),
                "fen_human_verified": True,
            }
        ]

        payload = replay_source_notation_blocks(
            source_payload,
            diagrams,
        )
        block = payload["pages"]["16"]["solution_blocks"][0]

        self.assertEqual(block["replay_status"], "accepted")
        self.assertEqual(block["diagram_id"], "diagram-1-1")
        self.assertTrue(block["accepted_pgn"])
        self.assertTrue(block["final_fen"])
        self.assertEqual(
            payload["replay_summary"]["accepted_count"],
            1,
        )
        self.assertFalse(
            payload["replay_summary"]["model_policy"]["ai_may_auto_accept"]
        )

    def test_replay_blocks_untrusted_fen(self) -> None:
        source_payload = {
            "pages": {
                "16": {
                    "solution_blocks": [
                        {
                            "exercise_id": "1-1",
                            "page_number": 16,
                            "decoded_text": "1.e4 e5",
                            "notation_text": "1.e4 e5",
                            "blockers": [],
                        }
                    ]
                }
            }
        }
        diagrams = [
            {
                "id": "diagram-1-1",
                "exercise_id": "1-1",
                "full_fen": (
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/"
                    "RNBQKBNR w KQkq - 0 1"
                ),
            }
        ]

        payload = replay_source_notation_blocks(
            source_payload,
            diagrams,
        )
        block = payload["pages"]["16"]["solution_blocks"][0]

        self.assertEqual(block["replay_status"], "review")
        self.assertIn("missing_trusted_full_fen", block["blockers"])
        self.assertFalse(block["accepted_pgn"])
        self.assertEqual(block["model_route"]["route"], "fen_review")
        self.assertEqual(block["model_route"]["model_tier"], "none")

    @staticmethod
    def _glyph(
        *,
        glyph_id: int,
        raw: str,
        decoded: str,
        x: float,
    ) -> SourceGlyph:
        return SourceGlyph(
            font_name="Fd597605",
            font_fingerprint="a" * 64,
            glyph_id=glyph_id,
            raw_unicode=raw,
            decoded_text=decoded,
            origin=(x, 20.0),
            bbox=(x, 10.0, x + 5.0, 22.0),
            status=(
                "synthetic_to_unicode_continuation"
                if glyph_id < 0
                else (
                    "unmapped_source_glyph"
                    if glyph_id == 999
                    else "source_bound_mapping"
                )
            ),
        )

    @staticmethod
    def _line(
        text: str,
        *,
        x: float,
        baseline: float,
    ) -> SourceNotationLine:
        return SourceNotationLine(
            page_number=16,
            baseline=baseline,
            bbox=(x, baseline - 10, x + 120, baseline + 2),
            raw_text=text,
            decoded_text=text,
            glyphs=(),
            blockers=(),
        )


if __name__ == "__main__":
    unittest.main()
