from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "kindlemaster.conversion_rebuild_bundle.v1"
CHUNK_MANIFEST_SCHEMA = "kindlemaster.conversion_rebuild_bundle_chunks.v1"
MANIFEST_PATH = "_kindlemaster/rebuild_manifest.json"
RESTORE_MARKER_FILENAME = ".kindlemaster_rebuild_complete.json"
MAX_FILE_COUNT = 10_000
MAX_FILE_BYTES = 192 * 1024 * 1024
MAX_TOTAL_BYTES = 768 * 1024 * 1024
DEFAULT_STORAGE_CHUNK_BYTES = 45 * 1024 * 1024


class ConversionRebuildBundleError(ValueError):
    pass


def split_conversion_rebuild_bundle(
    data: bytes,
    *,
    chunk_size_bytes: int = DEFAULT_STORAGE_CHUNK_BYTES,
) -> tuple[list[bytes], dict[str, Any]]:
    chunk_size = int(chunk_size_bytes)
    if chunk_size <= 0:
        raise ConversionRebuildBundleError("rebuild_chunk_size_invalid")
    if not data:
        raise ConversionRebuildBundleError("rebuild_bundle_empty")

    parts = [data[offset : offset + chunk_size] for offset in range(0, len(data), chunk_size)]
    manifest_parts = [
        {
            "index": index,
            "kind": f"chess_rebuild_bundle_part_{index:04d}",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for index, payload in enumerate(parts, start=1)
    ]
    manifest = {
        "schema": CHUNK_MANIFEST_SCHEMA,
        "bundle_size_bytes": len(data),
        "bundle_sha256": hashlib.sha256(data).hexdigest(),
        "part_count": len(parts),
        "parts": manifest_parts,
    }
    return parts, manifest


def encode_conversion_rebuild_chunk_manifest(manifest: dict[str, Any]) -> bytes:
    _validate_chunk_manifest(manifest)
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def decode_conversion_rebuild_chunk_manifest(data: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_invalid") from error
    if not isinstance(manifest, dict):
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_invalid")
    _validate_chunk_manifest(manifest)
    return manifest


def assemble_conversion_rebuild_bundle(
    manifest: dict[str, Any],
    part_payloads: dict[str, bytes],
) -> bytes:
    rows = _validate_chunk_manifest(manifest)
    expected_kinds = {str(row["kind"]) for row in rows}
    if set(part_payloads) != expected_kinds:
        raise ConversionRebuildBundleError("rebuild_chunk_parts_mismatch")

    ordered: list[bytes] = []
    for row in rows:
        kind = str(row["kind"])
        payload = part_payloads[kind]
        if len(payload) != int(row["size_bytes"]):
            raise ConversionRebuildBundleError(f"rebuild_chunk_size_mismatch:{kind}")
        if hashlib.sha256(payload).hexdigest() != str(row["sha256"]):
            raise ConversionRebuildBundleError(f"rebuild_chunk_integrity_failed:{kind}")
        ordered.append(payload)
    bundle = b"".join(ordered)
    if len(bundle) != int(manifest["bundle_size_bytes"]):
        raise ConversionRebuildBundleError("rebuild_bundle_size_mismatch")
    if hashlib.sha256(bundle).hexdigest() != str(manifest["bundle_sha256"]):
        raise ConversionRebuildBundleError("rebuild_bundle_integrity_failed")
    return bundle


def _validate_chunk_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("schema") != CHUNK_MANIFEST_SCHEMA:
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_schema_invalid")
    rows = manifest.get("parts")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_FILE_COUNT:
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_parts_invalid")
    if int(manifest.get("part_count") or -1) != len(rows):
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_part_count_mismatch")
    expected_indexes = list(range(1, len(rows) + 1))
    indexes: list[int] = []
    kinds: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ConversionRebuildBundleError("rebuild_chunk_manifest_row_invalid")
        index = int(row.get("index") or 0)
        kind = str(row.get("kind") or "")
        expected_kind = f"chess_rebuild_bundle_part_{index:04d}"
        digest = str(row.get("sha256") or "")
        size_bytes = int(row.get("size_bytes") or 0)
        if (
            index <= 0
            or kind != expected_kind
            or size_bytes <= 0
            or size_bytes > MAX_FILE_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ConversionRebuildBundleError("rebuild_chunk_manifest_row_invalid")
        indexes.append(index)
        kinds.append(kind)
    if indexes != expected_indexes or len(kinds) != len(set(kinds)):
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_order_invalid")
    bundle_size = int(manifest.get("bundle_size_bytes") or 0)
    bundle_digest = str(manifest.get("bundle_sha256") or "")
    if (
        bundle_size <= 0
        or bundle_size > MAX_TOTAL_BYTES
        or sum(int(row["size_bytes"]) for row in rows) != bundle_size
    ):
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_size_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", bundle_digest):
        raise ConversionRebuildBundleError("rebuild_chunk_manifest_digest_invalid")
    return rows


def build_conversion_rebuild_bundle(job_root: str | Path) -> tuple[bytes, dict[str, Any]]:
    root = Path(job_root).resolve()
    if not root.is_dir() or not root.name:
        raise ConversionRebuildBundleError("artifact_root_missing")
    selected = list(_selected_files(root))
    if not selected:
        raise ConversionRebuildBundleError("rebuild_files_missing")
    if len(selected) > MAX_FILE_COUNT:
        raise ConversionRebuildBundleError("rebuild_file_count_exceeded")

    manifest_files: list[dict[str, Any]] = []
    total_bytes = 0
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in selected:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise ConversionRebuildBundleError(f"rebuild_file_too_large:{relative}")
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ConversionRebuildBundleError("rebuild_total_size_exceeded")
            digest = hashlib.sha256(data).hexdigest()
            manifest_files.append({"path": relative, "size_bytes": len(data), "sha256": digest})
            archive.writestr(relative, data)
        manifest = {
            "schema": SCHEMA,
            "job_id": root.name,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "file_count": len(manifest_files),
            "total_uncompressed_bytes": total_bytes,
            "files": manifest_files,
        }
        archive.writestr(MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False, indent=2))
    payload = output.getvalue()
    return payload, {
        **manifest,
        "bundle_size_bytes": len(payload),
        "bundle_sha256": hashlib.sha256(payload).hexdigest(),
    }


def restore_conversion_rebuild_bundle(
    data: bytes,
    *,
    destination_root: str | Path,
    expected_job_id: str,
) -> dict[str, Any]:
    destination = Path(destination_root).resolve()
    job_id = str(expected_job_id or "").strip()
    if not job_id or destination.name != job_id:
        raise ConversionRebuildBundleError("rebuild_job_id_mismatch")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise ConversionRebuildBundleError("rebuild_bundle_invalid") from error
    with archive:
        try:
            manifest = json.loads(archive.read(MANIFEST_PATH).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConversionRebuildBundleError("rebuild_manifest_invalid") from error
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            raise ConversionRebuildBundleError("rebuild_manifest_schema_invalid")
        if str(manifest.get("job_id") or "") != job_id:
            raise ConversionRebuildBundleError("rebuild_manifest_job_id_mismatch")
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_FILE_COUNT:
            raise ConversionRebuildBundleError("rebuild_manifest_files_invalid")
        if int(manifest.get("file_count") or -1) != len(rows):
            raise ConversionRebuildBundleError("rebuild_manifest_file_count_mismatch")
        declared_paths = {str(row.get("path") or "") for row in rows if isinstance(row, dict)}
        archive_paths = {name for name in archive.namelist() if name != MANIFEST_PATH and not name.endswith("/")}
        if declared_paths != archive_paths:
            raise ConversionRebuildBundleError("rebuild_manifest_archive_mismatch")

        destination.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        total_bytes = 0
        for row in rows:
            if not isinstance(row, dict):
                raise ConversionRebuildBundleError("rebuild_manifest_row_invalid")
            relative = _safe_relative_path(str(row.get("path") or ""))
            expected_size = int(row.get("size_bytes") or -1)
            expected_digest = str(row.get("sha256") or "").strip().lower()
            payload = archive.read(relative.as_posix())
            total_bytes += len(payload)
            if len(payload) > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise ConversionRebuildBundleError("rebuild_payload_size_exceeded")
            if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_digest:
                raise ConversionRebuildBundleError(f"rebuild_file_integrity_failed:{relative.as_posix()}")
            target = (destination / Path(*relative.parts)).resolve()
            if destination not in target.parents:
                raise ConversionRebuildBundleError("rebuild_path_outside_destination")
            _atomic_write(target, payload)
            written.append(relative.as_posix())
    _atomic_write(
        destination / RESTORE_MARKER_FILENAME,
        json.dumps(
            {
                "schema": SCHEMA,
                "job_id": job_id,
                "restored_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "file_count": len(written),
                "total_uncompressed_bytes": total_bytes,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )
    return {
        "status": "restored",
        "schema": SCHEMA,
        "job_id": job_id,
        "file_count": len(written),
        "total_uncompressed_bytes": total_bytes,
        "files": written,
    }


def _selected_files(root: Path) -> Iterable[Path]:
    for directory in ("review", "semantic_chess_html"):
        base = root / directory
        if base.is_dir():
            yield from _regular_files(base)
    for directory in ("report", "reports", "log"):
        base = root / directory
        if not base.is_dir():
            continue
        for path in _regular_files(base):
            if _include_report_file(path.relative_to(root)):
                yield path
    verified_epub = root / "output" / "chess_verified_positions.epub"
    if verified_epub.is_file():
        yield verified_epub


def _regular_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path.resolve()


def _include_report_file(relative: Path) -> bool:
    name = relative.name.lower()
    if name in {
        "chess_diagrams.json",
        "chess_diagrams_verified.json",
        "chess_games.html",
        "chess_games.pgn",
        "chess_exercises.pgn",
        "chess_verified_positions.pgn",
        "chess_verified_fen_publication.json",
        "fen_verified_labels.jsonl",
        "fen_review_excluded.jsonl",
        "fen_verified_labels_validation.json",
        "fen_review_corpus_export.json",
        "fen_review_corpus_export.md",
    }:
        return True
    return name.endswith((".quality.json", ".quality.md", ".runtime.json"))


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value or "").replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or path.parts[0] not in {
        "review",
        "semantic_chess_html",
        "report",
        "reports",
        "log",
        "output",
    }:
        raise ConversionRebuildBundleError("rebuild_path_invalid")
    return path


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise
