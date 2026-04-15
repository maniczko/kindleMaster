# Contributing To Kindle Master

## Working Model

Kindle Master is governed by repository control files rather than ad hoc coding.

Before changing code or docs:
1. confirm the task in `project_control/backlog.yaml`
2. confirm dependencies and owner role
3. confirm the relevant quality gate
4. confirm what evidence must exist before the task can be `DONE`

## Non-Negotiable Rules

- never paraphrase publication content
- never silently change meaning
- never rewrite author style for polish
- use deterministic logic first
- route uncertainty to `project_control/low_confidence_review_queue.yaml`
- log every discovered defect in `project_control/issue_register.yaml`

## Done Criteria

A quality-affecting task is done only when:
- output exists
- issues were updated if needed
- metrics were updated if relevant
- the assigned gate passed
- regressions were checked
- evidence was captured in repository artifacts

## Verification

Primary local verification:
```bash
python -m py_compile kindle_semantic_cleanup.py kindlemaster_pdf_to_epub.py kindlemaster_end_to_end.py kindlemaster_webapp.py kindlemaster_local_server.py
pytest
```
