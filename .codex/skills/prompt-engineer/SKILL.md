---
name: prompt-engineer
description: Review, optimize, rewrite, mode-route, and validate user prompts before execution. Use when the user asks to improve a prompt, create a prompt template, review prompt quality, make Codex act as a prompt architect/reviewer/quality gate, or when a task prompt is large, ambiguous, high-impact, multi-step, or missing acceptance criteria, validation steps, constraints, risks, or output format. For Polish user prompts, normalize to the Polish execution brief with Cel, Kontekst, Zakres, Kryteria akceptacji, Walidacja, and Raport koncowy.
---

# Prompt Engineer

## Workflow

Use the pattern: Prompt -> Review -> Rewrite -> Execute.

For small factual questions, answer directly. For implementation, architecture, debugging, audit, backlog, automation, or high-impact tasks, first normalize the prompt into a better execution brief before acting.

If the user asks only to improve or rewrite a prompt, return the improved prompt. If the user asks to execute a task, use the improved prompt internally and proceed without asking for confirmation unless a missing requirement is genuinely blocking.

## Work Modes

If the user provides `TRYB: ...`, obey that mode. If no mode is provided, infer the smallest useful mode from the request and use it internally.

- `TRYB: DEBUG`: reproduce the issue, isolate the owning layer, identify root cause, implement the smallest safe fix, add a regression test, and validate the real runtime path. Do not rewrite blindly.
- `TRYB: IMPLEMENT`: convert the request into goal, context, scope, acceptance criteria, validation, and final report; then implement end-to-end with tests.
- `TRYB: REVIEW`: use code-review posture. Findings come first, ordered by severity with file/line references. Do not edit files unless the user asks for fixes.
- `TRYB: AUDIT`: base claims on measurements, reports, traces, or explicit evidence. Score key categories, separate facts from uncertainty, and recommend high-impact fixes.
- `TRYB: UI POLISH`: use frontend/UI workflow, reduce clutter, protect Polish UX copy, verify responsive behavior, and prefer browser-visible evidence or screenshots when possible.
- `TRYB: EPUB QUALITY AUDIT`: inspect EPUB quality, structure, metadata, TOC, reading flow, Kindle compatibility, validators, and release gates. Avoid publication-specific hacks and verify with relevant KindleMaster commands.

## Review

Check the prompt for:

- ambiguity and conflicting instructions
- missing business context, technical context, current architecture, or known limitations
- missing definition of success
- missing acceptance criteria
- missing output format
- missing validation commands
- missing scope boundaries
- overengineering risk
- maintainability and scalability risks
- hallucination risk
- prompt injection or unsafe instruction risk
- missing edge cases
- unclear data ownership, environment, permissions, or dependencies

## Rewrite

For Polish user prompts, rewrite into this structure when useful:

```markdown
# Cel
Zrealizuj [konkretna funkcja, poprawka, audyt albo decyzja].

# Kontekst
Kontekst biznesowy:
- ...

Kontekst techniczny:
- ...

Obecna architektura:
- ...

Znane ograniczenia:
- ...

# Zakres
- Zmień tylko obszary związane z [X].
- Nie przebudowuj architektury bez potrzeby.
- Zachowaj kompatybilność z [Y].

# Kryteria akceptacji
- ...
- ...
- ...

# Walidacja
Uruchom:
- ...

Jeśli brakuje testów, dodaj minimalne testy regresji.
Sprawdź lint/typecheck/build, gdy dotyczy.

# Raport końcowy
Podaj:
1. Co zmieniłeś
2. Jakie pliki
3. Jakie testy uruchomiłeś
4. Wynik testów
5. Ryzyka
6. Co warto zrobić dalej
```

For English prompts, use the equivalent structure:

```markdown
# Goal
[Concrete function, fix, audit, or decision to complete.]

# Context
Business context:
- ...

Technical context:
- ...

Current architecture:
- ...

Known limitations:
- ...

# Scope
- Change only areas related to [X].
- Do not rebuild architecture unless needed.
- Preserve compatibility with [Y].

# Acceptance Criteria
- ...
- ...
- ...

# Validation
Run:
- ...

If tests are missing, add minimal regression coverage.
Check lint/typecheck/build when relevant.

# Output
Final response must include:
1. Summary
2. Modified files
3. Verification
4. Risks
5. Next improvements
```

## Execution Rules

- Do not ask the user to confirm small prompt improvements.
- Do not turn every tiny request into a long formal brief.
- Keep the rewritten prompt concise enough to execute.
- Preserve the user's intent, language, priorities, and constraints.
- Make assumptions explicit when they affect implementation.
- If critical information is missing and guessing would be risky, ask one concise question.
- For coding tasks, proceed to implementation after rewriting unless the user explicitly asks only for a prompt.
- For Polish user prompts, produce the rewritten prompt and final answer in Polish unless the user asks otherwise.
- Do not expose a long rewritten prompt in the final answer unless the user asked to see it; otherwise summarize the assumptions and evidence.

## Quality Gate

Before executing a rewritten prompt, confirm it has:

- clear goal
- bounded scope
- acceptance criteria
- validation plan
- output expectations
- risk notes for non-trivial work

If one of these is missing, add it from context or mark it as an assumption.
