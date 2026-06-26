from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_position_recognizer import validate_fen  # noqa: E402
from scripts.evaluate_chess_fen_recognizer import (  # noqa: E402
    DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    evaluate_chess_fen_recognizer,
)

SCHEMA = "kindlemaster.chess_fen.alternative_benchmark.v1"

TARGET_BUCKETS = {
    "simple_diagrams": 100,
    "medium_diagrams": 100,
    "hard_scans": 100,
    "false_positives": 50,
    "cropped_boards": 50,
}


def evaluate_chess_fen_alternative_benchmarks(
    *,
    labels_dir: str | Path = "reference_inputs/chess_fen/labels",
    template_dir: str | Path = "reference_inputs/chess_fen/templates/fundamenty_merida_like",
    out_dir: str | Path = "output/chess_fen/alternative_benchmark",
    report_path: str | Path = "reports/chess_fen/alternative_benchmark.json",
    baseline_report: str | Path = "reports/corpus/fen_corpus_90.json",
    max_cases: int = 80,
) -> dict[str, Any]:
    started = time.perf_counter()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = Path(report_path)
    source_records = _collect_records(Path(labels_dir))
    records = source_records[: max(0, int(max_cases or 0))] if int(max_cases or 0) > 0 else source_records
    benchmark_labels = out / "alternative_benchmark_labels.jsonl"
    _write_jsonl(benchmark_labels, records)

    manifest = _benchmark_manifest(source_records)
    strategies = _run_strategies(
        benchmark_labels=benchmark_labels,
        template_dir=Path(template_dir),
        out_dir=out,
        runnable=bool(records),
    )
    status = "completed_with_gaps" if records else "insufficient_inputs"
    if records and all(bucket["missing_count"] == 0 for bucket in manifest["buckets"].values()):
        status = "completed"

    payload = {
        "schema": SCHEMA,
        "status": status,
        "policy": {
            "report_only": True,
            "runtime_strict_acceptance_changed": False,
            "accepted_fen_changed": 0,
            "external_paid_vision_api_used": False,
        },
        "inputs": {
            "labels_dir": str(labels_dir),
            "template_dir": str(template_dir),
            "source_record_count": len(source_records),
            "benchmark_case_count": len(records),
            "max_cases": int(max_cases or 0),
            "baseline_report": str(baseline_report),
        },
        "benchmark_manifest": manifest,
        "baseline": _load_baseline_metrics(Path(baseline_report)),
        "strategies": strategies,
        "comparison": _compare_strategies(strategies),
        "metrics": _aggregate_metrics(strategies, elapsed_seconds=time.perf_counter() - started),
        "artifacts": {
            "out_dir": str(out),
            "benchmark_labels": str(benchmark_labels),
            "report_path": str(report),
        },
        "next_actions": _next_actions(manifest, strategies),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _collect_records(labels_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(labels_dir.glob("*.jsonl")) if labels_dir.exists() else []:
        for index, row in enumerate(_read_jsonl(path)):
            fen = str(row.get("fen") or "").strip()
            valid, warnings = validate_fen(fen)
            crop_path = _resolve_crop_path(row)
            record_id = str(row.get("id") or row.get("diagram_id") or f"{path.stem}_{index}")
            if record_id in seen or not valid or warnings or not crop_path.is_file():
                continue
            record = {
                "id": record_id,
                "diagram_id": record_id,
                "fen": fen,
                "crop_path": str(crop_path),
                "page": _safe_int(row.get("page")),
                "source_labels": str(path),
                "notes": str(row.get("notes") or ""),
                "bucket_tags": _bucket_tags(row, fen),
            }
            records.append(record)
            seen.add(record_id)
    return records


def _benchmark_manifest(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for bucket, target in TARGET_BUCKETS.items():
        rows = [row for row in records if bucket in set(row.get("bucket_tags") or [])]
        buckets[bucket] = {
            "target_count": target,
            "available_count": len(rows),
            "missing_count": max(0, target - len(rows)),
            "sample_ids": [str(row.get("id") or "") for row in rows[:10]],
        }
    return {
        "schema": "kindlemaster.chess_fen.alternative_benchmark_manifest.v1",
        "total_available_records": len(records),
        "buckets": buckets,
    }


def _run_strategies(
    *,
    benchmark_labels: Path,
    template_dir: Path,
    out_dir: Path,
    runnable: bool,
) -> list[dict[str, Any]]:
    strategy_configs = [
        {
            "id": "template_current_threshold",
            "description": "Current deterministic template recognizer threshold.",
            "min_confidence": DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
        },
        {
            "id": "template_low_confidence_review_only",
            "description": "Lower confidence spike for evidence only; not a runtime threshold change.",
            "min_confidence": 0.0,
        },
    ]
    strategies: list[dict[str, Any]] = []
    for config in strategy_configs:
        started = time.perf_counter()
        output_path = out_dir / f"{config['id']}.json"
        if not runnable:
            raw = {"status": "insufficient_inputs", "case_count": 0, "cases": []}
        else:
            raw = evaluate_chess_fen_recognizer(
                benchmark_labels,
                template_dir=template_dir,
                min_confidence=float(config["min_confidence"]),
                min_exact_accuracy=1.0,
                output_path=output_path,
            )
        elapsed = time.perf_counter() - started
        metrics = _strategy_metrics(raw, elapsed_seconds=elapsed)
        strategies.append(
            {
                "id": config["id"],
                "description": config["description"],
                "status": raw.get("status", "failed"),
                "min_confidence": float(config["min_confidence"]),
                "metrics": metrics,
                "report_path": str(output_path) if runnable else "",
                "accepted_fen_changed": 0,
                "runtime_strict_acceptance_changed": False,
            }
        )
    return strategies


def _strategy_metrics(raw: Mapping[str, Any], *, elapsed_seconds: float) -> dict[str, Any]:
    cases = list(raw.get("cases") or [])
    case_count = int(raw.get("case_count") or len(cases) or 0)
    exact_placement_count = sum(
        1
        for case in cases
        if str(case.get("expected_placement") or "") and case.get("expected_placement") == case.get("actual_placement")
    )
    review_count = sum(1 for case in cases if case.get("requires_review", True))
    false_positive_count = int(raw.get("false_positive_count") or 0)
    return {
        "case_count": case_count,
        "exact_placement_count": exact_placement_count,
        "exact_placement_rate": round(exact_placement_count / max(1, case_count), 4),
        "exact_full_fen_count": int(raw.get("exact_fen_count") or 0),
        "exact_full_fen_rate": round(float(raw.get("exact_fen_accuracy") or 0.0), 4),
        "false_positive_count": false_positive_count,
        "false_positive_rate": round(false_positive_count / max(1, case_count), 4),
        "review_count": review_count,
        "review_rate": round(review_count / max(1, case_count), 4),
        "runtime_cost_seconds": round(elapsed_seconds, 4),
        "seconds_per_case": round(elapsed_seconds / max(1, case_count), 4),
    }


def _aggregate_metrics(strategies: list[Mapping[str, Any]], *, elapsed_seconds: float) -> dict[str, Any]:
    if not strategies:
        return {
            "exact_placement_rate": 0.0,
            "exact_full_fen_rate": 0.0,
            "false_positive_rate": 0.0,
            "review_rate": 0.0,
            "runtime_cost_seconds": round(elapsed_seconds, 4),
        }
    best = max(
        strategies,
        key=lambda item: (
            float((item.get("metrics") or {}).get("exact_full_fen_rate") or 0.0),
            float((item.get("metrics") or {}).get("exact_placement_rate") or 0.0),
        ),
    )
    metrics = dict(best.get("metrics") or {})
    metrics["best_strategy_id"] = best.get("id")
    metrics["runtime_cost_seconds"] = round(elapsed_seconds, 4)
    return metrics


def _compare_strategies(strategies: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not strategies:
        return {"status": "unavailable", "winner": ""}
    ranked = sorted(
        strategies,
        key=lambda item: (
            float((item.get("metrics") or {}).get("exact_full_fen_rate") or 0.0),
            -float((item.get("metrics") or {}).get("false_positive_rate") or 0.0),
            float((item.get("metrics") or {}).get("exact_placement_rate") or 0.0),
        ),
        reverse=True,
    )
    return {
        "status": "report_only",
        "winner": ranked[0].get("id", ""),
        "ranked_strategy_ids": [str(item.get("id") or "") for item in ranked],
        "promotion_allowed": False,
    }


def _bucket_tags(row: Mapping[str, Any], fen: str) -> list[str]:
    text = " ".join(
        [
            str(row.get("id") or ""),
            str(row.get("filename") or ""),
            str(row.get("crop_path") or row.get("source_crop_path") or ""),
            str(row.get("notes") or ""),
        ]
    ).lower()
    piece_count = _piece_count(fen)
    tags: list[str] = []
    if piece_count <= 6:
        tags.append("simple_diagrams")
    elif piece_count <= 18:
        tags.append("medium_diagrams")
    else:
        tags.append("hard_scans")
    if "scan" in text or "hard" in text or "ocr" in text:
        tags.append("hard_scans")
    if "false_positive" in text or "false positive" in text or "rejected" in text:
        tags.append("false_positives")
    if "crop" in text or "cropped" in text or "border" in text or "recovered" in text:
        tags.append("cropped_boards")
    return _dedupe(tags)


def _piece_count(fen: str) -> int:
    placement = str(fen or "").split()[0]
    return sum(1 for char in placement if char.isalpha())


def _resolve_crop_path(row: Mapping[str, Any]) -> Path:
    for key in ("crop_path", "source_crop_path"):
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return path
        repo_path = REPO_ROOT / path
        if repo_path.is_file():
            return repo_path
    return Path("")


def _load_baseline_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "unavailable", "path": str(path)}
    value = _load_json(path)
    return {
        "status": str(value.get("status") or value.get("overall_status") or "available"),
        "path": str(path),
        "overall_exact_fen_accuracy": value.get("overall_exact_fen_accuracy"),
        "total_false_positive_count": value.get("total_false_positive_count"),
        "evaluated_case_count": value.get("evaluated_case_count"),
    }


def _next_actions(manifest: Mapping[str, Any], strategies: list[Mapping[str, Any]]) -> list[str]:
    actions: list[str] = []
    buckets = manifest.get("buckets") or {}
    for bucket, summary in buckets.items():
        missing = int((summary or {}).get("missing_count") or 0)
        if missing:
            actions.append(f"{bucket}: add {missing} benchmark input(s) to reach target.")
    if any(float((item.get("metrics") or {}).get("false_positive_rate") or 0.0) > 0 for item in strategies):
        actions.append("keep all alternative recognizer outputs report-only until false positives reach zero.")
    actions.append("do not change production strict FEN acceptance from this benchmark-only issue.")
    return _dedupe(actions)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run report-only alternative chess FEN recognizer benchmarks.")
    parser.add_argument("--labels-dir", default="reference_inputs/chess_fen/labels")
    parser.add_argument("--template-dir", default="reference_inputs/chess_fen/templates/fundamenty_merida_like")
    parser.add_argument("--out-dir", default="output/chess_fen/alternative_benchmark")
    parser.add_argument("--report", default="reports/chess_fen/alternative_benchmark.json")
    parser.add_argument("--baseline-report", default="reports/corpus/fen_corpus_90.json")
    parser.add_argument("--max-cases", type=int, default=80)
    args = parser.parse_args(argv)
    payload = evaluate_chess_fen_alternative_benchmarks(
        labels_dir=args.labels_dir,
        template_dir=args.template_dir,
        out_dir=args.out_dir,
        report_path=args.report,
        baseline_report=args.baseline_report,
        max_cases=args.max_cases,
    )
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "status": payload["status"],
                "report_path": payload["artifacts"]["report_path"],
                "benchmark_case_count": payload["inputs"]["benchmark_case_count"],
                "metrics": payload["metrics"],
                "bucket_counts": {
                    bucket: {
                        "available_count": summary["available_count"],
                        "missing_count": summary["missing_count"],
                    }
                    for bucket, summary in payload["benchmark_manifest"]["buckets"].items()
                },
                "next_actions": payload["next_actions"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
