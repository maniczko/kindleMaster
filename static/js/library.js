    function renderRecentConversions() {
      if (!recentConversionsList) return;
      if (!recentConversions.length) {
        recentConversionsList.innerHTML = `
          <li id="recentConversionsEmpty">
            <b>Brak konwersji</b>
            <small>Historia pojawi sie po pierwszej konwersji.</small>
          </li>
        `;
        return;
      }
      recentConversionsList.innerHTML = recentConversions.map(renderRecentConversionItem).join("");
    }

    function normalizeRecentConversionStatus(status) {
      const value = String(status || "").toLowerCase();
      if (["ready", "complete", "completed", "done", "success", "succeeded"].includes(value)) return "ready";
      if (["running", "processing", "started", "in_progress"].includes(value)) return "running";
      if (["queued", "pending", "waiting"].includes(value)) return "queued";
      if (["blocked", "release_blocked", "quality_blocked"].includes(value)) return "blocked";
      if (["interrupted", "aborted", "application_restart", "restart"].includes(value)) return "interrupted";
      if (["failed", "error", "cancelled", "canceled"].includes(value)) return "failed";
      return value || "unknown";
    }

    function formatRecentConversionStatus(status) {
      const normalized = normalizeRecentConversionStatus(status);
      if (normalized === "ready") return "Gotowe";
      if (normalized === "running") return "W toku";
      if (normalized === "queued") return "W kolejce";
      if (normalized === "blocked") return "Zablokowane";
      if (normalized === "interrupted") return "Przerwane";
      if (normalized === "failed") return "Błąd";
      return "Nieznany";
    }

    function formatRecentConversionElapsed(seconds) {
      const elapsedSeconds = coerceFiniteNumber(seconds);
      if (!Number.isFinite(elapsedSeconds) || elapsedSeconds < 0) return "";
      if (elapsedSeconds < 60) return `${Math.round(elapsedSeconds)}s`;
      const minutes = Math.floor(elapsedSeconds / 60);
      const remainder = Math.round(elapsedSeconds % 60);
      return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
    }

    function normalizeRecentConversion(item) {
      const payload = item && typeof item === "object" ? item : {};
      const downloadUrl = payload.download_url || payload.downloadUrl || "";
      const pdfLayoutPreviewUrl = payload.pdf_layout_preview_url
        || payload.pdfLayoutPreviewUrl
        || (window.KindleMasterArtifactLinks
          ? window.KindleMasterArtifactLinks.artifactShellUrl(payload, "pdf_layout_preview")
          : "");
      const qualityStateUrl = payload.quality_state_url || payload.qualityStateUrl || "";
      const status = normalizeRecentConversionStatus(
        payload.status || (downloadUrl || qualityStateUrl || payload.verdict ? "ready" : ""),
      );
      return {
        jobId: payload.job_id || payload.jobId || "",
        status,
        message: payload.message || "",
        title: payload.title || "",
        filename: payload.filename || "Bez nazwy",
        sourceType: payload.source_type || payload.sourceType || "",
        documentClass: payload.document_class || payload.documentClass || "",
        elapsedSeconds: coerceFiniteNumber(payload.elapsed_seconds ?? payload.elapsedSeconds),
        downloadUrl,
        pdfLayoutPreviewUrl,
        qualityStateUrl,
        reportJsonUrl: payload.report_json_url || payload.reportJsonUrl || "",
        reportMarkdownUrl: payload.report_markdown_url || payload.reportMarkdownUrl || "",
        outputSizeBytes: coerceFiniteNumber(payload.output_size_bytes ?? payload.outputSizeBytes),
        error: payload.error || "",
        verdict: payload.release_verdict || payload.releaseVerdict || payload.verdict || "",
        readingVerdict: payload.reading_verdict || payload.readingVerdict || "",
        releaseBlocked: Boolean(payload.release_blocked || payload.releaseBlocked),
        searchableTextAvailable: Boolean(payload.searchable_text_available || payload.searchableTextAvailable),
        textExcerpt: payload.text_excerpt || payload.textExcerpt || "",
        profile: payload.profile || "",
        validation: payload.validation || "",
      };
    }

    function renderRecentConversionItem(item) {
      const payload = normalizeRecentConversion(item);
      const status = normalizeRecentConversionStatus(payload.status);
      const sourceType = payload.sourceType ? payload.sourceType.toUpperCase() : "";
      const elapsed = formatRecentConversionElapsed(payload.elapsedSeconds);
      const outputSize = Number.isFinite(payload.outputSizeBytes) ? formatBytes(payload.outputSizeBytes) : "";
      const statusLine = [
        formatRecentConversionStatus(status),
        sourceType,
        payload.verdict,
        elapsed,
        outputSize,
      ].filter(Boolean).join(" | ");
      const detail = status === "failed"
        ? (payload.error || payload.message || "Konwersja nie powiodla sie.")
        : (payload.textExcerpt || payload.message || payload.readingVerdict || "");
      const downloadLabel = payload.releaseBlocked || payload.verdict === "release_blocked"
        ? "Pobierz szkic EPUB"
        : "Pobierz EPUB";
      const evidenceActions = ["ready", "failed", "blocked", "interrupted"].includes(status);
      const actions = evidenceActions ? [
        payload.downloadUrl ? `<a href="${escapeHtml(payload.downloadUrl)}">${downloadLabel}</a>` : "",
        payload.pdfLayoutPreviewUrl ? `<a href="${escapeHtml(payload.pdfLayoutPreviewUrl)}" target="_blank" rel="noreferrer">Podglad PDF</a>` : "",
        payload.qualityStateUrl ? `<a href="${escapeHtml(payload.qualityStateUrl)}" target="_blank" rel="noreferrer">JSON jakości</a>` : "",
        payload.reportMarkdownUrl ? `<a href="${escapeHtml(payload.reportMarkdownUrl)}" target="_blank" rel="noreferrer">Raport MD</a>` : "",
        payload.reportJsonUrl ? `<a href="${escapeHtml(payload.reportJsonUrl)}" target="_blank" rel="noreferrer">Raport JSON</a>` : "",
      ].filter(Boolean).join("") : "";
      return `
        <li data-job-status="${escapeHtml(status)}" data-job-id="${escapeHtml(payload.jobId)}">
          <b>${escapeHtml(payload.title || payload.filename || "Bez nazwy")}</b>
          <small>${escapeHtml(statusLine || formatRecentConversionStatus(status))}</small>
          ${payload.jobId && evidenceActions ? `<small class="recent-conversion-evidence">Zadanie: ${escapeHtml(payload.jobId)}</small>` : ""}
          ${detail ? `<small class="recent-conversion-message" data-tone="${status === "failed" ? "failed" : "neutral"}">${escapeHtml(detail)}</small>` : ""}
          ${actions ? `<div class="recent-conversion-actions">${actions}</div>` : ""}
        </li>
      `;
    }

    function formatLibraryVerdictLabel(value, releaseBlocked) {
      const normalized = String(value || "").toLowerCase();
      if (releaseBlocked || normalized === "release_blocked") return "Nie publikuj";
      if (normalized === "release_ready") return "Publikuj";
      if (normalized === "ready_with_review") return "Kontrola";
      if (normalized === "failed") return "Błąd";
      return normalized ? formatStatusText(normalized) : "Brak decyzji";
    }

    function renderLibraryItem(item) {
      const payload = normalizeRecentConversion(item);
      const status = normalizeRecentConversionStatus(payload.status);
      const releaseVerdict = payload.verdict || "unknown";
      const sourceType = payload.sourceType ? payload.sourceType.toUpperCase() : "plik";
      const outputSize = Number.isFinite(payload.outputSizeBytes) ? formatBytes(payload.outputSizeBytes) : "rozmiar nieznany";
      const downloadLabel = payload.releaseBlocked || releaseVerdict === "release_blocked"
        ? "Pobierz szkic EPUB"
        : "Pobierz EPUB";
      const statusDetail = [
        sourceType,
        payload.documentClass,
        payload.profile,
        outputSize,
      ].filter(Boolean).join(" | ");
      const qualityDetail = [
        payload.readingVerdict ? `reading: ${payload.readingVerdict}` : "",
        payload.searchableTextAvailable ? "tekst indeksowany" : "",
      ].filter(Boolean).join(" | ");
      const actions = [
        payload.downloadUrl ? `<a data-primary="true" href="${escapeHtml(payload.downloadUrl)}">${downloadLabel}</a>` : "<span>Brak EPUB</span>",
        payload.pdfLayoutPreviewUrl ? `<a href="${escapeHtml(payload.pdfLayoutPreviewUrl)}" target="_blank" rel="noreferrer">Podglad PDF</a>` : "",
        payload.qualityStateUrl ? `<a href="${escapeHtml(payload.qualityStateUrl)}" target="_blank" rel="noreferrer">Quality JSON</a>` : "",
        payload.reportMarkdownUrl ? `<a href="${escapeHtml(payload.reportMarkdownUrl)}" target="_blank" rel="noreferrer">Raport MD</a>` : "",
        payload.reportJsonUrl ? `<a href="${escapeHtml(payload.reportJsonUrl)}" target="_blank" rel="noreferrer">Raport JSON</a>` : "",
      ].filter(Boolean).join("");
      return `
        <li data-job-id="${escapeHtml(payload.jobId)}" data-job-status="${escapeHtml(status)}" data-release-verdict="${escapeHtml(releaseVerdict)}">
          <div class="library-project-title">
            <strong>${escapeHtml(payload.title || payload.filename || "Bez nazwy")}</strong>
            <small>${escapeHtml(payload.filename || payload.jobId || "lokalny artefakt")}</small>
            ${payload.textExcerpt ? `<small>${escapeHtml(payload.textExcerpt)}</small>` : ""}
          </div>
          <div class="library-status-cell">
            <strong>${escapeHtml(formatRecentConversionStatus(status))}</strong>
            <small>${escapeHtml(statusDetail || "local job store")}</small>
          </div>
          <div class="library-quality-cell">
            <strong>${escapeHtml(formatLibraryVerdictLabel(releaseVerdict, payload.releaseBlocked))}</strong>
            <small>${escapeHtml(qualityDetail || "quality_state dostępny po konwersji")}</small>
          </div>
          <div class="library-actions">${actions}</div>
        </li>
      `;
    }

    function renderLibraryResults(items, meta = {}) {
      if (!libraryResultsList) {
        recentConversions = items.map(normalizeRecentConversion).slice(0, RECENT_CONVERSIONS_LIMIT);
        renderRecentConversions();
        return;
      }
      const normalizedItems = items.map(normalizeRecentConversion).slice(0, RECENT_CONVERSIONS_LIMIT);
      libraryItems = normalizedItems;
      const query = meta.query ? ` dla „${meta.query}”` : "";
      const verdict = meta.verdict ? `, filtr: ${formatLibraryVerdictLabel(meta.verdict, false)}` : "";
      if (librarySummary) {
        librarySummary.textContent = normalizedItems.length
          ? `${normalizedItems.length} wyników${query}${verdict}. Pobranie EPUB nie oznacza zgody na publikację.`
          : `Brak wyników${query}${verdict}.`;
      }
      if (!normalizedItems.length) {
        libraryResultsList.innerHTML = `
          <li class="library-empty-state" id="libraryEmptyState">
            <b>Brak wyników biblioteki</b>
            <small>Zmień filtr, wyszukaj inny tytuł albo wykonaj pierwszą konwersję.</small>
          </li>
        `;
        return;
      }
      libraryResultsList.innerHTML = normalizedItems.map(renderLibraryItem).join("");
    }

    function setLibraryViewVisible(visible) {
      if (!libraryView) return;
      libraryView.hidden = !visible;
    }

    function rememberRecentConversion(item) {
      const payload = normalizeRecentConversion(item);
      const isDifferentJob = (existing) => !payload.jobId || existing.jobId !== payload.jobId;
      recentConversions = [
        payload,
        ...recentConversions.filter(isDifferentJob),
      ].slice(0, RECENT_CONVERSIONS_LIMIT);
      renderRecentConversions();
    }

    async function loadConversionHistory({ silent = false } = {}) {
      try {
        const { response, data, text } = await fetchJsonWithTimeout(
          "/convert/jobs",
          {
            method: "GET",
            cache: "no-store",
          },
        );
        if (!response.ok || !data || !data.success || !Array.isArray(data.jobs)) {
          throw new Error((data && data.error) || text || "Nie udalo sie wczytac historii konwersji.");
        }
        recentConversions = data.jobs
          .map(normalizeRecentConversion)
          .slice(0, RECENT_CONVERSIONS_LIMIT);
        renderRecentConversions();
      } catch (error) {
        if (!silent) {
          console.warn("Nie udalo sie wczytac historii konwersji.", error);
        }
        renderRecentConversions();
      }
    }

    async function loadConversionLibrary({ silent = false } = {}) {
      const query = librarySearchInput ? librarySearchInput.value.trim() : "";
      const verdict = libraryVerdictFilter ? libraryVerdictFilter.value : "";
      const params = new URLSearchParams({ limit: String(RECENT_CONVERSIONS_LIMIT) });
      if (query) {
        params.set("q", query);
        params.set("include_text", "true");
      }
      if (verdict) params.set("release_verdict", verdict);
      const endpoint = query ? "/convert/search" : "/convert/library";
      try {
        const { response, data, text } = await fetchJsonWithTimeout(
          `${endpoint}?${params.toString()}`,
          {
            method: "GET",
            headers: { Accept: "application/json" },
            cache: "no-store",
          },
          CONVERSION_REQUEST_TIMEOUT_MS,
        );
        if (!response.ok || !data || data.success === false || !Array.isArray(data.items)) {
          throw new Error((data && data.error) || text || "Nie udalo sie pobrac archiwum konwersji.");
        }
        renderLibraryResults(data.items, {
          query,
          verdict,
          count: data.count,
        });
      } catch (error) {
        if (!silent) {
          setStatus(error.message || "Nie udalo sie pobrac archiwum konwersji.", "warn");
        }
        renderLibraryResults([], { query, verdict });
      }
    }

