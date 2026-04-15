from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from kindlemaster_versioning import build_identity, read_display_version


ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "kindlemaster_runtime"
RELEASE_CANDIDATE_DIR = RUNTIME_ROOT / "output" / "release_candidate"


def repo_relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        return str(path.resolve()).replace("\\", "/")


def _package_version() -> str:
    version_path = ROOT / "VERSION"
    return read_display_version(version_path)


def _git_commit_short() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=ROOT,
        )
        return (completed.stdout or "").strip() or "nogit"
    except Exception:
        return "nogit"


def build_label() -> str:
    return build_identity(_package_version(), _git_commit_short())


def sanitize_build_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._") or "unversioned"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_base_name(final_epub: Path, build: str) -> str:
    return f"{final_epub.stem}--{sanitize_build_label(build)}"


def create_release_candidate(
    *,
    final_epub: Path,
    baseline_epub: Path | None = None,
    report_json: Path | None = None,
    publication_id: str | None = None,
    build: str | None = None,
    approval_reference: str | None = None,
    approved: bool = False,
    release_candidate_dir: Path | None = None,
) -> dict[str, object]:
    if not approved:
        raise PermissionError("Release candidate creation requires explicit approved=True.")

    final_epub = Path(final_epub).resolve()
    if not final_epub.exists():
        raise FileNotFoundError(f"Final EPUB does not exist: {final_epub}")

    baseline_path = Path(baseline_epub).resolve() if baseline_epub else None
    report_path = Path(report_json).resolve() if report_json else None
    if baseline_path and baseline_path == final_epub:
        raise ValueError("Release candidate must be distinct from the baseline EPUB path.")

    release_root = (release_candidate_dir or RELEASE_CANDIDATE_DIR).resolve()
    release_root.mkdir(parents=True, exist_ok=True)

    chosen_build = build or build_label()
    candidate_base = _candidate_base_name(final_epub, chosen_build)
    candidate_epub = release_root / f"{candidate_base}.epub"
    candidate_evidence = release_root / f"{candidate_base}.json"

    source_sha = sha256_file(final_epub)
    created = False

    if candidate_epub.exists():
        existing_sha = sha256_file(candidate_epub)
        if existing_sha != source_sha:
            raise FileExistsError(
                f"Immutable release candidate already exists with different content: {candidate_epub}"
            )
    else:
        shutil.copy2(final_epub, candidate_epub)
        created = True

    evidence_payload = {
        "publication_id": publication_id,
        "build_label": chosen_build,
        "approved": True,
        "approval_reference": approval_reference,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "immutable": True,
        "created": created,
        "source_final_epub": repo_relative_path(final_epub),
        "source_baseline_epub": repo_relative_path(baseline_path) if baseline_path else None,
        "source_report_json": repo_relative_path(report_path) if report_path and report_path.exists() else None,
        "release_candidate_epub": repo_relative_path(candidate_epub),
        "source_sha256": source_sha,
        "release_candidate_sha256": sha256_file(candidate_epub),
    }

    if candidate_evidence.exists():
        existing_evidence = json.loads(candidate_evidence.read_text(encoding="utf-8"))
        existing_sha = existing_evidence.get("release_candidate_sha256")
        if existing_sha and existing_sha != evidence_payload["release_candidate_sha256"]:
            raise FileExistsError(
                f"Immutable release candidate evidence already exists with conflicting hash: {candidate_evidence}"
            )

    candidate_evidence.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an immutable Kindle Master release candidate from an approved final EPUB")
    parser.add_argument("--final-epub", required=True, help="Path to the approved final EPUB")
    parser.add_argument("--baseline-epub", help="Optional path to the baseline EPUB")
    parser.add_argument("--report-json", help="Optional path to the end-to-end report JSON")
    parser.add_argument("--publication-id", help="Manifest-backed publication id")
    parser.add_argument("--build-label", help="Optional explicit build label")
    parser.add_argument("--approval-reference", required=True, help="Approval evidence reference, for example T13-005 or a report path")
    parser.add_argument("--approved", action="store_true", help="Explicitly approve copy-up into release_candidate")
    args = parser.parse_args()

    payload = create_release_candidate(
        final_epub=Path(args.final_epub),
        baseline_epub=Path(args.baseline_epub) if args.baseline_epub else None,
        report_json=Path(args.report_json) if args.report_json else None,
        publication_id=args.publication_id,
        build=args.build_label,
        approval_reference=args.approval_reference,
        approved=args.approved,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
