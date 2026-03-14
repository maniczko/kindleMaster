import { corsHeaders } from "../_shared/cors.ts";
import { getErrorMessage, json } from "../_shared/http.ts";
import { stripeRequest } from "../_shared/stripe.ts";
import { createServiceClient, requireUser } from "../_shared/supabase.ts";

const STRIPE_SECRET_KEY = Deno.env.get("STRIPE_SECRET_KEY")?.trim() || "";
const STRIPE_PRICE_ID = Deno.env.get("STRIPE_PRICE_ID")?.trim() || "";
const STRIPE_MONTHLY_PRICE_ID = Deno.env.get("STRIPE_MONTHLY_PRICE_ID")?.trim() || "";
const STRIPE_YEARLY_PRICE_ID = Deno.env.get("STRIPE_YEARLY_PRICE_ID")?.trim() || "";
const STRIPE_CHECKOUT_MODE = Deno.env.get("STRIPE_CHECKOUT_MODE")?.trim() || "subscription";
const APP_BASE_URL = Deno.env.get("APP_BASE_URL")?.trim() || "";
const BILLING_PLAN_NAME = Deno.env.get("BILLING_PLAN_NAME")?.trim() || "Zen Quiz Pro";
const DEFAULT_BILLING_INTERVAL = Deno.env.get("DEFAULT_BILLING_INTERVAL")?.trim().toLowerCase() || "monthly";

function resolveBillingSelection(requestedInterval = "") {
  const normalizedInterval = String(requestedInterval || DEFAULT_BILLING_INTERVAL || "monthly").trim().toLowerCase();
  const supported = {
    monthly: STRIPE_MONTHLY_PRICE_ID || STRIPE_PRICE_ID,
    yearly: STRIPE_YEARLY_PRICE_ID || STRIPE_PRICE_ID,
  };

  if (normalizedInterval === "yearly" && supported.yearly) {
    return { billingInterval: "yearly", priceId: supported.yearly };
  }

  if (normalizedInterval === "monthly" && supported.monthly) {
    return { billingInterval: "monthly", priceId: supported.monthly };
  }

  return {
    billingInterval: supported.yearly && DEFAULT_BILLING_INTERVAL === "yearly" ? "yearly" : "monthly",
    priceId: STRIPE_PRICE_ID || supported.monthly || supported.yearly,
  };
}

function buildRedirectUrl(baseUrl: string, returnPath = "/", checkoutState = "success") {
  const url = new URL(returnPath || "/", baseUrl);
  url.searchParams.set("checkout", checkoutState);
  return url.toString();
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    if (!STRIPE_SECRET_KEY || (!STRIPE_PRICE_ID && !STRIPE_MONTHLY_PRICE_ID && !STRIPE_YEARLY_PRICE_ID)) {
      return json({ error: "Missing STRIPE_SECRET_KEY and at least one Stripe price ID in Edge Function secrets." }, 500);
    }

    const user = await requireUser(req);
    const service = createServiceClient();
    const body = await req.json().catch(() => ({}));
    const baseUrl = APP_BASE_URL || String(body?.baseUrl || req.headers.get("origin") || "").trim();
    if (!baseUrl) {
      return json({ error: "Missing APP_BASE_URL secret or request origin." }, 500);
    }

    const returnPath = String(body?.returnPath || "/").trim() || "/";
    const { billingInterval, priceId } = resolveBillingSelection(String(body?.billingInterval || "").trim());
    const { data: billingAccount } = await service
      .from("billing_accounts")
      .select("user_id,email,stripe_customer_id,billing_status")
      .eq("user_id", user.id)
      .maybeSingle();

    const metadata = {
      user_id: user.id,
      user_email: user.email || "",
      price_id: priceId,
      plan_name: BILLING_PLAN_NAME,
      billing_interval: billingInterval,
    };

    const session = await stripeRequest(STRIPE_SECRET_KEY, "/checkout/sessions", {
      method: "POST",
      formData: {
        mode: STRIPE_CHECKOUT_MODE,
        success_url: buildRedirectUrl(baseUrl, returnPath, "success"),
        cancel_url: buildRedirectUrl(baseUrl, returnPath, "cancelled"),
        allow_promotion_codes: true,
        client_reference_id: user.id,
        payment_method_types: ["card"],
        line_items: [{ price: priceId, quantity: 1 }],
        customer: billingAccount?.stripe_customer_id || undefined,
        customer_email: billingAccount?.stripe_customer_id ? undefined : user.email || undefined,
        customer_creation: billingAccount?.stripe_customer_id ? undefined : STRIPE_CHECKOUT_MODE === "payment" ? "always" : undefined,
        metadata,
        subscription_data: STRIPE_CHECKOUT_MODE === "subscription" ? { metadata } : undefined,
      },
    });

    await service.from("billing_accounts").upsert(
      {
        user_id: user.id,
        email: user.email || billingAccount?.email || "",
        stripe_customer_id: String(session?.customer || billingAccount?.stripe_customer_id || "").trim() || null,
        stripe_checkout_session_id: String(session?.id || "").trim() || null,
        stripe_subscription_id: String(session?.subscription || "").trim() || null,
        checkout_mode: STRIPE_CHECKOUT_MODE,
        billing_status: String(billingAccount?.billing_status || "checkout_started").trim() || "checkout_started",
        payment_status: String(session?.payment_status || "unpaid").trim() || "unpaid",
        price_id: priceId,
        currency: String(session?.currency || "").trim() || null,
        amount_total: Number(session?.amount_total || 0) || null,
        last_event_type: "checkout.session.created",
        metadata,
        updated_at: new Date().toISOString(),
      },
      { onConflict: "user_id" }
    );

    if (!session?.url) {
      throw new Error("Stripe did not return a Checkout URL.");
    }

    return json({
      ok: true,
      mode: session.mode,
      billingInterval,
      checkoutUrl: session.url,
      sessionId: session.id,
    });
  } catch (error) {
    const message = getErrorMessage(error);
    const status = /authorization|user session/i.test(message) ? 401 : 500;
    return json({ error: message }, status);
  }
});
