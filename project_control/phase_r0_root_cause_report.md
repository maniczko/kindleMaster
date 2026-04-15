# Phase R0-007: Root Cause Report

Date: 2026-04-12
Owner: Lead / Orchestrator
Gate: `GR0`

## Short Answer

Recent changes failed to improve the Kindle reading experience because the final remediation stage is applying large, user-visible rewrites that are mostly destructive or neutral, while the real PDF artifact cleanup remains too weak to deliver clear benefit.

## Root Causes

### 1. The final EPUB is being changed, but the wrong things are changing

The final EPUB is not identical to baseline. All content pages, navigation, title page, and OPF are rewritten.

The problem is that the dominant changes are:

- heading over-promotion
- TOC regeneration noise
- title-page loss
- navigation path breakage
- stylesheet path mismatch

### 2. Navigation is rebuilt from weak heading heuristics

The TOC is not rebuilt from validated article semantics. It is rebuilt from headings promoted from page-level lines, names, page markers, and front-matter residues.

Result:

- more TOC entries
- more noisy entries
- duplicate entries
- broken relative paths

### 3. The semantic stage is destructive for magazine-like page content

The current heuristics treat many short, capitalized, or prominent lines as headings, even when they are:

- author names
- roles
- page labels
- section labels
- front-matter residues

This creates a more structured-looking EPUB that is actually less readable and less navigable.

### 4. Text cleanup does not materially reduce the defects users still notice

On the active PDF sample:

- split words did not improve
- joined words did not improve
- only paragraph-fragmentation indicators improved partially

So the user-visible defects remain while the structural rewrite becomes more aggressive.

### 5. Final CSS intent is not reliably shipped

Final XHTML references `style/default.css`, but the packaged EPUB still only carries `EPUB/styles/baseline.css`.

That means some intended Kindle styling does not have a valid packaged target.

### 6. There is no proof gate between cleanup intent and packaged final result

The current end-to-end runner validates package presence for nav/toc files, but it does not prove:

- semantic quality
- TOC correctness
- stylesheet target validity
- visible artifact reduction

## Which Stages Are Ineffective

- text cleanup for split/join artifact reduction
- metadata normalization for premium-quality output

## Which Stages Are Destructive

- heading promotion in `kindle_semantic_cleanup.py`
- final navigation regeneration in `kindle_semantic_cleanup.py`
- title/front-matter generic rewriting in `kindle_semantic_cleanup.py`

## What Must Be Fixed Before More Tuning

1. explicit artifact lifecycle and non-overwrite rules
2. valid final CSS asset lifecycle
3. valid final TOC path generation
4. constrained heading promotion
5. title/front-matter preservation
6. post-cleanup rescanning and measurable final-output proof

## Answer Required By The Brief

Why did recent changes fail to improve the Kindle reading experience?

Because the final pipeline stage is rewriting the EPUB in a way that amplifies noisy headings and broken navigation while leaving major text-cleanup defects mostly unresolved. The changes are real, but the most visible ones are harmful or neutral instead of improving reading quality.
