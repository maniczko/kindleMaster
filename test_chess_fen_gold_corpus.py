from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from chess_fen_gold_corpus import (
    INTAKE_MANIFEST_SCHEMA,
    build_fen_gold_corpus_review,
    validate_fen_gold_corpus_labels,
)


VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


class ChessFenGoldCorpusTests(unittest.TestCase):
    def test_builds_source_bound_review_pack_with_isolated_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, job, labels = _fixture(root)
            out = root / "secure-pack"

            report = build_fen_gold_corpus_review(
                source_pdf=source,
                job_output=job,
                marker_labels=labels,
                output_dir=out,
            )

            self.assertEqual(report["status"], "ready_for_human_review")
            self.assertEqual(report["candidate_count"], 5)
            self.assertEqual(report["manual_marker_label_count"], 2)
            self.assertEqual(report["remaining_human_review_count"], 5)
            self.assertEqual(report["missing_assets"], [])
            self.assertEqual(report["missing_board_asset_count"], 0)
            self.assertEqual(report["missing_optional_asset_count"], 0)
            manifest = json.loads((out / "intake_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], INTAKE_MANIFEST_SCHEMA)
            self.assertFalse(manifest["source"]["copyright_content_committed"])
            self.assertEqual(set(manifest["counts"]["splits"]), {"train", "calibration", "holdout"})
            review_rows = _read_jsonl(out / "full_fen_review.jsonl")
            self.assertEqual(len({row["diagram_fingerprint"] for row in review_rows}), 5)
            self.assertTrue(all(row["source_document_sha256"] == manifest["source"]["sha256"] for row in review_rows))
            self.assertTrue(all((out / row["board_crop_path"]).is_file() for row in review_rows))
            marker_rows = _read_jsonl(out / "marker_training_labels.jsonl")
            self.assertEqual({row["manual_visible_marker"] for row in marker_rows}, {"outline_triangle", "bad_crop"})
            self.assertTrue(all(row["marker_bbox_verified"] is False for row in marker_rows))
            pages: dict[int, str] = {}
            groups: dict[str, str] = {}
            for row in review_rows:
                self.assertNotIn(row["page"], pages)
                pages[row["page"]] = row["split"]
                prior = groups.setdefault(row["chapter_id"], row["split"])
                self.assertEqual(prior, row["split"])
            with zipfile.ZipFile(out / "full_fen_review_package.zip") as archive:
                names = set(archive.namelist())
            self.assertIn("full_fen_review.html", names)
            self.assertIn("full_fen_review.jsonl", names)
            self.assertNotIn(source.name, names)
            rendered_html = (out / "full_fen_review.html").read_text(encoding="utf-8")
            self.assertIn("join('\\n')+'\\n'", rendered_html)

    def test_imports_only_complete_source_bound_human_verified_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, job, labels = _fixture(root)
            pack = root / "secure-pack"
            build_fen_gold_corpus_review(
                source_pdf=source,
                job_output=job,
                marker_labels=labels,
                output_dir=pack,
            )
            rows = _read_jsonl(pack / "full_fen_review.jsonl")
            for row in rows:
                side = row.get("manual_marker_side") or "w"
                row["manual_fen"] = VALID_FEN.replace(" w ", f" {side} ")
                row["label_status"] = "verified"
                row["human_verified"] = True
                row["verified_by"] = "reviewer-a"
                row["verified_at"] = "2026-07-13T12:00:00Z"
            filled = root / "filled.jsonl"
            _write_jsonl(filled, rows)

            report = validate_fen_gold_corpus_labels(
                source_pdf=source,
                intake_manifest=pack / "intake_manifest.json",
                filled_labels=filled,
                output_dir=root / "imported",
            )

            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["verified_row_count"], 5)
            imported = _read_jsonl(Path(report["verified_labels_path"]))
            self.assertTrue(all(row["label_source"] == "manual_fen" for row in imported))
            self.assertTrue(all(row["human_verified"] for row in imported))

    def test_import_rejects_duplicate_invalid_and_source_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, job, labels = _fixture(root)
            pack = root / "secure-pack"
            build_fen_gold_corpus_review(
                source_pdf=source,
                job_output=job,
                marker_labels=labels,
                output_dir=pack,
            )
            rows = _read_jsonl(pack / "full_fen_review.jsonl")
            first = rows[0]
            first["manual_fen"] = "not-a-fen"
            first["label_status"] = "verified"
            first["human_verified"] = True
            first["verified_by"] = "reviewer-a"
            first["verified_at"] = "2026-07-13T12:00:00Z"
            filled = root / "invalid.jsonl"
            _write_jsonl(filled, [first, first, *rows[1:]])

            report = validate_fen_gold_corpus_labels(
                source_pdf=source,
                intake_manifest=pack / "intake_manifest.json",
                filled_labels=filled,
                output_dir=root / "invalid-import",
            )

            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("manual_fen_invalid" in error for error in report["errors"]))
            self.assertTrue(any("diagram_fingerprint_duplicate" in error for error in report["errors"]))
            other_source = root / "other.pdf"
            other_source.write_bytes(source.read_bytes() + b"different")
            with self.assertRaisesRegex(ValueError, "source_sha256_mismatch"):
                validate_fen_gold_corpus_labels(
                    source_pdf=other_source,
                    intake_manifest=pack / "intake_manifest.json",
                    filled_labels=filled,
                    output_dir=root / "mismatch",
                )

    def test_rejects_job_without_available_source_and_does_not_copy_external_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, job, labels = _fixture(root)
            payload_path = job / "chess_diagrams.json"
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            payload["source_pdf"] = str(root / "missing.pdf")
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "job_source_pdf_unavailable"):
                build_fen_gold_corpus_review(
                    source_pdf=source,
                    job_output=job,
                    marker_labels=labels,
                    output_dir=root / "unavailable-source",
                )

            payload["source_pdf"] = str(source)
            external = root / "external.png"
            Image.new("RGB", (20, 20), "white").save(external)
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            label_rows = _read_jsonl(labels)
            label_rows[0]["side_marker_review_crop_path"] = str(external)
            _write_jsonl(labels, label_rows)
            report = build_fen_gold_corpus_review(
                source_pdf=source,
                job_output=job,
                marker_labels=labels,
                output_dir=root / "external-asset",
            )
            self.assertEqual(report["missing_board_asset_count"], 0)
            self.assertEqual(report["missing_optional_asset_count"], 1)
            self.assertFalse(any(path.name == external.name for path in (root / "external-asset").rglob("*")))

            label_rows[0]["side_marker_review_crop_path"] = "../external.png"
            _write_jsonl(labels, label_rows)
            relative_report = build_fen_gold_corpus_review(
                source_pdf=source,
                job_output=job,
                marker_labels=labels,
                output_dir=root / "relative-external-asset",
            )
            self.assertEqual(relative_report["missing_optional_asset_count"], 1)

    def test_import_rejects_review_artifact_outside_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, job, labels = _fixture(root)
            pack = root / "secure-pack"
            build_fen_gold_corpus_review(
                source_pdf=source,
                job_output=job,
                marker_labels=labels,
                output_dir=pack,
            )
            manifest_path = pack / "intake_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"]["review_rows"] = "../outside.jsonl"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "package_member_outside_root"):
                validate_fen_gold_corpus_labels(
                    source_pdf=source,
                    intake_manifest=manifest_path,
                    filled_labels=pack / "full_fen_review.jsonl",
                    output_dir=root / "imported",
                )


