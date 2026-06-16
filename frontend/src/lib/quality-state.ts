export type QualityStatus = "processing" | "needs_review" | "kindle_ready" | "premium_ready" | "failed";

export interface QualityStatePayload {
  score?: number | null;
  sendable?: boolean | null;
  kindle_ready?: boolean | null;
  premium_ready?: boolean | null;
  status?: string | null;
  phase?: string | null;
  reading_verdict?: string | null;
  release_verdict?: string | null;
  release_blocked?: boolean | null;
  send_to_kindle_ready?: boolean | null;
  send_to_kindle_blockers?: Array<Record<string, unknown>> | null;
  quality_blockers?: Array<Record<string, unknown>> | null;
  blockers?: Array<Record<string, unknown>> | null;
  warnings?: Array<string | Record<string, unknown>> | number | null;
  reports?: Record<string, string> | null;
  artifacts?: Record<string, string> | null;
  auto_repair?: Record<string, unknown> | null;
  sentry_event_id?: string | null;
  summary?: Record<string, unknown> | null;
  user_facing_verdict?: {
    label?: string;
    detail?: string;
    decision?: string;
  } | null;
}

export interface NormalizedQualityState {
  status: QualityStatus;
  label: string;
  detail: string;
  score: number;
  blockers: Array<Record<string, unknown>>;
  warnings: Array<string | Record<string, unknown>>;
  sendToKindleReady: boolean | null;
  sendToKindleBlockers: Array<Record<string, unknown>>;
  reports: Record<string, string>;
  artifacts: Record<string, string>;
  sentryEventId: string;
}

const STATUS_LABELS: Record<QualityStatus, string> = {
  processing: "Przetwarzanie",
  needs_review: "Wymaga kontroli",
  kindle_ready: "Gotowe na Kindle",
  premium_ready: "Gotowe premium",
  failed: "Błąd",
};

const STATUS_DETAILS: Record<QualityStatus, string> = {
  processing: "Konwersja jest w toku albo czeka na pierwsze dane jakości.",
  needs_review: "EPUB jest dostępny, ale bramka jakości wymaga kontroli.",
  kindle_ready: "Plik wygląda na gotowy do wysłania na Kindle.",
  premium_ready: "Plik spełnia próg premium i może iść dalej bez ręcznej blokady.",
  failed: "Konwersja albo walidacja zgłosiła błąd blokujący.",
};

export function normalizeQualityState(payload?: QualityStatePayload | null): NormalizedQualityState {
  const state = payload && typeof payload === "object" ? payload : {};
  const blockers = normalizeIssueList(state.quality_blockers ?? state.blockers);
  const sendToKindleBlockers = normalizeIssueList(state.send_to_kindle_blockers);
  const warnings = normalizeWarningList(state.warnings);
  const releaseVerdict = String(state.release_verdict ?? "").toLowerCase();
  const readingVerdict = String(state.reading_verdict ?? "").toLowerCase();
  const rawStatus = String(state.status ?? "").toLowerCase();
  const userDecision = String(state.user_facing_verdict?.decision ?? "").toLowerCase();

  let status: QualityStatus = "processing";
  if (rawStatus === "failed" || releaseVerdict === "failed" || readingVerdict === "failed") {
    status = "failed";
  } else if (state.premium_ready === true || releaseVerdict === "release_ready" || userDecision === "ready") {
    status = "premium_ready";
  } else if (state.kindle_ready === true || state.sendable === true || readingVerdict === "ready") {
    status = "kindle_ready";
  } else if (blockers.length || warnings.length || state.release_blocked || releaseVerdict === "ready_with_review") {
    status = "needs_review";
  } else if (rawStatus === "ready" || state.phase === "completed") {
    status = "needs_review";
  }

  const score = clampScore(Number(state.score ?? state.summary?.score ?? state.summary?.quality_score ?? 0));
  return {
    status,
    label: state.user_facing_verdict?.label || STATUS_LABELS[status],
    detail: state.user_facing_verdict?.detail || STATUS_DETAILS[status],
    score,
    blockers,
    warnings,
    sendToKindleReady: typeof state.send_to_kindle_ready === "boolean" ? state.send_to_kindle_ready : null,
    sendToKindleBlockers,
    reports: normalizeRecord(state.reports),
    artifacts: normalizeRecord(state.artifacts),
    sentryEventId: String(state.sentry_event_id ?? ""),
  };
}

function normalizeIssueList(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)));
}

function normalizeWarningList(value: unknown): Array<string | Record<string, unknown>> {
  if (typeof value === "number") return value > 0 ? [`${value} ostrzeżenie/ostrzeżeń`] : [];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string | Record<string, unknown> => {
    return typeof item === "string" || Boolean(item && typeof item === "object" && !Array.isArray(item));
  });
}

function normalizeRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length > 0),
  );
}

function clampScore(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

