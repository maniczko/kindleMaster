from __future__ import annotations

import re
import zipfile
from pathlib import Path

import yaml

from kindlemaster_image_layout_audit import analyze_image_layout
from kindlemaster_quality_score import score_epub
from kindlemaster_release_gate import PAGE_LABEL_RE, is_suspicious_nav_label


REPO_ROOT = Path(__file__).resolve().parents[1]


def _final_epub_for(publication: dict) -> Path:
    pdf_rel = publication["inputs"]["pdf_path"]
    stem = Path(pdf_rel).stem
    return (REPO_ROOT / "kindlemaster_runtime" / "output" / "final_epub" / f"{stem}.epub").resolve()


def _nav_labels(epub_path: Path) -> list[str]:
    with zipfile.ZipFile(epub_path) as zf:
        nav_name = next(name for name in zf.namelist() if name.endswith("nav.xhtml"))
        nav_text = zf.read(nav_name).decode("utf-8", errors="replace")
    return re.findall(r'<a href="[^"]+">([^<]+)</a>', nav_text)


def test_publication_profiles_include_all_supported_profiles() -> None:
    payload = yaml.safe_load((REPO_ROOT / "project_control" / "publication_profiles.yaml").read_text(encoding="utf-8"))
    declared = set(payload.get("supported_profiles", []))
    assert declared == {
        "document_like",
        "book_like",
        "report_like",
        "magazine_like",
        "mixed_layout",
    }


def test_manifest_has_real_fixture_or_explicit_blocker_for_each_supported_profile(publication_manifest: dict) -> None:
    covered_profiles = set()
    for publication in publication_manifest.get("publications", []):
        if publication.get("status", {}).get("fixture_quality", "").startswith("real"):
            covered_profiles.update(publication.get("scenario_profiles", [publication["profile"]]))
    explicit_blockers = {
        gap["profile"]
        for gap in publication_manifest.get("coverage_gaps", [])
        if gap.get("release_blocker") is True
    }
    missing = [
        profile
        for profile in ("document_like", "book_like", "report_like", "magazine_like", "mixed_layout")
        if profile not in covered_profiles and profile not in explicit_blockers
    ]
    assert not missing, f"Profiles missing both real fixtures and explicit blockers: {missing}"


def test_document_like_scenario_fixture_exists_and_is_real(manifest_publications: list[dict]) -> None:
    publication = next(item for item in manifest_publications if item["publication_id"] == "cover-letter-iwo-2026")
    assert publication["profile"] == "document_like"
    assert publication["status"]["fixture_quality"].startswith("real")
    assert publication["status"]["release_eligible"] is False


def test_document_like_scenario_is_readable_and_structurally_clean(manifest_publications: list[dict]) -> None:
    publication = next(item for item in manifest_publications if item["publication_id"] == "cover-letter-iwo-2026")
    epub_path = _final_epub_for(publication)
    score = score_epub(epub_path, publication_id=publication["publication_id"])

    assert epub_path.exists()
    assert score["smoke"]["checks"]["valid_nav_paths"] is True
    assert score["smoke"]["checks"]["valid_ncx_paths"] is True
    assert score["smoke"]["checks"]["no_title_author_lead_page_merge"] is True
    assert score["text_audit"]["split_word_count"] == 0
    assert score["text_audit"]["joined_word_count"] == 0
    assert score["weighted_score"] >= 8.8


def test_report_like_active_scenario_meets_premium_threshold(manifest_publications: list[dict]) -> None:
    publication = next(item for item in manifest_publications if item["publication_id"] == "strefa-pmi-52-2026")
    epub_path = _final_epub_for(publication)
    score = score_epub(epub_path, publication_id=publication["publication_id"])

    assert epub_path.exists()
    assert score["smoke"]["failed_checks"] == []
    assert score["weighted_score"] >= score["premium_target"]
    assert score["text_audit"]["split_word_count"] <= 1
    assert score["smoke"]["counts"]["toc_entry_count"] <= 12


