# Issue 326 — canonical bidirectional exercise-solution navigation

## Decision rule

Exercise-to-solution navigation is generated from one canonical `ChessExercise` record only when all of the following are true:

1. an explicit exercise identity exists;
2. the printed exercise number is available for visible link text;
3. the normalized game title is available;
4. a canonical diagram identity exists;
5. a canonical solution record exists;
6. solution reconciliation is accepted as `exact` or `normalized` and is not production-blocked;
7. solution integrity is not strict-blocked;
8. the exercise and solution targets are unique.

A record failing any rule receives `status=blocked`. Its serialized `forward_href` and `backlink_href` are empty, so the renderer cannot accidentally publish a legacy fallback link for a known-invalid canonical record.

## Generated evidence

Each accepted record contains:

- exercise ID and printed number;
- normalized title;
- diagram and solution identities;
- exercise and solution document paths;
- exercise and solution anchors;
- forward href and backlink href;
- visible link text containing the printed exercise number;
- findings and acceptance status.

The aggregate report includes accepted/blocked counts, forward/backlink counts, orphan counts, duplicate target counts, finding counts and production-blocked status.

## Link construction

### Same document

```html
<article id="exercise-ex-1-7">
  <a href="#solution-ex-1-7">Open solution for Exercise 1-7</a>
</article>
<section id="solution-ex-1-7">
  <a href="#exercise-ex-1-7">Back to Exercise 1-7</a>
</section>
```

### Cross-file

```html
<!-- exercises/chapter-01.xhtml -->
<a href="../solutions/chapter-01.xhtml#solution-ex-1-7">Open solution for Exercise 1-7</a>

<!-- solutions/chapter-01.xhtml -->
<a href="../exercises/chapter-01.xhtml#exercise-ex-1-7">Back to Exercise 1-7</a>
```

Relative paths are resolved from the source document. Absolute URLs and links without fragments are outside the internal-link validator.

## Validator

`validate_internal_links()` parses all supplied HTML/XHTML documents and reports:

- document count;
- unique anchor count;
- internal href count;
- duplicate anchors;
- orphan href/fragment pairs;
- aggregate validity.

The validator resolves cross-file relative paths before checking the target anchor.

## Compatibility policy

Legacy semantic-book fixtures without the new navigation contract retain their historical same-document behavior so existing Reader data remains readable. Once a canonical `navigation_status` exists, it is authoritative:

- `accepted` publishes only the canonical anchor/href pair;
- `blocked` publishes no semantic forward link or backlink;
- blocked canonical records never fall back to independently generated IDs.

## Validation evidence

The focused integration suite runs 71 tests covering:

- navigation model and internal-link validator;
- 1,128-pair synthetic contract fixture;
- model serialization and Reader propagation;
- accepted and blocked rendering;
- canonical reconciliation;
- solution integrity;
- existing Chess Reader exercise, solution and navigation contracts;
- semantic data contracts and the full chess-study pipeline.

The 1,128-pair fixture proves the architecture at target cardinality:

- 1,128 unique canonical navigation records;
- 1,128 forward links;
- 1,128 backlinks;
- 2,256 unique anchors;
- 2,256 validated internal hrefs;
- zero orphan hrefs;
- zero duplicate targets.

This is a legal synthetic contract fixture. Final source-bound validation must still run against the protected fixed-edition Woodpecker manifest before claiming that all real book pairs and document paths are correct.

## Merge order

This work is stacked because it consumes the model fields introduced by the preceding P0 changes:

1. PR #343 — canonical solution reconciliation;
2. PR #344 — solution integrity;
3. PR #345 — canonical bidirectional navigation.

After each lower PR is merged, the next PR must be retargeted to `main`, its reduced diff reviewed, and required checks rerun before merge.
