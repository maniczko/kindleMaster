from __future__ import annotations

from pathlib import Path

from kindlemaster_release_gate import run_checks


def test_navigation_paths_and_anchors_pass_release_gate(active_release_epub: Path) -> None:
    report = run_checks(active_release_epub)
    assert report["checks"]["valid_nav_paths"] is True
    assert report["checks"]["valid_ncx_paths"] is True
    assert report["checks"]["valid_anchors"] is True


def test_toc_noise_thresholds_pass_release_gate(active_release_epub: Path) -> None:
    report = run_checks(active_release_epub)
    assert report["checks"]["no_duplicate_low_value_entries"] is True
    assert report["checks"]["no_page_label_dominance"] is True
    assert report["checks"]["no_author_only_noise"] is True
    assert report["checks"]["no_image_only_toc_targets"] is True
    assert report["checks"]["no_page_like_toc_targets"] is True
    assert report["counts"]["front_matter_target_count"] == 0
    assert report["checks"]["no_truncated_or_dangling_nav_labels"] is True
