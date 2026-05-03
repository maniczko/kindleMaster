    const PDF_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    const PDF_JS_WORKER_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    const PDF_LIB_URL = "https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js";
    let pdfPreviewLibrariesPromise = null;

    function loadExternalScript(src) {
      return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[data-runtime-src="${src}"]`);
        if (existing && existing.dataset.loaded === "true") {
          resolve();
          return;
        }
        if (existing) {
          existing.addEventListener("load", () => resolve(), { once: true });
          existing.addEventListener("error", () => reject(new Error(`Nie udalo sie zaladowac skryptu: ${src}`)), { once: true });
          return;
        }
        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.crossOrigin = "anonymous";
        script.dataset.runtimeSrc = src;
        script.addEventListener("load", () => {
          script.dataset.loaded = "true";
          resolve();
        }, { once: true });
        script.addEventListener("error", () => reject(new Error(`Nie udalo sie zaladowac skryptu: ${src}`)), { once: true });
        document.head.appendChild(script);
      });
    }

    async function ensurePdfPreviewLibraries() {
      if (window.pdfjsLib && window.PDFLib) {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDF_JS_WORKER_URL;
        return { pdfjsLib: window.pdfjsLib, PDFLib: window.PDFLib };
      }
      if (!pdfPreviewLibrariesPromise) {
        pdfPreviewLibrariesPromise = (async () => {
          if (!window.pdfjsLib) {
            await loadExternalScript(PDF_JS_URL);
          }
          if (window.pdfjsLib) {
            window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDF_JS_WORKER_URL;
          }
          if (!window.PDFLib) {
            await loadExternalScript(PDF_LIB_URL);
          }
          if (!window.pdfjsLib || !window.PDFLib) {
            throw new Error("Brakuje bibliotek PDF preview/export.");
          }
          return { pdfjsLib: window.pdfjsLib, PDFLib: window.PDFLib };
        })().catch((error) => {
          pdfPreviewLibrariesPromise = null;
          throw error;
        });
      }
      return pdfPreviewLibrariesPromise;
    }

    // â”€â”€ Dark mode â”€â”€
    (function () {
      var saved;
      try { saved = localStorage.getItem("km-theme"); } catch (e) {}
      var prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      var theme = saved || (prefersDark ? "dark" : "light");
      document.documentElement.setAttribute("data-theme", theme);
    })();

    var themeToggle = document.getElementById("themeToggle");
    function applyTheme(theme) {
      document.documentElement.setAttribute("data-theme", theme);
      themeToggle.textContent = theme === "dark" ? "☀" : "🌙";
      try { localStorage.setItem("km-theme", theme); } catch (e) {}
    }
    applyTheme(document.documentElement.getAttribute("data-theme"));
    themeToggle.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme");
      applyTheme(current === "dark" ? "light" : "dark");
    });

    // â”€â”€ Collapsible cards â”€â”€
    function syncCardHeaderState(card) {
      if (!card) return;
      var header = card.querySelector(".card-header");
      if (!header) return;
      header.setAttribute("aria-expanded", String(!card.classList.contains("collapsed")));
    }

    function toggleCard(id) {
      var card = document.getElementById(id);
      if (!card) return;
      card.classList.toggle("collapsed");
      syncCardHeaderState(card);
    }

    document.querySelectorAll(".card, .crop-preview-card").forEach(syncCardHeaderState);

    // â”€â”€ App â”€â”€
    const A4_WIDTH = 595.28;
    const A4_HEIGHT = 841.89;
    const $ = (id) => document.getElementById(id);

    const fileInput = $("fileInput");
    const dropZone = $("dropZone");
    const fileInfo = $("fileInfo");
    const fileMark = $("fileMark");
    const fileName = $("fileName");
    const fileSize = $("fileSize");
    const pageCountValue = $("pageCountValue");
    const pageIndicatorValue = $("pageIndicatorValue");
    const pageSizeValue = $("pageSizeValue");
    const renderScaleValue = $("renderScaleValue");
    const cropSizeValue = $("cropSizeValue");
    const savedCropsValue = $("savedCropsValue");
    const prevPageButton = $("prevPageButton");
    const nextPageButton = $("nextPageButton");
    const pageNumberInput = $("pageNumberInput");
    const pageCountInline = $("pageCountInline");
    const goToPageButton = $("goToPageButton");
    const zoomOutButton = $("zoomOutButton");
    const zoomInButton = $("zoomInButton");
    const fitPageButton = $("fitPageButton");
    const fitWidthButton = $("fitWidthButton");
    const zoomResetButton = $("zoomResetButton");
    const zoomStatusLabel = $("zoomStatusLabel");
    const cropButton = $("cropButton");
    const resetCropButton = $("resetCropButton");
    const applyAllPages = $("applyAllPages");
    const profileSelect = $("profileSelect");
    const languageSelect = $("languageSelect");
    const forceOcrCheckbox = $("forceOcrCheckbox");
    const headingRepairCheckbox = $("headingRepairCheckbox");
    const analyzeButton = $("analyzeButton");
    const convertEpubButton = $("convertEpubButton");
    const analysisBox = $("analysisBox");
    const conversionBox = $("conversionBox");
    const exportModeLabel = $("exportModeLabel");
    const statusBox = $("statusBox");
    const statusText = $("statusText");
    const viewportShell = $("viewportShell");
    const emptyState = $("emptyState");
    const canvasStage = $("canvasStage");
    const pdfCanvas = $("pdfCanvas");
    const cropOverlay = $("cropOverlay");
    const cropSelection = $("cropSelection");
    const cropPreviewCanvas = $("cropPreviewCanvas");
    const cropPreviewEmpty = $("cropPreviewEmpty");
    const cropCoverageNote = $("cropCoverageNote");
    const cropReadout = $("cropReadout");
    const previewTitle = $("previewTitle");
    const previewSubtitle = $("previewSubtitle");
    const appLoader = $("appLoader");
    const loaderTitle = $("loaderTitle");
    const loaderMessage = $("loaderMessage");
    const quickUploadButton = $("quickUploadButton");
    const workspaceStateLabel = $("workspaceStateLabel");
    const workspaceGuidance = $("workspaceGuidance");
    const workspaceResultSummary = $("workspaceResultSummary");
    const recentConversionsList = $("recentConversionsList");
    const librarySearchInput = $("librarySearchInput");
    const libraryVerdictFilter = $("libraryVerdictFilter");
    const librarySearchButton = $("librarySearchButton");
    const libraryView = $("libraryView");
    const libraryResultsList = $("libraryResultsList");
    const librarySummary = $("librarySummary");
    const topbarSearchInput = $("topbarSearchInput");
    const recentProjectSelect = $("recentProjectSelect");
    const newConversionButton = $("newConversionButton");
    const dashboardProjectName = $("dashboardProjectName");
    const dashboardPageMetric = $("dashboardPageMetric");
    const dashboardVerdictMetric = $("dashboardVerdictMetric");
    const pagesPanelCount = $("pagesPanelCount");
    const pagesPanelCurrent = $("pagesPanelCurrent");
    const pagesJumpButton = $("pagesJumpButton");
    const profileHint = $("profileHint");
    const presetButtons = Array.from(document.querySelectorAll(".preset-chip[data-profile]"));
    const appViewButtons = Array.from(document.querySelectorAll("[data-app-view]"));
    const workspaceModeButtons = Array.from(document.querySelectorAll("[data-workspace-mode-button]"));
    const previewStage = document.querySelector(".preview-stage");

    const canvasContext = pdfCanvas.getContext("2d", { alpha: false, willReadFrequently: true });
    const cropPreviewContext = cropPreviewCanvas.getContext("2d");

    let selectedFile = null;
    let selectedSourceType = null;
    let pdfBytes = null;
    let pdfJsDoc = null;
    let currentPage = null;
    let currentPageNumber = 1;
    let renderScale = 1;
    let zoomMode = "fit-page";
    let manualZoom = 1;
    let renderTask = null;
    let pageWidthPt = 0;
    let pageHeightPt = 0;
    let cropRectNormalized = null;
    let cropRectsByPage = new Map();
    let busyCounter = 0;
    let isDrawing = false;
    let dragStart = null;
    let busyOverlayTimer = null;
    let busyMessage = "Przetwarzam dokument.";
    let recentConversions = [];
    let libraryItems = [];
    const BASE_CONVERSION_POLL_INTERVAL_MS = 1500;
    const MAX_CONVERSION_POLL_INTERVAL_MS = 5000;
    const CONVERSION_REQUEST_TIMEOUT_MS = 15000;
    const CONVERSION_START_TIMEOUT_MS = 30000;
    const MAX_CONVERSION_POLL_ERRORS = 3;
    const RECENT_CONVERSIONS_LIMIT = 6;
    const LONG_CONVERSION_NOTICE_MS = 20 * 60 * 1000;
    const LONG_CONVERSION_NOTICE_REPEAT_MS = 60 * 1000;
    const PROFILE_DESCRIPTIONS = {
      "auto-premium": "Auto Premium sam wybiera trasę konwersji i traktuje jakość EPUB jako bramkę publikacji.",
      "book": "Książka: reflowable EPUB dla tekstowych PDF/DOCX z rozdziałami, TOC i metadanymi.",
      "magazine": "Magazyn: layout-aware flow dla artykułów, galerii i materiałów editorialowych.",
      "technical-study": "Techniczny: raporty, tabele, referencje i dokumenty z mocną strukturą analityczną.",
      "preserve-layout": "Zachowaj układ: ostrożny fallback, gdy reflow może uszkodzić czytelność źródła.",
    };
    const WORKSPACE_MODE_COPY = {
      preview: "Tryb podglądu: sprawdź dokument, strony i czytelność przed konwersją.",
      crop: "Tryb kadrowania: zaznacz fragment PDF albo użyj ustawienia dla wszystkich stron.",
      result: "Tryb wyniku: po konwersji zobaczysz tu skrót EPUB, a pełna jakość zostaje w prawym cockpit.",
    };

    const pdfRenderCanvasFactory = {
      create(width, height) {
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d", { willReadFrequently: true });
        canvas.width = width;
        canvas.height = height;
        return { canvas, context };
      },
      reset(canvasAndContext, width, height) {
        canvasAndContext.canvas.width = width;
        canvasAndContext.canvas.height = height;
      },
      destroy(canvasAndContext) {
        canvasAndContext.canvas.width = 0;
        canvasAndContext.canvas.height = 0;
        canvasAndContext.canvas = null;
        canvasAndContext.context = null;
      },
    };

    function formatBytes(byteCount) {
      const bytes = Number(byteCount) || 0;
      if (bytes <= 0) return "0 B";
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 2 : 1)} MB`;
    }

    function syncProfilePreset() {
      presetButtons.forEach((button) => {
        button.classList.toggle("is-active", button.dataset.profile === profileSelect.value);
      });
      if (profileHint) {
        profileHint.textContent = PROFILE_DESCRIPTIONS[profileSelect.value] || PROFILE_DESCRIPTIONS["auto-premium"];
      }
    }

    function setWorkspaceMode(mode) {
      const safeMode = WORKSPACE_MODE_COPY[mode] ? mode : "preview";
      if (previewStage) previewStage.dataset.workspaceMode = safeMode;
      workspaceModeButtons.forEach((button) => {
        const active = button.dataset.workspaceModeButton === safeMode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      });
      if (workspaceGuidance) {
        workspaceGuidance.textContent = WORKSPACE_MODE_COPY[safeMode];
      }
    }

    function setStatus(message, type = "info") {
      statusBox.className = `status-box ${type}`;
      statusText.textContent = message;
      busyMessage = message;
      loaderMessage.textContent = message;
    }

    function setBusy(isBusy) {
      busyCounter += isBusy ? 1 : -1;
      busyCounter = Math.max(0, busyCounter);
      updateControlsState();
      updateBusyOverlay();
    }

    function updateBusyOverlay() {
      const busy = busyCounter > 0;
      clearTimeout(busyOverlayTimer);
      if (!busy) {
        appLoader.classList.remove("visible");
        appLoader.setAttribute("aria-hidden", "true");
        return;
      }
      busyOverlayTimer = setTimeout(() => {
        if (busyCounter <= 0) return;
        loaderTitle.textContent = selectedSourceType === "docx" ? "Trwa operacja na DOCX" : "Trwa operacja na PDF";
        loaderMessage.textContent = busyMessage;
        appLoader.classList.add("visible");
        appLoader.setAttribute("aria-hidden", "false");
      }, 180);
    }

    function hasPdfLoaded() {
      return selectedSourceType === "pdf" && Boolean(pdfBytes && pdfJsDoc);
    }

    function detectSourceType(file) {
      const name = (file && file.name ? file.name : "").toLowerCase();
      if (name.endsWith(".pdf") || file.type === "application/pdf") return "pdf";
      if (
        name.endsWith(".docx")
        || file.type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      ) {
        return "docx";
      }
      return "";
    }

    function resetPreviewMetrics() {
      pageSizeValue.textContent = "-";
      renderScaleValue.textContent = "-";
      cropSizeValue.textContent = "-";
      pageNumberInput.value = "1";
      pageWidthPt = 0;
      pageHeightPt = 0;
    }

    function showDocxPreviewState() {
      pdfBytes = null;
      pdfJsDoc = null;
      currentPage = null;
      currentPageNumber = 1;
      cropRectsByPage = new Map();
      cropRectNormalized = null;
      clearSelectionVisual();
      canvasStage.classList.remove("visible");
      cropPreviewCanvas.classList.remove("visible");
      cropPreviewEmpty.style.display = "block";
      cropCoverageNote.className = "crop-coverage-note";
      cropCoverageNote.textContent = "DOCX nie obsluguje podgladu ani kadrowania stron. Analiza i konwersja EPUB dzialaja normalnie.";
      previewTitle.textContent = "Tryb DOCX";
      previewSubtitle.textContent = "Dla DOCX pokazujemy analize struktury dokumentu zamiast podgladu stron PDF.";
      emptyState.style.display = "block";
      emptyState.innerHTML = `
        <strong>Tryb DOCX</strong>
        <p>Podglad stron i kadrowanie sa dostepne tylko dla PDF. Dla DOCX mozesz uruchomic analize struktury i od razu konwersje do EPUB.</p>
      `;
      cropReadout.innerHTML = "<strong>Tryb DOCX</strong><br>Kadrowanie PDF jest niedostepne dla dokumentow Word.";
      resetPreviewMetrics();
      updatePageSummary();
      updateExportModeLabel();
      updateControlsState();
    }

    function updateControlsState() {
      const busy = busyCounter > 0;
      const hasPdf = hasPdfLoaded();
      const pages = pdfJsDoc ? pdfJsDoc.numPages : 0;
      const hasCrop = Boolean(cropRectNormalized);

      prevPageButton.disabled = !hasPdf || busy || currentPageNumber <= 1;
      nextPageButton.disabled = !hasPdf || busy || currentPageNumber >= pages;
      goToPageButton.disabled = !hasPdf || busy;
      pageNumberInput.disabled = !hasPdf || busy;
      zoomOutButton.disabled = !hasPdf || busy;
      zoomInButton.disabled = !hasPdf || busy;
      fitPageButton.disabled = !hasPdf || busy;
      fitWidthButton.disabled = !hasPdf || busy;
      zoomResetButton.disabled = !hasPdf || busy;
      analyzeButton.disabled = !selectedFile || busy;
      convertEpubButton.disabled = !selectedFile || busy;
      cropButton.disabled = !hasPdf || !hasCrop || busy;
      resetCropButton.disabled = !hasCrop || busy;
      applyAllPages.disabled = !hasPdf || busy;
      if (pagesJumpButton) pagesJumpButton.disabled = !hasPdf || busy;
      forceOcrCheckbox.disabled = selectedSourceType === "docx" || busy;

      pageCountValue.textContent = hasPdf ? `${pages}` : "-";
      pageIndicatorValue.textContent = hasPdf ? `${currentPageNumber}` : "-";
      pageCountInline.textContent = hasPdf ? `${pages}` : "0";
      savedCropsValue.textContent = `${cropRectsByPage.size}`;
      updateZoomStatus();
      fitPageButton.classList.toggle("is-active", zoomMode === "fit-page");
      fitWidthButton.classList.toggle("is-active", zoomMode === "fit-width");
      zoomResetButton.classList.toggle("is-active", zoomMode === "manual" && Math.abs(manualZoom - 1) < 0.01);
    }

    function clearSelectionVisual() {
      cropSelection.classList.remove("visible");
      cropSelection.style.left = "0px";
      cropSelection.style.top = "0px";
      cropSelection.style.width = "0px";
      cropSelection.style.height = "0px";
    }

    function updateExportModeLabel() {
      if (selectedSourceType === "docx") {
        exportModeLabel.textContent = "Eksport PDF A4 jest dostepny tylko dla PDF";
        return;
      }
      if (!hasPdfLoaded()) {
        exportModeLabel.textContent = "Eksport: brak pliku";
        return;
      }
      exportModeLabel.textContent = applyAllPages.checked
        ? `Eksport: wszystkie strony wedlug kadru ze strony ${currentPageNumber}`
        : `Eksport: tylko strona ${currentPageNumber}`;
    }

    function getAutoZoomScales(viewportAtOne) {
      const availableWidth = Math.max(280, viewportShell.clientWidth - 80);
      const availableHeight = Math.max(360, viewportShell.clientHeight - 80);
      return {
        fitPage: Math.max(0.25, Math.min(3, Math.min(availableWidth / viewportAtOne.width, availableHeight / viewportAtOne.height) * 0.92)),
        fitWidth: Math.max(0.25, Math.min(3, (availableWidth / viewportAtOne.width) * 0.98)),
      };
    }

    function updateZoomStatus() {
      if (!hasPdfLoaded()) {
        zoomStatusLabel.textContent = "Dopasuj do okna";
        return;
      }
      const percent = `${Math.round(renderScale * 100)}%`;
      if (zoomMode === "fit-width") { zoomStatusLabel.textContent = `Szerokosc - ${percent}`; return; }
      if (zoomMode === "manual") { zoomStatusLabel.textContent = `Manualny - ${percent}`; return; }
      zoomStatusLabel.textContent = `Okno - ${percent}`;
    }

    function normalizedToPdfBox(normalized, widthPt, heightPt) {
      const left = normalized.left * widthPt;
      const right = (normalized.left + normalized.width) * widthPt;
      const top = heightPt - normalized.top * heightPt;
      const bottom = heightPt - (normalized.top + normalized.height) * heightPt;
      return {
        left: Math.max(0, Math.min(left, widthPt)),
        right: Math.max(0, Math.min(right, widthPt)),
        bottom: Math.max(0, Math.min(bottom, heightPt)),
        top: Math.max(0, Math.min(top, heightPt)),
      };
    }

    function updateCropReadout() {
      if (!cropRectNormalized || !pageWidthPt || !pageHeightPt) {
        cropReadout.innerHTML = "<strong>Brak zaznaczenia</strong><br>Zaznacz obszar, ktory ma trafic na strone A4.";
        cropSizeValue.textContent = "-";
        return;
      }
      const box = normalizedToPdfBox(cropRectNormalized, pageWidthPt, pageHeightPt);
      const widthPt = box.right - box.left;
      const heightPt = box.top - box.bottom;
      cropSizeValue.textContent = `${widthPt.toFixed(1)} x ${heightPt.toFixed(1)} pt`;
      cropReadout.innerHTML =
        `<strong>Kadr - strona ${currentPageNumber}</strong><br>` +
        `L ${box.left.toFixed(1)}  B ${box.bottom.toFixed(1)}<br>` +
        `R ${box.right.toFixed(1)}  T ${box.top.toFixed(1)} pt`;
    }

    function updateCropPreview() {
      if (!cropRectNormalized || !pdfCanvas.width || !pdfCanvas.height) {
        cropPreviewCanvas.classList.remove("visible");
        cropPreviewEmpty.style.display = "block";
        cropCoverageNote.className = "crop-coverage-note";
      cropCoverageNote.textContent = "Zaznacz kadr, aby zobaczyc podglad.";
        return;
      }
      const sourceX = Math.max(0, Math.round(cropRectNormalized.left * pdfCanvas.width));
      const sourceY = Math.max(0, Math.round(cropRectNormalized.top * pdfCanvas.height));
      const sourceW = Math.max(1, Math.round(cropRectNormalized.width * pdfCanvas.width));
      const sourceH = Math.max(1, Math.round(cropRectNormalized.height * pdfCanvas.height));
      const maxPreviewWidth = 280;
      const maxPreviewHeight = 190;
      const scale = Math.min(maxPreviewWidth / sourceW, maxPreviewHeight / sourceH, 2.2);
      const targetWidth = Math.max(1, Math.round(sourceW * scale));
      const targetHeight = Math.max(1, Math.round(sourceH * scale));

      cropPreviewCanvas.width = targetWidth;
      cropPreviewCanvas.height = targetHeight;
      cropPreviewContext.setTransform(1, 0, 0, 1, 0, 0);
      cropPreviewContext.clearRect(0, 0, targetWidth, targetHeight);
      cropPreviewContext.drawImage(pdfCanvas, sourceX, sourceY, sourceW, sourceH, 0, 0, targetWidth, targetHeight);
      cropPreviewCanvas.classList.add("visible");
      cropPreviewEmpty.style.display = "none";

      const coverage = cropRectNormalized.width * cropRectNormalized.height;
      const coversMostOfPage = coverage > 0.6 || (cropRectNormalized.width > 0.8 && cropRectNormalized.height > 0.72);
      cropCoverageNote.className = coversMostOfPage ? "crop-coverage-note warn" : "crop-coverage-note";
      cropCoverageNote.textContent = coversMostOfPage
        ? "Ten kadr obejmuje wiekszosc strony - finalny PDF bedzie podobny do oryginalu."
        : "Podglad pokazuje realny wycinek, ktory zostanie osadzony na stronie A4.";
    }

    function updateSelectionFromNormalized() {
      if (!cropRectNormalized) {
        clearSelectionVisual();
        updateCropReadout();
        updateCropPreview();
        updateControlsState();
        return;
      }
      const width = cropOverlay.clientWidth;
      const height = cropOverlay.clientHeight;
      cropSelection.style.left = `${cropRectNormalized.left * width}px`;
      cropSelection.style.top = `${cropRectNormalized.top * height}px`;
      cropSelection.style.width = `${cropRectNormalized.width * width}px`;
      cropSelection.style.height = `${cropRectNormalized.height * height}px`;
      cropSelection.classList.add("visible");
      updateCropReadout();
      updateCropPreview();
      updateControlsState();
    }

    function syncCurrentPageCrop() {
      cropRectNormalized = cropRectsByPage.get(currentPageNumber) || null;
      updateSelectionFromNormalized();
      updateExportModeLabel();
    }

    function updatePageSummary() {
      if (selectedSourceType === "docx") {
        previewTitle.textContent = "Tryb DOCX";
        previewSubtitle.textContent = "Analiza struktury i konwersja EPUB sa dostepne bez podgladu stron.";
        if (pagesPanelCount) pagesPanelCount.textContent = "DOCX";
        if (pagesPanelCurrent) pagesPanelCurrent.textContent = "Analiza struktury";
        if (dashboardPageMetric) dashboardPageMetric.textContent = "DOCX";
        return;
      }
      if (!hasPdfLoaded()) {
        previewTitle.textContent = "Podgląd strony PDF";
        previewSubtitle.textContent = "Po wgraniu pliku mozesz przechodzic miedzy stronami i rysowac kadr na dowolnej z nich.";
        if (pagesPanelCount) pagesPanelCount.textContent = "0";
        if (pagesPanelCurrent) pagesPanelCurrent.textContent = selectedFile ? "Czeka na podgląd" : "Brak pliku";
        if (dashboardPageMetric) dashboardPageMetric.textContent = selectedFile ? "Wczytywanie" : "0 stron";
        return;
      }
      const cropHint = cropRectsByPage.has(currentPageNumber) ? "Ma zapisany kadr." : "Brak kadru.";
      previewTitle.textContent = `Strona ${currentPageNumber} z ${pdfJsDoc.numPages}`;
      previewSubtitle.textContent = `${cropHint} Wlacz eksport wszystkich stron, by uzyc kadru z tej strony dla calego dokumentu.`;
      if (pagesPanelCount) pagesPanelCount.textContent = `${pdfJsDoc.numPages}`;
      if (pagesPanelCurrent) pagesPanelCurrent.textContent = `Strona ${currentPageNumber} z ${pdfJsDoc.numPages}`;
      if (dashboardPageMetric) dashboardPageMetric.textContent = `${pdfJsDoc.numPages} stron`;
    }

    function resetAnalysisBox() {
      analysisBox.innerHTML = `
        <strong>Analiza jeszcze nieuruchomiona</strong>
        <p>Kliknij "Analizuj", aby zobaczyc profil, pewność, narzedzia i rekomendacje dla EPUB.</p>
        <div class="analysis-meta"></div>
      `;
      conversionBox.className = "analysis-box info";
      conversionBox.innerHTML = `
        <strong>Ostatnia konwersja</strong>
        <p>Po wygenerowaniu EPUB zobaczysz tutaj status EPUBCheck, liczbe sekcji, zasoby, typ ukladu i audyt premium.</p>
        <div class="analysis-meta"></div>
      `;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function updateCurrentFileState(file, sourceType) {
      if (!workspaceStateLabel || !file) return;
      workspaceStateLabel.textContent = `${sourceType.toUpperCase()} wybrany: ${file.name}`;
      if (dashboardProjectName) dashboardProjectName.textContent = file.name;
      if (recentProjectSelect) {
        recentProjectSelect.innerHTML = `<option>Ostatnio: ${escapeHtml(file.name)}</option>`;
      }
    }

    async function renderCurrentPage() {
      if (!pdfJsDoc) return;
      currentPage = await pdfJsDoc.getPage(currentPageNumber);
      const viewportAtOne = currentPage.getViewport({ scale: 1 });
      pageWidthPt = viewportAtOne.width;
      pageHeightPt = viewportAtOne.height;

      const autoScales = getAutoZoomScales(viewportAtOne);
      if (zoomMode === "fit-width") renderScale = autoScales.fitWidth;
      else if (zoomMode === "manual") renderScale = Math.max(0.25, Math.min(3, manualZoom));
      else renderScale = autoScales.fitPage;

      const viewport = currentPage.getViewport({ scale: renderScale });
      const deviceScale = window.devicePixelRatio || 1;

      if (renderTask) renderTask.cancel();

      pdfCanvas.width = Math.floor(viewport.width * deviceScale);
      pdfCanvas.height = Math.floor(viewport.height * deviceScale);
      pdfCanvas.style.width = `${viewport.width}px`;
      pdfCanvas.style.height = `${viewport.height}px`;
      canvasStage.style.width = `${viewport.width}px`;
      canvasStage.style.height = `${viewport.height}px`;
      cropOverlay.style.width = `${viewport.width}px`;
      cropOverlay.style.height = `${viewport.height}px`;

      canvasContext.setTransform(deviceScale, 0, 0, deviceScale, 0, 0);
      renderTask = currentPage.render({ canvasContext, viewport, canvasFactory: pdfRenderCanvasFactory });

      try {
        await renderTask.promise;
      } catch (error) {
        if (!error || error.name !== "RenderingCancelledException") throw error;
      }

      emptyState.style.display = "none";
      canvasStage.classList.add("visible");
      pageSizeValue.textContent = `${pageWidthPt.toFixed(1)} x ${pageHeightPt.toFixed(1)} pt`;
      renderScaleValue.textContent = `${Math.round(renderScale * 100)}%`;
      pageNumberInput.value = `${currentPageNumber}`;
      updateZoomStatus();
      updatePageSummary();
      syncCurrentPageCrop();
      updateControlsState();
    }

    async function loadPdf(file) {
      setStatus("Wczytuje PDF i renderuje wybrana strone...", "info");
      selectedFile = file;
      selectedSourceType = "pdf";
      setWorkspaceMode("preview");
      if (workspaceResultSummary) workspaceResultSummary.hidden = true;
      zoomMode = "fit-page";
      manualZoom = 1;
      cropRectsByPage = new Map();
      cropRectNormalized = null;
      clearSelectionVisual();
      resetAnalysisBox();
      fileInfo.classList.add("visible");
      fileMark.textContent = "PDF";
      fileName.textContent = file.name;
      fileSize.textContent = formatBytes(file.size);
      updateCurrentFileState(file, "pdf");
      emptyState.innerHTML = `
        <strong>Brak aktywnego podgladu</strong>
        <p>Po wgraniu PDF aktualna strona zostanie wyrenderowana. Nad nia pojawi sie warstwa do rysowania kadru.</p>
      `;
      updatePageSummary();
      updateExportModeLabel();
      updateControlsState();

      const { pdfjsLib } = await ensurePdfPreviewLibraries();
      pdfBytes = new Uint8Array(await file.arrayBuffer());
      const previewBytes = pdfBytes.slice();
      pdfJsDoc = await pdfjsLib.getDocument({ data: previewBytes }).promise;
      currentPageNumber = 1;

      await renderCurrentPage();
      updateExportModeLabel();
      setStatus("PDF gotowy. Mozesz przechodzic miedzy stronami i rysowac kadr.", "success");
    }

    async function loadDocx(file) {
      setStatus("Wczytuje DOCX i przygotowuje tryb analizy strukturalnej...", "info");
      selectedFile = file;
      selectedSourceType = "docx";
      setWorkspaceMode("preview");
      if (workspaceResultSummary) workspaceResultSummary.hidden = true;
      zoomMode = "fit-page";
      manualZoom = 1;
      resetAnalysisBox();
      fileInfo.classList.add("visible");
      fileMark.textContent = "DOCX";
      fileName.textContent = file.name;
      fileSize.textContent = formatBytes(file.size);
      updateCurrentFileState(file, "docx");
      showDocxPreviewState();
      setStatus("DOCX gotowy. Podglad stron jest wylaczony, ale analiza i konwersja EPUB sa dostepne.", "success");
    }

    function rerenderForZoom(statusMessage) {
      if (!pdfJsDoc) return Promise.resolve();
      setBusy(true);
      return renderCurrentPage()
        .then(() => { if (statusMessage) setStatus(statusMessage, "info"); })
        .finally(() => setBusy(false));
    }

    function setZoomMode(nextMode) {
      if (!pdfJsDoc) return;
      zoomMode = nextMode;
      rerenderForZoom(null).catch((error) => {
        console.error(error);
        setStatus(`Nie udalo sie zmienic zoomu: ${error.message}`, "error");
      });
    }

    function stepZoom(direction) {
      if (!pdfJsDoc) return;
      const baseZoom = zoomMode === "manual" ? manualZoom : renderScale;
      const multiplier = direction > 0 ? 1.18 : 1 / 1.18;
      manualZoom = Math.max(0.25, Math.min(3, baseZoom * multiplier));
      zoomMode = "manual";
      rerenderForZoom(`Zoom: ${Math.round(manualZoom * 100)}%.`).catch((error) => {
        console.error(error);
        setStatus(`Nie udalo sie zmienic zoomu: ${error.message}`, "error");
      });
    }

    function handleFiles(fileList) {
      const [file] = fileList;
      if (!file) return;
      const sourceType = detectSourceType(file);
      if (!sourceType) {
        setStatus("Obslugiwane sa tylko pliki PDF i DOCX.", "error");
        return;
      }
      const loader = sourceType === "pdf" ? loadPdf(file) : loadDocx(file);
      loader.catch((error) => {
        console.error(error);
        setStatus(`Nie udalo sie wczytac dokumentu: ${error.message}`, "error");
      });
    }

    async function goToPage(pageNumber) {
      if (!pdfJsDoc) return;
      const safePageNumber = Math.min(pdfJsDoc.numPages, Math.max(1, pageNumber));
      if (safePageNumber === currentPageNumber && currentPage) {
        pageNumberInput.value = `${currentPageNumber}`;
        return;
      }
      setBusy(true);
      try {
        currentPageNumber = safePageNumber;
        await renderCurrentPage();
        setStatus(`Strona ${currentPageNumber}.`, "info");
      } finally {
        setBusy(false);
      }
    }

    function triggerPageJump() {
      if (!pdfJsDoc) return;
      const value = Number.parseInt(pageNumberInput.value, 10);
      if (!Number.isFinite(value)) { pageNumberInput.value = `${currentPageNumber}`; return; }
      goToPage(value).catch((error) => {
        console.error(error);
        setStatus(`Nie udalo sie otworzyc strony: ${error.message}`, "error");
      });
    }

    function normalizeDragRect(start, end, width, height) {
      const left = Math.max(0, Math.min(start.x, end.x));
      const top = Math.max(0, Math.min(start.y, end.y));
      const right = Math.min(width, Math.max(start.x, end.x));
      const bottom = Math.min(height, Math.max(start.y, end.y));
      return { left, top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
    }

    function getPointerPosition(event) {
      const rect = cropOverlay.getBoundingClientRect();
      const clientX = event.clientX ?? (event.touches && event.touches[0] && event.touches[0].clientX);
      const clientY = event.clientY ?? (event.touches && event.touches[0] && event.touches[0].clientY);
      return {
        x: Math.max(0, Math.min(rect.width, clientX - rect.left)),
        y: Math.max(0, Math.min(rect.height, clientY - rect.top)),
      };
    }

    function startDrawing(event) {
      if (!currentPage || busyCounter > 0) return;
      event.preventDefault();
      isDrawing = true;
      dragStart = getPointerPosition(event);
      clearSelectionVisual();
      cropOverlay.setPointerCapture?.(event.pointerId);
    }

    function moveDrawing(event) {
      if (!isDrawing || !dragStart) return;
      event.preventDefault();
      const dragCurrent = getPointerPosition(event);
      const rect = normalizeDragRect(dragStart, dragCurrent, cropOverlay.clientWidth, cropOverlay.clientHeight);
      cropSelection.style.left = `${rect.left}px`;
      cropSelection.style.top = `${rect.top}px`;
      cropSelection.style.width = `${rect.width}px`;
      cropSelection.style.height = `${rect.height}px`;
      cropSelection.classList.add("visible");
    }

    function finishDrawing(event) {
      if (!isDrawing || !dragStart) return;
      event.preventDefault();
      const end = getPointerPosition(event);
      const rect = normalizeDragRect(dragStart, end, cropOverlay.clientWidth, cropOverlay.clientHeight);
      isDrawing = false;
      dragStart = null;

      if (rect.width < 8 || rect.height < 8) {
        cropRectNormalized = null;
        cropRectsByPage.delete(currentPageNumber);
        clearSelectionVisual();
        updateCropReadout();
        updateControlsState();
        updatePageSummary();
        setStatus("Zaznaczenie zbyt male. Sprobuj narysowac wiekszy kadr.", "info");
        return;
      }

      cropRectNormalized = {
        left: rect.left / cropOverlay.clientWidth,
        top: rect.top / cropOverlay.clientHeight,
        width: rect.width / cropOverlay.clientWidth,
        height: rect.height / cropOverlay.clientHeight,
      };
      cropRectsByPage.set(currentPageNumber, { ...cropRectNormalized });
      updateSelectionFromNormalized();
      updatePageSummary();
      setStatus(`Kadr zapisany dla strony ${currentPageNumber}.`, "success");
    }

    async function cropAndExport() {
      if (!pdfBytes || !cropRectNormalized) {
        setStatus("Najpierw wgraj PDF i zaznacz kadr.", "error");
        return;
      }
      setBusy(true);
      try {
        const { PDFLib } = await ensurePdfPreviewLibraries();
        const { PDFDocument } = PDFLib;
        setStatus("Przetwarzam PDF i osadzam kadr na stronach A4...", "info");
        const sourcePdf = await PDFDocument.load(pdfBytes);
        const outputPdf = await PDFDocument.create();
        const sourcePages = sourcePdf.getPages();
        const currentCrop = cropRectsByPage.get(currentPageNumber);
        if (!currentCrop) throw new Error(`Brak kadru dla strony ${currentPageNumber}.`);

        const pageIndexes = applyAllPages.checked ? sourcePages.map((_, i) => i) : [currentPageNumber - 1];
        for (const pageIndex of pageIndexes) {
          const sourcePage = sourcePages[pageIndex];
          const pageSize = sourcePage.getSize();
          const cropBox = normalizedToPdfBox(currentCrop, pageSize.width, pageSize.height);
          const cropWidth = cropBox.right - cropBox.left;
          const cropHeight = cropBox.top - cropBox.bottom;
          if (cropWidth <= 0 || cropHeight <= 0) throw new Error(`Nieprawidlowy kadr dla strony ${pageIndex + 1}.`);
          const embeddedPage = await outputPdf.embedPage(sourcePage, cropBox);
          const targetPage = outputPdf.addPage([A4_WIDTH, A4_HEIGHT]);
          const scale = Math.min(A4_WIDTH / cropWidth, A4_HEIGHT / cropHeight);
          const fittedWidth = cropWidth * scale;
          const fittedHeight = cropHeight * scale;
          targetPage.drawPage(embeddedPage, { x: (A4_WIDTH - fittedWidth) / 2, y: (A4_HEIGHT - fittedHeight) / 2, xScale: scale, yScale: scale });
        }

        const outputBytes = await outputPdf.save();
        const blob = new Blob([outputBytes], { type: "application/pdf" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        const suffix = applyAllPages.checked
          ? `-cropped-a4-all-from-page-${currentPageNumber}.pdf`
          : `-cropped-a4-page-${currentPageNumber}.pdf`;
        anchor.href = url;
        anchor.download = `${fileName.textContent.replace(/\.pdf$/i, "")}${suffix}`;
        anchor.click();
        URL.revokeObjectURL(url);
        setStatus("Gotowe. Nowy PDF A4 zostal wygenerowany i pobrany.", "success");
      } catch (error) {
        console.error(error);
        setStatus(`Eksport nie powiodl sie: ${error.message}`, "error");
      } finally {
        setBusy(false);
      }
    }

    async function analyzePdfForEpub() {
      if (!selectedFile) { setStatus("Najpierw wgraj dokument.", "error"); return; }
      setBusy(true);
      try {
        const sourceLabel = selectedSourceType === "docx" ? "DOCX" : "PDF";
        setStatus(`Analizuje ${sourceLabel} pod katem EPUB...`, "info");
        const formData = new FormData();
        formData.append("file", selectedFile, selectedFile.name);
        const response = await fetch("/analyze", { method: "POST", body: formData });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || "Analiza nie powiodla sie.");
        renderAnalysis(data);
        setStatus(`Analiza zakonczona. Profil: ${(data.publication_analysis && data.publication_analysis.profile) || data.analysis.recommended_strategy}.`, "success");
      } catch (error) {
        console.error(error);
        setStatus(`Analiza nie powiodla sie: ${error.message}`, "error");
      } finally {
        setBusy(false);
      }
    }

    function downloadBlob(blob, filename) {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    }

    function delay(ms) {
      return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    function downloadFromUrl(url) {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      window.setTimeout(() => anchor.remove(), 0);
    }

    function coerceFiniteNumber(value) {
      const numericValue = Number(value);
      return Number.isFinite(numericValue) ? numericValue : null;
    }

    function normalizePostConversionPayload(jobPayload) {
      const payload = jobPayload && typeof jobPayload === "object" ? jobPayload : {};
      const qualityState = payload.quality_state && typeof payload.quality_state === "object" ? payload.quality_state : null;
      const conversion = payload.conversion && typeof payload.conversion === "object" ? payload.conversion : null;
      const summary = qualityState && qualityState.summary && typeof qualityState.summary === "object" ? qualityState.summary : {};
      const validationState = qualityState && qualityState.validation && typeof qualityState.validation === "object" ? qualityState.validation : {};
      const auditState = qualityState && qualityState.audit && typeof qualityState.audit === "object" ? qualityState.audit : {};
      const headingRepairState = qualityState && qualityState.heading_repair && typeof qualityState.heading_repair === "object" ? qualityState.heading_repair : {};
      const renderBudgetState = qualityState && qualityState.render_budget && typeof qualityState.render_budget === "object" ? qualityState.render_budget : {};
      const sizeBudgetState = qualityState && qualityState.size_budget && typeof qualityState.size_budget === "object" ? qualityState.size_budget : {};
      const metadataHealthState = qualityState && qualityState.metadata_health && typeof qualityState.metadata_health === "object" ? qualityState.metadata_health : null;
      const linkHealthState = qualityState && qualityState.link_health && typeof qualityState.link_health === "object" ? qualityState.link_health : null;
      const visibleJunkState = qualityState && qualityState.visible_junk && typeof qualityState.visible_junk === "object" ? qualityState.visible_junk : null;
      const issueGroupsState = qualityState && qualityState.issue_groups && typeof qualityState.issue_groups === "object" ? qualityState.issue_groups : null;
      const contentMetricsState = qualityState && qualityState.content_metrics && typeof qualityState.content_metrics === "object" ? qualityState.content_metrics : null;
      const textCleanupState = qualityState && qualityState.text_cleanup && typeof qualityState.text_cleanup === "object" ? qualityState.text_cleanup : null;
      const referenceCleanupState = qualityState && qualityState.reference_cleanup && typeof qualityState.reference_cleanup === "object" ? qualityState.reference_cleanup : null;
      const assetSummaryState = qualityState && qualityState.asset_summary && typeof qualityState.asset_summary === "object" ? qualityState.asset_summary : null;
      const tocPreviewState = qualityState && qualityState.toc_preview && typeof qualityState.toc_preview === "object" ? qualityState.toc_preview : null;
      const epubcheckDetailState = qualityState && qualityState.epubcheck_detail && typeof qualityState.epubcheck_detail === "object" ? qualityState.epubcheck_detail : null;
      const metadataSummaryState = qualityState && qualityState.metadata_summary && typeof qualityState.metadata_summary === "object" ? qualityState.metadata_summary : null;
      const qualityCompletenessState = qualityState && qualityState.quality_completeness && typeof qualityState.quality_completeness === "object" ? qualityState.quality_completeness : null;
      const issueGroupBlockers = issueGroupsState && Array.isArray(issueGroupsState.blockers)
        ? issueGroupsState.blockers
        : issueGroupsState && issueGroupsState.blockers && typeof issueGroupsState.blockers === "object" && Array.isArray(issueGroupsState.blockers.items)
          ? issueGroupsState.blockers.items
          : [];
      const qualityBlockersState = qualityState && Array.isArray(qualityState.quality_blockers)
        ? qualityState.quality_blockers
        : issueGroupBlockers.length ? issueGroupBlockers : null;
      const conversionHeadingRepair = conversion && conversion.heading_repair && typeof conversion.heading_repair === "object" ? conversion.heading_repair : {};
      const downloadAvailableState = qualityState && typeof qualityState.download_available === "boolean"
        ? qualityState.download_available
        : Boolean(payload.download_url || (qualityState && qualityState.download_url));
      const releaseVerdictState = qualityState && qualityState.release_verdict ? qualityState.release_verdict : "";
      const releaseBlockedState = qualityState && typeof qualityState.release_blocked === "boolean"
        ? qualityState.release_blocked
        : releaseVerdictState === "release_blocked" || Boolean(qualityBlockersState && qualityBlockersState.length);
      const sendToKindleReadyState = qualityState && typeof qualityState.send_to_kindle_ready === "boolean"
        ? qualityState.send_to_kindle_ready
        : null;
      const sendToKindleBlockersState = qualityState && Array.isArray(qualityState.send_to_kindle_blockers)
        ? qualityState.send_to_kindle_blockers
        : [];
      const userFacingReasonsState = qualityState && Array.isArray(qualityState.user_facing_reasons)
        ? qualityState.user_facing_reasons
        : conversion && Array.isArray(conversion.user_facing_reasons) ? conversion.user_facing_reasons : [];

      return {
        qualityStateUrl: payload.quality_state_url || "",
        downloadUrl: payload.download_url || (qualityState && qualityState.download_url) || "",
        downloadAvailable: downloadAvailableState,
        readingVerdict: qualityState && qualityState.reading_verdict ? qualityState.reading_verdict : "",
        releaseVerdict: releaseVerdictState,
        releaseBlocked: releaseBlockedState,
        qualityBlockers: qualityBlockersState || [],
        sendToKindleReady: sendToKindleReadyState,
        sendToKindleBlockers: sendToKindleBlockersState,
        userFacingVerdict: qualityState && qualityState.user_facing_verdict
          ? qualityState.user_facing_verdict
          : (conversion && conversion.user_facing_verdict) || "",
        userFacingReasons: userFacingReasonsState,
        sourceType: qualityState && qualityState.source_type
          ? qualityState.source_type
          : payload.source_type || (conversion && conversion.source_type) || selectedSourceType || "pdf",
        strategy: summary.strategy || (conversion && conversion.strategy) || null,
        profile: (summary.profile && summary.profile !== "unknown") ? summary.profile : ((conversion && conversion.profile) || null),
        validation: validationState.status || (conversion && conversion.validation) || "unavailable",
        validationTool: validationState.tool || (conversion && conversion.validation_tool) || "unknown",
        outputSizeBytes: coerceFiniteNumber(summary.output_size_bytes ?? payload.output_size_bytes ?? (conversion && conversion.output_size_bytes)),
        sections: coerceFiniteNumber(summary.sections ?? (conversion && conversion.sections)),
        assets: coerceFiniteNumber(summary.assets ?? (conversion && conversion.assets)),
        layout: summary.layout || (conversion && conversion.layout) || null,
        warnings: coerceFiniteNumber(auditState.warning_count ?? (conversion && conversion.warnings)) ?? 0,
        highRiskPages: coerceFiniteNumber(auditState.high_risk_pages ?? (conversion && conversion.high_risk_pages)) ?? 0,
        highRiskSections: coerceFiniteNumber(auditState.high_risk_sections ?? (conversion && conversion.high_risk_sections)) ?? 0,
        warningList: Array.isArray(auditState.warnings)
          ? auditState.warnings
          : Array.isArray(conversion && conversion.warning_list) ? conversion.warning_list : [],
        highRiskPageList: Array.isArray(auditState.high_risk_page_list)
          ? auditState.high_risk_page_list
          : Array.isArray(conversion && conversion.high_risk_page_list) ? conversion.high_risk_page_list : [],
        highRiskSectionList: Array.isArray(auditState.high_risk_section_list)
          ? auditState.high_risk_section_list
          : Array.isArray(conversion && conversion.high_risk_section_list) ? conversion.high_risk_section_list : [],
        alerts: Array.isArray(qualityState && qualityState.alerts) ? qualityState.alerts : [],
        severity: qualityState && qualityState.overall_severity ? qualityState.overall_severity : "",
        qualityAvailable: qualityState ? Boolean(qualityState.quality_available) : null,
        sizeBudget: {
          status: sizeBudgetState.status || (conversion && conversion.size_budget_status) || "unavailable",
          message: sizeBudgetState.message || (conversion && conversion.size_budget_message) || "",
        },
        renderBudget: {
          budgetClass: renderBudgetState.budget_class || (conversion && conversion.render_budget_class) || "",
          attempt: renderBudgetState.attempt || (conversion && conversion.render_budget_attempt) || "",
          targetWarnBytes: coerceFiniteNumber(renderBudgetState.target_warn_bytes ?? (conversion && conversion.target_warn_bytes)) ?? 0,
          targetHardBytes: coerceFiniteNumber(renderBudgetState.target_hard_bytes ?? (conversion && conversion.target_hard_bytes)) ?? 0,
        },
        issueGroups: issueGroupsState || (conversion && conversion.issue_groups) || null,
        contentMetrics: contentMetricsState || (conversion && conversion.content_metrics) || null,
        textCleanup: textCleanupState || (conversion && conversion.text_cleanup) || null,
        referenceCleanup: referenceCleanupState || (conversion && conversion.reference_cleanup) || null,
        assetSummary: assetSummaryState || (conversion && conversion.asset_summary) || null,
        tocPreview: tocPreviewState || (conversion && conversion.toc_preview) || null,
        epubcheckDetail: epubcheckDetailState || (conversion && conversion.epubcheck_detail) || null,
        metadataSummary: metadataSummaryState || (conversion && conversion.metadata_summary) || null,
        metadataHealth: normalizeQualityHealth(metadataHealthState || (conversion && conversion.metadata_health), "Metadane"),
        linkHealth: normalizeQualityHealth(linkHealthState || (conversion && conversion.link_health), "Linki"),
        visibleJunk: normalizeQualityHealth(visibleJunkState || (conversion && conversion.visible_junk), "Widoczne artefakty"),
        qualityCompleteness: normalizeQualityCompleteness(qualityCompletenessState || (conversion && conversion.quality_completeness)),
        headingRepair: {
          status: headingRepairState.status || conversionHeadingRepair.status || "skipped",
          release: headingRepairState.release || conversionHeadingRepair.release || "unavailable",
          tocBefore: coerceFiniteNumber(headingRepairState.toc_before ?? conversionHeadingRepair.toc_before),
          tocAfter: coerceFiniteNumber(headingRepairState.toc_after ?? conversionHeadingRepair.toc_after),
          removed: coerceFiniteNumber(headingRepairState.removed ?? conversionHeadingRepair.removed),
          review: coerceFiniteNumber(headingRepairState.review ?? conversionHeadingRepair.review),
          epubcheck: headingRepairState.epubcheck || conversionHeadingRepair.epubcheck || "unavailable",
          error: headingRepairState.error || conversionHeadingRepair.error || "",
        },
      };
    }

    function applyConversionOutcome(jobPayload, selectedName) {
      const normalized = normalizePostConversionPayload(jobPayload);
      const sourceType = normalized.sourceType || selectedSourceType || "pdf";
      const strategy = normalized.strategy || null;
      const profile = normalized.profile || null;
      const validation = normalized.validation || "unavailable";
      const validationTool = normalized.validationTool || "unknown";
      const outputSize = Number.isFinite(normalized.outputSizeBytes) ? formatBytes(normalized.outputSizeBytes) : "";
      const headingRepair = normalized.headingRepair || { status: "skipped" };
      const tocBefore = Number.isFinite(headingRepair.tocBefore) ? headingRepair.tocBefore : null;
      const tocAfter = Number.isFinite(headingRepair.tocAfter) ? headingRepair.tocAfter : null;
      const verdict = deriveQualityVerdict(normalized);
      renderConversionReport({
        profile,
        validation,
        validationTool,
        sections: Number.isFinite(normalized.sections) ? normalized.sections : null,
        assets: Number.isFinite(normalized.assets) ? normalized.assets : null,
        layout: normalized.layout || null,
        outputSizeBytes: normalized.outputSizeBytes,
        warnings: Number.isFinite(normalized.warnings) ? normalized.warnings : 0,
        highRiskPages: Number.isFinite(normalized.highRiskPages) ? normalized.highRiskPages : 0,
        highRiskSections: Number.isFinite(normalized.highRiskSections) ? normalized.highRiskSections : 0,
        warningList: Array.isArray(normalized.warningList) ? normalized.warningList : [],
        highRiskPageList: Array.isArray(normalized.highRiskPageList) ? normalized.highRiskPageList : [],
        highRiskSectionList: Array.isArray(normalized.highRiskSectionList) ? normalized.highRiskSectionList : [],
        alerts: Array.isArray(normalized.alerts) ? normalized.alerts : [],
        severity: normalized.severity || "",
        qualityAvailable: normalized.qualityAvailable,
        sizeBudget: normalized.sizeBudget,
        renderBudget: normalized.renderBudget,
        verdict,
        qualityStateUrl: normalized.qualityStateUrl,
        downloadUrl: normalized.downloadUrl,
        downloadAvailable: normalized.downloadAvailable,
        readingVerdict: normalized.readingVerdict,
        releaseVerdict: normalized.releaseVerdict,
        releaseBlocked: normalized.releaseBlocked,
        qualityBlockers: normalized.qualityBlockers,
        sendToKindleReady: normalized.sendToKindleReady,
        sendToKindleBlockers: normalized.sendToKindleBlockers,
        userFacingVerdict: normalized.userFacingVerdict,
        userFacingReasons: normalized.userFacingReasons,
        issueGroups: normalized.issueGroups,
        contentMetrics: normalized.contentMetrics,
        textCleanup: normalized.textCleanup,
        referenceCleanup: normalized.referenceCleanup,
        assetSummary: normalized.assetSummary,
        tocPreview: normalized.tocPreview,
        epubcheckDetail: normalized.epubcheckDetail,
        metadataSummary: normalized.metadataSummary,
        metadataHealth: normalized.metadataHealth,
        linkHealth: normalized.linkHealth,
        visibleJunk: normalized.visibleJunk,
        qualityCompleteness: normalized.qualityCompleteness,
        headingRepair: {
          status: headingRepair.status || "skipped",
          release: headingRepair.release || "unavailable",
          tocBefore,
          tocAfter,
          removed: Number.isFinite(headingRepair.removed) ? headingRepair.removed : null,
          review: Number.isFinite(headingRepair.review) ? headingRepair.review : null,
          epubcheck: headingRepair.epubcheck || "unavailable",
          error: headingRepair.error || "",
        },
      });
      rememberRecentConversion({
        jobId: jobPayload.job_id || "",
        status: "ready",
        message: jobPayload.message || "",
        filename: selectedName,
        sourceType,
        elapsedSeconds: jobPayload.elapsed_seconds,
        downloadUrl: normalized.downloadUrl,
        qualityStateUrl: normalized.qualityStateUrl,
        outputSizeBytes: normalized.outputSizeBytes,
        verdict: verdict.label,
        profile: profile || "unknown",
        validation,
      });
      const headingRepairStatusText = headingRepair.status === "applied"
        ? ` TOC repair: ${tocBefore ?? 0}→${tocAfter ?? 0}.`
        : headingRepair.status === "failed"
          ? " Heading repair nie powiodl sie, ale zachowano bazowy EPUB."
          : headingRepair.status === "skipped" && headingRepair.error
            ? ` ${headingRepair.error}.`
            : "";
      const releaseStatusText = normalized.releaseBlocked
        ? ` Wymaga naprawy przed publikacją: ${(normalized.qualityBlockers || []).map((item) => item.code || item.message).filter(Boolean).slice(0, 3).join(", ") || "quality blocker"}.`
        : "";
      const statusType = verdict.tone === "failed" ? "error" : verdict.tone === "ready" ? "success" : "info";
      if (typeof workspaceResultSummary !== "undefined" && workspaceResultSummary) {
        workspaceResultSummary.hidden = false;
        workspaceResultSummary.textContent = `EPUB: ${verdict.label}. ${outputSize ? `Rozmiar: ${outputSize}. ` : ""}${validation ? `Walidacja: ${validation}. ` : ""}${normalized.releaseBlocked ? "Pobranie jest szkicem do kontroli jakości." : "Pobranie jest dostępne w panelu raportów."}`;
      }
      if (typeof setWorkspaceMode === "function") {
        setWorkspaceMode("result");
      }
      setStatus(
        `EPUB wygenerowany i pobrany z ${sourceType.toUpperCase()}.${outputSize ? ` Rozmiar: ${outputSize}.` : ""}${profile ? ` Profil: ${profile}.` : ""}${strategy ? ` Strategia: ${strategy}.` : ""}${validation ? ` Walidacja: ${validation}.` : ""}${headingRepairStatusText}${releaseStatusText}`,
        statusType,
      );
    }

    function isTransientConversionNetworkError(error) {
      if (!error) return false;
      if (error.name === "AbortError") return true;
      const message = `${error.message || error}`.toLowerCase();
      return (
        message.includes("failed to fetch")
        || message.includes("networkerror")
        || message.includes("load failed")
        || message.includes("timed out")
        || message.includes("limit czasu")
        || message.includes("status chwilowo nie odpowiada")
      );
    }

    function isApplicationRestartConversionError(error) {
      if (!error) return false;
      const code = `${error.code || error.error_code || ""}`.toLowerCase();
      const message = `${error.message || error}`.toLowerCase();
      return (
        code === "application_restart"
        || message.includes("restart aplikacji")
        || message.includes("application restart")
      );
    }

    function nextPollDelay(currentDelay, suggestedDelay = null) {
      const numericSuggested = Number.isFinite(Number(suggestedDelay)) ? Number(suggestedDelay) : 0;
      const nextDelay = numericSuggested > 0 ? numericSuggested : Math.round(currentDelay * 1.2);
      return Math.max(
        BASE_CONVERSION_POLL_INTERVAL_MS,
        Math.min(MAX_CONVERSION_POLL_INTERVAL_MS, nextDelay),
      );
    }

    async function fetchJsonWithTimeout(url, options = {}, timeoutMs = CONVERSION_REQUEST_TIMEOUT_MS) {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(url, {
          ...options,
          signal: controller.signal,
          headers: {
            Accept: "application/json",
            ...(options.headers || {}),
          },
        });
        const contentType = response.headers.get("content-type") || "";
        const data = contentType.includes("application/json") ? await response.json() : null;
        const text = data === null ? await response.text() : "";
        return { response, data, text };
      } catch (error) {
        if (error && error.name === "AbortError") {
          throw new Error("Przekroczono limit czasu odpowiedzi lokalnego serwera.");
        }
        throw error;
      } finally {
        window.clearTimeout(timeoutId);
      }
    }

    async function startConversionJob(formData) {
      const { response, data, text } = await fetchJsonWithTimeout(
        "/convert/start",
        { method: "POST", body: formData },
        CONVERSION_START_TIMEOUT_MS,
      );
      if (!response.ok || !data || !data.success) {
        throw new Error((data && data.error) || text || `Nie udalo sie uruchomic konwersji (HTTP ${response.status}).`);
      }
      return data;
    }

    async function pollConversionJob(jobId, sourceLabel) {
      const startedAt = Date.now();
      let pollDelay = BASE_CONVERSION_POLL_INTERVAL_MS;
      let transientErrorCount = 0;
      let lastLongConversionNoticeAt = 0;
      while (true) {
        await delay(pollDelay);
        try {
          const { response, data, text } = await fetchJsonWithTimeout(
            `/convert/status/${encodeURIComponent(jobId)}`,
            {
              method: "GET",
              cache: "no-store",
            },
          );
          if (!response.ok || !data || !data.success) {
            throw new Error((data && data.error) || text || "Nie udalo sie sprawdzic statusu konwersji.");
          }
          transientErrorCount = 0;
          if (data.message) {
            const activeForMs = Date.now() - startedAt;
            if (
              activeForMs >= LONG_CONVERSION_NOTICE_MS
              && Date.now() - lastLongConversionNoticeAt >= LONG_CONVERSION_NOTICE_REPEAT_MS
              && data.status !== "ready"
              && data.status !== "failed"
            ) {
              lastLongConversionNoticeAt = Date.now();
              const elapsedMinutes = Math.max(1, Math.round(activeForMs / 60000));
              setStatus(
                `${data.message} Duzy dokument nadal jest przetwarzany (${elapsedMinutes} min). Nie zamykaj tej karty.`,
                "info",
              );
            } else {
              setStatus(data.message, "info");
            }
          } else {
            const elapsedSeconds = Number.isFinite(data.elapsed_seconds)
              ? data.elapsed_seconds
              : Math.max(1, Math.round((Date.now() - startedAt) / 1000));
            setStatus(`Konwertuje ${sourceLabel} do EPUB... (${elapsedSeconds}s)`, "info");
          }
          if (data.status === "ready") return data;
          if (data.status === "failed") {
            const failure = new Error(data.error || "Konwersja nie powiodla sie.");
            failure.code = data.error_code || "";
            failure.jobStatus = data.status;
            throw failure;
          }
          pollDelay = nextPollDelay(pollDelay, data.poll_after_ms);
        } catch (error) {
          if (!isTransientConversionNetworkError(error)) {
            throw error;
          }
          transientErrorCount += 1;
          if (transientErrorCount >= MAX_CONVERSION_POLL_ERRORS) {
            throw new Error("Polaczenie z lokalnym serwerem konwersji zostalo przerwane. Sprobuj ponownie za chwile.");
          }
          pollDelay = nextPollDelay(pollDelay + transientErrorCount * 800);
          setStatus(
            `Lokalny status konwersji chwilowo nie odpowiada. Ponawiam probe (${transientErrorCount}/${MAX_CONVERSION_POLL_ERRORS})...`,
            "info",
          );
        }
      }
    }

    async function convertPdfToEpub() {
      if (!selectedFile) { setStatus("Najpierw wgraj dokument.", "error"); return; }
      setBusy(true);
      let conversionJobStarted = false;
      try {
        const isPdf = selectedSourceType === "pdf";
        const longRunHint = isPdf && pdfJsDoc && pdfJsDoc.numPages > 250 ? " Duzy dokument - moze potrwac kilka minut." : "";
        setStatus(`Konwertuje ${isPdf ? "PDF" : "DOCX"} do EPUB...${longRunHint}`, "info");
        const formData = new FormData();
        formData.append("file", selectedFile, selectedFile.name);
        formData.append("profile", profileSelect.value);
        formData.append("ocr", forceOcrCheckbox.checked ? "true" : "false");
        formData.append("language", languageSelect.value);
        formData.append("heading_repair", headingRepairCheckbox.checked ? "true" : "false");
        const startPayload = await startConversionJob(formData);
        conversionJobStarted = Boolean(startPayload.job_id);
        setStatus(startPayload.message || `Konwersja ${isPdf ? "PDF" : "DOCX"} ruszyla. Czekam na gotowy EPUB...`, "info");
        const jobPayload = await pollConversionJob(startPayload.job_id, isPdf ? "PDF" : "DOCX");
        if (!jobPayload.download_url || (!jobPayload.quality_state && !jobPayload.conversion)) {
          throw new Error("Serwer zakonczyl konwersje, ale nie zwrocil danych do pobrania EPUB.");
        }
        downloadFromUrl(jobPayload.download_url);
        applyConversionOutcome(jobPayload, selectedFile.name);
      } catch (error) {
        const interruptedByRestart = isApplicationRestartConversionError(error);
        if (!isTransientConversionNetworkError(error) && !interruptedByRestart) {
          console.debug("Kontrolowany blad konwersji zostal pokazany w UI.", error);
        }
        const message = interruptedByRestart
          ? "Lokalna aplikacja zostala zrestartowana w trakcie pracy. Uruchom konwersje ponownie."
          : (error && error.message ? error.message : "Nieznany blad sieci lub backendu.");
        setStatus(
          interruptedByRestart ? message : `Konwersja nie powiodla sie: ${message}`,
          interruptedByRestart ? "info" : "error",
        );
      } finally {
        setBusy(false);
        if (conversionJobStarted) {
          loadConversionHistory({ silent: true });
        }
      }
    }


    function initializeKindleMasterUi() {
    // â”€â”€ Event listeners â”€â”€
    quickUploadButton.addEventListener("click", () => fileInput.click());
    dropZone.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      fileInput.click();
    });
    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
    dropZone.addEventListener("drop", (e) => { e.preventDefault(); dropZone.classList.remove("dragover"); handleFiles(e.dataTransfer.files); });
    fileInput.addEventListener("change", (e) => { handleFiles(e.target.files); e.target.value = ""; });
    prevPageButton.addEventListener("click", () => goToPage(currentPageNumber - 1).catch((e) => setStatus(`Blad: ${e.message}`, "error")));
    nextPageButton.addEventListener("click", () => goToPage(currentPageNumber + 1).catch((e) => setStatus(`Blad: ${e.message}`, "error")));
    goToPageButton.addEventListener("click", triggerPageJump);
    zoomOutButton.addEventListener("click", () => stepZoom(-1));
    zoomInButton.addEventListener("click", () => stepZoom(1));
    fitPageButton.addEventListener("click", () => setZoomMode("fit-page"));
    fitWidthButton.addEventListener("click", () => setZoomMode("fit-width"));
    zoomResetButton.addEventListener("click", () => { manualZoom = 1; setZoomMode("manual"); });
    pageNumberInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); triggerPageJump(); } });
    cropOverlay.addEventListener("pointerdown", (event) => {
      setWorkspaceMode("crop");
      startDrawing(event);
    });
    cropOverlay.addEventListener("pointermove", moveDrawing);
    cropOverlay.addEventListener("pointerup", finishDrawing);
    cropOverlay.addEventListener("pointercancel", finishDrawing);
    cropOverlay.addEventListener("pointerleave", (e) => { if (isDrawing && e.buttons === 0) finishDrawing(e); });
    resetCropButton.addEventListener("click", () => {
      cropRectNormalized = null;
      cropRectsByPage.delete(currentPageNumber);
      clearSelectionVisual();
      updateCropReadout();
      updateControlsState();
      updatePageSummary();
      setStatus(`Kadr strony ${currentPageNumber} wyczyszczony.`, "info");
    });
    applyAllPages.addEventListener("change", updateExportModeLabel);
    profileSelect.addEventListener("change", syncProfilePreset);
    workspaceModeButtons.forEach((button) => {
      button.addEventListener("click", () => setWorkspaceMode(button.dataset.workspaceModeButton));
    });
    appViewButtons.forEach((button) => {
      button.addEventListener("click", () => {
        appViewButtons.forEach((item) => {
          item.classList.toggle("is-active", item === button);
          if (item === button) {
            item.setAttribute("aria-current", "page");
          } else {
            item.removeAttribute("aria-current");
          }
        });
        const target = button.dataset.appView || "dashboard";
        setLibraryViewVisible(target === "library");
        if (target === "library") {
          loadConversionLibrary({ silent: true });
          document.querySelector("[data-vr-hook='km-library-view']")?.scrollIntoView({ behavior: "smooth", block: "start" });
        } else if (target === "dashboard") {
          document.querySelector("[data-vr-hook='vat-209-hero']")?.scrollIntoView({ behavior: "smooth", block: "start" });
        } else if (["quality", "metadata", "export"].includes(target)) {
          document.querySelector("[data-vr-hook='vat-209-insights-rail']")?.scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
          document.querySelector("[data-vr-hook='vat-209-preview-stage']")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
    presetButtons.forEach((button) => {
      button.addEventListener("click", () => {
        profileSelect.value = button.dataset.profile;
        syncProfilePreset();
      });
    });
    analyzeButton.addEventListener("click", analyzePdfForEpub);
    convertEpubButton.addEventListener("click", convertPdfToEpub);
    cropButton.addEventListener("click", cropAndExport);
    if (newConversionButton) {
      newConversionButton.addEventListener("click", () => fileInput.click());
    }
    if (pagesJumpButton) {
      pagesJumpButton.addEventListener("click", () => pageNumberInput.focus());
    }
    if (topbarSearchInput) {
      topbarSearchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          if (librarySearchInput) librarySearchInput.value = topbarSearchInput.value;
          setLibraryViewVisible(true);
          loadConversionLibrary();
          document.querySelector("[data-vr-hook='km-library-view']")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    }
    if (librarySearchButton) {
      librarySearchButton.addEventListener("click", () => {
        setLibraryViewVisible(true);
        loadConversionLibrary();
        document.querySelector("[data-vr-hook='km-library-view']")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    if (librarySearchInput) {
      librarySearchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          setLibraryViewVisible(true);
          loadConversionLibrary();
          document.querySelector("[data-vr-hook='km-library-view']")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    }
    if (libraryVerdictFilter) {
      libraryVerdictFilter.addEventListener("change", () => {
        setLibraryViewVisible(true);
        loadConversionLibrary({ silent: true });
      });
    }
    window.addEventListener("resize", () => {
      if (!currentPage) return;
      clearTimeout(window.__renderResizeTimer);
      window.__renderResizeTimer = setTimeout(() => {
        renderCurrentPage().catch((e) => setStatus(`Nie udalo sie odswiezyc: ${e.message}`, "error"));
      }, 120);
    });

    syncProfilePreset();
    updateControlsState();
    updateExportModeLabel();
    updatePageSummary();
    resetAnalysisBox();
    loadConversionHistory({ silent: true });

    }

    if (document.readyState !== "complete") {
      document.addEventListener("DOMContentLoaded", initializeKindleMasterUi, { once: true });
    } else {
      window.setTimeout(initializeKindleMasterUi, 0);
    }
