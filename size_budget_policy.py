from __future__ import annotations

import io
import json
import math
from pathlib import Path
from typing import Any
from zipfile import ZipFile


POLICY_PATH = Path(__file__).resolve().parent / "reference_inputs" / "size_budgets.json"
PUBLICATION_PROFILE_BUDGET_KEYS = {
    "diagram_book_reflow": "diagram_book_reflow_balanced",
}


class SizeBudgetExceededError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.payload = payload


def normalize_budget_key(raw_value: str) -> str:
    normalized = str(raw_value or "").strip().lower()
    for token in (" ", "-", "/", "\\"):
        normalized = normalized.replace(token, "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def load_size_budget_policy(policy_path: str | Path | None = None) -> dict[str, Any]:
    resolved_path = Path(policy_path or POLICY_PATH).resolve()
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    document_classes = {
        normalize_budget_key(key): _normalize_policy_entry(entry)
        for key, entry in (payload.get("document_classes") or {}).items()
    }
    render_budget_classes = {
        normalize_budget_key(key): _normalize_render_entry(entry)
        for key, entry in (payload.get("render_budget_classes") or {}).items()
    }
    return {
        "version": int(payload.get("version", 1)),
        "updated_at": str(payload.get("updated_at", "")),
        "path": str(resolved_path),
        "document_classes": document_classes,
        "render_budget_classes": render_budget_classes,
    }


def get_document_size_budget(document_class: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    loaded_policy = policy or load_size_budget_policy()
    return loaded_policy["document_classes"].get(normalize_budget_key(document_class))


def get_render_budget_policy(render_budget_class: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    loaded_policy = policy or load_size_budget_policy()
    return loaded_policy["render_budget_classes"].get(normalize_budget_key(render_budget_class))


def resolve_publication_size_budget(
    publication_profile: str,
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    loaded_policy = policy or load_size_budget_policy()
    normalized_profile = normalize_budget_key(publication_profile)
    budget_key = PUBLICATION_PROFILE_BUDGET_KEYS.get(normalized_profile)
    if not budget_key:
        return None, None
    return budget_key, get_document_size_budget(budget_key, policy=loaded_policy)


def get_budget_attempt_settings(
    budget: dict[str, Any] | None,
    attempt: str,
) -> dict[str, int]:
    if not budget:
        return {}
    normalized_attempt = normalize_budget_key(attempt) or "primary"
    raw_settings = budget.get(normalized_attempt) or {}
    if not raw_settings:
        return {}
    return {
        "diagram_long_edge": int(raw_settings["diagram_long_edge"]),
        "diagram_palette_colors": int(raw_settings["diagram_palette_colors"]),
        "diagram_target_dpi": int(raw_settings["diagram_target_dpi"]),
        "raster_long_edge": int(raw_settings["raster_long_edge"]),
        "raster_jpeg_quality": int(raw_settings["raster_jpeg_quality"]),
    }


def inspect_epub_archive(epub_bytes: bytes) -> dict[str, Any]:
    with ZipFile(io.BytesIO(epub_bytes), "r") as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        image_infos = [
            info for info in infos if info.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        ]
        largest_assets = sorted(infos, key=lambda info: info.file_size, reverse=True)[:5]
        return {
            "entry_count": len(infos),
            "image_count": len(image_infos),
            "largest_assets": [
                {
                    "name": info.filename,
                    "size_bytes": info.file_size,
                    "compressed_bytes": info.compress_size,
                }
                for info in largest_assets
            ],
        }


def evaluate_size_budget(
    *,
    budget_key: str,
    budget: dict[str, Any] | None,
    epub_size_bytes: int,
    inspection: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    normalized_key = normalize_budget_key(budget_key)
    if not budget:
        return {
            "status": "failed",
            "budget_key": normalized_key,
            "epub_size_bytes": int(epub_size_bytes),
            "warn_bytes": None,
            "hard_bytes": None,
            "inspection": inspection,
            "message": f"Brak zdefiniowanego budzetu rozmiaru dla {label} {normalized_key}. Smoke wymaga jawnego progu.",
        }

    warn_bytes = int(budget["warn_bytes"])
    hard_bytes = int(budget["hard_bytes"])
    if epub_size_bytes > hard_bytes:
        status = "failed"
        message = f"Przekroczono hard gate dla {label} {normalized_key}: {epub_size_bytes} B > {hard_bytes} B."
    elif epub_size_bytes > warn_bytes:
        status = "passed_with_warnings"
        message = (
            f"Rozmiar EPUB dla {label} {normalized_key} przekracza prog ostrzezenia "
            f"({epub_size_bytes} B > {warn_bytes} B), ale miesci sie w hard gate {hard_bytes} B."
        )
    else:
        status = "passed"
        message = (
            f"Rozmiar EPUB dla {label} {normalized_key} miesci sie w budzecie: "
            f"{epub_size_bytes} B <= {warn_bytes} B warning / {hard_bytes} B hard."
        )
    return {
        "status": status,
        "budget_key": normalized_key,
        "epub_size_bytes": int(epub_size_bytes),
        "warn_bytes": warn_bytes,
        "hard_bytes": hard_bytes,
        "inspection": inspection,
        "message": message,
        "notes": str(budget.get("notes", "")),
        "baseline_cases": list(budget.get("baseline_cases", []) or []),
    }


def build_document_budget_proposals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[int]] = {}
    for row in rows:
        document_class = normalize_budget_key(str(row.get("document_class", "")))
        epub_size_bytes = int(row.get("epub_size_bytes", 0) or 0)
        if not document_class or epub_size_bytes <= 0:
            continue
        grouped.setdefault(document_class, []).append(epub_size_bytes)

    proposals: dict[str, Any] = {}
    for document_class, sizes in sorted(grouped.items()):
        largest = max(sizes)
        warn_bytes = _round_bytes(max(largest, math.ceil(largest * 1.2)))
        hard_bytes = _round_bytes(max(warn_bytes, math.ceil(largest * 1.4)))
        proposals[document_class] = {
            "observed_cases": len(sizes),
            "largest_observed_bytes": largest,
            "suggested_warn_bytes": warn_bytes,
            "suggested_hard_bytes": hard_bytes,
        }
    return {
        "generated_at": POLICY_PATH.stat().st_mtime if POLICY_PATH.exists() else None,
        "document_classes": proposals,
    }


def _normalize_policy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "warn_bytes": int(entry["warn_bytes"]),
        "hard_bytes": int(entry["hard_bytes"]),
        "baseline_cases": list(entry.get("baseline_cases", []) or []),
        "notes": str(entry.get("notes", "")),
        "updated_at": str(entry.get("updated_at", "")),
    }
    for attempt in ("primary", "fallback"):
        raw_settings = entry.get(attempt)
        if raw_settings and "diagram_long_edge" in raw_settings:
            normalized[attempt] = _normalize_diagram_optimizer_settings(raw_settings)
    return normalized


def _normalize_render_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_policy_entry(entry)
    normalized["primary"] = _normalize_render_settings(entry.get("primary") or {})
    normalized["fallback"] = _normalize_render_settings(entry.get("fallback") or {})
    return normalized


def _normalize_render_settings(raw_settings: dict[str, Any]) -> dict[str, int]:
    return {
        "dpi": int(raw_settings["dpi"]),
        "jpeg_quality": int(raw_settings["jpeg_quality"]),
        "jpeg_subsampling": int(raw_settings["jpeg_subsampling"]),
        "cover_dpi": int(raw_settings["cover_dpi"]),
        "cover_quality": int(raw_settings["cover_quality"]),
    }


def _normalize_diagram_optimizer_settings(raw_settings: dict[str, Any]) -> dict[str, int]:
    return {
        "diagram_long_edge": int(raw_settings["diagram_long_edge"]),
        "diagram_palette_colors": int(raw_settings["diagram_palette_colors"]),
        "diagram_target_dpi": int(raw_settings["diagram_target_dpi"]),
        "raster_long_edge": int(raw_settings["raster_long_edge"]),
        "raster_jpeg_quality": int(raw_settings["raster_jpeg_quality"]),
    }


def _round_bytes(raw_value: int) -> int:
    bucket = 8 * 1024
    return int(math.ceil(max(raw_value, bucket) / bucket) * bucket)
