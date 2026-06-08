from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


DEFAULT_MANIFEST_PATH = Path("reference_inputs") / "manifest.json"
DEFAULT_LABELS_PATH = Path("reference_inputs") / "ml_labels.json"
DEFAULT_OUTPUT_DIR = Path("reference_inputs") / "pdf_samples"
DEFAULT_MAX_PAGES = 80
DEFAULT_MIN_PAGES = 150
DEFAULT_MIN_SIZE_BYTES = 20 * 1024 * 1024


def sample_reference_inputs(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    labels_path: str | Path = DEFAULT_LABELS_PATH,
    repo_root: str | Path = ".",
    input_types: tuple[str, ...] = ("pdf",),
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    max_pages: int = DEFAULT_MAX_PAGES,
    min_pages: int = DEFAULT_MIN_PAGES,
    min_size_bytes: int = DEFAULT_MIN_SIZE_BYTES,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    manifest_file = _resolve_path(root, manifest_path)
    labels_file = _resolve_path(root, labels_path)
    output_root = _resolve_path(root, output_dir)
    manifest = _load_json_object(manifest_file)
    labels = _load_json_object(labels_file)
    cases = manifest.get("cases")
    label_cases = labels.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"Invalid manifest cases list: {manifest_file}")
    if not isinstance(label_cases, dict):
        raise ValueError(f"Invalid ML labels cases object: {labels_file}")

    normalized_input_types = {item.strip().lower() for item in input_types}
    existing_ids = {str(case.get("id") or "") for case in cases if isinstance(case, Mapping)}
    existing_targets = {
        _normalize_rel_path(str(case.get("target_path") or case.get("target") or ""))
        for case in cases
        if isinstance(case, Mapping)
    }

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for case in list(cases):
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id") or "").strip()
        input_type = str(case.get("input_type") or "").strip().lower()
        if input_type not in normalized_input_types:
            continue
        if input_type != "pdf":
            skipped.append({"case_id": case_id, "reason": f"unsupported_input_type:{input_type or 'unknown'}"})
            continue
        if case.get("sample_of"):
            skipped.append({"case_id": case_id, "reason": "already_sample_case"})
            continue
        input_path = _case_input_path(root, case)
        if input_path is None or not input_path.is_file():
            skipped.append({"case_id": case_id, "reason": "missing_input", "path": str(input_path or "")})
            continue
        if _normalize_rel_path(input_path.as_posix()).find("/pdf_samples/") >= 0:
            skipped.append({"case_id": case_id, "reason": "sample_path_not_resampled"})
            continue
        try:
            page_count = _read_pdf_page_count(input_path)
        except Exception as error:
            skipped.append({"case_id": case_id, "reason": "page_count_failed", "error": str(error), "path": str(input_path)})
            continue
        size_bytes = input_path.stat().st_size
        if page_count <= max_pages:
            skipped.append({"case_id": case_id, "reason": "already_within_max_pages", "page_count": page_count})
            continue
        if page_count <= min_pages and size_bytes <= min_size_bytes:
            skipped.append(
                {
                    "case_id": case_id,
                    "reason": "below_sampling_threshold",
                    "page_count": page_count,
                    "size_bytes": size_bytes,
                }
            )
            continue

        sample_case_id = _unique_case_id(f"{case_id}_sample_{max_pages}p", existing_ids)
        sample_filename = f"{_slugify(Path(input_path).stem)[:80]}_sample_{max_pages}p.pdf"
        sample_path = output_root / sample_filename
        sample_rel_path = _normalize_rel_path(sample_path.relative_to(root).as_posix())
        if sample_rel_path in existing_targets:
            skipped.append({"case_id": case_id, "reason": "sample_already_in_manifest", "sample_path": sample_rel_path})
            continue

        if not dry_run:
            output_root.mkdir(parents=True, exist_ok=True)
            _write_pdf_sample(input_path, sample_path, max_pages=max_pages)
            sample_size_bytes = sample_path.stat().st_size
        else:
            sample_size_bytes = 0

        sample_case = {
            "id": sample_case_id,
            "document_class": str(case.get("document_class") or "book_reference"),
            "input_type": "pdf",
            "language": str(case.get("language") or "en"),
            "quick_smoke": False,
            "release_strict": False,
            "target": sample_rel_path,
            "notes": f"Sample of {case_id}; first {max_pages} pages for fast ML/corpus routing feedback.",
            "source_path": sample_rel_path,
            "target_path": sample_rel_path,
            "size_bytes": sample_size_bytes,
            "sample_of": case_id,
            "sample_pages": max_pages,
            "source_page_count": page_count,
            "source_size_bytes": size_bytes,
        }
        source_label = label_cases.get(case_id) if isinstance(label_cases.get(case_id), Mapping) else {}
        label_cases[sample_case_id] = {
            "route_label": str(source_label.get("route_label") or "book_reflow"),
            "label_quality": "weak_sample",
            "notes": f"Sample inherits route label from {case_id}; review before promotion to curated training data.",
        }
        if isinstance(case, dict):
            case["ml_training"] = "full_corpus_only"
            case["sample_case_id"] = sample_case_id
            case["sample_target_path"] = sample_rel_path
        cases.append(sample_case)
        existing_ids.add(sample_case_id)
        existing_targets.add(sample_rel_path)
        created.append(
            {
                "case_id": sample_case_id,
                "sample_of": case_id,
                "path": sample_rel_path,
                "route_label": label_cases[sample_case_id]["route_label"],
                "label_quality": "weak_sample",
                "source_page_count": page_count,
                "sample_pages": max_pages,
                "source_size_bytes": size_bytes,
                "sample_size_bytes": sample_size_bytes,
            }
        )

    if created and not dry_run:
        _write_json(manifest_file, manifest)
        _write_json(labels_file, labels)

    return {
        "status": "dry_run" if dry_run else "updated",
        "manifest_path": str(manifest_file),
        "labels_path": str(labels_file),
        "output_dir": str(output_root),
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "skipped": skipped,
        "thresholds": {
            "max_pages": max_pages,
            "min_pages": min_pages,
            "min_size_bytes": min_size_bytes,
        },
    }


