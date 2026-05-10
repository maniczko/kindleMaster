const NO_DATA_LABEL = "Brak danych";

export const QUALITY_STATUS_LABELS = Object.freeze({
  applied: "Zastosowano",
  blocked: "Nie publikuj",
  complete: "Kompletne",
  error: "B\u0142\u0105d",
  failed: "B\u0142\u0105d",
  not_reported: NO_DATA_LABEL,
  partial: "Cz\u0119\u015bciowe",
  passed: "Przesz\u0142o",
  passed_with_warnings: "Przesz\u0142o z ostrze\u017ceniami",
  pending: "Oczekuje",
  primary: "G\u0142\u00f3wna pr\u00f3ba",
  ready: "Gotowe",
  ready_with_review: "Do kontroli",
  release_blocked: "Blokada publikacji",
  release_ready: "Gotowe do publikacji",
  reported: "Zaraportowano",
  review: "Kontrola",
  skipped: "Pomini\u0119to",
  unavailable: "Niedost\u0119pne",
  warning: "Ostrze\u017cenie",
  warnings: "Ostrze\u017cenia",
});

export function coerceFiniteNumber(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : null;
}

export function normalizeStatusKey(value, fallback = "not_reported") {
  const candidate = value === null || value === undefined ? fallback : String(value);
  const normalized = candidate.trim().toLowerCase().replace(/[\s-]+/g, "_");
  return normalized || fallback;
}

export function formatStatusLabel(value, fallback = "not_reported") {
  const key = normalizeStatusKey(value, fallback);
  return QUALITY_STATUS_LABELS[key] || String(value ?? fallback);
}

export function normalizeQualityScore(value) {
  const score = coerceFiniteNumber(value);
  if (score === null) {
    return {
      score: null,
      roundedScore: null,
      scoreLabel: NO_DATA_LABEL,
    };
  }
  const roundedScore = Math.max(0, Math.min(100, Math.round(score)));
  return {
    score,
    roundedScore,
    scoreLabel: `${roundedScore}%`,
  };
}

export function formatQualityScore(value) {
  return normalizeQualityScore(value).scoreLabel;
}

export function normalizeQualityCompleteness(rawValue) {
  const payload = rawValue && typeof rawValue === "object" && !Array.isArray(rawValue) ? rawValue : null;
  if (!payload) return null;

  const score = normalizeQualityScore(payload.score);
  const missingSections = Array.isArray(payload.missing_sections)
    ? payload.missing_sections
    : Array.isArray(payload.missingSections) ? payload.missingSections : [];
  const notReportedSections = Array.isArray(payload.not_reported_sections)
    ? payload.not_reported_sections
    : Array.isArray(payload.notReportedSections) ? payload.notReportedSections : [];

  return {
    ...score,
    status: normalizeStatusKey(payload.status),
    statusLabel: formatStatusLabel(payload.status),
    expectedSections: coerceFiniteNumber(payload.expected_sections ?? payload.expectedSections ?? payload.expected),
    reportedSections: coerceFiniteNumber(payload.reported_sections ?? payload.reportedSections ?? payload.reported),
    missingCount: coerceFiniteNumber(payload.missing_count ?? payload.missingCount ?? payload.missing),
    notReportedCount: coerceFiniteNumber(payload.not_reported_count ?? payload.notReportedCount),
    sections: Array.isArray(payload.sections) ? payload.sections : [],
    missingSections,
    notReportedSections,
  };
}

export function normalizeQualityState(rawValue) {
  const payload = rawValue && typeof rawValue === "object" && !Array.isArray(rawValue) ? rawValue : {};
  const readingVerdict = normalizeStatusKey(payload.reading_verdict ?? payload.readingVerdict, "");
  const releaseVerdict = normalizeStatusKey(payload.release_verdict ?? payload.releaseVerdict, "");

  return {
    status: normalizeStatusKey(payload.status),
    statusLabel: formatStatusLabel(payload.status),
    readingVerdict,
    readingVerdictLabel: readingVerdict ? formatStatusLabel(readingVerdict) : NO_DATA_LABEL,
    releaseVerdict,
    releaseVerdictLabel: releaseVerdict ? formatStatusLabel(releaseVerdict) : NO_DATA_LABEL,
    qualityCompleteness: normalizeQualityCompleteness(
      payload.quality_completeness ?? payload.qualityCompleteness,
    ),
  };
}
