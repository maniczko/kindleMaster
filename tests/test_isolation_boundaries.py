from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISALLOWED_ROOT_PATHS = [
    "src",
    "supabase",
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.js",
    "vercel.json",
]
OPERATIONAL_FILES = [
    "kindle_semantic_cleanup.py",
    "kindlemaster_manifest.py",
    "kindlemaster_pdf_analysis.py",
    "kindlemaster_pdf_to_epub.py",
    "kindlemaster_end_to_end.py",
    "kindlemaster_release_gate.py",
    "kindlemaster_webapp.py",
    "kindlemaster_local_server.py",
    "run-kindlemaster-e2e.ps1",
    "run-kindlemaster-e2e.bat",
    "start-kindlemaster-local.ps1",
    "start-kindlemaster-local.bat",
    ".github/workflows/kindlemaster-python.yml",
]
FOREIGN_RUNTIME_MARKERS = ("supabase", "vite")


def test_legacy_frontend_root_paths_are_absent() -> None:
    missing = [path for path in DISALLOWED_ROOT_PATHS if (REPO_ROOT / path).exists()]
    assert not missing, f"Legacy frontend paths unexpectedly present in Kindle Master root: {missing}"


def test_only_kindle_workflow_remains() -> None:
    workflow_names = sorted(path.name for path in (REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflow_names == ["kindlemaster-python.yml"]


def test_operational_files_do_not_reference_foreign_frontend_runtime() -> None:
    offending = []
    for rel_path in OPERATIONAL_FILES:
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        lowered = text.lower()
        if any(marker in lowered for marker in FOREIGN_RUNTIME_MARKERS):
            offending.append(rel_path)
    assert not offending, f"Operational Kindle Master files still reference foreign frontend runtime markers: {offending}"
