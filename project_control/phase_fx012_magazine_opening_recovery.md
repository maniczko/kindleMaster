# FX-012 Magazine Opening Recovery

Date: `2026-04-12`
Owner: `Structure & Semantics`
Task: `FX-012`

## Goal

Recover true article openings from page-split magazine XHTML without reintroducing noisy TOC entries, false `h1` headings, or front-matter pollution.

## Changes Applied

- Added compact signal matching for TOC, promo-banner, and front-matter markers.
- Strengthened chapter-profile classification:
  - `w kioskach` and `nr indeksu` now force `front_matter`
  - marketing-heavy pages with `www.` plus ad-like markers now force `promo`
- Blocked TOC teaser lines from becoming headings or nav entries.
- Added merged-opening recovery:
  - detects `title + subtitle + lead` packed into one paragraph
  - emits `h1` plus separate lead paragraph when article-opening evidence is strong
- Relaxed local article-opening detection just enough to accept interview-style byline/support patterns.
- Blocked false article titles such as:
  - `NEWSWEEK:` interview questions
  - long quote-like pull lines ending in quotes

## Evidence

Artifact:
- `kindlemaster_runtime/output/final_epub/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.epub`

Before this pass:
- weighted score: `6.93/10`
- noisy duplicate TOC pressure present
- front matter distinctness: `false`
- many true article starts under-selected

After this pass:
- weighted score: `10.0/10`
- `h1_count`: `16`
- `toc_entry_count`: `14`
- `duplicate_low_value_entries`: `0`
- `split_word_count`: `0`
- `joined_word_boundary_count`: `2`
- `front_matter.distinctness_pass`: `true`
- failed smoke checks limited to:
  - `creator_not_unknown_for_release`

Recovered article openings now include:
- `Nie tęskni za Francją Krówki z walizki babci`
- `Na poprawę nastroju Wszyscy tęsknimy za domowymi smakami`
- `Prostota i jakość Dam się pokroić za kremówkę`
- `Dumny Polak Kucharz nigdy nie jest głodny`
- `Grecki temperament Ouzo z widokiem na morze!`
- `Hotele na majówkę Więcej niż łóżko i śniadanie`

Removed false headline/navigation promotion includes:
- `NEWSWEEK: ...` interview questions
- quote-style pull lines masquerading as article titles
- early cover and ad teaser pages

## Gate Result

`GR2`: `PASS`

Why:
- continued pages no longer masquerade as fresh article openings on the tracked magazine guard sample
- true article starts split across page-based XHTML are recovered more reliably
- TOC quality improved without reintroducing dead links, duplicates, or front-matter pollution

## Remaining Limits

- This resolves page-split continuation recovery for the tracked magazine-like guard sample.
- It does not resolve release metadata for this publication; creator remains untrusted and therefore non-release.
- It does not close the broader finalizer decomposition work tracked in `FX-011`.
