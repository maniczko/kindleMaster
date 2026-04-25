from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
SAMPLE_PDF = REPO_ROOT / "reference_inputs" / "pdf" / "ocr_probe.pdf"
PRIVACY_NOISE_MARKERS = (
    "tracking prevention blocked access to storage",
    "blocked access to storage",
    "storage access is denied",
    "permission denied to access property \"localstorage\"",
    "permission denied to access property \"sessionstorage\"",
    "cookies are disabled",
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _is_privacy_noise_message(message: str) -> bool:
    normalized = " ".join(str(message).strip().lower().split())
    return any(marker in normalized for marker in PRIVACY_NOISE_MARKERS)


class BrowserPollingE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            raise unittest.SkipTest(f"Python Playwright nie jest dostępny: {exc}") from exc

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
        cls._wait_until_server_ready()
        cls.playwright = cls._sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            cls._stop_server()
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium Playwright nie jest gotowy: {exc}") from exc

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

    @classmethod
    def _wait_until_server_ready(cls, timeout_seconds: float = 30.0) -> None:
        deadline = time.time() + timeout_seconds
        last_error: Exception | None = None
        while time.time() < deadline:
            if cls.server_process.poll() is not None:
                raise RuntimeError(f"Serwer KindleMaster zakończył się przed startem (code={cls.server_process.returncode}).")
            try:
                with urllib.request.urlopen(f"{cls.base_url}/", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                time.sleep(0.25)
        raise RuntimeError(f"Serwer KindleMaster nie wystartował na {cls.base_url}: {last_error}")

    def setUp(self) -> None:
        self.context = self.browser.new_context(accept_downloads=True)
        self.page = self.context.new_page()

    def tearDown(self) -> None:
        with contextlib.suppress(Exception):
            self.context.close()

    def _load_pdf(self) -> None:
        self.page.goto(f"{self.base_url}/")
        self.page.set_input_files("#fileInput", str(SAMPLE_PDF))
        self.page.locator("#fileName").wait_for(state="visible")
        self.assertEqual(self.page.locator("#fileName").text_content(), "ocr_probe.pdf")
        self.page.locator("#statusText").wait_for(state="visible")

    def _wait_for_status_text(self, fragment: str, timeout_ms: int = 10000) -> str:
        self.page.wait_for_function(
            """([selector, expected]) => {
              const element = document.querySelector(selector);
              return !!element && (element.textContent || "").includes(expected);
            }""",
            arg=["#statusText", fragment],
            timeout=timeout_ms,
        )
        return self.page.locator("#statusText").text_content() or ""

    def _wait_for_any_status_text(self, fragments: list[str], timeout_ms: int = 10000) -> str:
        matched_text = self.page.wait_for_function(
            """([selector, expectedFragments]) => {
              const element = document.querySelector(selector);
              if (!element) return false;
              const text = element.textContent || "";
              return Array.isArray(expectedFragments) && expectedFragments.some((fragment) => text.includes(fragment))
                ? text
                : false;
            }""",
            arg=["#statusText", fragments],
            timeout=timeout_ms,
        )
        return str(matched_text.json_value() or "")

    def test_retry_backoff_recovers_after_transient_status_failures(self) -> None:
        status_calls = {"count": 0}

        def handle_start(route) -> None:
            route.fulfill(
                status=202,
                content_type="application/json",
                body=(
                    '{"success":true,"job_id":"job-retry","status":"queued","source_type":"pdf",'
                    '"message":"Konwersja wystartowala. Trwa przygotowanie EPUB.","poll_after_ms":1500}'
                ),
            )

        def handle_status(route) -> None:
            status_calls["count"] += 1
            if status_calls["count"] < 3:
                route.abort("failed")
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"success":true,"job_id":"job-retry","status":"ready","message":"EPUB gotowy do pobrania.",'
                    '"source_type":"pdf","error":"","conversion":{"profile":"book_reflow","validation":"passed",'
                    '"output_size_bytes":4734,"heading_repair":{"status":"skipped"}},"download_url":"/convert/download/job-retry",'
                    '"poll_after_ms":0,"elapsed_seconds":5,"output_size_bytes":4734}'
                ),
            )

        def handle_download(route) -> None:
            route.fulfill(
                status=200,
                headers={
                    "content-type": "application/epub+zip",
                    "content-disposition": 'attachment; filename="ocr_probe.epub"',
                    "x-source-type": "pdf",
                },
                body=b"epub-mock",
            )

        self.page.route("**/convert/start", handle_start)
        self.page.route("**/convert/status/job-retry", handle_status)
        self.page.route("**/convert/download/job-retry", handle_download)
        self._load_pdf()

        with self.page.expect_download() as download_info:
            self.page.locator("#convertEpubButton").click()
        download = download_info.value
        self.assertIn("ocr_probe", download.suggested_filename)
        rendered = self._wait_for_status_text("EPUB wygenerowany i pobrany")
        self.assertIn("EPUB wygenerowany i pobrany", rendered)
        self.assertIn("KB", rendered)
        self.assertNotIn("0.00 MB", rendered)
        self.assertEqual(status_calls["count"], 3)

    def test_failed_status_ends_flow_without_download(self) -> None:
        downloads: list[str] = []
        self.page.on("download", lambda payload: downloads.append(payload.suggested_filename))

        self.page.route(
            "**/convert/start",
            lambda route: route.fulfill(
                status=202,
                content_type="application/json",
                body=(
                    '{"success":true,"job_id":"job-failed","status":"queued","source_type":"pdf",'
                    '"message":"Konwersja wystartowala. Trwa przygotowanie EPUB.","poll_after_ms":1500}'
                ),
            ),
        )
        self.page.route(
            "**/convert/status/job-failed",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"success":true,"job_id":"job-failed","status":"failed","message":"Konwersja nie powiodla sie.",'
                    '"source_type":"pdf","error":"backend timeout","conversion":null,"download_url":null,'
                    '"poll_after_ms":0,"elapsed_seconds":7,"output_size_bytes":null}'
                ),
            ),
        )

        self._load_pdf()
        self.page.locator("#convertEpubButton").click()
        rendered = self._wait_for_status_text("Konwersja nie powiodla sie: backend timeout")
        self.assertIn("Konwersja nie powiodla sie: backend timeout", rendered)
        self.page.wait_for_timeout(300)
        self.assertEqual(downloads, [])

    def test_status_timeout_exhausts_retry_budget_with_controlled_error(self) -> None:
        self.page.add_init_script(
            """
            (() => {
              const originalSetTimeout = window.setTimeout.bind(window);
              window.setTimeout = (fn, ms, ...args) => originalSetTimeout(fn, Math.min(ms, 50), ...args);
              const originalFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === "string" ? input : input.url;
                if (url.includes("/convert/status/job-timeout")) {
                  return new Promise((resolve, reject) => {
                    const signal = init && init.signal;
                    if (!signal) {
                      return;
                    }
                    if (signal.aborted) {
                      reject(new DOMException("Aborted", "AbortError"));
                      return;
                    }
                    signal.addEventListener(
                      "abort",
                      () => reject(new DOMException("Aborted", "AbortError")),
                      { once: true },
                    );
                  });
                }
                return originalFetch(input, init);
              };
            })();
            """
        )
        self.page.route(
            "**/convert/start",
            lambda route: route.fulfill(
                status=202,
                content_type="application/json",
                body=(
                    '{"success":true,"job_id":"job-timeout","status":"queued","source_type":"pdf",'
                    '"message":"Konwersja wystartowala. Trwa przygotowanie EPUB.","poll_after_ms":1500}'
                ),
            ),
        )

        self._load_pdf()
        self.page.locator("#convertEpubButton").click()
        rendered = self._wait_for_any_status_text(
            [
                "Polaczenie z lokalnym serwerem konwersji zostalo przerwane",
                "Przekroczono limit czasu odpowiedzi lokalnego serwera",
            ],
            timeout_ms=12000,
        )
        self.assertTrue(
            "Polaczenie z lokalnym serwerem konwersji zostalo przerwane" in rendered
            or "Przekroczono limit czasu odpowiedzi lokalnego serwera" in rendered
        )

    def test_start_timeout_fails_before_polling_begins(self) -> None:
        self.page.add_init_script(
            """
            (() => {
              const originalSetTimeout = window.setTimeout.bind(window);
              window.setTimeout = (fn, ms, ...args) => originalSetTimeout(fn, Math.min(ms, 50), ...args);
              const originalFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === "string" ? input : input.url;
                if (url.endsWith("/convert/start")) {
                  return new Promise((resolve, reject) => {
                    const signal = init && init.signal;
                    if (!signal) {
                      return;
                    }
                    if (signal.aborted) {
                      reject(new DOMException("Aborted", "AbortError"));
                      return;
                    }
                    signal.addEventListener(
                      "abort",
                      () => reject(new DOMException("Aborted", "AbortError")),
                      { once: true },
                    );
                  });
                }
                return originalFetch(input, init);
              };
            })();
            """
        )

        self._load_pdf()
        self.page.locator("#convertEpubButton").click()
        text = self._wait_for_status_text("Przekroczono limit czasu odpowiedzi lokalnego serwera", timeout_ms=12000)
        self.assertIn("Przekroczono limit czasu odpowiedzi lokalnego serwera", text)
        self.assertNotIn("Ponawiam probe", text)

    def test_tracking_prevention_console_noise_does_not_break_successful_flow(self) -> None:
        console_entries: list[dict[str, str]] = []
        self.page.on(
            "console",
            lambda message: console_entries.append(
                {
                    "type": message.type,
                    "text": message.text,
                }
            ),
        )

        self.page.route(
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
        self.page.route(
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
        self.page.route(
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

        self._load_pdf()
        self.page.evaluate(
            """
            () => {
              console.warn('Tracking Prevention blocked access to storage for this page.');
              console.warn('Permission denied to access property "localStorage".');
            }
            """
        )
        with self.page.expect_download() as download_info:
            self.page.locator("#convertEpubButton").click()

        download = download_info.value
        self.assertIn("ocr_probe", download.suggested_filename)
        rendered = self._wait_for_status_text("EPUB wygenerowany i pobrany")
        self.assertIn("EPUB wygenerowany i pobrany", rendered)
        privacy_warnings = [entry for entry in console_entries if _is_privacy_noise_message(entry["text"])]
        self.assertGreaterEqual(len(privacy_warnings), 2, console_entries)
        self.assertFalse(any(entry["type"] == "error" for entry in console_entries), console_entries)


if __name__ == "__main__":
    unittest.main()
