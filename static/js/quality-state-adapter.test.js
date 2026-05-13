import { describe, expect, it } from "vitest";

import {
  formatQualityScore,
  formatStatusLabel,
  normalizeQualityCompleteness,
  normalizeQualityState,
} from "./quality-state-adapter.js";

describe("quality-state adapter score mapping", () => {
  it("rounds and clamps quality completeness scores for UI display", () => {
    expect(formatQualityScore("88.6")).toBe("89%");
    expect(formatQualityScore(-4.4)).toBe("0%");
    expect(formatQualityScore(123.4)).toBe("100%");
  });

  it("keeps missing or invalid scores as no-data instead of converting them to zero", () => {
    expect(formatQualityScore(null)).toBe("Brak danych");
    expect(formatQualityScore("")).toBe("Brak danych");
    expect(formatQualityScore("not-a-number")).toBe("Brak danych");
  });

  it("normalizes snake_case and camelCase completeness fields", () => {
    const normalized = normalizeQualityCompleteness({
      score: "15.2",
      status: "partial",
      expected_sections: "13",
      reportedSections: 2,
      missing_count: "11",
      notReportedCount: "11",
      missingSections: ["text_cleanup"],
      not_reported_sections: ["semantic_cleanup"],
      sections: [{ key: "validation", reported: true, status: "passed" }],
    });

    expect(normalized).toMatchObject({
      score: 15.2,
      roundedScore: 15,
      scoreLabel: "15%",
      status: "partial",
      statusLabel: "Cz\u0119\u015bciowe",
      expectedSections: 13,
      reportedSections: 2,
      missingCount: 11,
      notReportedCount: 11,
      missingSections: ["text_cleanup"],
      notReportedSections: ["semantic_cleanup"],
    });
  });
});

describe("quality-state adapter status labels", () => {
  it("maps backend quality statuses to static UI labels", () => {
    expect(formatStatusLabel("passed")).toBe("Przesz\u0142o");
    expect(formatStatusLabel("passed_with_warnings")).toBe("Przesz\u0142o z ostrze\u017ceniami");
    expect(formatStatusLabel("failed")).toBe("B\u0142\u0105d");
    expect(formatStatusLabel("release_ready")).toBe("Gotowe do publikacji");
    expect(formatStatusLabel("ready_with_review")).toBe("Do kontroli");
    expect(formatStatusLabel("release_blocked")).toBe("Blokada publikacji");
    expect(formatStatusLabel("not_reported")).toBe("Brak danych");
  });

  it("is case-insensitive and tolerates hyphenated status aliases", () => {
    expect(formatStatusLabel("FAILED")).toBe("B\u0142\u0105d");
    expect(formatStatusLabel("ready-with-review")).toBe("Do kontroli");
  });

  it("preserves unknown statuses for forward-compatible UI display", () => {
    expect(formatStatusLabel("previewer_passed")).toBe("previewer_passed");
  });

  it("normalizes quality_state verdict fields and score data together", () => {
    const normalized = normalizeQualityState({
      status: "ready",
      reading_verdict: "ready_with_review",
      release_verdict: "release_ready",
      quality_completeness: {
        score: 99.6,
        status: "complete",
      },
    });

    expect(normalized).toMatchObject({
      status: "ready",
      statusLabel: "Gotowe",
      readingVerdict: "ready_with_review",
      readingVerdictLabel: "Do kontroli",
      releaseVerdict: "release_ready",
      releaseVerdictLabel: "Gotowe do publikacji",
      qualityCompleteness: {
        score: 99.6,
        roundedScore: 100,
        scoreLabel: "100%",
        status: "complete",
        statusLabel: "Kompletne",
      },
    });
  });
});
