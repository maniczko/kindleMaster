from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import io
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile


TERMINAL_STATUSES = {"ready", "failed", "timed_out", "cancelled", "dead_letter"}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


class AcceptanceFailure(RuntimeError):
    pass


class HttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str = "",
        guest_capability: str = "",
        timeout_seconds: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.bearer_token = bearer_token
        self.guest_capability = guest_capability
        self.timeout_seconds = max(5, int(timeout_seconds))

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        merged = {
            "Accept": "application/json",
            "User-Agent": "KindleMaster-P0-Acceptance/1.0",
        }
        if self.bearer_token:
            merged["Authorization"] = f"Bearer {self.bearer_token}"
        if self.guest_capability:
            merged["X-KindleMaster-Guest-Capability"] = self.guest_capability
        merged.update(headers or {})
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=body,
            method=method,
            headers=merged,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return (
                    int(response.status),
                    {key.lower(): value for key, value in response.headers.items()},
                    response.read(),
                )
        except HTTPError as error:
            return (
                int(error.code),
                {key.lower(): value for key, value in error.headers.items()},
                error.read(),
            )
        except URLError as error:
            raise AcceptanceFailure(f"Request failed for {path}: {error.reason}") from error

    def json_request(self, path: str, **kwargs: Any) -> tuple[int, dict[str, str], dict[str, Any]]:
        status, headers, body = self.request(path, **kwargs)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AcceptanceFailure(
                f"Expected JSON from {path}, received HTTP {status} and {len(body)} bytes."
            ) from error
        if not isinstance(payload, dict):
            raise AcceptanceFailure(f"Expected JSON object from {path}.")
        return status, headers, payload

    def upload(
        self,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        idempotency_key: str = "",
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        boundary = f"----kindlemaster-{uuid4().hex}"
        body = _multipart_body(
            boundary=boundary,
            fields={
                "profile": "auto-premium",
                "language": "pl",
                "heading_repair": "1",
            },
            filename=filename,
            content_type=content_type,
            data=data,
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self.json_request(
            "/convert/start",
            method="POST",
            body=body,
            headers=headers,
        )


def _multipart_body(
    *,
    boundary: str,
    fields: dict[str, str],
    filename: str,
    content_type: str,
    data: bytes,
) -> bytes:
    output = io.BytesIO()
    for name, value in fields.items():
        output.write(f"--{boundary}\r\n".encode())
        output.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        output.write(str(value).encode("utf-8"))
        output.write(b"\r\n")
    output.write(f"--{boundary}\r\n".encode())
    output.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    output.write(f"Content-Type: {content_type}\r\n\r\n".encode())
    output.write(data)
    output.write(b"\r\n")
    output.write(f"--{boundary}--\r\n".encode())
    return output.getvalue()


def _minimal_pdf() -> bytes:
    try:
        import fitz
    except ImportError as error:
        raise AcceptanceFailure("PyMuPDF is required to generate the legal synthetic PDF.") from error
    document = fitz.open()
    try:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 96), "KindleMaster P0 staging acceptance", fontsize=14)
        page.insert_text((72, 126), datetime.now(UTC).isoformat(), fontsize=9)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


