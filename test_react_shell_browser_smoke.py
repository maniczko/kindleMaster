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


REPO_ROOT = Path(__file__).resolve().parent


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_until_server_ready(base_url: str, process: subprocess.Popen[bytes], timeout_seconds: float = 60.0) -> None:
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


class ReactShellBrowserSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            raise unittest.SkipTest(f"Python Playwright is unavailable: {exc}") from exc

        if not (REPO_ROOT / "static" / "react" / "index.html").exists():
            raise unittest.SkipTest("React production bundle is unavailable; run `npm run build:ui` before browser smoke")

        cls.port = _find_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        cls.server_process = subprocess.Popen(
            [sys.executable, "kindlemaster.py", "serve", "--runtime", "flask", "--port", str(cls.port)],
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_until_server_ready(cls.base_url, cls.server_process)
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            cls._stop_server()
            cls.playwright.stop()
            raise unittest.SkipTest(f"Playwright Chromium is unavailable: {exc}") from exc

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
        self.console_errors: list[str] = []
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.on(
            "console",
            lambda message: self.console_errors.append(message.text) if message.type == "error" else None,
        )
        self._install_api_routes()

    def tearDown(self) -> None:
        with contextlib.suppress(Exception):
            self.context.close()

    def _install_api_routes(self) -> None:
        quality_state = {
            "score": 97,
            "release_verdict": "release_ready",
            "send_to_kindle_ready": True,
            "send_to_kindle_blockers": [],
            "user_facing_verdict": {"label": "Publikuj", "detail": "Gotowe."},
            "reports": {"json": "/convert/report/job-browser/json"},
        }
        job = {
            "job_id": "job-browser",
            "filename": "browser-smoke.pdf",
            "source_type": "pdf",
            "status": "ready",
            "elapsed_seconds": 4.2,
            "output_size_bytes": 4096,
            "download_url": "/convert/download/job-browser",
            "source_preview_url": "/convert/preview/job-browser/input",
            "quality_state": quality_state,
        }
        profile = {
            "conversion": {
                "default_profile": "auto-premium",
                "default_language": "pl",
                "force_ocr": False,
                "heading_repair": True,
            },
            "email_delivery": {
                "enabled": True,
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "operator@example.com",
                "from_address": "operator@example.com",
                "default_recipient": "reader@kindle.com",
                "max_attachment_bytes": 52428800,
                "secret_configured": True,
            },
        }

        self.page.route(
            "**/auth/config",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body({"success": True, "auth": {"enabled": False, "configured": False}}),
            ),
        )
        self.page.route(
            "**/convert/jobs**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body({"jobs": [job]}),
            ),
        )
        self.page.route(
            "**/user/profile",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body({"success": True, "profile": profile}),
            ),
        )
        self.page.route(
            "**/convert/delivery/config",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body(
                    {
                        "success": True,
                        "delivery": {
                            "enabled": True,
                            "configured": True,
                            "provider": "smtp",
                            "secret_configured": True,
                            "profile_configured": True,
                            "missing_config": [],
                        },
                    }
                ),
            ),
        )

    def test_key_react_views_render_and_navigate_without_console_errors(self) -> None:
        self.page.goto(f"{self.base_url}/app#library")

        self.page.locator("h1", has_text="Biblioteka").wait_for()
        self.page.get_by_role("button", name="browser-smoke.pdf", exact=True).wait_for()
        self.assertEqual(self.page.locator('a:has-text("PDF")').get_attribute("href"), "/convert/preview/job-browser/input")

        self.page.get_by_role("button", name="Otwórz").click()
        self.page.locator("h1", has_text="Szczegóły pliku").wait_for()
        self.assertTrue(self.page.get_by_label("Informacje o aktywnym zadaniu").is_visible())
        self.assertIn("browser-smoke.pdf", self.page.text_content("body") or "")

        self.page.get_by_role("button", name="Ustawienia").click()
        self.page.locator("h1", has_text="Ustawienia").wait_for()
        self.assertTrue(self.page.get_by_label("Domyślny adres Kindle").is_visible())
        self.assertEqual(self.page.get_by_label("Domyślny adres Kindle").input_value(), "reader@kindle.com")

        self.page.get_by_role("button", name="Strona główna KindleMaster").click()
        self.page.locator("h1", has_text="Konwersja").wait_for()
        self.assertFalse(self.console_errors, "\n".join(self.console_errors))


if __name__ == "__main__":
    unittest.main()
