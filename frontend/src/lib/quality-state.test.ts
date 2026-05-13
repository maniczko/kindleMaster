import { describe, expect, it } from "vitest";

import { normalizeQualityState } from "./quality-state";

describe("normalizeQualityState", () => {
  it("maps premium ready release payloads to the premium ready label", () => {
    const state = normalizeQualityState({
      score: 94,
      premium_ready: true,
      release_verdict: "release_ready",
      reports: { json: "/convert/report/job.json" },
      artifacts: { epub: "/convert/download/job" },
    });

    expect(state.status).toBe("premium_ready");
    expect(state.label).toBe("Premium ready");
    expect(state.score).toBe(94);
    expect(state.reports.json).toBe("/convert/report/job.json");
    expect(state.artifacts.epub).toBe("/convert/download/job");
  });

  it("keeps blockers and warnings in needs review instead of marking sendable", () => {
    const state = normalizeQualityState({
      score: 73.4,
      release_verdict: "ready_with_review",
      quality_blockers: [{ code: "toc_noise", message: "TOC needs review" }],
      warnings: ["Publisher metadata missing"],
    });

    expect(state.status).toBe("needs_review");
    expect(state.score).toBe(73);
    expect(state.blockers).toHaveLength(1);
    expect(state.warnings).toHaveLength(1);
  });

  it("maps failed conversion payloads to failed with sentry context", () => {
    const state = normalizeQualityState({
      status: "failed",
      sentry_event_id: "event-123",
    });

    expect(state.status).toBe("failed");
    expect(state.sentryEventId).toBe("event-123");
  });
});

