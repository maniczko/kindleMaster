import { apiUrl, appendGuestAccess, normalizeApiBaseUrl } from "./api-base";
import { describe, expect, it } from "vitest";

const guestAccessId = "guest-session-0123456789abcdef";

describe("api base URL helpers", () => {
  it("keeps local relative paths unchanged when guest access is disabled", () => {
    expect(apiUrl("/convert/jobs", "", "")).toBe("/convert/jobs");
    expect(apiUrl("convert/jobs", "", "")).toBe("convert/jobs");
  });

  it("prefixes relative API paths for split Vercel/Railway deployments", () => {
    expect(apiUrl("/convert/jobs", "https://kindlemaster-api.up.railway.app/", "")).toBe(
      "https://kindlemaster-api.up.railway.app/convert/jobs",
    );
    expect(apiUrl("auth/config", "https://kindlemaster-api.up.railway.app", "")).toBe(
      "https://kindlemaster-api.up.railway.app/auth/config",
    );
  });

  it("does not rewrite unrelated absolute or browser-managed URLs", () => {
    expect(apiUrl("https://api.example.com/convert/jobs", "https://railway.example", guestAccessId)).toBe(
      "https://api.example.com/convert/jobs",
    );
    expect(apiUrl("blob:https://app.example/id", "https://railway.example", guestAccessId)).toBe(
      "blob:https://app.example/id",
    );
  });

  it("adds an opaque guest capability to Railway and local API URLs", () => {
    expect(apiUrl("/convert/jobs", "https://railway.example", guestAccessId)).toBe(
      `https://railway.example/convert/jobs?km_guest=${guestAccessId}`,
    );
    expect(apiUrl("convert/status/job-1?full=1#quality", "", guestAccessId)).toBe(
      `convert/status/job-1?full=1&km_guest=${guestAccessId}#quality`,
    );
  });

  it("does not duplicate an existing guest capability", () => {
    const value = `/convert/jobs?km_guest=${guestAccessId}`;
    expect(appendGuestAccess(value, guestAccessId)).toBe(value);
  });

  it("normalizes trailing slashes from configured API base", () => {
    expect(normalizeApiBaseUrl(" https://railway.example/// ")).toBe("https://railway.example");
  });
});
