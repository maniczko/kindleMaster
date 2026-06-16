# KindleMaster V2 Reader Workflow Roadmap

Related Linear scope: VAT-215 and VAT-216. Foundation dependencies: VAT-204, VAT-205, and VAT-214.

This document defines the deferred reader and knowledge-workflow roadmap. It does not change the v1 release contract: KindleMaster v1 remains a local-first, release-grade PDF/DOCX to EPUB converter with quality evidence, not a reader app, cloud sync service, or annotation platform.

## Scope Decision

V1 owns conversion, downloadability, validation, release verdicts, quality reports, local artifact history, and audit evidence.

V2 may add guided post-conversion workflows only after the generated EPUB has a trustworthy release state. V2 features must consume the v1 quality verdict; they must not hide, downgrade, or bypass release blockers.

V2 does not require:

- cloud accounts,
- hidden credential storage,
- automatic or silent email delivery,
- full EPUB reader UI,
- shared team knowledge spaces,
- annotation sync across devices.

## Dependencies

| Dependency | Required foundation |
| --- | --- |
| VAT-204 | Local library/history for generated EPUBs with title, filename, created time, size, quality verdict, download link, and quality report link. |
| VAT-205 | Searchable quality archive and per-conversion Markdown/JSON report exports. |
| VAT-214 | Local full-text archive for generated EPUB search, linked back to the EPUB artifact and quality evidence. |

These dependencies keep V2 workflows grounded in generated artifacts and quality reports instead of creating a second source of truth.

## VAT-215: Send-to-Kindle Handoff

Product decision: V2 should build on the v1 local handoff and optional explicit SMTP delivery path rather than storing cloud credentials or sending silently. The supported direction remains "prepare and guide"; credentialed delivery must stay opt-in and quality-visible, while publication-ready claims remain quality-gated.

Supported V2 handoff:

1. Require `release_verdict = release_ready` before claiming an artifact is publication-ready.
2. Package the final EPUB together with a small handoff summary that names the title, filename, size, generated time, release verdict, and quality report path.
3. Show provider-specific instructions for the user's external delivery path, such as Amazon Send to Kindle web/app/email or Kobo/manual import.
4. Keep the actual delivery step under user control. V1 SMTP delivery is allowed as an explicit user action for any generated EPUB, with quality warnings visible when the file is not `release_ready`.
5. Preserve the quality report link so the user can inspect why the artifact was or was not eligible for a publication-ready claim.

Non-release-ready outputs:

- `ready_with_review`: allow download, explicit SMTP send, and report review, but show warnings.
- `release_blocked`: allow download and explicit SMTP send if an EPUB exists, but do not call it publication-ready.
- `failed`: do not offer handoff because no valid generated output is available.
- missing quality state: treat as review/blocker for publication claims, not as an SMTP transport blocker when a generated EPUB exists.

Privacy and security constraints:

- Do not store Kindle, Amazon, Kobo, SMTP, OAuth, or cloud credentials in V2 without a separate security design.
- Do not log recipient email addresses or device identifiers by default.
- Do not auto-send files from localhost without an explicit user action and clear destination.
- Do not persist full recipient email addresses; store only masked recipient and non-reversible hashes for delivery evidence.
- Keep all handoff artifacts local and derived from the final EPUB plus quality report.
- Make external provider limits visible when known, but do not hardcode unstable service promises into release gates.

Failure modes to document in the eventual UI or runbook:

- EPUB is not `release_ready`.
- External provider rejects the file, file size, metadata, or account configuration.
- User sends an outdated artifact instead of the latest release-ready file.
- Network or provider service is unavailable.
- The device syncs slowly or not at all after successful external upload.

Implementation placement:

- V2.0: docs, local handoff package, and the v1 explicit SMTP action from a library item.
- V2.1: richer provider-specific instructions and delivery evidence, with `release_ready` required for publication-ready labels.
- V2.2 or later: additional credentialed providers only after explicit threat model, opt-in setup, and tests proving publication quality gates cannot be bypassed.

## VAT-216: Notes, Highlights, and Exports

Product decision: V2 should support knowledge exports from trusted local artifacts before attempting a full annotation system. The roadmap should favor open, local formats first and external services second.

Primary scenarios:

- Export a conversion summary and quality context to a personal knowledge base.
- Export selected passages or future annotations with source title, section, EPUB location, and quality verdict.
- Preserve links from exported knowledge back to the local library item, EPUB download, and quality report.
- Keep low-confidence OCR or review-only text labeled with quality context.

Export priority:

| Rank | Target | Decision |
| --- | --- | --- |
| 1 | Markdown and Obsidian | First-class local export. Use readable Markdown files with front matter for title, author, source filename, generated time, release verdict, quality report path, and stable local IDs. This gives immediate value without accounts. |
| 2 | JSON | Stable machine-readable sidecar for automation, future importers, and regression testing. JSON should mirror report truth instead of inventing a new status model. |
| 3 | Readwise | Later integration target. Start with manual/import-compatible exports or documented mapping; defer API sync until account, rate-limit, and privacy handling are designed. |
| 4 | Kindle notes/highlights import | Later compatibility target. Useful only after local library, full-text search, and source-location mapping are reliable enough to avoid mismatching highlights. |

V2 annotation roadmap:

1. Export-ready quality summaries from existing reports.
2. Local passage selection or imported notes tied to generated EPUB locations.
3. Obsidian/Markdown export with quality context.
4. JSON export contract for automation.
5. Optional Readwise-compatible export once target fields and privacy expectations are validated.

Deferred scope:

- no full annotation editor in v1,
- no cloud sync in v1,
- no Readwise API integration in v1,
- no team/workspace knowledge features in v1,
- no export of low-quality OCR text without visible quality context.

## Release-Gate Rule

No v1 release gate depends on Send-to-Kindle, notes, highlights, Obsidian export, Readwise export, or any other reader workflow. These features can improve benchmark comparison later, but Score 9 v1 remains measured against release-grade conversion quality and evidence.

## Open Product Questions

- Which delivery target should V2 validate first: Amazon Send to Kindle web/app/email, Kobo/manual import, or generic local handoff?
- Should `ready_with_review` ever allow a handoff package for advanced users, or should all guided delivery stay strictly `release_ready`?
- Should Obsidian export use one note per book, one note per chapter, or one note per passage once annotations exist?
- Should Readwise support be import-file-only first, or should API sync be planned as a separate opt-in integration?