def test_magazine_like_scenario_is_tracked_with_explicit_nonrelease_gaps(manifest_publications: list[dict]) -> None:
    publication = next(item for item in manifest_publications if item["publication_id"] == "newsweek-food-living-2026-01")
    epub_path = _final_epub_for(publication)
    score = score_epub(epub_path, publication_id=publication["publication_id"])

    assert epub_path.exists()
    assert publication["status"]["release_eligible"] is False
    assert score["smoke"]["checks"]["valid_nav_paths"] is True
    assert score["smoke"]["checks"]["valid_ncx_paths"] is True
    assert score["smoke"]["checks"]["no_front_matter_toc_pollution"] is True
    assert score["smoke"]["counts"]["h1_count"] >= 12
    assert score["smoke"]["counts"]["toc_entry_count"] <= 16
    assert score["text_audit"]["split_word_count"] == 0
    assert score["front_matter"]["distinctness_pass"] is True
    assert score["weighted_score"] >= score["premium_target"]
    assert "creator_not_unknown_for_release" in score["smoke"]["failed_checks"]


def test_magazine_like_nav_labels_are_generic_and_human_readable(manifest_publications: list[dict]) -> None:
    publication = next(item for item in manifest_publications if item["publication_id"] == "newsweek-food-living-2026-01")
    labels = _nav_labels(_final_epub_for(publication))

    assert labels
    assert all(not PAGE_LABEL_RE.match(label) for label in labels)
    assert all(not is_suspicious_nav_label(label) for label in labels)
    assert sum(1 for label in labels if len(label.split()) >= 3 or len(label) >= 18) >= max(2, len(labels) // 2)


def test_book_and_mixed_layout_scenario_is_stable_but_not_release_ready(manifest_publications: list[dict]) -> None:
    publication = next(
        item for item in manifest_publications if item["publication_id"] == "chess-5334-problems-combinations-and-games"
    )
    epub_path = _final_epub_for(publication)
    score = score_epub(epub_path, publication_id=publication["publication_id"])

    assert epub_path.exists()
    assert publication["status"]["release_eligible"] is False
    assert set(publication["scenario_profiles"]) == {"book_like", "mixed_layout"}
    assert score["smoke"]["checks"]["valid_nav_paths"] is True
    assert score["smoke"]["checks"]["no_page_label_dominance"] is True
    assert score["smoke"]["checks"]["no_truncated_or_dangling_nav_labels"] is True
    assert score["smoke"]["checks"]["no_page_like_toc_targets"] is True
    assert score["smoke"]["checks"]["no_image_only_toc_targets"] is True
    assert score["text_audit"]["split_word_count"] == 0
    assert score["front_matter"]["distinctness_pass"] is True
    assert score["front_matter"]["article_heading_leaks"] == 0
    assert score["front_matter"]["heading_noise_count"] <= 1
    assert score["image_layout"]["image_layout_pass"] is True
    assert score["weighted_score"] >= score["premium_target"]
    assert "creator_not_unknown_for_release" in score["smoke"]["failed_checks"]


def test_book_like_nav_labels_are_balanced_and_non_fragmentary(manifest_publications: list[dict]) -> None:
    publication = next(
        item for item in manifest_publications if item["publication_id"] == "chess-5334-problems-combinations-and-games"
    )
    labels = _nav_labels(_final_epub_for(publication))

    assert all(label.count("(") == label.count(")") for label in labels if "(" in label)
    assert all(not is_suspicious_nav_label(label) for label in labels)


def test_manifest_corpus_uses_generic_image_layout_guards(manifest_publications: list[dict]) -> None:
    for publication in manifest_publications:
        epub_path = _final_epub_for(publication)
        audit = analyze_image_layout(epub_path)

        assert audit["image_layout_pass"] is True
        assert audit["nav_target_to_image_only_count"] == 0
        assert audit["nav_target_to_page_like_count"] == 0
