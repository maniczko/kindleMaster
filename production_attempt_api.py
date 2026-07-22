from __future__ import annotations

from types import ModuleType

from durable_job_queue import DurableJobQueue


def install_attempt_history_api(app_module: ModuleType, queue: DurableJobQueue) -> None:
    """Expose immutable attempt evidence through the existing ownership check."""

    from flask import jsonify

    endpoint = "durable_conversion_attempts"
    if endpoint in app_module.app.view_functions:
        return

    def conversion_attempts(job_id: str):
        auth_context = app_module._resolve_request_auth_context()
        if getattr(auth_context, "error", ""):
            return app_module._json_auth_error(auth_context)
        job = app_module._get_conversion_job_for_auth(job_id, auth_context)
        if not job:
            return app_module._json_error(
                "Nie znaleziono zadania konwersji.",
                error_code="missing_conversion_job",
                status_code=404,
                phase="attempts",
                job_id=job_id,
                retryable=False,
            )
        attempts = queue.attempts(job_id)
        record = queue.get(job_id)
        response = jsonify(
            {
                "success": True,
                "job_id": job_id,
                "attempts": attempts,
                "count": len(attempts),
                "current_attempt": int(record.attempt if record is not None else 0),
                "max_attempts": int(record.max_attempts if record is not None else 0),
            }
        )
        app_module.apply_no_store_headers(response.headers)
        return response

    app_module.app.add_url_rule(
        "/convert/attempts/<job_id>",
        endpoint=endpoint,
        view_func=conversion_attempts,
        methods=["GET"],
    )
