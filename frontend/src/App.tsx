import * as React from "react";
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpDown,
  BookOpen,
  CheckCircle2,
  Cloud,
  Scissors,
  Download,
  FileText,
  Gauge,
  KeyRound,
  LibraryBig,
  Loader2,
  LogIn,
  LogOut,
  RefreshCw,
  Save,
  Search,
  Send,
  Settings as SettingsIcon,
  ShieldCheck,
  Upload,
  UserRound,
  Wrench,
} from "lucide-react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "./components/ui/dialog";
import { Progress } from "./components/ui/progress";
import {
  accessTokenFromClient,
  accountFromSession,
  anonymousAccount,
  createKindleMasterAuthClient,
  type AccountState,
  type AuthConfigPayload,
} from "./lib/auth";
import { apiRequestInput, apiUrl } from "./lib/api-base";
import { normalizeQualityState, type NormalizedQualityState, type QualityStatePayload } from "./lib/quality-state";
import type { SupabaseClient } from "@supabase/supabase-js";

type JobStatus = "idle" | "queued" | "running" | "ready" | "failed";
type ViewId = "convert" | "preview" | "library" | "details" | "settings";
type LibrarySort =
  | "updated_desc"
  | "updated_asc"
  | "name_asc"
  | "name_desc"
  | "quality_desc"
  | "quality_asc"
  | "status_asc"
  | "status_desc"
  | "kindle_desc"
  | "kindle_asc";

interface ConversionJobPayload {
  success?: boolean;
  job_id?: string;
  status?: JobStatus | string;
  message?: string;
  title?: string;
  filename?: string;
  source_type?: string;
  elapsed_seconds?: number;
  created_at?: string;
  updated_at?: string;
  source_preview_url?: string;
  download_url?: string;
  download_available?: boolean;
  download_state?: Record<string, unknown>;
  quality_state_url?: string;
  report_json_url?: string;
  report_markdown_url?: string;
  artifacts?: Record<string, unknown>;
  conversion?: Record<string, unknown> | null;
  quality_state?: QualityStatePayload;
  auto_repair?: Record<string, unknown>;
  email_delivery?: Record<string, unknown>;
  output_size_bytes?: number;
  sentry_event_id?: string;
  text_excerpt?: string;
  error?: string;
  error_code?: string;
}

interface UserProfilePayload {
  conversion: {
    default_profile: string;
    default_language: string;
    force_ocr: boolean;
    heading_repair: boolean;
  };
  email_delivery: {
    enabled: boolean;
    host: string;
    port: number;
    security: string;
    username: string;
    from_address: string;
    default_recipient: string;
    max_attachment_bytes: number;
    secret_configured?: boolean;
    secret_registered?: boolean;
  };
}

interface DeliveryConfigPayload {
  enabled?: boolean;
  configured?: boolean;
  send_ready?: boolean;
  provider?: string;
  secret_configured?: boolean;
  secret_registered?: boolean;
  secret_env_name?: string;
  profile_configured?: boolean;
  config_source?: string;
  missing_config?: string[];
}

interface ImportPromptState {
  visible: boolean;
  busy: boolean;
  message: string;
  error: string;
}

const profiles = [
  { value: "auto-premium", label: "Auto Premium" },
  { value: "book", label: "Książka" },
  { value: "magazine", label: "Magazyn" },
  { value: "technical-study", label: "Techniczny" },
  { value: "preserve-layout", label: "Zachowaj układ" },
];

const navigation: Array<{ id: ViewId; label: string; hint: string; icon: React.ElementType }> = [
  { id: "convert", label: "Konwersja", hint: "Wgranie i profil", icon: Upload },
  { id: "library", label: "Biblioteka", hint: "Historia zadań", icon: LibraryBig },
];

const validViews = new Set<ViewId>(["convert", "preview", "library", "details", "settings"]);

const pipelineSteps = ["Wgranie", "Kolejka", "Konwersja", "Audyt", "Bramka jakości", "Artefakty"];
const START_REQUEST_TIMEOUT_MS = 30000;
const STATUS_REQUEST_TIMEOUT_MS = 30000;
const MAX_STATUS_TRANSIENT_FAILURES = 5;
const MAX_STATUS_POLL_ATTEMPTS = 1200;

const defaultProfile: UserProfilePayload = {
  conversion: {
    default_profile: "auto-premium",
    default_language: "pl",
    force_ocr: false,
    heading_repair: true,
  },
  email_delivery: {
    enabled: false,
    host: "",
    port: 587,
    security: "starttls",
    username: "",
    from_address: "",
    default_recipient: "",
    max_attachment_bytes: 52428800,
    secret_configured: false,
    secret_registered: false,
  },
};

