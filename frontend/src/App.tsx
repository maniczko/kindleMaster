import * as React from "react";
import {
  AlertTriangle,
  Download,
  Gauge,
  Loader2,
  RefreshCw,
  Send,
  Upload,
} from "lucide-react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "./components/ui/dialog";
import { Progress } from "./components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { normalizeQualityState, type NormalizedQualityState, type QualityStatePayload } from "./lib/quality-state";

type JobStatus = "idle" | "queued" | "running" | "ready" | "failed";

interface ConversionJobPayload {
  success?: boolean;
  job_id?: string;
  status?: JobStatus | string;
  message?: string;
  filename?: string;
  source_type?: string;
  elapsed_seconds?: number;
  download_url?: string;
  quality_state_url?: string;
  report_json_url?: string;
  report_markdown_url?: string;
  quality_state?: QualityStatePayload;
  sentry_event_id?: string;
  error?: string;
  error_code?: string;
}

const profiles = [
  { value: "auto-premium", label: "Auto Premium" },
  { value: "book", label: "Book" },
  { value: "magazine", label: "Magazine" },
  { value: "technical-study", label: "Technical" },
  { value: "preserve-layout", label: "Preserve layout" },
];

const pipelineSteps = ["Upload", "Queue", "Convert", "Audit", "Quality gate", "Artifacts"];

