from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_MODULES = [
    REPO_ROOT / "kindlemaster_pdf_to_epub.py",
    REPO_ROOT / "kindlemaster_image_layout_audit.py",
    REPO_ROOT / "kindlemaster_release_gate.py",
    REPO_ROOT / "kindlemaster_quality_score.py",
    REPO_ROOT / "kindlemaster_release_gate_enforcer.py",
    REPO_ROOT / "kindlemaster_webapp.py",
    REPO_ROOT / "kindle_semantic_cleanup.py",
]


def test_production_modules_do_not_reference_manifest_fixture_ids(publication_manifest: dict) -> None:
    forbidden_tokens = set()
    for publication in publication_manifest.get("publications", []):
        publication_id = publication.get("publication_id")
        if publication_id:
            forbidden_tokens.add(publication_id.lower())
        pdf_path = (publication.get("inputs") or {}).get("pdf_path")
        if pdf_path:
            forbidden_tokens.add(Path(pdf_path).stem.lower())

    for module_path in PRODUCTION_MODULES:
        content = module_path.read_text(encoding="utf-8").lower()
        for token in sorted(forbidden_tokens):
            assert token not in content, f"{module_path.name} contains fixture-specific token: {token}"
