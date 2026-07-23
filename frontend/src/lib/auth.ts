import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";

export const FEN_REVIEW_SESSION_TOKEN_KEY = "kindlemaster.fen-review.access-token";

export interface AuthConfigPayload {
  enabled?: boolean;
  configured?: boolean;
  provider?: string;
  supabase_url?: string;
  publishable_key?: string;
  require_login?: boolean;
  missing_config?: string[];
}

export interface AccountState {
  authenticated: boolean;
  userId: string;
  emailMasked: string;
  email: string;
}

export const anonymousAccount: AccountState = {
  authenticated: false,
  userId: "",
  emailMasked: "",
  email: "",
};

export function createKindleMasterAuthClient(config: AuthConfigPayload): SupabaseClient | null {
  if (!config.enabled || !config.configured || !config.supabase_url || !config.publishable_key) {
    return null;
  }
  return createClient(config.supabase_url, config.publishable_key);
}

export function accountFromSession(session: Session | null | undefined): AccountState {
  const user = session?.user;
  if (!user?.id) return anonymousAccount;
  const email = user.email ?? "";
  return {
    authenticated: true,
    userId: user.id,
    email,
    emailMasked: maskEmail(email),
  };
}

export async function accessTokenFromClient(client: SupabaseClient | null): Promise<string> {
  if (!client) return "";
  const { data } = await client.auth.getSession();
  return data.session?.access_token ?? "";
}

export function storeFenReviewSessionToken(token: string | null | undefined): void {
  if (typeof window === "undefined") return;
  const normalized = String(token ?? "").trim();
  try {
    if (normalized) {
      window.sessionStorage?.setItem(FEN_REVIEW_SESSION_TOKEN_KEY, normalized);
    } else {
      window.sessionStorage?.removeItem(FEN_REVIEW_SESSION_TOKEN_KEY);
    }
  } catch {
    // Some privacy modes disable storage; the authenticated API still remains usable.
  }
}

export function maskEmail(email: string): string {
  const normalized = email.trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(normalized)) return "";
  const [local, domain] = normalized.split("@");
  return `${local[0] ?? ""}***@${domain}`;
}
