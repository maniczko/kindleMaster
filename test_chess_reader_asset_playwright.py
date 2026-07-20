from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
JOB_ID = f"yusupov-reader-assets-runtime-{uuid.uuid4().hex}"
DIAGRAM_COUNT = 274
TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, process: subprocess.Popen[bytes], timeout_seconds: float = 90.0) -> None:
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


def _write_reader_fixture(artifact_root: Path) -> None:
    job_root = artifact_root / JOB_ID
    report_dir = job_root / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "chess_games.html").write_text(
        '<!doctype html><html><body data-artifact-type="source_html_evidence_only"></body></html>',
        encoding="utf-8",
    )
    (report_dir / "conversion.quality.json").write_text(
        json.dumps(
            {
                "job": {
                    "job_id": JOB_ID,
                    "status": "ready",
                    "filename": "Yusupov.pdf",
                    "source_type": "pdf",
                }
            }
        ),
        encoding="utf-8",
    )

    semantic_dir = job_root / "semantic_chess_html"
    data_dir = semantic_dir / "data"
    reports_dir = semantic_dir / "reports"
    crop_dir = job_root / "review" / "chess_fen" / "two_crop"
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)

    cards: list[str] = []
    for number in range(1, DIAGRAM_COUNT + 1):
        stem = f"notation_layout_p{number:03d}_01"
        board_name = f"{stem}_board.png"
        marker_name = f"{stem}_marker.png"
        (crop_dir / board_name).write_bytes(TEST_PNG)
        (crop_dir / marker_name).write_bytes(TEST_PNG)
        cards.append(
            f'''<article class="card" data-diagram-number="{number}">
              <img class="board-crop" src="review/chess_fen/two_crop/{board_name}" alt="board crop {number}">
              <img class="marker-crop" src="review/chess_fen/two_crop/{marker_name}" alt="marker crop {number}">
            </article>'''
        )

    (semantic_dir / "index.html").write_text(
        '<!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">'
        + "".join(cards)
        + "</body></html>",
        encoding="utf-8",
    )
    (data_dir / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "schema": "kindlemaster.chess_study.artifact_manifest.v1",
                "artifact_type": "final_pdf_two_crop_reader",
                "pipeline_mode": "pdf_two_crop_reader",
                "diagrams_total": DIAGRAM_COUNT,
                "diagram_cards_count": DIAGRAM_COUNT,
                "board_crop_count": DIAGRAM_COUNT,
                "side_marker_crop_count": DIAGRAM_COUNT,
                "empty_img_src_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "final_reader_health_gate.json").write_text(
        json.dumps(
            {
                "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
                "decision": "pass",
                "status": "PASS",
                "artifact_type": "final_pdf_two_crop_reader",
                "pipeline_mode": "pdf_two_crop_reader",
                "diagrams_total": DIAGRAM_COUNT,
                "diagram_cards_count": DIAGRAM_COUNT,
                "board_crop_count": DIAGRAM_COUNT,
                "side_marker_crop_count": DIAGRAM_COUNT,
                "empty_img_src_count": 0,
                "blockers": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )


class ChessReaderAssetPlaywrightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            raise unittest.SkipTest(f"Python Playwright is unavailable: {exc}") from exc

        cls.artifact_temp_dir = tempfile.TemporaryDirectory()
        cls.artifact_root = Path(cls.artifact_temp_dir.name)
        _write_reader_fixture(cls.artifact_root)
        cls.port = _find_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment["KINDLEMASTER_ARTIFACT_ROOT"] = str(cls.artifact_root)
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        cls.server_process = subprocess.Popen(
            [sys.executable, "kindlemaster.py", "serve", "--runtime", "waitress", "--port", str(cls.port)],
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(cls.base_url, cls.server_process)
        except Exception:
            cls._stop_server()
            cls.artifact_temp_dir.cleanup()
            raise
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-dependent gate
            cls._stop_server()
            cls.playwright.stop()
            cls.artifact_temp_dir.cleanup()
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
        artifact_temp_dir = getattr(cls, "artifact_temp_dir", None)
        if artifact_temp_dir is not None:
            artifact_temp_dir.cleanup()

    @classmethod
    def _stop_server(cls) -> None:
        process = getattr(cls, "server_process", None)
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(Exception):
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                process.wait(timeout=5)

    def test_all_yusupov_reader_assets_load_with_dimensions(self) -> None:
        context = self.browser.new_context()
        page = context.new_page()
        page.set_default_navigation_timeout(120_000)
        failed_requests: list[str] = []
        page.on("requestfailed", lambda request: failed_requests.append(request.url))
        response = page.goto(f"{self.base_url}/convert/artifact/{JOB_ID}/chess_reader", wait_until="networkidle")

        self.assertIsNotNone(response)
        self.assertEqual(response.status, 200)
        cards = page.locator("article.card")
        images = page.locator("article.card img")
        self.assertEqual(cards.count(), DIAGRAM_COUNT)
        self.assertEqual(images.count(), DIAGRAM_COUNT * 2)

        for index in (0, DIAGRAM_COUNT // 2, DIAGRAM_COUNT - 1):
            card_images = cards.nth(index).locator("img")
            self.assertEqual(card_images.count(), 2)
            dimensions = card_images.evaluate_all(
                "nodes => nodes.map(node => ({complete: node.complete, width: node.naturalWidth, height: node.naturalHeight}))"
            )
            self.assertTrue(all(item["complete"] for item in dimensions))
            self.assertTrue(all(item["width"] > 0 and item["height"] > 0 for item in dimensions))

        image_results = images.evaluate_all(
            "nodes => nodes.map(node => ({src: node.src, complete: node.complete, width: node.naturalWidth, height: node.naturalHeight}))"
        )
        self.assertEqual(len(image_results), DIAGRAM_COUNT * 2)
        self.assertTrue(all(item["complete"] for item in image_results))
        self.assertTrue(all(item["width"] > 0 and item["height"] > 0 for item in image_results))
        self.assertEqual(failed_requests, [])

        http_statuses = page.evaluate(
            "async () => Promise.all(Array.from(document.images, image => fetch(image.src).then(response => response.status)))"
        )
        self.assertEqual(http_statuses, [200] * (DIAGRAM_COUNT * 2))

        with urllib.request.urlopen(f"{self.base_url}/convert/status/{JOB_ID}", timeout=10) as status_response:
            status_payload = json.loads(status_response.read().decode("utf-8"))
        health = status_payload["final_reader_health"]
        self.assertEqual(health["referenced_image_asset_count"], DIAGRAM_COUNT * 2)
        self.assertEqual(health["missing_required_asset_count"], 0)
        self.assertEqual(health["status"], "PASS")
        context.close()


if __name__ == "__main__":
    unittest.main()
