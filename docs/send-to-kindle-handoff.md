# Send to Kindle Handoff

KindleMaster v1 does not automatically send EPUB files to Amazon. The intended handoff is manual and quality-aware.

## Operator Flow

1. Generate the EPUB.
2. Read the cockpit decision:
   - `Publikuj`: the EPUB is the best candidate for Send to Kindle.
   - `Kontrola`: the EPUB can be inspected, but review the listed reasons first.
   - `Nie publikuj`: download is only a draft/inspection copy; fix blockers before sending it as final.
3. Download the EPUB or draft EPUB.
4. Open the quality JSON/report if the cockpit shows warnings or blockers.
5. Send only a clean or accepted-review EPUB through the user's chosen Send to Kindle method.

## Minimum Conditions Before Sending

- EPUB validation is passed or the remaining issue is explicitly accepted.
- Metadata title/author/language are sane.
- TOC is useful enough for navigation.
- No release blockers are active.
- Tables, images, references, and visible text artifacts are checked when the source uses them.

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
