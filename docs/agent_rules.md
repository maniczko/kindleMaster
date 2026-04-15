# Agent Rules

All agents must:
- work from `project_control/backlog.yaml`
- respect `project_control/input_registry.yaml`
- log every discovered defect in `project_control/issue_register.yaml`
- route uncertainty to `project_control/low_confidence_review_queue.yaml`
- avoid paraphrase and meaning changes
- treat editorial regressions as failures
- treat `KINDLE MASTER` and `foreign frontend/runtime` as isolated projects
- avoid using foreign frontend/runtime runtime or Vite output as Kindle Master evidence

Authority rules:
- Lead / Orchestrator controls execution order and remediation creation but cannot override failed gates
- Quality Guardian / Release Gate Agent owns release-blocking test enforcement and can force `NOT READY`
- QA / Regression Agent owns measurable before/after proof and cannot close quality tasks without evidence
- Release Readiness Agent owns final `READY / NOT READY`

Role map:
- Lead / Orchestrator
- Input & Intake
- Cross-Project Boundary
- PDF Analysis
- Baseline Conversion Strategy
- EPUB Analysis
- Text Cleanup
- Structure & Semantics
- TOC & Navigation
- Image & Layout
- CSS / Kindle UX
- Quality Guardian / Release Gate
- QA / Regression
- Release Readiness
