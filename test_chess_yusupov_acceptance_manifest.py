from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from chess_diagram_fingerprint import build_diagram_fingerprint
from chess_side_to_move_audit_export import export_side_to_move_audit
from chess_yusupov_acceptance import (
    ACCEPTANCE_MANIFEST_SCHEMA,
    DEFAULT_PROFILE,
    SECURE_CORPUS_ENV,
    evaluate_acceptance,
    load_acceptance_profile,
    load_job_evidence,
    run_fixed_edition_acceptance,
    secure_acceptance_for_quick,
    validate_acceptance_manifest,
)
from chess_yusupov_acceptance_summary import write_markdown_summary
from kindlemaster import main as kindlemaster_main


SOURCE_SHA = "1" * 64
OTHER_SOURCE_SHA = "f" * 64
COMMIT_SHA = "2" * 40
PROFILE_PATH = Path(
    "reference_inputs/chess_marker_acceptance/profiles/yusupov-fundamentals.json"
)


def _fingerprint(page: int, bbox: list[float], shade: int) -> dict[str, object]:
    image = Image.new("L", (80, 80), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((8 + shade, 8, 70, 70 - shade), outline=0, width=2)
    draw.line((10, 12 + shade, 66, 64), fill=80 + shade, width=3)
    return build_diagram_fingerprint(
        source_sha256=SOURCE_SHA,
        page=page,
        normalized_bbox_xyxy=bbox,
        board_crop=image,
    )


def _manifest() -> dict[str, object]:
    labels = [
        {
            **_fingerprint(1, [0.1, 0.1, 0.4, 0.4], 1),
            "chapter_id": "chapter-train",
            "split": "train",
            "allowed_for_tuning": True,
            "marker_status": "present",
            "expected_side": "w",
            "marker_ownership": "assigned",
            "crop_quality": "clear",
            "source_of_truth": "dual_human",
            "expected_fallback_source": "none",
            "label_status": "verified",
        },
        {
            **_fingerprint(2, [0.2, 0.2, 0.5, 0.5], 2),
            "chapter_id": "chapter-calibration",
            "split": "calibration",
            "allowed_for_tuning": True,
            "marker_status": "present",
            "expected_side": "b",
            "marker_ownership": "assigned",
            "crop_quality": "damaged",
            "source_of_truth": "human_visual",
            "expected_fallback_source": "text",
            "label_status": "verified",
        },
        {
            **_fingerprint(3, [0.3, 0.3, 0.6, 0.6], 3),
            "chapter_id": "chapter-holdout",
            "split": "holdout",
            "allowed_for_tuning": False,
            "marker_status": "absent",
            "expected_side": "w",
            "marker_ownership": "unassigned",
            "crop_quality": "unusable",
            "source_of_truth": "manual_verified",
            "expected_fallback_source": "pgn",
            "label_status": "verified",
        },
    ]
    hard_negatives = []
    kinds = (
        "coordinates",
        "letters",
        "borders",
        "arrows",
        "captions",
        "neighboring_diagrams",
    )
    for index, kind in enumerate(kinds, start=1):
        split, page, chapter = (
            ("train", 1, "chapter-train")
            if index <= 2
            else ("calibration", 2, "chapter-calibration")
            if index <= 4
            else ("holdout", 3, "chapter-holdout")
        )
        hard_negatives.append(
            {
                "hard_negative_fingerprint": "hnf_" + sha256(kind.encode()).hexdigest()[:32],
                "kind": kind,
                "page": page,
                "chapter_id": chapter,
                "split": split,
                "allowed_for_tuning": split != "holdout",
                "normalized_bbox_xyxy": [0.7, 0.1 + index * 0.01, 0.8, 0.2 + index * 0.01],
                "source_of_truth": "dual_human",
                "label_status": "verified",
                "expected_disposition": "reject",
            }
        )
    return {
        "schema": ACCEPTANCE_MANIFEST_SCHEMA,
        "manifest_version": "2026-07-10.1",
        "source_profile": DEFAULT_PROFILE,
        "source": {
            "kind": "fixed_edition_pdf",
            "sha256": SOURCE_SHA,
            "edition_id": "private-fixed-edition",
            "copyright_content_committed": False,
        },
        "verification": {
            "status": "verified",
            "verified_by": "reviewer-a+reviewer-b",
            "verified_at": "2026-07-10T00:00:00Z",
        },
        "diagrams": labels,
        "hard_negatives": hard_negatives,
    }


def _detected_records(manifest: dict[str, object]) -> list[dict[str, object]]:
    diagrams = manifest["diagrams"]
    assert isinstance(diagrams, list)
    first, second, third = diagrams
    return [
        {
            "diagram_fingerprint": first["diagram_fingerprint"],
            "diagram_id": "diagram-one",
            "page": 1,
            "side_marker_status": "trusted_marker",
            "side_to_move": "w",
            "marker_ownership_status": "assigned",
            "marker_bbox": [10, 10, 20, 20],
            "full_fen_allowed": True,
        },
        {
            "diagram_fingerprint": second["diagram_fingerprint"],
            "diagram_id": "diagram-two",
            "page": 2,
            "marker_semantic_status": "review",
            "side_to_move": "b",
            "marker_ownership_status": "assigned",
            "marker_bbox": [10, 10, 20, 20],
            "full_fen_allowed": False,
        },
        {
            "diagram_fingerprint": third["diagram_fingerprint"],
            "diagram_id": "diagram-three",
            "page": 3,
            "side_marker_status": "marker_missing",
            "side_to_move": "w",
            "marker_ownership_status": "unassigned",
            "full_fen_allowed": False,
        },
    ]


def _hard_negative_records(manifest: dict[str, object]) -> list[dict[str, object]]:
    hard_negatives = manifest["hard_negatives"]
    assert isinstance(hard_negatives, list)
    return [
        {
            "hard_negative_fingerprint": row["hard_negative_fingerprint"],
            "side_marker_status": "marker_rejected",
            "marker_semantic_status": "rejected",
        }
        for row in hard_negatives
    ]


def _write_job_output(
    root: Path,
    manifest: dict[str, object],
    *,
    source_sha: str = SOURCE_SHA,
    commit_sha: str = COMMIT_SHA,
) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "chess_fen").mkdir(parents=True, exist_ok=True)
    (root / "chess_diagrams.json").write_text(
        json.dumps(
            {
                "schema": "kindlemaster.test.diagrams.v1",
                "source_document_sha256": source_sha,
                "diagrams": _detected_records(manifest),
                "hard_negatives": _hard_negative_records(manifest),
            }
        ),
        encoding="utf-8",
    )
    (root / "data" / "artifact_manifest.json").write_text(
        json.dumps({"schema": "kindlemaster.test.artifacts.v1", "commit_sha": commit_sha}),
        encoding="utf-8",
    )
    (root / "reports" / "chess_fen" / "side_marker_assignment.json").write_text(
        json.dumps(
            {
                "schema": "kindlemaster.chess_fen.side_marker_assignment.v1",
                "items": [
                    {"diagram_id": "diagram-one", "marker_classifier_version": "real-v1"},
                    {"diagram_id": "diagram-two", "marker_classifier_version": "real-v1"},
                    {"diagram_id": "diagram-three", "marker_classifier_version": "real-v1"},
                ],
            }
        ),
        encoding="utf-8",
    )


