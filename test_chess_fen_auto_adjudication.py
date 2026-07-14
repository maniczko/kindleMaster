from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from chess_fen_auto_adjudication import auto_label_fen_corpus
from chess_fen_gold_corpus import INTAKE_MANIFEST_SCHEMA


WHITE_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
BLACK_FEN = "4k3/8/8/8/8/8/8/4K3 b - - 0 1"


class ChessFenAutoAdjudicationTests(unittest.TestCase):
    def test_routes_exact_consensus_and_conflicts_without_human_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _fixture(root)
            rows = _read_jsonl(root / "full_fen_review.jsonl")
            rows[1]["manual_marker_side"] = "b"
            _write_jsonl(root / "full_fen_review.jsonl", rows)
            replay = root / "replay.jsonl"
            _write_jsonl(
                replay,
                [
                    {"diagram_fingerprint": rows[0]["diagram_fingerprint"], "tier": "medium", "fen": WHITE_FEN, "confidence": 0.97},
                    {"diagram_fingerprint": rows[1]["diagram_fingerprint"], "tier": "medium", "fen": BLACK_FEN, "confidence": 0.96},
                    {"diagram_fingerprint": rows[1]["diagram_fingerprint"], "tier": "strong", "fen": BLACK_FEN, "confidence": 0.98},
                    {"diagram_fingerprint": rows[2]["diagram_fingerprint"], "tier": "medium", "fen": "invalid", "confidence": 0.99},
                    {"diagram_fingerprint": rows[2]["diagram_fingerprint"], "tier": "strong", "fen": "", "confidence": 0.0, "status": "unreadable"},
                ],
            )

            report = auto_label_fen_corpus(
                intake_manifest=manifest,
                output_dir=root / "out",
                vision_mode="replay",
                replay_path=replay,
            )

            self.assertEqual(report["processed_count"], 3)
            self.assertEqual(report["automatic_consensus_count"], 2)
            self.assertIsNone(report["independent_accuracy"])
            output = _read_jsonl(root / "out" / "automatic_fen_candidates.jsonl")
            self.assertEqual([row["status"] for row in output[:2]], ["auto_consensus", "auto_consensus"])
            self.assertTrue(all(row["human_verified"] is False for row in output))
            self.assertTrue(all(row["accepted_for_gold_corpus"] is False for row in output))
            self.assertEqual(len(_read_jsonl(root / "out" / "fen_adjudication_queue.jsonl")), 1)
            self.assertTrue((root / "out" / "fen_exception_review.md").is_file())
            exception_row = _read_jsonl(root / "out" / "fen_adjudication_queue.jsonl")[0]
            self.assertTrue((root / "out" / exception_row["exception_crop_path"]).is_file())

    def test_marker_conflict_blocks_consensus_and_crop_tampering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _fixture(root, marker_side="b", count=1)
            row = _read_jsonl(root / "full_fen_review.jsonl")[0]
            replay = root / "replay.jsonl"
            _write_jsonl(replay, [{"diagram_fingerprint": row["diagram_fingerprint"], "tier": "medium", "fen": WHITE_FEN, "confidence": 0.99}])
            report = auto_label_fen_corpus(
                intake_manifest=manifest,
                output_dir=root / "out",
                vision_mode="replay",
                replay_path=replay,
            )
            self.assertEqual(report["automatic_consensus_count"], 0)
            output = _read_jsonl(root / "out" / "automatic_fen_candidates.jsonl")
            self.assertEqual(output[0]["decision_reason"], "marker_side_conflict")

            (root / "assets" / "board-0.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "board_crop_sha256_mismatch"):
                auto_label_fen_corpus(
                    intake_manifest=manifest,
                    output_dir=root / "tampered",
                    vision_mode="off",
                )

    def test_vision_uncertainty_blocks_exact_text_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _fixture(root, count=1)
            row = _read_jsonl(root / "full_fen_review.jsonl")[0]
            replay = root / "replay.jsonl"
            _write_jsonl(
                replay,
                [
                    {
                        "diagram_fingerprint": row["diagram_fingerprint"],
                        "tier": "medium",
                        "fen": WHITE_FEN,
                        "confidence": 0.99,
                        "needs_review": True,
                        "uncertain_squares": ["e4"],
                    }
                ],
            )
            report = auto_label_fen_corpus(
                intake_manifest=manifest,
                output_dir=root / "out",
                vision_mode="replay",
                replay_path=replay,
            )
            self.assertEqual(report["automatic_consensus_count"], 0)
            self.assertEqual(report["needs_adjudication_count"], 1)


def _fixture(root: Path, *, marker_side: str = "w", count: int = 3) -> Path:
    assets = root / "assets"
    assets.mkdir()
    source_sha = "a" * 64
    rows = []
    for index in range(count):
        crop = assets / f"board-{index}.png"
        crop.write_bytes(f"crop-{index}".encode())
        rows.append(
            {
                "diagram_fingerprint": f"fingerprint-{index}",
                "source_document_sha256": source_sha,
                "diagram_id": f"d{index}",
                "page": index + 1,
                "split": "train",
                "board_crop_path": crop.relative_to(root).as_posix(),
                "board_crop_sha256": sha256(crop.read_bytes()).hexdigest(),
                "manual_marker_side": marker_side,
                "candidate_fen": WHITE_FEN if index < 2 else "invalid",
                "candidate_confidence": 0.98,
            }
        )
    _write_jsonl(root / "full_fen_review.jsonl", rows)
    manifest = {
        "schema": INTAKE_MANIFEST_SCHEMA,
        "source": {"sha256": source_sha},
        "artifacts": {"review_rows": "full_fen_review.jsonl"},
    }
    path = root / "intake_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
