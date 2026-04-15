# Phase R0-005: Semantic Structure Audit

Date: 2026-04-12
Owner: Structure & Semantics
Gate: `GR0`

## Current Final EPUB Findings

- `h1_count`: `0`
- `h2_count`: `149`
- `h3_count`: `186`
- files with no `h1`: `85`

## Detected Structural Problems

- missing real `h1` article titles
- heavy overuse of `h2`
- many author names promoted to headings
- staff and role labels promoted to headings
- page labels promoted to headings
- one merged heading pattern remains
- `title.xhtml` was emptied by generic processing
- special sections masquerade as article structure

## Concrete Examples

From `page-0001.xhtml` in final EPUB:

- `Page 1` became `h2`
- `Julia Janiszewska` became `h2`
- `Bartosz Misiurek` became `h2`
- `S. 8` became `h2`
- `Management` became `h2`

From `page-0002.xhtml` in final EPUB:

- `ZESPÓŁ ZARZĄDZAJĄCY` became `h2`
- multiple staff names became `h2`
- role labels such as `Redaktor Naczelna` became `h2`

## Why This Harms Kindle Reading

- headings no longer reflect actual article hierarchy
- the TOC is polluted by names, page labels, and organizational residues
- front matter competes with article content
- article openings do not present as clean title -> lead -> author -> body sequences

## Conclusion

Recent semantic changes did not recover article structure. They replaced under-structured page paragraphs with over-promoted heading noise, which is more visible to Kindle readers and more damaging to navigation.
