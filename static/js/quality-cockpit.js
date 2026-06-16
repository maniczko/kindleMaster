    function normalizeQualityHealth(rawValue, fallbackLabel) {
      const payload = rawValue && typeof rawValue === "object" ? rawValue : {};
      const status = payload.status || payload.health || payload.state || "not_reported";
      const count = coerceFiniteNumber(payload.count ?? payload.error_count ?? payload.broken_count ?? payload.warning_count);
      const message = payload.message || payload.summary || payload.detail || "";
      return {
        label: fallbackLabel,
        status,
        count,
        message,
      };
    }

    function normalizeQualityCompleteness(rawValue) {
      const payload = rawValue && typeof rawValue === "object" && !Array.isArray(rawValue) ? rawValue : null;
      if (!payload) return null;
      const score = coerceFiniteNumber(payload.score);
      const expectedSections = coerceFiniteNumber(payload.expected_sections ?? payload.expectedSections ?? payload.expected);
      const reportedSections = coerceFiniteNumber(payload.reported_sections ?? payload.reportedSections ?? payload.reported);
      const missingCount = coerceFiniteNumber(payload.missing_count ?? payload.missingCount ?? payload.missing);
      const notReportedCount = coerceFiniteNumber(payload.not_reported_count ?? payload.notReportedCount);
      const sections = Array.isArray(payload.sections) ? payload.sections : [];
      const missingSections = Array.isArray(payload.missing_sections)
        ? payload.missing_sections
        : Array.isArray(payload.missingSections) ? payload.missingSections : [];
      const notReportedSections = Array.isArray(payload.not_reported_sections)
        ? payload.not_reported_sections
        : Array.isArray(payload.notReportedSections) ? payload.notReportedSections : [];
      return {
        score,
        status: payload.status || "not_reported",
        expectedSections,
        reportedSections,
        missingCount,
        notReportedCount,
        missingSections,
        notReportedSections,
        sections,
      };
    }

    function formatQualityHealth(health) {
      const payload = health && typeof health === "object" ? health : {};
      const status = payload.status || "not_reported";
      const count = Number.isFinite(payload.count) ? ` (${payload.count})` : "";
      if (status === "not_reported" || status === "unavailable") return "Brak danych";
      const displayStatus = formatStatusText(status);
      if (payload.message) return `${displayStatus}${count}: ${payload.message}`;
      return `${displayStatus}${count}`;
    }

    function formatCompletenessScore(completeness) {
      if (!completeness || !Number.isFinite(completeness.score)) return "Brak danych";
      return `${Math.max(0, Math.min(100, Math.round(completeness.score)))}%`;
    }

    function formatStatusText(value) {
      const text = String(value ?? "");
      const normalized = text.toLowerCase();
      const labels = {
        passed: "Przeszło",
        passed_with_warnings: "Przeszło z ostrzeżeniami",
        failed: "Błąd",
        warning: "Ostrzeżenie",
        warnings: "Ostrzeżenia",
        error: "Błąd",
        unavailable: "Niedostępne",
        not_reported: "Brak danych",
        reported: "Zaraportowano",
        ready: "Gotowe",
        ready_with_review: "Do kontroli",
        release_ready: "Gotowe do publikacji",
        release_blocked: "Blokada publikacji",
        partial: "Częściowe",
        primary: "Główna próba",
        skipped: "Pominięto",
        applied: "Zastosowano",
        verified: "Zweryfikowano",
        not_verified: "Niezweryfikowane",
        previewer_passed: "Kindle Previewer OK",
        send_to_kindle_passed: "Send to Kindle OK",
        blocked: "Zablokowane",
      };
      return labels[normalized] || text;
    }

    function notReported(value) {
      if (value === null || value === undefined || value === "") return "Brak danych";
      return formatStatusText(value);
    }

    function normalizeOptionalObject(value) {
      return value && typeof value === "object" && !Array.isArray(value) ? value : null;
    }

    function normalizeOptionalArray(value) {
      return Array.isArray(value) ? value : [];
    }

    function formatMetricValue(value) {
      if (value === null || value === undefined || value === "") return "Brak danych";
      if (typeof value === "number" && Number.isFinite(value)) return String(value);
      if (typeof value === "boolean") return value ? "Tak" : "Nie";
      if (Array.isArray(value)) return value.length ? value.join(", ") : "Brak danych";
      if (typeof value === "object") {
        const status = value.status || value.health || value.state || "";
        const count = coerceFiniteNumber(value.count ?? value.total ?? value.items ?? value.value);
        const message = value.message || value.summary || value.detail || "";
        if (status || Number.isFinite(count) || message) {
          return [formatStatusText(status), Number.isFinite(count) ? String(count) : "", message].filter(Boolean).join(" / ") || "Brak danych";
        }
        return "Zaraportowano";
      }
      return formatStatusText(value);
    }

    function renderQualityRows(rows) {
      return rows.map(([label, value]) => `
        <div class="quality-row">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(formatMetricValue(value))}</strong>
        </div>
      `).join("");
    }

    function renderCompactList(items, emptyText = "Brak danych", limit = 6) {
      const safeItems = normalizeOptionalArray(items).filter((item) => item !== null && item !== undefined && item !== "");
      if (!safeItems.length) return `<div class="quality-empty">${escapeHtml(emptyText)}</div>`;
      return `
        <ul class="quality-compact-list">
          ${safeItems.slice(0, limit).map((item) => {
            if (typeof item === "object") {
              const label = item.label || item.title || item.name || item.message || item.href || item.path || JSON.stringify(item);
              const detail = item.detail || item.status || item.level || item.page || "";
              return `<li>${escapeHtml(detail ? `${label} (${detail})` : label)}</li>`;
            }
            return `<li>${escapeHtml(item)}</li>`;
          }).join("")}
        </ul>
      `;
    }

    function describeQualityReason(item) {
      if (item === null || item === undefined || item === "") return "";
      if (typeof item !== "object") return String(item);
      const code = item.code ? `[${item.code}] ` : "";
      const message = item.message || item.title || item.name || item.summary || item.detail || item.suggested_action || "";
      const source = item.source ? ` (${item.source})` : "";
      if (message) return `${code}${message}${source}`;
      try {
        return `${code}${JSON.stringify(item)}`;
      } catch (error) {
        return code || "Zgłoszono problem jakości.";
      }
    }

    function buildTopQualityReasons(options) {
      const payload = options || {};
      const {
        blockers = [],
        issueBlockers = [],
        userFacingReasons = [],
        qualitySelectionReasons = [],
        sendToKindleBlockers = [],
        warnings = [],
        reviewItems = [],
        fallbackReason = "",
        isReady = false,
      } = payload;
      const orderedGroups = [
        blockers,
        issueBlockers,
        userFacingReasons,
        qualitySelectionReasons,
        sendToKindleBlockers,
        warnings,
        reviewItems,
      ];
      const seen = new Set();
      const reasons = [];
      orderedGroups.forEach((group) => {
        normalizeOptionalArray(group).forEach((item) => {
          const reason = describeQualityReason(item).trim();
          const key = reason.toLowerCase();
          if (!reason || seen.has(key)) return;
          seen.add(key);
          reasons.push(reason);
        });
      });
      if (!reasons.length) {
        reasons.push(isReady ? "Brak blockerów publikacji." : (fallbackReason || "Brak danych."));
      }
      return reasons.slice(0, 5);
    }

    function renderTopQualityReasons(items) {
      const safeItems = normalizeOptionalArray(items).filter(Boolean);
      return `
        <ol class="quality-top-reasons-list">
          ${safeItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ol>
      `;
    }

    function renderMagazineProblemSamples(preview) {
      const payload = normalizeOptionalObject(preview);
      const samples = payload ? normalizeOptionalArray(payload.problem_samples || payload.samples) : [];
      if (!samples.length) {
        return `<div class="quality-empty">Brak automatycznych próbek problemów magazynu.</div>`;
      }
      return `
        <ul class="quality-compact-list quality-sample-list">
          ${samples.slice(0, 10).map((item) => {
            const type = item && item.type ? item.type : "sample";
            const title = item && (item.title || item.evidence) ? (item.title || item.evidence) : "Próbka jakości";
            const evidence = item && item.evidence ? `: ${item.evidence}` : "";
            const source = item && item.source ? ` [${item.source}]` : "";
            const location = item && item.location && typeof item.location === "object"
              ? [item.location.href, item.location.file, item.location.page ? `str. ${item.location.page}` : "", item.location.section].filter(Boolean).join(" / ")
              : "";
            return `<li><strong>${escapeHtml(type)}</strong>${escapeHtml(source)} — ${escapeHtml(title)}${escapeHtml(evidence)}${location ? `<br><small>${escapeHtml(location)}</small>` : ""}</li>`;
          }).join("")}
        </ul>
      `;
    }

    function formatPremiumScore(premiumScoring) {
      const payload = normalizeOptionalObject(premiumScoring) || {};
      const score = coerceFiniteNumber(payload.premium_score ?? payload.premiumScore ?? payload.score_10 ?? payload.score);
      if (!Number.isFinite(score)) return "Brak danych";
      const clamped = Math.max(0, Math.min(10, score));
      const precision = Number.isInteger(clamped) ? 0 : 1;
      return `${clamped.toFixed(precision)}/10`;
    }

    function resolveKindleReadyValue(premiumScoring, sendToKindleReady, kindleDelivery) {
      const scoring = normalizeOptionalObject(premiumScoring) || {};
      const delivery = normalizeOptionalObject(kindleDelivery) || {};
      if (typeof scoring.kindle_ready === "boolean") return scoring.kindle_ready;
      if (typeof scoring.kindleReady === "boolean") return scoring.kindleReady;
      if (typeof delivery.automated_ready === "boolean") return delivery.automated_ready;
      if (typeof sendToKindleReady === "boolean") return sendToKindleReady;
      return null;
    }

    function formatYesNo(value) {
      if (value === true) return "yes";
      if (value === false) return "no";
      return "Brak danych";
    }

    function qualityToneFromStatus(value) {
      const normalized = String(value ?? "").toLowerCase();
      if (["passed", "ready", "release_ready", "verified", "previewer_passed", "send_to_kindle_passed", "success"].includes(normalized)) return "ready";
      if (["failed", "error", "release_blocked", "blocked", "no"].includes(normalized)) return "failed";
      return "review";
    }

    function qualityToneFromBoolean(value) {
      if (value === true) return "ready";
      if (value === false) return "failed";
      return "review";
    }

    function qualityToneFromPremiumScore(premiumScoring) {
      const payload = normalizeOptionalObject(premiumScoring) || {};
      const score = coerceFiniteNumber(payload.premium_score ?? payload.premiumScore ?? payload.score_10 ?? payload.score);
      if (!Number.isFinite(score)) return "review";
      if (score >= 9) return "ready";
      if (score >= 7) return "review";
      return "failed";
    }

    function formatAiVerifierStatus(aiVerifier) {
      if (aiVerifier === null || aiVerifier === undefined || aiVerifier === "") return "Brak danych";
      if (typeof aiVerifier !== "object") return formatStatusText(aiVerifier);
      const status = aiVerifier.status || aiVerifier.state || aiVerifier.verdict || aiVerifier.result || "reported";
      const message = aiVerifier.message || aiVerifier.summary || aiVerifier.detail || "";
      return [formatStatusText(status), message].filter(Boolean).join(" / ") || "Brak danych";
    }

    function renderQualityHeroMetric(label, value, detail = "", tone = "review") {
      return `
        <div class="quality-hero-metric" data-tone="${escapeHtml(tone)}">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          ${detail ? `<small>${escapeHtml(detail)}</small>` : ""}
        </div>
      `;
    }

    function getIssueGroupItems(issueGroups, key, fallbackItems = []) {
      const groups = normalizeOptionalObject(issueGroups) || {};
      const value = groups[key] || groups[`${key}_items`] || fallbackItems;
      if (Array.isArray(value)) return value.length ? value : normalizeOptionalArray(fallbackItems);
      if (value && typeof value === "object" && Array.isArray(value.items)) return value.items;
      if (Number.isFinite(coerceFiniteNumber(value)) && Number(value) > 0) return [`Zgłoszono: ${value}`];
      return [];
    }

    function summarizeIssueGroup(issueGroups, key, fallbackItems = []) {
      const groups = normalizeOptionalObject(issueGroups) || {};
      const value = groups[key] || groups[`${key}_items`];
      if (Array.isArray(value)) return value.length || normalizeOptionalArray(fallbackItems).length;
      if (value && typeof value === "object") {
        const count = coerceFiniteNumber(value.count ?? value.total);
        if (Number.isFinite(count)) return count;
        if (Array.isArray(value.items)) return value.items.length;
      }
      return normalizeOptionalArray(fallbackItems).length;
    }

    function buildManualReviewQueue(issueGroups, qualityBlockers = [], fallbackReviewItems = []) {
      const groups = normalizeOptionalObject(issueGroups);
      if (!groups) return null;
      const queueGroups = [
        ["blockers", "Napraw przed publikacją", normalizeOptionalArray(qualityBlockers)],
        ["review", "Kontrola ręczna", normalizeOptionalArray(fallbackReviewItems)],
        ["warnings", "Ostrzeżenie", []],
      ];
      const queue = [];
      queueGroups.forEach(([key, label, fallbackItems]) => {
        getIssueGroupItems(groups, key, fallbackItems).forEach((item) => {
          if (item && typeof item === "object") {
            const message = item.message || item.title || item.name || item.code || "Zgłoszono problem jakości.";
            const context = [item.source, item.section, item.page ? `strona ${item.page}` : "", item.file]
              .filter(Boolean)
              .join(" / ");
            queue.push({
              label,
              message,
              context,
            });
            return;
          }
          queue.push({
            label,
            message: String(item),
            context: "",
          });
        });
      });
      return queue;
    }

    function renderManualReviewQueue(queue) {
      if (queue === null) return `<div class="quality-empty">Brak danych</div>`;
      if (!queue.length) return `<div class="quality-empty">Brak pozycji do recznej kontroli.</div>`;
      return `
        <ul class="quality-compact-list">
          ${queue.slice(0, 8).map((item) => {
            const context = item.context ? ` (${item.context})` : "";
            return `<li>${escapeHtml(item.label)}: ${escapeHtml(item.message)}${escapeHtml(context)}</li>`;
          }).join("")}
        </ul>
      `;
    }

    function renderIssueColumn(label, items) {
      return `
        <div class="quality-issue-column">
          <strong>${escapeHtml(label)}</strong>
          ${renderCompactList(items, "Brak danych", 5)}
        </div>
      `;
    }

    function renderQualityDisclosurePanel(options) {
      const {
        id,
        title,
        subtitle = "",
        body = "",
        wide = false,
        open = false,
      } = options || {};
      return `
        <details class="quality-cockpit-panel" id="${escapeHtml(id)}"${wide ? ' data-span="wide"' : ""}${open ? " open" : ""}>
          <summary class="quality-panel-title">
            <span>${escapeHtml(title)}</span>
            <small>${escapeHtml(subtitle)}</small>
          </summary>
          ${body}
        </details>
      `;
    }

    function deriveQualityVerdict(normalized) {
      const payload = normalized && typeof normalized === "object" ? normalized : {};
      const releaseVerdict = payload.releaseVerdict || "";
      const readingVerdict = payload.readingVerdict || "";
      const releaseBlocked = Boolean(payload.releaseBlocked);
      const downloadAvailable = payload.downloadAvailable;
      const qualityBlockers = Array.isArray(payload.qualityBlockers) ? payload.qualityBlockers : [];
      const hasReleaseBlocker = releaseVerdict === "release_blocked" || releaseBlocked || qualityBlockers.length > 0;
      if (releaseVerdict === "failed") {
        return {
          key: "failed",
          tone: "failed",
          label: "Nie publikuj",
          detail: downloadAvailable === false
            ? "EPUB nie jest dostępny do pobrania dla tego zadania."
            : "Konwersja lub strukturalna walidacja zakończyła się błędem.",
        };
      }
      if (hasReleaseBlocker) {
        return {
          key: "release_blocked",
          tone: "failed",
          label: "Nie publikuj",
          detail: "EPUB wygenerowany, ale wymaga naprawy przed publikacją.",
        };
      }
      if (releaseVerdict === "ready_with_review") {
        return {
          key: "ready_with_review",
          tone: "review",
          label: "Kontrola",
          detail: "EPUB wygenerowany, ale wymaga kontroli jakości.",
        };
      }
      if (releaseVerdict === "release_ready") {
        return {
          key: "ready",
          tone: "ready",
          label: "Publikuj",
          detail: "EPUB jest gotowy do czytania i publikacji.",
        };
      }
      if (readingVerdict === "failed") {
        return {
          key: "failed",
          tone: "failed",
          label: "Nie publikuj",
          detail: downloadAvailable === false
            ? "EPUB nie jest dostępny do pobrania dla tego zadania."
            : "EPUB ma strukturalny problem jakości. Sprawdź walidację przed użyciem pliku.",
        };
      }
      if (readingVerdict === "ready_with_review") {
        return {
          key: "ready_with_review",
          tone: "review",
          label: "Kontrola",
          detail: "EPUB wygenerowany, ale wymaga kontroli jakości.",
        };
      }
      if (readingVerdict === "ready") {
        return {
          key: "ready",
          tone: "ready",
          label: "Publikuj",
          detail: "EPUB jest gotowy do czytania i publikacji.",
        };
      }
      const severity = payload.severity || "";
      const validation = payload.validation || "unavailable";
      const sizeStatus = payload.sizeBudget && payload.sizeBudget.status ? payload.sizeBudget.status : "unavailable";
      const reviewCount =
        (Number.isFinite(payload.highRiskPages) ? payload.highRiskPages : 0)
        + (Number.isFinite(payload.highRiskSections) ? payload.highRiskSections : 0)
        + (payload.headingRepair && Number.isFinite(payload.headingRepair.review) ? payload.headingRepair.review : 0);
      const warningCount = Number.isFinite(payload.warnings) ? payload.warnings : 0;
      const hasFallback = Boolean(payload.renderBudget && payload.renderBudget.attempt && payload.renderBudget.attempt !== "primary");
      const hasAlerts = Array.isArray(payload.alerts) && payload.alerts.length > 0;

      if (severity === "error" || validation === "failed" || sizeStatus === "failed") {
        return {
          key: "failed_quality_gate",
          tone: "failed",
          label: "Nie publikuj",
          detail: "Walidacja albo limit rozmiaru zgłosiły blokujący problem. Sprawdź EPUB przed użyciem.",
        };
      }

      if (
        severity === "warning"
        || validation === "passed_with_warnings"
        || sizeStatus === "passed_with_warnings"
        || reviewCount > 0
        || warningCount > 0
        || hasFallback
        || hasAlerts
        || payload.qualityAvailable === false
      ) {
        return {
          key: "ready_with_review",
          tone: "review",
          label: "Kontrola",
          detail: "EPUB wygenerowany, ale wymaga kontroli jakości.",
        };
      }

      if (validation === "passed") {
        return {
          key: "ready",
          tone: "ready",
          label: "Publikuj",
          detail: "Walidacja przeszła i payload jakości nie zgłosił sygnału kontroli.",
        };
      }

      return {
        key: "quality_pending",
        tone: "review",
        label: "Kontrola",
        detail: "EPUB wygenerowany, ale dowody jakości są niepełne albo niedostępne.",
      };
    }

    function parseJsonHeader(response, headerName) {
      const raw = response.headers.get(headerName);
      if (!raw) return [];
      try {
        return JSON.parse(decodeURIComponent(raw));
      } catch (error) {
        console.warn(`Nie udalo sie odczytac naglowka ${headerName}`, error);
        return [];
      }
    }

    function renderAuditList(title, items, formatter) {
      if (!items || !items.length) {
        return `
          <div class="audit-group">
            <strong>${escapeHtml(title)}</strong>
            <div class="audit-empty">Brak pozycji do recznej kontroli.</div>
          </div>
        `;
      }
      return `
        <div class="audit-group">
          <strong>${escapeHtml(title)}</strong>
          <ul class="audit-list">${items.map((item) => `<li>${formatter(item)}</li>`).join("")}</ul>
        </div>
      `;
    }

    function renderConversionReport(options) {
      const {
        profile,
        validation,
        validationTool,
        sections,
        assets,
        layout,
        outputSizeBytes = null,
        warnings,
        highRiskPages,
        highRiskSections,
        warningList = [],
        highRiskPageList = [],
        highRiskSectionList = [],
        headingRepair = null,
        qualityAvailable = null,
        severity = "",
        alerts = [],
        sizeBudget = null,
        renderBudget = null,
        verdict = null,
        qualityStateUrl = "",
        downloadUrl = "",
        downloadAvailable = null,
        readingVerdict = "",
        releaseVerdict = "",
        releaseBlocked = false,
        qualityBlockers = [],
        sendToKindleReady = null,
        sendToKindleBlockers = [],
        issueGroups = null,
        contentMetrics = null,
        textCleanup = null,
        referenceCleanup = null,
        assetSummary = null,
        magazineQualityPreview = null,
        tocPreview = null,
        epubcheckDetail = null,
        metadataSummary = null,
        premiumScoring = null,
        qualitySelection = null,
        kindleDelivery = null,
        aiVerifier = null,
        routeModelShadow = null,
        trainedQualityModelStatus = "",
        metadataHealth = null,
        linkHealth = null,
        visibleJunk = null,
        qualityCompleteness = null,
        userFacingVerdict = "",
        userFacingReasons = [],
      } = options || {};
      const safeValidation = validation || "unavailable";
      const reportVerdict = verdict || deriveQualityVerdict({
        severity,
        validation,
        warnings,
        highRiskPages,
        highRiskSections,
        alerts,
        sizeBudget,
        renderBudget,
        headingRepair,
        qualityAvailable,
        downloadAvailable,
        readingVerdict,
        releaseVerdict,
        releaseBlocked,
        qualityBlockers,
      });
      const type = reportVerdict.tone === "failed"
        ? "error"
        : reportVerdict.tone === "ready" ? "success" : "info";
      const validationLabel = safeValidation === "passed"
        ? "EPUBCheck przeszedl bez bledow"
        : safeValidation === "failed"
          ? "EPUBCheck wykryl problemy wymagajace poprawy"
          : "EPUBCheck nie byl dostepny";
      const budgetDiagnostics = [];
      if (sizeBudget && sizeBudget.status && sizeBudget.status !== "unavailable") {
        budgetDiagnostics.push(`status ${sizeBudget.status}`);
      }
      if (renderBudget && renderBudget.budgetClass) {
        budgetDiagnostics.push(`klasa ${renderBudget.budgetClass}`);
      }
      if (renderBudget && renderBudget.attempt) {
        budgetDiagnostics.push(`attempt ${renderBudget.attempt}`);
      }
      if (renderBudget && Number.isFinite(renderBudget.targetWarnBytes) && renderBudget.targetWarnBytes > 0) {
        budgetDiagnostics.push(`warn ${formatBytes(renderBudget.targetWarnBytes)}`);
      }
      if (renderBudget && Number.isFinite(renderBudget.targetHardBytes) && renderBudget.targetHardBytes > 0) {
        budgetDiagnostics.push(`hard ${formatBytes(renderBudget.targetHardBytes)}`);
      }
      if (sizeBudget && sizeBudget.message) {
        budgetDiagnostics.push(sizeBudget.message);
      }
      const badges = [
        profile ? `profil: ${profile}` : null,
        sections ? `sekcje ${sections}` : null,
        assets ? `zasoby ${assets}` : null,
        layout ? `układ: ${layout}` : null,
        validationTool ? `narzedzie: ${validationTool}` : null,
        Number.isFinite(outputSizeBytes) && outputSizeBytes > 0 ? `rozmiar ${formatBytes(outputSizeBytes)}` : null,
        Number.isFinite(warnings) ? `ostrzezenia ${warnings}` : null,
        Number.isFinite(highRiskSections) ? `sekcje wysokiego ryzyka ${highRiskSections}` : null,
        Number.isFinite(highRiskPages) ? `strony wysokiego ryzyka ${highRiskPages}` : null,
        sizeBudget && sizeBudget.status && sizeBudget.status !== "unavailable" ? `limit rozmiaru: ${formatStatusText(sizeBudget.status)}` : null,
        renderBudget && renderBudget.budgetClass ? `budżet: ${renderBudget.budgetClass}` : null,
        headingRepair && headingRepair.status === "applied" ? `naprawa TOC ${headingRepair.tocBefore || 0}→${headingRepair.tocAfter || 0}` : null,
        headingRepair && headingRepair.status === "applied" && Number.isFinite(headingRepair.removed) ? `fałszywe nagłówki ${headingRepair.removed}` : null,
        headingRepair && headingRepair.status === "applied" && Number.isFinite(headingRepair.review) ? `kontrola ${headingRepair.review}` : null,
      ].filter(Boolean).map((item) => `<span class="badge">${item}</span>`).join("");

      const contractHint = qualityAvailable === false
        ? `<p style="margin-top:8px;font-size:.8rem;color:var(--muted);">Stan jakości nie byl kompletny, wiec UI pokazuje zapasowe metadane z odpowiedzi konwersji.</p>`
        : "";
      const auditHint = Number.isFinite(highRiskSections) || Number.isFinite(highRiskPages)
        ? `<p style="margin-top:8px;font-size:.8rem;color:var(--muted);">Audyt premium: ${highRiskSections || 0} sekcji i ${highRiskPages || 0} stron oznaczonych do kontroli.</p>`
        : "";
      const headingRepairHint = headingRepair && headingRepair.status === "applied"
        ? `<p style="margin-top:8px;font-size:.8rem;color:var(--muted);">Naprawa nagłówków: publikacja ${escapeHtml(formatStatusText(headingRepair.release || "unavailable"))}, EPUBCheck ${escapeHtml(formatStatusText(headingRepair.epubcheck || "unavailable"))}.</p>`
        : headingRepair && headingRepair.status === "failed"
          ? `<p style="margin-top:8px;font-size:.8rem;color:#b25c37;">Naprawa nagłówków nie zostala zastosowana. EPUB bazowy zostal zachowany. Powod: ${escapeHtml(headingRepair.error || "nieznany blad")}.</p>`
          : headingRepair && headingRepair.status === "skipped"
          ? `<p style="margin-top:8px;font-size:.8rem;color:var(--muted);">Naprawa nagłówków nie byla uruchomiona dla tej konwersji.${headingRepair.error ? ` Powod: ${escapeHtml(headingRepair.error)}.` : ""}</p>`
          : "";
      const budgetHint = budgetDiagnostics.length
        ? `<p style="margin-top:8px;font-size:.8rem;color:var(--muted);">Budżet rozmiaru: ${escapeHtml(budgetDiagnostics.join(" | "))}.</p>`
        : "";
      const alertsPanel = Array.isArray(alerts) && alerts.length
        ? `
          <div class="audit-group">
            <strong>Alerty operacyjne</strong>
            <ul class="audit-list">${alerts.map((item) => {
              const level = item && item.level ? `${item.level}: ` : "";
              const message = item && item.message ? item.message : "";
              return `<li>${escapeHtml(level)}${escapeHtml(message)}</li>`;
            }).join("")}</ul>
          </div>
        `
        : "";
      const auditPanel = `
        <div class="audit-panel">
          ${alertsPanel}
          ${renderAuditList("Ostrzezenia", warningList, (item) => escapeHtml(item))}
          ${renderAuditList("Strony do kontroli", highRiskPageList, (item) => {
            const page = item.page ? `strona ${escapeHtml(item.page)}` : "strona ?";
            const title = item.title ? ` - ${escapeHtml(item.title)}` : "";
            const kind = item.kind ? ` (${escapeHtml(item.kind)})` : "";
            const flags = Array.isArray(item.flags) && item.flags.length ? ` [${escapeHtml(item.flags.join(", "))}]` : "";
            return `${page}${kind}${title}${flags}`;
          })}
          ${renderAuditList("Sekcje do kontroli", highRiskSectionList, (item) => {
            const title = item.title ? escapeHtml(item.title) : "Sekcja bez tytulu";
            const pages = Array.isArray(item.pages) && item.pages.length === 2
              ? ` (str. ${escapeHtml(item.pages[0])}-${escapeHtml(item.pages[1])})`
              : "";
            const flags = Array.isArray(item.flags) && item.flags.length ? ` [${escapeHtml(item.flags.join(", "))}]` : "";
            return `${title}${pages}${flags}`;
          })}
        </div>
      `;
      const fallbackLabel = renderBudget && renderBudget.attempt
        ? `${renderBudget.attempt}${renderBudget.budgetClass ? ` / ${renderBudget.budgetClass}` : ""}`
        : "Brak danych";
      const tocLabel = headingRepair && headingRepair.status
        ? `${formatStatusText(headingRepair.status)}${Number.isFinite(headingRepair.review) && headingRepair.review > 0 ? ` / kontrola ${headingRepair.review}` : ""}`
        : "Brak danych";
      const safeIssueGroups = normalizeOptionalObject(issueGroups);
      const safeContentMetrics = normalizeOptionalObject(contentMetrics);
      const safeTextCleanup = normalizeOptionalObject(textCleanup);
      const safeReferenceCleanup = normalizeOptionalObject(referenceCleanup);
      const safeAssetSummary = normalizeOptionalObject(assetSummary);
      const safeMagazineQualityPreview = normalizeOptionalObject(magazineQualityPreview);
      const safeTocPreview = normalizeOptionalObject(tocPreview);
      const safeEpubcheckDetail = normalizeOptionalObject(epubcheckDetail);
      const safeMetadataSummary = normalizeOptionalObject(metadataSummary);
      const safePremiumScoring = normalizeOptionalObject(premiumScoring);
      const safeQualitySelection = normalizeOptionalObject(qualitySelection);
      const safeRouteModelShadow = normalizeOptionalObject(routeModelShadow);
      const safeKindleDelivery = normalizeOptionalObject(kindleDelivery);
      const safeQualityCompleteness = normalizeQualityCompleteness(qualityCompleteness);
      const safeQualityBlockers = normalizeOptionalArray(qualityBlockers);
      const safeSendToKindleBlockers = normalizeOptionalArray(sendToKindleBlockers);
      const safeUserFacingReasons = normalizeOptionalArray(userFacingReasons);
      const safeUserFacingVerdict = normalizeOptionalObject(userFacingVerdict);
      const userFacingLabel = safeUserFacingVerdict && safeUserFacingVerdict.label
        ? safeUserFacingVerdict.label
        : reportVerdict.label;
      const userFacingDetail = safeUserFacingVerdict && safeUserFacingVerdict.detail
        ? safeUserFacingVerdict.detail
        : typeof userFacingVerdict === "string" && userFacingVerdict
          ? userFacingVerdict
          : reportVerdict.detail;
      const sendToKindleLabel = sendToKindleReady === true
        ? "Można wysłać na Kindle"
        : sendToKindleReady === false
          ? "Draft do kontroli"
          : "Brak danych";
      const premiumScoreLabel = formatPremiumScore(safePremiumScoring);
      const kindleReadyValue = resolveKindleReadyValue(safePremiumScoring, sendToKindleReady, safeKindleDelivery);
      const kindleReadyLabel = formatYesNo(kindleReadyValue);
      const aiVerifierLabel = formatAiVerifierStatus(aiVerifier);
      const qualityPolicyLabel = "Local quality policy verifier";
      const routeShadowLabel = safeRouteModelShadow && safeRouteModelShadow.status !== "not_reported"
        ? `${safeRouteModelShadow.mode || "shadow"} / ${safeRouteModelShadow.ml_profile || "no prediction"} (${formatMetricValue(safeRouteModelShadow.ml_confidence)})`
        : "Brak danych";
      const trainedQualityLabel = trainedQualityModelStatus || "policy_only_not_trained";
      if (dashboardVerdictMetric) {
        dashboardVerdictMetric.textContent = reportVerdict.label;
      }
      const blockerItems = getIssueGroupItems(safeIssueGroups, "blockers", safeQualityBlockers);
      const warningItems = getIssueGroupItems(safeIssueGroups, "warnings", warningList);
      const reviewItems = getIssueGroupItems(safeIssueGroups, "review", [...highRiskPageList, ...highRiskSectionList]);
      const manualReviewQueue = buildManualReviewQueue(
        safeIssueGroups,
        safeQualityBlockers,
        [...highRiskPageList, ...highRiskSectionList],
      );
      const qualityRows = renderQualityRows([
        ["Premium score", premiumScoreLabel],
        ["Wybor artefaktu", safeQualitySelection ? formatStatusText(safeQualitySelection.status || "reported") : "Brak danych"],
        ["Wybrany EPUB", safeQualitySelection ? (safeQualitySelection.selected_candidate || safeQualitySelection.selected_stage || "Brak danych") : "Brak danych"],
        ["Odrzucony EPUB", safeQualitySelection ? (safeQualitySelection.rejected_candidate || safeQualitySelection.rejected_stage || "Brak danych") : "Brak danych"],
        ["Kindle-ready", kindleReadyLabel],
        [qualityPolicyLabel, aiVerifierLabel],
        ["Route model shadow", routeShadowLabel],
        ["Trained quality model", trainedQualityLabel],
        ["Kompletność", formatCompletenessScore(safeQualityCompleteness)],
        ["Pobranie", downloadAvailable === null ? "Brak danych" : Boolean(downloadAvailable)],
        ["Gotowość do czytania", readingVerdict || reportVerdict.label],
        ["Kindle / email", sendToKindleLabel],
        ["Blokuje publikację", Boolean(releaseBlocked)],
        ["Walidacja", validationTool ? `${safeValidation} / ${validationTool}` : safeValidation],
        ["Fallback", fallbackLabel],
        ["Jakość TOC", tocLabel],
        ["Treść", safeContentMetrics ? (safeContentMetrics.summary || safeContentMetrics.status || "Zaraportowano") : "Brak danych"],
        ["Czyszczenie tekstu", safeTextCleanup ? (safeTextCleanup.status || safeTextCleanup.summary || "Zaraportowano") : "Brak danych"],
        ["Bibliografia", safeReferenceCleanup ? (safeReferenceCleanup.status || safeReferenceCleanup.summary || "Zaraportowano") : "Brak danych"],
        ["Metadane", formatQualityHealth(metadataHealth)],
        ["Linki", formatQualityHealth(linkHealth)],
        ["Widoczne artefakty", formatQualityHealth(visibleJunk)],
      ]);
      const completenessRows = renderQualityRows([
        ["Wynik", formatCompletenessScore(safeQualityCompleteness)],
        ["Status", safeQualityCompleteness ? safeQualityCompleteness.status : "Brak danych"],
        ["Zaraportowane", safeQualityCompleteness && Number.isFinite(safeQualityCompleteness.reportedSections) ? safeQualityCompleteness.reportedSections : "Brak danych"],
        ["Oczekiwane", safeQualityCompleteness && Number.isFinite(safeQualityCompleteness.expectedSections) ? safeQualityCompleteness.expectedSections : "Brak danych"],
        ["Brakujące", safeQualityCompleteness && Number.isFinite(safeQualityCompleteness.missingCount) ? safeQualityCompleteness.missingCount : "Brak danych"],
        ["Bez danych", safeQualityCompleteness && Number.isFinite(safeQualityCompleteness.notReportedCount) ? safeQualityCompleteness.notReportedCount : "Brak danych"],
      ]);
      const completenessSectionItems = safeQualityCompleteness && Array.isArray(safeQualityCompleteness.sections)
        ? safeQualityCompleteness.sections.map((section) => {
          const label = section && (section.label || section.key) ? (section.label || section.key) : "Sekcja";
          const status = section && section.reported ? (section.status || "reported") : "Brak danych";
          return `${label}: ${formatStatusText(status)}`;
        })
        : [];
      const issueBoard = `
        <div class="quality-cockpit-panel" id="qualityIssueBoard" data-span="wide">
          <div class="quality-panel-title">
            <span>Kontrola jakości</span>
            <small>${summarizeIssueGroup(safeIssueGroups, "blockers", safeQualityBlockers)} do naprawy / ${summarizeIssueGroup(safeIssueGroups, "warnings", warningList)} ostrzeżeń / ${summarizeIssueGroup(safeIssueGroups, "review", [...highRiskPageList, ...highRiskSectionList])} do kontroli</small>
          </div>
          <div class="quality-issue-board">
            ${renderIssueColumn("Wymaga naprawy przed publikacją", blockerItems)}
            ${renderIssueColumn("Ostrzeżenia", warningItems)}
            ${renderIssueColumn("Do kontroli", reviewItems)}
          </div>
        </div>
      `;
      const userFacingReasonsPanel = `
        <div class="quality-cockpit-panel" id="qualityUserFacingReasonsPanel" data-span="wide">
          <div class="quality-panel-title"><span>Najważniejsze powody</span><small>Widoczne dla czytelnika</small></div>
          ${renderCompactList(safeUserFacingReasons, "Brak danych", 5)}
        </div>
      `;
      const manualReviewQueuePanel = `
        ${renderQualityDisclosurePanel({
          id: "qualityManualReviewQueuePanel",
          title: "Kolejka kontroli ręcznej",
          subtitle: "blokery / kontrola / ostrzeżenia",
          body: renderManualReviewQueue(manualReviewQueue),
          wide: true,
        })}
      `;
      const epubcheckRows = renderQualityRows([
        ["Status", safeEpubcheckDetail ? (safeEpubcheckDetail.status || safeValidation) : safeValidation],
        ["Narzędzie", safeEpubcheckDetail ? (safeEpubcheckDetail.tool || validationTool || "Brak danych") : (validationTool || "Brak danych")],
        ["Błędy", safeEpubcheckDetail ? (safeEpubcheckDetail.errors ?? safeEpubcheckDetail.error_count) : "Brak danych"],
        ["Ostrzeżenia", safeEpubcheckDetail ? (safeEpubcheckDetail.warnings ?? safeEpubcheckDetail.warning_count) : "Brak danych"],
      ]);
      const tocItems = safeTocPreview
        ? normalizeOptionalArray(safeTocPreview.items || safeTocPreview.entries || safeTocPreview.headings)
        : [];
      const metadataRows = renderQualityRows([
        ["Tytuł", safeMetadataSummary ? safeMetadataSummary.title : "Brak danych"],
        ["Autor", safeMetadataSummary ? safeMetadataSummary.author : "Brak danych"],
        ["Język", safeMetadataSummary ? safeMetadataSummary.language : "Brak danych"],
        ["Stan", formatQualityHealth(metadataHealth)],
      ]);
      const imageRows = renderQualityRows([
        ["Obrazy", safeAssetSummary ? (safeAssetSummary.images ?? safeAssetSummary.image_count) : (Number.isFinite(assets) ? assets : "Brak danych")],
        ["Diagramy", safeAssetSummary ? (safeAssetSummary.diagrams ?? safeAssetSummary.diagram_count) : "Brak danych"],
        ["Brak alt", safeAssetSummary ? (safeAssetSummary.missing_alt ?? safeAssetSummary.missing_alt_count) : "Brak danych"],
        ["Za duże", safeAssetSummary ? (safeAssetSummary.oversized ?? safeAssetSummary.oversized_count) : "Brak danych"],
        ["Ryzykowne media", safeAssetSummary ? (safeAssetSummary.unsupported_media_count ?? "Brak danych") : "Brak danych"],
      ]);
      const kindleRows = renderQualityRows([
        ["Status", sendToKindleLabel],
        ["Rozmiar", Number.isFinite(outputSizeBytes) && outputSizeBytes > 0 ? formatBytes(outputSizeBytes) : "Brak danych"],
        ["Blokery", safeSendToKindleBlockers.length],
      ]);
      const qualitySelectionRows = renderQualityRows([
        ["Status", safeQualitySelection ? formatStatusText(safeQualitySelection.status || "reported") : "Brak danych"],
        ["Wybrany", safeQualitySelection ? (safeQualitySelection.selected_candidate || safeQualitySelection.selected_stage || "Brak danych") : "Brak danych"],
        ["Odrzucony", safeQualitySelection ? (safeQualitySelection.rejected_candidate || safeQualitySelection.rejected_stage || "Brak danych") : "Brak danych"],
        ["Delta score", safeQualitySelection && Number.isFinite(coerceFiniteNumber(safeQualitySelection.score_delta)) ? coerceFiniteNumber(safeQualitySelection.score_delta) : "Brak danych"],
        ["Delta blockerow", safeQualitySelection && Number.isFinite(coerceFiniteNumber(safeQualitySelection.blocker_delta)) ? coerceFiniteNumber(safeQualitySelection.blocker_delta) : "Brak danych"],
      ]);
      const qualityMatrixPanel = renderQualityDisclosurePanel({
        id: "qualityMatrixPanel",
        title: "Macierz jakości",
        subtitle: "Dowody jakości",
        body: `<div class="quality-matrix">${qualityRows}</div>`,
        wide: true,
        open: true,
      });
      const completenessPanel = renderQualityDisclosurePanel({
        id: "qualityCompletenessPanel",
        title: "Kompletność jakości",
        subtitle: formatCompletenessScore(safeQualityCompleteness),
        body: `
          <div class="quality-matrix">${completenessRows}</div>
          ${renderCompactList(completenessSectionItems, "Brak danych", 9)}
        `,
        wide: true,
      });
      const epubcheckPanel = renderQualityDisclosurePanel({
        id: "qualityEpubcheckPanel",
        title: "EPUBCheck",
        subtitle: notReported(safeEpubcheckDetail && safeEpubcheckDetail.version),
        body: `
          <div class="quality-matrix">${epubcheckRows}</div>
          ${safeEpubcheckDetail && safeEpubcheckDetail.message ? `<div class="quality-empty">${escapeHtml(safeEpubcheckDetail.message)}</div>` : ""}
        `,
      });
      const tocPreviewPanel = renderQualityDisclosurePanel({
        id: "qualityTocPreviewPanel",
        title: "Podgląd TOC",
        subtitle: safeTocPreview ? notReported(safeTocPreview.status || safeTocPreview.depth) : "Brak danych",
        body: renderCompactList(tocItems, "Brak danych", 8),
      });
      const metadataPanel = renderQualityDisclosurePanel({
        id: "qualityMetadataPanel",
        title: "Metadane",
        subtitle: formatQualityHealth(metadataHealth),
        body: `<div class="quality-matrix">${metadataRows}</div>`,
      });
      const assetsPanel = renderQualityDisclosurePanel({
        id: "qualityAssetsPanel",
        title: "Obrazy / diagramy",
        subtitle: safeAssetSummary ? (safeAssetSummary.status || "Zaraportowano") : "Brak danych",
        body: `<div class="quality-matrix">${imageRows}</div>`,
      });
      const magazinePreviewPanel = renderQualityDisclosurePanel({
        id: "qualityMagazinePreviewPanel",
        title: "Szybki preview magazynu",
        subtitle: safeMagazineQualityPreview ? `${safeMagazineQualityPreview.sample_count || 0} próbek` : "Brak danych",
        body: renderMagazineProblemSamples(safeMagazineQualityPreview),
        wide: true,
        open: Boolean(safeMagazineQualityPreview && safeMagazineQualityPreview.sample_count),
      });
      const kindleDeliveryPanel = renderQualityDisclosurePanel({
        id: "qualityKindleDeliveryPanel",
        title: "Kindle / mail",
        subtitle: sendToKindleLabel,
        body: `
          <div class="quality-matrix">${kindleRows}</div>
          <div class="quality-empty">Handoff: pobierz EPUB, sprawdź verdict i wyślij przez Send to Kindle dopiero po zaakceptowaniu kontroli jakości.</div>
          ${safeSendToKindleBlockers.length ? renderCompactList(safeSendToKindleBlockers, "Brak danych", 5) : `<div class="quality-empty">Brak blockerów wysyłki.</div>`}
        `,
      });
      const qualitySelectionPanel = renderQualityDisclosurePanel({
        id: "qualitySelectionPanel",
        title: "Wybor artefaktu",
        subtitle: safeQualitySelection ? formatStatusText(safeQualitySelection.status || "reported") : "Brak danych",
        body: `
          <div class="quality-matrix">${qualitySelectionRows}</div>
          ${safeQualitySelection && Array.isArray(safeQualitySelection.reason_codes) ? renderCompactList(safeQualitySelection.reason_codes, "Brak danych", 6) : ""}
          ${safeQualitySelection && safeQualitySelection.status === "rejected" ? `<div class="quality-empty">Automatyczna naprawa odrzucona: pogarsza jakosc EPUB. Pobierany jest lepszy artefakt.</div>` : ""}
        `,
      });
      const userFacingDecision = safeUserFacingVerdict && safeUserFacingVerdict.decision ? safeUserFacingVerdict.decision : "";
      const decisionKey = userFacingDecision || (reportVerdict.key === "ready"
        ? "ready"
        : reportVerdict.key === "release_blocked" || reportVerdict.key === "failed" || reportVerdict.key === "failed_quality_gate"
          ? "blocked"
          : "review");
      const decisionStrip = `
        <div class="quality-decision-strip" id="qualityDecisionStrip" role="list" aria-label="Decyzja publikacji">
          ${[
            { key: "ready", label: "Publikuj" },
            { key: "review", label: "Kontrola" },
            { key: "blocked", label: "Nie publikuj" },
          ].map((item) => `
            <div class="quality-decision-option${decisionKey === item.key ? " is-active" : ""}" data-decision="${item.key}" role="listitem" aria-current="${decisionKey === item.key ? "true" : "false"}">
              ${item.label}
            </div>
          `).join("")}
        </div>
      `;
      const topReasons = buildTopQualityReasons({
        blockers: safeQualityBlockers,
        issueBlockers: blockerItems,
        userFacingReasons: safeUserFacingReasons,
        qualitySelectionReasons: safeQualitySelection && safeQualitySelection.status === "rejected"
          ? [{ code: "quality_selection_rejected", message: "Automatyczna naprawa odrzucona: pogarsza jakosc EPUB.", source: "quality_selection" }]
          : [],
        sendToKindleBlockers: safeSendToKindleBlockers,
        warnings: warningItems,
        reviewItems,
        fallbackReason: userFacingDetail,
        isReady: decisionKey === "ready",
      });
      const cockpitHero = `
        <div class="quality-verdict quality-cockpit-hero" id="qualityVerdictHeader" data-tone="${escapeHtml(reportVerdict.tone)}">
          <div class="quality-hero-copy">
            <div class="quality-hero-header">
              <span class="quality-eyebrow">Decyzja publikacji</span>
              <div class="quality-status-pill" aria-label="Status walidacji">Walidacja: ${escapeHtml(formatStatusText(safeValidation || "not_reported"))}</div>
            </div>
            <strong>${escapeHtml(userFacingLabel)}</strong>
            <span>${escapeHtml(userFacingDetail)}</span>
            ${decisionStrip}
          </div>
          <div class="quality-hero-metrics" aria-label="Kluczowe metryki jakości">
            ${renderQualityHeroMetric("Premium score", premiumScoreLabel, "target 9/10", qualityToneFromPremiumScore(safePremiumScoring))}
            ${renderQualityHeroMetric("Kindle-ready", kindleReadyLabel, "yes/no", qualityToneFromBoolean(kindleReadyValue))}
            ${renderQualityHeroMetric(qualityPolicyLabel, aiVerifierLabel, "offline policy; not trained ML", qualityToneFromStatus(aiVerifier && typeof aiVerifier === "object" ? (aiVerifier.status || aiVerifier.state || aiVerifier.verdict || aiVerifier.result) : aiVerifier))}
          </div>
          <div class="quality-top-reasons" id="qualityTopReasons">
            <div class="quality-panel-title"><span>Top 5 reasons/blockers</span><small>blokery najpierw</small></div>
            ${renderTopQualityReasons(topReasons)}
          </div>
        </div>
      `;
      const downloadLabel = safeUserFacingVerdict && safeUserFacingVerdict.download_label
        ? safeUserFacingVerdict.download_label
        : releaseBlocked || reportVerdict.key === "release_blocked"
        ? "Pobierz szkic EPUB"
        : "Pobierz EPUB";
      const qualityLinks = [
        qualityStateUrl ? `<a href="${escapeHtml(qualityStateUrl)}" target="_blank" rel="noreferrer">JSON jakości</a>` : "",
        downloadUrl ? `<a href="${escapeHtml(downloadUrl)}" class="quality-download-action">${downloadLabel}</a>` : "",
      ].filter(Boolean).join("");
      const qualityReportPanel = `
        <div class="flat2-quality-report" data-quality-verdict="${escapeHtml(reportVerdict.key)}" data-vr-hook="vat-209-quality-report">
          ${cockpitHero}
          <div class="quality-cockpit-grid" id="qualityCockpit">
            ${issueBoard}
            ${userFacingReasonsPanel}
            ${manualReviewQueuePanel}
            ${qualityMatrixPanel}
            ${completenessPanel}
            ${epubcheckPanel}
            ${tocPreviewPanel}
            ${magazinePreviewPanel}
            ${metadataPanel}
            ${assetsPanel}
            ${qualitySelectionPanel}
            ${kindleDeliveryPanel}
            <div class="quality-cockpit-panel" id="qualityReportsActionsPanel" data-span="wide">
              <div class="quality-panel-title"><span>Raporty / akcje</span><small>Tylko odczyt</small></div>
              ${qualityLinks ? `<div class="quality-actions" data-readonly="true">${qualityLinks}</div>` : `<div class="quality-empty">Brak danych</div>`}
            </div>
          </div>
        </div>
      `;

      conversionBox.className = `analysis-box ${type}`;
      conversionBox.innerHTML = `
        <strong>Ostatnia konwersja</strong>
        <p>${validationLabel}.</p>
        <div class="analysis-meta">${badges}</div>
        ${qualityReportPanel}
        ${contractHint}
        ${auditHint}
        ${headingRepairHint}
        ${budgetHint}
        ${auditPanel}
      `;
    }

    function renderAnalysis(data) {
      const sourceType = data.source_type || "pdf";
      const analysis = data.analysis || {};
      const publication = data.publication_analysis || {};
      const recommendations = data.recommendations || {};
      const tools = publication.external_tools || {};
      const commandTools = tools.commands || {};
      const javaTool = tools.java || {};
      const tesseractTool = tools.tesseract || {};
      const epubcheck = tools.epubcheck || {};
      const pdfboxTool = tools.pdfbox || {};
      const moduleTools = tools.python_modules || {};
      const tesseractLanguages = (tesseractTool.languages || []).join(", ");

      if (sourceType === "docx") {
        const badges = [
          `akapity ${analysis.paragraph_count ?? 0}`,
          `H1 ${analysis.heading1_count ?? 0}`,
          `H2 ${analysis.heading2_count ?? 0}`,
          `H3 ${analysis.heading3_count ?? 0}`,
          `listy ${analysis.list_count ?? 0}`,
          `tabele ${analysis.table_count ?? 0}`,
          `obrazy ${analysis.image_count ?? 0}`,
          `linki ${analysis.hyperlink_count ?? 0}`,
        ];
        if (publication.profile) badges.push(`profil: ${publication.profile}`);
        if (publication.confidence) badges.push(`pewność ${Math.round(publication.confidence * 100)}%`);
        if (publication.has_toc) badges.push("toc");
        const badgeHtml = badges.map((b) => `<span class="badge">${b}</span>`).join("");
        const toolBadges = [
          commandTools.java ? "java ✓" : "java ✕",
          epubcheck.jar_found && commandTools.java ? "epubcheck ✓" : "epubcheck ✕",
          moduleTools.pdfplumber ? "pdfplumber ✓" : "pdfplumber ✕",
        ].map((b) => `<span class="badge">${b}</span>`).join("");

        analysisBox.innerHTML = `
          <strong>Profil: ${publication.profile || "docx_reflow"}</strong>
          <p>${publication.profile_reason || "DOCX zostanie przetworzony do reflowable EPUB na podstawie struktury stylow i blokow."}</p>
          <div class="analysis-meta">${badgeHtml}</div>
          <p style="margin-top:6px;font-size:.78rem;color:var(--muted);">Narzedzia:</p>
          <div class="analysis-meta">${toolBadges}</div>
        `;

        profileSelect.value = "book";
        syncProfilePreset();
        forceOcrCheckbox.checked = false;
        renderConversionReport({
          profile: publication.profile || "docx_reflow",
          validation: "unavailable",
          validationTool: epubcheck.jar_found && commandTools.java ? "epubcheck" : "niedostepny",
          sections: analysis.estimated_sections || publication.estimated_sections || null,
          assets: analysis.image_count ?? null,
          layout: "reflowable",
          warnings: 0,
          highRiskPages: null,
          highRiskSections: null,
        });
        return;
      }

      const badges = [
        `strony ${analysis.page_count ?? "-"}`,
        `tekst ${analysis.text_pages ?? 0}`,
        `obrazy ${analysis.image_pages ?? 0}`,
        `skany ${analysis.scanned_pages ?? 0}`,
        analysis.has_text_layer ? "warstwa: tak" : "warstwa: nie",
        analysis.has_images ? "obrazy: tak" : "obrazy: nie",
      ];
      if (analysis.layout_heavy) badges.push("układ złożony");
      if (analysis.text_heavy) badges.push("tekst dominujący");
      if (publication.profile) badges.push(`profil: ${publication.profile}`);
      if (publication.confidence) badges.push(`pewność ${Math.round(publication.confidence * 100)}%`);
      if (publication.has_toc) badges.push("TOC");
      if (publication.has_tables) badges.push("tabele");
      if (publication.has_diagrams) badges.push("diagramy");
      if (publication.has_meaningful_images) badges.push("grafiki");
      if (publication.estimated_columns) badges.push(`${publication.estimated_columns} kol`);
      const badgeHtml = badges.map((b) => `<span class="badge">${b}</span>`).join("");
      const extraToolBadges = [
        javaTool.found ? "java ✓" : "java ✕",
        pdfboxTool.jar_found ? "pdfbox ✓" : "pdfbox ✕",
      ].map((b) => `<span class="badge">${b}</span>`).join("");
      const toolBadges = [
        moduleTools.pdfplumber ? "pdfplumber ✓" : "pdfplumber ✕",
        commandTools.tesseract ? "tesseract ✓" : "tesseract ✕",
        commandTools.ocrmypdf ? "ocrmypdf ✓" : "ocrmypdf ✕",
        commandTools.surya ? "surya ✓" : "surya ✕",
        epubcheck.jar_found && commandTools.java ? "epubcheck ✓" : "epubcheck ✕",
      ].map((b) => `<span class="badge">${b}</span>`).join("");

      analysisBox.innerHTML = `
        <strong>Profil: ${publication.profile || "unknown"}</strong>
        <p>${publication.profile_reason || ""} ${publication.fallback_recommendation ? `Fallback: ${publication.fallback_recommendation}.` : ""} ${analysis.is_scanned ? "Wykryto cechy skanu." : "Born-digital lub mieszany."}</p>
        <div class="analysis-meta">${badgeHtml}</div>
        <p style="margin-top:6px;font-size:.78rem;color:var(--muted);">Narzedzia:</p>
        <div class="analysis-meta">${toolBadges}${extraToolBadges}</div>
        <p style="margin-top:6px;font-size:.78rem;color:var(--muted);">
          Java: ${javaTool.path || "brak"}<br>
          Tesseract: ${tesseractTool.path || "brak"}${tesseractLanguages ? `<br>Jezyki OCR: ${tesseractLanguages}` : ""}<br>
          PDFBox: ${pdfboxTool.jar_path || "brak"}<br>
          EPUBCheck: ${epubcheck.jar_path || "brak"}
        </p>
      `;

      profileSelect.value = data.recommended_profile
        ? ({ "Book": "book", "Magazine": "magazine", "Technical/Study": "technical-study", "Preserve Layout": "preserve-layout" }[data.recommended_profile] || "auto-premium")
        : "auto-premium";
      if (publication.ui_profile && publication.ui_profile !== "book") {
        profileSelect.value = publication.ui_profile;
      }
      syncProfilePreset();
      if (recommendations.ocr_needed && recommendations.ocr_needed.required) {
        forceOcrCheckbox.checked = true;
      }
      renderConversionReport({
        profile: publication.profile || null,
        validation: "unavailable",
        validationTool: epubcheck.jar_found && commandTools.java ? "epubcheck" : "niedostepny",
        sections: publication.estimated_sections || null,
        assets: null,
        layout: publication.profile === "fixed_layout_fallback" ? "fixed" : "reflowable",
        warnings: 0,
        highRiskPages: null,
        highRiskSections: null,
      });
    }