class ChessYusupovAcceptanceManifestTests(unittest.TestCase):
    def test_redacted_summary_renderer_rejects_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "summary.json"
            source.write_text("[]", encoding="utf-8")

            with self.assertRaises(ValueError):
                write_markdown_summary(source, root / "summary.md")

    def test_redacted_summary_renderer_does_not_copy_arbitrary_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "summary.json"
            target = root / "summary.md"
            source.write_text(
                json.dumps(
                    {
                        "status": "private-status",
                        "errors": ["private-blocker"],
                        "metrics": {"expected_diagram_recall": "private-value"},
                    }
                ),
                encoding="utf-8",
            )

            write_markdown_summary(source, target)
            markdown = target.read_text(encoding="utf-8")

        self.assertNotIn("private-status", markdown)
        self.assertNotIn("private-blocker", markdown)
        self.assertNotIn("private-value", markdown)
        self.assertIn("unclassified_acceptance_blocker", markdown)

    def test_corrupt_profile_fails_closed_without_json_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profile.json"
            profile_path.write_text("{broken", encoding="utf-8")

            profile = load_acceptance_profile(
                DEFAULT_PROFILE,
                profile_path=profile_path,
            )

        self.assertEqual(profile, {})

    def test_verified_manifest_requires_stable_fingerprints_and_separated_splits(self) -> None:
        validation = validate_acceptance_manifest(_manifest(), source_profile=DEFAULT_PROFILE)

        self.assertEqual(validation["status"], "valid", validation)
        self.assertEqual(validation["diagram_count"], 3)
        self.assertEqual(validation["hard_negative_count"], 6)
        self.assertEqual(validation["split_counts"], {"calibration": 1, "holdout": 1, "train": 1})

    def test_page_or_chapter_leakage_and_holdout_tuning_are_rejected(self) -> None:
        manifest = copy.deepcopy(_manifest())
        manifest["diagrams"][2]["page"] = 1
        manifest["diagrams"][2]["chapter_id"] = "chapter-train"
        manifest["diagrams"][2]["allowed_for_tuning"] = True

        validation = validate_acceptance_manifest(manifest, source_profile=DEFAULT_PROFILE)

        self.assertEqual(validation["status"], "invalid")
        self.assertTrue(any("page_split_leakage" in error for error in validation["errors"]))
        self.assertTrue(any("chapter_split_leakage" in error for error in validation["errors"]))
        self.assertTrue(any("holdout_must_forbid_tuning" in error for error in validation["errors"]))

    def test_invalid_page_and_threshold_fail_closed_without_crashing(self) -> None:
        manifest = _manifest()
        manifest["diagrams"][0]["page"] = "not-a-page"

        validation = validate_acceptance_manifest(manifest, source_profile=DEFAULT_PROFILE)
        report = evaluate_acceptance(
            _manifest(),
            detected_records=[
                *_detected_records(_manifest()),
                *_hard_negative_records(_manifest()),
            ],
            source_document_sha256=SOURCE_SHA,
            runtime_commit_sha=COMMIT_SHA,
            validator_commit_sha=COMMIT_SHA,
            thresholds={"minimum_expected_diagram_recall": "not-a-number"},
        )

        self.assertEqual(validation["status"], "invalid")
        self.assertTrue(any("page_invalid" in error for error in validation["errors"]))
        self.assertEqual(report["status"], "failed")
        self.assertFalse(
            next(
                check["passed"]
                for check in report["checks"]
                if check["name"] == "minimum_expected_diagram_recall"
            )
        )

    def test_gate_reports_all_required_metrics_and_zero_false_trust(self) -> None:
        manifest = _manifest()
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        detected = [*_detected_records(manifest), *_hard_negative_records(manifest)]
        detected[0]["diagram_fingerprint"] = (
            f"  {detected[0]['diagram_fingerprint']}  "
        )

        report = evaluate_acceptance(
            manifest,
            detected_records=detected,
            source_document_sha256=SOURCE_SHA,
            runtime_commit_sha=COMMIT_SHA,
            validator_commit_sha=COMMIT_SHA,
            thresholds=profile["thresholds"],
        )

        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["metrics"]["expected_diagram_recall"], 1.0)
        self.assertEqual(report["metrics"]["marker_candidate_recall_visible_subset"], 1.0)
        self.assertEqual(report["metrics"]["marker_ownership_accuracy"], 1.0)
        self.assertEqual(report["metrics"]["clear_marker_classification_accuracy"], 1.0)
        self.assertEqual(report["metrics"]["false_trusted_marker_count"], 0)
        self.assertEqual(report["metrics"]["trusted_marker_rate"], 0.3333)
        self.assertEqual(report["metrics"]["side_to_move_coverage_rate"], 1.0)
        self.assertEqual(report["metrics"]["unknown_count"], 0)
        self.assertEqual(report["metrics"]["full_fen_safe_acceptance_rate"], 0.3333)
        self.assertEqual(report["metrics"]["hard_negative_evidence_rate"], 1.0)
        self.assertEqual(report["subsets"]["hard_negatives"]["exercised_count"], 6)
        self.assertEqual(report["subsets"]["damaged_ambiguous"]["expected_count"], 1)
        self.assertTrue(report["closing_evidence_eligible"])

    def test_false_trusted_ambiguous_marker_and_extra_detection_fail_gate(self) -> None:
        manifest = _manifest()
        detected = [*_detected_records(manifest), *_hard_negative_records(manifest)]
        detected[1].update(
            {
                "marker_semantic_status": "trusted",
                "marker_semantic_side": "w",
                "side_marker_status": "trusted_marker",
            }
        )
        detected.append(
            {
                "diagram_fingerprint": "dfp_" + "a" * 32,
                "side_marker_status": "trusted_marker",
                "side_to_move": "b",
            }
        )
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

        report = evaluate_acceptance(
            manifest,
            detected_records=detected,
            source_document_sha256=SOURCE_SHA,
            runtime_commit_sha=COMMIT_SHA,
            validator_commit_sha=COMMIT_SHA,
            thresholds=profile["thresholds"],
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["metrics"]["false_trusted_marker_count"], 2)
        self.assertFalse(report["closing_evidence_eligible"])

    def test_missing_or_trusted_hard_negative_fails_gate(self) -> None:
        manifest = _manifest()
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        incomplete = [*_detected_records(manifest), *_hard_negative_records(manifest)[:-1]]

        missing_report = evaluate_acceptance(
            manifest,
            detected_records=incomplete,
            source_document_sha256=SOURCE_SHA,
            runtime_commit_sha=COMMIT_SHA,
            validator_commit_sha=COMMIT_SHA,
            thresholds=profile["thresholds"],
        )
        trusted = [*_detected_records(manifest), *_hard_negative_records(manifest)]
        trusted[-1]["side_marker_status"] = "trusted_marker"
        trusted[-1]["marker_semantic_status"] = "trusted"
        trusted_report = evaluate_acceptance(
            manifest,
            detected_records=trusted,
            source_document_sha256=SOURCE_SHA,
            runtime_commit_sha=COMMIT_SHA,
            validator_commit_sha=COMMIT_SHA,
            thresholds=profile["thresholds"],
        )

        self.assertEqual(missing_report["status"], "failed")
        self.assertLess(missing_report["metrics"]["hard_negative_evidence_rate"], 1.0)
        self.assertEqual(trusted_report["status"], "failed")
        self.assertEqual(trusted_report["metrics"]["false_trusted_marker_count"], 1)

    def test_directory_and_safe_audit_zip_evidence_load_by_fingerprint(self) -> None:
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            _write_job_output(job, manifest)
            directory = load_job_evidence(job)
            archive = root / "audit.zip"
            exported = export_side_to_move_audit(job_output=job, out_path=archive)
            zipped = load_job_evidence(archive)

        self.assertEqual(exported["status"], "created", exported)
        self.assertEqual(directory["status"], "loaded", directory)
        self.assertEqual(zipped["status"], "loaded", zipped)
        self.assertEqual(directory["source_document_sha256"], SOURCE_SHA)
        self.assertEqual(zipped["runtime_commit_sha"], COMMIT_SHA)
        self.assertEqual(len(directory["records"]), 9)
        self.assertEqual(len(zipped["records"]), 9)
        self.assertEqual(
            {
                row.get("marker_classifier_version")
                for row in directory["records"]
                if row.get("diagram_fingerprint")
            },
            {"real-v1"},
        )

    def test_full_runner_writes_json_and_markdown_and_rejects_source_mismatch(self) -> None:
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secure = root / "secure" / DEFAULT_PROFILE
            secure.mkdir(parents=True)
            manifest_path = secure / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            job = root / "job"
            _write_job_output(job, manifest, source_sha=OTHER_SOURCE_SHA)
            report_dir = root / "reports"

            report = run_fixed_edition_acceptance(
                source_profile=DEFAULT_PROFILE,
                job_output=job,
                manifest_path=manifest_path,
                profile_path=PROFILE_PATH,
                report_dir=report_dir,
                validator_commit_sha=COMMIT_SHA,
            )

            self.assertTrue((report_dir / f"{DEFAULT_PROFILE}.json").is_file())
            persisted_json = (report_dir / f"{DEFAULT_PROFILE}.json").read_text(
                encoding="utf-8"
            )
            markdown = (report_dir / f"{DEFAULT_PROFILE}.md").read_text(encoding="utf-8")

        self.assertEqual(report["status"], "failed")
        self.assertIn("source_document_sha256_match", report["errors"])
        self.assertIn("synthetic fixtures may claim real acceptance: `false`", markdown)
        self.assertNotIn(str(job), persisted_json)
        self.assertNotIn(str(manifest_path), persisted_json)
        self.assertNotIn(SOURCE_SHA, persisted_json)
        self.assertNotIn(OTHER_SOURCE_SHA, persisted_json)
        self.assertNotIn(COMMIT_SHA, persisted_json)
        self.assertNotIn(SOURCE_SHA, markdown)
        self.assertNotIn(OTHER_SOURCE_SHA, markdown)
        self.assertNotIn(COMMIT_SHA, markdown)

    def test_missing_private_pack_is_unavailable_and_available_pack_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unavailable = run_fixed_edition_acceptance(
                source_profile=DEFAULT_PROFILE,
                job_output=root / "missing-job",
                repo_root=root,
                profile_path=PROFILE_PATH,
                report_dir=root / "reports",
                environ={},
            )
            secure = root / "secure" / DEFAULT_PROFILE
            secure.mkdir(parents=True)
            (secure / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
            mandatory = secure_acceptance_for_quick(
                repo_root=Path.cwd(),
                environ={SECURE_CORPUS_ENV: str(root / "secure")},
            )

        self.assertEqual(unavailable["status"], "corpus_unavailable")
        self.assertFalse(unavailable["synthetic_fixture_claim_allowed"])
        self.assertTrue(mandatory["enforced"])
        self.assertEqual(mandatory["status"], "failed")

    def test_cli_runs_fixed_edition_gate_and_writes_reports(self) -> None:
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            job = root / "job"
            _write_job_output(job, manifest)
            report_dir = root / "reports"
            argv = [
                "kindlemaster.py",
                "chess",
                "validate-side-markers",
                "--source-profile",
                DEFAULT_PROFILE,
                "--job-output",
                str(job),
                "--manifest",
                str(manifest_path),
                "--profile-config",
                str(PROFILE_PATH),
                "--report-dir",
                str(report_dir),
                "--current-main-sha",
                COMMIT_SHA,
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as output:
                returncode = kindlemaster_main()

            report = json.loads((report_dir / f"{DEFAULT_PROFILE}.json").read_text(encoding="utf-8"))

        self.assertEqual(returncode, 0, output.getvalue())
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["closing_evidence_eligible"])
        self.assertEqual(report["metrics"]["side_to_move_coverage_rate"], 1.0)
        self.assertEqual(report["metrics"]["unknown_count"], 0)
        self.assertEqual(report["metrics"]["false_trusted_marker_count"], 0)
        self.assertEqual(report["side_to_move_fusion"]["exact_verified_label_reuse_count"], 2)
        self.assertEqual(report["side_to_move_fusion"]["conflict_count"], 0)


if __name__ == "__main__":
    unittest.main()
