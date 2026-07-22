from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from conversion_rebuild_bundle import (
    MANIFEST_PATH,
    RESTORE_MARKER_FILENAME,
    ConversionRebuildBundleError,
    build_conversion_rebuild_bundle,
    restore_conversion_rebuild_bundle,
)


class ConversionRebuildBundleTests(unittest.TestCase):
    def test_round_trip_keeps_review_reader_and_required_reports_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "job-1"
            (root / "review" / "fen_manual_assets").mkdir(parents=True)
            (root / "semantic_chess_html" / "data").mkdir(parents=True)
            (root / "report").mkdir(parents=True)
            (root / "review" / "fen_manual_review.html").write_text("review", encoding="utf-8")
            (root / "review" / "fen_manual_assets" / "board.png").write_bytes(b"png")
            (root / "semantic_chess_html" / "index.html").write_text("reader", encoding="utf-8")
            (root / "semantic_chess_html" / "data" / "artifact_manifest.json").write_text("{}", encoding="utf-8")
            (root / "report" / "chess_diagrams.json").write_text('{"records": []}', encoding="utf-8")
            (root / "report" / "chess_glyph_diagnostics.json").write_bytes(b"large-debug")

            bundle, manifest = build_conversion_rebuild_bundle(root)
            destination = Path(temp_dir) / "restore" / "job-1"
            restored = restore_conversion_rebuild_bundle(
                bundle,
                destination_root=destination,
                expected_job_id="job-1",
            )

            self.assertEqual(restored["status"], "restored")
            self.assertEqual(manifest["file_count"], 5)
            self.assertEqual((destination / "review" / "fen_manual_assets" / "board.png").read_bytes(), b"png")
            self.assertTrue((destination / "semantic_chess_html" / "index.html").is_file())
            self.assertTrue((destination / RESTORE_MARKER_FILENAME).is_file())
            self.assertFalse((destination / "report" / "chess_glyph_diagnostics.json").exists())

    def test_restore_rejects_manifest_path_traversal(self) -> None:
        manifest = {
            "schema": "kindlemaster.conversion_rebuild_bundle.v1",
            "job_id": "job-1",
            "file_count": 1,
            "files": [{"path": "../escape.txt", "size_bytes": 1, "sha256": "0" * 64}],
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escape.txt", b"x")
            archive.writestr(MANIFEST_PATH, json.dumps(manifest))
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ConversionRebuildBundleError, "rebuild_path_invalid"):
                restore_conversion_rebuild_bundle(
                    buffer.getvalue(),
                    destination_root=Path(temp_dir) / "job-1",
                    expected_job_id="job-1",
                )


if __name__ == "__main__":
    unittest.main()