def _traversal_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
        archive.writestr("../escape.txt", "blocked")
    return output.getvalue()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def _record(results: list[CheckResult], name: str, function) -> Any:
    try:
        value, detail, evidence = function()
        results.append(CheckResult(name=name, status="passed", detail=detail, evidence=evidence))
        return value
    except Exception as error:
        results.append(CheckResult(name=name, status="failed", detail=str(error)))
        raise


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = HttpClient(
        base_url=args.base_url,
        bearer_token=args.bearer_token,
        guest_capability=args.guest_capability,
        timeout_seconds=args.request_timeout_seconds,
    )
    results: list[CheckResult] = []

    def health_check():
        status, _, payload = client.json_request("/auth/config")
        _require(status < 500, f"Health/config endpoint returned HTTP {status}.")
        return payload, f"HTTP {status}", {"status_code": status}

    _record(results, "api_health", health_check)

    def malformed_pdf_check():
        status, _, payload = client.upload(
            filename="invalid.pdf",
            content_type="application/pdf",
            data=b"not-a-pdf",
        )
        _require(status in {400, 413, 415, 422}, f"Malformed PDF returned HTTP {status}.")
        _require(
            payload.get("error_code") in {"upload_magic_mismatch", "malformed_pdf"},
            f"Unexpected malformed PDF code: {payload.get('error_code')!r}",
        )
        return payload, str(payload.get("error_code")), {"status_code": status}

    _record(results, "reject_malformed_pdf", malformed_pdf_check)

    def traversal_docx_check():
        status, _, payload = client.upload(
            filename="traversal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=_traversal_docx(),
        )
        _require(status in {400, 413, 415, 422}, f"Traversal DOCX returned HTTP {status}.")
        _require(
            payload.get("error_code") == "docx_path_traversal",
            f"Unexpected traversal code: {payload.get('error_code')!r}",
        )
        return payload, "docx_path_traversal", {"status_code": status}

    _record(results, "reject_docx_traversal", traversal_docx_check)

    idempotency_key = f"p0-acceptance-{uuid4().hex}"
    pdf = _minimal_pdf()

    def start_check():
        status, headers, payload = client.upload(
            filename="p0-acceptance.pdf",
            content_type="application/pdf",
            data=pdf,
            idempotency_key=idempotency_key,
        )
        _require(status == 202, f"Valid start returned HTTP {status}: {payload}")
        job_id = str(payload.get("job_id") or "")
        _require(bool(job_id), "Valid start did not return job_id.")
        return job_id, "job accepted", {
            "status_code": status,
            "job_id": job_id,
            "rate_limit_limit": headers.get("x-ratelimit-limit", ""),
        }

    job_id = _record(results, "valid_conversion_start", start_check)

    def idempotency_check():
        status, _, payload = client.upload(
            filename="p0-acceptance.pdf",
            content_type="application/pdf",
            data=pdf,
            idempotency_key=idempotency_key,
        )
        _require(status == 202, f"Idempotent replay returned HTTP {status}: {payload}")
        replay_job_id = str(payload.get("job_id") or "")
        _require(replay_job_id == job_id, f"Replay created {replay_job_id}, expected {job_id}.")
        return payload, "same canonical job", {"status_code": status, "job_id": replay_job_id}

    _record(results, "idempotent_start_replay", idempotency_check)

    def completion_check():
        deadline = time.monotonic() + max(30, args.conversion_timeout_seconds)
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status, _, payload = client.json_request(f"/convert/status/{job_id}")
            _require(status == 200, f"Status endpoint returned HTTP {status}: {payload}")
            last_payload = payload
            job_status = str(payload.get("status") or "").lower()
            if job_status in TERMINAL_STATUSES:
                _require(job_status == "ready", f"Conversion ended with {job_status}: {payload}")
                return payload, "conversion ready", {
                    "job_id": job_id,
                    "status": job_status,
                    "download_available": bool(payload.get("download_available")),
                }
            poll_ms = int(payload.get("poll_after_ms") or 2000)
            time.sleep(max(0.5, min(5.0, poll_ms / 1000.0)))
        raise AcceptanceFailure(
            f"Conversion did not finish within {args.conversion_timeout_seconds}s; last={last_payload}"
        )

    _record(results, "durable_conversion_completion", completion_check)

    if args.mode == "bounded-abuse":
        def rate_limit_check():
            evidence: dict[str, Any] = {"attempts": 0}
            for attempt in range(1, 25):
                status, headers, payload = client.upload(
                    filename="rate-limit.pdf",
                    content_type="application/pdf",
                    data=b"not-a-pdf",
                )
                evidence["attempts"] = attempt
                if status == 429:
                    _require(payload.get("error_code") == "rate_limit_exceeded", str(payload))
                    evidence.update(
                        {
                            "retry_after": headers.get("retry-after", ""),
                            "limit": headers.get("x-ratelimit-limit", ""),
                            "remaining": headers.get("x-ratelimit-remaining", ""),
                        }
                    )
                    return payload, "rate limit enforced", evidence
                _require(status in {400, 413, 415, 422}, f"Unexpected burst response {status}: {payload}")
            raise AcceptanceFailure("No HTTP 429 observed within the bounded 24-request burst.")

        _record(results, "bounded_start_rate_limit", rate_limit_check)

    passed = all(item.status == "passed" for item in results)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "base_url": args.base_url.rstrip("/"),
        "mode": args.mode,
        "status": "passed" if passed else "failed",
        "checks": [asdict(item) for item in results],
    }
    return report


def write_report(report: dict[str, Any], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "production-p0-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# KindleMaster production P0 acceptance",
        "",
        f"- Status: `{report['status']}`",
        f"- Mode: `{report['mode']}`",
        f"- Target: `{report['base_url']}`",
        f"- Generated: `{report['generated_at']}`",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"]:
        detail = str(check.get("detail") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check['name']} | {check['status']} | {detail} |")
    lines.append("")
    (report_dir / "production-p0-acceptance.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded KindleMaster P0 staging acceptance checks.")
    parser.add_argument("--base-url", default=os.environ.get("KINDLEMASTER_STAGING_BASE_URL", ""))
    parser.add_argument("--bearer-token", default=os.environ.get("KINDLEMASTER_STAGING_BEARER_TOKEN", ""))
    parser.add_argument(
        "--guest-capability",
        default=os.environ.get("KINDLEMASTER_STAGING_GUEST_CAPABILITY", ""),
    )
    parser.add_argument("--mode", choices=("safe", "bounded-abuse"), default="safe")
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--conversion-timeout-seconds", type=int, default=1200)
    parser.add_argument("--report-dir", type=Path, default=Path("reports/production-p0-acceptance"))
    args = parser.parse_args()
    if not str(args.base_url or "").strip():
        parser.error("--base-url or KINDLEMASTER_STAGING_BASE_URL is required")
    return args


def main() -> int:
    args = parse_args()
    report: dict[str, Any]
    try:
        report = run(args)
    except Exception as error:
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "base_url": str(args.base_url).rstrip("/"),
            "mode": args.mode,
            "status": "failed",
            "checks": [
                {
                    "name": "acceptance_execution",
                    "status": "failed",
                    "detail": str(error),
                    "evidence": {"exception_class": error.__class__.__name__},
                }
            ],
        }
        write_report(report, args.report_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    write_report(report, args.report_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
