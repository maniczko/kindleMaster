from __future__ import annotations

import json
from pathlib import Path

import yaml

from kindlemaster_webapp import app


REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_REPORT_PATH = REPO_ROOT / "project_control" / "phase12_corpus_release_gate_enforcement.json"
QUALITY_LOOP_STATE_PATH = REPO_ROOT / "project_control" / "quality_loop_state.json"
BACKLOG_PATH = REPO_ROOT / "project_control" / "backlog.yaml"


def test_quality_state_exposes_corpus_wide_text_first_summary() -> None:
    with app.test_client() as client:
        response = client.get("/quality-state")

    assert response.status_code == 200
    payload = response.get_json()
    corpus = payload["corpus"]

    assert corpus["found"] is True
    assert corpus["verdict"] == "PASS"
    assert corpus["quality_first"] is True
    assert corpus["publications_total"] >= 4
    assert corpus["text_first_pass_total"] == corpus["publications_total"]
    assert corpus["unjustified_fallback_total"] == 0
    assert payload["repository_truth"]["status"] == "READY"


def test_corpus_gate_report_contains_per_publication_premium_notes() -> None:
    payload = json.loads(CORPUS_REPORT_PATH.read_text(encoding="utf-8"))
    publications = payload["publications"]

    assert payload["verdict"] == "PASS"
    assert publications
    for publication in publications:
        assert isinstance(publication.get("what_is_good"), list)
        assert isinstance(publication.get("what_is_bad"), list)
        assert publication["coverage_pass"] is True
        assert publication["text_first_pass"] is True
        assert publication["unjustified_fallback_count"] == 0


def test_quality_loop_state_matches_active_backlog_or_idle_mode() -> None:
    state = json.loads(QUALITY_LOOP_STATE_PATH.read_text(encoding="utf-8"))
    backlog = yaml.safe_load(BACKLOG_PATH.read_text(encoding="utf-8")) or {}
    active_task_ids = {str(task.get("id")) for task in backlog.get("tasks", []) if task.get("id")}
    selected_task = state.get("selected_issue_or_task")
    state_kind = str(state.get("state_kind") or "")

    if state_kind in {"maintenance_idle", "recovery_idle"}:
        assert not selected_task
    else:
        assert selected_task in active_task_ids
