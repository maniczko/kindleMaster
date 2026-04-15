# GitHub Setup

Minimum repository expectations:
- issue templates and PR template exist locally
- backlog tasks map to GitHub issues
- `FX-*` remediation tasks stay traceable as dedicated issues when used remotely
- branch protection and required checks remain external GitHub-admin configuration
- Kindle Master CI must stay Python- and release-gate-oriented rather than reusing foreign frontend checks
- release-blocking checks must be derived from the Kindle Master test matrix, not from historical shared-root assumptions

Local source of truth still lives in:
- `project_control/backlog.yaml`
- `project_control/issue_register.yaml`
- `project_control/status_board.md`

Current repository boundary:
- this repository is the Kindle Master root
- extracted `foreign frontend/runtime` repository path: `an external repository path outside this workspace`
- no shared GitHub workflow, cache, or build requirement with `foreign frontend/runtime` may remain in this repo's release path
