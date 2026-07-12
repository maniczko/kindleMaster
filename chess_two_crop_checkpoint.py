from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CHECKPOINT_SCHEMA = "kindlemaster.chess_fen.two_crop_progress.v1"
TWO_CROP_ALGORITHM_VERSION = "two_crop_single_pass.v1"
TWO_CROP_CROP_VERSION = "two_crop_review_artifacts.v1"
IDENTITY_KEYS = (
    "source_pdf_sha256",
    "fingerprint_version",
    "fingerprint_schema",
    "algorithm_version",
    "crop_version",
    "dpi",
    "quality_profile",
)


@dataclass(frozen=True)
class CheckpointLoadResult:
    checkpoint: dict[str, Any] | None
    compatible: bool
    reason_code: str


def checkpoint_path(out_dir: str | Path) -> Path:
    return Path(out_dir) / "reports" / "chess_fen" / "two_crop_progress.json"


def build_checkpoint_identity(
    *,
    source_pdf_sha256: str,
    fingerprint_schema: str,
    dpi: int,
    quality_profile: str,
) -> dict[str, Any]:
    return {
        "source_pdf_sha256": str(source_pdf_sha256 or "").strip().lower(),
        "fingerprint_version": str(fingerprint_schema or "").strip(),
        "fingerprint_schema": str(fingerprint_schema or "").strip(),
        "algorithm_version": TWO_CROP_ALGORITHM_VERSION,
        "crop_version": TWO_CROP_CROP_VERSION,
        "dpi": int(dpi),
        "quality_profile": str(quality_profile or "").strip().lower(),
    }


def load_compatible_checkpoint(
    path: str | Path,
    expected_identity: Mapping[str, Any],
) -> CheckpointLoadResult:
    candidate = Path(path)
    if not candidate.is_file():
        return CheckpointLoadResult(None, False, "checkpoint_missing")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return CheckpointLoadResult(None, False, "checkpoint_corrupt")
    if not isinstance(payload, dict) or payload.get("schema") != CHECKPOINT_SCHEMA:
        return CheckpointLoadResult(None, False, "checkpoint_schema_mismatch")
    identity = payload.get("identity")
    if not isinstance(identity, Mapping):
        return CheckpointLoadResult(None, False, "checkpoint_identity_missing")
    for key in IDENTITY_KEYS:
        if identity.get(key) != expected_identity.get(key):
            return CheckpointLoadResult(None, False, f"checkpoint_{key}_mismatch")
    pages = payload.get("pages")
    if not isinstance(pages, dict) or not _valid_page_payloads(pages):
        return CheckpointLoadResult(None, False, "checkpoint_payload_invalid")
    return CheckpointLoadResult(payload, True, "checkpoint_compatible")


def new_checkpoint(
    identity: Mapping[str, Any],
    *,
    total_pages: int,
    total_diagrams: int,
    resume_requested: bool,
    resume_reason_code: str,
) -> dict[str, Any]:
    return {
        "schema": CHECKPOINT_SCHEMA,
        "status": "in_progress",
        "identity": dict(identity),
        "cache_policy": "resume_opt_in" if resume_requested else "cold_no_reuse",
        "resume_requested": bool(resume_requested),
        "resume_used": False,
        "resume_reason_code": str(resume_reason_code),
        "total_page_count": max(0, int(total_pages)),
        "total_diagram_count": max(0, int(total_diagrams)),
        "completed_pages": [],
        "completed_page_count": 0,
        "completed_diagram_fingerprints": [],
        "completed_diagram_count": 0,
        "reused_diagram_count": 0,
        "computed_diagram_count": 0,
        "progress_percent": 0.0,
        "elapsed_seconds": 0.0,
        "median_page_seconds": 0.0,
        "median_diagrams_per_second": 0.0,
        "eta_seconds": None,
        "pages": {},
    }


