from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"
REPO_ROOT = Path(__file__).resolve().parent
LOCALHOST = "127.0.0.1"
LOCAL_SERVER_URL = f"http://{LOCALHOST}:5001"
SAMPLE_PDF = REPO_ROOT / "reference_inputs" / "pdf" / "ocr_probe.pdf"
PRIVACY_NOISE_MARKERS = (
    "tracking prevention blocked access to storage",
    "blocked access to storage",
    "storage access is denied",
    "permission denied to access property \"localstorage\"",
    "permission denied to access property \"sessionstorage\"",
    "cookies are disabled",
)


def _extract_function_source(html: str, function_name: str) -> str:
    candidates = [
        f"async function {function_name}(",
        f"function {function_name}(",
    ]
    start = -1
    for candidate in candidates:
        start = html.find(candidate)
        if start >= 0:
            break
    if start < 0:
        raise AssertionError(f"Nie znaleziono funkcji {function_name} w templates/index.html")

    brace_index = html.find("{", start)
    if brace_index < 0:
        raise AssertionError(f"Nie znaleziono otwarcia funkcji {function_name}")

    depth = 0
    for index in range(brace_index, len(html)):
        character = html[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return html[start : index + 1]
    raise AssertionError(f"Nie domknięto funkcji {function_name}")


def _is_privacy_noise_message(message: str) -> bool:
    normalized = " ".join(str(message).strip().lower().split())
    return any(marker in normalized for marker in PRIVACY_NOISE_MARKERS)


@unittest.skipUnless(shutil.which("node"), "Node.js jest wymagany do harnessu browser/runtime polling.")
class BrowserPollingRuntimeHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        template_html = TEMPLATE_PATH.read_text(encoding="utf-8")
        cls.function_sources = [
            _extract_function_source(template_html, "isTransientConversionNetworkError"),
            _extract_function_source(template_html, "nextPollDelay"),
            _extract_function_source(template_html, "pollConversionJob"),
        ]

    def _run_polling_scenario(self, steps: list[dict]) -> dict:
        node_script = f"""
const vm = require("node:vm");
const functionSources = {json.dumps(self.function_sources, ensure_ascii=False)};
const steps = {json.dumps(steps, ensure_ascii=False)};
const statusLog = [];
const delayLog = [];
let fetchCalls = 0;
let nowTick = 0;

const BASE_CONVERSION_POLL_INTERVAL_MS = 1500;
const MAX_CONVERSION_POLL_INTERVAL_MS = 5000;
const MAX_CONVERSION_WAIT_MS = 15 * 60 * 1000;
const MAX_CONVERSION_POLL_ERRORS = 3;

function setStatus(message, level) {{
  statusLog.push({{ message, level }});
}}

async function delay(ms) {{
  delayLog.push(ms);
}}

async function fetchJsonWithTimeout() {{
  fetchCalls += 1;
  if (!steps.length) {{
    throw new Error("Scenario exhausted before polling completed.");
  }}
  const step = steps.shift();
  if (step.type === "throw") {{
    const error = new Error(step.message);
    if (step.name) {{
      error.name = step.name;
    }}
    throw error;
  }}
  return {{
    response: {{
      ok: step.response_ok !== false,
      status: step.status_code || 200,
      headers: {{
        get(name) {{
          return name === "content-type" ? "application/json" : "";
        }},
      }},
    }},
    data: step.data ?? null,
    text: step.text || "",
  }};
}}

Date.now = () => {{
  nowTick += 1000;
  return 1_000_000 + nowTick;
}};

for (const source of functionSources) {{
  vm.runInThisContext(source);
}}

(async () => {{
  try {{
    const result = await pollConversionJob("job-123", "PDF");
    process.stdout.write(JSON.stringify({{
      ok: true,
      result,
      statusLog,
      delayLog,
      fetchCalls,
    }}));
  }} catch (error) {{
    process.stdout.write(JSON.stringify({{
      ok: false,
      error: error && error.message ? error.message : String(error),
      statusLog,
      delayLog,
      fetchCalls,
    }}));
  }}
}})();
"""
        completed = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(f"Node harness nie uruchomił się poprawnie:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_fetch_reject_retries_with_backoff_and_finishes_ready(self) -> None:
        payload = self._run_polling_scenario(
            [
                {"type": "throw", "message": "Failed to fetch"},
                {
                    "type": "response",
                    "data": {
                        "success": True,
                        "status": "ready",
                        "message": "EPUB gotowy do pobrania.",
                        "download_url": "/convert/download/job-123",
                        "conversion": {"profile": "book_reflow"},
                        "poll_after_ms": 0,
                    },
                },
            ]
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "ready")
        self.assertEqual(payload["fetchCalls"], 2)
        self.assertEqual(payload["delayLog"][0], 1500)
        self.assertGreater(payload["delayLog"][1], payload["delayLog"][0])
        self.assertTrue(
            any("Ponawiam probe (1/3)" in entry["message"] for entry in payload["statusLog"]),
            payload["statusLog"],
        )

    def test_timeout_error_is_treated_as_transient_and_can_recover(self) -> None:
        payload = self._run_polling_scenario(
            [
                {"type": "throw", "message": "Przekroczono limit czasu odpowiedzi lokalnego serwera."},
                {
                    "type": "response",
                    "data": {
                        "success": True,
                        "status": "ready",
                        "message": "EPUB gotowy do pobrania.",
                        "download_url": "/convert/download/job-123",
                        "conversion": {"profile": "book_reflow"},
                        "poll_after_ms": 0,
                    },
                },
            ]
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "ready")
        self.assertEqual(payload["fetchCalls"], 2)
        self.assertTrue(
            any("chwilowo nie odpowiada" in entry["message"] for entry in payload["statusLog"]),
            payload["statusLog"],
        )

    def test_failed_status_surfaces_backend_error_without_retry_loop(self) -> None:
        payload = self._run_polling_scenario(
            [
                {
                    "type": "response",
                    "data": {
                        "success": True,
                        "status": "failed",
                        "message": "Konwersja nie powiodla sie.",
                        "error": "backend timeout",
                        "poll_after_ms": 0,
                    },
                }
            ]
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "backend timeout")
        self.assertEqual(payload["fetchCalls"], 1)
        self.assertFalse(
            any("Ponawiam probe" in entry["message"] for entry in payload["statusLog"]),
            payload["statusLog"],
        )

    def test_repeated_transient_failures_exhaust_retry_budget(self) -> None:
        payload = self._run_polling_scenario(
            [
                {"type": "throw", "message": "Failed to fetch"},
                {"type": "throw", "message": "Failed to fetch"},
                {"type": "throw", "message": "Failed to fetch"},
            ]
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"],
            "Polaczenie z lokalnym serwerem konwersji zostalo przerwane. Sprobuj ponownie za chwile.",
        )
        self.assertEqual(payload["fetchCalls"], 3)
        self.assertEqual(payload["delayLog"][0], 1500)
        self.assertGreater(payload["delayLog"][1], payload["delayLog"][0])
        self.assertGreaterEqual(payload["delayLog"][2], payload["delayLog"][1])
        retry_messages = [entry["message"] for entry in payload["statusLog"] if "Ponawiam probe" in entry["message"]]
        self.assertEqual(len(retry_messages), 2, retry_messages)

    def test_tracking_prevention_noise_is_classified_without_hiding_real_errors(self) -> None:
        self.assertTrue(_is_privacy_noise_message("Tracking Prevention blocked access to storage for this page."))
        self.assertTrue(_is_privacy_noise_message("Permission denied to access property \"localStorage\"."))
        self.assertFalse(_is_privacy_noise_message("Konwersja nie powiodla sie: backend timeout"))
        self.assertFalse(_is_privacy_noise_message("Failed to fetch"))


if __name__ == "__main__":
    unittest.main()
