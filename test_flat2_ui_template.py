from __future__ import annotations

import unittest
from pathlib import Path


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"
STATIC_PATH = Path(__file__).with_name("static")
FRONTEND_ASSET_PATHS = (
    STATIC_PATH / "css" / "app-shell.css",
    STATIC_PATH / "js" / "conversion-ui.js",
    STATIC_PATH / "js" / "quality-cockpit.js",
    STATIC_PATH / "js" / "library.js",
)


def frontend_source() -> str:
    return "\n".join(
        [TEMPLATE_PATH.read_text(encoding="utf-8")]
        + [path.read_text(encoding="utf-8") for path in FRONTEND_ASSET_PATHS]
    )


class Flat2UiTemplateTests(unittest.TestCase):
    def test_root_prefers_react_shell_and_legacy_renders_flat_shell_hooks(self) -> None:
        client = app.test_client()

        root_response = client.get("/")
        legacy_response = client.get("/legacy")

        self.assertEqual(root_response.status_code, 200)
        root_html = root_response.get_data(as_text=True)
        if 'id="root"' in root_html:
            self.assertIn("KindleMaster Pipeline", root_html)
        else:
            self.assertIn("Lokalny panel EPUB", root_html)

        self.assertEqual(legacy_response.status_code, 200)
        html = legacy_response.get_data(as_text=True)
        self.assertIn('class="flat-sidebar-card"', html)
        self.assertIn('data-vr-hook="km-ui3-commandbar"', html)
        self.assertIn('id="quickUploadButton"', html)
        self.assertIn('id="topbarSearchInput"', html)
        self.assertIn('id="recentProjectSelect"', html)
        self.assertIn('id="newConversionButton"', html)
        self.assertIn('id="recentConversionsList"', html)
        self.assertIn("filename='css/app-shell.css'", html)
        self.assertIn("filename='js/conversion-ui.js'", html)
        self.assertIn("filename='js/quality-cockpit.js'", html)
        self.assertIn("filename='js/library.js'", html)
        frontend = frontend_source()
        self.assertIn('class="flat2-quality-report"', frontend)
        self.assertIn('data-quality-verdict', frontend)
        self.assertIn("favicon.ico", html)
        self.assertIn("site.webmanifest", html)

    def test_template_declares_flat2_visual_contract(self) -> None:
        html = frontend_source()

        self.assertIn("Flat 2.0 visible contract", html)
        self.assertIn("Premium cockpit cleanup", html)
        self.assertIn("--radius: 4px", html)
        self.assertIn("--shadow: none", html)
        self.assertIn("--focus-ring", html)
        self.assertIn(":where(button, input, select, a, .drop-zone):focus-visible", html)
        self.assertIn(".quality-matrix", html)
        self.assertIn("@media (max-width: 980px)", html)
        self.assertIn("grid-template-columns: 232px minmax(0, 1fr);", html)
        self.assertIn("position: static;\n        width: auto;\n        height: auto;", html)
        self.assertIn("JSON jakości", html)
        self.assertIn(".hero-band {\n        display: flex !important;", html)

    def test_vat209_visual_regression_hooks_and_static_counts_are_declared(self) -> None:
        html = frontend_source()

        for hook in (
            'data-vr-hook="vat-209-shell"',
            'data-vr-hook="vat-209-hero"',
            'data-vr-hook="vat-209-dropzone"',
            'data-vr-hook="vat-209-workspace"',
            'data-vr-hook="vat-209-setup-rail"',
            'data-vr-hook="vat-209-preview-stage"',
            'data-vr-hook="vat-209-insights-rail"',
            'data-vr-hook="vat-209-quality-report"',
        ):
            self.assertIn(hook, html)

        self.assertNotIn("<span>1</span></button>", html)
        self.assertNotIn("<span>0</span></button>", html)
        for label in (
            "<span>Dashboard</span>",
            "<span>Biblioteka</span>",
            "<span>Konwersja</span>",
            "<span>Kontrola jakości</span>",
            "<span>Eksport</span>",
        ):
            self.assertIn(label, html)
        self.assertIn('data-app-view="dashboard" aria-current="page"', html)
        self.assertIn('class="pages-panel"', html)
        self.assertIn('id="cardSuggestedFixes"', html)
        self.assertIn('id="cardExportOptions"', html)

    def test_vat209_visible_labels_are_polish_first_and_keyboard_reachable(self) -> None:
        html = frontend_source()

        for label in (
            "Lokalny panel EPUB",
            "Bieżący projekt",
            "Szukaj projektów",
            "Nowa konwersja",
            "Wgraj plik",
            "Widoki",
            "Dashboard",
            "Biblioteka",
            "Konwersja",
            "Struktura i TOC",
            "Kontrola jakości",
            "Eksport",
            "Ostatnie konwersje",
            "Brak konwersji",
            "Przycięcie A4",
            "Jakość EPUB",
            "Kolejka kontroli ręcznej",
            "Kompletność jakości",
            "JSON jakości",
        ):
            self.assertIn(label, html)

        for removed_label in (
            "Kindle-ready workspace",
            "Production flow",
            "Quick Upload",
            "Folders",
            "Recent conversions",
            "No conversions yet",
            "Advanced crop",
            "EPUB quality",
            "Manual review queue",
            "Quality completeness",
        ):
            self.assertNotIn(removed_label, html)

        self.assertIn('role="button" tabindex="0" aria-describedby="dropZoneHint"', html)
        self.assertIn('dropZone.addEventListener("keydown"', html)
        self.assertIn('e.key !== "Enter" && e.key !== " "', html)

    def test_conversion_setup_panel_explains_profiles_without_backend_drift(self) -> None:
        html = frontend_source()

        self.assertIn('id="profileHint"', html)
        self.assertIn("const PROFILE_DESCRIPTIONS = {", html)
        for profile in (
            '"auto-premium"',
            '"book"',
            '"magazine"',
            '"technical-study"',
            '"preserve-layout"',
        ):
            self.assertIn(profile, html)
        self.assertIn("profileHint.textContent = PROFILE_DESCRIPTIONS[profileSelect.value]", html)
        self.assertIn("docs/conversion-profiles.md", Path("README.md").read_text(encoding="utf-8"))

    def test_document_workspace_declares_modes_and_result_summary(self) -> None:
        html = frontend_source()

        self.assertIn('class="workspace-mode-bar"', html)
        self.assertIn('data-workspace-mode-button="preview"', html)
        self.assertIn('data-workspace-mode-button="crop"', html)
        self.assertIn('data-workspace-mode-button="result"', html)
        self.assertIn('id="workspaceGuidance"', html)
        self.assertIn('id="workspaceResultSummary"', html)
        self.assertIn("function setWorkspaceMode(mode)", html)
        self.assertIn("setWorkspaceMode(\"result\")", html)
        self.assertIn("workspaceResultSummary.textContent", html)

    def test_send_to_kindle_handoff_is_documented_and_visible(self) -> None:
        html = frontend_source()
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("Send-to-Kindle SMTP", html)
        self.assertIn("Send to Kindle", html)
        self.assertIn("docs/send-to-kindle-handoff.md", readme)
        self.assertIn("docs/kindle-previewer-validation.md", readme)
        self.assertIn("kindle_previewer.md", Path("docs/kindle-previewer-validation.md").read_text(encoding="utf-8"))

    def test_project_favicon_assets_are_present(self) -> None:
        for asset_name in (
            "favicon.ico",
            "favicon-16x16.png",
            "favicon-32x32.png",
            "favicon-192x192.png",
            "favicon-512x512.png",
            "site.webmanifest",
        ):
            asset_path = STATIC_PATH / asset_name
            self.assertTrue(asset_path.exists(), f"Missing favicon asset: {asset_path}")
            self.assertGreater(asset_path.stat().st_size, 0, f"Empty favicon asset: {asset_path}")

    def test_favicon_route_serves_project_icon(self) -> None:
        response = app.test_client().get("/favicon.ico")

        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/x-icon")
            self.assertGreater(len(response.data), 0)
        finally:
            response.close()

    def test_async_conversion_polling_does_not_abort_active_long_running_jobs(self) -> None:
        html = frontend_source()

        self.assertIn("LONG_CONVERSION_NOTICE_MS", html)
        self.assertIn("Duzy dokument nadal jest przetwarzany", html)
        self.assertNotIn('throw new Error("Konwersja trwa zbyt dlugo. Sprobuj ponownie za chwile.")', html)

    def test_recent_conversions_loads_jobs_and_exposes_ready_actions(self) -> None:
        html = frontend_source()

        self.assertIn("/convert/jobs", html)
        self.assertIn("/convert/library", html)
        self.assertIn("/convert/search", html)
        self.assertGreaterEqual(html.count("loadConversionHistory({ silent: true });"), 2)
        self.assertIn("loadConversionLibrary", html)
        self.assertIn('id="librarySearchInput"', html)
        self.assertIn('id="libraryVerdictFilter"', html)
        self.assertIn('id="librarySearchButton"', html)
        self.assertIn('data-vr-hook="km-library-view"', html)
        self.assertIn('id="libraryResultsList"', html)
        self.assertIn('id="librarySummary"', html)
        self.assertIn("function renderLibraryItem", html)
        self.assertIn("function renderLibraryResults", html)
        self.assertIn("function setLibraryViewVisible", html)
        self.assertIn('setLibraryViewVisible(target === "library")', html)
        self.assertIn("renderRecentConversionItem", html)
        self.assertIn('const evidenceActions = ["ready", "failed", "blocked", "interrupted"].includes(status)', html)
        self.assertIn("recent-conversion-actions", html)
        self.assertIn("Pobierz EPUB", html)
        self.assertIn("JSON jakości", html)
        self.assertIn("Raport MD", html)
        self.assertIn("Raport JSON", html)
        self.assertIn("payload.error || payload.message", html)
        for field_name in (
            "job_id",
            "status",
            "message",
            "filename",
            "source_type",
            "elapsed_seconds",
            "download_url",
            "quality_state_url",
            "report_json_url",
            "report_markdown_url",
            "release_verdict",
            "reading_verdict",
            "text_excerpt",
            "output_size_bytes",
            "error",
            "error_code",
        ):
            self.assertIn(field_name, html)

    def test_pdf_preview_uses_read_frequent_canvas_contexts(self) -> None:
        html = frontend_source()

        self.assertIn('pdfCanvas.getContext("2d", { alpha: false, willReadFrequently: true })', html)
        self.assertIn("pdfRenderCanvasFactory", html)
        self.assertIn('canvas.getContext("2d", { willReadFrequently: true })', html)
        self.assertIn("currentPage.render({ canvasContext, viewport, canvasFactory: pdfRenderCanvasFactory })", html)

    def test_async_conversion_restart_failure_is_user_friendly(self) -> None:
        html = frontend_source()

        self.assertIn("function isApplicationRestartConversionError(error)", html)
        self.assertIn('failure.code = data.error_code || "";', html)
        self.assertIn("Lokalna aplikacja zostala zrestartowana w trakcie pracy", html)
        self.assertIn("!interruptedByRestart", html)

    def test_quality_cockpit_declares_readonly_panels(self) -> None:
        html = frontend_source()

        for hook in (
            'id="qualityVerdictHeader"',
            'id="qualityCockpit"',
            'id="qualityIssueBoard"',
            'id="qualityReportsActionsPanel"',
            'class="quality-cockpit-panel"',
            'class="quality-issue-board"',
            'data-readonly="true"',
        ):
            self.assertIn(hook, html)
        for panel_id in (
            "qualityManualReviewQueuePanel",
            "qualityMatrixPanel",
            "qualityCompletenessPanel",
            "qualityEpubcheckPanel",
            "qualityTocPreviewPanel",
            "qualityMetadataPanel",
            "qualityAssetsPanel",
            "qualityKindleDeliveryPanel",
        ):
            self.assertTrue(
                f'id="{panel_id}"' in html or f'id: "{panel_id}"' in html,
                panel_id,
            )

        self.assertIn("Raporty / akcje", html)
        self.assertIn("Tylko odczyt", html)
        self.assertIn("function renderQualityDisclosurePanel", html)
        self.assertIn("<details class=\"quality-cockpit-panel\"", html)
        self.assertIn("<summary class=\"quality-panel-title\">", html)
        self.assertIn("open: true", html)
        self.assertIn("Kolejka kontroli ręcznej", html)
        self.assertIn("Kompletność jakości", html)
        self.assertIn("Kompletność", html)
        self.assertIn("Pobierz EPUB", html)
        self.assertIn("JSON jakości", html)
        self.assertIn("EPUB wygenerowany, ale wymaga kontroli jakości", html)
        self.assertIn("Wymaga naprawy przed publikacją", html)
        self.assertIn('id="qualityDecisionStrip"', html)
        self.assertIn("Kindle / mail", html)
        self.assertIn("Można wysłać na Kindle", html)
        self.assertIn("Draft do kontroli", html)
        self.assertIn('aria-label="Decyzja publikacji"', html)
        self.assertIn('data-decision="${item.key}"', html)
        self.assertNotIn("repair-button", html)

    def test_status_and_conversion_regions_are_announced_accessibly(self) -> None:
        html = frontend_source()

        self.assertIn('id="statusBox" role="status" aria-live="polite" aria-atomic="true"', html)
        self.assertIn('id="conversionBox" role="region" aria-live="polite" aria-atomic="true"', html)

    def test_collapsible_headers_are_keyboard_accessible_buttons(self) -> None:
        html = frontend_source()

        self.assertNotIn('<div class="card-header" onclick=', html)
        self.assertIn("function syncCardHeaderState(card)", html)
        self.assertIn('header.setAttribute("aria-expanded"', html)
        for card_id in (
            "cardEpub",
            "cardCrop",
            "cardNav",
            "cardZoom",
            "cardDoc",
            "cardInsights",
            "cardPreview",
        ):
            self.assertIn(f'<button class="card-header" type="button" id="{card_id}Header"', html)
            self.assertIn(f'aria-controls="{card_id}Body"', html)
            self.assertIn(f'id="{card_id}Body"', html)

    def test_quality_cockpit_declares_single_release_decision_labels(self) -> None:
        html = frontend_source()

        self.assertIn('label: "Publikuj"', html)
        self.assertIn('label: "Kontrola"', html)
        self.assertIn('label: "Nie publikuj"', html)
        self.assertIn('{ key: "ready", label: "Publikuj" }', html)
        self.assertIn('{ key: "review", label: "Kontrola" }', html)
        self.assertIn('{ key: "blocked", label: "Nie publikuj" }', html)
        self.assertIn('aria-label="Status walidacji">Walidacja:', html)
        self.assertNotIn('label: "Ready"', html)
        self.assertNotIn('label: "Ready with review"', html)
        self.assertNotIn('label: "Failed quality gate"', html)

    def test_quality_cockpit_consumes_expanded_optional_quality_state_fields(self) -> None:
        html = frontend_source()

        for field_name in (
            "issue_groups",
            "content_metrics",
            "text_cleanup",
            "reference_cleanup",
            "asset_summary",
            "toc_preview",
            "epubcheck_detail",
            "metadata_summary",
            "metadata_health",
            "link_health",
            "visible_junk",
            "download_available",
            "reading_verdict",
            "release_verdict",
            "release_blocked",
            "quality_blockers",
            "send_to_kindle_ready",
            "send_to_kindle_blockers",
            "quality_completeness",
            "user_facing_verdict",
            "user_facing_reasons",
        ):
            self.assertIn(field_name, html)

        for normalized_name in (
            "issueGroups",
            "contentMetrics",
            "textCleanup",
            "referenceCleanup",
            "assetSummary",
            "magazineQualityPreview",
            "tocPreview",
            "epubcheckDetail",
            "metadataSummary",
            "metadataHealth",
            "linkHealth",
            "visibleJunk",
            "downloadAvailable",
            "readingVerdict",
            "releaseVerdict",
            "releaseBlocked",
            "qualityBlockers",
            "sendToKindleReady",
            "sendToKindleBlockers",
            "qualityCompleteness",
            "userFacingVerdict",
            "userFacingReasons",
        ):
            self.assertIn(normalized_name, html)

    def test_quality_cockpit_renders_user_facing_verdict_and_top_reasons(self) -> None:
        html = frontend_source()

        self.assertIn("userFacingVerdict", html)
        self.assertIn("userFacingReasons", html)
        self.assertIn("const safeUserFacingReasons = normalizeOptionalArray(userFacingReasons);", html)
        self.assertIn("const safeUserFacingVerdict = normalizeOptionalObject(userFacingVerdict);", html)
        self.assertIn("const userFacingLabel = safeUserFacingVerdict && safeUserFacingVerdict.label", html)
        self.assertIn("const userFacingDetail = safeUserFacingVerdict && safeUserFacingVerdict.detail", html)
        self.assertIn("qualityUserFacingReasonsPanel", html)
        self.assertIn("Najważniejsze powody", html)
        self.assertIn("${escapeHtml(userFacingLabel)}", html)
        self.assertIn("${escapeHtml(userFacingDetail)}", html)
        self.assertIn('renderCompactList(safeUserFacingReasons, "Brak danych", 5)', html)
        self.assertIn("user_facing_verdict", html)
        self.assertIn("user_facing_reasons", html)
        self.assertIn("Top 5 reasons/blockers", html)
        self.assertIn("renderMagazineProblemSamples", html)
        self.assertIn("qualityMagazinePreviewPanel", html)

    def test_conversion_polling_understands_progress_heartbeat_states(self) -> None:
        html = frontend_source()

        self.assertIn("function renderConversionProgressMessage", html)
        self.assertIn("Pracuje długo, ale serwer odpowiada", html)
        self.assertIn("Brak świeżego heartbeat, sprawdzam dalej", html)
        self.assertIn('data.status === "failed" || data.status === "timed_out"', html)

    def test_quality_cockpit_uses_polish_missing_data_fallback_for_missing_fields(self) -> None:
        html = frontend_source()

        self.assertIn("function notReported(value)", html)
        self.assertIn("Brak danych", html)
        self.assertIn("function formatStatusText(value)", html)
        self.assertIn("function normalizeQualityCompleteness(rawValue)", html)
        self.assertIn("function renderManualReviewQueue(queue)", html)
        self.assertIn('renderCompactList(tocItems, "Brak danych", 8)', html)
        self.assertIn('renderCompactList(completenessSectionItems, "Brak danych", 9)', html)
        self.assertIn('renderCompactList(items, "Brak danych", 5)', html)
        self.assertIn('safeAssetSummary ? (safeAssetSummary.status || "Zaraportowano") : "Brak danych"', html)

    def test_quality_cockpit_actions_remain_links_not_mutating_buttons(self) -> None:
        html = frontend_source()
        reports_panel_start = html.index('id="qualityReportsActionsPanel"')
        reports_panel = html[reports_panel_start : reports_panel_start + 900]

        self.assertIn('qualityStateUrl ? `<a href="${escapeHtml(qualityStateUrl)}"', html)
        self.assertIn('downloadUrl ? `<a href="${escapeHtml(downloadUrl)}"', html)
        self.assertIn('class="quality-download-action"', html)
        self.assertIn("Pobierz szkic EPUB", html)
        self.assertIn("const downloadLabel = safeUserFacingVerdict && safeUserFacingVerdict.download_label", html)
        self.assertIn('releaseBlocked || reportVerdict.key === "release_blocked"', html)
        self.assertIn('data-readonly="true"', reports_panel)
        self.assertNotIn("<button", reports_panel)
        self.assertNotIn("fetch(", reports_panel)

    def test_recent_conversion_evidence_links_render_for_non_ready_terminal_states(self) -> None:
        html = frontend_source()

        self.assertIn('["blocked", "release_blocked", "quality_blocked"].includes(value)', html)
        self.assertIn('["interrupted", "aborted", "application_restart", "restart"].includes(value)', html)
        self.assertIn('["ready", "failed", "blocked", "interrupted"].includes(status)', html)
        self.assertIn('class="recent-conversion-evidence"', html)
        self.assertIn("Zadanie:", html)
        self.assertIn("Pobierz szkic EPUB", html)


if __name__ == "__main__":
    unittest.main()
