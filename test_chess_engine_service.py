from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from chess_engine_service import analyze_fen, is_engine_available, normalize_engine_score, resolve_engine_path


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class ChessEngineServiceTests(unittest.TestCase):
    def test_resolve_engine_path_uses_env_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = Path(temp_dir) / "stockfish.exe"
            engine.write_text("", encoding="utf-8")

            result = resolve_engine_path(env={"KINDLEMASTER_STOCKFISH_PATH": str(engine)}, repo_root=temp_dir)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["path"], str(engine))
        self.assertEqual(result["source"], "env:KINDLEMASTER_STOCKFISH_PATH")

    def test_analyze_fen_returns_best_move_score_pv_and_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _write_fake_uci_engine(Path(temp_dir))
            cache_path = Path(temp_dir) / "cache.jsonl"

            first = analyze_fen(
                START_FEN,
                limit_ms=25,
                multipv=2,
                engine_command=[sys.executable, str(engine)],
                cache_path=cache_path,
            )
            second = analyze_fen(
                START_FEN,
                limit_ms=25,
                multipv=2,
                engine_command=[sys.executable, str(engine)],
                cache_path=cache_path,
            )

        self.assertEqual(first["schema"], "kindlemaster.chess_engine.analysis.v1")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["engine"], "stockfish")
        self.assertEqual(first["engine_version"], "Fakefish 1.0")
        self.assertEqual(first["side_to_move"], "w")
        self.assertEqual(first["best_move_uci"], "e2e4")
        self.assertEqual(first["best_move_san"], "e4")
        self.assertEqual(first["score_cp"], 34)
        self.assertEqual(first["pov_score"], "+0.34")
        self.assertEqual(first["pv"][0]["moves_uci"], ["e2e4", "e7e5"])
        self.assertEqual(first["pv"][0]["moves_san"], ["e4", "e5"])
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(second["best_move_uci"], "e2e4")

    def test_is_engine_available_reads_uci_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _write_fake_uci_engine(Path(temp_dir))

            result = is_engine_available(engine_command=[sys.executable, str(engine)])

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["available"])
        self.assertEqual(result["engine_version"], "Fakefish 1.0")

    def test_analyze_fen_returns_invalid_fen_without_engine(self) -> None:
        result = analyze_fen("not a fen", engine_command=[sys.executable, "not-used.py"])

        self.assertEqual(result["status"], "invalid_fen")
        self.assertEqual(result["best_move_uci"], "")
        self.assertFalse(result["cache"]["hit"])

    def test_analyze_fen_returns_engine_unavailable_for_missing_path(self) -> None:
        result = analyze_fen(START_FEN, engine_path=str(Path("missing-stockfish-binary.exe")))

        self.assertEqual(result["status"], "engine_unavailable")
        self.assertIn("stockfish_not_found", result["warnings"])

    def test_timeout_does_not_escape_as_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _write_fake_uci_engine(Path(temp_dir), hang_on_go=True)

            result = analyze_fen(
                START_FEN,
                limit_ms=25,
                engine_command=[sys.executable, str(engine)],
                timeout_ms=200,
            )

        self.assertEqual(result["status"], "timeout")
        self.assertIn("engine_timeout", result["warnings"])

    def test_normalize_mate_and_centipawn_scores(self) -> None:
        self.assertEqual(normalize_engine_score("cp", 123)["pov_score"], "+1.23")
        self.assertEqual(normalize_engine_score("mate", -2)["pov_score"], "#-2")


def _write_fake_uci_engine(root: Path, *, hang_on_go: bool = False) -> Path:
    script = root / ("fake_hanging_uci.py" if hang_on_go else "fake_uci.py")
    lines = [
        "import sys",
        "import time",
        "",
        "for raw in sys.stdin:",
        "    line = raw.strip()",
        "    if line == 'uci':",
        "        print('id name Fakefish 1.0', flush=True)",
        "        print('id author KindleMaster Tests', flush=True)",
        "        print('uciok', flush=True)",
        "    elif line == 'isready':",
        "        print('readyok', flush=True)",
        "    elif line.startswith('go '):",
    ]
    if hang_on_go:
        lines.append("        time.sleep(5)")
    else:
        lines.extend(
            [
                "        print('info depth 8 multipv 1 score cp 34 pv e2e4 e7e5', flush=True)",
                "        print('info depth 8 multipv 2 score cp 12 pv d2d4 d7d5', flush=True)",
                "        print('bestmove e2e4', flush=True)",
            ]
        )
    lines.extend(["    elif line == 'quit':", "        break", ""])
    script.write_text("\n".join(lines), encoding="utf-8")
    return script


if __name__ == "__main__":
    unittest.main()
