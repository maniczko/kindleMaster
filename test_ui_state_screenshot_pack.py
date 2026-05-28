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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
SAMPLE_PDF = REPO_ROOT / "reference_inputs" / "pdf" / "ocr_probe.pdf"
SAMPLE_DOCX = REPO_ROOT / "reference_inputs" / "docx" / "simple_report.docx"
OUTPUT_ROOT = REPO_ROOT / "output" / "ui-state-screenshots"
REPORT_ROOT = REPO_ROOT / "reports" / "ui-state-screenshots"
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


def _wait_until_server_ready(base_url: str, process: subprocess.Popen[bytes], timeout_seconds: float = 90.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Waitress runtime stopped before startup (code={process.returncode}).")
        try:
            with urllib.request.urlopen(f"{base_url}/", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Waitress runtime did not start at {base_url}: {last_error}")


def _is_privacy_noise_message(message: str) -> bool:
    normalized = " ".join(str(message or "").strip().lower().split())
    return any(marker in normalized for marker in PRIVACY_NOISE_MARKERS)


MappingLike = dict[str, Any]


def _json_body(payload: MappingLike) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _quality_state(
    *,
    job_id: str,
    release_verdict: str,
    reading_verdict: str,
    release_blocked: bool = False,
    blockers: list[MappingLike] | None = None,
    warnings: list[MappingLike] | None = None,
    review: list[MappingLike] | None = None,
    send_to_kindle_ready: bool = True,
) -> MappingLike:
    return {
        "job_id": job_id,
        "source_type": "pdf",
        "quality_available": True,
        "download_available": True,
        "download_url": f"/convert/download/{job_id}",
        "reading_verdict": reading_verdict,
        "release_verdict": release_verdict,
        "release_blocked": release_blocked,
        "quality_blockers": blockers or [],
        "send_to_kindle_ready": send_to_kindle_ready,
        "send_to_kindle_blockers": [] if send_to_kindle_ready else [{"code": "kindle_delivery_not_verified"}],
        "premium_scoring": {
            "status": "passed" if release_verdict == "release_ready" else "failed" if release_blocked else "passed_with_warnings",
            "premium_score": 9.4 if release_verdict == "release_ready" else 5.8 if release_blocked else 7.6,
            "kindle_ready": not release_blocked,
        },
        "ai_verifier": {
            "status": "passed" if release_verdict == "release_ready" else "failed" if release_blocked else "passed_with_warnings",
            "message": "AI verifier evidence available.",
        },
        "summary": {
            "profile": "book_reflow",
            "strategy": "premium",
            "layout": "reflowable",
            "sections": 14,
            "assets": 3,
            "output_size_bytes": 734003,
        },
        "validation": {"status": "passed", "tool": "epubcheck"},
        "audit": {
            "warning_count": len(warnings or []),
            "warnings": warnings or [],
            "high_risk_pages": 0,
            "high_risk_sections": 0,
        },
        "metadata_health": {"status": "passed", "message": "Metadata complete."},
        "link_health": {"status": "passed", "message": "No broken links reported."},
        "visible_junk": {"status": "passed", "message": "No visible junk reported."},
        "issue_groups": {
            "blockers": blockers or [],
            "warnings": warnings or [],
            "review": review or [],
        },
        "content_metrics": {
            "status": "reported",
            "sections": 14,
            "assets": 3,
            "xhtml_table_count": 2,
            "source_table_count": 2,
        },
        "toc_preview": {"status": "passed", "entry_count": 12, "entries": ["Executive summary", "Chapter 1"]},
        "asset_summary": {"status": "passed", "image_count": 3, "asset_budget_status": "passed"},
        "metadata_summary": {
            "status": "passed",
            "title": "Quality state sample",
            "creator": "KindleMaster",
            "language": "pl",
        },
        "epubcheck_detail": {"status": "passed", "error_count": 0, "warning_count": 0, "messages": []},
        "quality_completeness": {
            "status": "complete",
            "score": 100,
            "expected_sections": 8,
            "reported_sections": 8,
            "missing_count": 0,
            "not_reported_count": 0,
            "sections": [],
        },
        "user_facing_verdict": {
            "label": "Publikuj" if release_verdict == "release_ready" else "Nie publikuj" if release_blocked else "Kontrola",
            "message": "EPUB generated with quality evidence.",
        },
        "user_facing_reasons": [
            "EPUB wygenerowany, ale wymaga kontroli jakości." if release_blocked else "Quality evidence available."
        ],
    }


def _ready_payload(job_id: str, quality_state: MappingLike) -> MappingLike:
    return {
        "success": True,
        "job_id": job_id,
        "status": "ready",
        "message": "EPUB gotowy do pobrania.",
        "source_type": "pdf",
        "download_url": f"/convert/download/{job_id}",
        "quality_state_url": f"/convert/quality/{job_id}",
        "quality_state": quality_state,
        "conversion": {
            "profile": "book_reflow",
            "validation": "passed",
            "output_size_bytes": 734003,
            "heading_repair": {"status": "skipped"},
        },
        "output_size_bytes": 734003,
        "poll_after_ms": 0,
        "elapsed_seconds": 4,
    }


def _library_item(
    job_id: str,
    *,
    title: str,
    filename: str,
    release_verdict: str,
    reading_verdict: str = "ready_with_review",
    release_blocked: bool = False,
    text_excerpt: str = "",
) -> MappingLike:
    return {
        "job_id": job_id,
        "title": title,
        "filename": filename,
        "source_type": "pdf",
        "document_class": "document_like_report",
        "status": "ready",
        "release_verdict": release_verdict,
        "reading_verdict": reading_verdict,
        "release_blocked": release_blocked,
        "download_available": True,
        "download_url": f"/convert/download/{job_id}",
        "quality_state_url": f"/convert/quality/{job_id}",
        "report_json_url": f"/convert/report/{job_id}.json",
        "report_markdown_url": f"/convert/report/{job_id}.md",
        "output_size_bytes": 734003,
        "elapsed_seconds": 8,
        "text_excerpt": text_excerpt,
        "searchable_text_available": bool(text_excerpt),
    }


class UiStateScreenshotPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            raise unittest.SkipTest(f"Python Playwright is unavailable: {exc}") from exc

        if not SAMPLE_PDF.exists():
            raise unittest.SkipTest(f"Missing PDF fixture: {SAMPLE_PDF}")
        if not SAMPLE_DOCX.exists():
            raise unittest.SkipTest(f"Missing DOCX fixture: {SAMPLE_DOCX}")

        cls.run_id = os.environ.get("KINDLEMASTER_UI_STATE_RUN_ID", "latest")
        cls.output_dir = OUTPUT_ROOT / cls.run_id
        cls.report_dir = REPORT_ROOT / cls.run_id
        cls.output_dir.mkdir(parents=True, exist_ok=True)
        cls.report_dir.mkdir(parents=True, exist_ok=True)
        cls.manifest: MappingLike = {
            "run_id": cls.run_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "states": [],
        }
        cls.console_entries: list[MappingLike] = []

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
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            cls._stop_server()
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium Playwright is unavailable: {exc}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls._write_reports()
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
    def _write_reports(cls) -> None:
        report_dir = getattr(cls, "report_dir", None)
        if report_dir is None:
            return
        manifest = getattr(cls, "manifest", {"states": []})
        console_entries = getattr(cls, "console_entries", [])
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (report_dir / "console.json").write_text(
            json.dumps(console_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        lines = [
            f"# KindleMaster UI state screenshot pack: {manifest.get('run_id', 'unknown')}",
            "",
            f"- States captured: {len(manifest.get('states', []))}",
            f"- Console entries: {len(console_entries)}",
            "",
            "| State | Viewport | Screenshot | Horizontal overflow |",
            "| --- | --- | --- | --- |",
        ]
        for item in manifest.get("states", []):
            lines.append(
                "| {state} | {viewport} | `{path}` | {overflow} |".format(
                    state=item.get("state", ""),
                    viewport=item.get("viewport", ""),
                    path=item.get("screenshot", ""),
                    overflow="yes" if item.get("horizontal_overflow") else "no",
                )
            )
        (report_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _open_state_page(
        self,
        *,
        viewport: tuple[int, int],
        jobs: list[MappingLike] | None = None,
        library_items: list[MappingLike] | None = None,
        search_items: list[MappingLike] | None = None,
    ):
        context = self.browser.new_context(
            base_url=self.base_url,
            accept_downloads=True,
            viewport={"width": viewport[0], "height": viewport[1]},
        )
        context.add_init_script("window.localStorage.setItem('kindlemaster.start.continue-local', '1');")
        context.add_init_script(
            """
            (() => {
              const makeViewport = (scale = 1) => ({
                width: 612 * scale,
                height: 792 * scale,
                scale,
              });
              window.pdfjsLib = {
                GlobalWorkerOptions: {},
                getDocument: () => ({
                  promise: Promise.resolve({
                    numPages: 3,
                    getPage: async () => ({
                      getViewport: ({ scale = 1 } = {}) => makeViewport(scale),
                      render: ({ canvasContext, viewport }) => {
                        if (canvasContext && viewport) {
                          canvasContext.save();
                          canvasContext.fillStyle = "#ffffff";
                          canvasContext.fillRect(0, 0, viewport.width, viewport.height);
                          canvasContext.fillStyle = "#f3f4f6";
                          canvasContext.fillRect(48, 48, viewport.width - 96, viewport.height - 96);
                          canvasContext.fillStyle = "#111827";
                          canvasContext.font = "24px sans-serif";
                          canvasContext.fillText("KindleMaster PDF preview", 72, 96);
                          canvasContext.restore();
                        }
                        return { promise: Promise.resolve(), cancel: () => {} };
                      },
                    }),
                  }),
                }),
              };
              window.PDFLib = {
                PDFDocument: {
                  load: async () => ({
                    getPages: () => [
                      { getWidth: () => 612, getHeight: () => 792 },
                    ],
                    embedPage: async () => ({}),
                  }),
                  create: async () => ({
                    addPage: () => ({ drawPage: () => {} }),
                    save: async () => new Uint8Array([37, 80, 68, 70]),
                  }),
                },
              };
            })();
            """
        )
        page = context.new_page()
        page.on("console", lambda message: self._record_console(message))
        page.on("pageerror", lambda error: self._record_page_error(error))
        self._install_library_routes(
            page,
            jobs=jobs or [],
            library_items=library_items or [],
            search_items=search_items or library_items or [],
        )
        page.goto("/")
        page.wait_for_selector('[data-vr-hook="vat-209-shell"]')
        return context, page

    def _install_library_routes(
        self,
        page,
        *,
        jobs: list[MappingLike],
        library_items: list[MappingLike],
        search_items: list[MappingLike],
    ) -> None:
        page.route(
            "**/convert/jobs*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body({"success": True, "jobs": jobs, "count": len(jobs)}),
            ),
        )
        page.route(
            "**/convert/library*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body(
                    {
                        "success": True,
                        "index_version": "kindlemaster-library-v1",
                        "items": library_items,
                        "count": len(library_items),
                    }
                ),
            ),
        )
        page.route(
            "**/convert/search*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_json_body(
                    {
                        "success": True,
                        "index_version": "kindlemaster-library-v1",
                        "items": search_items,
                        "count": len(search_items),
                    }
                ),
            ),
        )

    def _install_conversion_routes(self, page, *, job_id: str, status_payload: MappingLike) -> None:
        page.route(
            "**/convert/start",
            lambda route: route.fulfill(
                status=202,
                content_type="application/json",
                body=_json_body(
                    {
                        "success": True,
                        "job_id": job_id,
                        "status": "queued",
                        "source_type": "pdf",
                        "message": "Konwersja wystartowala. Trwa przygotowanie EPUB.",
                        "poll_after_ms": 50,
                    }
                ),
            ),
        )
        page.route(
            f"**/convert/status/{job_id}",
            lambda route: route.fulfill(status=200, content_type="application/json", body=_json_body(status_payload)),
        )
        page.route(
            f"**/convert/download/{job_id}",
            lambda route: route.fulfill(
                status=200,
                headers={
                    "content-type": "application/epub+zip",
                    "content-disposition": f'attachment; filename="{job_id}.epub"',
                },
                body=b"epub-mock",
            ),
        )

    def _record_console(self, message) -> None:
        self.console_entries.append({"type": message.type, "text": message.text})

    def _record_page_error(self, error: Exception) -> None:
        self.console_entries.append({"type": "pageerror", "text": str(error)})

    def _capture(self, page, *, state: str, viewport_name: str) -> None:
        page.wait_for_timeout(200)
        viewport_dir = self.output_dir / viewport_name
        viewport_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = viewport_dir / f"{state}.png"
        page.screenshot(path=str(screenshot_path), full_page=False)
        metrics = page.evaluate(
            """() => ({
              title: document.title,
              scrollWidth: document.documentElement.scrollWidth,
              innerWidth: window.innerWidth,
              horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
              statusText: document.querySelector('#statusText')?.textContent || '',
              hasQualityDecision: Boolean(document.querySelector('#qualityDecisionStrip')),
              qualityHeroText: document.querySelector('#qualityVerdictHeader')?.textContent || '',
              hasLibraryList: Boolean(document.querySelector('#recentConversionsList')),
            })"""
        )
        self.manifest["states"].append(
            {
                "state": state,
                "viewport": viewport_name,
                "screenshot": str(screenshot_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "horizontal_overflow": bool(metrics["horizontalOverflow"]),
                "status_text": metrics["statusText"],
                "has_quality_decision": bool(metrics["hasQualityDecision"]),
                "has_library_list": bool(metrics["hasLibraryList"]),
            }
        )
        if metrics["hasQualityDecision"]:
            self.assertIn("Premium score", metrics["qualityHeroText"])
            self.assertIn("Kindle-ready", metrics["qualityHeroText"])
            self.assertIn("AI verifier", metrics["qualityHeroText"])
        self.assertFalse(metrics["horizontalOverflow"], f"{state}/{viewport_name} has horizontal overflow: {metrics}")

    def _load_pdf(self, page) -> None:
        page.set_input_files("#fileInput", str(SAMPLE_PDF))
        page.locator("#fileName").wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            """() => (document.querySelector('#statusText')?.textContent || '').includes('PDF gotowy')""",
            timeout=15000,
        )

    def _load_docx(self, page) -> None:
        page.set_input_files("#fileInput", str(SAMPLE_DOCX))
        page.locator("#fileName").wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            """() => (document.querySelector('#statusText')?.textContent || '').includes('DOCX gotowy')""",
            timeout=15000,
        )

    def _capture_static_state(self, *, state: str, viewport_name: str, viewport: tuple[int, int]) -> None:
        context, page = self._open_state_page(viewport=viewport)
        try:
            self._capture(page, state=state, viewport_name=viewport_name)
        finally:
            context.close()

    def test_capture_ui_state_screenshot_pack(self) -> None:
        viewports = {
            "desktop": (1440, 900),
            "tablet": (768, 900),
            "mobile": (375, 800),
        }
        self._capture_static_state(state="empty", viewport_name="desktop", viewport=viewports["desktop"])
        self._capture_static_state(state="empty", viewport_name="tablet", viewport=viewports["tablet"])
        self._capture_static_state(state="empty", viewport_name="mobile", viewport=viewports["mobile"])

        context, page = self._open_state_page(viewport=viewports["desktop"])
        try:
            self._load_pdf(page)
            self._capture(page, state="file-selected-pdf", viewport_name="desktop")
        finally:
            context.close()

        context, page = self._open_state_page(viewport=viewports["desktop"])
        try:
            self._load_docx(page)
            self._capture(page, state="file-selected-docx", viewport_name="desktop")
        finally:
            context.close()

        self._capture_conversion_state(
            state="running",
            job_id="job-running",
            status_payload={
                "success": True,
                "job_id": "job-running",
                "status": "running",
                "message": "Trwa konwersja EPUB. Nie zamykaj tej karty.",
                "source_type": "pdf",
                "conversion": None,
                "download_url": None,
                "poll_after_ms": 5000,
                "elapsed_seconds": 3,
            },
            wait_fragment="Trwa konwersja EPUB",
        )

        self._capture_conversion_state(
            state="ready",
            job_id="job-ready",
            status_payload=_ready_payload(
                "job-ready",
                _quality_state(job_id="job-ready", release_verdict="release_ready", reading_verdict="ready"),
            ),
            wait_fragment="EPUB wygenerowany",
            expect_download=True,
        )

        self._capture_conversion_state(
            state="ready-with-review",
            job_id="job-review",
            status_payload=_ready_payload(
                "job-review",
                _quality_state(
                    job_id="job-review",
                    release_verdict="ready_with_review",
                    reading_verdict="ready_with_review",
                    warnings=[{"severity": "warning", "code": "toc_review", "message": "TOC requires review."}],
                    review=[{"severity": "review", "code": "manual_review", "message": "Manual review item."}],
                    send_to_kindle_ready=False,
                ),
            ),
            wait_fragment="EPUB wygenerowany",
            expect_download=True,
        )

        self._capture_conversion_state(
            state="release-blocked",
            job_id="job-blocked",
            status_payload=_ready_payload(
                "job-blocked",
                _quality_state(
                    job_id="job-blocked",
                    release_verdict="release_blocked",
                    reading_verdict="ready_with_review",
                    release_blocked=True,
                    blockers=[
                        {
                            "severity": "error",
                            "code": "reference_coverage_failed",
                            "message": "References are incomplete.",
                            "source": "quality_gate",
                        }
                    ],
                    send_to_kindle_ready=False,
                ),
            ),
            wait_fragment="Wymaga naprawy przed publikacją",
            expect_download=True,
        )

        self._capture_conversion_state(
            state="failed",
            job_id="job-failed-ui",
            status_payload={
                "success": True,
                "job_id": "job-failed-ui",
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "error": "backend timeout",
                "conversion": None,
                "download_url": None,
                "poll_after_ms": 0,
                "elapsed_seconds": 7,
            },
            wait_fragment="backend timeout",
        )

        self._capture_conversion_state(
            state="application-restart",
            job_id="job-restart-ui",
            status_payload={
                "success": True,
                "job_id": "job-restart-ui",
                "status": "failed",
                "message": "Konwersja przerwana przez restart aplikacji.",
                "source_type": "pdf",
                "error": "Konwersja zostala przerwana przez restart aplikacji. Uruchom konwersje ponownie.",
                "error_code": "application_restart",
                "conversion": None,
                "download_url": None,
                "poll_after_ms": 0,
                "elapsed_seconds": 7,
            },
            wait_fragment="Lokalna aplikacja zostala zrestartowana",
        )

        self._capture_library_state(state="library-empty", items=[], search=False)
        library_items = [
            _library_item(
                "library-ready",
                title="Raport gotowy",
                filename="raport.pdf",
                release_verdict="release_ready",
                reading_verdict="ready",
                text_excerpt="Gotowy raport do czytania.",
            ),
            _library_item(
                "library-blocked",
                title="Raport do kontroli",
                filename="blocked.pdf",
                release_verdict="release_blocked",
                release_blocked=True,
                text_excerpt="Znaleziono blocker referencji.",
            ),
        ]
        self._capture_library_state(state="library-populated", items=library_items, search=False)
        self._capture_library_state(state="library-search-release-blocked", items=[library_items[1]], search=True)

        problematic_console = [
            entry
            for entry in self.console_entries
            if entry["type"] in {"error", "warning", "pageerror"} and not _is_privacy_noise_message(entry["text"])
        ]
        self.assertEqual(problematic_console, [])
        self.assertGreaterEqual(len(self.manifest["states"]), 13)

    def _capture_conversion_state(
        self,
        *,
        state: str,
        job_id: str,
        status_payload: MappingLike,
        wait_fragment: str,
        expect_download: bool = False,
    ) -> None:
        context, page = self._open_state_page(viewport=(1440, 900))
        try:
            self._install_conversion_routes(page, job_id=job_id, status_payload=status_payload)
            self._load_pdf(page)
            if expect_download:
                with page.expect_download(timeout=10000):
                    page.locator("#convertEpubButton").click()
            else:
                page.locator("#convertEpubButton").click()
            page.wait_for_function(
                """([selector, expected]) => {
                  const element = document.querySelector(selector);
                  return !!element && (element.textContent || '').includes(expected);
                }""",
                arg=["#statusText", wait_fragment],
                timeout=15000,
            )
            self._capture(page, state=state, viewport_name="desktop")
        finally:
            context.close()

    def _capture_library_state(self, *, state: str, items: list[MappingLike], search: bool) -> None:
        context, page = self._open_state_page(viewport=(1440, 900), library_items=items, search_items=items)
        try:
            if search:
                page.fill("#librarySearchInput", "blocker")
                page.locator("#librarySearchButton").click()
            else:
                page.evaluate(
                    "() => { setLibraryViewVisible(true); return loadConversionLibrary({ silent: false }); }"
                )
            expected = "Brak wyników biblioteki" if not items else items[0]["title"]
            page.wait_for_timeout(300)
            list_text = page.locator("#libraryResultsList").text_content() or ""
            self.assertIn(expected, list_text)
            self._capture(page, state=state, viewport_name="desktop")
        finally:
            context.close()
