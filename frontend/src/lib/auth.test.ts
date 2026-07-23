import { beforeEach, describe, expect, it, vi } from "vitest";

import { FEN_REVIEW_SESSION_TOKEN_KEY, storeFenReviewSessionToken } from "./auth";

describe("FEN review session bridge", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("stores the active access token for same-tab review navigation", () => {
    storeFenReviewSessionToken("  signed-session-token  ");

    expect(window.sessionStorage.getItem(FEN_REVIEW_SESSION_TOKEN_KEY)).toBe(
      "signed-session-token",
    );
  });

  it("clears the bridge when the session ends", () => {
    window.sessionStorage.setItem(FEN_REVIEW_SESSION_TOKEN_KEY, "stale-token");

    storeFenReviewSessionToken("");

    expect(window.sessionStorage.getItem(FEN_REVIEW_SESSION_TOKEN_KEY)).toBeNull();
  });

  it("does not break authentication when browser storage is unavailable", () => {
    const storageSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage disabled", "SecurityError");
    });

    expect(() => storeFenReviewSessionToken("signed-session-token")).not.toThrow();

    storageSpy.mockRestore();
  });
});
