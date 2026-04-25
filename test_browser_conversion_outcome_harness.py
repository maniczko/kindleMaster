from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"


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


@unittest.skipUnless(shutil.which("node"), "Node.js jest wymagany do harnessu frontendowego.")
class BrowserConversionOutcomeHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        template_html = TEMPLATE_PATH.read_text(encoding="utf-8")
        cls.function_sources = [
            _extract_function_source(template_html, "coerceFiniteNumber"),
            _extract_function_source(template_html, "normalizeQualityHealth"),
            _extract_function_source(template_html, "deriveQualityVerdict"),
            _extract_function_source(template_html, "normalizePostConversionPayload"),
            _extract_function_source(template_html, "formatBytes"),
            _extract_function_source(template_html, "applyConversionOutcome"),
        ]

    def _run_apply_conversion_outcome(self, job_payload: dict, *, selected_source_type: str = "pdf") -> dict:
        node_script = f"""
const vm = require("node:vm");
const functionSources = {json.dumps(self.function_sources, ensure_ascii=False)};
const jobPayload = {json.dumps(job_payload, ensure_ascii=False)};
let selectedSourceType = {json.dumps(selected_source_type, ensure_ascii=False)};
const renderedReports = [];
const statusLog = [];
const recentConversions = [];

function renderConversionReport(payload) {{
  renderedReports.push(payload);
}}

function rememberRecentConversion(payload) {{
  recentConversions.push(payload);
}}

function setStatus(message, level) {{
  statusLog.push({{ message, level }});
}}

for (const source of functionSources) {{
  vm.runInThisContext(source);
}}

applyConversionOutcome(jobPayload, "sample.pdf");
process.stdout.write(JSON.stringify({{
  renderedReport: renderedReports[0] || null,
  recentConversion: recentConversions[0] || null,
  statusLog,
}}));
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

    def test_apply_conversion_outcome_uses_conversion_payload_as_current_fallback_contract(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "conversion": {
                    "source_type": "pdf",
                    "profile": "diagram_book_reflow",
                    "strategy": "image-first-reflow",
                    "validation": "passed_with_warnings",
                    "validation_tool": "epubcheck",
                    "sections": 18,
                    "assets": 224,
                    "layout": "reflowable",
                    "warnings": 1,
                    "warning_list": ["Dense image book is near the warn threshold."],
                    "high_risk_pages": 0,
                    "high_risk_sections": 0,
                    "output_size_bytes": 9216,
                    "heading_repair": {
                        "status": "skipped",
                        "release": "skipped",
                        "toc_before": 0,
                        "toc_after": 0,
                        "removed": 0,
                        "review": 0,
                        "epubcheck": "skipped",
                        "error": "Skipped for diagram-heavy training book to avoid noisy TOC churn.",
                    },
                },
            }
        )

        rendered = payload["renderedReport"]
        self.assertEqual(rendered["profile"], "diagram_book_reflow")
        self.assertEqual(rendered["assets"], 224)
        self.assertEqual(rendered["headingRepair"]["status"], "skipped")
        self.assertEqual(rendered["verdict"]["key"], "ready_with_review")
        self.assertEqual(rendered["qualityStateUrl"], "")
        self.assertEqual(rendered["metadataHealth"]["status"], "not_reported")
        self.assertEqual(payload["recentConversion"]["verdict"], "Ready with review")
        self.assertIn(
            "diagram-heavy training book",
            payload["statusLog"][-1]["message"],
        )

    def test_apply_conversion_outcome_prefers_quality_state_over_stale_conversion_payload(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "source_type": "pdf",
                "output_size_bytes": 9216,
                "conversion": {
                    "source_type": "pdf",
                    "profile": "legacy-book-reflow",
                    "strategy": "legacy-strategy",
                    "validation": "passed",
                    "validation_tool": "legacy-tool",
                    "sections": 2,
                    "assets": 3,
                    "layout": "reflowable",
                    "warnings": 0,
                    "warning_list": [],
                    "high_risk_pages": 0,
                    "high_risk_sections": 0,
                    "heading_repair": {
                        "status": "applied",
                        "release": "pass",
                        "toc_before": 1,
                        "toc_after": 2,
                        "removed": 0,
                        "review": 0,
                        "epubcheck": "passed",
                        "error": "",
                    },
                },
                "quality_state": {
                    "source_type": "pdf",
                    "overall_severity": "warning",
                    "quality_available": True,
                    "download_url": "/convert/download/job-quality",
                    "summary": {
                        "profile": "diagram_book_reflow",
                        "strategy": "image-first-reflow",
                        "sections": 18,
                        "assets": 224,
                        "layout": "reflowable",
                        "output_size_bytes": 9216,
                    },
                    "validation": {
                        "status": "passed_with_warnings",
                        "tool": "epubcheck",
                    },
                    "audit": {
                        "warning_count": 1,
                        "warnings": ["Dense image book is near the warn threshold."],
                        "high_risk_pages": 0,
                        "high_risk_page_list": [],
                        "high_risk_sections": 0,
                        "high_risk_section_list": [],
                    },
                    "size_budget": {
                        "status": "passed_with_warnings",
                        "message": "Diagram-heavy output is near the warn threshold.",
                    },
                    "render_budget": {
                        "budget_class": "fixed_layout_balanced",
                        "attempt": "primary",
                        "target_warn_bytes": 8192,
                        "target_hard_bytes": 12288,
                    },
                    "heading_repair": {
                        "status": "skipped",
                        "release": "skipped",
                        "toc_before": 0,
                        "toc_after": 0,
                        "removed": 0,
                        "review": 0,
                        "epubcheck": "skipped",
                        "error": "Skipped for diagram-heavy training book to avoid noisy TOC churn.",
                    },
                    "alerts": [
                        {"code": "size_budget_warning", "level": "warning", "message": "warn"},
                    ],
                    "metadata_health": {"status": "passed", "message": "metadata normalized"},
                    "link_health": {"status": "passed", "broken_count": 0},
                    "visible_junk": {"status": "passed", "count": 0},
                },
                "quality_state_url": "/convert/quality/job-quality",
            }
        )

        rendered = payload["renderedReport"]
        self.assertEqual(rendered["profile"], "diagram_book_reflow")
        self.assertEqual(rendered["severity"], "warning")
        self.assertEqual(rendered["validation"], "passed_with_warnings")
        self.assertEqual(rendered["validationTool"], "epubcheck")
        self.assertEqual(rendered["assets"], 224)
        self.assertEqual(rendered["renderBudget"]["budgetClass"], "fixed_layout_balanced")
        self.assertEqual(rendered["sizeBudget"]["status"], "passed_with_warnings")
        self.assertEqual(rendered["headingRepair"]["status"], "skipped")
        self.assertEqual(rendered["verdict"]["key"], "ready_with_review")
        self.assertEqual(rendered["qualityStateUrl"], "/convert/quality/job-quality")
        self.assertEqual(rendered["downloadUrl"], "/convert/download/job-quality")
        self.assertEqual(rendered["metadataHealth"]["status"], "passed")
        self.assertEqual(rendered["linkHealth"]["status"], "passed")
        self.assertEqual(rendered["visibleJunk"]["status"], "passed")
        self.assertEqual(payload["recentConversion"]["profile"], "diagram_book_reflow")
        self.assertIn("diagram-heavy training book", payload["statusLog"][-1]["message"])

    def test_apply_conversion_outcome_marks_failed_quality_gate_from_validation_failure(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "source_type": "pdf",
                "conversion": {
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "strategy": "text_reflowable",
                    "validation": "failed",
                    "validation_tool": "epubcheck",
                    "sections": 8,
                    "assets": 1,
                    "layout": "reflowable",
                    "warnings": 0,
                    "warning_list": [],
                    "high_risk_pages": 0,
                    "high_risk_sections": 0,
                    "output_size_bytes": 8192,
                    "heading_repair": {"status": "skipped"},
                },
            }
        )

        rendered = payload["renderedReport"]
        self.assertEqual(rendered["verdict"]["key"], "failed_quality_gate")
        self.assertEqual(rendered["verdict"]["tone"], "failed")
        self.assertEqual(payload["recentConversion"]["verdict"], "Failed quality gate")


if __name__ == "__main__":
    unittest.main()
