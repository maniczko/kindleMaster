# Phase -2 Isolation Remediation Plan

Date: `2026-04-11`

## Goal

Ensure Kindle Master can execute and validate without sharing runtime, build, CI, output, cache, or configuration assumptions with foreign frontend/runtime.

## Required Remediation

1. Define a Kindle Master-only runtime entry path that does not depend on Vite or frontend localhost behavior.
2. Define Kindle Master-only output directories and keep them out of foreign frontend/runtime build flows.
3. Split or clearly isolate CI responsibilities so Kindle Master checks do not reuse foreign frontend/runtime runtime assumptions.
4. Ensure root scripts or packaging assumptions are not required for Kindle Master execution.
5. Record the isolation boundary in governance and release criteria.

## Current Status

The original physical-mixing blocker has been remediated on the Kindle Master side:

- `foreign frontend/runtime` was extracted into `an external repository path outside this workspace`
- the current repository root now carries Kindle Master only
- `FX-004` is resolved

The remaining isolation work is no longer physical extraction. It is ongoing enforcement:

- keep post-split isolation tests green
- document reverse-side scope limits explicitly
- block release if new direct coupling appears
