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
    assemble_conversion_rebuild_bundle,
    build_conversion_rebuild_bundle,
    decode_conversion_rebuild_chunk_manifest,
    encode_conversion_rebuild_chunk_manifest,
    restore_conversion_rebuild_bundle,
    split_conversion_rebuild_bundle,
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

    def test_chunk_manifest_round_trip_preserves_bundle_integrity(self) -> None:
        bundle = bytes(range(256)) * 4
        parts, manifest = split_conversion_rebuild_bundle(bundle, chunk_size_bytes=127)
        encoded = encode_conversion_rebuild_chunk_manifest(manifest)
        decoded = decode_conversion_rebuild_chunk_manifest(encoded)
        payloads = {row["kind"]: payload for row, payload in zip(decoded["parts"], parts, strict=True)}

        self.assertEqual(assemble_conversion_rebuild_bundle(decoded, payloads), bundle)
        self.assertEqual(decoded["part_count"], 9)
        self.assertTrue(all(len(payload) <= 127 for payload in parts))

    def test_chunk_assembly_rejects_corrupted_part(self) -> None:
        bundle = b"0123456789" * 20
        parts, manifest = split_conversion_rebuild_bundle(bundle, chunk_size_bytes=64)
        payloads = {row["kind"]: payload for row, payload in zip(manifest["parts"], parts, strict=True)}
        payloads[manifest["parts"][1]["kind"]] = b"corrupt"

        with self.assertRaisesRegex(ConversionRebuildBundleError, "rebuild_chunk_size_mismatch"):
            assemble_conversion_rebuild_bundle(manifest, payloads)


if __name__ == "__main__":
    unittest.main()
