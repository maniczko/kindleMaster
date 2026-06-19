from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any


def _predict_payload(module: Any, crop_path: str, model_path: str) -> Any:
    if hasattr(module, "ChessPositionPredictor") and model_path:
        predictor = module.ChessPositionPredictor(model_path)
        try:
            return predictor.predict_chessboard(crop_path, return_tiles=True)
        except TypeError:
            return predictor.predict_chessboard(crop_path)
    if hasattr(module, "predict_fen"):
        return module.predict_fen(crop_path)
    if hasattr(module, "ChessPositionPredictor"):
        predictor = module.ChessPositionPredictor()
        try:
            return predictor.predict_chessboard(crop_path, return_tiles=True)
        except TypeError:
            return predictor.predict_chessboard(crop_path)
    raise RuntimeError("No supported chessimg2pos API found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("crop_path", nargs="?")
    parser.add_argument("--model-path", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    module = importlib.import_module("chessimg2pos")
    provider_version = str(getattr(module, "__version__", "") or "unknown")
    if args.version:
        print(provider_version)
        return 0

    if not args.crop_path:
        raise SystemExit("crop_path is required")
    crop_path = str(Path(args.crop_path))
    payload = _predict_payload(module, crop_path, str(args.model_path or "").strip())
    result = {
        "payload": payload,
        "provider_version": provider_version,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
