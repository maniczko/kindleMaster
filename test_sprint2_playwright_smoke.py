from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sprint2_playwright_smoke import (
    BLOCKED,
    FAILED,
    PASSED,
    UNAVAILABLE,
    classify_smoke_contract,
    unavailable_result,
)


REPO_ROOT = Path(__file__).resolve().parent
SAMPLE_PDF = REPO_ROOT / "reference_inputs" / "pdf" / "ocr_probe.pdf"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_until_server_ready(base_url: str, process: subprocess.Popen[bytes], timeout_seconds: float = 90.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"KindleMaster runtime stopped before startup (code={process.returncode}).")
        try:
            with urllib.request.urlopen(f"{base_url}/", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"KindleMaster runtime did not start at {base_url}: {last_error}")


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _quality_state(*, job_id: str, release_blocked: bool = False) -> dict[str, Any]:
    blockers = (
        [
            {
                "severity": "blocker",
                "code": "metadata_release_blocker",
                "message": "Required release metadata is missing.",
                "source": "metadata_health",
            }
        ]
        if release_blocked
        else []
    )
    return {
        "job_id": job_id,
        "source_type": "pdf",
        "status": "ready",
        "phase": "completed",
        "quality_available": True,
        "download_available": True,
        "download_ready": True,
        "download_url": f"/convert/download/{job_id}",
        "reading_verdict": "ready_with_review" if release_blocked else "ready",
        "release_verdict": "release_blocked" if release_blocked else "release_ready",
        "release_blocked": release_blocked,
        "quality_blockers": blockers,
        "summary": {
            "profile": "book_reflow",
            "strategy": "text_reflowable",
            "layout": "reflowable",
            "sections": 4,
            "assets": 1,
            "output_size_bytes": 4096,
        },
        "validation": {"status": "passed", "tool": "epubcheck"},
        "audit": {
            "warning_count": 1,
            "warnings": [{"code": "qa_probe_warning", "message": "Sprint 2 smoke audit warning."}],
            "high_risk_pages": 0,
            "high_risk_sections": 0,
        },
        "issue_groups": {"blockers": blockers, "warnings": [], "review": []},
        "metadata_health": {"status": "passed", "message": "Metadata complete."},
        "link_health": {"status": "passed", "message": "No broken links reported."},
        "visible_junk": {"status": "passed", "message": "No visible junk reported."},
        "quality_completeness": {
            "status": "complete",
            "score": 100,
            "expected_sections": 8,
            "reported_sections": 8,
            "missing_count": 0,
            "not_reported_count": 0,
            "sections": [],
        },
    }


def _ready_payload(*, job_id: str, release_blocked: bool = False) -> dict[str, Any]:
    quality_state = _quality_state(job_id=job_id, release_blocked=release_blocked)
    return {
        "success": True,
        "job_id": job_id,
        "status": "ready",
        "message": "EPUB ready for download.",
        "source_type": "pdf",
        "download_url": f"/convert/download/{job_id}",
        "quality_state_url": f"/convert/quality/{job_id}",
        "quality_state": quality_state,
        "conversion": {
            "profile": "book_reflow",
            "validation": "passed",
            "output_size_bytes": 4096,
            "heading_repair": {"status": "skipped"},
        },
        "output_size_bytes": 4096,
        "poll_after_ms": 0,
        "elapsed_seconds": 3,
    }


