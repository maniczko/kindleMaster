import json
import tempfile
import unittest
from pathlib import Path

from chess_engine_analysis import build_engine_analysis_artifacts, build_engine_analysis_gate


class ChessEngineAnalysisGateTests(unittest.TestCase):
    def test_gate_counts_available_review_untrusted_invalid_and_unavailable(self) -> None:
        records = [
            {
                "diagram_id": "ok",
                "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "fen_status": "FEN_MACHINE_ACCEPTED",
                "side_marker_status": "trusted_marker",
                "engine_status": "ok",
            },
            {
                "diagram_id": "review",
                "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "fen_status": "requires_review",
                "side_marker_status": "trusted_marker",
                "engine_status": "skipped",
                "skip_reason": "fen_not_accepted",
            },
            {
                "diagram_id": "untrusted",
                "fen": "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
                "fen_status": "FEN_MACHINE_ACCEPTED",
                "side_marker_status": "marker_missing",
                "engine_status": "skipped",
                "skip_reason": "side_to_move_not_trusted",
            },
            {
                "diagram_id": "invalid",
                "fen": "not a fen",
                "fen_status": "FEN_MACHINE_ACCEPTED",
                "side_marker_status": "trusted_marker",
                "engine_status": "skipped",
                "skip_reason": "invalid_fen",
            },
            {
                "diagram_id": "no-engine",
                "fen": "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
                "fen_status": "FEN_CORPUS_VERIFIED",
                "side_marker_status": "trusted_marker",
                "engine_status": "engine_unavailable",
                "skip_reason": "engine_unavailable",
            },
        ]

        gate = build_engine_analysis_gate({"items": records})

        self.assertEqual(gate["schema"], "kindlemaster.chess_engine.gate.v1")
        self.assertEqual(gate["diagram_count"], 5)
        self.assertEqual(gate["eligible_count"], 2)
        self.assertEqual(gate["analyzed_count"], 1)
        self.assertEqual(gate["unavailable_count"], 4)
        self.assertTrue(gate["engine_reader_available"])
        self.assertEqual(gate["availability"], "partially_available")
        reasons = {item["reason"]: item["count"] for item in gate["top_reasons"]}
        self.assertEqual(reasons["fen_not_accepted"], 1)
        self.assertEqual(reasons["side_to_move_not_trusted"], 1)
        self.assertEqual(reasons["invalid_fen"], 1)
        self.assertEqual(reasons["engine_unavailable"], 1)

    def test_artifact_builder_writes_gate_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            diagrams = [
                {
                    "diagram_id": "one",
                    "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "full_fen_status": "FEN_MACHINE_ACCEPTED",
                    "side_marker_status": "trusted_marker",
                }
            ]

            result = build_engine_analysis_artifacts(out, diagrams, analyze_fen_fn=_fake_engine_unavailable)

            gate_path = result["paths"]["engine_analysis_gate"]
            gate_md_path = result["paths"]["engine_analysis_gate_md"]
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            self.assertTrue(gate_path.is_file())
            self.assertTrue(gate_md_path.is_file())
            self.assertEqual(gate["availability"], "unavailable")
            self.assertEqual(gate["top_reasons"][0]["reason"], "engine_unavailable")
            self.assertIn("engine_analysis_gate", result["paths"])
            self.assertEqual(result["gate"]["schema"], "kindlemaster.chess_engine.gate.v1")


def _fake_engine_unavailable(fen: str, **_: object) -> dict:
    return {
        "status": "engine_unavailable",
        "fen": fen,
        "side_to_move": fen.split()[1],
    }


if __name__ == "__main__":
    unittest.main()
