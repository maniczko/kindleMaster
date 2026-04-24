from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
SAMPLE_PDF = REPO_ROOT / "reference_inputs" / "pdf" / "ocr_probe.pdf"
PRIVACY_NOISE_MARKERS = (
    "tracking prevention blocked access to storage",
    "blocked access to storage",
    "permission denied to access property \"localstorage\"",
    "permission denied to access property \"sessionstorage\"",
    "storage access is denied",
    "cookies are disabled",
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, process: subprocess.Popen[bytes], timeout_seconds: float = 45.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Waitress runtime zakończyl się przed startem (code={process.returncode}).")
        try:
            with urllib.request.urlopen(f"{base_url}/", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Waitress runtime nie wystartowal pod {base_url}")


def _is_privacy_noise_message(message: str) -> bool:
    normalized = " ".join(str(message or "").strip().lower().split())
    return any(marker in normalized for marker in PRIVACY_NOISE_MARKERS)


class BrowserPrivacyDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f"Python Playwright nie jest dostepny: {exc}") from exc

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
        _wait_for_server(cls.base_url, cls.server_process)
        cls.playwright = cls._sync_playwright().start()

    @classmethod
    def tearDownClass(cls) -> None:
        playwright = getattr(cls, "playwright", None)
        if playwright is not None:
            with contextlib.suppress(Exception):
                playwright.stop()
        process = getattr(cls, "server_process", None)
        if process is not None and process.poll() is None:
            with contextlib.suppress(Exception):
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    process.kill()

    def _run_browser_probe(self, browser_name: str, launch_callable) -> dict:
        try:
            browser = launch_callable()
        except Exception as exc:
            return {"browser": browser_name, "status": "unavailable", "detail": str(exc)}

        console_messages: list[dict[str, str]] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        result: dict = {"browser": browser_name}
        try:
            context = browser.new_context(base_url=self.base_url, accept_downloads=True)
            page = context.new_page()
            page.on("console", lambda message: console_messages.append({"type": message.type, "text": message.text}))
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("requestfailed", lambda request: failed_requests.append(request.url))
            page.goto("/")
            page.route(
                "**/convert/start",
                lambda route: route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=(
                        '{"success":true,"job_id":"job-privacy","status":"queued","source_type":"pdf",'
                        '"message":"Konwersja wystartowala. Trwa przygotowanie EPUB.","poll_after_ms":1500}'
                    ),
                ),
            )
            page.route(
                "**/convert/status/job-privacy",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"success":true,"job_id":"job-privacy","status":"ready","message":"EPUB gotowy do pobrania.",'
                        '"source_type":"pdf","error":"","conversion":{"profile":"book_reflow","validation":"passed",'
                        '"output_size_bytes":4734,"heading_repair":{"status":"skipped"}},"download_url":"/convert/download/job-privacy",'
                        '"poll_after_ms":0,"elapsed_seconds":5,"output_size_bytes":4734}'
                    ),
                ),
            )
            page.route(
                "**/convert/download/job-privacy",
                lambda route: route.fulfill(
                    status=200,
                    headers={
                        "content-type": "application/epub+zip",
                        "content-disposition": 'attachment; filename="ocr_probe.epub"',
                        "x-source-type": "pdf",
                    },
                    body=b"epub-mock",
                ),
            )
            storage_probe = page.evaluate(
                """
                () => {
                  const issues = [];
                  try {
                    window.localStorage.setItem('km-privacy-probe', '1');
                    window.localStorage.removeItem('km-privacy-probe');
                  } catch (error) {
                    issues.push(`localStorage:${error && error.message ? error.message : String(error)}`);
                  }
                  try {
                    window.sessionStorage.setItem('km-privacy-probe', '1');
                    window.sessionStorage.removeItem('km-privacy-probe');
                  } catch (error) {
                    issues.push(`sessionStorage:${error && error.message ? error.message : String(error)}`);
                  }
                  return issues;
                }
                """
            )
            page.set_input_files("#fileInput", str(SAMPLE_PDF))
            with page.expect_download(timeout=120000) as download_info:
                page.locator("#convertEpubButton").click()
            download = download_info.value
            page.wait_for_function(
                """() => {
                  const element = document.querySelector('#statusText');
                  return !!element && (element.textContent || '').includes('EPUB wygenerowany i pobrany');
                }""",
                timeout=120000,
            )
            status_text = page.locator("#statusText").text_content() or ""
            relevant_failed_requests = [
                request_url
                for request_url in failed_requests
                if "/convert/download/" not in request_url
            ]
            privacy_warnings = [entry for entry in console_messages if _is_privacy_noise_message(entry["text"])]
            if page_errors or relevant_failed_requests:
                classification = "app_bug"
            elif privacy_warnings and not storage_probe:
                classification = "browser_noise"
            elif storage_probe:
                classification = "app_bug"
            else:
                classification = "no_repro"
            result.update(
                {
                    "status": "executed",
                    "classification": classification,
                    "download": download.suggested_filename,
                    "status_text": status_text,
                    "storage_probe": storage_probe,
                    "privacy_warning_count": len(privacy_warnings),
                    "console_messages": console_messages,
                    "page_errors": page_errors,
                    "failed_requests": relevant_failed_requests,
                }
            )
            context.close()
        finally:
            with contextlib.suppress(Exception):
                browser.close()
        return result

    def test_privacy_diagnostics_classify_noise_without_false_app_bug(self) -> None:
        browsers = [
            ("chromium", lambda: self.playwright.chromium.launch(headless=True)),
            ("firefox", lambda: self.playwright.firefox.launch(headless=True)),
            ("edge", lambda: self.playwright.chromium.launch(channel="msedge", headless=True)),
        ]
        results = [self._run_browser_probe(name, launcher) for name, launcher in browsers]
        executed = [item for item in results if item.get("status") == "executed"]
        app_bugs = [item for item in executed if item.get("classification") == "app_bug"]

        self.assertGreaterEqual(len(executed), 1, results)
        self.assertEqual(app_bugs, [], json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
