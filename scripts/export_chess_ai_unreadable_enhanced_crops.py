from __future__ import annotations

import argparse
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_CASES_JSONL = Path(
    "reports/chess_fen/ai_autoread/fundamenty_full_live/with_followups_import/strict_fen_recovery_cases.jsonl"
)
DEFAULT_REPORT_JSON = Path("reports/chess_fen/fundamenty_exact_label_lookup_fix.json")
DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/ai_autoread/fundamenty_full_live/with_followups_import")
RETRY_RECOMMENDATION = "run_enhanced_vision_retry"


def export_chess_ai_unreadable_enhanced_crops(
    cases_jsonl: str | Path = DEFAULT_CASES_JSONL,
    report_json: str | Path = DEFAULT_REPORT_JSON,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    epub_path: str | Path | None = None,
    scale: int = 3,
) -> dict[str, Any]:
    cases_path = Path(cases_jsonl)
    report_path = Path(report_json)
    target = Path(output_dir)
    crops_dir = target / "unreadable_enhanced_crops"
    manifest_path = target / "ai_unreadable_enhanced_manifest.jsonl"
    target.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    cases = [case for case in _read_jsonl(cases_path) if case.get("recommendation") == RETRY_RECOMMENDATION]
    source_epub = Path(epub_path) if epub_path else _epub_path_from_report(report_path)
    archive: zipfile.ZipFile | None = None
    archive_names: dict[str, str] = {}
    if source_epub.is_file():
        archive = zipfile.ZipFile(source_epub)
        archive_names = _archive_name_index(archive)

    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases, start=1):
            filename = str(case.get("filename") or "").strip()
            row = _base_manifest_row(case, filename=filename)
            archive_name = _lookup_archive_name(archive_names, filename)
            if archive is None or not archive_name:
                rows.append({**row, "status": "crop_missing", "enhanced_crop_path": ""})
                continue
            enhanced_filename = _enhanced_filename(case, filename, index)
            enhanced_path = crops_dir / enhanced_filename
            try:
                enhanced_path.write_bytes(_enhance_crop_bytes(archive.read(archive_name), scale=scale))
            except Exception as exc:
                rows.append(
                    {
                        **row,
                        "status": "crop_unreadable",
                        "enhanced_crop_path": "",
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
                continue
            rows.append({**row, "status": "enhanced_crop_exported", "enhanced_crop_path": str(enhanced_path)})
    finally:
        if archive is not None:
            archive.close()

    _write_jsonl(manifest_path, rows)
    summary = {
        "status": "ok",
        "mode": "ai_unreadable_enhanced_crop_export",
        "release_safe": False,
        "cases_jsonl": str(cases_path),
        "report_json": str(report_path),
        "source_epub": str(source_epub),
        "output_dir": str(target),
        "enhanced_crops_dir": str(crops_dir),
        "manifest_jsonl": str(manifest_path),
        "candidate_count": len(cases),
        "exported_count": sum(1 for row in rows if row["status"] == "enhanced_crop_exported"),
        "missing_count": sum(1 for row in rows if row["status"] == "crop_missing"),
        "unreadable_count": sum(1 for row in rows if row["status"] == "crop_unreadable"),
        "policy": "ai_autoread_enhanced_retry_only_no_strict_or_canonical_fen",
    }
    (target / "ai_unreadable_enhanced_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _base_manifest_row(case: dict[str, Any], *, filename: str) -> dict[str, Any]:
    return {
        "id": str(case.get("id") or ""),
        "page": case.get("page"),
        "filename": filename,
        "original_status": str(case.get("ai_readout_status") or ""),
        "recommendation": str(case.get("recommendation") or ""),
        "release_safe": False,
    }


def _enhance_crop_bytes(data: bytes, *, scale: int) -> bytes:
    from PIL import Image, ImageFilter

    scale = max(1, int(scale))
    with Image.open(BytesIO(data)) as image:
        image = image.convert("RGB")
        if scale > 1:
            resampling = getattr(Image.Resampling, "LANCZOS", Image.BICUBIC)
            image = image.resize((image.width * scale, image.height * scale), resampling)
        image = image.filter(ImageFilter.SHARPEN)
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _enhanced_filename(case: dict[str, Any], filename: str, index: int) -> str:
    source_stem = Path(filename or f"crop_{index:04d}.png").stem
    row_id = _safe_token(str(case.get("id") or f"unreadable_{index:04d}"))
    return f"{row_id}__{_safe_token(source_stem)}__enhanced.png"


def _safe_token(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return safe.strip("_") or "crop"


def _epub_path_from_report(report_path: Path) -> Path:
    if not report_path.is_file():
        return Path("")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output_path = Path(str(report.get("output_path") or "").strip())
    if output_path.is_absolute():
        return output_path
    candidate = report_path.parent / output_path
    return candidate if candidate.exists() else output_path


def _archive_name_index(archive: zipfile.ZipFile) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in archive.namelist():
        normalized = name.replace("\\", "/")
        result.setdefault(normalized, name)
        result.setdefault(Path(normalized).name, name)
    return result


def _lookup_archive_name(archive_names: dict[str, str], filename: str) -> str:
    if not filename:
        return ""
    normalized = filename.replace("\\", "/")
    return archive_names.get(normalized) or archive_names.get(Path(normalized).name, "")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export enhanced AI-only crop retries for unreadable chess FEN cases.")
    parser.add_argument("--cases-jsonl", default=str(DEFAULT_CASES_JSONL))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--epub-path", default="")
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args(argv)
    summary = export_chess_ai_unreadable_enhanced_crops(
        args.cases_jsonl,
        args.report_json,
        output_dir=args.output_dir,
        epub_path=args.epub_path or None,
        scale=args.scale,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