class Sprint2PlaywrightSmokeContractTests(unittest.TestCase):
    def test_ready_payload_maps_to_passed_contract(self) -> None:
        result = classify_smoke_contract(
            status_payload=_ready_payload(job_id="job-pass"),
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, PASSED)
        self.assertEqual(result.reason, "runtime_roundtrip_ready")
        self.assertEqual(result.missing_evidence, ())

    def test_failed_status_maps_to_failed_contract(self) -> None:
        result = classify_smoke_contract(
            status_payload={"status": "failed", "quality_state": _quality_state(job_id="job-failed")},
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.reason, "conversion_failed")

    def test_timeout_status_maps_to_failed_contract(self) -> None:
        result = classify_smoke_contract(
            status_payload={"status": "timed_out", "quality_state": _quality_state(job_id="job-timeout")},
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.reason, "conversion_timed_out")

    def test_missing_output_maps_to_specific_failure(self) -> None:
        result = classify_smoke_contract(
            status_payload={
                "status": "failed",
                "error_code": "missing_output",
                "quality_state": _quality_state(job_id="job-missing-output"),
            },
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.reason, "missing_output")

    def test_ocr_failure_maps_to_specific_failure(self) -> None:
        result = classify_smoke_contract(
            status_payload={
                "status": "failed",
                "error_code": "ocr_failed",
                "quality_state": _quality_state(job_id="job-ocr-failed"),
            },
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.reason, "ocr_failed")

    def test_retryable_failure_requires_retry_evidence(self) -> None:
        result = classify_smoke_contract(
            status_payload={
                "status": "failed",
                "retryable": True,
                "quality_state": _quality_state(job_id="job-retryable"),
            },
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
            retry_attempted=False,
        )

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.reason, "retryable_failure_without_retry")

    def test_heavy_pdf_ready_payload_still_maps_to_passed_contract(self) -> None:
        payload = _ready_payload(job_id="job-heavy")
        payload["conversion"]["output_size_bytes"] = 42 * 1024 * 1024
        payload["quality_state"]["summary"]["output_size_bytes"] = 42 * 1024 * 1024

        result = classify_smoke_contract(
            status_payload=payload,
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, PASSED)
        self.assertEqual(result.reason, "runtime_roundtrip_ready")

    def test_release_blocked_payload_maps_to_blocked_contract_with_download_evidence(self) -> None:
        result = classify_smoke_contract(
            status_payload=_ready_payload(job_id="job-blocked", release_blocked=True),
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=True,
            download_attempted=True,
        )

        self.assertEqual(result.status, BLOCKED)
        self.assertEqual(result.reason, "release_blocked")

    def test_missing_browser_tooling_maps_to_unavailable_contract(self) -> None:
        result = unavailable_result(
            missing_requirements=["python-playwright", "chromium"],
            notes=["Install with `python -m playwright install chromium`."],
        )

        self.assertEqual(result.status, UNAVAILABLE)
        self.assertEqual(result.missing_evidence, ("python-playwright", "chromium"))
        self.assertIn("runtime_tooling_unavailable", result.reason)
        self.assertEqual(result.as_dict()["status"], UNAVAILABLE)

    def test_missing_evidence_is_failed_not_silently_passed(self) -> None:
        result = classify_smoke_contract(
            status_payload=_ready_payload(job_id="job-no-download"),
            upload_selected=True,
            convert_start_accepted=True,
            quality_rendered=False,
            download_attempted=False,
        )

        self.assertEqual(result.status, FAILED)
        self.assertEqual(result.reason, "required_evidence_missing")
        self.assertEqual(result.missing_evidence, ("quality_audit_available", "download_attempted"))


