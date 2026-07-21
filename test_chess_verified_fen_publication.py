from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from chess_fen_hardening import fen_to_cells
from chess_verified_fen_publication import (
    VerifiedFenPublicationError,
    publish_verified_fen_artifacts,
)


FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


class ChessVerifiedFenPublicationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict, Path]:
        artifact_id = "artifact-verified-fen"
        source = root / "input" / "source.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source-pdf")
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        review_dir = root / "review" / "chess_fen"
        crop = review_dir / "fen_manual_assets" / "board.png"
        crop.parent.mkdir(parents=True)
        Image.new("RGB", (160, 160), "white").save(crop)
        crop_digest = hashlib.sha256(crop.read_bytes()).hexdigest()
        diagram_id = "layout-chess-p001-d01"
        fingerprint = hashlib.sha256(
            f"{source_digest}:{diagram_id}:1".encode("utf-8")
        ).hexdigest()
        records = [
            {
                "diagram_id": diagram_id,
                "page": 1,
                "board_crop_path": "review/chess_fen/fen_manual_assets/board.png",
                "full_fen": "",
                "full_fen_allowed": False,
                "status": "review",
                "requires_review": True,
            },
            {
                "diagram_id": "layout-chess-p002-d01",
                "page": 2,
                "board_crop_path": "review/chess_fen/fen_manual_assets/board.png",
                "full_fen": FEN,
                "full_fen_allowed": True,
                "full_fen_status": "FEN_MACHINE_ACCEPTED",
                "status": "accepted",
                "requires_review": False,
            },
        ]
        diagrams_path = root / "report" / "chess_diagrams.json"
        diagrams_path.parent.mkdir(parents=True)
        diagrams_path.write_text(json.dumps({"records": records}), encoding="utf-8")
        payload = {
            "status": "ok",
            "session_status": "complete",
            "storage": "database",
            "source_document_sha256": source_digest,
            "summary": {
                "total": 1,
                "verified": 1,
                "excluded": 0,
                "pending": 0,
                "invalid": 0,
            },
            "rows": [
                {
                    "artifact_id": artifact_id,
                    "diagram_id": diagram_id,
                    "diagram_fingerprint": fingerprint,
                    "source_document_sha256": source_digest,
                    "source_artifact_sha256": source_digest,
                    "page": 1,
                    "review_index": 1,
                    "crop_rel_path": "fen_manual_assets/board.png",
                    "crop_sha256": crop_digest,
                    "square_labels": fen_to_cells(FEN),
                    "piece_labels_verified": True,
                    "manual_fen": FEN,
                    "manual_side_to_move": "w",
                    "manual_side_evidence": "verified_source",
                    "manual_label": "correct_diagram",
                    "board_crop_label": "correct",
                    "marker_crop_label": "complete_no_marker",
                    "label_status": "verified",
                    "verified_by": "unit-test",
                    "verified_at": "2026-07-21T10:00:00Z",
                    "verification_source": "human_visual",
                    "human_verified": True,
                    "fen_human_verified": True,
                }
            ],
        }
        return payload, review_dir

    def test_publishes_source_bound_human_fen_without_mutating_raw_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload, review_dir = self._fixture(root)
            raw_before = (root / "report" / "chess_diagrams.json").read_bytes()

            report = publish_verified_fen_artifacts(
                artifact_id="artifact-verified-fen",
                artifact_root=root,
                review_payload=payload,
                review_dir=review_dir,
            )

            self.assertEqual(report["status"], "published")
            self.assertEqual(report["summary"]["fen_human_verified"], 1)
            self.assertEqual(report["summary"]["fen_automatic"], 1)
            self.assertEqual(report["summary"]["fen_unrecognized"], 0)
            self.assertEqual((root / "report" / "chess_diagrams.json").read_bytes(), raw_before)
            verified = json.loads(
                (root / "report" / "chess_diagrams_verified.json").read_text(encoding="utf-8")
            )
            first = verified["records"][0]
            self.assertTrue(first["fen_human_verified"])
            self.assertEqual(first["fen_source"], "human_verified_override")
            self.assertEqual(first["verified_board_crop_sha256"], payload["rows"][0]["crop_sha256"])
            pgn_text = (root / "report" / "chess_verified_positions.pgn").read_text(encoding="utf-8")
            self.assertEqual(pgn_text.count('[SetUp "1"]'), 2)
            with zipfile.ZipFile(root / "output" / "chess_verified_positions.epub") as archive:
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                self.assertIn("EPUB/package.opf", archive.namelist())
                page = archive.read("EPUB/positions-001.xhtml").decode("utf-8")
                self.assertIn("Human verified FEN", page)
                self.assertIn("Automatically accepted FEN", page)

    def test_rejects_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload, review_dir = self._fixture(root)
            payload["rows"][0]["diagram_fingerprint"] = "0" * 64

            with self.assertRaisesRegex(
                VerifiedFenPublicationError,
                "diagram_fingerprint_mismatch",
            ):
                publish_verified_fen_artifacts(
                    artifact_id="artifact-verified-fen",
                    artifact_root=root,
                    review_payload=payload,
                    review_dir=review_dir,
                )

    def test_rejects_source_and_crop_hash_mismatches(self) -> None:
        for mismatch in ("source", "crop"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                payload, review_dir = self._fixture(root)
                if mismatch == "source":
                    payload["source_document_sha256"] = "0" * 64
                else:
                    payload["rows"][0]["crop_sha256"] = "0" * 64

                with self.assertRaises(ValueError):
                    publish_verified_fen_artifacts(
                        artifact_id="artifact-verified-fen",
                        artifact_root=root,
                        review_payload=payload,
                        review_dir=review_dir,
                    )


if __name__ == "__main__":
    unittest.main()
