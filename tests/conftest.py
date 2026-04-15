from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kindlemaster_end_to_end import run_end_to_end
from kindlemaster_manifest import load_publication_manifest


SUPPORTED_PROFILES = [
    "document_like",
    "book_like",
    "report_like",
    "magazine_like",
    "mixed_layout",
]


@pytest.fixture(scope="session")
def publication_manifest() -> dict:
    return load_publication_manifest()


@pytest.fixture(scope="session")
def active_publication_id(publication_manifest: dict) -> str | None:
    env_publication_id = (os.environ.get("KM_ACTIVE_PUBLICATION_ID") or "").strip()
    if env_publication_id:
        return env_publication_id
    for publication in publication_manifest.get("publications", []):
        if publication.get("status", {}).get("release_eligible") is True:
            return publication["publication_id"]
    return None


@pytest.fixture(scope="session")
def manifest_publications(publication_manifest: dict) -> list[dict]:
    return list(publication_manifest.get("publications", []))


@pytest.fixture(scope="session")
def active_release_publication(manifest_publications: list[dict], active_publication_id: str | None) -> dict:
    if active_publication_id:
        for publication in manifest_publications:
            if publication.get("publication_id") == active_publication_id:
                return publication
        raise AssertionError(f"Manifest-backed publication_id not found for test run: {active_publication_id}")

    candidates = [
        publication
        for publication in manifest_publications
        if publication.get("status", {}).get("release_eligible") is True
    ]
    assert candidates, "At least one manifest-backed release-eligible publication is required."
    return candidates[0]


@pytest.fixture(scope="session")
def active_release_report(active_release_publication: dict, tmp_path_factory: pytest.TempPathFactory) -> dict:
    report_override = (os.environ.get("KM_ACTIVE_REPORT_JSON") or "").strip()
    if report_override:
        return json.loads(Path(report_override).resolve().read_text(encoding="utf-8"))

    pdf_rel = active_release_publication["inputs"]["pdf_path"]
    runtime_root = tmp_path_factory.mktemp("active-release-runtime")
    return run_end_to_end(
        (REPO_ROOT / pdf_rel).resolve(),
        publication_id=active_release_publication["publication_id"],
        release_mode=True,
        baseline_dir=runtime_root / "baseline",
        final_dir=runtime_root / "final",
        release_candidate_dir=runtime_root / "release_candidate",
        report_dir=runtime_root / "reports",
    )


@pytest.fixture(scope="session")
def active_release_epub(active_release_report: dict) -> Path:
    epub_override = (os.environ.get("KM_ACTIVE_FINAL_EPUB") or "").strip()
    if epub_override:
        return Path(epub_override).resolve()
    return Path(active_release_report["final_epub"]).resolve()
