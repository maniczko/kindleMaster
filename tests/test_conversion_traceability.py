from __future__ import annotations

import re
from pathlib import Path

from kindlemaster_quality_score import score_epub
from kindlemaster_webapp import app, build_premium_report, current_build_info


def test_active_release_report_has_explicit_page_coverage(active_release_report: dict) -> None:
    coverage = active_release_report["coverage"]

    assert coverage["coverage_pass"] is True
    assert coverage["source_pdf_page_count"] == coverage["baseline_page_records"]
    assert coverage["source_pdf_page_count"] == coverage["baseline_page_documents"]
    assert coverage["source_pdf_page_count"] == coverage["final_page_documents"]
    assert coverage["coverage_ratio"] == 1.0


def test_active_release_report_records_conversion_options(active_release_report: dict) -> None:
    options = active_release_report["conversion_options"]
    baseline_report = active_release_report["baseline_report"]

    assert options["profile_requested"] == "auto-premium"
    assert options["profile_applied"] in {"text_first_reflow", "text_priority", "preserve_layout_fallback"}
    assert options["force_ocr_requested"] is False
    assert options["force_ocr_applied"] is False
    assert baseline_report["hybrid_illustrated_pages"] == 0
    assert baseline_report["unjustified_fallback_pages"] == 0


def test_webapp_uses_major_minor_version_scheme() -> None:
    build = current_build_info()

    assert re.fullmatch(r"\d+\.\d+", build["version"])
    assert build["build_label"].startswith(f"v{build['version']}")


def test_quality_state_exposes_premium_report(active_release_report: dict) -> None:
    with app.test_client() as client:
        response = client.get("/quality-state")

    assert response.status_code == 200
    payload = response.get_json()
    premium_report = payload["quality"]["premium_report"]

    assert isinstance(premium_report, dict)
    assert premium_report["score_1_10"] is not None
    assert isinstance(premium_report["what_is_good"], list)
    assert isinstance(premium_report["what_is_bad"], list)


def test_build_premium_report_is_generic_and_human_readable(active_release_report: dict) -> None:
    final_epub = Path(active_release_report["final_epub"])
    score_report = score_epub(final_epub, publication_id=active_release_report["publication"]["publication_id"])
    premium_report = build_premium_report(score_report)

    assert 1.0 <= premium_report["score_1_10"] <= 10.0
    assert premium_report["verdict"] in {"premium", "needs-work"}
    assert premium_report["what_is_good"]
    assert premium_report["what_is_bad"]


def test_index_page_surfaces_premium_report_and_version() -> None:
    with app.test_client() as client:
        response = client.get("/")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Premium audit" in html
    assert "Ostatnia konwersja" in html
    assert re.search(r"Wersja <strong>\d+\.\d+</strong>", html)