def _read_pdf_page_count(path: Path) -> int:
    import fitz

    with fitz.open(path) as document:
        return int(document.page_count)


def _write_pdf_sample(source_path: Path, sample_path: Path, *, max_pages: int) -> None:
    import fitz

    with fitz.open(source_path) as source:
        last_page = min(max_pages, source.page_count) - 1
        if last_page < 0:
            raise ValueError(f"PDF has no pages: {source_path}")
        sample = fitz.open()
        try:
            sample.insert_pdf(source, from_page=0, to_page=last_page)
            sample.save(sample_path)
        finally:
            sample.close()


def _case_input_path(root: Path, case: Mapping[str, Any]) -> Path | None:
    raw_path = str(case.get("target_path") or case.get("target") or case.get("source_path") or "").strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    return candidate if candidate.is_absolute() else root / candidate


def _unique_case_id(base: str, known_ids: set[str]) -> str:
    candidate = _slugify(base)[:96].strip("_") or "pdf_sample"
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


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create fast ML/corpus PDF samples without modifying source PDFs.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS_PATH))
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--input-type", action="append", choices=("pdf",), default=[])
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--min-pages", type=int, default=DEFAULT_MIN_PAGES)
    parser.add_argument("--min-size-bytes", type=int, default=DEFAULT_MIN_SIZE_BYTES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = sample_reference_inputs(
        manifest_path=args.manifest,
        labels_path=args.labels,
        repo_root=args.repo_root,
        input_types=tuple(args.input_type or ("pdf",)),
        output_dir=args.output_dir,
        max_pages=args.max_pages,
        min_pages=args.min_pages,
        min_size_bytes=args.min_size_bytes,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
