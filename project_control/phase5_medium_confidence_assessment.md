# Phase 5 Medium-Confidence Assessment

## Scope

Task: `T5-002`

Goal:
- apply AI-assisted medium-confidence cleanup without paraphrase or meaning change

## Findings

The remaining candidate set is not a clean generic automation target yet.

Main buckets:
- uppercase glued labels such as `ZOBACZWSIECI` and `ORGANIZATION-RELATED`
- notation-heavy chess fragments such as `O- Om`
- furniture-like editorial or publisher blocks that may actually be valid masthead or credits content

## Decision

`T5-002` cannot be closed safely on the current evidence set.

Why:
- the remaining cases are not dominated by one reusable generic rule
- silent cleanup would risk editorial regression
- the ambiguous furniture-like blocks belong partly to semantic reconstruction, not only text cleanup

## Required Next Step

Keep `T5-002` in `REVIEW` until one of these happens:
- a narrower approved medium-confidence cleanup policy is defined
- specific medium-confidence cases are adjudicated and become safe to automate
- later semantic segmentation reduces ambiguity enough to revisit them safely
