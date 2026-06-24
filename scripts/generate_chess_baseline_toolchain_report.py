from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from premium_tools import detect_toolchain


DEFAULT_OUTPUT_DIR = Path("reports/audit")


def _status_for_required_parts(required: list[str], *, total_parts: int, optional: bool = False) -> str:
    if not required:
        return "ok"
    if optional:
        return "degraded"
    if len(required) >= total_parts:
        return "unavailable"
    return "degraded"


def _module_status(toolchain: dict[str, Any], module: str) -> bool:
    modules = toolchain.get("python_modules") or {}
    return bool(modules.get(module))


def _command_status(toolchain: dict[str, Any], command: str) -> bool:
    commands = toolchain.get("commands") or {}
    return bool(commands.get(command))


def build_chess_baseline_toolchain_reports(
    toolchain: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    surfaces = toolchain.get("verification_surfaces") or {}
    capabilities = toolchain.get("conversion_capabilities") or {}

    ocr_required = []
    if not _module_status(toolchain, "pytesseract"):
        ocr_required.append("pytesseract")
    if not _command_status(toolchain, "tesseract"):
        ocr_required.append("tesseract")

    fen_required = []
    if not _module_status(toolchain, "chess"):
        fen_required.append("python-chess")

    pgn_required = []
    if not _module_status(toolchain, "chess"):
        pgn_required.append("python-chess")

    diagnostic_required = []
    if not _module_status(toolchain, "cv2"):
        diagnostic_required.append("opencv-python-headless")

    baseline = {
        "schema_version": "kindlemaster.chess_baseline_toolchain.v1",
        "generated_at": generated_at,
        "statuses": {
            "ocr": _status_for_required_parts(ocr_required, total_parts=2),
            "fen": _status_for_required_parts(fen_required, total_parts=1),
            "pgn": _status_for_required_parts(pgn_required, total_parts=1),
            "crop_grid_diagnostics": _status_for_required_parts(diagnostic_required, total_parts=1, optional=True),
        },
        "dependency_decisions": {
            "pytesseract": {
                "support_level": "runtime",
                "decision": "declared_runtime_dependency",
                "reason": "ocr_module.py uses direct Tesseract OCR as a scanned/OCR fallback.",
            },
            "opencv-python-headless": {
                "support_level": "developer_diagnostic",
                "decision": "declared_dev_dependency",
                "reason": "OpenCV is audit-only for chess crop/grid diagnostics and must not change default runtime recognition.",
            },
        },
        "python_modules": {
            "pytesseract": _module_status(toolchain, "pytesseract"),
            "python_chess": _module_status(toolchain, "chess"),
            "opencv": _module_status(toolchain, "cv2"),
            "ocrmypdf": _module_status(toolchain, "ocrmypdf"),
        },
        "commands": {
            "tesseract": _command_status(toolchain, "tesseract"),
            "ocrmypdf": _command_status(toolchain, "ocrmypdf"),
            "qpdf": _command_status(toolchain, "qpdf"),
            "ghostscript": _command_status(toolchain, "ghostscript"),
        },
        "verification_surfaces": {
            name: {
                "status": surface.get("status"),
                "missing_requirements": surface.get("missing_requirements", []),
            }
            for name, surface in surfaces.items()
            if name in {"quick", "corpus", "release"}
        },
        "conversion_capabilities": {
            name: {
                "status": capability.get("status"),
                "missing_requirements": capability.get("missing_requirements", []),
            }
            for name, capability in capabilities.items()
            if name in {"ocr_pipeline", "core_conversion"}
        },
    }

    dependency_gaps = {
        "schema_version": "kindlemaster.chess_dependency_gap.v1",
        "generated_at": generated_at,
        "gaps": [],
    }
    if "pytesseract" in ocr_required:
        dependency_gaps["gaps"].append(
            {
                "dependency": "pytesseract>=0.3.13",
                "support_level": "runtime",
                "impact": "Direct Tesseract OCR fallback in ocr_module.py is unavailable.",
                "next_action": "Run python kindlemaster.py bootstrap --runtime-only.",
            }
        )
    if "tesseract" in ocr_required:
        dependency_gaps["gaps"].append(
            {
                "dependency": "Tesseract OCR executable",
                "support_level": "optional_external_tool",
                "impact": "Scanned/OCR-heavy PDFs cannot use direct Tesseract fallback.",
                "next_action": "Install Tesseract and re-run python kindlemaster.py doctor.",
            }
        )
    if "opencv-python-headless" in diagnostic_required:
        dependency_gaps["gaps"].append(
            {
                "dependency": "opencv-python-headless>=4.10.0",
                "support_level": "developer_diagnostic",
                "impact": "Future chess crop/grid geometry diagnostics are unavailable.",
                "next_action": "Run python kindlemaster.py bootstrap for the developer profile.",
            }
        )
    if fen_required or pgn_required:
        dependency_gaps["gaps"].append(
            {
                "dependency": "chess>=1.11,<2",
                "support_level": "runtime",
                "impact": "Strict FEN/PGN validation and replay proof are unavailable.",
                "next_action": "Run python kindlemaster.py bootstrap --runtime-only.",
            }
        )
    dependency_gaps["status"] = "ok" if not dependency_gaps["gaps"] else "degraded"
    return baseline, dependency_gaps


def write_chess_baseline_toolchain_reports(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    refresh: bool = True,
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    baseline, gaps = build_chess_baseline_toolchain_reports(detect_toolchain(refresh=refresh))
    baseline_path = output / "baseline_toolchain_report.json"
    gaps_path = output / "dependency_gap_report.json"
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gaps_path.write_text(json.dumps(gaps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "baseline_toolchain_report": str(baseline_path),
        "dependency_gap_report": str(gaps_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate chess FEN/PGN baseline toolchain reports.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    print(json.dumps(write_chess_baseline_toolchain_reports(args.output_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
