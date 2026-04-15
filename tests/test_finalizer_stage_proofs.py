from __future__ import annotations

import json
from pathlib import Path

import pytest

from kindle_semantic_cleanup import create_finalizer_pipeline


EXPECTED_STAGE_SEQUENCE = [
    "extract",
    "css_normalization",
    "semantic_planning",
    "semantic_apply",
    "navigation_rebuild",
    "metadata_normalization",
    "packaging",
]


def test_finalizer_navigation_proof_survives_semantic_rebuild(active_release_report: dict) -> None:
    finalizer_report = active_release_report["finalizer_report"]
    navigation_proof = finalizer_report["navigation_quality_proof"]

    assert active_release_report["finalizer_stage_sequence"] == EXPECTED_STAGE_SEQUENCE
    assert active_release_report["finalizer_stage_sequence_valid"] is True
    assert active_release_report["finalizer_navigation_stage_integrity_ok"] is True
    assert finalizer_report["stage_integrity"]["navigation_stage_present"] is True
    assert finalizer_report["stage_integrity"]["stage_sequence_valid"] is True

    assert navigation_proof["nav_exists"] is True
    assert navigation_proof["toc_exists"] is True
    assert navigation_proof["semantic_toc_entry_count"] > 0
    assert navigation_proof["semantic_top_toc_entry_count"] > 0
    assert navigation_proof["nav_entry_count"] == navigation_proof["toc_entry_count"]
    assert navigation_proof["nav_entry_count"] == navigation_proof["semantic_top_toc_entry_count"]
    assert navigation_proof["nav_dead_target_count"] == 0
    assert navigation_proof["toc_dead_target_count"] == 0
    assert navigation_proof["navigation_survives_semantic_rebuild"] is True
    assert navigation_proof["stage_integrity_ok"] is True
    assert isinstance(navigation_proof["nav_labels_sample"], list)
    assert isinstance(navigation_proof["toc_labels_sample"], list)


def test_end_to_end_report_surfaces_finalizer_proof_fields(active_release_report: dict) -> None:
    assert active_release_report["finalizer_navigation_proof"] == active_release_report["finalizer_report"][
        "navigation_quality_proof"
    ]
    assert active_release_report["finalizer_stage_integrity"]["navigation_stage_present"] is True
    assert active_release_report["finalizer_stage_integrity"]["stage_sequence_valid"] is True
    assert active_release_report["finalizer_acceptance_boundaries_ok"] is True


def test_finalizer_persists_stage_artifact_manifest(active_release_report: dict) -> None:
    manifest_path = Path(active_release_report["finalizer_artifact_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path.exists()
    assert manifest["enabled"] is True
    assert manifest["stage_sequence"] == EXPECTED_STAGE_SEQUENCE
    assert len(manifest["stages"]) == len(EXPECTED_STAGE_SEQUENCE)

    for stage in manifest["stages"]:
        assert Path(stage["stage_dir"]).exists()
        assert Path(stage["proof_path"]).exists()
        assert stage["snapshot_files"]
        for snapshot in stage["snapshot_files"]:
            assert Path(snapshot["artifact_path"]).exists()
            assert snapshot["sha256"]


def test_semantic_stage_reports_profile_counts(active_release_report: dict) -> None:
    planning_stage = next(
        stage for stage in active_release_report["finalizer_report"]["stages"] if stage["stage"] == "semantic_planning"
    )
    apply_stage = next(
        stage for stage in active_release_report["finalizer_report"]["stages"] if stage["stage"] == "semantic_apply"
    )
    proofs = planning_stage["proofs"]
    apply_proofs = apply_stage["proofs"]

    assert proofs["semantic_plan_ready"] is True
    assert proofs["chapter_profile_counts"]["article"] >= 1
    assert proofs["special_profile_count"] >= 1
    assert "front_matter" in proofs["chapter_profile_counts"] or "toc" in proofs["chapter_profile_counts"]
    assert apply_proofs["semantic_plan_consumed"] is True
    assert apply_proofs["chapter_writes_match_plan"] is True
    assert apply_proofs["title_page_non_empty"] is True


def test_semantic_plan_and_apply_artifacts_are_observable(active_release_report: dict) -> None:
    manifest_path = Path(active_release_report["finalizer_artifact_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stages = {stage["stage"]: stage for stage in manifest["stages"]}

    planning_artifacts = {artifact["artifact_type"] for artifact in stages["semantic_planning"]["extra_artifacts"]}
    apply_artifacts = {artifact["artifact_type"] for artifact in stages["semantic_apply"]["extra_artifacts"]}

    assert "semantic_plan" in planning_artifacts
    assert "chapter_write_manifest" in apply_artifacts


def test_finalizer_pipeline_supports_independent_stage_execution(active_release_report: dict, tmp_path: Path) -> None:
    baseline_epub = Path(active_release_report["baseline_epub"]).resolve()
    with create_finalizer_pipeline(
        baseline_epub.read_bytes(),
        title=active_release_report["title"],
        author=active_release_report["author"],
        language=active_release_report["language"],
        artifact_dir=tmp_path / "artifacts",
    ) as pipeline:
        for stage_name in EXPECTED_STAGE_SEQUENCE[:-1]:
            stage_report = pipeline.run_stage(stage_name)
            assert stage_report.accepted is True
            assert pipeline.completed_stage_names[-1] == stage_name

        packaging_report = pipeline.run_stage("packaging")
        assert packaging_report.accepted is True
        assert pipeline.packaged_bytes is not None
        assert pipeline.completed_stage_names == EXPECTED_STAGE_SEQUENCE


def test_finalizer_pipeline_enforces_stage_dependencies(active_release_report: dict, tmp_path: Path) -> None:
    baseline_epub = Path(active_release_report["baseline_epub"]).resolve()
    with create_finalizer_pipeline(
        baseline_epub.read_bytes(),
        title=active_release_report["title"],
        author=active_release_report["author"],
        language=active_release_report["language"],
        artifact_dir=tmp_path / "artifacts",
    ) as pipeline:
        with pytest.raises(RuntimeError, match="requires completed stages"):
            pipeline.run_stage("semantic_apply")
