# FX-019 Generic Guard Derisking

## Result

Executable quality gates no longer depend on publication-specific labels or known sample wording.

## What Changed

- scenario tests replaced concrete label assertions with generic bookmark-quality rules
- image-layout quality is now audited generically across the manifest-backed corpus
- premium scoring now includes an explicit image-layout subscore
- quality-loop comparison now treats page-like or image-only TOC pollution as a hard regression

## Why This Matters

This removes false confidence from the guard layer. Premium claims are now backed by reusable quality rules rather than by remembered sample titles.

## Verdict

`FX-019` is complete.