function App() {
  const [file, setFile] = React.useState<File | null>(null);
  const [profile, setProfile] = React.useState("auto-premium");
  const [language, setLanguage] = React.useState("pl");
  const [forceOcr, setForceOcr] = React.useState(false);
  const [headingRepair, setHeadingRepair] = React.useState(true);
  const [activeJob, setActiveJob] = React.useState<ConversionJobPayload | null>(null);
  const [jobs, setJobs] = React.useState<ConversionJobPayload[]>([]);
  const [isBusy, setIsBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const normalizedQuality = React.useMemo(
    () => normalizeQualityState(activeJob?.quality_state ?? null),
    [activeJob?.quality_state],
  );
  const activeStatus = normalizeJobStatus(activeJob?.status);

  React.useEffect(() => {
    void loadJobs();
  }, []);

  async function loadJobs() {
    try {
      const response = await fetch("/convert/jobs", { cache: "no-store" });
      const payload = await response.json();
      const items = Array.isArray(payload.jobs) ? payload.jobs : Array.isArray(payload.items) ? payload.items : [];
      setJobs(items.slice(0, 8));
    } catch {
      setJobs([]);
    }
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

      const startResponse = await fetch("/convert/start", { method: "POST", body: formData });
      const startPayload = await startResponse.json();
      if (!startResponse.ok || !startPayload.success || !startPayload.job_id) {
        throw new Error(startPayload.error || "Nie udało się uruchomić konwersji.");
      }

      const initialJob: ConversionJobPayload = {
        ...startPayload,
        status: startPayload.status || "queued",
        filename: file.name,
      };
      setActiveJob(initialJob);
      await pollJob(startPayload.job_id, initialJob);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Nieznany błąd konwersji.";
      setError(message);
      setActiveJob((current) => ({
        ...(current ?? {}),
        status: "failed",
        error: message,
      }));
    } finally {
      setIsBusy(false);
      void loadJobs();
    }
  }

  async function pollJob(jobId: string, seed: ConversionJobPayload) {
    let current = seed;
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await delay(attempt === 0 ? 800 : 1600);
      const response = await fetch(`/convert/status/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Status konwersji jest niedostępny.");
      }
      current = payload;
      setActiveJob(payload);
      if (payload.status === "ready") return;
      if (payload.status === "failed") throw new Error(payload.error || payload.message || "Konwersja nie powiodła się.");
    }
    throw new Error("Konwersja trwa zbyt długo dla interaktywnego podglądu.");
  }

  const canStart = Boolean(file && !isBusy);
  const debugText = JSON.stringify(activeJob ?? { status: "idle" }, null, 2);

  return (
    <main className="km-app-shell">
      <aside className="km-sidebar" aria-label="KindleMaster navigation">
        <div className="km-brand">
          <div className="km-brand-mark">KM</div>
          <div>
            <strong>KindleMaster</strong>
            <span>Conversion OS</span>
          </div>
        </div>
        <nav className="km-nav" aria-label="Pipeline">
          {["Dashboard", "Jobs", "Quality", "Artifacts", "Debug"].map((item, index) => (
            <a href={`#${item.toLowerCase()}`} className={index === 0 ? "is-active" : ""} key={item}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              {item}
            </a>
          ))}
        </nav>
        <div className="km-sidebar-status">
          <span>Local API</span>
          <strong>127.0.0.1:5001</strong>
        </div>
      </aside>

      <section className="km-main">
        <header className="km-header">
          <div>
            <h1>Conversion dashboard</h1>
            <p>Konwersja, status, jakość i pliki wynikowe w jednym panelu.</p>
          </div>
          <div className="km-header-actions">
            <Button variant="outline" onClick={() => void loadJobs()}>
              <RefreshCw data-icon="inline-start" aria-hidden="true" />
              Refresh jobs
            </Button>
            <Button onClick={startConversion} disabled={!canStart}>
              {isBusy ? <Loader2 data-icon="inline-start" aria-hidden="true" /> : <Send data-icon="inline-start" aria-hidden="true" />}
              Convert
            </Button>
          </div>
        </header>

        <section className="km-board" id="dashboard">
          <Card className="km-upload-panel">
            <CardHeader>
              <CardTitle>New conversion</CardTitle>
              <CardDescription>Wybierz dokument i profil konwersji.</CardDescription>
            </CardHeader>
            <CardContent>
              <label className="km-drop-zone">
                <Upload aria-hidden="true" />
                <input
                  aria-label="Upload PDF or DOCX"
                  type="file"
                  accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
                <strong>{file ? file.name : "Choose file"}</strong>
                <span>{file ? formatBytes(file.size) : "PDF or DOCX"}</span>
              </label>

              <div className="km-form-grid">
                <label>
                  <span>Profile</span>
                  <select value={profile} onChange={(event) => setProfile(event.target.value)}>
                    {profiles.map((item) => (
                      <option value={item.value} key={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>OCR language</span>
                  <select value={language} onChange={(event) => setLanguage(event.target.value)}>
                    <option value="pl">pl</option>
                    <option value="en">en</option>
                  </select>
                </label>
              </div>

              <div className="km-switch-row">
                <label>
                  <input type="checkbox" checked={forceOcr} onChange={(event) => setForceOcr(event.target.checked)} />
                  Force OCR
                </label>
                <label>
                  <input type="checkbox" checked={headingRepair} onChange={(event) => setHeadingRepair(event.target.checked)} />
                  Heading repair
                </label>
              </div>
            </CardContent>
          </Card>

          <StatusPanel job={activeJob} jobStatus={activeStatus} quality={normalizedQuality} busy={isBusy} />
        </section>

        <section className="km-pipeline-strip" aria-label="Conversion pipeline">
          {pipelineSteps.map((step, index) => (
            <div className={pipelineStepClass(index, activeStatus)} key={step}>
              <span>{index + 1}</span>
              <strong>{step}</strong>
            </div>
          ))}
        </section>

        <Tabs defaultValue="quality" className="km-workspace-tabs">
          <TabsList>
            <TabsTrigger value="quality">Quality report</TabsTrigger>
            <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
            <TabsTrigger value="jobs">Jobs</TabsTrigger>
            <TabsTrigger value="debug">Debug</TabsTrigger>
          </TabsList>
          <TabsContent value="quality">
            <QualityReport quality={normalizedQuality} raw={activeJob?.quality_state ?? null} />
          </TabsContent>
          <TabsContent value="artifacts">
            <ArtifactPanel job={activeJob} quality={normalizedQuality} />
          </TabsContent>
          <TabsContent value="jobs">
            <JobsPanel jobs={jobs} onSelect={setActiveJob} />
          </TabsContent>
          <TabsContent value="debug">
            <DebugPanel activeJob={activeJob} debugText={debugText} error={error} />
          </TabsContent>
        </Tabs>
      </section>
    </main>
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
        <CardTitle>Job status</CardTitle>
        <CardDescription>{job?.job_id || "No active job"}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="km-score-line">
          <Gauge aria-hidden="true" />
          <div>
            <strong>{quality.score || 0}</strong>
            <span>Quality score</span>
          </div>
        </div>
        <Progress value={quality.score} />
        <div className="km-status-grid">
          <Badge variant={busy || jobStatus === "running" || jobStatus === "queued" ? "secondary" : badgeVariant}>
            {busy ? "Processing" : quality.label}
          </Badge>
          <span>{job?.message || quality.detail}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function QualityReport({ quality, raw }: { quality: NormalizedQualityState; raw: QualityStatePayload | null }) {
  const rawSummary = raw?.summary ?? {};
  return (
    <div className="km-panel-grid">
      <Card>
        <CardHeader>
          <CardTitle>Quality gate</CardTitle>
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
              ["Reading", String(raw?.reading_verdict ?? "not reported")],
              ["Release", String(raw?.release_verdict ?? "not reported")],
              ["Sendable", raw?.sendable === true ? "yes" : "not confirmed"],
              ["Kindle ready", raw?.kindle_ready === true ? "yes" : "not confirmed"],
              ["Premium ready", raw?.premium_ready === true ? "yes" : "not confirmed"],
              ["Profile", String(rawSummary.profile ?? "not reported")],
            ]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Review queue</CardTitle>
          <CardDescription>Blockers and warnings from the same quality_state payload.</CardDescription>
        </CardHeader>
        <CardContent>
          <IssueList title="Blockers" items={quality.blockers} empty="No blockers reported" />
          <IssueList title="Warnings" items={quality.warnings} empty="No warnings reported" />
        </CardContent>
      </Card>
    </div>
  );
}

function ArtifactPanel({ job, quality }: { job: ConversionJobPayload | null; quality: NormalizedQualityState }) {
  const links = [
    ["Download EPUB", job?.download_url],
    ["Quality JSON", job?.quality_state_url],
    ["Report JSON", job?.report_json_url],
    ["Report MD", job?.report_markdown_url],
    ...Object.entries(quality.reports).map(([key, value]) => [`Report: ${key}`, value]),
    ...Object.entries(quality.artifacts).map(([key, value]) => [`Artifact: ${key}`, value]),
  ].filter((item): item is [string, string] => typeof item[1] === "string" && item[1].length > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Artifact and download panel</CardTitle>
        <CardDescription>Pobierz EPUB i raporty jakości dla aktywnego zadania.</CardDescription>
      </CardHeader>
      <CardContent>
        {links.length ? (
          <div className="km-artifact-list">
            {links.map(([label, href]) => (
              <a href={href} key={`${label}-${href}`}>
                <Download aria-hidden="true" />
                <span>{label}</span>
              </a>
            ))}
          </div>
        ) : (
          <div className="km-empty-state">No artifacts for the active job.</div>
        )}
      </CardContent>
    </Card>
  );
}

function JobsPanel({ jobs, onSelect }: { jobs: ConversionJobPayload[]; onSelect: (job: ConversionJobPayload) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent jobs</CardTitle>
        <CardDescription>Fast scan of local conversion state.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="km-job-table" role="table" aria-label="Recent jobs">
          <div role="row" className="km-job-row is-header">
            <span>File</span>
            <span>Status</span>
            <span>Quality</span>
            <span>Action</span>
          </div>
          {jobs.length ? (
            jobs.map((job) => {
              const quality = normalizeQualityState(job.quality_state ?? null);
              return (
                <div role="row" className="km-job-row" key={job.job_id || job.filename}>
                  <span>{job.filename || job.job_id || "Unnamed job"}</span>
                  <span>{job.status || "unknown"}</span>
                  <span>{quality.label}</span>
                  <Button variant="outline" size="sm" onClick={() => onSelect(job)}>
                    Open
                  </Button>
                </div>
              );
            })
          ) : (
            <div className="km-empty-state">No recent jobs reported.</div>
          )}
        </div>
      </CardContent>
    </Card>
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
    <Card>
      <CardHeader>
        <CardTitle>Error and debug panel</CardTitle>
        <CardDescription>{sentryEventId ? `Sentry event: ${sentryEventId}` : "No Sentry event for the active job."}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="km-debug-toolbar">
          <Badge variant={error ? "destructive" : "secondary"}>{error ? "Error" : "Healthy"}</Badge>
          {error ? (
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger>
                <Button variant="destructive" size="sm">
                  <AlertTriangle data-icon="inline-start" aria-hidden="true" />
                  Inspect
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Conversion error</DialogTitle>
                  <DialogDescription>{error}</DialogDescription>
                </DialogHeader>
              </DialogContent>
            </Dialog>
          ) : (
            <span>Payload zadania jest gotowy do wglądu.</span>
          )}
        </div>
        <pre className="km-debug-pre">{debugText}</pre>
      </CardContent>
    </Card>
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

function formatIssue(item: string | Record<string, unknown>) {
  if (typeof item === "string") return item;
  return String(item.message ?? item.code ?? item.title ?? JSON.stringify(item));
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
