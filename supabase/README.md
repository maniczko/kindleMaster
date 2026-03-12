# Supabase Cloud AI setup

Nie zapisuj klucza Anthropic w tabeli ani w frontendzie.
Trzymaj go jako sekret Edge Function.

## 1. Zaloguj i połącz projekt

```bash
supabase login
supabase link --project-ref TWOJ_PROJECT_REF
```

## 2. Ustaw sekret Anthropic

```bash
supabase secrets set ANTHROPIC_API_KEY=twoj_klucz_anthropic
```

## 3. Wdróż funkcję

```bash
supabase functions deploy claude-summary
```

## 4. Skonfiguruj frontend

W zakładce `Ustawienia` wpisz:

- `Supabase URL`: `https://TWOJ_PROJECT_REF.supabase.co`
- `Publishable / anon key`: najlepiej pełny `anon JWT`

Potem kliknij `Test Supabase` i `Test Cloud AI`.

## Dlaczego nie tabela?

Zwykła tabela to zły magazyn na sekrety:

- klucz może trafić do zapytań SQL, logów i eksportów,
- łatwo go przypadkiem wystawić przez błędne RLS,
- Edge Function i tak lepiej czyta sekret bezpośrednio z `Deno.env`.
