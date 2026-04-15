from __future__ import annotations

import zipfile

from kindlemaster_release_gate import extract_opf_metadata, run_checks


def test_release_mode_uses_manifest_metadata(active_release_publication: dict, active_release_report: dict, active_release_epub):
    with zipfile.ZipFile(active_release_epub) as zf:
        metadata = extract_opf_metadata(zf, "EPUB/content.opf")

    expected = active_release_publication["release_metadata"]
    assert active_release_report["publication"]["manifest_matched"] is True
    assert active_release_report["publication"]["release_ready_metadata"] is True
    assert active_release_report["release_gate"]["blockers"] == []
    assert metadata["title"] == expected["title"]
    assert metadata["creator"] == expected["creator"]
    assert metadata["language"] == expected["language"]


def test_release_smoke_metadata_checks_pass(active_release_epub):
    report = run_checks(active_release_epub)
    assert report["checks"]["no_opaque_or_slug_human_title"] is True
    assert report["checks"]["creator_not_unknown_for_release"] is True


def test_release_smoke_has_no_current_failures(active_release_epub):
    report = run_checks(active_release_epub)
    assert report["failed_checks"] == []
