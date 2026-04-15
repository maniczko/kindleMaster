# Phase 8 Handling Decisions

## Purpose

This file is the explicit handling map for `T8-002`. It turns the Phase 8 classification evidence into reusable routing decisions instead of leaving image-heavy handling as an implicit implementation detail.

## Decision Map

### Report-like corpus members

- generic inline figures remain reflowable when they already participate in linear reading flow
- decorative or isolated figures do not become TOC drivers
- editorial or publisher-supporting graphics stay attached to their surrounding section semantics

### Mixed-layout chess corpus

- chess problem diagrams remain image-based rather than being force-converted into synthetic prose
- chapters with dense diagram clusters are treated as page-like sections for Kindle-risk tracking
- figure and caption groupings must remain preserved as paired structures
- notation-heavy content must not be misclassified as broken prose merely because it resembles corrupted line breaks

## Generic Rules

- prefer reflowable handling for normal article or report content when reading order is already stable
- prefer image-based handling for diagrams, puzzle boards, and page-like structures where forced reflow would damage meaning or usability
- keep mixed-layout risks visible in the issue register instead of pretending they are fully solved by classification alone

## Current Open Risk

- `ISSUE-004` remains open because the current pipeline classifies the risk correctly but has not yet closed the release-level readability concern for the image-heavy chess sections
- `FX-003` remains the tracked follow-on task for that targeted remediation
