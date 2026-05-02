# Kindle Previewer Validation

KindleMaster can mark an EPUB as automated-ready, but premium claims still need manual Kindle evidence.

## Required Artifact Folder

Use one folder per validation run:

```text
reports/kindle_delivery/<case-id-or-job-id>/
```

Expected files:

- `kindle_previewer.md`
- `send_to_kindle.md`
- optional screenshots or device photos named by viewport/device, for example `previewer-eink-table.png`.

## Kindle Previewer Checklist

Record:

- EPUB path and checksum.
- Kindle Previewer version.
- Import status: `passed` or `failed`.
- Cover and library metadata.
- TOC navigation sample.
- First chapter reading sample.
- Table sample.
- Image or diagram sample.
- References, footnotes, or links sample when present.
- End matter, glossary, and appendix sample when present.

## Send To Kindle Checklist

Record:

- Delivery method: web, email, app, or device share sheet.
- File size.
- Upload/delivery status.
- Device/app opened successfully.
- Title, creator, language, and cover look correct.
- Downloaded book is readable offline.

## Status Values

Use these values when copying the result into quality metadata:

- `not_verified`: no manual evidence yet.
- `previewer_passed`: Kindle Previewer import and spot check passed.
- `send_to_kindle_passed`: delivery and reading sample passed.
- `failed`: manual validation failed.

Do not use `previewer_passed` or `send_to_kindle_passed` without evidence in the run folder.
