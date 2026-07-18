import { apiRequestInput, apiRequestUrl, apiUrl, appendGuestAccess, normalizeApiBaseUrl } from "./api-base";
import { describe, expect, it } from "vitest";

const guestAccessId = "guest-session-0123456789abcdef";

describe("api base URL helpers", () => {
  it("keeps local relative request paths unchanged", () => {
    expect(apiRequestUrl("/convert/jobs", "")).toBe("/convert/jobs");
    expect(apiRequestUrl("convert/jobs", "")).toBe("convert/jobs");
    expect(apiRequestInput("/user/profile")).toBe("/user/profile");
  });

  it("prefixes relative API request paths for split Vercel/Railway deployments", () => {
    expect(apiRequestUrl("/convert/jobs", "https://kindlemaster-api.up.railway.app/")).toBe(
      "https://kindlemaster-api.up.railway.app/convert/jobs",
    );
    expect(apiRequestUrl("auth/config", "https://kindlemaster-api.up.railway.app")).toBe(
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

  it("adds an opaque guest capability only to direct browser API links", () => {
    expect(apiUrl("/convert/jobs", "https://railway.example", guestAccessId)).toBe(
      `https://railway.example/convert/jobs?km_guest=${guestAccessId}`,
    );
    expect(apiUrl("convert/status/job-1?full=1#quality", "", guestAccessId)).toBe(
      `convert/status/job-1?full=1&km_guest=${guestAccessId}#quality`,
    );
    expect(apiRequestUrl("/convert/jobs", "https://railway.example")).toBe(
      "https://railway.example/convert/jobs",
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
