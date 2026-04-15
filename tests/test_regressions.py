from __future__ import annotations

from pathlib import Path

from kindlemaster_quality_score import score_epub
from kindlemaster_release_gate import run_checks


def test_known_release_blocking_regressions_remain_closed(active_release_epub: Path) -> None:
    report = run_checks(active_release_epub)

    assert report["failed_checks"] == []
    assert report["counts"]["title_merge_count"] == 0
    assert report["counts"]["special_heading_count"] == 0
    assert report["counts"]["flattened_inline_list_count"] == 0
    assert report["missing_stylesheets"] == []
    assert report["nav_dead_targets"] == []
    assert report["ncx_dead_targets"] == []


def test_quality_score_stays_above_premium_target(active_release_epub: Path, active_release_publication: dict) -> None:
    score = score_epub(active_release_epub, publication_id=active_release_publication["publication_id"])

    assert score["weighted_score"] >= score["premium_target"]
    assert score["smoke"]["failed_checks"] == []


def test_finalizer_proof_gate_passes(active_release_report: dict) -> None:
    finalizer_report = active_release_report["finalizer_report"]

    assert finalizer_report["final_pass"] is True
    assert finalizer_report["stage_integrity"]["acceptance_boundaries_ok"] is True
    assert [stage["stage"] for stage in finalizer_report["stages"]] == [
        "extract",
        "css_normalization",
        "semantic_planning",
        "semantic_apply",
        "navigation_rebuild",
        "metadata_normalization",
        "packaging",
    ]
