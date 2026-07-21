from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


READY_LABEL = "agent:ready"
CLAIMED_LABEL = "agent:claimed"
NEEDS_REVIEW_LABEL = "agent:needs-review"
TRAINING_DATA_MISSING_LABEL = "training-data:missing"
AUTOPILOT_ALLOWED_LABEL = "autopilot:allowed"
AUTOPILOT_REQUIRES_HUMAN_LABEL = "autopilot:requires-human"

BLOCKING_LABELS = {
    "agent:blocked",
    NEEDS_REVIEW_LABEL,
    AUTOPILOT_REQUIRES_HUMAN_LABEL,
    "needs-product-decision",
    TRAINING_DATA_MISSING_LABEL,
}

AREA_LABELS = {
    "area:app",
    "area:converter",
    "area:semantic",
    "area:text",
    "area:ui",
    "area:delivery",
    "area:auth",
    "area:corpus",
    "area:governance",
}

GATE_COMMANDS = {
    "gate:quick": "python kindlemaster.py test --suite quick",
    "gate:quality-critical": "python kindlemaster.py test --suite quality-critical",
    "gate:browser": "python kindlemaster.py test --suite browser",
    "gate:runtime": "python kindlemaster.py test --suite runtime",
    "gate:release": "python kindlemaster.py test --suite release",
    "gate:corpus": "python kindlemaster.py test --suite corpus",
}

AREA_DEFAULT_GATES = {
    "area:app": ("gate:quick", "gate:runtime"),
    "area:converter": ("gate:quality-critical",),
    "area:semantic": ("gate:quality-critical",),
    "area:text": ("gate:quality-critical",),
    "area:ui": ("gate:quick", "gate:browser"),
    "area:delivery": ("gate:quick", "gate:runtime"),
    "area:auth": ("gate:quick", "gate:runtime"),
    "area:corpus": ("gate:quality-critical", "gate:corpus"),
    "area:governance": ("gate:quick",),
}

REQUIRED_SECTION_ALIASES = {
    "goal": ("Cel", "Goal", "Cel / Goal", "Goal / Cel"),
    "context": ("Kontekst", "Context", "Kontekst / Context", "Context / Kontekst"),
    "scope": ("Zakres", "Scope", "Zakres / Scope", "Scope / Zakres"),
    "acceptance_criteria": (
        "Kryteria akceptacji",
        "Acceptance Criteria",
        "Kryteria akceptacji / Acceptance Criteria",
        "Acceptance Criteria / Kryteria akceptacji",
    ),
    "validation": ("Walidacja", "Validation", "Walidacja / Validation", "Validation / Walidacja"),
    "final_report": (
        "Raport koncowy",
        "Raport końcowy",
        "Final report",
        "Final Report",
        "Output",
        "Raport koncowy / Final Report",
        "Raport końcowy / Final Report",
        "Final Report / Raport koncowy",
        "Final Report / Raport końcowy",
    ),
}

WORKFLOW_BASELINE_AREAS = {
    "area:converter",
    "area:semantic",
    "area:text",
    "area:delivery",
    "area:corpus",
}

_CLOSING_ISSUE_LINE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(?P<number>\d+)\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IssueContract:
    number: int | None
    title: str
    state: str
    body: str
    labels: tuple[str, ...]
    url: str


def linked_issue_number_from_pr_body(body: str) -> int | None:
    """Return the first explicit closing reference outside fenced code blocks."""
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        marker = None
        if stripped.startswith("```"):
            marker = "```"
        elif stripped.startswith("~~~"):
            marker = "~~~"
        if marker:
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        match = _CLOSING_ISSUE_LINE.fullmatch(line)
        if match:
            return int(match.group("number"))
    return None


def load_issue_contracts(path: str | Path) -> list[IssueContract]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("issues"), list):
        payload = payload["issues"]
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    elif isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("GitHub issue payload must be a list, an issue object, or an object with issues/items.")
    return [issue_contract_from_payload(item) for item in payload if isinstance(item, dict)]


def issue_contract_from_payload(payload: dict[str, Any]) -> IssueContract:
    return IssueContract(
        number=_optional_int(payload.get("number")),
        title=str(payload.get("title") or "").strip(),
        state=str(payload.get("state") or "open").strip().lower(),
        body=str(payload.get("body") or ""),
        labels=tuple(sorted(_label_names(payload.get("labels")))),
        url=str(payload.get("html_url") or payload.get("url") or "").strip(),
    )