function App() {
  const [activeView, setActiveView] = React.useState<ViewId>(initialView);
  const [file, setFile] = React.useState<File | null>(null);
  const [profile, setProfile] = React.useState(defaultProfile.conversion.default_profile);
  const [language, setLanguage] = React.useState(defaultProfile.conversion.default_language);
  const [forceOcr, setForceOcr] = React.useState(defaultProfile.conversion.force_ocr);
  const [headingRepair, setHeadingRepair] = React.useState(defaultProfile.conversion.heading_repair);
  const [activeJob, setActiveJob] = React.useState<ConversionJobPayload | null>(null);
  const [jobs, setJobs] = React.useState<ConversionJobPayload[]>([]);
  const [libraryQuery, setLibraryQuery] = React.useState("");
  const [librarySort, setLibrarySort] = React.useState<LibrarySort>("updated_desc");
  const [settingsForm, setSettingsForm] = React.useState<UserProfilePayload>(defaultProfile);
  const [deliveryConfig, setDeliveryConfig] = React.useState<DeliveryConfigPayload>({});
  const [authConfig, setAuthConfig] = React.useState<AuthConfigPayload>({});
  const [authReady, setAuthReady] = React.useState(false);
  const [account, setAccount] = React.useState<AccountState>(anonymousAccount);
  const [authStatus, setAuthStatus] = React.useState("");
  const [guestMode, setGuestMode] = React.useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage?.getItem(startGuestModeStorageKey()) === "1";
  });
  const [importPrompt, setImportPrompt] = React.useState<ImportPromptState>({
    visible: false,
    busy: false,
    message: "",
    error: "",
  });
  const authClientRef = React.useRef<SupabaseClient | null>(null);
  const authSubscriptionRef = React.useRef<{ unsubscribe: () => void } | null>(null);
  const [isBusy, setIsBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [settingsStatus, setSettingsStatus] = React.useState("");

  const normalizedQuality = React.useMemo(
    () => normalizeQualityState(activeJob?.quality_state ?? null),
    [activeJob?.quality_state],
  );
  const activeStatus = normalizeJobStatus(activeJob?.status);

  React.useEffect(() => {
    void loadAuth();
    void loadJobs();
    void loadSettings();
    return () => {
      authSubscriptionRef.current?.unsubscribe();
      authSubscriptionRef.current = null;
    };
  }, []);

  React.useEffect(() => {
    if (account.authenticated) {
      setGuestMode(false);
      window.localStorage?.removeItem(startGuestModeStorageKey());
      const dismissed = window.localStorage?.getItem(importPromptStorageKey(account.userId));
      setImportPrompt((current) => ({ ...current, visible: dismissed !== "1" }));
      void loadJobs();
      void loadSettings();
    } else {
      setImportPrompt({ visible: false, busy: false, message: "", error: "" });
      void loadJobs();
      void loadSettings();
    }
  }, [account.authenticated, account.userId]);

  React.useEffect(() => {
    const compatWindow = window as unknown as Record<string, unknown>;
    compatWindow.setLibraryViewVisible = (visible = true) => {
      openView(visible ? "library" : "convert");
    };
    compatWindow.loadConversionLibrary = async () => loadLibraryItems("/convert/library?limit=100");
    return () => {
      delete compatWindow.setLibraryViewVisible;
      delete compatWindow.loadConversionLibrary;
    };
  });

  async function loadAuth() {
    try {
      const response = await fetch(apiUrl("/auth/config"), { cache: "no-store" });
      const payload = await response.json();
      const config = (payload.auth ?? {}) as AuthConfigPayload;
      setAuthConfig(config);
      authSubscriptionRef.current?.unsubscribe();
      authSubscriptionRef.current = null;
      const client = createKindleMasterAuthClient(config);
      authClientRef.current = client;
      if (!client) {
        setAccount(anonymousAccount);
        return;
      }
      const { data } = await client.auth.getSession();
      setAccount(accountFromSession(data.session));
      const subscription = client.auth.onAuthStateChange((_event, session) => {
        setAccount(accountFromSession(session));
      });
      authSubscriptionRef.current = subscription.data.subscription;
    } catch {
      setAuthConfig({});
      setAccount(anonymousAccount);
    } finally {
      setAuthReady(true);
    }
  }

  async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
    const token = await accessTokenFromClient(authClientRef.current);
    const resolvedInput = apiRequestInput(input);
    if (!token) return fetch(resolvedInput, init);
    const baseHeaders =
      init.headers instanceof Headers
        ? Object.fromEntries(init.headers.entries())
        : Array.isArray(init.headers)
          ? Object.fromEntries(init.headers)
          : { ...(init.headers as Record<string, string> | undefined) };
    return fetch(resolvedInput, {
      ...init,
      headers: {
        ...baseHeaders,
        Authorization: `Bearer ${token}`,
      },
    });
  }

  async function apiFetchJson<T = Record<string, any>>(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = START_REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await apiFetch(input, { ...init, signal: controller.signal });
      const payload = (await response.json().catch(() => ({}))) as T;
      return { response, payload };
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        throw new Error("Przekroczono limit czasu odpowiedzi lokalnego serwera.");
      }
      throw caught;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function loadJobs() {
    try {
      const response = await apiFetch("/convert/jobs?limit=100", { cache: "no-store" });
      const payload = await response.json();
      const items = Array.isArray(payload.jobs) ? payload.jobs : Array.isArray(payload.items) ? payload.items : [];
      const recentJobs = items.slice(0, 100);
      setJobs(recentJobs);
      const hydratedJobs = await Promise.all(recentJobs.map(loadJobQualityState));
      setJobs(hydratedJobs);
    } catch {
      setJobs([]);
    }
  }

  async function loadLibraryItems(url: string) {
    const response = await apiFetch(url, { cache: "no-store" });
    const payload = await response.json();
    const items = Array.isArray(payload.items) ? payload.items : Array.isArray(payload.jobs) ? payload.jobs : [];
    setJobs(items.slice(0, 100));
    return payload;
  }

  async function openLibrarySearch() {
    const query = libraryQuery.trim();
    await loadLibraryItems(query ? `/convert/search?q=${encodeURIComponent(query)}&limit=100` : "/convert/library?limit=100");
    openView("library");
  }

  async function loadJobQualityState(job: ConversionJobPayload): Promise<ConversionJobPayload> {
    if (job.quality_state || !job.quality_state_url) return job;
    try {
      const response = await apiFetch(job.quality_state_url, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.success || !payload.quality_state) return job;
      return {
        ...job,
        quality_state: payload.quality_state,
        auto_repair: payload.auto_repair ?? payload.quality_state?.auto_repair ?? job.auto_repair,
        email_delivery: payload.email_delivery ?? job.email_delivery,
      };
    } catch {
      return job;
    }
  }

  async function loadSettings() {
    try {
      const profileResponse = await apiFetch("/user/profile", { cache: "no-store" });
      const profilePayload = await profileResponse.json();
      const loadedProfile = normalizeUserProfile(profilePayload.profile);
      setSettingsForm(loadedProfile);
      setProfile(loadedProfile.conversion.default_profile);
      setLanguage(loadedProfile.conversion.default_language);
      setForceOcr(loadedProfile.conversion.force_ocr);
      setHeadingRepair(loadedProfile.conversion.heading_repair);
      const deliveryResponse = await apiFetch("/convert/delivery/config", { cache: "no-store" });
      const deliveryPayload = await deliveryResponse.json();
      setDeliveryConfig(deliveryPayload.delivery ?? {});
    } catch {
      setSettingsForm(defaultProfile);
      setDeliveryConfig({});
    }
  }

  async function saveSettings() {
    setSettingsStatus("");
    const response = await apiFetch("/user/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settingsForm),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      setSettingsStatus(payload.error || "Nie udało się zapisać ustawień");
      return;
    }
    const saved = normalizeUserProfile(payload.profile);
    setSettingsForm(saved);
    setProfile(saved.conversion.default_profile);
    setLanguage(saved.conversion.default_language);
    setForceOcr(saved.conversion.force_ocr);
    setHeadingRepair(saved.conversion.heading_repair);
    setSettingsStatus("Ustawienia zapisane");
    await loadSettings();
  }

  async function signIn(email: string, password: string) {
    setAuthStatus("");
    const client = authClientRef.current;
    if (!client) {
      setAuthStatus("Logowanie Supabase nie jest skonfigurowane.");
      return;
    }
    const { data, error: signInError } = await client.auth.signInWithPassword({ email, password });
    if (signInError) {
      setAuthStatus(signInError.message || "Nie udało się zalogować.");
      return;
    }
    setAccount(accountFromSession(data.session));
    setAuthStatus("Zalogowano.");
  }

  async function signUp(email: string, password: string) {
    setAuthStatus("");
    const client = authClientRef.current;
    if (!client) {
      setAuthStatus("Rejestracja Supabase nie jest skonfigurowana.");
      return;
    }
    const { data, error: signUpError } = await client.auth.signUp({ email, password });
    if (signUpError) {
      setAuthStatus(signUpError.message || "Nie udało się utworzyć konta.");
      return;
    }
    setAccount(accountFromSession(data.session));
    setAuthStatus(data.session ? "Konto utworzone i zalogowane." : "Konto utworzone. Sprawdź email, jeśli Supabase wymaga potwierdzenia.");
  }

  async function signOut() {
    setAuthStatus("");
    await authClientRef.current?.auth.signOut();
    setAccount(anonymousAccount);
    setGuestMode(false);
    window.localStorage?.removeItem(startGuestModeStorageKey());
    setAuthStatus("Wylogowano.");
  }

  function continueLocally() {
    window.localStorage?.setItem(startGuestModeStorageKey(), "1");
    setGuestMode(true);
    setAuthStatus("");
  }

  function openAccountStart() {
    window.localStorage?.removeItem(startGuestModeStorageKey());
    setGuestMode(false);
  }

  async function importLocalHistory() {
    setImportPrompt((current) => ({ ...current, busy: true, error: "", message: "" }));
    try {
      const response = await apiFetch("/user/library/import-local", { method: "POST" });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Nie udało się zaimportować historii.");
      }
      const imported = Number(payload.import?.imported ?? 0);
      const skipped = Number(payload.import?.skipped ?? 0);
      setImportPrompt({ visible: true, busy: false, error: "", message: `Zaimportowano ${imported}, pominięto ${skipped}.` });
      await loadJobs();
    } catch (caught) {
      setImportPrompt((current) => ({
        ...current,
        busy: false,
        error: caught instanceof Error ? caught.message : "Nie udało się zaimportować historii.",
      }));
    }
  }

  function dismissImportPrompt() {
    if (account.userId) {
      window.localStorage?.setItem(importPromptStorageKey(account.userId), "1");
    }
    setImportPrompt({ visible: false, busy: false, message: "", error: "" });
  }

  async function startConversion() {
    if (!file) return;
    setIsBusy(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file, file.name);
      formData.append("profile", profile);
      formData.append("language", language);
      formData.append("ocr", forceOcr ? "true" : "false");
      formData.append("heading_repair", headingRepair ? "true" : "false");

      const { response: startResponse, payload: startPayload } = await apiFetchJson<ConversionJobPayload>(
        "/convert/start",
        { method: "POST", body: formData },
        START_REQUEST_TIMEOUT_MS,
      );
      if (!startResponse.ok || !startPayload.success || !startPayload.job_id) {
        throw new Error(startPayload.error || "Nie udało się uruchomić konwersji.");
      }

      const initialJob: ConversionJobPayload = {
        ...startPayload,
        status: startPayload.status || "queued",
        filename: file.name,
      };
      setActiveJob(initialJob);
      const completedJob = await pollJob(startPayload.job_id, initialJob);
      if (normalizeJobStatus(completedJob.status) === "ready" && completedJob.download_url) {
        await triggerBrowserDownload(completedJob.download_url, apiFetch);
      }
      setActiveView("details");
      syncHash("details");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Nieznany błąd konwersji.";
      setError(message);
      setActiveJob((current) => ({
        ...(current ?? {}),
        status: "failed",
        message,
        error: message,
      }));
    } finally {
      setIsBusy(false);
      void loadJobs();
    }
  }

  async function pollJob(jobId: string, seed: ConversionJobPayload): Promise<ConversionJobPayload> {
    let current = seed;
    let transientFailures = 0;
    for (let attempt = 0; attempt < MAX_STATUS_POLL_ATTEMPTS; attempt += 1) {
      await delay(attempt === 0 ? 800 : 1600);
      let response: Response;
      let payload: ConversionJobPayload;
      try {
        const result = await apiFetchJson<ConversionJobPayload>(
          `/convert/status/${encodeURIComponent(jobId)}`,
          { cache: "no-store" },
          STATUS_REQUEST_TIMEOUT_MS,
        );
        response = result.response;
        payload = result.payload;
      } catch (caught) {
        transientFailures += 1;
        if (transientFailures <= MAX_STATUS_TRANSIENT_FAILURES) {
          current = {
            ...current,
            status: "running",
            message: "Ponawiam połączenie z lokalnym serwerem konwersji...",
          };
          setActiveJob(current);
          continue;
        }
        const message = caught instanceof Error ? caught.message : "Połączenie z lokalnym serwerem konwersji zostało przerwane.";
        throw new Error(message);
      }
      if (!response.ok || !payload.success) {
        if (response.status === 404 || payload.error_code === "application_restart") {
          throw new Error("Lokalna aplikacja została zrestartowana albo zadanie wygasło. Uruchom konwersję ponownie.");
        }
        transientFailures += 1;
        if (transientFailures <= MAX_STATUS_TRANSIENT_FAILURES) {
          current = {
            ...current,
            status: "running",
            message: payload.error || "Ponawiam odczyt statusu konwersji...",
          };
          setActiveJob(current);
          continue;
        }
        throw new Error(payload.error || "Status konwersji jest niedostępny.");
      }
      transientFailures = 0;
      current = {
        ...current,
        ...payload,
        filename: payload.filename || current.filename,
        source_type: payload.source_type || current.source_type,
      };
      setActiveJob(current);
      if (payload.status === "ready") return current;
      if (payload.status === "failed") throw new Error(payload.error || payload.message || "Konwersja nie powiodła się.");
    }
    throw new Error("Konwersja trwa zbyt długo dla interaktywnego podglądu.");
  }

  async function retryConversion(job: ConversionJobPayload) {
    if (!job.job_id) return;
    setIsBusy(true);
    setError("");
    try {
      const { response, payload } = await apiFetchJson<ConversionJobPayload>(
        `/convert/retry/${encodeURIComponent(job.job_id)}`,
        { method: "POST" },
        START_REQUEST_TIMEOUT_MS,
      );
      if (!response.ok || !payload.success || !payload.job_id) {
        throw new Error(payload.error || "Nie udało się ponowić konwersji.");
      }
      const retryJob: ConversionJobPayload = {
        ...payload,
        status: payload.status || "queued",
        filename: payload.filename || job.filename,
        source_type: payload.source_type || job.source_type,
      };
      setActiveJob(retryJob);
      setActiveView("details");
      syncHash("details");
      await pollJob(payload.job_id, retryJob);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Nie udało się ponowić konwersji.";
      setError(message);
      setActiveJob((current) => ({
        ...(current ?? job),
        status: "failed",
        message,
        error: message,
      }));
      throw new Error(message);
    } finally {
      setIsBusy(false);
      void loadJobs();
    }
  }

  function openView(view: ViewId) {
    setActiveView(view);
    syncHash(view);
  }

  function openJobDetails(job: ConversionJobPayload) {
    setActiveJob(job);
    openView("details");
  }

  function applyJobUpdate(job: ConversionJobPayload) {
    setActiveJob((current) => {
      if (!current) return job;
      if (current.job_id === job.job_id) return { ...current, ...job };
      return current;
    });
    setJobs((current) => current.map((item) => (item.job_id === job.job_id ? { ...item, ...job } : item)));
  }

  const canStart = Boolean(file && !isBusy);
  const debugText = JSON.stringify(activeJob ?? { status: "idle" }, null, 2);
  const showStartScreen = !authReady || (!account.authenticated && Boolean(authConfig.enabled && authConfig.configured) && !guestMode);

  if (showStartScreen) {
    return (
      <AuthStartScreen
        authConfig={authConfig}
        authReady={authReady}
        authStatus={authStatus}
        signIn={signIn}
        signUp={signUp}
        continueLocally={continueLocally}
      />
    );
  }

  return (
    <main className="km-app-shell km-premium-shell" data-vr-hook="vat-209-shell">
      <aside className="km-sidebar" aria-label="Nawigacja KindleMaster">
        <button type="button" className="km-brand km-brand-home" aria-label="Strona główna KindleMaster" onClick={() => openView("convert")}>
          <div className="km-brand-mark">KM</div>
          <div>
            <strong>KindleMaster</strong>
            <span>Konsola premium EPUB</span>
          </div>
        </button>
        <nav className="km-nav km-sidebar-nav" aria-label="Główne widoki">
          {navigation.map((item, index) => {
            const Icon = item.icon;
            return (
              <button
                type="button"
                aria-label={item.label}
                className={item.id === activeView ? "is-active" : ""}
                onClick={() => openView(item.id)}
                key={item.id}
              >
                <Icon className="km-nav-icon" aria-hidden="true" />
                <span className="km-nav-copy">
                  <strong>{item.label}</strong>
                  <small>{item.hint}</small>
                </span>
                <span className="km-nav-index">{String(index + 1).padStart(2, "0")}</span>
              </button>
            );
          })}
        </nav>
        <div className="km-sidebar-footer">
          <button
            type="button"
            aria-label="Ustawienia"
            className={activeView === "settings" ? "km-sidebar-settings-button is-active" : "km-sidebar-settings-button"}
            onClick={() => openView("settings")}
          >
            <SettingsIcon aria-hidden="true" />
            <span>
              <strong>Ustawienia</strong>
            </span>
          </button>
        </div>
      </aside>

      <section className="km-main">
        <header className="km-header km-premium-header">
          <div>
            <h1>{viewTitle(activeView)}</h1>
            <p>{viewDescription(activeView)}</p>
            {activeView !== "library" ? (
              <div className="km-global-library-compat" aria-label="Szybkie wyszukiwanie biblioteki">
                <input
                  id="librarySearchInput"
                  type="search"
                  value={libraryQuery}
                  onChange={(event) => setLibraryQuery(event.target.value)}
                  placeholder="Szukaj w bibliotece"
                  aria-label="Szybkie wyszukiwanie biblioteki"
                />
                <Button id="librarySearchButton" type="button" variant="outline" size="sm" onClick={() => void openLibrarySearch()}>
                  Szukaj
                </Button>
              </div>
            ) : null}
          </div>
        </header>

        {!account.authenticated ? (
          <GuestHistoryBanner authConfig={authConfig} />
        ) : importPrompt.visible ? (
          <ImportLocalHistoryBanner
            state={importPrompt}
            importLocalHistory={importLocalHistory}
            dismissImportPrompt={dismissImportPrompt}
          />
        ) : null}

        {activeView === "convert" ? (
          <ConvertView
            file={file}
            setFile={setFile}
            profile={profile}
            setProfile={setProfile}
            language={language}
            setLanguage={setLanguage}
            forceOcr={forceOcr}
            setForceOcr={setForceOcr}
            headingRepair={headingRepair}
            setHeadingRepair={setHeadingRepair}
            isBusy={isBusy}
            canStart={canStart}
            error={error}
            startConversion={startConversion}
            openPreview={() => openView("preview")}
          />
        ) : null}

        {activeView === "preview" ? <PdfPreviewWorkspace file={file} /> : null}

        {activeView === "library" ? (
          <JobsPanel
            jobs={jobs}
            activeJob={activeJob}
            activeJobStatus={activeStatus}
            busy={isBusy}
            onSelect={setActiveJob}
            openDetails={openJobDetails}
            libraryQuery={libraryQuery}
            onLibraryQueryChange={setLibraryQuery}
            librarySort={librarySort}
            onLibrarySortChange={setLibrarySort}
            deliveryConfig={deliveryConfig}
            defaultKindleRecipient={settingsForm.email_delivery.default_recipient}
            apiFetch={apiFetch}
          />
        ) : null}

        {activeView === "details" ? (
          <FileDetailsWorkspace
            job={activeJob}
            jobStatus={activeStatus}
            quality={normalizedQuality}
            busy={isBusy}
            deliveryConfig={deliveryConfig}
            defaultKindleRecipient={settingsForm.email_delivery.default_recipient}
            onJobUpdate={applyJobUpdate}
            reloadJobs={loadJobs}
            retryConversion={retryConversion}
            openLibrary={() => openView("library")}
            apiFetch={apiFetch}
          />
        ) : null}

        {activeView === "settings" ? (
          <SettingsPanel
            settingsForm={settingsForm}
            setSettingsForm={setSettingsForm}
            deliveryConfig={deliveryConfig}
            authConfig={authConfig}
            account={account}
            authStatus={authStatus}
            settingsStatus={settingsStatus}
            saveSettings={saveSettings}
            reloadSettings={loadSettings}
            signOut={signOut}
            openAccountStart={openAccountStart}
          />
        ) : null}
      </section>
    </main>
  );
}