class Sprint2PlaywrightRuntimeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            raise unittest.SkipTest(f"Python Playwright is unavailable: {exc}") from exc

        if not SAMPLE_PDF.exists():
            raise unittest.SkipTest(f"Missing PDF fixture: {SAMPLE_PDF}")

        cls._sync_playwright = sync_playwright
        cls.port = _find_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        cls.server_process = subprocess.Popen(
            [sys.executable, "kindlemaster.py", "serve", "--runtime", "waitress", "--port", str(cls.port)],
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_until_server_ready(cls.base_url, cls.server_process)
        cls.playwright = cls._sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            cls._stop_server()
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium Playwright is unavailable: {exc}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        browser = getattr(cls, "browser", None)
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        playwright = getattr(cls, "playwright", None)
        if playwright is not None:
            with contextlib.suppress(Exception):
                playwright.stop()
        cls._stop_server()

    @classmethod
    def _stop_server(cls) -> None:
        process = getattr(cls, "server_process", None)
        if process is None:
            return
        if process.poll() is None:
            with contextlib.suppress(Exception):
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    process.kill()
                with contextlib.suppress(Exception):
                    process.wait(timeout=5)

    def setUp(self) -> None:
        self.context = self.browser.new_context(accept_downloads=True)
        self.context.add_init_script("window.localStorage.setItem('kindlemaster.start.continue-local', '1');")
        self.page = self.context.new_page()

    def tearDown(self) -> None:
        with contextlib.suppress(Exception):
            self.context.close()

    def test_upload_status_quality_audit_and_download_flow(self) -> None:
        job_id = "job-sprint2-smoke"
        status_payload = _ready_payload(job_id=job_id)
        evidence = {
            "upload_selected": False,
            "convert_start_accepted": False,
            "quality_rendered": False,
            "download_attempted": False,
        }
        console_errors: list[str] = []
        self.page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

        self.page.route(
            "**/convert/start",
            lambda route: (
                evidence.__setitem__("convert_start_accepted", True),
                route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=_json_body(
                        {
                            "success": True,
                            "job_id": job_id,
                            "status": "queued",
                            "source_type": "pdf",
                            "message": "Conversion started.",
                            "poll_after_ms": 100,
                        }
                    ),
                ),
            ),
        )
        self.page.route(
            f"**/convert/status/{job_id}",
            lambda route: route.fulfill(status=200, content_type="application/json", body=_json_body(status_payload)),
        )
        self.page.route(
            f"**/convert/quality/{job_id}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body({"success": True, "job_id": job_id, "quality_state": status_payload["quality_state"]}),
            ),
        )
        self.page.route(
            f"**/convert/download/{job_id}",
            lambda route: (
                evidence.__setitem__("download_attempted", True),
                route.fulfill(
                    status=200,
                    headers={
                        "content-type": "application/epub+zip",
                        "content-disposition": 'attachment; filename="sprint2-smoke.epub"',
                    },
                    body=b"epub-smoke",
                ),
            ),
        )

        self.page.goto(f"{self.base_url}/")
        self.page.wait_for_function(
            """() => {
              const text = document.body ? document.body.innerText || "" : "";
              return text.includes("Nowa konwersja") || text.includes("Kontynuuj lokalnie");
            }""",
            timeout=15000,
        )
        local_button = self.page.locator('[data-testid="continue-locally-button"]')
        if local_button.count():
            local_button.click()
        self.page.locator('[data-testid="conversion-file-input"]').set_input_files(str(SAMPLE_PDF))
        self.page.wait_for_function(
            """(filename) => {
              const text = document.body ? document.body.innerText || "" : "";
              return text.includes(filename);
            }""",
            arg=SAMPLE_PDF.name,
            timeout=15000,
        )
        evidence["upload_selected"] = True

        self.page.locator('[data-testid="start-conversion-button"]').click()
        self.page.locator('[data-testid="file-details-view"]').wait_for(state="visible", timeout=30000)

        with self.page.expect_download() as download_info:
            self.page.locator(f'a[href="/convert/download/{job_id}"]').click()
        download = download_info.value
        self.assertIn("sprint2-smoke", download.suggested_filename)

        evidence["quality_rendered"] = self.page.locator('[data-testid="file-details-view"]').is_visible()
        rendered_report = self.page.locator('[data-testid="file-details-view"]').text_content() or ""
        self.assertIn("Decyzja jakości", rendered_report)
        self.assertIn("Finalny EPUB", rendered_report)

        contract = classify_smoke_contract(
            status_payload=status_payload,
            upload_selected=evidence["upload_selected"],
            convert_start_accepted=evidence["convert_start_accepted"],
            quality_rendered=evidence["quality_rendered"],
            download_attempted=evidence["download_attempted"],
            console_errors=console_errors,
        )
        self.assertEqual(contract.status, PASSED, contract.as_dict())


if __name__ == "__main__":
    unittest.main()
