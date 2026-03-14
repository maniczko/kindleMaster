# Supabase Cloud AI setup

Nie zapisuj klucza Anthropic w tabeli ani w frontendzie.
Trzymaj go jako sekret Edge Function.

## 1. Zaloguj i polacz projekt

```bash
supabase login
supabase link --project-ref TWOJ_PROJECT_REF
```

## 2. Ustaw sekret Anthropic

```bash
supabase secrets set ANTHROPIC_API_KEY=twoj_klucz_anthropic
```

## 3. Wdroz funkcje

```bash
supabase functions deploy claude-summary
```

## 4. Skonfiguruj frontend

W zakladce `Ustawienia` wpisz:

- `Supabase URL`: `https://TWOJ_PROJECT_REF.supabase.co`
- `Publishable / anon key`: najlepiej pelny `anon JWT`

Potem kliknij `Test Supabase` i `Test Cloud AI`.

## Dlaczego nie tabela?

Zwykla tabela to zly magazyn na sekrety:

- klucz moze trafic do zapytan SQL, logow i eksportow,
- latwo go przypadkiem wystawic przez bledne RLS,
- Edge Function i tak lepiej czyta sekret bezposrednio z `Deno.env`.

## Stripe billing setup

Do platnosci kartami aplikacja korzysta ze Stripe Checkout i Stripe Customer Portal przez Supabase Edge Functions.

### 1. Ustaw sekrety Stripe i Supabase

```bash
supabase secrets set STRIPE_SECRET_KEY=sk_live_...
supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...
supabase secrets set STRIPE_PRICE_ID=price_...
supabase secrets set STRIPE_CHECKOUT_MODE=subscription
supabase secrets set APP_BASE_URL=https://twoj-frontend.vercel.app
supabase secrets set BILLING_PLAN_NAME="Zen Quiz Pro"
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=twoj_service_role_key
```

Uwagi:

- `STRIPE_CHECKOUT_MODE` ustaw na `subscription` albo `payment`, zaleznie od typu `price` w Stripe.
- `APP_BASE_URL` powinien wskazywac publiczny adres frontendu, do ktorego Stripe ma wrocic po checkoutcie.
- `SUPABASE_SERVICE_ROLE_KEY` jest potrzebny webhookowi do aktualizacji tabeli `billing_accounts`.

### 2. Wdroz funkcje Edge

```bash
supabase functions deploy create-checkout-session
supabase functions deploy create-billing-portal-session
supabase functions deploy stripe-webhook
```

### 3. Skonfiguruj webhook w Stripe

Dodaj endpoint:

```text
https://TWOJ_PROJECT_REF.supabase.co/functions/v1/stripe-webhook
```

Wybierz zdarzenia:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

### 4. Uruchom migracje SQL

Nowa migracja tworzy tabele `public.billing_accounts`, z ktorej frontend odczytuje status planu i aktywacji dostepu.