def update_checkpoint_page(
    checkpoint: dict[str, Any],
    *,
    page_number: int,
    elapsed_seconds: float,
    records: list[dict[str, Any]],
    elapsed_total_seconds: float,
    reused_diagram_count: int,
    computed_diagram_count: int,
    page_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pages = checkpoint.setdefault("pages", {})
    page_payload = {
        "page": int(page_number),
        "elapsed_seconds": round(max(0.0, float(elapsed_seconds)), 6),
        "diagram_count": len(records),
        "diagram_fingerprints": sorted(str(row.get("diagram_fingerprint") or "") for row in records),
        "records": records,
    }
    page_payload.update(dict(page_metadata or {}))
    pages[str(int(page_number))] = page_payload
    _refresh_progress(
        checkpoint,
        elapsed_total_seconds=elapsed_total_seconds,
        reused_diagram_count=reused_diagram_count,
        computed_diagram_count=computed_diagram_count,
    )
    return checkpoint


def complete_checkpoint(
    checkpoint: dict[str, Any],
    *,
    elapsed_total_seconds: float,
    reused_diagram_count: int,
    computed_diagram_count: int,
) -> dict[str, Any]:
    checkpoint["status"] = "completed"
    checkpoint["resume_used"] = reused_diagram_count > 0
    _refresh_progress(
        checkpoint,
        elapsed_total_seconds=elapsed_total_seconds,
        reused_diagram_count=reused_diagram_count,
        computed_diagram_count=computed_diagram_count,
    )
    return checkpoint


def atomic_write_checkpoint(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return target


def reusable_page_records(
    checkpoint: Mapping[str, Any],
    *,
    page_number: int,
    expected_fingerprints: list[str],
    artifact_root: str | Path,
) -> list[dict[str, Any]] | None:
    pages = checkpoint.get("pages")
    page_payload = pages.get(str(int(page_number))) if isinstance(pages, Mapping) else None
    if not isinstance(page_payload, Mapping):
        return None
    records = page_payload.get("records")
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        return None
    actual = sorted(str(row.get("diagram_fingerprint") or "") for row in records)
    if actual != sorted(expected_fingerprints):
        return None
    root = Path(artifact_root).resolve()
    for record in records:
        updates = record.get("updates")
        artifact_paths = record.get("artifact_paths")
        if not isinstance(updates, dict) or not isinstance(artifact_paths, list):
            return None
        for relative in artifact_paths:
            if not _safe_existing_artifact(root, relative):
                return None
    return records


def checkpoint_provenance(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    checkpoint = payload if isinstance(payload, Mapping) else {}
    return {
        "cache_policy": str(checkpoint.get("cache_policy") or "cold_no_reuse"),
        "resume_used": bool(checkpoint.get("resume_used")),
        "resume_reason_code": str(checkpoint.get("resume_reason_code") or "checkpoint_unavailable"),
        "reused_diagram_count": int(checkpoint.get("reused_diagram_count") or 0),
        "computed_diagram_count": int(checkpoint.get("computed_diagram_count") or 0),
        "completed_diagram_count": int(checkpoint.get("completed_diagram_count") or 0),
        "total_diagram_count": int(checkpoint.get("total_diagram_count") or 0),
        "progress_percent": float(checkpoint.get("progress_percent") or 0.0),
    }


def _refresh_progress(
    checkpoint: dict[str, Any],
    *,
    elapsed_total_seconds: float,
    reused_diagram_count: int,
    computed_diagram_count: int,
) -> None:
    pages = checkpoint.get("pages") if isinstance(checkpoint.get("pages"), Mapping) else {}
    completed_pages = sorted(int(key) for key in pages)
    fingerprints = sorted(
        {
            str(fingerprint)
            for page in pages.values()
            if isinstance(page, Mapping)
            for fingerprint in page.get("diagram_fingerprints") or []
            if str(fingerprint)
        }
    )
    elapsed_values = [
        float(page.get("elapsed_seconds") or 0.0)
        for page in pages.values()
        if isinstance(page, Mapping)
    ]
    throughput_values = [
        float(page.get("diagram_count") or 0) / float(page.get("elapsed_seconds") or 0.0)
        for page in pages.values()
        if isinstance(page, Mapping)
        and float(page.get("elapsed_seconds") or 0.0) > 0.0
        and int(page.get("diagram_count") or 0) > 0
    ]
    total_diagrams = int(checkpoint.get("total_diagram_count") or 0)
    completed_diagrams = len(fingerprints)
    median_throughput = statistics.median(throughput_values) if throughput_values else 0.0
    remaining = max(0, total_diagrams - completed_diagrams)
    checkpoint.update(
        {
            "completed_pages": completed_pages,
            "completed_page_count": len(completed_pages),
            "completed_diagram_fingerprints": fingerprints,
            "completed_diagram_count": completed_diagrams,
            "reused_diagram_count": max(0, int(reused_diagram_count)),
            "computed_diagram_count": max(0, int(computed_diagram_count)),
            "progress_percent": round(
                100.0 if total_diagrams == 0 else min(100.0, completed_diagrams * 100.0 / total_diagrams),
                2,
            ),
            "elapsed_seconds": round(max(0.0, float(elapsed_total_seconds)), 6),
            "median_page_seconds": round(float(statistics.median(elapsed_values)), 6) if elapsed_values else 0.0,
            "median_diagrams_per_second": round(float(median_throughput), 6),
            "eta_seconds": round(remaining / median_throughput, 2) if median_throughput > 0 else None,
        }
    )


def _valid_page_payloads(pages: Mapping[str, Any]) -> bool:
    for key, page in pages.items():
        if not str(key).isdigit() or not isinstance(page, Mapping):
            return False
        records = page.get("records")
        if not isinstance(records, list):
            return False
        for record in records:
            if not isinstance(record, Mapping):
                return False
            if not str(record.get("diagram_fingerprint") or "").strip():
                return False
            if not isinstance(record.get("updates"), Mapping):
                return False
            if not isinstance(record.get("artifact_paths"), list):
                return False
    return True


def _safe_existing_artifact(root: Path, relative: Any) -> bool:
    value = str(relative or "").strip().replace("\\", "/")
    if not value or value.startswith(("/", "data:")) or ".." in Path(value).parts:
        return False
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path.is_file()