def evaluate_issue_contract(issue: IssueContract) -> dict[str, Any]:
    missing_sections = _missing_sections(issue.body)
    missing_labels = _missing_required_labels(issue.labels)
    blocking_labels = sorted(set(issue.labels) & BLOCKING_LABELS)
    area_labels = sorted(set(issue.labels) & AREA_LABELS)
    gate_labels = _gate_labels(issue.labels, area_labels)
    commands = [GATE_COMMANDS[label] for label in gate_labels if label in GATE_COMMANDS]
    branch = branch_name_for_issue(issue.number, issue.title)
    status = "ready"
    blockers: list[str] = []

    if issue.state != "open":
        blockers.append("issue_is_not_open")
    if missing_labels:
        blockers.extend(f"missing_label:{label}" for label in missing_labels)
    if not area_labels:
        blockers.append("missing_area_label")
    if missing_sections:
        blockers.extend(f"missing_section:{section}" for section in missing_sections)
    if blocking_labels:
        blockers.extend(f"blocking_label:{label}" for label in blocking_labels)
    if blockers:
        status = "blocked"

    workflow_baseline_required = any(label in WORKFLOW_BASELINE_AREAS for label in area_labels)
    return {
        "issue_number": issue.number,
        "title": issue.title,
        "state": issue.state,
        "url": issue.url,
        "status": status,
        "labels": list(issue.labels),
        "missing_sections": missing_sections,
        "missing_labels": missing_labels,
        "blocking_labels": blocking_labels,
        "area_labels": area_labels,
        "gate_labels": gate_labels,
        "recommended_commands": commands,
        "branch": branch,
        "workflow_baseline_required": workflow_baseline_required,
        "training_data_gap_protocol": _training_data_gap_protocol(area_labels, gate_labels, issue.body + "\n" + issue.title),
        "actions": _actions_for_status(status, branch, commands, workflow_baseline_required),
        "blockers": blockers,
    }


def sync_issues(issues: Sequence[IssueContract]) -> dict[str, Any]:
    contracts = [evaluate_issue_contract(issue) for issue in issues]
    ready = [item for item in contracts if item["status"] == "ready"]
    blocked = [item for item in contracts if item["status"] != "ready"]
    return {
        "status": "passed" if ready else "passed_with_warnings",
        "source": "github_issues",
        "summary": {
            "total": len(contracts),
            "ready": len(ready),
            "blocked": len(blocked),
        },
        "issues": contracts,
    }


def claim_issue(issue: IssueContract, *, apply_branch: bool = False, repo_root: str | Path = ".") -> dict[str, Any]:
    payload = evaluate_issue_contract(issue)
    payload["operation"] = "claim"
    payload["applied"] = False
    if payload["status"] != "ready":
        payload["status"] = "blocked"
        return payload
    if apply_branch:
        branch_result = _create_branch(payload["branch"], repo_root=repo_root)
        payload["applied"] = branch_result["status"] == "created"
        payload["branch_result"] = branch_result
        if branch_result["status"] != "created":
            payload["status"] = "failed"
    return payload


def execute_issue(issue: IssueContract) -> dict[str, Any]:
    payload = evaluate_issue_contract(issue)
    payload["operation"] = "execute"
    if payload["status"] != "ready":
        return payload
    payload["execution_mode"] = "local_agent_handoff"
    payload["notes"] = [
        "Use this payload as the execution contract for a Codex session.",
        "The GitHub queue comments @codex, claims one issue, and after merge prepares the next ready issue.",
        "If training, benchmark, corpus, fixture, or holdout data is missing, post TRAINING_DATA_GAP and add training-data:missing instead of blocking unrelated work.",
    ]
    return payload


def build_issue_report(issue: IssueContract, *, evidence: Iterable[str] = ()) -> dict[str, Any]:
    payload = evaluate_issue_contract(issue)
    evidence_list = [item for item in evidence if item]
    markdown_lines = [
        f"## Autopilot report for #{issue.number or 'unknown'}",
        "",
        f"- Status: `{payload['status']}`",
        f"- Branch: `{payload['branch']}`",
        f"- Areas: `{', '.join(payload['area_labels']) or 'missing'}`",
        f"- Gates: `{', '.join(payload['gate_labels']) or 'missing'}`",
    ]
    if payload["training_data_gap_protocol"]["applies"]:
        markdown_lines.append("- Training-data gap protocol: `applies_if_data_is_missing`")
    if evidence_list:
        markdown_lines.append(f"- Evidence: `{', '.join(evidence_list)}`")
    if payload["blockers"]:
        markdown_lines.append(f"- Blockers: `{', '.join(payload['blockers'])}`")
    return {
        "status": payload["status"],
        "issue_number": issue.number,
        "branch": payload["branch"],
        "markdown": "\n".join(markdown_lines) + "\n",
    }


def doctor_orchestration(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root)
    required_files = {
        "issue_template": root / ".github" / "ISSUE_TEMPLATE" / "kindlemaster_task.yml",
        "global_issue_template": root / ".github" / "ISSUE_TEMPLATE" / "agent_task.yml",
        "pr_template": root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        "orchestration_config": root / ".codex" / "orchestration.json",
        "autopilot_doc": root / "docs" / "github-autopilot-orchestration.md",
        "source_of_truth": root / "docs" / "source-of-truth-matrix.md",
    }
    missing_files = [name for name, path in required_files.items() if not path.exists()]
    labels = sorted(
        {
            READY_LABEL,
            CLAIMED_LABEL,
            AUTOPILOT_ALLOWED_LABEL,
            AUTOPILOT_REQUIRES_HUMAN_LABEL,
            *BLOCKING_LABELS,
            *AREA_LABELS,
            *GATE_COMMANDS,
        }
    )
    return {
        "status": "failed" if missing_files else "passed",
        "provider": "github_issues",
        "mode": "local_autopilot_contract",
        "missing_files": missing_files,
        "required_labels": labels,
        "required_issue_sections": sorted(REQUIRED_SECTION_ALIASES),
        "notes": [
            "GitHub Issues are task truth; Markdown is policy/template/runbook only.",
            "Autopilot acts only on issues with agent:ready and autopilot:allowed.",
            "training-data:missing is issue-local and must not block unrelated ready issues.",
        ],
    }


