const STRIPE_API_BASE = "https://api.stripe.com/v1";

function appendFormValue(form: URLSearchParams, key: string, value: unknown) {
  if (value === undefined || value === null || value === "") return;

  if (Array.isArray(value)) {
    value.forEach((item, index) => appendFormValue(form, `${key}[${index}]`, item));
    return;
  }

  if (typeof value === "object") {
    Object.entries(value as Record<string, unknown>).forEach(([nestedKey, nestedValue]) => {
      appendFormValue(form, `${key}[${nestedKey}]`, nestedValue);
    });
    return;
  }

  form.append(key, String(value));
}

export async function stripeRequest(secretKey: string, path: string, { method = "POST", formData }: { method?: string; formData?: Record<string, unknown> } = {}) {
  if (!secretKey) throw new Error("Missing STRIPE_SECRET_KEY.");

  const headers: Record<string, string> = {
    Authorization: `Bearer ${secretKey}`,
  };

  let body: string | undefined;
  if (formData && method !== "GET") {
    const form = new URLSearchParams();
    Object.entries(formData).forEach(([key, value]) => appendFormValue(form, key, value));
    body = form.toString();
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  }

  const response = await fetch(`${STRIPE_API_BASE}${path}`, {
    method,
    headers,
    body,
  });

  const text = await response.text();
  let data: any = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {}

  if (!response.ok) {
    throw new Error(data?.error?.message || text || `Stripe HTTP ${response.status}`);
  }

  return data || {};
}

function parseStripeSignature(header = "") {
  const result: { timestamp: number; signatures: string[] } = {
    timestamp: 0,
    signatures: [],
  };

  header
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .forEach((part) => {
      const [key, value] = part.split("=", 2);
      if (key === "t") result.timestamp = Number(value || 0);
      if (key === "v1" && value) result.signatures.push(value);
    });

  return result;
}

function timingSafeEqual(left: string, right: string) {
  if (left.length !== right.length) return false;

  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) {
    mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return mismatch === 0;
}

async function computeHmacSha256Hex(secret: string, payload: string) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    {
      name: "HMAC",
      hash: "SHA-256",
    },
    false,
    ["sign"]
  );

  const signatureBuffer = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return [...new Uint8Array(signatureBuffer)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

export async function verifyStripeWebhookSignature(payload: string, signatureHeader: string, endpointSecret: string, toleranceSeconds = 300) {
  if (!endpointSecret) throw new Error("Missing STRIPE_WEBHOOK_SECRET.");

  const { timestamp, signatures } = parseStripeSignature(signatureHeader);
  if (!timestamp || !signatures.length) throw new Error("Missing Stripe-Signature header.");

  const nowInSeconds = Math.floor(Date.now() / 1000);
  if (Math.abs(nowInSeconds - timestamp) > toleranceSeconds) {
    throw new Error("Stripe webhook signature timestamp is outside the allowed tolerance.");
  }

  const expectedSignature = await computeHmacSha256Hex(endpointSecret, `${timestamp}.${payload}`);
  const valid = signatures.some((candidate) => timingSafeEqual(candidate, expectedSignature));
  if (!valid) throw new Error("Invalid Stripe webhook signature.");
}

export function unixSecondsToIso(value: number | null | undefined) {
  const seconds = Number(value || 0);
  return seconds > 0 ? new Date(seconds * 1000).toISOString() : null;
}
