import { getErrorMessage, json } from "../_shared/http.ts";
import { unixSecondsToIso, verifyStripeWebhookSignature } from "../_shared/stripe.ts";
import { createServiceClient } from "../_shared/supabase.ts";

const STRIPE_SECRET_KEY = Deno.env.get("STRIPE_SECRET_KEY")?.trim() || "";
const STRIPE_WEBHOOK_SECRET = Deno.env.get("STRIPE_WEBHOOK_SECRET")?.trim() || "";

function readStripeId(value: unknown) {
  if (typeof value === "string") return value.trim();
  if (value && typeof value === "object" && typeof (value as { id?: unknown }).id === "string") {
    return String((value as { id: string }).id || "").trim();
  }
  return "";
}

async function findBillingAccount(
  service: ReturnType<typeof createServiceClient>,
  {
    userId = "",
    customerId = "",
    subscriptionId = "",
  }: { userId?: string; customerId?: string; subscriptionId?: string }
) {
  if (userId) {
    const { data } = await service
      .from("billing_accounts")
      .select("*")
      .eq("user_id", userId)
      .maybeSingle();
    if (data) return data;
  }

  if (customerId) {
    const { data } = await service
      .from("billing_accounts")
      .select("*")
      .eq("stripe_customer_id", customerId)
      .maybeSingle();
    if (data) return data;
  }

  if (subscriptionId) {
    const { data } = await service
      .from("billing_accounts")
      .select("*")
      .eq("stripe_subscription_id", subscriptionId)
      .maybeSingle();
    if (data) return data;
  }

  return null;
}

async function upsertBillingAccount(
  service: ReturnType<typeof createServiceClient>,
  partial: Record<string, unknown>
) {
  const userId = String(partial.user_id || "").trim();
  if (!userId) return;

  const existing = await findBillingAccount(service, {
    userId,
    customerId: String(partial.stripe_customer_id || "").trim(),
    subscriptionId: String(partial.stripe_subscription_id || "").trim(),
  });

  const nextRow = {
    user_id: userId,
    email: String(partial.email || existing?.email || "").trim(),
    stripe_customer_id: String(partial.stripe_customer_id || existing?.stripe_customer_id || "").trim() || null,
    stripe_checkout_session_id: String(partial.stripe_checkout_session_id || existing?.stripe_checkout_session_id || "").trim() || null,
    stripe_subscription_id: String(partial.stripe_subscription_id || existing?.stripe_subscription_id || "").trim() || null,
    checkout_mode: String(partial.checkout_mode || existing?.checkout_mode || "subscription").trim() || "subscription",
    billing_status: String(partial.billing_status || existing?.billing_status || "inactive").trim() || "inactive",
    payment_status: String(partial.payment_status || existing?.payment_status || "").trim() || null,
    price_id: String(partial.price_id || existing?.price_id || "").trim() || null,
    currency: String(partial.currency || existing?.currency || "").trim() || null,
    amount_total: Number(partial.amount_total || existing?.amount_total || 0) || null,
    current_period_end: partial.current_period_end || existing?.current_period_end || null,
    last_event_type: String(partial.last_event_type || existing?.last_event_type || "").trim() || null,
    metadata:
      partial.metadata && typeof partial.metadata === "object"
        ? partial.metadata
        : existing?.metadata && typeof existing.metadata === "object"
        ? existing.metadata
        : {},
    updated_at: new Date().toISOString(),
  };

  await service.from("billing_accounts").upsert(nextRow, { onConflict: "user_id" });
}

async function syncCheckoutSession(service: ReturnType<typeof createServiceClient>, session: any, eventType: string) {
  const customerId = readStripeId(session?.customer);
  const subscriptionId = readStripeId(session?.subscription);
  const userId =
    String(session?.client_reference_id || session?.metadata?.user_id || "").trim() ||
    String((await findBillingAccount(service, { customerId, subscriptionId }))?.user_id || "").trim();

  if (!userId) return;

  const paymentStatus = String(session?.payment_status || "").trim() || "unpaid";
  const billingStatus =
    eventType === "checkout.session.expired"
      ? "checkout_expired"
      : eventType === "checkout.session.async_payment_failed"
      ? "payment_failed"
      : session?.mode === "payment" && paymentStatus === "paid"
      ? "paid"
      : paymentStatus === "paid"
      ? "active"
      : "checkout_completed";

  await upsertBillingAccount(service, {
    user_id: userId,
    email: String(session?.customer_details?.email || session?.metadata?.user_email || "").trim(),
    stripe_customer_id: customerId || null,
    stripe_checkout_session_id: String(session?.id || "").trim() || null,
    stripe_subscription_id: subscriptionId || null,
    checkout_mode: String(session?.mode || "subscription").trim() || "subscription",
    billing_status: billingStatus,
    payment_status: paymentStatus,
    price_id: String(session?.metadata?.price_id || "").trim() || null,
    currency: String(session?.currency || "").trim() || null,
    amount_total: Number(session?.amount_total || 0) || null,
    last_event_type: eventType,
    metadata: session?.metadata || {},
  });
}

