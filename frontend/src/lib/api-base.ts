const ABSOLUTE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:/i;
const GUEST_ACCESS_STORAGE_KEY = "kindlemaster.guest-access.v1";
export const GUEST_ACCESS_HEADER = "X-KindleMaster-Guest-Id";
export const GUEST_ACCESS_QUERY_PARAM = "km_guest";
const GUEST_ACCESS_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{19,127}$/;

export function normalizeApiBaseUrl(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  return raw.replace(/\/+$/, "");
}

export function configuredApiBaseUrl(): string {
  return normalizeApiBaseUrl(import.meta.env.VITE_KINDLEMASTER_API_BASE_URL);
}

export function getOrCreateGuestAccessId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage?.getItem(GUEST_ACCESS_STORAGE_KEY) ?? "";
  if (GUEST_ACCESS_PATTERN.test(existing)) return existing;
  // Component tests must opt into a guest session explicitly. Browser smoke and
  // production builds still exercise the real persistent capability flow.
  if (import.meta.env.MODE === "test") return "";

  let generated = "";
  if (typeof window.crypto?.randomUUID === "function") {
    generated = window.crypto.randomUUID();
  } else if (typeof window.crypto?.getRandomValues === "function") {
    const bytes = new Uint8Array(24);
    window.crypto.getRandomValues(bytes);
    generated = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  if (!GUEST_ACCESS_PATTERN.test(generated)) return "";
  window.localStorage?.setItem(GUEST_ACCESS_STORAGE_KEY, generated);
  return generated;
}

export function appendGuestAccess(value: string, guestAccessId = getOrCreateGuestAccessId()): string {
  const normalizedGuestAccessId = String(guestAccessId || "").trim();
  if (!value || !GUEST_ACCESS_PATTERN.test(normalizedGuestAccessId)) return value;

  const hashIndex = value.indexOf("#");
  const beforeHash = hashIndex >= 0 ? value.slice(0, hashIndex) : value;
  const hash = hashIndex >= 0 ? value.slice(hashIndex) : "";
  const queryPattern = new RegExp(`(?:^|[?&])${GUEST_ACCESS_QUERY_PARAM}=`);
  if (queryPattern.test(beforeHash)) return value;
  const separator = beforeHash.includes("?") ? "&" : "?";
  return `${beforeHash}${separator}${GUEST_ACCESS_QUERY_PARAM}=${encodeURIComponent(normalizedGuestAccessId)}${hash}`;
}

function isConfiguredApiAbsoluteUrl(value: string, baseUrl: string): boolean {
  if (!baseUrl) {
    if (typeof window === "undefined") return false;
    try {
      return new URL(value).origin === window.location.origin;
    } catch {
      return false;
    }
  }
  return value === baseUrl || value.startsWith(`${baseUrl}/`);
}

export function apiRequestUrl(pathOrUrl: string, baseUrl = configuredApiBaseUrl()): string {
  const value = String(pathOrUrl || "").trim();
  if (!value) return value;
  const normalizedBase = normalizeApiBaseUrl(baseUrl);
  if (ABSOLUTE_URL_PATTERN.test(value)) return value;
  const normalizedPath = value.startsWith("/") ? value : `/${value}`;
  return normalizedBase ? `${normalizedBase}${normalizedPath}` : value;
}

export function apiUrl(
  pathOrUrl: string,
  baseUrl = configuredApiBaseUrl(),
  guestAccessId = getOrCreateGuestAccessId(),
): string {
  const value = apiRequestUrl(pathOrUrl, baseUrl);
  if (!value) return value;
  const normalizedBase = normalizeApiBaseUrl(baseUrl);
  if (ABSOLUTE_URL_PATTERN.test(value) && !isConfiguredApiAbsoluteUrl(value, normalizedBase)) return value;
  return appendGuestAccess(value, guestAccessId);
}

export function apiRequestInput(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input === "string") return apiRequestUrl(input);
  if (input instanceof URL) return apiRequestUrl(input.toString());
  return input;
}
