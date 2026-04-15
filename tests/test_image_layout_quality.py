from __future__ import annotations

from pathlib import Path

from kindlemaster_image_layout_audit import analyze_image_layout


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_active_release_has_no_image_or_page_like_toc_pollution(active_release_epub: Path) -> None:
    audit = analyze_image_layout(active_release_epub)

    assert audit["image_layout_pass"] is True
    assert audit["nav_target_to_image_only_count"] == 0
    assert audit["nav_target_to_page_like_count"] == 0
    assert audit["unjustified_fallback_count"] == 0
    assert audit["text_first_file_count"] > audit["image_fallback_file_count"]


def test_image_layout_ratios_remain_within_generic_premium_bounds(manifest_publications: list[dict]) -> None:
    for publication in manifest_publications:
        audit = analyze_image_layout(
            REPO_ROOT / "kindlemaster_runtime" / "output" / "final_epub" / f"{Path(publication['inputs']['pdf_path']).stem}.epub"
        )
        assert audit["image_only_ratio"] <= 0.4
        assert audit["nav_target_to_page_like_count"] == 0
        assert audit["unjustified_fallback_count"] == 0
