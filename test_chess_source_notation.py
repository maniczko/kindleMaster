from __future__ import annotations

import unittest

from chess_source_notation import (
    SourceGlyph,
    _group_glyphs_into_lines,
    load_source_glyph_maps,
    looks_like_decoded_notation_line,
    normalize_decoded_notation,
    validate_san_line,
)


class ChessSourceNotationTests(unittest.TestCase):
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
                for glyph in (79, 106, 112, 235, 269)
            },
            {79: "B", 106: "Q", 112: "K", 235: "B", 269: "Q"},
        )
        self.assertEqual(
            {
                glyph: maps[alternate_fingerprint]["glyphs"][glyph]
                for glyph in (29, 31, 65, 102, 137)
            },
            {29: "K", 31: "Q", 65: "B", 102: "R", 137: "N"},
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


if __name__ == "__main__":
    unittest.main()
