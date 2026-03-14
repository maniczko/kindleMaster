import { corsHeaders } from "../_shared/cors.ts";
import { getErrorMessage, json } from "../_shared/http.ts";
import { stripeRequest } from "../_shared/stripe.ts";
import { createServiceClient, requireUser } from "../_shared/supabase.ts";

const STRIPE_SECRET_KEY = Deno.env.get("STRIPE_SECRET_KEY")?.trim() || "";
const APP_BASE_URL = Deno.env.get("APP_BASE_URL")?.trim() || "";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    if (!STRIPE_SECRET_KEY) {
      return json({ error: "Missing STRIPE_SECRET_KEY in Edge Function secrets." }, 500);
    }

    const user = await requireUser(req);
    const service = createServiceClient();
    const body = await req.json().catch(() => ({}));
    const baseUrl = APP_BASE_URL || String(body?.baseUrl || req.headers.get("origin") || "").trim();
    if (!baseUrl) {
      return json({ error: "Missing APP_BASE_URL secret or request origin." }, 500);
    }

    const returnPath = String(body?.returnPath || "/").trim() || "/";
    const returnUrl = new URL(returnPath, baseUrl).toString();
    const { data: billingAccount } = await service
      .from("billing_accounts")
      .select("stripe_customer_id")
      .eq("user_id", user.id)
      .maybeSingle();

    const stripeCustomerId = String(billingAccount?.stripe_customer_id || "").trim();
    if (!stripeCustomerId) {
      return json({ error: "User does not have an active Stripe customer yet." }, 400);
    }

    const session = await stripeRequest(STRIPE_SECRET_KEY, "/billing_portal/sessions", {
      method: "POST",
      formData: {
        customer: stripeCustomerId,
        return_url: returnUrl,
      },
    });

    if (!session?.url) {
      throw new Error("Stripe did not return a billing portal URL.");
    }

    return json({
      ok: true,
      portalUrl: session.url,
    });
  } catch (error) {
    const message = getErrorMessage(error);
    const status = /authorization|user session/i.test(message) ? 401 : 500;
    return json({ error: message }, status);
  }
});
