from __future__ import annotations

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from external_chessimg2pos_provider import (
    chessimg2pos_provider_available,
    recognize_fen_with_chessimg2pos,
)


class ChessImg2PosProviderTests(unittest.TestCase):
    def test_provider_disabled_keeps_runtime_stable(self) -> None:
        result = recognize_fen_with_chessimg2pos(
            "dummy.png",
            settings={
                "enabled": False,
                "mode": "auto",
                "python": "",
                "model_path": "",
                "timeout_ms": 1000,
            },
        )

        self.assertEqual(result.fen, "")
        self.assertIn("external_fen_provider_failed", result.warnings)

    def test_provider_available_depends_on_enabled_setting(self) -> None:
        self.assertTrue(chessimg2pos_provider_available({"enabled": True}))
        self.assertFalse(chessimg2pos_provider_available({"enabled": False}))

    def test_import_mode_normalizes_placement_only_payload(self) -> None:
        fake_module = SimpleNamespace(
            __version__="2026.02.27",
            predict_fen=lambda crop_path: {
                "placement": "4k3/8/8/8/8/8/8/4K3",
                "confidence": 0.91,
            },
        )
        with mock.patch("external_chessimg2pos_provider.importlib.import_module", return_value=fake_module):
            result = recognize_fen_with_chessimg2pos(
                "dummy.png",
                settings={
                    "enabled": True,
                    "mode": "import",
                    "python": "",
                    "model_path": "",
                    "timeout_ms": 1000,
                },
            )

        self.assertEqual(result.provider_version, "2026.02.27")
        self.assertEqual(result.placement, "4k3/8/8/8/8/8/8/4K3")
        self.assertEqual(result.fen, "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertAlmostEqual(result.confidence, 0.91)
        self.assertAlmostEqual(result.effective_confidence, 0.88)
        self.assertEqual(result.variant_role, "")

    def test_import_mode_normalizes_tiles_payload_and_variant_role(self) -> None:
        fake_module = SimpleNamespace(
            __version__="2026.02.27",
            predict_fen=lambda crop_path: {
                "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "confidence": 0.9,
                "tiles": [
                    {"row": 0, "col": 4, "piece": "k", "confidence": 0.98},
                    {"row": 7, "col": 4, "piece": "K", "confidence": 0.97},
                ],
            },
        )
        with mock.patch("external_chessimg2pos_provider.importlib.import_module", return_value=fake_module):
            result = recognize_fen_with_chessimg2pos(
                "dummy.png",
                settings={
                    "enabled": True,
                    "mode": "import",
                    "python": "",
                    "model_path": "",
                    "timeout_ms": 1000,
                },
                variant_role="reader_visible",
            )

        self.assertEqual(result.variant_role, "reader_visible")
        self.assertEqual(result.king_squares, {"black": "e8", "white": "e1"})
        self.assertEqual(result.piece_count_summary, {"K": 1, "k": 1})
        self.assertEqual(result.squares[0]["square"], "e1")
        self.assertEqual(result.squares[1]["square"], "e8")
        self.assertAlmostEqual(result.effective_confidence, 0.86)

    def test_subprocess_timeout_returns_timeout_warning(self) -> None:
        with mock.patch(
            "external_chessimg2pos_provider.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="python", timeout=1.0),
        ):
            result = recognize_fen_with_chessimg2pos(
                "dummy.png",
                settings={
                    "enabled": True,
                    "mode": "subprocess",
                    "python": "python",
                    "model_path": "",
                    "timeout_ms": 1000,
                },
            )

        self.assertEqual(result.fen, "")
        self.assertIn("external_fen_provider_timeout", result.warnings)

    def test_import_and_subprocess_modes_share_same_contract(self) -> None:
        fake_module = SimpleNamespace(
            __version__="2026.02.27",
            predict_fen=lambda crop_path: {
                "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "confidence": 0.88,
            },
        )
        subprocess_payload = json.dumps(
            {
                "payload": {
                    "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "confidence": 0.88,
                },
                "provider_version": "2026.02.27",
            }
        )
        with mock.patch("external_chessimg2pos_provider.importlib.import_module", return_value=fake_module):
            import_result = recognize_fen_with_chessimg2pos(
                "dummy.png",
                settings={
                    "enabled": True,
                    "mode": "import",
                    "python": "",
                    "model_path": "",
                    "timeout_ms": 1000,
                },
            )
        with mock.patch(
            "external_chessimg2pos_provider.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=subprocess_payload, stderr=""),
        ):
            subprocess_result = recognize_fen_with_chessimg2pos(
                "dummy.png",
                settings={
                    "enabled": True,
                    "mode": "subprocess",
                    "python": "python",
                    "model_path": "",
                    "timeout_ms": 1000,
                },
            )

        self.assertEqual(import_result.fen, subprocess_result.fen)
        self.assertEqual(import_result.placement, subprocess_result.placement)
        self.assertAlmostEqual(import_result.confidence, subprocess_result.confidence)
        self.assertAlmostEqual(import_result.effective_confidence, subprocess_result.effective_confidence)
        self.assertEqual(import_result.provider, subprocess_result.provider)
        self.assertEqual(import_result.provider_version, subprocess_result.provider_version)
        self.assertEqual(import_result.method, subprocess_result.method)


if __name__ == "__main__":
    unittest.main()
