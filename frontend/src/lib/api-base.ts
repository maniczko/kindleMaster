const ABSOLUTE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:/i;

export function normalizeApiBaseUrl(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  return raw.replace(/\/+$/, "");
}

export function configuredApiBaseUrl(): string {
  return normalizeApiBaseUrl(import.meta.env.VITE_KINDLEMASTER_API_BASE_URL);
}

export function apiUrl(pathOrUrl: string, baseUrl = configuredApiBaseUrl()): string {
  const value = String(pathOrUrl || "").trim();
  if (!value || ABSOLUTE_URL_PATTERN.test(value)) return value;
  const normalizedBase = normalizeApiBaseUrl(baseUrl);
  if (!normalizedBase) return value;
  const normalizedPath = value.startsWith("/") ? value : `/${value}`;
  return `${normalizedBase}${normalizedPath}`;
}

export function apiRequestInput(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input === "string") return apiUrl(input);
  if (input instanceof URL) return apiUrl(input.toString());
  return input;
}
