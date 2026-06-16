# Send to Kindle Handoff

KindleMaster v1 does not automatically send EPUB files to Amazon. It supports a manual, quality-aware handoff and an optional explicit SMTP action for generated EPUB files.

## Operator Flow

1. Generate the EPUB.
2. Read the cockpit decision:
   - `Publikuj`: the EPUB is the best candidate for Send to Kindle.
   - `Kontrola`: the EPUB can be inspected, but review the listed reasons first.
   - `Nie publikuj`: download is only a draft/inspection copy; fix blockers before sending it as final.
3. Download the EPUB or draft EPUB.
4. Open the quality JSON/report if the cockpit shows warnings or blockers.
5. Prefer sending a clean EPUB through the user's chosen Send to Kindle method.
6. If SMTP is configured, the explicit email action may send any generated EPUB. Non-`release_ready` files must keep visible quality warnings; they are not publication-ready, but email transport itself is allowed.

## Minimum Conditions Before Sending

- EPUB validation is passed or the remaining issue is explicitly accepted.
- Metadata title/author/language are sane.
- TOC is useful enough for navigation.
- No release blockers are active before claiming the file is publication-ready.
- Tables, images, references, and visible text artifacts are checked when the source uses them.

## Optional SMTP Delivery

The React console has a `Settings` tab for local non-secret SMTP defaults:

- enabled/disabled,
- host, port, and security mode,
- username and sender address,
- default Send-to-Kindle recipient address,
- maximum attachment size.

In guest mode these values are stored in the local user profile at `%APPDATA%\KindleMaster\profile.json` unless `KINDLEMASTER_USER_PROFILE_PATH` is set. After login they are also stored in Supabase `public.user_profiles.smtp_defaults` under the authenticated user's `user_id`. Passwords, API keys, and tokens are never stored there.

Set the secret before starting the local app:

```powershell
$env:KINDLEMASTER_SMTP_PASSWORD="secret"
```

You can also configure everything through env vars; env vars override the local profile:

```powershell
$env:KINDLEMASTER_EMAIL_DELIVERY="1"
$env:KINDLEMASTER_SMTP_HOST="smtp.example.com"
$env:KINDLEMASTER_SMTP_PORT="587"
$env:KINDLEMASTER_SMTP_SECURITY="starttls"
$env:KINDLEMASTER_SMTP_USERNAME="user-or-apikey"
$env:KINDLEMASTER_SMTP_PASSWORD="secret"
$env:KINDLEMASTER_SMTP_FROM="kindlemaster@example.com"
```

For Twilio SendGrid, use the SendGrid SMTP endpoint, for example `KINDLEMASTER_SMTP_HOST=smtp.sendgrid.net`, with SendGrid's SMTP username/password convention.

Safety rules:

- SMTP delivery is never automatic after conversion.
- SMTP delivery blocks only missing/not-ready output, oversized attachments, invalid recipients, or incomplete SMTP. Quality problems are warnings, not email transport blockers.
- `KINDLEMASTER_SMTP_PASSWORD` is the only supported secret source.
- A default Send-to-Kindle address can be stored in the user profile/Supabase settings; per-job delivery history stores only masked recipient and hash.
- If SMTP is unavailable, download/manual handoff remains the fallback.

Delivery diagnostics:

- Successful and refused SMTP attempts include sanitized `delivery.diagnostics`.
- Diagnostics report SMTP host/port/security, whether SMTP accepted the recipient, whether `From` matches the SMTP username when both are email addresses, MIME shape, attachment filename, `application/epub+zip`, `Content-Disposition: attachment`, transfer encoding, size, and SHA256.
- Raw MIME is not logged by default because it can expose recipient addresses and document content. Use the sanitized diagnostics first when Amazon accepts SMTP but the document does not appear on Kindle.

## Manual Premium Check

For premium claims, automated checks are not enough. Use Kindle Previewer or a real Kindle app/device to inspect:

- cover and library metadata,
- first chapter,
- TOC navigation,
- a section with a table,
- a section with an image or diagram,
- references/links if present,
- end matter and appendices.

Record the manual evidence using [Kindle Previewer Validation](kindle-previewer-validation.md).
