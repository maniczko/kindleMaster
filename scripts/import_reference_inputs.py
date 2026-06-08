from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_INPUT_TYPES = ("pdf", "docx")
DEFAULT_MANIFEST_PATH = Path("reference_inputs") / "manifest.json"
DEFAULT_LABELS_PATH = Path("reference_inputs") / "ml_labels.json"


def import_reference_inputs(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    labels_path: str | Path = DEFAULT_LABELS_PATH,
    repo_root: str | Path = ".",
    input_types: tuple[str, ...] = SUPPORTED_INPUT_TYPES,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    manifest_file = _resolve_path(root, manifest_path)
    labels_file = _resolve_path(root, labels_path)
    manifest = _load_json_object(manifest_file, fallback={"version": 2, "root_dir": ".", "cases": []})
    labels = _load_json_object(
        labels_file,
        fallback={
            "version": 1,
            "label_source": "reference_input_importer",
            "classes": [
                "book_reflow",
                "magazine_reflow",
                "diagram_book_reflow",
                "scanned_reflow",
                "docx_reflow",
                "fixed_layout_fallback",
            ],
            "cases": {},
        },
    )

    cases = manifest.setdefault("cases", [])
    if not isinstance(cases, list):
        raise ValueError(f"Invalid manifest cases list: {manifest_file}")
    label_cases = labels.setdefault("cases", {})
    if not isinstance(label_cases, dict):
        raise ValueError(f"Invalid ML labels cases object: {labels_file}")

    known_targets = {
        _normalize_rel_path(str(case.get("target_path") or case.get("target") or ""))
        for case in cases
        if isinstance(case, Mapping)
    }
    known_ids = {str(case.get("id") or "") for case in cases if isinstance(case, Mapping)}

    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for input_type in input_types:
        normalized_type = input_type.strip().lower()
        if normalized_type not in SUPPORTED_INPUT_TYPES:
            skipped.append({"input_type": input_type, "reason": "unsupported_input_type"})
            continue
        input_root = root / "reference_inputs" / normalized_type
        if not input_root.is_dir():
            skipped.append({"input_type": normalized_type, "reason": "missing_input_dir", "path": str(input_root)})
            continue
        for path in sorted(input_root.glob(f"*.{normalized_type}"), key=lambda item: item.name.lower()):
            rel_path = _normalize_rel_path(path.relative_to(root).as_posix())
            if rel_path in known_targets:
                skipped.append({"path": rel_path, "reason": "already_in_manifest"})
                continue
            case_id = _unique_case_id(path.stem, normalized_type, known_ids)
            profile = _infer_reference_profile(path.name, normalized_type)
            case = {
                "id": case_id,
                "document_class": profile["document_class"],
                "input_type": normalized_type,
                "language": profile["language"],
                "quick_smoke": False,
                "release_strict": False,
                "target": rel_path,
                "notes": profile["notes"],
                "source_path": rel_path,
                "target_path": rel_path,
                "size_bytes": path.stat().st_size,
            }
            label = {
                "route_label": profile["route_label"],
                "label_quality": profile["label_quality"],
                "notes": profile["label_notes"],
            }
            cases.append(case)
            label_cases[case_id] = label
            known_targets.add(rel_path)
            known_ids.add(case_id)
            imported.append(
                {
                    "case_id": case_id,
                    "path": rel_path,
                    "document_class": case["document_class"],
                    "route_label": label["route_label"],
                    "label_quality": label["label_quality"],
                    "size_bytes": case["size_bytes"],
                }
            )

    if imported and not dry_run:
        _write_json(manifest_file, manifest)
        _write_json(labels_file, labels)

    return {
        "status": "dry_run" if dry_run else "updated",
        "manifest_path": str(manifest_file),
        "labels_path": str(labels_file),
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "imported": imported,
        "skipped": skipped,
    }


def _infer_reference_profile(filename: str, input_type: str) -> dict[str, str]:
    normalized = filename.lower()
    if input_type == "docx":
        return {
            "document_class": "docx_reference",
            "route_label": "docx_reflow",
            "language": "en",
            "label_quality": "weak",
            "notes": "Imported DOCX reference input; review language and document class before treating as curated.",
            "label_notes": "Auto-inferred DOCX route label. Review before promotion to curated training data.",
        }
    if any(token in normalized for token in ("chess", "jobava", "london", "woodpecker", "tactits", "tactics", "fundamenty")):
        language = "pl" if "fundamenty" in normalized else "en"
        return {
            "document_class": "diagram_training_book",
            "route_label": "diagram_book_reflow",
            "language": language,
            "label_quality": "weak",
            "notes": "Imported chess/training-book PDF; useful for diagram-heavy routing and Kindle structure regression.",
            "label_notes": "Auto-inferred diagram/training route from filename. Review after first corpus run.",
        }
    if any(token in normalized for token in ("scan", "ocr")):
        return {
            "document_class": "scan_ocr",
            "route_label": "scanned_reflow",
            "language": "pl" if _looks_polish_filename(normalized) else "en",
            "label_quality": "weak",
            "notes": "Imported scan/OCR reference input.",
            "label_notes": "Auto-inferred scanned route from filename. Review OCR quality after conversion.",
        }
    if any(token in normalized for token in ("magazine", "catalog", "layout")):
        return {
            "document_class": "magazine_layout",
            "route_label": "magazine_reflow",
            "language": "pl" if _looks_polish_filename(normalized) else "en",
            "label_quality": "weak",
            "notes": "Imported layout-heavy/magazine reference input.",
            "label_notes": "Auto-inferred magazine route from filename. Review before curated training use.",
        }
    return {
        "document_class": "book_reference",
        "route_label": "book_reflow",
        "language": "pl" if _looks_polish_filename(normalized) else "en",
        "label_quality": "weak",
        "notes": "Imported book-like PDF reference input.",
        "label_notes": "Auto-inferred book route fallback. Review after first analysis if this is layout-heavy or scanned.",
    }


def _looks_polish_filename(normalized_filename: str) -> bool:
    return any(token in normalized_filename for token in ("fundamenty", "polski", "pl_", "_pl", "-pl"))


def _unique_case_id(stem: str, input_type: str, known_ids: set[str]) -> str:
    base = _slugify(stem)
    if base.startswith("oceanofpdf_com_"):
        base = base.removeprefix("oceanofpdf_com_")
    base = base[:80].strip("_") or "reference_input"
    candidate = f"{base}_{input_type}"
    if candidate not in known_ids:
        return candidate
    index = 2
    while f"{candidate}_{index}" in known_ids:
        index += 1
    return f"{candidate}_{index}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", slug).strip("_")


def _normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").strip()


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _load_json_object(path: Path, *, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import new reference_inputs PDF/DOCX files into manifest and ML labels.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS_PATH))
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--input-type", action="append", choices=SUPPORTED_INPUT_TYPES, default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = import_reference_inputs(
        manifest_path=args.manifest,
        labels_path=args.labels,
        repo_root=args.repo_root,
        input_types=tuple(args.input_type or SUPPORTED_INPUT_TYPES),
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
