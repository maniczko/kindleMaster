from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openai_chess_fen_reviewer import _http_transport, _runtime_env, openai_chess_fen_reviewer_status


def run_side_marker_ai_review_requests(
    requests_jsonl: str | Path,
    *,
    output_jsonl: str | Path,
    limit: int | None = None,
    cwd: str | Path = ROOT_DIR,
    max_workers: int = 1,
) -> dict[str, Any]:
    status = openai_chess_fen_reviewer_status(cwd=cwd)
    target = Path(output_jsonl)
    if not bool(status.get("configured")):
        summary = {
            "status": "disabled",
            "reason": "openai_chess_fen_reviewer_not_configured",
            "request_jsonl": str(requests_jsonl),
            "output_jsonl": str(target),
            "openai_status": status,
            "response_count": 0,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    requests = _read_jsonl(Path(requests_jsonl))
    if limit is not None:
        requests = requests[: max(0, int(limit))]
    headers = {
        "Authorization": f"Bearer {_api_key(cwd=cwd)}",
        "Content-Type": "application/json",
    }
    base_url = str(status.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    worker_count = max(1, int(max_workers or 1))
    if worker_count == 1:
        responses = [_run_one_request(request, base_url=base_url, headers=headers) for request in requests]
    else:
        responses_by_index: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index = {
                executor.submit(_run_one_request, request, base_url=base_url, headers=headers): index
                for index, request in enumerate(requests)
            }
            for future in as_completed(future_to_index):
                responses_by_index[future_to_index[future]] = future.result()
        responses = [responses_by_index[index] for index in range(len(requests))]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in responses), encoding="utf-8")
    summary = {
        "status": "ok",
        "request_jsonl": str(requests_jsonl),
        "output_jsonl": str(target),
        "request_count": len(requests),
        "response_count": len(responses),
        "error_count": sum(1 for row in responses if row.get("error")),
        "max_workers": worker_count,
        "openai_status": status,
        "policy": "ai_side_marker_review_only_no_human_verification",
    }
    target.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _api_key(*, cwd: str | Path = ROOT_DIR) -> str:
    return str(_runtime_env(env=None, cwd=cwd).get("OPENAI_API_KEY") or "").strip()


def _run_one_request(request: dict[str, Any], *, base_url: str, headers: dict[str, str]) -> dict[str, Any]:
    body = request.get("body")
    if not isinstance(body, dict):
        return {"custom_id": request.get("custom_id", ""), "error": "request_body_missing"}
    try:
        response_body = _http_transport(f"{base_url}/responses", headers, body, 90.0)
        return {"custom_id": request.get("custom_id", ""), "response": {"body": response_body}}
    except Exception as exc:
        return {"custom_id": request.get("custom_id", ""), "error": str(exc)}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run side-marker AI review requests through OpenAI Responses API.")
    parser.add_argument("requests_jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=1)
    args = parser.parse_args(argv)
    summary = run_side_marker_ai_review_requests(
        args.requests_jsonl,
        output_jsonl=args.output,
        limit=args.limit,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") in {"ok", "disabled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