def run_orchestration_command(args: Any) -> dict[str, Any]:
    command = args.orchestrate_command
    if command == "doctor":
        return doctor_orchestration(repo_root=args.repo_root)

    issues = load_issue_contracts(args.issues_json)
    if command == "sync":
        payload = sync_issues(issues)
        _write_json_if_requested(payload, args.output_json)
        return payload

    issue = _select_issue(issues, args.issue_number)
    if command == "claim":
        payload = claim_issue(issue, apply_branch=bool(args.apply_branch), repo_root=args.repo_root)
        _write_json_if_requested(payload, args.output_json)
        return payload
    if command == "execute":
        payload = execute_issue(issue)
        _write_json_if_requested(payload, args.output_json)
        return payload
    if command == "report":
        payload = build_issue_report(issue, evidence=args.evidence)
        _write_json_if_requested(payload, args.output_json)
        if args.output_md:
            Path(args.output_md).write_text(payload["markdown"], encoding="utf-8")
        return payload
    return {"status": "failed", "error": "unknown_orchestration_command", "command": command}


def branch_name_for_issue(number: int | None, title: str) -> str:
    issue_number = str(number) if number is not None else "unknown"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:48].strip("-") or "task"
    return f"codex/issue-{issue_number}-{slug}"


def _label_names(labels: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, str):
            names.add(label.strip())
        elif isinstance(label, dict):
            names.add(str(label.get("name") or "").strip())
    return {name for name in names if name}


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _missing_sections(body: str) -> list[str]:
    return [
        section
        for section, aliases in REQUIRED_SECTION_ALIASES.items()
        if not any(_has_section(body, alias) for alias in aliases)
    ]


def _has_section(body: str, alias: str) -> bool:
    escaped = re.escape(alias)
    patterns = (
        rf"(?im)^\s*#{{1,4}}\s*{escaped}\s*$",
        rf"(?im)^\s*\*\*{escaped}\*\*\s*:?$",
        rf"(?im)^\s*{escaped}\s*:\s*$",
    )
    return any(re.search(pattern, body) for pattern in patterns)


def _missing_required_labels(labels: Sequence[str]) -> list[str]:
    present = set(labels)
    required = [READY_LABEL, AUTOPILOT_ALLOWED_LABEL]
    return [label for label in required if label not in present]


def _gate_labels(labels: Sequence[str], area_labels: Sequence[str]) -> list[str]:
    explicit_gates = sorted(label for label in labels if label in GATE_COMMANDS)
    if explicit_gates:
        return explicit_gates
    inferred: set[str] = set()
    for area_label in area_labels:
        inferred.update(AREA_DEFAULT_GATES.get(area_label, ()))
    return sorted(inferred)


def _training_data_gap_protocol(area_labels: Sequence[str], gate_labels: Sequence[str], text: str) -> dict[str, Any]:
    lower_text = text.lower()
    applies = bool(
        "area:corpus" in area_labels
        or "gate:corpus" in gate_labels
        or re.search(r"\b(training data|dane treningowe|dataset|benchmark|fixture|corpus|holdout)\b", lower_text)
    )
    return {
        "applies": applies,
        "missing_label": TRAINING_DATA_MISSING_LABEL,
        "comment_prefix": "TRAINING_DATA_GAP",
        "required_fields": [
            "missing data or path",
            "required counts or classes",
            "current available counts or classes",
            "whether useful report-only work can continue",
            "smallest next data-acquisition action",
        ],
    }


def _actions_for_status(status: str, branch: str, commands: Sequence[str], workflow_baseline_required: bool) -> list[str]:
    if status != "ready":
        return ["comment_missing_contract", "add_agent_blocked_label"]
    actions = ["add_agent_claimed_label", f"create_branch:{branch}"]
    if workflow_baseline_required:
        actions.append("run_workflow_baseline_if_fixture_is_defined")
        actions.append("post_training_data_gap_if_training_or_corpus_data_is_missing")
    actions.extend(f"run:{command}" for command in commands)
    actions.extend(["open_pull_request", "attach_evidence_and_residual_risks"])
    return actions


def _select_issue(issues: Sequence[IssueContract], issue_number: int | None) -> IssueContract:
    if issue_number is None:
        if len(issues) != 1:
            raise ValueError("--issue-number is required when the payload contains multiple issues.")
        return issues[0]
    for issue in issues:
        if issue.number == issue_number:
            return issue
    raise ValueError(f"Issue #{issue_number} was not found in the provided payload.")


def _create_branch(branch: str, *, repo_root: str | Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "switch", "-c", branch],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "status": "created" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _write_json_if_requested(payload: dict[str, Any], output_json: str) -> None:
    if not output_json:
        return
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
