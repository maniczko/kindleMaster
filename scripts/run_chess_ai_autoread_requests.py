from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openai_chess_fen_reviewer import DEFAULT_OPENAI_BASE_URL, _http_transport, _runtime_env


def run_chess_ai_autoread_requests(
    requests_jsonl: str | Path,
    *,
    output_jsonl: str | Path,
    limit: int | None = None,
    cwd: str | Path = ROOT_DIR,
    max_workers: int = 1,
    timeout_seconds: float = 120.0,
    retries: int = 1,
    resume: bool = True,
) -> dict[str, Any]:
    target = Path(output_jsonl)
    status = _autoread_openai_status(cwd=cwd)
    if not bool(status.get("configured")):
        target.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "status": "disabled",
            "reason": "openai_api_key_missing",
            "request_jsonl": str(requests_jsonl),
            "output_jsonl": str(target),
            "response_count": 0,
            "openai_status": status,
            "policy": "ai_autoread_experimental_no_runtime_promotion",
        }
        target.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    requests = _read_jsonl(Path(requests_jsonl))
    if limit is not None:
        requests = requests[: max(0, int(limit))]
    existing = _existing_responses(target) if resume else {}
    pending = [request for request in requests if str(request.get("custom_id") or "") not in existing]
    headers = {
        "Authorization": f"Bearer {_api_key(cwd=cwd)}",
        "Content-Type": "application/json",
    }
    base_url = str(status.get("base_url") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    worker_count = max(1, int(max_workers or 1))
    target.parent.mkdir(parents=True, exist_ok=True)
    new_responses: list[dict[str, Any]] = []
    if worker_count == 1:
        for request in pending:
            response = _run_one_request(
                request,
                base_url=base_url,
                headers=headers,
                timeout_seconds=timeout_seconds,
                retries=retries,
            )
            _append_jsonl(target, response)
            new_responses.append(response)
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _run_one_request,
                    request,
                    base_url=base_url,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                ): request
                for request in pending
            }
            for future in as_completed(futures):
                response = future.result()
                _append_jsonl(target, response)
                new_responses.append(response)

    merged = _existing_responses(target) if resume else {**existing}
    for response in new_responses:
        merged[str(response.get("custom_id") or "")] = response
    ordered = [merged[str(request.get("custom_id") or "")] for request in requests if str(request.get("custom_id") or "") in merged]
    _write_jsonl(target, ordered)
    summary = {
        "status": "ok",
        "request_jsonl": str(requests_jsonl),
        "output_jsonl": str(target),
        "request_count": len(requests),
        "skipped_existing_count": len(existing),
        "pending_request_count": len(pending),
        "response_count": len(ordered),
        "new_response_count": len(new_responses),
        "error_count": sum(1 for row in ordered if row.get("error")),
        "max_workers": worker_count,
        "timeout_seconds": float(timeout_seconds),
        "retries": int(retries),
        "resume": bool(resume),
        "openai_status": status,
        "policy": "ai_autoread_experimental_no_runtime_promotion",
    }
    target.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _autoread_openai_status(*, cwd: str | Path = ROOT_DIR) -> dict[str, Any]:
    env = _runtime_env(env=None, cwd=cwd)
    api_key_present = bool(str(env.get("OPENAI_API_KEY") or "").strip())
    return {
        "enabled": api_key_present,
        "configured": api_key_present,
        "api_key_present": api_key_present,
        "provider": "openai-ai-autoread" if api_key_present else "none",
        "base_url": str(env.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL),
        "mode": "ai_autoread_experimental",
        "mutates_fen": False,
        "mutates_pgn": False,
        "release_safe": False,
    }


def _api_key(*, cwd: str | Path = ROOT_DIR) -> str:
    return str(_runtime_env(env=None, cwd=cwd).get("OPENAI_API_KEY") or "").strip()


def _run_one_request(
    request: dict[str, Any],
    *,
    base_url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    retries: int,
) -> dict[str, Any]:
    body = request.get("body")
    custom_id = str(request.get("custom_id") or "")
    if not isinstance(body, dict):
        return {"custom_id": custom_id, "error": "request_body_missing"}
    last_error = ""
    for attempt in range(max(1, int(retries) + 1)):
        try:
            response_body = _http_transport(f"{base_url}/responses", headers, body, float(timeout_seconds))
            return {"custom_id": custom_id, "response": {"body": response_body}}
        except Exception as exc:
            last_error = str(exc)
            if attempt < int(retries):
                time.sleep(min(2.0 * (attempt + 1), 8.0))
    return {"custom_id": custom_id, "error": last_error or "openai_request_failed"}


def _existing_responses(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {str(row.get("custom_id") or ""): row for row in _read_jsonl(path) if str(row.get("custom_id") or "")}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run experimental AI autoread OpenAI Responses API requests.")
    parser.add_argument("requests_jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(argv)
    summary = run_chess_ai_autoread_requests(
        args.requests_jsonl,
        output_jsonl=args.output,
        limit=args.limit,
        max_workers=args.max_workers,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        resume=not args.no_resume,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") in {"ok", "disabled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
