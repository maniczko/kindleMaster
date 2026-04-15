from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "project_control" / "publication_manifest.yaml"


def repo_relative_path(path: Path | str | None) -> str | None:
    if not path:
        return None
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_publication_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {
            "version": 0,
            "project": "kindle-master",
            "authoritative_for_release_metadata": False,
            "metadata_precedence": [],
            "publications": [],
            "coverage_gaps": [],
        }
    payload = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
    payload.setdefault("publications", [])
    payload.setdefault("coverage_gaps", [])
    return payload


def list_publications() -> list[dict[str, Any]]:
    return list(load_publication_manifest().get("publications", []))


def get_publication(publication_id: str | None) -> dict[str, Any] | None:
    if not publication_id:
        return None
    for publication in list_publications():
        if publication.get("publication_id") == publication_id:
            return publication
    return None


def find_publication_for_input(*, pdf_path: Path | None = None, epub_path: Path | None = None) -> dict[str, Any] | None:
    pdf_rel = repo_relative_path(pdf_path)
    epub_rel = repo_relative_path(epub_path)
    for publication in list_publications():
        inputs = publication.get("inputs") or {}
        if pdf_rel and inputs.get("pdf_path") == pdf_rel:
            return publication
        if epub_rel and inputs.get("epub_path") == epub_rel:
            return publication
    return None


def _resolve_metadata_field(
    field: str,
    *,
    override: str | None,
    manifest_metadata: dict[str, Any],
    fallback_metadata: dict[str, str],
) -> tuple[str, str]:
    override_value = (override or "").strip()
    if override_value:
        return override_value, "cli_override"

    manifest_value = str(manifest_metadata.get(field) or "").strip()
    if manifest_value:
        return manifest_value, "publication_manifest"

    fallback_value = (fallback_metadata.get(field) or "").strip()
    if fallback_value:
        return fallback_value, "source_metadata"

    return "", "missing"


def resolve_publication_context(
    *,
    pdf_path: Path | None = None,
    epub_path: Path | None = None,
    publication_id: str | None = None,
    title: str | None = None,
    author: str | None = None,
    language: str | None = None,
    fallback_title: str,
    fallback_author: str,
    fallback_language: str,
    release_mode: bool = False,
) -> dict[str, Any]:
    if release_mode and not publication_id:
        raise ValueError("Release mode requires --publication-id so metadata stays traceable.")

    publication = get_publication(publication_id) if publication_id else find_publication_for_input(pdf_path=pdf_path, epub_path=epub_path)
    if publication_id and publication is None:
        raise ValueError(f"Publication manifest entry not found for publication_id={publication_id}.")

    manifest_metadata = (publication or {}).get("release_metadata") or {}
    fallback_metadata = {
        "title": fallback_title,
        "creator": fallback_author,
        "language": fallback_language,
    }

    resolved_title, title_source = _resolve_metadata_field(
        "title",
        override=title,
        manifest_metadata=manifest_metadata,
        fallback_metadata=fallback_metadata,
    )
    resolved_creator, creator_source = _resolve_metadata_field(
        "creator",
        override=author,
        manifest_metadata=manifest_metadata,
        fallback_metadata=fallback_metadata,
    )
    resolved_language, language_source = _resolve_metadata_field(
        "language",
        override=language,
        manifest_metadata=manifest_metadata,
        fallback_metadata=fallback_metadata,
    )

    status = (publication or {}).get("status") or {}
    manifest_metadata_complete = all(str(manifest_metadata.get(field) or "").strip() for field in ("title", "creator", "language"))
    release_blockers: list[str] = []
    if release_mode:
        if not publication:
            release_blockers.append("manifest_entry_missing")
        if not manifest_metadata_complete and not all((title or "").strip() and (author or "").strip() and (language or "").strip()):
            release_blockers.append("manifest_release_metadata_incomplete")
        if publication and not bool(status.get("release_eligible")):
            release_blockers.append("manifest_entry_not_release_eligible")

    return {
        "publication_id": publication.get("publication_id") if publication else publication_id,
        "publication_profile": publication.get("profile") if publication else None,
        "manifest_matched": publication is not None,
        "manifest_entry": publication,
        "manifest_fixture_quality": status.get("fixture_quality") if publication else None,
        "manifest_release_eligible": bool(status.get("release_eligible")) if publication else False,
        "manifest_metadata_complete": manifest_metadata_complete,
        "effective_metadata": {
            "title": resolved_title,
            "creator": resolved_creator,
            "language": resolved_language,
        },
        "metadata_sources": {
            "title": title_source,
            "creator": creator_source,
            "language": language_source,
        },
        "fallback_metadata": fallback_metadata,
        "release_mode": release_mode,
        "release_blockers": release_blockers,
        "release_ready_metadata": len(release_blockers) == 0,
        "input_paths": {
            "pdf_path": repo_relative_path(pdf_path),
            "epub_path": repo_relative_path(epub_path),
        },
    }
