from __future__ import annotations

import json
from pathlib import Path

from kindlemaster_end_to_end import run_end_to_end
from kindlemaster_release_candidate import create_release_candidate, sha256_file


def test_release_candidate_is_not_written_during_normal_end_to_end_run(
    active_release_publication: dict,
    tmp_path: Path,
) -> None:
    pdf_path = Path(active_release_publication["inputs"]["pdf_path"]).resolve()
    baseline_dir = tmp_path / "baseline"
    final_dir = tmp_path / "final"
    release_candidate_dir = tmp_path / "release_candidate"
    report_dir = tmp_path / "reports"

    report = run_end_to_end(
        pdf_path,
        publication_id=active_release_publication["publication_id"],
        release_mode=True,
        baseline_dir=baseline_dir,
        final_dir=final_dir,
        release_candidate_dir=release_candidate_dir,
        report_dir=report_dir,
    )

    assert report["release_candidate_epub"] is None
    assert report["release_candidate_epub_relative"] is None
    assert report["artifact_lifecycle"]["release_candidate_created"] is False
    assert list(release_candidate_dir.rglob("*.epub")) == []


def test_release_candidate_creation_requires_explicit_approval(
    active_release_publication: dict,
    tmp_path: Path,
) -> None:
    pdf_path = Path(active_release_publication["inputs"]["pdf_path"]).resolve()
    baseline_dir = tmp_path / "baseline"
    final_dir = tmp_path / "final"
    release_candidate_dir = tmp_path / "release_candidate"
    report_dir = tmp_path / "reports"

    report = run_end_to_end(
        pdf_path,
        publication_id=active_release_publication["publication_id"],
        release_mode=True,
        baseline_dir=baseline_dir,
        final_dir=final_dir,
        release_candidate_dir=release_candidate_dir,
        report_dir=report_dir,
    )

    try:
        create_release_candidate(
            final_epub=Path(report["final_epub"]),
            baseline_epub=Path(report["baseline_epub"]),
            report_json=report_dir / f"{pdf_path.stem}-end-to-end.json",
            publication_id=active_release_publication["publication_id"],
            build="vtest",
            approval_reference="T13-005",
            approved=False,
            release_candidate_dir=release_candidate_dir,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Release candidate creation should require explicit approval.")


def test_release_candidate_copy_up_is_traceable_and_immutable(
    active_release_publication: dict,
    tmp_path: Path,
) -> None:
    pdf_path = Path(active_release_publication["inputs"]["pdf_path"]).resolve()
    baseline_dir = tmp_path / "baseline"
    final_dir = tmp_path / "final"
    release_candidate_dir = tmp_path / "release_candidate"
    report_dir = tmp_path / "reports"

    report = run_end_to_end(
        pdf_path,
        publication_id=active_release_publication["publication_id"],
        release_mode=True,
        baseline_dir=baseline_dir,
        final_dir=final_dir,
        release_candidate_dir=release_candidate_dir,
        report_dir=report_dir,
    )

    final_epub = Path(report["final_epub"]).resolve()
    baseline_epub = Path(report["baseline_epub"]).resolve()
    report_json = report_dir / f"{pdf_path.stem}-end-to-end.json"

    payload = create_release_candidate(
        final_epub=final_epub,
        baseline_epub=baseline_epub,
        report_json=report_json,
        publication_id=active_release_publication["publication_id"],
        build="vtest-immutability",
        approval_reference="T13-005",
        approved=True,
        release_candidate_dir=release_candidate_dir,
    )

    candidate_epub = Path(payload["release_candidate_epub"])
    if not candidate_epub.is_absolute():
        candidate_epub = (Path.cwd() / candidate_epub).resolve()
    candidate_json = candidate_epub.with_suffix(".json")

    assert candidate_epub.exists()
    assert candidate_json.exists()
    assert candidate_epub != final_epub
    assert candidate_epub != baseline_epub
    assert payload["source_sha256"] == payload["release_candidate_sha256"]
    assert sha256_file(final_epub) == sha256_file(candidate_epub)

    stored_payload = json.loads(candidate_json.read_text(encoding="utf-8"))
    assert stored_payload["publication_id"] == active_release_publication["publication_id"]
    assert stored_payload["approval_reference"] == "T13-005"
    assert stored_payload["immutable"] is True

    final_epub.write_bytes(final_epub.read_bytes() + b"\nmutated-after-copy-up")
    try:
        create_release_candidate(
            final_epub=final_epub,
            baseline_epub=baseline_epub,
            report_json=report_json,
            publication_id=active_release_publication["publication_id"],
            build="vtest-immutability",
            approval_reference="T13-005",
            approved=True,
            release_candidate_dir=release_candidate_dir,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("Immutable release candidate must reject overwrite attempts with different content.")
