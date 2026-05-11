from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"
STATIC_PATH = Path(__file__).with_name("static")
FRONTEND_ASSET_PATHS = (
    STATIC_PATH / "js" / "conversion-ui.js",
    STATIC_PATH / "js" / "quality-cockpit.js",
    STATIC_PATH / "js" / "library.js",
)


def frontend_source() -> str:
    return "\n".join(
        [TEMPLATE_PATH.read_text(encoding="utf-8")]
        + [path.read_text(encoding="utf-8") for path in FRONTEND_ASSET_PATHS]
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


@unittest.skipUnless(shutil.which("node"), "Node.js jest wymagany do harnessu frontendowego.")
class BrowserConversionOutcomeHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        template_html = frontend_source()
        cls.function_sources = [
            _extract_function_source(template_html, "coerceFiniteNumber"),
            _extract_function_source(template_html, "normalizeQualityHealth"),
            _extract_function_source(template_html, "normalizeQualityCompleteness"),
            _extract_function_source(template_html, "deriveQualityVerdict"),
            _extract_function_source(template_html, "normalizePostConversionPayload"),
            _extract_function_source(template_html, "formatBytes"),
            _extract_function_source(template_html, "applyConversionOutcome"),
        ]
        cls.render_report_sources = [
            _extract_function_source(template_html, "coerceFiniteNumber"),
            _extract_function_source(template_html, "formatBytes"),
            _extract_function_source(template_html, "escapeHtml"),
            _extract_function_source(template_html, "normalizeQualityHealth"),
            _extract_function_source(template_html, "normalizeQualityCompleteness"),
            _extract_function_source(template_html, "formatQualityHealth"),
            _extract_function_source(template_html, "formatCompletenessScore"),
            _extract_function_source(template_html, "formatStatusText"),
            _extract_function_source(template_html, "notReported"),
            _extract_function_source(template_html, "normalizeOptionalObject"),
            _extract_function_source(template_html, "normalizeOptionalArray"),
            _extract_function_source(template_html, "formatMetricValue"),
            _extract_function_source(template_html, "renderQualityRows"),
            _extract_function_source(template_html, "renderCompactList"),
            _extract_function_source(template_html, "describeQualityReason"),
            _extract_function_source(template_html, "buildTopQualityReasons"),
            _extract_function_source(template_html, "renderTopQualityReasons"),
            _extract_function_source(template_html, "formatPremiumScore"),
            _extract_function_source(template_html, "resolveKindleReadyValue"),
            _extract_function_source(template_html, "formatYesNo"),
            _extract_function_source(template_html, "qualityToneFromStatus"),
            _extract_function_source(template_html, "qualityToneFromBoolean"),
            _extract_function_source(template_html, "qualityToneFromPremiumScore"),
            _extract_function_source(template_html, "formatAiVerifierStatus"),
            _extract_function_source(template_html, "renderQualityHeroMetric"),
            _extract_function_source(template_html, "getIssueGroupItems"),
            _extract_function_source(template_html, "summarizeIssueGroup"),
            _extract_function_source(template_html, "buildManualReviewQueue"),
            _extract_function_source(template_html, "renderManualReviewQueue"),
            _extract_function_source(template_html, "renderIssueColumn"),
            _extract_function_source(template_html, "renderQualityDisclosurePanel"),
            _extract_function_source(template_html, "deriveQualityVerdict"),
            _extract_function_source(template_html, "renderAuditList"),
            _extract_function_source(template_html, "renderConversionReport"),
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
            ["node", "-"],
            cwd=Path(__file__).parent,
            input=node_script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            self.fail(f"Node harness nie uruchomił się poprawnie:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def _run_render_conversion_report(self, report_payload: dict) -> dict:
        node_script = f"""
const vm = require("node:vm");
const functionSources = {json.dumps(self.render_report_sources, ensure_ascii=False)};
const reportPayload = {json.dumps(report_payload, ensure_ascii=False)};
globalThis.conversionBox = {{ className: "", innerHTML: "" }};
globalThis.dashboardVerdictMetric = {{ textContent: "" }};

for (const source of functionSources) {{
  vm.runInThisContext(source);
}}

renderConversionReport(reportPayload);
process.stdout.write(JSON.stringify({{
  className: conversionBox.className,
  html: conversionBox.innerHTML,
  dashboardVerdict: dashboardVerdictMetric.textContent,
}}));
"""
        completed = subprocess.run(
            ["node", "-"],
            cwd=Path(__file__).parent,
            input=node_script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            self.fail(f"Node render harness nie uruchomił się poprawnie:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
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
        self.assertEqual(payload["recentConversion"]["verdict"], "Kontrola")
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

    def test_apply_conversion_outcome_normalizes_quality_cockpit_contract_fields(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "job_id": "job-cockpit",
                "source_type": "pdf",
                "quality_state_url": "/convert/quality/job-cockpit",
                "quality_state": {
                    "source_type": "pdf",
                    "overall_severity": "warning",
                    "quality_available": True,
                    "download_url": "/convert/download/job-cockpit",
                    "summary": {
                        "profile": "book_reflow",
                        "strategy": "text_reflowable",
                        "sections": 12,
                        "assets": 7,
                        "layout": "reflowable",
                        "output_size_bytes": 16384,
                    },
                    "validation": {"status": "passed_with_warnings", "tool": "epubcheck"},
                    "issue_groups": {
                        "blockers": [],
                        "warnings": [
                            {
                                "severity": "warning",
                                "code": "metadata_placeholder",
                                "message": "Missing publisher",
                                "source": "metadata_health",
                                "suggested_action": "Replace placeholder metadata.",
                            },
                        ],
                        "review": [],
                    },
                    "epubcheck_detail": {
                        "status": "passed_with_warnings",
                        "tool": "epubcheck",
                        "error_count": 0,
                        "warning_count": 2,
                        "messages": ["OPF metadata warning"],
                    },
                    "toc_preview": {
                        "status": "ready",
                        "total": 4,
                        "entries": [
                            {"label": "Introduction", "href": "chapter1.xhtml"},
                            {"label": "Methods", "href": "chapter2.xhtml"},
                        ],
                    },
                    "asset_summary": {
                        "image_count": 5,
                        "table_count": 2,
                        "figure_count": 3,
                        "oversized_count": 1,
                    },
                    "metadata_summary": {
                        "title": "Quality Cockpit Sample",
                        "author": "KindleMaster QA",
                        "language": "pl",
                    },
                    "metadata_health": {"status": "warning", "count": 1, "message": "Publisher missing"},
                    "link_health": {"status": "passed", "broken_count": 0, "message": "12 links checked"},
                    "visible_junk": {"status": "passed", "count": 0, "message": "No visible junk detected"},
                    "quality_completeness": {
                        "score": 78,
                        "status": "partial",
                        "expected_sections": 9,
                        "reported_sections": 7,
                        "missing_count": 2,
                        "not_reported_count": 2,
                        "missing_sections": ["text_cleanup", "reference_cleanup"],
                        "sections": [
                            {"key": "validation", "label": "Validation", "status": "passed_with_warnings", "reported": True},
                            {"key": "text_cleanup", "label": "Text cleanup", "status": "not_reported", "reported": False},
                        ],
                    },
                    "premium_scoring": {
                        "status": "passed_with_warnings",
                        "premium_score": 8.4,
                        "kindle_ready": True,
                    },
                    "kindle_delivery": {
                        "status": "not_verified",
                        "automated_ready": True,
                    },
                    "ai_verifier": {
                        "status": "passed",
                        "message": "AI verifier accepted the evidence bundle.",
                    },
                },
            }
        )

        rendered = payload["renderedReport"]
        self.assertEqual(rendered["issueGroups"]["warnings"][0]["code"], "metadata_placeholder")
        self.assertEqual(rendered["epubcheckDetail"]["warning_count"], 2)
        self.assertEqual(rendered["tocPreview"]["entries"][0]["label"], "Introduction")
        self.assertEqual(rendered["assetSummary"]["table_count"], 2)
        self.assertEqual(rendered["metadataSummary"]["title"], "Quality Cockpit Sample")
        self.assertEqual(rendered["metadataHealth"]["status"], "warning")
        self.assertEqual(rendered["linkHealth"]["status"], "passed")
        self.assertEqual(rendered["visibleJunk"]["status"], "passed")
        self.assertEqual(rendered["qualityCompleteness"]["score"], 78)
        self.assertEqual(rendered["qualityCompleteness"]["missingCount"], 2)
        self.assertEqual(rendered["qualityCompleteness"]["missingSections"], ["text_cleanup", "reference_cleanup"])
        self.assertEqual(rendered["premiumScoring"]["premium_score"], 8.4)
        self.assertTrue(rendered["premiumScoring"]["kindle_ready"])
        self.assertEqual(rendered["kindleDelivery"]["status"], "not_verified")
        self.assertEqual(rendered["aiVerifier"]["status"], "passed")
        self.assertTrue(rendered["downloadAvailable"])
        self.assertFalse(rendered["releaseBlocked"])

    def test_render_conversion_report_promotes_publication_decision_metrics_and_top_reasons(self) -> None:
        payload = self._run_render_conversion_report(
            {
                "profile": "book_reflow",
                "validation": "passed",
                "validationTool": "epubcheck",
                "sections": 8,
                "assets": 1,
                "layout": "reflowable",
                "outputSizeBytes": 8192,
                "warnings": 1,
                "highRiskPages": 0,
                "highRiskSections": 0,
                "qualityStateUrl": "/convert/quality/job-cockpit",
                "downloadUrl": "/convert/download/job-cockpit",
                "downloadAvailable": True,
                "releaseVerdict": "release_blocked",
                "releaseBlocked": True,
                "qualityBlockers": [
                    {
                        "code": "reference_coverage_failed",
                        "message": "References are incomplete.",
                        "source": "quality_gate",
                    },
                    {
                        "code": "visible_junk_detected",
                        "message": "Visible OCR junk remains.",
                        "source": "visible_junk",
                    },
                ],
                "sendToKindleReady": False,
                "sendToKindleBlockers": [{"code": "kindle_delivery_release_not_ready"}],
                "issueGroups": {
                    "blockers": [],
                    "warnings": [{"code": "toc_review", "message": "TOC requires review."}],
                    "review": [{"code": "manual_review", "message": "Manual spot check required."}],
                },
                "premiumScoring": {
                    "status": "failed",
                    "premium_score": 6.8,
                    "kindle_ready": False,
                },
                "aiVerifier": {
                    "status": "failed",
                    "message": "Verifier found unresolved blockers.",
                },
                "userFacingVerdict": {
                    "label": "Nie publikuj",
                    "message": "Fix blockers before release.",
                },
                "userFacingReasons": [
                    {"code": "reader_visible_blocker", "message": "Reader-visible blocker remains."}
                ],
                "qualityCompleteness": {
                    "status": "complete",
                    "score": 100,
                    "expected_sections": 8,
                    "reported_sections": 8,
                },
            }
        )

        html = payload["html"]
        self.assertIn('id="qualityVerdictHeader"', html)
        self.assertIn("Publikuj", html)
        self.assertIn("Kontrola", html)
        self.assertIn("Nie publikuj", html)
        self.assertIn("Premium score", html)
        self.assertIn("6.8/10", html)
        self.assertIn("Kindle-ready", html)
        self.assertIn(">no<", html)
        self.assertIn("AI verifier", html)
        self.assertIn("Verifier found unresolved blockers.", html)
        self.assertIn("Top 3 reasons/blockers", html)
        self.assertIn("reference_coverage_failed", html)
        self.assertIn("visible_junk_detected", html)
        self.assertIn("reader_visible_blocker", html)
        self.assertNotIn("toc_review", html.split("qualityTopReasons", 1)[1].split("</ol>", 1)[0])
        self.assertIn("JSON jakości", html)
        self.assertIn("Pobierz szkic EPUB do kontroli", html)
        self.assertEqual(payload["dashboardVerdict"], "Nie publikuj")

    def test_apply_conversion_outcome_keeps_download_available_when_release_is_blocked(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "job_id": "job-release-blocked",
                "source_type": "pdf",
                "download_url": "/convert/download/job-release-blocked",
                "quality_state_url": "/convert/quality/job-release-blocked",
                "quality_state": {
                    "source_type": "pdf",
                    "quality_available": True,
                    "download_url": "/convert/download/job-release-blocked",
                    "download_available": True,
                    "reading_verdict": "ready_with_review",
                    "release_blocked": True,
                    "quality_blockers": [
                        {
                            "severity": "blocker",
                            "code": "text_cleanup_blocked",
                            "message": "Text cleanup blocked 1 unsafe change.",
                            "source": "text_cleanup",
                        }
                    ],
                    "overall_severity": "error",
                    "summary": {
                        "profile": "book_reflow",
                        "strategy": "text_reflowable",
                        "sections": 8,
                        "assets": 1,
                        "layout": "reflowable",
                        "output_size_bytes": 8192,
                    },
                    "validation": {"status": "passed", "tool": "epubcheck"},
                    "issue_groups": {
                        "blockers": [
                            {
                                "severity": "blocker",
                                "code": "text_cleanup_blocked",
                                "message": "Text cleanup blocked 1 unsafe change.",
                                "source": "text_cleanup",
                            }
                        ],
                        "warnings": [],
                        "review": [],
                    },
                },
            }
        )

        rendered = payload["renderedReport"]
        self.assertTrue(rendered["downloadAvailable"])
        self.assertTrue(rendered["releaseBlocked"])
        self.assertEqual(rendered["readingVerdict"], "ready_with_review")
        self.assertEqual(rendered["qualityBlockers"][0]["code"], "text_cleanup_blocked")
        self.assertEqual(rendered["verdict"]["key"], "release_blocked")
        self.assertEqual(rendered["verdict"]["tone"], "failed")
        self.assertEqual(rendered["verdict"]["label"], "Nie publikuj")
        self.assertIn("EPUB wygenerowany, ale wymaga naprawy", rendered["verdict"]["detail"])
        self.assertEqual(payload["recentConversion"]["verdict"], "Nie publikuj")
        self.assertEqual(payload["statusLog"][-1]["level"], "error")
        self.assertIn("Wymaga naprawy przed publik", payload["statusLog"][-1]["message"])
        self.assertIn("text_cleanup_blocked", payload["statusLog"][-1]["message"])

    def test_apply_conversion_outcome_infers_release_blocked_from_issue_group_blockers(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "job_id": "job-inferred-blocker",
                "source_type": "pdf",
                "download_url": "/convert/download/job-inferred-blocker",
                "quality_state": {
                    "source_type": "pdf",
                    "quality_available": True,
                    "download_available": True,
                    "download_url": "/convert/download/job-inferred-blocker",
                    "reading_verdict": "ready",
                    "overall_severity": "warning",
                    "summary": {
                        "profile": "book_reflow",
                        "strategy": "text_reflowable",
                        "sections": 8,
                        "assets": 1,
                        "layout": "reflowable",
                        "output_size_bytes": 8192,
                    },
                    "validation": {"status": "passed", "tool": "epubcheck"},
                    "issue_groups": {
                        "blockers": [
                            {
                                "severity": "blocker",
                                "code": "metadata_release_blocker",
                                "message": "Required release metadata is missing.",
                                "source": "metadata_health",
                            }
                        ],
                        "warnings": [],
                        "review": [],
                    },
                },
            }
        )

        rendered = payload["renderedReport"]
        self.assertTrue(rendered["downloadAvailable"])
        self.assertTrue(rendered["releaseBlocked"])
        self.assertEqual(rendered["qualityBlockers"][0]["code"], "metadata_release_blocker")
        self.assertEqual(rendered["verdict"]["key"], "release_blocked")
        self.assertEqual(rendered["verdict"]["label"], "Nie publikuj")
        self.assertEqual(payload["statusLog"][-1]["level"], "error")

    def test_apply_conversion_outcome_prefers_release_verdict_when_present(self) -> None:
        payload = self._run_apply_conversion_outcome(
            {
                "job_id": "job-release-verdict",
                "source_type": "pdf",
                "download_url": "/convert/download/job-release-verdict",
                "quality_state": {
                    "source_type": "pdf",
                    "quality_available": True,
                    "download_url": "/convert/download/job-release-verdict",
                    "download_available": True,
                    "reading_verdict": "ready_with_review",
                    "release_verdict": "release_blocked",
                    "release_blocked": True,
                    "quality_blockers": [
                        {
                            "severity": "blocker",
                            "code": "visible_junk_detected",
                            "message": "Visible OCR junk remains in the output.",
                            "source": "visible_junk",
                        }
                    ],
                    "overall_severity": "error",
                    "summary": {
                        "profile": "book_reflow",
                        "strategy": "text_reflowable",
                        "sections": 8,
                        "assets": 1,
                        "layout": "reflowable",
                        "output_size_bytes": 8192,
                    },
                    "validation": {"status": "passed", "tool": "epubcheck"},
                },
            }
        )

        rendered = payload["renderedReport"]
        self.assertEqual(rendered["releaseVerdict"], "release_blocked")
        self.assertEqual(rendered["verdict"]["key"], "release_blocked")
        self.assertEqual(rendered["verdict"]["tone"], "failed")
        self.assertEqual(rendered["verdict"]["label"], "Nie publikuj")
        self.assertIn("naprawy przed publik", rendered["verdict"]["detail"])

    def test_template_mentions_quality_cockpit_fields_and_not_reported_fallbacks(self) -> None:
        template_html = frontend_source()

        for stable_token in (
            "issueGroups",
            "epubcheckDetail",
            "tocPreview",
            "assetSummary",
            "metadataSummary",
            "metadataHealth",
            "linkHealth",
            "visibleJunk",
            "downloadAvailable",
            "readingVerdict",
            "releaseVerdict",
            "releaseBlocked",
            "qualityBlockers",
            "qualityCompleteness",
            "quality_completeness",
            "premiumScoring",
            "premium_scoring",
            "kindleDelivery",
            "kindle_delivery",
            "aiVerifier",
            "ai_verifier",
            "Premium score",
            "Kindle-ready",
            "AI verifier",
            "Top 3 reasons/blockers",
            "Kolejka kontroli ręcznej",
            "Kompletność",
            "EPUB wygenerowany, ale wymaga kontroli jakości",
            "Wymaga naprawy przed publikacją",
            "Brak danych",
        ):
            self.assertIn(stable_token, template_html)

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
        self.assertEqual(payload["recentConversion"]["verdict"], "Nie publikuj")
        self.assertEqual(payload["statusLog"][-1]["level"], "error")

    def test_recent_conversion_item_exposes_blocked_job_evidence_links(self) -> None:
        template_html = frontend_source()
        function_sources = [
            _extract_function_source(template_html, "coerceFiniteNumber"),
            _extract_function_source(template_html, "formatBytes"),
            _extract_function_source(template_html, "escapeHtml"),
            _extract_function_source(template_html, "normalizeRecentConversionStatus"),
            _extract_function_source(template_html, "formatRecentConversionStatus"),
            _extract_function_source(template_html, "formatRecentConversionElapsed"),
            _extract_function_source(template_html, "normalizeRecentConversion"),
            _extract_function_source(template_html, "renderRecentConversionItem"),
        ]
        node_script = f"""
const vm = require("node:vm");
const functionSources = {json.dumps(function_sources, ensure_ascii=False)};
for (const source of functionSources) {{
  vm.runInThisContext(source);
}}
const html = renderRecentConversionItem({{
  job_id: "job-blocked-42",
  status: "release_blocked",
  filename: "blocked.pdf",
  release_blocked: true,
  release_verdict: "release_blocked",
  message: "Quality gate blocked publication.",
  download_url: "/convert/download/job-blocked-42",
  quality_state_url: "/convert/quality/job-blocked-42",
  report_json_url: "/reports/job-blocked-42.json",
  report_markdown_url: "/reports/job-blocked-42.md",
}});
process.stdout.write(JSON.stringify({{ html }}));
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

        rendered = json.loads(completed.stdout)["html"]
        self.assertIn('data-job-status="blocked"', rendered)
        self.assertIn("Zadanie: job-blocked-42", rendered)
        self.assertIn("Pobierz szkic EPUB do kontroli", rendered)
        self.assertIn("JSON", rendered)
        self.assertIn("Raport MD", rendered)
        self.assertIn("Raport JSON", rendered)


if __name__ == "__main__":
    unittest.main()
