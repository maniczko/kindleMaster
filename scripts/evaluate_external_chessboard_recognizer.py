from __future__ import annotations

import argparse
import glob
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_position_recognizer import load_piece_templates, recognize_chess_position_from_image, validate_fen


DEFAULT_TEMPLATE_DIR = REPO_ROOT / "reference_inputs" / "chess_fen" / "templates" / "fundamenty_merida_like"


def evaluate_external_chessboard_recognizer(
    crop_paths: Iterable[str | Path],
    *,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
    external_command: str = "",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    templates = load_piece_templates(template_dir) if Path(template_dir).exists() else {}
    cases: list[dict[str, Any]] = []
    for crop_path_value in crop_paths:
        crop_path = Path(crop_path_value)
        local = _local_recognition(crop_path, templates)
        external = _external_recognition(crop_path, external_command) if external_command else _missing_external_result()
        selected = _select_candidate(local, external)
        cases.append(
            {
                "crop_path": str(crop_path),
                "local_fen": local["fen"],
                "local": local,
                "external_fen": external["fen"],
                "external": external,
                "selected_fen": selected["fen"],
                "selected_source": selected["source"],
                "selected_reason": selected["reason"],
            }
        )
    summary = {
        "case_count": len(cases),
        "external_enabled": bool(external_command),
        "local_fen_count": len([case for case in cases if case["local_fen"]]),
        "external_fen_count": len([case for case in cases if case["external_fen"]]),
        "agreement_count": len(
            [case for case in cases if case["local_fen"] and case["local_fen"] == case["external_fen"]]
        ),
        "selected_external_count": len([case for case in cases if case["selected_source"] == "external"]),
        "cases": cases,
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _local_recognition(crop_path: Path, templates: dict[str, Any]) -> dict[str, Any]:
    if not crop_path.is_file():
        return {"source": "local", "fen": "", "confidence": 0.0, "warnings": ["crop_missing"], "valid": False}
    if not templates:
        return {"source": "local", "fen": "", "confidence": 0.0, "warnings": ["local_templates_missing"], "valid": False}
    result = recognize_chess_position_from_image(crop_path.read_bytes(), piece_templates=templates).to_dict()
    fen = str(result.get("fen") or "").strip()
    valid, validation_warnings = validate_fen(fen) if fen else (False, ["fen_missing"])
    return {
        "source": "local",
        "fen": fen,
        "confidence": float(result.get("confidence") or 0.0),
        "warnings": sorted(set([*result.get("warnings", []), *validation_warnings])),
        "valid": bool(valid),
        "method": result.get("method") or "",
    }


def _external_recognition(crop_path: Path, external_command: str) -> dict[str, Any]:
    if not crop_path.is_file():
        return {"source": "external", "fen": "", "confidence": 0.0, "warnings": ["crop_missing"], "valid": False}
    command = [part.format(crop=str(crop_path)) for part in shlex.split(external_command)]
    if "{crop}" not in external_command:
        command.append(str(crop_path))
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"source": "external", "fen": "", "confidence": 0.0, "warnings": [f"external_failed:{exc}"], "valid": False}
    output = "\n".join([completed.stdout or "", completed.stderr or ""]).strip()
    fen = _extract_fen_from_output(output)
    valid, validation_warnings = validate_fen(fen) if fen else (False, ["fen_missing"])
    warnings = list(validation_warnings)
    if completed.returncode != 0:
        warnings.append(f"external_exit_{completed.returncode}")
    return {
        "source": "external",
        "fen": fen,
        "confidence": 1.0 if fen and valid else 0.0,
        "warnings": sorted(set(warnings)),
        "valid": bool(valid),
        "raw_output": output[:1000],
    }


def _missing_external_result() -> dict[str, Any]:
    return {
        "source": "external",
        "fen": "",
        "confidence": 0.0,
        "warnings": ["external_command_not_configured"],
        "valid": False,
    }


def _extract_fen_from_output(output: str) -> str:
    for line in str(output or "").splitlines():
        candidate = line.strip().strip('"')
        if len(candidate.split()) == 6:
            return candidate
        if "/" in candidate:
            parts = candidate.split()
            for index in range(0, max(0, len(parts) - 5)):
                fen = " ".join(parts[index : index + 6])
                valid, _warnings = validate_fen(fen)
                if valid:
                    return fen
    return ""


def _select_candidate(local: dict[str, Any], external: dict[str, Any]) -> dict[str, Any]:
    if local.get("fen") and local.get("fen") == external.get("fen"):
        return {"fen": local["fen"], "source": "agreement", "reason": "local_external_agree"}
    if external.get("valid") and not local.get("valid"):
        return {"fen": external["fen"], "source": "external", "reason": "external_valid_local_invalid"}
    if local.get("valid"):
        return {"fen": local["fen"], "source": "local", "reason": "local_valid"}
    if external.get("valid"):
        return {"fen": external["fen"], "source": "external", "reason": "external_valid"}
    return {"fen": "", "source": "", "reason": "no_valid_candidate"}


def _resolve_crop_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.png")))
        elif any(char in value for char in "*?[]"):
            paths.extend(Path(match) for match in sorted(glob.glob(value)))
        else:
            paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local chess FEN recognition with an optional external recognizer.")
    parser.add_argument("crops", nargs="+", help="PNG crop files, directories, or glob patterns.")
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    parser.add_argument(
        "--external-command",
        default="",
        help='Optional command for linrock/chessboard-recognizer, e.g. "python C:/tools/chessboard-recognizer/recognize.py {crop}".',
    )
    parser.add_argument("--output", default="reports/chess_fen/external_recognizer_eval.json")
    args = parser.parse_args()
    summary = evaluate_external_chessboard_recognizer(
        _resolve_crop_paths(args.crops),
        template_dir=args.template_dir,
        external_command=args.external_command,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