async function syncSubscription(service: ReturnType<typeof createServiceClient>, subscription: any, eventType: string) {
  const customerId = readStripeId(subscription?.customer);
  const subscriptionId = String(subscription?.id || "").trim();
  const price = subscription?.items?.data?.[0]?.price || {};
  const fallbackAccount = await findBillingAccount(service, { customerId, subscriptionId });
  const userId = String(subscription?.metadata?.user_id || fallbackAccount?.user_id || "").trim();

  if (!userId) return;

  await upsertBillingAccount(service, {
    user_id: userId,
    email: String(fallbackAccount?.email || "").trim(),
    stripe_customer_id: customerId || null,
    stripe_subscription_id: subscriptionId || null,
    checkout_mode: "subscription",
    billing_status:
      eventType === "customer.subscription.deleted"
        ? "canceled"
        : String(subscription?.status || fallbackAccount?.billing_status || "inactive").trim() || "inactive",
    payment_status:
      eventType === "customer.subscription.deleted"
        ? "canceled"
        : String(fallbackAccount?.payment_status || "").trim() || null,
    price_id: String(price?.id || subscription?.metadata?.price_id || fallbackAccount?.price_id || "").trim() || null,
    currency: String(price?.currency || fallbackAccount?.currency || "").trim() || null,
    amount_total: Number(price?.unit_amount || fallbackAccount?.amount_total || 0) || null,
    current_period_end: unixSecondsToIso(subscription?.current_period_end),
    last_event_type: eventType,
    metadata: subscription?.metadata || fallbackAccount?.metadata || {},
  });
}

async function syncInvoice(service: ReturnType<typeof createServiceClient>, invoice: any, eventType: string) {
  const customerId = readStripeId(invoice?.customer);
  const subscriptionId = readStripeId(invoice?.subscription);
  const existing = await findBillingAccount(service, { customerId, subscriptionId });
  if (!existing?.user_id) return;

  const periodEnd = Number(invoice?.lines?.data?.[0]?.period?.end || 0);
  await upsertBillingAccount(service, {
    user_id: existing.user_id,
    email: existing.email || "",
    stripe_customer_id: customerId || existing.stripe_customer_id || null,
    stripe_subscription_id: subscriptionId || existing.stripe_subscription_id || null,
    billing_status:
      eventType === "invoice.payment_failed"
        ? "past_due"
        : String(existing.billing_status || "active").trim() || "active",
    payment_status: eventType === "invoice.payment_failed" ? "failed" : "paid",
    currency: String(invoice?.currency || existing.currency || "").trim() || null,
    amount_total: Number(invoice?.amount_paid || invoice?.amount_due || existing.amount_total || 0) || null,
    current_period_end: periodEnd ? unixSecondsToIso(periodEnd) : existing.current_period_end || null,
    last_event_type: eventType,
    metadata: existing.metadata || {},
  });
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    if (!STRIPE_SECRET_KEY || !STRIPE_WEBHOOK_SECRET) {
      return json({ error: "Missing STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRET in Edge Function secrets." }, 500);
    }

    const signatureHeader = req.headers.get("Stripe-Signature") || "";
    const payload = await req.text();
    await verifyStripeWebhookSignature(payload, signatureHeader, STRIPE_WEBHOOK_SECRET);

    const event = JSON.parse(payload);
    const service = createServiceClient();

    switch (String(event?.type || "").trim()) {
      case "checkout.session.completed":
      case "checkout.session.async_payment_succeeded":
      case "checkout.session.async_payment_failed":
      case "checkout.session.expired":
        await syncCheckoutSession(service, event?.data?.object || {}, event.type);
        break;
      case "customer.subscription.created":
      case "customer.subscription.updated":
      case "customer.subscription.deleted":
        await syncSubscription(service, event?.data?.object || {}, event.type);
        break;
      case "invoice.paid":
      case "invoice.payment_failed":
        await syncInvoice(service, event?.data?.object || {}, event.type);
        break;
      default:
        break;
    }

    return json({ received: true, type: event?.type || "unknown" });
  } catch (error) {
    return json({ error: getErrorMessage(error) }, 400);
  }
});