def _fixture(root: Path) -> tuple[Path, Path, Path]:
    source = root / "fixed-edition.pdf"
    document = fitz.open()
    for _ in range(33):
        document.new_page(width=600, height=800)
    document.save(source)
    document.close()
    job = root / "job"
    crop_dir = job / "review" / "chess_fen" / "two_crop"
    crop_dir.mkdir(parents=True)
    pages = [1, 9, 17, 25, 33]
    diagrams = []
    for index, page in enumerate(pages, start=1):
        diagram_id = f"p{page:03d}_d01"
        board = Image.new("RGB", (180, 180), "white")
        draw = ImageDraw.Draw(board)
        draw.rectangle((10, 10, 170, 170), outline="black", width=3)
        draw.text((20 + index, 20), str(index), fill="black")
        board_path = crop_dir / f"{diagram_id}_board.png"
        board.save(board_path)
        diagrams.append(
            {
                "diagram_id": diagram_id,
                "page": page,
                "bbox_xyxy": [100.0, 120.0, 280.0, 300.0],
                "board_crop_path": board_path.relative_to(job).as_posix(),
                "fen_candidate": VALID_FEN,
                "confidence": 0.5,
            }
        )
    (job / "chess_diagrams.json").write_text(
        json.dumps({"source_pdf": str(source), "diagram_count": len(diagrams), "diagrams": diagrams}),
        encoding="utf-8",
    )
    marker_path = crop_dir / "p001_d01_marker_search.png"
    Image.new("RGB", (80, 80), "white").save(marker_path)
    labels = root / "marker-labels.jsonl"
    _write_jsonl(
        labels,
        [
            {
                "diagram_id": "p001_d01",
                "manual_visible_marker": "outline_triangle",
                "manual_side_to_move": "w",
                "manual_marker_bbox": "",
                "review_suggestion_bbox": [10, 10, 30, 30],
                "side_marker_search_crop_path": marker_path.relative_to(job).as_posix(),
                "label_status": "verified",
                "human_verified": True,
                "verification_source": "human_visual",
                "verified_at": "2026-07-13T10:00:00Z",
            },
            {
                "diagram_id": "p009_d01",
                "manual_visible_marker": "bad_crop",
                "manual_side_to_move": "",
                "manual_marker_bbox": "",
                "label_status": "verified",
                "human_verified": True,
                "verification_source": "human_visual",
                "verified_at": "2026-07-13T10:00:00Z",
            },
        ],
    )
    return source, job, labels


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
