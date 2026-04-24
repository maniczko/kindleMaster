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
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
SAMPLE_PDF = REPO_ROOT / "reference_inputs" / "pdf" / "ocr_probe.pdf"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _server_is_ready(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _wait_for_server(base_url: str, process: subprocess.Popen[bytes], timeout_seconds: float = 45.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Waitress runtime zakończyl się przed startem (code={process.returncode}).")
        try:
            if _server_is_ready(base_url):
                return
        except Exception as exc:  # pragma: no cover - defensive guard
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Waitress runtime nie wystartowal pod {base_url}: {last_error}")


def _encode_multipart_form_data(*, fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "----KindleMasterBoundary7MA4YWxkTrZu0gW"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), boundary


class WaitressRuntimeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.port = _find_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.branded_host_header = f"kindlemaster.localhost:{cls.port}"
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

    @classmethod
    def tearDownClass(cls) -> None:
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

    def test_waitress_runtime_accepts_branded_host_header(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/",
            headers={"Host": self.branded_host_header},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers.get("Content-Type", ""))

        self.assertIn("KindleMaster", html)

    def test_waitress_runtime_roundtrip_for_async_convert_flow(self) -> None:
        file_bytes = SAMPLE_PDF.read_bytes()
        body, boundary = _encode_multipart_form_data(
            fields={
                "profile": "auto-premium",
                "ocr": "false",
                "language": "pl",
                "heading_repair": "false",
            },
            file_field="file",
            filename="ocr_probe.pdf",
            file_bytes=file_bytes,
            content_type="application/pdf",
        )
        start_request = urllib.request.Request(
            f"{self.base_url}/convert/start",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Host": self.branded_host_header,
            },
            method="POST",
        )
        with urllib.request.urlopen(start_request, timeout=60) as response:
            self.assertEqual(response.status, 202)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            start_payload = json.loads(response.read().decode("utf-8"))

        self.assertTrue(start_payload["success"])
        self.assertEqual(start_payload["status"], "queued")
        job_id = start_payload["job_id"]
        self.assertGreaterEqual(int(start_payload["poll_after_ms"]), 1200)

        status_payload = None
        # Waitress + full conversion can take noticeably longer on a busy local
        # machine than the pure Flask/client test path, so keep this live gate
        # more tolerant than the unit-level async checks.
        deadline = time.time() + 180
        while time.time() < deadline:
            status_request = urllib.request.Request(
                f"{self.base_url}/convert/status/{job_id}",
                headers={"Host": self.branded_host_header},
                method="GET",
            )
            with urllib.request.urlopen(status_request, timeout=60) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("no-store", response.headers.get("Cache-Control", ""))
                status_payload = json.loads(response.read().decode("utf-8"))
            if status_payload["status"] == "ready":
                break
            if status_payload["status"] == "failed":
                self.fail(f"Waitress runtime zwrócil failed: {status_payload}")
            time.sleep(max(0.5, min(float(status_payload.get("poll_after_ms", 1500)) / 1000.0, 3.0)))

        self.assertIsNotNone(status_payload)
        self.assertEqual(status_payload["status"], "ready")
        self.assertEqual(status_payload["source_type"], "pdf")
        self.assertTrue(status_payload["download_url"])
        self.assertEqual(status_payload["quality_state_url"], f"/convert/quality/{job_id}")
        self.assertGreater(int(status_payload["output_size_bytes"] or 0), 0)
        self.assertTrue(status_payload["quality_state"]["quality_available"])
        self.assertTrue(status_payload["quality_state"]["download_ready"])
        self.assertEqual(status_payload["quality_state"]["download_url"], status_payload["download_url"])
        self.assertEqual(status_payload["quality_state"]["status"], "ready")
        self.assertEqual(status_payload["quality_state"]["phase"], "completed")
        self.assertEqual(status_payload["quality_state"]["source_type"], "pdf")
        self.assertEqual(
            status_payload["quality_state"]["summary"]["output_size_bytes"],
            int(status_payload["output_size_bytes"]),
        )

        quality_request = urllib.request.Request(
            f"{self.base_url}{status_payload['quality_state_url']}",
            headers={"Host": self.branded_host_header},
            method="GET",
        )
        with urllib.request.urlopen(quality_request, timeout=60) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            quality_payload = json.loads(response.read().decode("utf-8"))

        self.assertTrue(quality_payload["success"])
        self.assertEqual(quality_payload["job_id"], job_id)
        self.assertEqual(quality_payload["quality_state"], status_payload["quality_state"])

        download_request = urllib.request.Request(
            f"{self.base_url}{status_payload['download_url']}",
            headers={"Host": self.branded_host_header},
            method="GET",
        )
        with urllib.request.urlopen(download_request, timeout=60) as response:
            download_bytes = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/epub+zip")
            self.assertGreater(len(download_bytes), 0)


if __name__ == "__main__":
    unittest.main()
