import { apiUrl, normalizeApiBaseUrl } from "./api-base";
import { describe, expect, it } from "vitest";

describe("api base URL helpers", () => {
  it("keeps local relative paths unchanged when no API base is configured", () => {
    expect(apiUrl("/convert/jobs", "")).toBe("/convert/jobs");
    expect(apiUrl("convert/jobs", "")).toBe("convert/jobs");
  });

  it("prefixes relative API paths for split Vercel/Railway deployments", () => {
    expect(apiUrl("/convert/jobs", "https://kindlemaster-api.up.railway.app/")).toBe(
      "https://kindlemaster-api.up.railway.app/convert/jobs",
    );
    expect(apiUrl("auth/config", "https://kindlemaster-api.up.railway.app")).toBe(
      "https://kindlemaster-api.up.railway.app/auth/config",
    );
  });

  it("does not rewrite absolute or browser-managed URLs", () => {
    expect(apiUrl("https://api.example.com/convert/jobs", "https://railway.example")).toBe("https://api.example.com/convert/jobs");
    expect(apiUrl("blob:https://app.example/id", "https://railway.example")).toBe("blob:https://app.example/id");
  });

  it("normalizes trailing slashes from configured API base", () => {
    expect(normalizeApiBaseUrl(" https://railway.example/// ")).toBe("https://railway.example");
  });
});
