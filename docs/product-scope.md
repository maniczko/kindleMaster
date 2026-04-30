# KindleMaster Product Scope

Related Linear scope: VAT-197, VAT-215, and VAT-216.

This document defines the product boundary used for the Score 9 program. It is a product decision document, not a runtime specification. If implementation behavior disagrees with this document, fix the product decision or update the roadmap before treating the behavior as release-ready.

Deferred reader and knowledge workflows are defined in [docs/v2-reader-workflow-roadmap.md](v2-reader-workflow-roadmap.md). That roadmap is downstream from this v1 boundary.

## V1 Decision

KindleMaster v1 is a local-first, release-grade PDF/DOCX to EPUB converter.

The v1 product promise is:

- ingest PDF and DOCX documents locally,
- choose an appropriate conversion path for the document class,
- generate a Kindle-compatible EPUB,
- expose enough quality evidence to decide whether the EPUB is ready to read, ready with review, or blocked for publication,
- preserve generated EPUBs and quality evidence enough for operator trust and repeatable audit,
- avoid claiming reader, sync, annotation, or feed-intelligence features as part of the v1 release contract.

The v1 user should be able to answer:

- Was an EPUB generated and can I download it?
- Is the EPUB good enough to read?
- Is the EPUB good enough to publish or release?
- What exactly blocks release?
- Which reports or artifacts prove the verdict?

## V1 Non-Goals

These are not v1 blockers unless they directly affect conversion trust:

- full EPUB reader UI,
- RSS/newsletter inbox,
- cloud sync,
- mobile reading app,
- highlights and notes,
- Readwise or Obsidian export,
- Send-to-Kindle automation,
- team intelligence workflows,
- AI feed monitoring,
- TTS or listening workflows.

These features may be valuable, but they belong to v2/v3 product tracks after the release-grade converter is trustworthy. The current v2 decisions for Send-to-Kindle handoff, notes/highlights, and Obsidian/Readwise export live in [docs/v2-reader-workflow-roadmap.md](v2-reader-workflow-roadmap.md).

## Benchmark Classification

KindleMaster should use Instapaper, Feedly, and Readwise Reader as workflow benchmarks, not as products to clone in v1.

| Benchmark capability | V1 classification | Reason |
| --- | --- | --- |
| Clean saved reading view | Partial inspiration | KindleMaster prepares clean EPUBs before reading; it does not need an in-app reader for v1. |
| Offline reading | Indirectly supported | The generated EPUB can be used offline in Kindle-compatible readers. |
| Kindle/Kobo delivery | V2 handoff | Valuable after release verdicts are hard; tracked separately from v1 conversion quality. |
| Highlights and notes | V2 knowledge workflow | Depends on reader/library foundations and is not required to prove conversion quality. |
| Full-text search | V1.5 foundation | Useful for local history and quality archive; not a full reader requirement. |
| RSS/newsletter inbox | Out of v1 scope | Feed collection is a different product class from document conversion. |
| Boards/team intelligence | Out of v1 scope | Useful benchmark for organization, not a converter release gate. |
| AI reading assistant | Out of v1 scope | Should not block EPUB generation, validation, or release quality. |
| Export/API integrations | V2 workflow | Useful after quality reports, library, and full-text archive are stable. |

## Product Functionality 9/10 Criteria

For the chosen v1 scope, `Funkcjonalnosci produktu` reaches 9/10 when:

- PDF and DOCX conversion are supported through the standard CLI and web workflows.
- The main conversion flow exposes download availability separately from release readiness.
- The cockpit exposes canonical release verdict, hard blockers, review warnings, EPUBCheck, TOC, metadata, links, visible junk, assets, and report links when available.
- Generated EPUBs have a local history/library surface with title, filename, created time, size, verdict, download link, and quality report link.
- Quality reports can be searched or exported enough for audit and future knowledge workflows.
- Missing quality data is represented honestly as accepted, review, or blocker according to release policy.
- V2 reader/knowledge features are tracked but do not dilute v1 release acceptance.

A product score below 9 is expected if the app can generate EPUBs but cannot preserve or explain conversion quality after the run.

## Benchmark 9/10 Criteria

For the chosen v1 scope, `Porownanie z Instapaper/Feedly/Readwise` reaches 9/10 when:

- The benchmark is explicitly scoped as a workflow reference, not a feature parity checklist.
- Gaps are classified as v1 release blockers, v1.5 foundations, v2 reader workflow, or out-of-scope.
- The v1 converter is stronger than read-later tools at EPUB release evidence: validation, TOC, metadata, blockers, reports, and artifact audit.
- V2 items such as Send-to-Kindle, notes/highlights, and Obsidian/Readwise export have clear Linear tasks and dependencies.
- No v1 release claim depends on implementing feed reading, annotations, sync, or team intelligence.

A benchmark score below 9 is expected if the product compares itself to reader apps without deciding which gaps are intentionally deferred.

## Roadmap Boundary

The Score 9 program should sequence work in this order:

1. Hard release verdict and quality blockers.
2. Bounded async runtime and structured errors.
3. Balanced corpus proof and output assertions.
4. Operator UX and quality cockpit completeness.
5. Library/history and searchable quality archive.
6. V2 handoff and knowledge workflows as described in [docs/v2-reader-workflow-roadmap.md](v2-reader-workflow-roadmap.md).

This keeps the converter trustworthy before expanding into reader or knowledge-management behavior.
