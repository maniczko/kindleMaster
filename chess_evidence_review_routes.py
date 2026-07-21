from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from flask import Flask, jsonify, request

from chess_evidence_review_repository import ChessEvidenceReviewRepository
from chess_evidence_review_store import EvidenceReviewStoreError, export_marker_labels
from chess_evidence_review_ui import render_chess_evidence_review_html


def register_chess_evidence_review_routes(
    app: Flask,
    *,
    mark_timed_out_conversion_jobs: Callable[[], Any],
    cleanup_expired_conversion_jobs: Callable[[], Any],
    get_conversion_job: Callable[[str], dict | None],
    ensure_local_artifact_history_loaded: Callable[[], Any],
    restore_local_artifact_job_by_id: Callable[[str], dict | None],
    json_error: Callable[..., Any],
    error_missing_output: str,
    error_upload_failed: str,
) -> None:
    def resolve_job(job_id: str) -> dict | None:
        job = get_conversion_job(job_id)
        if not job:
            ensure_local_artifact_history_loaded()
            job = get_conversion_job(job_id)
        return job or restore_local_artifact_job_by_id(job_id)

    def missing_job(job_id: str):
        return json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=error_missing_output,
            status_code=404,
            phase="evidence_review",
            job_id=job_id,
        )

    @app.route("/convert/artifact/<job_id>/chess_evidence_review", methods=["GET"])
    def convert_chess_evidence_review(job_id: str):
        mark_timed_out_conversion_jobs()
        cleanup_expired_conversion_jobs()
        if not resolve_job(job_id):
            return missing_job(job_id)
        response = app.response_class(render_chess_evidence_review_html(), mimetype="text/html")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-KindleMaster-Artifact-Source"] = "supabase-evidence-review"
        return response

    @app.route(
        "/convert/artifact/<job_id>/chess_evidence_review_progress",
        methods=["GET", "PUT"],
    )
    def convert_chess_evidence_review_progress(job_id: str):
        mark_timed_out_conversion_jobs()
        cleanup_expired_conversion_jobs()
        if not resolve_job(job_id):
            return missing_job(job_id)
        repository = ChessEvidenceReviewRepository()
        try:
            if request.method == "GET":
                payload = repository.load(artifact_id=job_id)
                if payload is None:
                    raise EvidenceReviewStoreError("Nie znaleziono kolejki dowodow markerow.")
                result = _browser_payload(payload)
            else:
                submitted = request.get_json(silent=True)
                if not isinstance(submitted, dict) or not isinstance(submitted.get("row"), dict):
                    raise EvidenceReviewStoreError("Przeslij obiekt JSON z polem row.")
                result = repository.save_item(
                    artifact_id=job_id,
                    submitted=submitted["row"],
                    expected_revision=int(submitted.get("expected_revision", -1)),
                )
        except EvidenceReviewStoreError as exc:
            conflict = "innej sesji" in str(exc).lower()
            return json_error(
                str(exc),
                error_code="evidence_review_revision_conflict" if conflict else error_upload_failed,
                status_code=409 if conflict else 400,
                phase="evidence_review",
                job_id=job_id,
            )
        except RuntimeError as exc:
            conflict = "revision_conflict" in str(exc).lower()
            return json_error(
                str(exc),
                error_code="evidence_review_revision_conflict" if conflict else error_upload_failed,
                status_code=409 if conflict else 503,
                phase="evidence_review",
                job_id=job_id,
            )
        response = jsonify({"success": True, "job_id": job_id, **result})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    @app.route(
        "/convert/artifact/<job_id>/chess_evidence_review_export",
        methods=["GET"],
    )
    def convert_chess_evidence_review_export(job_id: str):
        if not resolve_job(job_id):
            return missing_job(job_id)
        try:
            payload = ChessEvidenceReviewRepository().load(artifact_id=job_id)
            if payload is None:
                raise EvidenceReviewStoreError("Nie znaleziono kolejki dowodow markerow.")
            labels = export_marker_labels(list(payload.get("rows") or []))
        except (EvidenceReviewStoreError, RuntimeError) as exc:
            return json_error(
                str(exc),
                error_code=error_missing_output,
                status_code=503,
                phase="evidence_review",
                job_id=job_id,
            )
        body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in labels)
        response = app.response_class(body, mimetype="application/x-ndjson")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="marker_evidence_{job_id}.jsonl"'
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


def _browser_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    source_sha = str(result.pop("source_document_sha256", "") or "")
    result["source_sha_short"] = f"{source_sha[:12]}..." if source_sha else "brak"
    result["rows"] = [
        {key: value for key, value in dict(row).items() if key != "source_document_sha256"}
        for row in payload.get("rows") or []
        if isinstance(row, Mapping)
    ]
    return result