function ConvertView({
  file,
  setFile,
  profile,
  setProfile,
  language,
  setLanguage,
  forceOcr,
  setForceOcr,
  headingRepair,
  setHeadingRepair,
  isBusy,
  canStart,
  error,
  startConversion,
  openPreview,
}: {
  file: File | null;
  setFile: (file: File | null) => void;
  profile: string;
  setProfile: (profile: string) => void;
  language: string;
  setLanguage: (language: string) => void;
  forceOcr: boolean;
  setForceOcr: (value: boolean) => void;
  headingRepair: boolean;
  setHeadingRepair: (value: boolean) => void;
  isBusy: boolean;
  canStart: boolean;
  error: string;
  startConversion: () => void;
  openPreview: () => void;
}) {
  const canPreviewUploadedPdf = isPdfFile(file);
  return (
    <section className="km-view km-convert-view">
      <div className="km-convert-single">
        <Card className="km-upload-panel">
          <CardHeader>
            <CardTitle>Nowa konwersja</CardTitle>
            <CardDescription>
              Wgraj dokument. KindleMaster sam dobierze profil, OCR i naprawę struktury; ręczne ustawienia są tylko dla wyjątków.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <label className="km-drop-zone">
              <Upload aria-hidden="true" />
              <input
                id="fileInput"
                data-testid="conversion-file-input"
                aria-label="Wgraj PDF albo DOCX"
                type="file"
                accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <strong id="fileName">{file ? file.name : "Wybierz plik"}</strong>
              <span>{file ? formatBytes(file.size) : "PDF albo DOCX"}</span>
            </label>

            <details className="km-advanced-conversion">
              <summary>Opcje zaawansowane</summary>
              <div className="km-form-grid">
                <label>
                  <span>Profil</span>
                  <select value={profile} onChange={(event) => setProfile(event.target.value)}>
                    {profiles.map((item) => (
                      <option value={item.value} key={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                  <small>Auto Premium analizuje dokument i wybiera trasę: książka, magazyn, skan, diagramy albo szachy.</small>
                </label>
                <label>
                  <span>Język OCR</span>
                  <select value={language} onChange={(event) => setLanguage(event.target.value)}>
                    <option value="pl">pl</option>
                    <option value="en">en</option>
                  </select>
                  <small>Używany tylko wtedy, gdy potrzebne jest OCR. Dla polskich książek zostaw „pl”.</small>
                </label>
              </div>

              <div className="km-switch-row">
                <label>
                  <input type="checkbox" checked={forceOcr} onChange={(event) => setForceOcr(event.target.checked)} />
                  <span>
                    Wymuś OCR
                    <small>Włącz tylko dla skanu bez tekstu; może znacząco wydłużyć konwersję.</small>
                  </span>
                </label>
                <label>
                  <input type="checkbox" checked={headingRepair} onChange={(event) => setHeadingRepair(event.target.checked)} />
                  <span>
                    Naprawa nagłówków
                    <small>Odbudowuje spis treści i hierarchię rozdziałów; domyślnie warto zostawić włączone.</small>
                  </span>
                </label>
              </div>
            </details>

            {canPreviewUploadedPdf ? (
              <Button type="button" variant="outline" className="km-preview-inline-action" onClick={openPreview}>
                <BookOpen data-icon="inline-start" aria-hidden="true" />
                Podgląd PDF / kadrowanie
              </Button>
            ) : null}

            {error ? (
              <p className="km-delivery-error" role="alert">
                {error}
              </p>
            ) : null}

            <Button id="convertEpubButton" data-testid="start-conversion-button" className="km-wide-action" onClick={startConversion} disabled={!canStart}>
              {isBusy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <Send data-icon="inline-start" aria-hidden="true" />}
              Rozpocznij konwersję
            </Button>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function AuthStartScreen({
  authConfig,
  authReady,
  authStatus,
  signIn,
  signUp,
  continueLocally,
}: {
  authConfig: AuthConfigPayload;
  authReady: boolean;
  authStatus: string;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  continueLocally: () => void;
}) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const configured = Boolean(authConfig.enabled && authConfig.configured);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await signIn(email.trim(), password);
  }

  return (
    <main className="km-start-shell">
      <div className="km-start-grid">
        <section className="km-start-hero" aria-label="KindleMaster">
          <div className="km-start-brand">
            <div className="km-brand-mark">KM</div>
            <span>KindleMaster</span>
          </div>
          <p className="km-eyebrow">Premium EPUB Operations</p>
          <h1>Twoja biblioteka Kindle z historią przypisaną do konta.</h1>
          <p>
            Konwertuj PDF i DOCX do EPUB, zapisuj wyniki w trwałej bibliotece i wysyłaj gotowe artefakty na Kindle
            dopiero po przejściu bramki jakości.
          </p>
          <div className="km-start-feature-grid">
            <StartFeature icon={<LibraryBig aria-hidden="true" />} title="Biblioteka cloud" text="Historia nie znika po kolejnych wersjach aplikacji." />
            <StartFeature icon={<ShieldCheck aria-hidden="true" />} title="Bezpieczne bramki" text="Wysyłka tylko dla artefaktów gotowych do wydania." />
            <StartFeature icon={<BookOpen aria-hidden="true" />} title="EPUB premium" text="Struktura, jakość i naprawy są widoczne w jednym workflow." />
          </div>
        </section>

        <section className="km-start-auth-card" aria-label="Logowanie">
          <div className="km-start-auth-header">
            <div className="km-start-auth-icon">
              <KeyRound aria-hidden="true" />
            </div>
            <div>
              <p className="km-eyebrow">Konto użytkownika</p>
              <h2>Zaloguj się albo załóż konto</h2>
              <p>Po zalogowaniu Biblioteka może być synchronizowana z Supabase.</p>
            </div>
          </div>

          {!authReady ? (
            <div className="km-start-loading">
              <Loader2 aria-hidden="true" />
              <span>Ładowanie konfiguracji konta...</span>
            </div>
          ) : configured ? (
            <form className="km-start-auth-form" onSubmit={handleSubmit}>
              <label>
                Email
                <input aria-label="Email konta" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="twoj@email.pl" />
              </label>
              <label>
                Hasło
                <input
                  aria-label="Hasło konta"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Minimum 6 znaków"
                />
              </label>
              <div className="km-start-auth-actions">
                <Button type="submit" disabled={!email.trim() || !password}>
                  <LogIn data-icon="inline-start" aria-hidden="true" />
                  Zaloguj
                </Button>
                <Button type="button" variant="outline" disabled={!email.trim() || !password} onClick={() => void signUp(email.trim(), password)}>
                  Zarejestruj
                </Button>
              </div>
            </form>
          ) : (
            <div className="km-start-auth-unavailable">
              <AlertTriangle aria-hidden="true" />
              <p>Supabase nie jest jeszcze skonfigurowany. Możesz pracować lokalnie.</p>
            </div>
          )}

          {authStatus ? <p className="km-start-auth-status">{authStatus}</p> : null}

          <div className="km-start-security-strip">
            <Cloud aria-hidden="true" />
            <span>Auth przez Supabase. Sekrety backendowe nie są wysyłane do przeglądarki.</span>
          </div>

          <Button type="button" variant="ghost" className="km-start-local-button" data-testid="continue-locally-button" onClick={continueLocally}>
            Kontynuuj lokalnie
            <ArrowRight data-icon="inline-end" aria-hidden="true" />
          </Button>
        </section>
      </div>
    </main>
  );
}

function StartFeature({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) {
  return (
    <article className="km-start-feature">
      {icon}
      <strong>{title}</strong>
      <span>{text}</span>
    </article>
  );
}

function StatusTile({ icon, title, value, state }: { icon: React.ReactNode; title: string; value: string; state: "ready" | "warning" }) {
  return (
    <article className={`km-status-tile is-${state}`}>
      {icon}
      <span>
        <strong>{title}</strong>
        <small>{value}</small>
      </span>
    </article>
  );
}

function SidebarAccountSummary({
  authConfig,
  account,
  authStatus,
  openAccountStart,
}: {
  authConfig: AuthConfigPayload;
  account: AccountState;
  authStatus: string;
  openAccountStart: () => void;
}) {
  const configured = Boolean(authConfig.enabled && authConfig.configured);
  if (account.authenticated) {
    return (
      <section className="km-account-card" aria-label="Konto użytkownika">
        <button type="button" className="km-account-inline-button" onClick={openAccountStart}>
          <CheckCircle2 aria-hidden="true" />
          <span>
            <strong>Konto</strong>
            <small>{account.email || account.userId}</small>
          </span>
        </button>
        {authStatus ? <p className="km-auth-status">{authStatus}</p> : null}
      </section>
    );
  }

  return (
    <section className="km-account-card km-account-card-compact" aria-label="Konto lokalne">
      <div className="km-account-identity">
        <UserRound aria-hidden="true" />
        <span>
          <strong>Tryb lokalny</strong>
          <small>{configured ? "Konto dostępne na ekranie startowym" : "Supabase wyłączony"}</small>
        </span>
      </div>
      {configured ? (
        <Button variant="outline" size="sm" onClick={openAccountStart}>
          <LogIn data-icon="inline-start" aria-hidden="true" />
          Zaloguj / konto
        </Button>
      ) : null}
    </section>
  );
}

function AccountPanel({
  authConfig,
  account,
  authStatus,
  signIn,
  signUp,
  signOut,
}: {
  authConfig: AuthConfigPayload;
  account: AccountState;
  authStatus: string;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const configured = Boolean(authConfig.enabled && authConfig.configured);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await signIn(email.trim(), password);
  }

  if (account.authenticated) {
    return (
      <section className="km-account-card" aria-label="Konto użytkownika">
        <div className="km-account-identity">
          <UserRound aria-hidden="true" />
          <span>
            <strong>Konto</strong>
            <small>{account.email || account.userId}</small>
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => void signOut()}>
          <LogOut data-icon="inline-start" aria-hidden="true" />
          Wyloguj
        </Button>
        {authStatus ? <p className="km-auth-status">{authStatus}</p> : null}
      </section>
    );
  }

  return (
    <section className="km-account-card" aria-label="Logowanie">
      <div className="km-account-identity">
        <UserRound aria-hidden="true" />
        <span>
          <strong>Konto</strong>
          <small>{configured ? "Historia cloud po logowaniu" : "Tryb lokalny"}</small>
        </span>
      </div>
      {configured ? (
        <form className="km-auth-form" onSubmit={handleSubmit}>
          <input aria-label="Email konta" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="email" />
          <input
            aria-label="Hasło konta"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="hasło"
          />
          <div className="km-auth-actions">
            <Button type="submit" size="sm" disabled={!email.trim() || !password}>
              <LogIn data-icon="inline-start" aria-hidden="true" />
              Zaloguj
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={!email.trim() || !password} onClick={() => void signUp(email.trim(), password)}>
              Zarejestruj
            </Button>
          </div>
        </form>
      ) : (
        <p className="km-auth-status">Supabase nie jest jeszcze skonfigurowany. Biblioteka zostaje lokalna.</p>
      )}
      {authStatus ? <p className="km-auth-status">{authStatus}</p> : null}
    </section>
  );
}

function GuestHistoryBanner({ authConfig }: { authConfig: AuthConfigPayload }) {
  return (
    <section className="km-account-banner">
      <strong>Historia lokalna</strong>
      <span>
        {authConfig.enabled
          ? "Zaloguj się, żeby Biblioteka była przypisana do konta i nie znikała po kolejnych wersjach."
          : "Trwała historia konta wymaga konfiguracji Supabase. Teraz działa bezpieczny lokalny fallback."}
      </span>
    </section>
  );
}

function ImportLocalHistoryBanner({
  state,
  importLocalHistory,
  dismissImportPrompt,
}: {
  state: ImportPromptState;
  importLocalHistory: () => Promise<void>;
  dismissImportPrompt: () => void;
}) {
  return (
    <section className="km-account-banner km-import-banner">
      <div>
        <strong>Import lokalnej historii</strong>
        <span>Możesz przypisać istniejące lokalne konwersje do zalogowanego konta.</span>
        {state.message ? <p className="km-delivery-status">{state.message}</p> : null}
        {state.error ? <p className="km-delivery-error">{state.error}</p> : null}
      </div>
      <div className="km-import-actions">
        <Button size="sm" onClick={() => void importLocalHistory()} disabled={state.busy}>
          {state.busy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <Upload data-icon="inline-start" aria-hidden="true" />}
          Importuj lokalną historię
        </Button>
        <Button variant="outline" size="sm" onClick={dismissImportPrompt}>
          Pomiń
        </Button>
      </div>
    </section>
  );
}

function PdfPreviewWorkspace({ file }: { file: File | null }) {
  const [previewUrl, setPreviewUrl] = React.useState("");
  const isPdf = isPdfFile(file);

  React.useEffect(() => {
    if (!isPdf || !file || typeof URL.createObjectURL !== "function") {
      setPreviewUrl("");
      return;
    }
    const nextUrl = URL.createObjectURL(file);
    setPreviewUrl(nextUrl);
    return () => {
      URL.revokeObjectURL?.(nextUrl);
    };
  }, [file, isPdf]);

  return (
    <section className="km-view km-preview-workspace">
      <Card className="km-pdf-preview-card">
        <CardHeader>
          <CardTitle>Podgląd PDF i kadrowanie</CardTitle>
          <CardDescription>
            Ten widok przywraca szybkie sprawdzenie stron PDF przed konwersją oraz miejsce na kadrowanie do formatu A4.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isPdf && previewUrl ? (
            <object
              className="km-pdf-object"
              title="Podgląd PDF"
              data={previewUrl}
              data-preview-src={previewUrl}
              type="application/pdf"
            >
              <a href={previewUrl}>Otwórz PDF w przeglądarce</a>
            </object>
          ) : (
            <div className="km-empty-state">Wgraj PDF w zakładce Konwersja, aby zobaczyć podgląd i narzędzia kadrowania.</div>
          )}
        </CardContent>
      </Card>

      <Card className="km-crop-card">
        <CardHeader>
          <CardTitle>Kadrowanie</CardTitle>
          <CardDescription>Tryb kadrowania jest dostępny dla PDF przed konwersją.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="km-crop-controls">
            <label>
              <span>Preset</span>
              <select defaultValue="a4">
                <option value="a4">A4 Kindle</option>
                <option value="margins">Usuń marginesy</option>
                <option value="manual">Ręcznie</option>
              </select>
            </label>
            <a className="km-button km-button-primary km-button-md" href={isPdf ? apiUrl("/legacy") : undefined} aria-disabled={!isPdf}>
              Kadruj do A4
            </a>
          </div>
          <p className="km-secret-note">
            Pełne rysowanie kadru z legacy panelu pozostaje dostępne pod /legacy; tutaj wraca najważniejszy punkt pracy:
            podgląd PDF przed startem konwersji i miejsce na crop.
          </p>
        </CardContent>
      </Card>
    </section>
  );
}

function JobStatusPanel({
  job,
  jobStatus,
  quality,
  busy,
  openDetails,
  showDetailsAction = true,
}: {
  job: ConversionJobPayload | null;
  jobStatus: JobStatus;
  quality: NormalizedQualityState;
  busy: boolean;
  openDetails: (job: ConversionJobPayload) => void;
  showDetailsAction?: boolean;
}) {
  const statusVariant = quality.status === "failed" ? "destructive" : quality.status === "needs_review" ? "warning" : "success";
  const badgeVariant = quality.status === "processing" ? "secondary" : statusVariant;
  const activity = buildJobActivity(job, jobStatus, busy, quality);
  const isReady = job?.status === "ready";
  const deliveryWarnings = quality.sendToKindleReady === false ? formatDeliveryBlockers(quality.sendToKindleBlockers) : [];
  const deliveryLabel = !job
    ? "Brak pliku"
    : activity.active
      ? "Czeka na wynik"
      : isReady && deliveryWarnings.length
        ? "Możliwa z uwagami"
        : isReady
          ? "Gotowa"
          : "Niedostępna";
  const outputSize = job?.output_size_bytes ? formatBytes(Number(job.output_size_bytes)) : "brak danych";
  return (
    <Card className="km-status-panel" data-testid="active-job-panel">
      <CardHeader>
        <CardTitle>Aktywne zadanie</CardTitle>
        <CardDescription>{job?.filename || job?.job_id || "Brak aktywnego zadania"}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className={activity.active ? "km-score-line km-score-line-live" : "km-score-line"}>
          {activity.active ? <Loader2 aria-hidden="true" /> : <Gauge aria-hidden="true" />}
          <div>
            <strong>{quality.score || 0}</strong>
            <span>{activity.active ? "Postęp konwersji" : "Ocena jakości"}</span>
          </div>
        </div>
        <Progress value={activity.active ? activity.progress : quality.score} />
        <div className="km-status-grid">
          <Badge variant={activity.active ? "secondary" : badgeVariant}>{activity.badge}</Badge>
          <span>{activity.detail}</span>
        </div>
        <div className="km-active-job-details" aria-label="Informacje o aktywnym zadaniu">
          <div>
            <span>Status</span>
            <strong>{job?.status || "brak"}</strong>
          </div>
          <div>
            <span>Jakość</span>
            <strong>{quality.label}</strong>
          </div>
          <div>
            <span>EPUB</span>
            <strong>{isReady ? "gotowy" : "czeka"}</strong>
          </div>
          <div>
            <span>Rozmiar</span>
            <strong>{outputSize}</strong>
          </div>
          <div>
            <span>Czas</span>
            <strong>{formatSeconds(job?.elapsed_seconds)}</strong>
          </div>
          <div>
            <span>Wysyłka</span>
            <strong>{deliveryLabel}</strong>
          </div>
        </div>
        {deliveryWarnings.length ? (
          <p className="km-active-job-warning" title={deliveryWarnings.join("\n")}>
            {deliveryWarnings[0]}
          </p>
        ) : null}
        {activity.active ? (
          <div className="km-activity-grid" aria-label="Postęp aktywnego zadania">
            <div>
              <span>Teraz</span>
              <strong>{activity.stage}</strong>
            </div>
            <div>
              <span>Minęło</span>
              <strong>{activity.elapsed}</strong>
            </div>
            <div>
              <span>Pozostało</span>
              <strong>{activity.remaining}</strong>
            </div>
          </div>
        ) : null}
        {showDetailsAction && job?.job_id ? (
          <Button variant="outline" size="sm" onClick={() => openDetails(job)}>
            <FileText data-icon="inline-start" aria-hidden="true" />
            Szczegóły pliku
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function StatusPanel({
  job,
  jobStatus,
  quality,
  busy,
}: {
  job: ConversionJobPayload | null;
  jobStatus: JobStatus;
  quality: NormalizedQualityState;
  busy: boolean;
}) {
  const statusVariant = quality.status === "failed" ? "destructive" : quality.status === "needs_review" ? "warning" : "success";
  const badgeVariant = quality.status === "processing" ? "secondary" : statusVariant;
  return (
    <Card className="km-status-panel">
      <CardHeader>
        <CardTitle>Aktywne zadanie</CardTitle>
        <CardDescription>{job?.filename || job?.job_id || "Brak aktywnego zadania"}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="km-score-line">
          <Gauge aria-hidden="true" />
          <div>
            <strong>{quality.score || 0}</strong>
            <span>Ocena jakości</span>
          </div>
        </div>
        <Progress value={quality.score} />
        <div className="km-status-grid">
          <Badge variant={busy || jobStatus === "running" || jobStatus === "queued" ? "secondary" : badgeVariant}>
            {busy ? "Przetwarzanie" : quality.label}
          </Badge>
          <span>{job?.message || quality.detail}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function QualityWorkspace({
  quality,
  raw,
  job,
  jobStatus,
  debugText,
  error,
}: {
  quality: NormalizedQualityState;
  raw: QualityStatePayload | null;
  job: ConversionJobPayload | null;
  jobStatus: JobStatus;
  debugText: string;
  error: string;
}) {
  return (
    <section className="km-view">
      <section className="km-pipeline-strip" aria-label="Etapy konwersji">
        {pipelineSteps.map((step, index) => (
          <div className={pipelineStepClass(index, jobStatus)} key={step}>
            <span>{index + 1}</span>
            <strong>{step}</strong>
          </div>
        ))}
      </section>
      <QualityReport quality={quality} raw={raw} />
      {error ? <DebugPanel activeJob={job} debugText={debugText} error={error} /> : null}
    </section>
  );
}

function QualityReport({ quality, raw }: { quality: NormalizedQualityState; raw: QualityStatePayload | null }) {
  const rawSummary = raw?.summary ?? {};
  return (
    <div className="km-panel-grid">
      <Card>
        <CardHeader>
          <CardTitle>Bramka jakości</CardTitle>
          <CardDescription>{quality.detail}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="km-decision-row">
            <Badge variant={quality.status === "failed" ? "destructive" : quality.status === "needs_review" ? "warning" : "success"}>
              {quality.label}
            </Badge>
            <strong>{quality.score}/100</strong>
          </div>
          <Progress value={quality.score} />
          <MetricRows
            rows={[
              ["Czytanie", String(raw?.reading_verdict ?? "brak danych")],
              ["Wydanie", String(raw?.release_verdict ?? "brak danych")],
              ["Wysyłka", raw?.sendable === true ? "tak" : "niepotwierdzone"],
              ["Kindle-ready", raw?.kindle_ready === true ? "tak" : "niepotwierdzone"],
              ["Premium-ready", raw?.premium_ready === true ? "tak" : "niepotwierdzone"],
              ["Profil", String(rawSummary.profile ?? "brak danych")],
            ]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Kolejka kontroli</CardTitle>
          <CardDescription>Tylko blokery i ostrzeżenia, które wpływają na decyzję wydania.</CardDescription>
        </CardHeader>
        <CardContent>
          <IssueList title="Blokery" items={quality.blockers} empty="Brak blokerów" />
          <IssueList title="Ostrzeżenia" items={quality.warnings} empty="Brak ostrzeżeń" />
        </CardContent>
      </Card>
    </div>
  );
}

function LibraryJobRow({
  job,
  deliveryConfig,
  defaultKindleRecipient,
  onSelect,
  openDetails,
  apiFetch,
}: {
  job: ConversionJobPayload;
  deliveryConfig: DeliveryConfigPayload;
  defaultKindleRecipient: string;
  onSelect: (job: ConversionJobPayload) => void;
  openDetails: (job: ConversionJobPayload) => void;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}) {
  const configuredRecipient = defaultKindleRecipient.trim();
  const [deliveryStatus, setDeliveryStatus] = React.useState("");
  const [deliveryError, setDeliveryError] = React.useState("");
  const [deliveryBusy, setDeliveryBusy] = React.useState(false);
  const jobStatus = normalizeJobStatus(job.status);
  const jobIsProcessing = isLibraryJobProcessing(job);
  const processingDetail = libraryProcessingDetail(job, jobStatus);
  const quality = normalizeQualityState(job.quality_state ?? null);
  const jobLabel = jobDisplayName(job) || "zadania";
  const sourcePreviewUrl = apiUrl(String(job.source_preview_url || "").trim());
  const recipientConfigured = Boolean(configuredRecipient);
  const deliveryQualityWarnings = quality.sendToKindleReady === false ? formatDeliveryBlockers(quality.sendToKindleBlockers) : [];
  const canSendToKindle = Boolean(job.job_id && job.status === "ready" && deliveryConfig.configured && recipientConfigured);
  const deliveryBlockers = !job.job_id
    ? ["Brak identyfikatora zadania."]
    : job.status !== "ready"
      ? ["EPUB nie jest jeszcze gotowy do wysyłki."]
      : !deliveryConfig.configured
        ? ["Skonfiguruj SMTP w Ustawieniach."]
        : !recipientConfigured
          ? ["Ustaw domyślny adres Kindle w Ustawieniach."]
        : [];
  const primaryDeliveryBlocker = deliveryBlockers[0] ?? "";
  const qualityTooltip = quality.sendToKindleBlockers.length
    ? formatDeliveryBlockers(quality.sendToKindleBlockers).join("\n")
    : quality.detail;
  const qualityScore = formatQualityScore(job.quality_state, quality.score);
  const kindleStatus = canSendToKindle
    ? deliveryQualityWarnings.length
      ? "Wysyłka z uwagami jakości"
      : "Wysyłka gotowa"
    : primaryDeliveryBlocker || "Wysyłka niedostępna";
  const sendButtonTitle = deliveryQualityWarnings.length
    ? `Wyślij na ${maskVisibleEmail(configuredRecipient)}. Uwagi jakości: ${deliveryQualityWarnings.join(" ")}`
    : `Wyślij na ${maskVisibleEmail(configuredRecipient)}`;

  async function sendToKindle(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!job.job_id || !configuredRecipient || !canSendToKindle) return;
    onSelect(job);
    setDeliveryBusy(true);
    setDeliveryStatus("");
    setDeliveryError("");
    try {
      const response = await apiFetch(`/convert/delivery/${encodeURIComponent(job.job_id)}/email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: configuredRecipient }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Wysyłka mailowa nie powiodła się.");
      }
      setDeliveryStatus(`Wysłano do ${payload.delivery?.masked_recipient || "odbiorcy"}`);
    } catch (caught) {
      setDeliveryError(caught instanceof Error ? caught.message : "Wysyłka mailowa nie powiodła się.");
    } finally {
      setDeliveryBusy(false);
    }
  }

  return (
    <div role="row" className="km-job-row" key={job.job_id || job.filename}>
      <button
        type="button"
        className="km-job-file-button"
        title="Otwórz szczegóły pliku"
        onClick={() => {
          onSelect(job);
          openDetails(job);
        }}
      >
        <FileText data-icon="inline-start" aria-hidden="true" />
        <span>{jobLabel}</span>
      </button>
      <span>
        {jobIsProcessing ? (
          <span className="km-library-status is-processing" role="status" aria-live="polite">
            <Loader2 data-icon="inline-start" aria-hidden="true" />
            <span>Przetwarzanie</span>
            <small>{processingDetail}</small>
          </span>
        ) : (
          <Badge variant={jobStatus === "ready" ? "success" : jobStatus === "failed" ? "destructive" : "secondary"}>
            {libraryStatusLabel(jobStatus)}
          </Badge>
        )}
      </span>
      <span>
        <div
          className={`km-quality-score km-quality-score-${quality.status}`}
          title={qualityTooltip}
          aria-label={`Jakość konwersji: ${qualityScore.label}. ${quality.label}`}
        >
          <Gauge aria-hidden="true" />
          <span>
            <strong>{qualityScore.value}</strong>
            <small>{qualityScore.scale}</small>
          </span>
        </div>
      </span>
      <div className="km-job-send-cell">
        {canSendToKindle ? null : (
          <div className="km-kindle-status is-blocked" title={[...deliveryQualityWarnings, ...deliveryBlockers].join("\n") || undefined}>
            <Send data-icon="inline-start" aria-hidden="true" />
            <span>{kindleStatus}</span>
          </div>
        )}
        {canSendToKindle ? (
          <form className="km-job-send-form" onSubmit={sendToKindle}>
            <Button type="submit" size="sm" disabled={deliveryBusy || !configuredRecipient} title={sendButtonTitle}>
              {deliveryBusy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <Send data-icon="inline-start" aria-hidden="true" />}
              Wyślij na Kindle
            </Button>
          </form>
        ) : (
          <div className="km-job-send-placeholder" aria-hidden="true" />
        )}
        {deliveryStatus ? <p className="km-delivery-status">{deliveryStatus}</p> : null}
        {deliveryError ? <p className="km-delivery-error">{deliveryError}</p> : null}
      </div>
      <div className="km-job-actions">
        {sourcePreviewUrl ? (
          <a className="km-button km-button-outline km-button-sm" href={sourcePreviewUrl} target="_blank" rel="noreferrer" title={`Podgląd PDF: ${jobLabel}`}>
            <BookOpen data-icon="inline-start" aria-hidden="true" />
            PDF
          </a>
        ) : null}
        {job.download_url ? (
          <a className="km-button km-button-outline km-button-sm" href={apiUrl(job.download_url)}>
            <Download data-icon="inline-start" aria-hidden="true" />
            EPUB
          </a>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            onSelect(job);
            openDetails(job);
          }}
        >
          <FileText data-icon="inline-start" aria-hidden="true" />
          Otwórz
        </Button>
      </div>
    </div>
  );
}

function JobsPanel({
  jobs,
  activeJob,
  activeJobStatus,
  busy,
  onSelect,
  openDetails,
  libraryQuery,
  onLibraryQueryChange,
  librarySort,
  onLibrarySortChange,
  deliveryConfig,
  defaultKindleRecipient,
  apiFetch,
}: {
  jobs: ConversionJobPayload[];
  activeJob: ConversionJobPayload | null;
  activeJobStatus: JobStatus;
  busy: boolean;
  onSelect: (job: ConversionJobPayload) => void;
  openDetails: (job: ConversionJobPayload) => void;
  libraryQuery: string;
  onLibraryQueryChange: (value: string) => void;
  librarySort: LibrarySort;
  onLibrarySortChange: (value: LibrarySort) => void;
  deliveryConfig: DeliveryConfigPayload;
  defaultKindleRecipient: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}) {
  const visibleJobs = mergeLibraryJobsWithActiveJob(jobs, activeJob, activeJobStatus, busy);
  const filteredJobs = sortLibraryJobs(filterLibraryJobs(visibleJobs, libraryQuery), librarySort);
  const compatibilityLibraryText = filteredJobs.length
    ? filteredJobs.map((job) => jobDisplayName(job)).join(" ")
    : "Brak wyników biblioteki";
  return (
    <section className="km-view">
      <Card>
        <CardHeader>
          <CardTitle>Biblioteka</CardTitle>
          <CardDescription>Ostatnie lokalne konwersje, szybkie pobranie EPUB i wysyłka na Kindle bez osobnej zakładki.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="km-library-compat-controls" aria-label="Szybkie wyszukiwanie biblioteki">
            <input
              type="search"
              value={libraryQuery}
              onChange={(event) => onLibraryQueryChange(event.target.value)}
              placeholder="Nazwa pliku, status albo jakość"
              aria-label="Szybkie wyszukiwanie biblioteki"
            />
            <Button id="librarySearchButton" type="button" variant="outline" size="sm">
              Szukaj
            </Button>
          </div>
          <div id="recentConversionsList" className="km-screen-reader-status">
            {compatibilityLibraryText}
          </div>
          <div id="libraryResultsList" className="km-screen-reader-status">
            {compatibilityLibraryText}
          </div>
          <div className="km-library-toolbar" aria-label="Narzędzia Biblioteki">
            <label className="km-library-control km-library-search">
              <span>
                <Search data-icon="inline-start" aria-hidden="true" />
                Szukaj
              </span>
              <input
                id="librarySearchInput"
                type="search"
                value={libraryQuery}
                onChange={(event) => onLibraryQueryChange(event.target.value)}
                placeholder="Nazwa pliku, status albo jakość"
                aria-label="Szukaj w bibliotece"
              />
            </label>
            <span className="km-library-count" aria-live="polite">
              {filteredJobs.length} z {visibleJobs.length}
            </span>
          </div>
          <div className="km-job-table" role="table" aria-label="Ostatnie zadania">
            <div role="row" className="km-job-row is-header">
              <SortHeaderButton
                label="Plik"
                active={librarySort === "name_asc" || librarySort === "name_desc"}
                direction={librarySort === "name_desc" ? "desc" : "asc"}
                onClick={() => onLibrarySortChange(nextLibrarySort(librarySort, "name"))}
              />
              <SortHeaderButton
                label="Status"
                active={librarySort === "status_asc" || librarySort === "status_desc"}
                direction={librarySort === "status_desc" ? "desc" : "asc"}
                onClick={() => onLibrarySortChange(nextLibrarySort(librarySort, "status"))}
              />
              <SortHeaderButton
                label="Jakość"
                active={librarySort === "quality_asc" || librarySort === "quality_desc"}
                direction={librarySort === "quality_asc" ? "asc" : "desc"}
                onClick={() => onLibrarySortChange(nextLibrarySort(librarySort, "quality"))}
              />
              <SortHeaderButton
                label="Kindle"
                active={librarySort === "kindle_asc" || librarySort === "kindle_desc"}
                direction={librarySort === "kindle_asc" ? "asc" : "desc"}
                onClick={() => onLibrarySortChange(nextLibrarySort(librarySort, "kindle"))}
              />
              <span>Akcja</span>
            </div>
            {filteredJobs.length ? (
              filteredJobs.map((job) => (
                <LibraryJobRow
                  job={job}
                  deliveryConfig={deliveryConfig}
                  defaultKindleRecipient={defaultKindleRecipient}
                  onSelect={onSelect}
                  openDetails={openDetails}
                  apiFetch={apiFetch}
                  key={job.job_id || job.filename}
                />
              ))
            ) : (
              <div className="km-empty-state">{visibleJobs.length ? "Brak wyników dla tego wyszukiwania." : "Brak ostatnich zadań."}</div>
            )}
          </div>
        </CardContent>
      </Card>
    </section>
  );
}

function SortHeaderButton({
  label,
  active,
  direction,
  onClick,
}: {
  label: string;
  active: boolean;
  direction: "asc" | "desc";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={["km-job-sort-header", active ? "is-active" : ""].filter(Boolean).join(" ")}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={onClick}
    >
      <span>{label}</span>
      <ArrowUpDown aria-hidden="true" />
    </button>
  );
}

function FileDetailsWorkspace({
  job,
  jobStatus,
  quality,
  busy,
  deliveryConfig,
  defaultKindleRecipient,
  onJobUpdate,
  reloadJobs,
  retryConversion,
  openLibrary,
  apiFetch,
}: {
  job: ConversionJobPayload | null;
  jobStatus: JobStatus;
  quality: NormalizedQualityState;
  busy: boolean;
  deliveryConfig: DeliveryConfigPayload;
  defaultKindleRecipient: string;
  onJobUpdate: (job: ConversionJobPayload) => void;
  reloadJobs: () => Promise<void>;
  retryConversion: (job: ConversionJobPayload) => Promise<void>;
  openLibrary: () => void;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}) {
  const [repairBusy, setRepairBusy] = React.useState(false);
  const [repairStatus, setRepairStatus] = React.useState("");
  const [repairError, setRepairError] = React.useState("");
  const [kindleEmail, setKindleEmail] = React.useState("");
  const [deliveryStatus, setDeliveryStatus] = React.useState("");
  const [deliveryError, setDeliveryError] = React.useState("");
  const [deliveryDiagnostics, setDeliveryDiagnostics] = React.useState<Record<string, unknown> | null>(null);
  const [deliveryBusy, setDeliveryBusy] = React.useState(false);
  const configuredRecipient = defaultKindleRecipient.trim();
  const autoRepair = normalizeAutoRepair(job?.auto_repair ?? job?.quality_state?.auto_repair);
  const sourcePreviewUrl = apiUrl(String(job?.source_preview_url || "").trim());
  React.useEffect(() => {
    setKindleEmail(configuredRecipient);
  }, [configuredRecipient]);
  React.useEffect(() => {
    setDeliveryDiagnostics(null);
  }, [job?.job_id]);
  const recipientConfigured = Boolean(configuredRecipient);
  const deliveryBlockers = [
    ...(!job?.job_id ? ["Brak identyfikatora zadania."] : []),
    ...(job?.job_id && job.status !== "ready" ? ["EPUB nie jest jeszcze gotowy do wysyłki."] : []),
    ...(deliveryConfig.configured ? [] : ["Skonfiguruj SMTP w Ustawieniach."]),
    ...(recipientConfigured ? [] : ["Ustaw domyślny adres Kindle w Ustawieniach."]),
  ];
  const canSendToKindle = Boolean(job?.job_id && job.status === "ready" && deliveryConfig.configured && recipientConfigured);
  const artifactRows = buildArtifactRows(job, quality);
  const persistedDiagnostics =
    job?.email_delivery?.diagnostics && typeof job.email_delivery.diagnostics === "object"
      ? (job.email_delivery.diagnostics as Record<string, unknown>)
      : null;
  const activeDeliveryDiagnostics = deliveryDiagnostics ?? persistedDiagnostics;
  const canRetryConversion = Boolean(
    job?.job_id
      && job.status === "failed"
      && (job.error_code === "application_restart" || job.artifacts?.input),
  );
  async function runRetryConversion() {
    if (!job || !canRetryConversion) return;
    setRepairBusy(true);
    setRepairStatus("Ponawiam rozczytanie z zachowanego pliku wejściowego...");
    setRepairError("");
    try {
      await retryConversion(job);
    } catch (caught) {
      setRepairError(caught instanceof Error ? caught.message : "Nie udało się ponowić rozczytania.");
    } finally {
      setRepairBusy(false);
    }
  }

  async function runRepair() {
    if (!job?.job_id) return;
    setRepairBusy(true);
    setRepairStatus("");
    setRepairError("");
    try {
      const response = await apiFetch(`/convert/repair/${encodeURIComponent(job.job_id)}`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Naprawa EPUB nie powiodła się.");
      }
      const updatedJob: ConversionJobPayload = {
        ...(payload.job ?? job),
        quality_state: payload.quality_state ?? job.quality_state,
        auto_repair: payload.auto_repair ?? payload.quality_state?.auto_repair,
      };
      onJobUpdate(updatedJob);
      setRepairStatus(autoRepairMessage(payload.auto_repair));
      await reloadJobs();
    } catch (caught) {
      setRepairError(caught instanceof Error ? caught.message : "Naprawa EPUB nie powiodła się.");
    } finally {
      setRepairBusy(false);
    }
  }

  async function sendToKindle(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!job?.job_id || !kindleEmail.trim() || !canSendToKindle) return;
    setDeliveryBusy(true);
    setDeliveryStatus("");
    setDeliveryError("");
    try {
      const response = await apiFetch(`/convert/delivery/${encodeURIComponent(job.job_id)}/email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: kindleEmail.trim(), artifact: "epub" }),
      });
      const payload = await response.json();
      if (payload.delivery?.diagnostics && typeof payload.delivery.diagnostics === "object") {
        setDeliveryDiagnostics(payload.delivery.diagnostics);
      }
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Wysyłka mailowa nie powiodła się.");
      }
      const sentArtifact = payload.delivery?.artifact === "pdf" || payload.delivery?.artifact === "cropped_pdf" ? "PDF" : "EPUB";
      setDeliveryStatus(`Wysłano ${sentArtifact} do ${payload.delivery?.masked_recipient || "odbiorcy"}`);
      setKindleEmail(configuredRecipient);
    } catch (caught) {
      setDeliveryError(caught instanceof Error ? caught.message : "Wysyłka mailowa nie powiodła się.");
    } finally {
      setDeliveryBusy(false);
    }
  }

  if (!job) {
    return (
      <section className="km-view" data-testid="file-details-view">
        <Card>
          <CardHeader>
            <CardTitle>Szczegóły pliku</CardTitle>
            <CardDescription>Wybierz zadanie w Bibliotece, żeby zobaczyć artefakty, jakość i naprawy.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="km-empty-state">
              Brak aktywnego pliku.
              <Button variant="outline" size="sm" onClick={openLibrary}>
                Otwórz bibliotekę
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
    );
  }

  return (
    <section className="km-view km-details-view" data-testid="file-details-view">
      <JobStatusPanel job={job} jobStatus={jobStatus} quality={quality} busy={busy} openDetails={() => undefined} showDetailsAction={false} />
      <div className="km-details-grid">
        <Card>
          <CardHeader>
            <CardTitle>{job.filename || job.download_url || "Szczegóły pliku"}</CardTitle>
            <CardDescription>Jedno miejsce na artefakty, decyzję jakości i naprawę wysyłki.</CardDescription>
          </CardHeader>
          <CardContent>
            <MetricRows
              rows={[
                ["Job ID", job.job_id || "brak"],
                ["Status", String(job.status || "nieznany")],
                ["Typ źródła", String(job.source_type || "nieznany")],
                ["Rozmiar EPUB", formatBytes(Number(job.output_size_bytes || 0))],
                ["Czas konwersji", formatSeconds(job.elapsed_seconds)],
              ]}
            />
            {sourcePreviewUrl ? (
              <div className="km-pdf-tools" aria-label="Narzędzia PDF">
                <a className="km-button km-button-outline km-button-sm" href={sourcePreviewUrl} target="_blank" rel="noreferrer">
                  <BookOpen data-icon="inline-start" aria-hidden="true" />
                  Podgląd PDF
                </a>
                <a className="km-button km-button-outline km-button-sm" href={apiUrl("/legacy")}>
                  <Scissors data-icon="inline-start" aria-hidden="true" />
                  Kadruj PDF
                </a>
                <p>PDF zostaje do podglądu i kadrowania; wysyłka z tego widoku używa finalnego EPUB-a.</p>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Decyzja jakości</CardTitle>
            <CardDescription>{quality.detail}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="km-score-strip">
              <Badge variant={quality.status === "premium_ready" ? "success" : quality.status === "failed" ? "destructive" : "warning"}>
                {quality.label}
              </Badge>
              <strong>{quality.score}/100</strong>
            </div>
            <IssueList title="Uwagi jakości Kindle" items={quality.sendToKindleBlockers} empty="Brak uwag jakości dla Kindle" />
            <div className="km-details-actions" hidden>
              <Button variant="outline">
                Otwórz jakość
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Artefakty</CardTitle>
            <CardDescription>Pobierz gotowy EPUB i dostępne artefakty konwersji.</CardDescription>
          </CardHeader>
          <CardContent>
            {artifactRows.length ? (
              <div className="km-artifact-list">
                {artifactRows.map((artifact) => (
                  <a href={artifact.href} key={artifact.label}>
                    <Download data-icon="inline-start" aria-hidden="true" />
                    {artifact.label}
                  </a>
                ))}
              </div>
            ) : (
              <div className="km-empty-state">Brak artefaktów do pobrania.</div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Naprawa i wysyłka</CardTitle>
            <CardDescription>Naprawa poprawia jakość, ale SMTP może wysłać każdy wygenerowany EPUB.</CardDescription>
          </CardHeader>
          <CardContent>
            <MetricRows
              rows={[
                ["Status naprawy", autoRepair.label],
                ["Akcje", autoRepair.actions.length ? autoRepair.actions.join(", ") : "brak"],
                ["Wybrany kandydat", autoRepair.selected || "brak"],
              ]}
            />
            <div className="km-details-actions">
              {canRetryConversion ? (
                <Button variant="outline" onClick={() => void runRetryConversion()} disabled={repairBusy || busy}>
                  {repairBusy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <RefreshCw data-icon="inline-start" aria-hidden="true" />}
                  Ponów rozczytanie
                </Button>
              ) : null}
              <Button variant="outline" onClick={runRepair} disabled={repairBusy || job.status !== "ready"}>
                {repairBusy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <Wrench data-icon="inline-start" aria-hidden="true" />}
                Napraw ponownie
              </Button>
            </div>
            {repairStatus ? <p className="km-delivery-status">{repairStatus}</p> : null}
            {repairError ? <p className="km-delivery-error">{repairError}</p> : null}

            {canSendToKindle ? (
              <form className="km-job-send-form km-details-send-form" onSubmit={sendToKindle}>
                <Button type="submit" disabled={deliveryBusy || !kindleEmail.trim() || !canSendToKindle}>
                  {deliveryBusy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <Send data-icon="inline-start" aria-hidden="true" />}
                  Wyślij na Kindle
                </Button>
              </form>
            ) : (
              <div className="km-empty-state">{deliveryBlockers.length ? deliveryBlockers[0] : "Wysyłka mailem jest dostępna dla wygenerowanego EPUB-a."}</div>
            )}
            {deliveryStatus ? <p className="km-delivery-status">{deliveryStatus}</p> : null}
            {deliveryError ? <p className="km-delivery-error">{deliveryError}</p> : null}
            <DeliveryDiagnosticsPanel diagnostics={activeDeliveryDiagnostics} />
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function SmtpStatusSummary({
  deliveryConfig,
  emailProfile,
  reloadSettings: _reloadSettings,
}: {
  deliveryConfig: DeliveryConfigPayload;
  emailProfile: UserProfilePayload["email_delivery"];
  reloadSettings: () => Promise<void>;
}) {
  const statusLabel = deliveryConfig.configured ? "SMTP skonfigurowane" : deliveryConfig.enabled ? "SMTP niekompletne" : "SMTP wyłączone";
  const missing = Array.isArray(deliveryConfig.missing_config) ? deliveryConfig.missing_config : [];
  const profileReady = Boolean(emailProfile.enabled && emailProfile.host && emailProfile.username && emailProfile.from_address);
  const recipientReady = Boolean(emailProfile.default_recipient);
  const source = String(deliveryConfig.config_source ?? "");
  const sourceLabel = source === "env+profile" ? "profil + env" : source === "profile" ? "profil lokalny" : source === "env" ? "env" : "brak danych";
  const secretRegistered = Boolean(deliveryConfig.secret_registered || emailProfile.secret_registered);
  const secretLabel = deliveryConfig.secret_configured ? "sekret widoczny w env" : secretRegistered ? "sekret odnotowany" : "brak sekretu";
  const summaryParts = [
    profileReady ? `${emailProfile.host}:${emailProfile.port}` : "profil SMTP niepełny",
    secretLabel,
    recipientReady ? `Kindle: ${maskVisibleEmail(emailProfile.default_recipient)}` : "brak adresu Kindle",
  ];
  return (
    <section className="km-smtp-summary" aria-label="Status SMTP">
      <div className="km-smtp-summary-header">
        <div>
          <Badge variant={deliveryConfig.configured ? "success" : deliveryConfig.enabled ? "warning" : "secondary"}>{statusLabel}</Badge>
          <h3>{deliveryConfig.configured ? "Wysyłka na Kindle gotowa" : "Wysyłka na Kindle wymaga konfiguracji"}</h3>
          <p>{summaryParts.join(" · ")}</p>
        </div>
      </div>
      <p className="km-smtp-footnote">
        Źródło: {sourceLabel}. {missing.length ? `Do uzupełnienia: ${missing.join(", ")}.` : "Konfiguracja kompletna."}
      </p>
    </section>
  );
}

function DeliveryDiagnosticsPanel({ diagnostics }: { diagnostics: Record<string, unknown> | null }) {
  if (!diagnostics) return null;
  const smtp = readRecord(diagnostics.smtp);
  const message = readRecord(diagnostics.message);
  const attachment = readRecord(diagnostics.attachment);
  const accepted = smtp.accepted_by_smtp === true ? "tak" : smtp.accepted_by_smtp === false ? "nie" : "brak danych";
  const fromMatches =
    smtp.from_matches_smtp_username === true
      ? "tak"
      : smtp.from_matches_smtp_username === false
        ? "nie"
        : "nie dotyczy / brak danych";
  const attachmentSize = Number(attachment.size_bytes || 0);
  const checksum = String(attachment.sha256 || "");
  return (
    <section className="km-delivery-diagnostics" aria-label="Diagnostyka SMTP">
      <h3>Diagnostyka SMTP</h3>
      <MetricRows
        rows={[
          ["SMTP", `${String(smtp.host || "brak")}:${String(smtp.port || "brak")} / ${String(smtp.security || "brak")}`],
          ["SMTP zaakceptował", accepted],
          ["From = użytkownik SMTP", fromMatches],
          ["MIME", `${String(message.content_type || "brak")} / plain text: ${message.has_plain_text_body === true ? "tak" : "nie"}`],
          [
            "Załącznik",
            `${String(attachment.filename || "brak")} / ${String(attachment.content_type || "brak")} / ${String(attachment.content_disposition || "brak")}`,
          ],
          ["Kodowanie", String(attachment.content_transfer_encoding || "brak")],
          ["Rozmiar", attachmentSize > 0 ? formatBytes(attachmentSize) : "brak danych"],
          ["SHA256", checksum ? `${checksum.slice(0, 12)}...${checksum.slice(-8)}` : "brak danych"],
        ]}
      />
    </section>
  );
}

function SettingsPanel({
  settingsForm,
  setSettingsForm,
  deliveryConfig,
  authConfig,
  account,
  authStatus,
  settingsStatus,
  saveSettings,
  reloadSettings,
  signOut,
  openAccountStart,
}: {
  settingsForm: UserProfilePayload;
  setSettingsForm: React.Dispatch<React.SetStateAction<UserProfilePayload>>;
  deliveryConfig: DeliveryConfigPayload;
  authConfig: AuthConfigPayload;
  account: AccountState;
  authStatus: string;
  settingsStatus: string;
  saveSettings: () => Promise<void>;
  reloadSettings: () => Promise<void>;
  signOut: () => Promise<void>;
  openAccountStart: () => void;
}) {
  const authConfigured = Boolean(authConfig.enabled && authConfig.configured);
  return (
    <section className="km-view">
      <Card>
        <CardHeader>
          <CardTitle>Ustawienia profilu użytkownika</CardTitle>
        </CardHeader>
        <CardContent>
          <section className="km-settings-account" aria-label="Konto użytkownika">
            <div className="km-settings-account-copy">
              {account.authenticated ? <CheckCircle2 aria-hidden="true" /> : <UserRound aria-hidden="true" />}
              <span>
                <strong>{account.authenticated ? "Konto" : "Tryb lokalny"}</strong>
                <small>
                  {account.authenticated
                    ? account.email || account.userId
                    : authConfigured
                      ? "Zaloguj się, aby przypisać bibliotekę i ustawienia do konta."
                      : "Supabase nie jest skonfigurowany. Aplikacja działa lokalnie."}
                </small>
              </span>
            </div>
            <div className="km-settings-account-actions">
              {account.authenticated ? (
                <Button variant="outline" size="sm" onClick={() => void signOut()}>
                  <LogOut data-icon="inline-start" aria-hidden="true" />
                  Wyloguj
                </Button>
              ) : authConfigured ? (
                <Button variant="outline" size="sm" onClick={openAccountStart}>
                  <LogIn data-icon="inline-start" aria-hidden="true" />
                  Zaloguj / konto
                </Button>
              ) : null}
            </div>
            {authStatus ? <p className="km-auth-status km-settings-auth-status">{authStatus}</p> : null}
          </section>
          <SmtpStatusSummary deliveryConfig={deliveryConfig} emailProfile={settingsForm.email_delivery} reloadSettings={reloadSettings} />
          <div className="km-settings-grid">
            <fieldset className="km-settings-form">
              <legend>Domyślne ustawienia konwersji</legend>
              <div className="km-auto-conversion-card km-settings-auto-summary">
                <div>
                  <span className="km-eyebrow">Automatyka konwersji</span>
                  <strong>Auto Premium dobiera profil i OCR przy starcie konwersji</strong>
                  <p>Ręczne domyślne ustawienia zostają dostępne jako override, ale nie konkurują z głównym widokiem.</p>
                </div>
                <span className="km-auto-pill">Zalecane</span>
              </div>

              <details className="km-advanced-conversion km-settings-advanced-conversion">
                <summary>Zaawansowane ustawienia konwersji</summary>
                <div className="km-settings-advanced-body">
                  <label>
                    <span>Domyślny profil konwersji</span>
                    <select
                      aria-label="Domyślny profil konwersji"
                      value={settingsForm.conversion.default_profile}
                      onChange={(event) =>
                        setSettingsForm((current) => ({
                          ...current,
                          conversion: { ...current.conversion, default_profile: event.target.value },
                        }))
                      }
                    >
                      {profiles.map((item) => (
                        <option value={item.value} key={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Domyślny język OCR</span>
                    <select
                      aria-label="Domyślny język OCR"
                      value={settingsForm.conversion.default_language}
                      onChange={(event) =>
                        setSettingsForm((current) => ({
                          ...current,
                          conversion: { ...current.conversion, default_language: event.target.value },
                        }))
                      }
                    >
                      <option value="pl">pl</option>
                      <option value="en">en</option>
                    </select>
                  </label>
                  <label className="km-inline-check">
                    <input
                      type="checkbox"
                      checked={settingsForm.conversion.force_ocr}
                      onChange={(event) =>
                        setSettingsForm((current) => ({
                          ...current,
                          conversion: { ...current.conversion, force_ocr: event.target.checked },
                        }))
                      }
                    />
                    Domyślnie wymuszaj OCR
                  </label>
                  <label className="km-inline-check">
                    <input
                      type="checkbox"
                      checked={settingsForm.conversion.heading_repair}
                      onChange={(event) =>
                        setSettingsForm((current) => ({
                          ...current,
                          conversion: { ...current.conversion, heading_repair: event.target.checked },
                        }))
                      }
                    />
                    Domyślnie naprawiaj nagłówki
                  </label>
                </div>
              </details>
            </fieldset>

            <fieldset className="km-settings-form">
              <legend>Profil SMTP</legend>
              <label className="km-inline-check">
                <input
                  type="checkbox"
                  checked={settingsForm.email_delivery.enabled}
                  onChange={(event) =>
                    setSettingsForm((current) => ({
                      ...current,
                      email_delivery: { ...current.email_delivery, enabled: event.target.checked },
                    }))
                  }
                />
                Włącz dostawę SMTP
              </label>
              <label>
                <span>Host SMTP</span>
                <input
                  aria-label="Host SMTP"
                  value={settingsForm.email_delivery.host}
                  onChange={(event) =>
                    setSettingsForm((current) => ({
                      ...current,
                      email_delivery: { ...current.email_delivery, host: event.target.value },
                    }))
                  }
                  placeholder="smtp.sendgrid.net"
                />
              </label>
              <label>
                <span>Użytkownik SMTP</span>
                <input
                  aria-label="Użytkownik SMTP"
                  value={settingsForm.email_delivery.username}
                  onChange={(event) =>
                    setSettingsForm((current) => ({
                      ...current,
                      email_delivery: { ...current.email_delivery, username: event.target.value },
                    }))
                  }
                  placeholder="apikey"
                />
              </label>
              <label>
                <span>Adres nadawcy SMTP</span>
                <input
                  aria-label="Adres nadawcy SMTP"
                  value={settingsForm.email_delivery.from_address}
                  onChange={(event) =>
                    setSettingsForm((current) => ({
                      ...current,
                      email_delivery: { ...current.email_delivery, from_address: event.target.value },
                    }))
                  }
                  placeholder="operator@example.com"
                />
              </label>
              <label>
                <span>Domyślny adres Kindle</span>
                <input
                  aria-label="Domyślny adres Kindle"
                  type="email"
                  value={settingsForm.email_delivery.default_recipient}
                  onChange={(event) =>
                    setSettingsForm((current) => ({
                      ...current,
                      email_delivery: { ...current.email_delivery, default_recipient: event.target.value },
                    }))
                  }
                  placeholder="twoj-adres@kindle.com"
                />
                <small>To adres z Amazon Send to Kindle. Biblioteka i Szczegóły pliku użyją go automatycznie.</small>
              </label>
              <div className="km-form-grid">
                <label>
                  <span>Port</span>
                  <input
                    type="number"
                    value={settingsForm.email_delivery.port}
                    onChange={(event) =>
                      setSettingsForm((current) => ({
                        ...current,
                        email_delivery: { ...current.email_delivery, port: Number(event.target.value) || 587 },
                      }))
                    }
                  />
                </label>
                <label>
                  <span>Zabezpieczenie</span>
                  <select
                    value={settingsForm.email_delivery.security}
                    onChange={(event) =>
                      setSettingsForm((current) => ({
                        ...current,
                        email_delivery: { ...current.email_delivery, security: event.target.value },
                      }))
                    }
                  >
                    <option value="starttls">starttls</option>
                    <option value="ssl">ssl</option>
                    <option value="none">brak</option>
                  </select>
                </label>
              </div>
              <p className="km-secret-note">
                Sekret: {deliveryConfig.secret_configured
                  ? `widoczny w ${deliveryConfig.secret_env_name || "env"}`
                  : settingsForm.email_delivery.secret_registered || deliveryConfig.secret_registered
                    ? "odnotowany w profilu; wartość nie jest przechowywana w bazie"
                    : "ustaw KINDLEMASTER_SMTP_PASSWORD przed startem serwera"}.
              </p>
            </fieldset>
          </div>
          <div className="km-settings-actions">
            <Button onClick={() => void saveSettings()}>
              <Save data-icon="inline-start" aria-hidden="true" />
              Zapisz ustawienia
            </Button>
            {settingsStatus ? <span className="km-settings-status">{settingsStatus}</span> : null}
          </div>
        </CardContent>
      </Card>
    </section>
  );
}

function DebugPanel({
  activeJob,
  debugText,
  error,
}: {
  activeJob: ConversionJobPayload | null;
  debugText: string;
  error: string;
}) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const sentryEventId = activeJob?.sentry_event_id || activeJob?.quality_state?.sentry_event_id || "";
  return (
    <details className="km-advanced-debug">
      <summary>Szczegóły błędu konwersji</summary>
      <Card>
        <CardHeader>
          <CardTitle>Diagnostyka błędu</CardTitle>
          <CardDescription>{sentryEventId ? `Zdarzenie Sentry: ${sentryEventId}` : "Brak zdarzenia Sentry dla aktywnego zadania."}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="km-debug-toolbar">
            <Badge variant={error ? "destructive" : "secondary"}>{error ? "Błąd" : "OK"}</Badge>
            {error ? (
              <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogTrigger>
                  <Button variant="destructive" size="sm">
                    <AlertTriangle data-icon="inline-start" aria-hidden="true" />
                    Pokaż
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Błąd konwersji</DialogTitle>
                    <DialogDescription>{error}</DialogDescription>
                  </DialogHeader>
                </DialogContent>
              </Dialog>
            ) : (
              <span>Diagnostyka jest ukryta, dopóki nie pojawi się błąd.</span>
            )}
          </div>
          <pre className="km-debug-pre">{debugText}</pre>
        </CardContent>
      </Card>
    </details>
  );
}

function MetricRows({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="km-metric-rows">
      {rows.map(([label, value]) => (
        <React.Fragment key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function IssueList({
  title,
  items,
  empty,
}: {
  title: string;
  items: Array<string | Record<string, unknown>>;
  empty: string;
}) {
  return (
    <section className="km-issue-list">
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.slice(0, 5).map((item, index) => (
            <li key={index}>{formatIssue(item)}</li>
          ))}
        </ul>
      ) : (
        <p>{empty}</p>
      )}
    </section>
  );
}

function pipelineStepClass(index: number, status: JobStatus) {
  const activeIndex = status === "idle" ? 0 : status === "queued" ? 1 : status === "running" ? 2 : status === "ready" ? 5 : 4;
  return ["km-pipeline-step", index <= activeIndex ? "is-active" : ""].filter(Boolean).join(" ");
}

function normalizeJobStatus(status: unknown): JobStatus {
  const value = String(status ?? "idle").toLowerCase();
  if (value === "queued" || value === "running" || value === "ready" || value === "failed") return value;
  return "idle";
}

function buildLegacyStatusText(
  job: ConversionJobPayload | null,
  jobStatus: JobStatus,
  file: File | null,
  busy: boolean,
  error: string,
  quality: NormalizedQualityState,
) {
  if (jobStatus === "ready") {
    const sizeLabel = formatBytes(readOutputSizeBytes(job));
    const sourceLabel = sourceTypeLabel(job, file);
    const reviewPrefix = quality.status === "needs_review" || Boolean(job?.quality_state?.release_blocked) ? "Wymaga naprawy przed publikacją. " : "";
    return `${reviewPrefix}EPUB wygenerowany i pobrany z ${sourceLabel}. Rozmiar: ${sizeLabel}.`;
  }

  if (jobStatus === "failed") {
    return buildLegacyErrorStatus(job?.error || job?.message || error);
  }

  if (busy || jobStatus === "queued" || jobStatus === "running") {
    const message = String(job?.message || "").trim();
    return message || "Trwa konwersja EPUB...";
  }

  if (error) {
    return buildLegacyErrorStatus(error);
  }

  if (file) {
    return `${isPdfFile(file) ? "PDF" : "DOCX"} gotowy do konwersji.`;
  }

  return "Wybierz plik PDF albo DOCX.";
}

function buildLegacyErrorStatus(message: unknown) {
  const rawMessage = String(message || "").trim();
  const folded = asciiFold(rawMessage).toLowerCase();
  if (folded.includes("restart aplikacji") || folded.includes("zrestartowana")) {
    return "Lokalna aplikacja zostala zrestartowana albo zadanie wygaslo. Uruchom konwersje ponownie.";
  }
  if (folded.includes("przekroczono limit czasu")) {
    return "Przekroczono limit czasu odpowiedzi lokalnego serwera.";
  }
  if (folded.includes("polaczenie z lokalnym serwerem")) {
    return "Polaczenie z lokalnym serwerem konwersji zostalo przerwane.";
  }
  if (folded.includes("backend timeout")) {
    return "Konwersja nie powiodla sie: backend timeout";
  }
  if (folded.includes("konwersja nie powiodla")) {
    return rawMessage;
  }
  return rawMessage ? `Konwersja nie powiodla sie: ${rawMessage}` : "Konwersja nie powiodla sie.";
}

function asciiFold(value: string) {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function sourceTypeLabel(job: ConversionJobPayload | null, file: File | null) {
  const sourceType = String(job?.source_type || "").toLowerCase();
  if (sourceType.includes("docx") || file?.name.toLowerCase().endsWith(".docx")) return "DOCX";
  return "PDF";
}

function readOutputSizeBytes(job: ConversionJobPayload | null) {
  const conversion = readRecord(job?.conversion);
  const qualitySummary = readRecord(job?.quality_state?.summary);
  const value = Number(job?.output_size_bytes ?? conversion.output_size_bytes ?? qualitySummary.output_size_bytes ?? 0);
  return Number.isFinite(value) ? value : 0;
}

async function triggerBrowserDownload(url: string, fetcher: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> = fetch) {
  if (!url || typeof document === "undefined") return;
  const response = await fetcher(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Nie udało się pobrać EPUB (${response.status}).`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const filename = filenameFromContentDisposition(response.headers.get("content-disposition")) || filenameFromDownloadUrl(url);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function filenameFromContentDisposition(header: string | null) {
  if (!header) return "";
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1].replace(/"/g, ""));
  const match = header.match(/filename="?([^";]+)"?/i);
  return match?.[1]?.trim() || "";
}

function filenameFromDownloadUrl(url: string) {
  const rawName = url.split(/[?#]/)[0]?.split("/").filter(Boolean).pop() || "kindlemaster.epub";
  return rawName.toLowerCase().endsWith(".epub") ? rawName : `${rawName}.epub`;
}

function isLibraryJobProcessing(job: ConversionJobPayload) {
  const status = normalizeJobStatus(job.status);
  return status === "queued" || status === "running";
}

function libraryStatusLabel(status: JobStatus) {
  if (status === "ready") return "Gotowe";
  if (status === "failed") return "Błąd";
  if (status === "queued" || status === "running") return "Trwa przetwarzanie";
  return "Nieznany";
}

function libraryProcessingDetail(job: ConversionJobPayload, status: JobStatus) {
  const message = String(job.message || "").trim();
  if (message) return message;
  if (status === "queued") return "Zadanie czeka na start konwersji.";
  return "Konwersja jest w toku. Biblioteka odświeży rekord automatycznie.";
}

function mergeLibraryJobsWithActiveJob(
  jobs: ConversionJobPayload[],
  activeJob: ConversionJobPayload | null,
  activeJobStatus: JobStatus,
  busy: boolean,
) {
  const activeJobId = activeJob?.job_id;
  const shouldShowLiveJob = Boolean(activeJobId && (busy || activeJobStatus === "queued" || activeJobStatus === "running"));
  if (!shouldShowLiveJob || !activeJob) return jobs;

  const liveJob: ConversionJobPayload = {
    ...activeJob,
    status: activeJobStatus === "idle" ? activeJob.status : activeJobStatus,
  };
  const hasExistingJob = jobs.some((job) => job.job_id === activeJobId);
  if (!hasExistingJob) return [liveJob, ...jobs];

  return jobs.map((job) => {
    if (job.job_id !== activeJobId) return job;
    return {
      ...job,
      ...liveJob,
      download_url: liveJob.download_url ?? job.download_url,
      quality_state: liveJob.quality_state ?? job.quality_state,
      auto_repair: liveJob.auto_repair ?? job.auto_repair,
      email_delivery: liveJob.email_delivery ?? job.email_delivery,
    };
  });
}

function filterLibraryJobs(jobs: ConversionJobPayload[], query: string) {
  const normalizedQuery = query.trim().toLocaleLowerCase("pl-PL");
  if (!normalizedQuery) return jobs;
  return jobs.filter((job) => librarySearchText(job).includes(normalizedQuery));
}

function sortLibraryJobs(jobs: ConversionJobPayload[], sort: LibrarySort) {
  const sorted = [...jobs];
  sorted.sort((left, right) => {
    if (isLibraryJobProcessing(left) !== isLibraryJobProcessing(right)) {
      return isLibraryJobProcessing(left) ? -1 : 1;
    }
    if (sort === "name_asc") return jobDisplayName(left).localeCompare(jobDisplayName(right), "pl");
    if (sort === "name_desc") return jobDisplayName(right).localeCompare(jobDisplayName(left), "pl");
    if (sort === "quality_desc") return jobQualityScore(right) - jobQualityScore(left);
    if (sort === "quality_asc") return jobQualityScore(left) - jobQualityScore(right);
    if (sort === "kindle_desc") return jobKindleRank(right) - jobKindleRank(left);
    if (sort === "kindle_asc") return jobKindleRank(left) - jobKindleRank(right);
    if (sort === "status_asc" || sort === "status_desc") {
      const statusCompare = libraryStatusLabel(normalizeJobStatus(left.status)).localeCompare(
        libraryStatusLabel(normalizeJobStatus(right.status)),
        "pl",
      );
      if (statusCompare !== 0) return sort === "status_desc" ? -statusCompare : statusCompare;
    }
    if (sort === "updated_asc") return jobUpdatedTime(left) - jobUpdatedTime(right);
    return jobUpdatedTime(right) - jobUpdatedTime(left);
  });
  return sorted;
}

function nextLibrarySort(current: LibrarySort, column: "name" | "status" | "quality" | "kindle"): LibrarySort {
  if (column === "name") return current === "name_asc" ? "name_desc" : "name_asc";
  if (column === "status") return current === "status_asc" ? "status_desc" : "status_asc";
  if (column === "quality") return current === "quality_desc" ? "quality_asc" : "quality_desc";
  return current === "kindle_desc" ? "kindle_asc" : "kindle_desc";
}

function librarySearchText(job: ConversionJobPayload) {
  const quality = normalizeQualityState(job.quality_state ?? null);
  const qualityScore = formatQualityScore(job.quality_state, quality.score);
  return [
    jobDisplayName(job),
    job.job_id,
    job.source_type,
    libraryStatusLabel(normalizeJobStatus(job.status)),
    quality.label,
    qualityScore.label,
    qualityScore.value,
    job.quality_state?.release_verdict,
    job.text_excerpt,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("pl-PL");
}

function jobDisplayName(job: ConversionJobPayload) {
  return String(job.title || job.filename || job.job_id || "").trim();
}

function jobUpdatedTime(job: ConversionJobPayload) {
  const rawTimestamp = String(job.updated_at || job.created_at || "").trim();
  const parsed = rawTimestamp ? Date.parse(rawTimestamp) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function jobQualityScore(job: ConversionJobPayload) {
  const quality = normalizeQualityState(job.quality_state ?? null);
  if (Number.isFinite(quality.score)) return quality.score;
  const rawScore = Number((job.quality_state as any)?.score ?? 0);
  return Number.isFinite(rawScore) ? rawScore : 0;
}

function jobKindleRank(job: ConversionJobPayload) {
  const quality = normalizeQualityState(job.quality_state ?? null);
  if (quality.sendToKindleReady === true) return 3;
  if (normalizeJobStatus(job.status) === "ready" && job.download_url) return 2;
  if (normalizeJobStatus(job.status) === "ready") return 1;
  return 0;
}

function normalizeUserProfile(payload: unknown): UserProfilePayload {
  const source = payload && typeof payload === "object" ? (payload as Partial<UserProfilePayload>) : {};
  return {
    conversion: {
      default_profile: source.conversion?.default_profile || defaultProfile.conversion.default_profile,
      default_language: source.conversion?.default_language || defaultProfile.conversion.default_language,
      force_ocr: Boolean(source.conversion?.force_ocr ?? defaultProfile.conversion.force_ocr),
      heading_repair: Boolean(source.conversion?.heading_repair ?? defaultProfile.conversion.heading_repair),
    },
    email_delivery: {
      enabled: Boolean(source.email_delivery?.enabled ?? defaultProfile.email_delivery.enabled),
      host: source.email_delivery?.host || "",
      port: Number(source.email_delivery?.port || defaultProfile.email_delivery.port),
      security: source.email_delivery?.security || defaultProfile.email_delivery.security,
      username: source.email_delivery?.username || "",
      from_address: source.email_delivery?.from_address || "",
      default_recipient: source.email_delivery?.default_recipient || "",
      max_attachment_bytes: Number(source.email_delivery?.max_attachment_bytes || defaultProfile.email_delivery.max_attachment_bytes),
      secret_configured: Boolean(source.email_delivery?.secret_configured),
      secret_registered: Boolean(source.email_delivery?.secret_registered),
    },
  };
}

function initialView(): ViewId {
  if (typeof window === "undefined") return "convert";
  const value = window.location.hash.replace("#", "");
  if (value === "delivery") return "library";
  if (value === "quality") return "library";
  return isViewId(value) ? value : "convert";
}

function isViewId(value: string): value is ViewId {
  return validViews.has(value as ViewId);
}

function syncHash(view: ViewId) {
  if (typeof window !== "undefined") {
    window.history.replaceState(null, "", `#${view}`);
  }
}

function importPromptStorageKey(userId: string) {
  return `kindlemaster.import-local-history.dismissed.${userId}`;
}

function startGuestModeStorageKey() {
  return "kindlemaster.start.continue-local";
}

interface JobActivityState {
  active: boolean;
  badge: string;
  stage: string;
  detail: string;
  progress: number;
  elapsed: string;
  remaining: string;
}

function buildJobActivity(
  job: ConversionJobPayload | null,
  jobStatus: JobStatus,
  busy: boolean,
  quality: NormalizedQualityState,
): JobActivityState {
  const active = Boolean(busy || jobStatus === "queued" || jobStatus === "running");
  const message = String(job?.message || "").trim();
  if (!active) {
    return {
      active: false,
      badge: quality.label,
      stage: quality.label,
      detail: message || quality.detail,
      progress: quality.score,
      elapsed: formatSeconds(job?.elapsed_seconds),
      remaining: jobStatus === "ready" ? "zakończone" : "brak danych",
    };
  }

  const lower = message.toLowerCase();
  let stage = "Przetwarzanie";
  let progress = 18;
  let remaining = "około 2-4 min";

  if (jobStatus === "queued") {
    stage = "W kolejce";
    progress = 8;
    remaining = "start za chwilę";
  } else if (lower.includes("ekstrakc") || lower.includes("extract")) {
    stage = "Ekstrakcja tekstu";
    progress = 26;
    remaining = "około 2-4 min";
  } else if (lower.includes("konwers") || lower.includes("convert")) {
    stage = "Budowanie EPUB";
    progress = 46;
    remaining = "około 1-3 min";
  } else if (lower.includes("cleanup") || lower.includes("czyszcz") || lower.includes("napraw")) {
    stage = "Czyszczenie i naprawa";
    progress = 68;
    remaining = "około 1-2 min";
  } else if (lower.includes("quality") || lower.includes("audyt") || lower.includes("walid")) {
    stage = "Audyt jakości";
    progress = 86;
    remaining = "zwykle poniżej minuty";
  }

  const elapsed = formatSeconds(job?.elapsed_seconds);
  const detail = message || "Konwersja jest w toku. Status odświeża się automatycznie.";
  return {
    active: true,
    badge: jobStatus === "queued" ? "Oczekuje" : "Przetwarzanie",
    stage,
    detail,
    progress,
    elapsed,
    remaining,
  };
}

function viewTitle(view: ViewId) {
  return {
    convert: "Konwersja",
    preview: "Podgląd PDF",
    library: "Biblioteka",
    details: "Szczegóły pliku",
    settings: "Ustawienia",
  }[view];
}

function viewDescription(view: ViewId) {
  return {
    convert: "Wgraj plik i ustaw tylko parametry potrzebne przed konwersją.",
    preview: "Sprawdź PDF w przeglądarce i przygotuj kadrowanie przed startem.",
    library: "Przeglądaj zadania, pobieraj EPUB i wysyłaj gotowe artefakty na Kindle.",
    details: "Zobacz artefakty, decyzję jakości i bezpieczną naprawę wybranego pliku.",
    settings: "Konto, domyślna konwersja i wysyłka na Kindle.",
  }[view];
}

function formatIssue(item: string | Record<string, unknown>) {
  if (typeof item === "string") return item;
  return translateIssue(item);
}

function maskVisibleEmail(value: string) {
  const [local, domain] = value.split("@");
  if (!local || !domain) return "adres Kindle z Ustawień";
  return `${local.slice(0, 1)}***@${domain}`;
}

function formatQualityScore(payload: QualityStatePayload | undefined, fallbackScore: number) {
  const rawValue = Number(payload?.score ?? payload?.summary?.score ?? payload?.summary?.quality_score ?? fallbackScore);
  const value = Number.isFinite(rawValue) ? rawValue : fallbackScore;
  if (value <= 10) {
    const rounded = Math.round(value * 10) / 10;
    return {
      value: Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1),
      scale: "/10",
      label: `${Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)}/10`,
    };
  }
  const rounded = Math.max(0, Math.min(100, Math.round(value)));
  return {
    value: String(rounded),
    scale: "/100",
    label: `${rounded}/100`,
  };
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function formatDeliveryBlockers(items: Array<Record<string, unknown>>) {
  if (!items.length) return ["EPUB nie jest jeszcze gotowy do wysyłki na Kindle."];
  return items.map(translateIssue);
}

function translateIssue(item: Record<string, unknown>) {
  const code = String(item.code ?? "");
  const message = String(item.message ?? item.title ?? "");
  const suggestedAction = String(item.suggested_action ?? "");
  if (code === "kindle_delivery_release_not_ready") {
    return "Bramka jakości ma status „Nie publikuj”. Mail może zostać wysłany, ale plik nie jest gotowy do publikacji; użyj „Napraw ponownie”, jeśli chcesz poprawić jakość.";
  }
  if (code === "kindle_delivery_validation_failed") {
    return "Walidacja EPUB/struktury nie jest zaliczona. Mail może zostać wysłany, ale Kindle może odrzucić plik albo wyświetlić go niepoprawnie.";
  }
  if (code === "kindle_delivery_progressive_jpeg") {
    const count = message.match(/\d+/)?.[0];
    return count
      ? `EPUB zawiera obrazy progressive JPEG (${count}); mail może zostać wysłany, ale zalecane jest przekodowanie do baseline JPEG albo PNG.`
      : "EPUB zawiera obrazy progressive JPEG; mail może zostać wysłany, ale zalecane jest przekodowanie do baseline JPEG albo PNG.";
  }
  if (code === "text_cleanup_blocked") {
    return "Czyszczenie tekstu zablokowało niebezpieczne zmiany. Sprawdź raport jakości przed publikacją.";
  }
  if (code === "semantic_cleanup_failed") {
    return "Semantyczne czyszczenie EPUB nie przeszło bramki. Sprawdź metadane, nagłówki i raport jakości.";
  }
  if (code === "runtime_quality_gate_draft") {
    return "Konwersja zakończyła się w trybie szkicu, więc plik nie jest zatwierdzony do publikacji.";
  }
  if (code === "metadata_placeholder") {
    return "Metadane zawierają placeholder. Uzupełnij tytuł/autora/wydawcę przed wysyłką.";
  }
  if (message) return message;
  if (suggestedAction) return suggestedAction;
  return String(item.code ?? JSON.stringify(item));
}

function normalizeAutoRepair(value: unknown) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
  const status = String(source.status ?? "not_run");
  const actions = Array.isArray(source.actions) ? source.actions.map(String).filter(Boolean) : [];
  return {
    status,
    label: autoRepairStatusLabel(status),
    actions,
    selected: String(source.selected_candidate ?? source.selected_stage ?? ""),
  };
}

function autoRepairStatusLabel(status: string) {
  return (
    {
      not_run: "Nie uruchamiano",
      applied: "Naprawa zastosowana",
      rejected: "Kandydat odrzucony",
      failed: "Naprawa nie powiodła się",
      skipped: "Pominięto",
    }[status] || status
  );
}

function autoRepairMessage(value: unknown) {
  const repair = normalizeAutoRepair(value);
  if (repair.status === "applied") return "Naprawa zastosowana i jakość przeliczona ponownie.";
  if (repair.status === "rejected") return "Kandydat naprawy został odrzucony, bo pogarszał jakość.";
  if (repair.status === "failed") return "Naprawa nie powiodła się; oryginalny EPUB pozostał aktywny.";
  if (repair.status === "skipped") return "Nie znaleziono bezpiecznej automatycznej naprawy.";
  return repair.label;
}

function buildArtifactRows(job: ConversionJobPayload | null, quality: NormalizedQualityState) {
  const rows: Array<{ label: string; href: string }> = [];
  if (job?.download_url) rows.push({ label: "Finalny EPUB", href: apiUrl(job.download_url) });
  if (job?.source_preview_url) rows.push({ label: "PDF źródłowy", href: apiUrl(job.source_preview_url) });
  const pgnUrl = artifactDownloadUrl(job, "chess_pgn");
  if (pgnUrl) rows.push({ label: "PGN partii", href: pgnUrl });
  const pgnHtmlUrl = artifactDownloadUrl(job, "chess_pgn_html");
  if (pgnHtmlUrl) rows.push({ label: "HTML PGN/FEN", href: pgnHtmlUrl });
  void quality;
  return rows;
}

function artifactDownloadUrl(job: ConversionJobPayload | null, key: string) {
  const artifacts = job?.artifacts && typeof job.artifacts === "object" ? job.artifacts : {};
  const artifact = artifacts[key];
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) return "";
  const source = artifact as Record<string, unknown>;
  const directUrl = String(source.download_url || "").trim();
  if (directUrl) return apiUrl(directUrl);
  if (!job?.job_id) return "";
  return apiUrl(`/convert/artifact/${encodeURIComponent(job.job_id)}/${encodeURIComponent(key)}`);
}

function formatSeconds(value: unknown) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "brak danych";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

function isPdfFile(file: File | null) {
  if (!file) return false;
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export default App;
