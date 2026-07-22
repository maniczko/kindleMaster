from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

from flask import g, has_request_context, jsonify, request

from durable_job_queue import DurableJob, DurableJobStatus
from durable_runtime_store import RuntimeDurableJobQueue


_INSTALL_MARKER = "_kindlemaster_durable_runtime_installed"
_GUEST_HEADER = "X-KindleMaster-Guest-Id"
_GUEST_QUERY = "km_guest"
_GUEST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{19,127}$")


def queue_database_path(app_module: Any | None = None) -> Path:
    configured = str(os.environ.get("KINDLEMASTER_DURABLE_QUEUE_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    upload_dir = Path(getattr(app_module, "UPLOAD_DIR", "/data/uploads"))
    root = upload_dir.parent if upload_dir.name == "uploads" else upload_dir
    return root / "queue" / "conversion_jobs.sqlite3"


def queue_source_root(app_module: Any | None = None) -> Path:
    configured = str(os.environ.get("KINDLEMASTER_DURABLE_SOURCE_DIR", "") or "").strip()
    if configured:
        root = Path(configured)
    else:
        root = queue_database_path(app_module).parent / "sources"
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_queue(app_module: Any | None = None) -> RuntimeDurableJobQueue:
    return RuntimeDurableJobQueue(queue_database_path(app_module))


def resolve_request_owner_id(app_module: Any, job: Mapping[str, Any] | None = None) -> str:
    job = job or {}
    user_id = str(job.get("user_id") or "").strip()
    if user_id:
        return f"user:{user_id}"
    guest_owner = str(job.get("guest_owner_id") or "").strip()
    if guest_owner:
        return guest_owner if guest_owner.startswith("guest:") else f"guest:{guest_owner}"

    if has_request_context():
        try:
            auth_context = app_module._resolve_request_auth_context()
        except Exception:
            auth_context = None
        if auth_context is not None and getattr(auth_context, "authenticated", False):
            resolved_user = str(getattr(auth_context, "user_id", "") or "").strip()
            if resolved_user:
                return f"user:{resolved_user}"

        raw_guest = str(
            request.headers.get(_GUEST_HEADER)
            or request.args.get(_GUEST_QUERY)
            or ""
        ).strip()
        if raw_guest and _GUEST_PATTERN.fullmatch(raw_guest):
            return "guest:" + hashlib.sha256(raw_guest.encode("utf-8")).hexdigest()

        hostname = str(request.host or "").split(":", 1)[0].lower()
        allow_local = str(os.environ.get("KINDLEMASTER_ALLOW_LEGACY_LOCAL_GUEST", "") or "").lower()
        if hostname in {"localhost", "127.0.0.1", "::1", "kindlemaster.localhost"} and allow_local not in {"0", "false", "no", "off"}:
            return "legacy-local"
    return ""


def durable_status_to_application(status: DurableJobStatus) -> str:
    if status in {DurableJobStatus.LEASED, DurableJobStatus.RUNNING}:
        return "running"
    if status == DurableJobStatus.RETRYING:
        return "queued"
    if status == DurableJobStatus.DEAD_LETTER:
        return "failed"
    return status.value


def merge_durable_job(local_job: Mapping[str, Any] | None, durable: DurableJob) -> dict[str, Any]:
    payload_job = durable.payload.get("job_record")
    merged = dict(local_job or {})
    if isinstance(payload_job, Mapping):
        merged.update(dict(payload_job))
    merged["job_id"] = durable.job_id
    merged["status"] = durable_status_to_application(durable.status)
    merged["updated_at"] = durable.updated_at
    runtime = dict(merged.get("runtime") or {})
    runtime.update(
        {
            "provider": "durable-sqlite",
            "status": durable.status.value,
            "attempt": durable.attempt,
            "max_attempts": durable.max_attempts,
            "worker_id": durable.worker_id,
            "lease_expires_at": durable.lease_expires_at,
            "heartbeat_at": durable.heartbeat_at,
        }
    )
    merged["runtime"] = runtime
    if durable.error_code:
        merged["error_code"] = durable.error_code
    if durable.error_message:
        merged["error"] = durable.error_message
        merged.setdefault("message", "Konwersja nie powiodła się.")
    return merged


def _idempotency_key() -> str:
    if not has_request_context():
        return ""
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    return value[:200]


def _duplicate_response(app_module: Any, durable: DurableJob):
    merged = merge_durable_job(None, durable)
    response = jsonify(
        {
            "success": True,
            "job_id": durable.job_id,
            "status": merged["status"],
            "message": str(merged.get("message") or "Zwrócono istniejące zadanie dla tego klucza idempotencji."),
            "poll_after_ms": getattr(app_module, "DEFAULT_CONVERSION_POLL_INTERVAL_MS", 1500),
            "runtime": merged["runtime"],
            "idempotent_replay": True,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _persist_source(app_module: Any, job_id: str, source_path: str, source_type: str) -> str:
    source = Path(source_path)
    target = queue_source_root(app_module) / f"{job_id}.{source_type}"
    if source.resolve() != target.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.replace(target)
        except OSError:
            shutil.copy2(source, target)
            source.unlink(missing_ok=True)
    return str(target)


def install_durable_runtime(app_module: Any | None = None, queue: RuntimeDurableJobQueue | None = None):
    if app_module is None:
        import app as app_module  # type: ignore[no-redef]

    flask_app = app_module.app
    if getattr(flask_app, _INSTALL_MARKER, False):
        return flask_app

    durable_queue = queue or build_queue(app_module)
    original_spawn = app_module._spawn_conversion_job
    original_get = app_module._get_conversion_job
    original_snapshot = app_module._visible_conversion_jobs_snapshot
    original_start_view = flask_app.view_functions.get("convert_start")
    original_delete_view = flask_app.view_functions.get("convert_job_delete")

    def durable_get(job_id: str):
        local = original_get(job_id)
        durable = durable_queue.get(job_id)
        if durable is None:
            return local
        if has_request_context():
            owner = resolve_request_owner_id(app_module, local)
            if owner and durable.owner_id != owner:
                return local
        return merge_durable_job(local, durable)

    def durable_snapshot():
        local_jobs = original_snapshot()
        owner = resolve_request_owner_id(app_module)
        durable_jobs = durable_queue.list_jobs(owner_id=owner or None, limit=1000)
        merged = {job_id: dict(job) for job_id, job in local_jobs.items()}
        for durable in durable_jobs:
            merged[durable.job_id] = merge_durable_job(merged.get(durable.job_id), durable)
        return merged

    def enqueue_conversion_job(**kwargs):
        job_id = str(kwargs["job_id"])
        local_job = original_get(job_id) or {}
        owner = resolve_request_owner_id(app_module, local_job)
        if not owner:
            raise RuntimeError("A durable conversion job requires an authenticated or anonymous owner.")
        durable_source = _persist_source(
            app_module,
            job_id,
            str(kwargs["source_path"]),
            str(kwargs["source_type"]),
        )
        payload = {
            "kind": "conversion",
            "source_path": durable_source,
            "source_type": kwargs["source_type"],
            "original_filename": kwargs["original_filename"],
            "profile": kwargs["profile"],
            "force_ocr": bool(kwargs["force_ocr"]),
            "language": kwargs["language"],
            "heading_repair_enabled": bool(kwargs["heading_repair_enabled"]),
            "route_model_mode": kwargs.get("route_model_mode", "shadow"),
            "quality_gate_mode": kwargs.get("quality_gate_mode", "draft"),
            "cloud_user_id": kwargs.get("cloud_user_id", ""),
            "job_record": {**local_job, "source_path": durable_source},
        }
        durable, created = durable_queue.enqueue(
            owner_id=owner,
            payload=payload,
            idempotency_key=_idempotency_key(),
            max_attempts=max(1, int(os.environ.get("KINDLEMASTER_DURABLE_MAX_ATTEMPTS", "3"))),
            job_id=job_id,
        )
        if not created and durable.job_id != job_id:
            Path(durable_source).unlink(missing_ok=True)
            try:
                app_module._CONVERSION_JOB_STORE.delete(job_id)
            except Exception:
                pass
            if has_request_context():
                g.kindlemaster_idempotent_job = durable
            return
        runtime = dict(local_job.get("runtime") or {})
        runtime.update(
            {
                "provider": "durable-sqlite",
                "status": durable.status.value,
                "attempt": durable.attempt,
                "max_attempts": durable.max_attempts,
            }
        )
        app_module._set_conversion_job(
            job_id,
            source_path=durable_source,
            runtime=runtime,
            status="queued",
            message="Zadanie zapisane w trwałej kolejce.",
        )

    def durable_start_view():
        owner = resolve_request_owner_id(app_module)
        if not owner:
            return app_module._json_error(
                "Publiczna konwersja wymaga zalogowanego użytkownika albo anonimowego identyfikatora sesji.",
                error_code="job_owner_required",
                status_code=401,
                phase="auth",
            )
        key = _idempotency_key()
        if key:
            existing = durable_queue.find_by_idempotency(owner_id=owner, idempotency_key=key)
            if existing is not None:
                return _duplicate_response(app_module, existing)
        response = original_start_view()
        duplicate = getattr(g, "kindlemaster_idempotent_job", None)
        if isinstance(duplicate, DurableJob):
            return _duplicate_response(app_module, duplicate)
        return response

    def durable_delete_view(job_id: str):
        owner = resolve_request_owner_id(app_module, original_get(job_id) or {})
        response = original_delete_view(job_id)
        status_code = getattr(response, "status_code", None)
        if status_code == 200 and owner:
            try:
                durable_queue.delete(job_id, owner_id=owner, terminal_only=True)
            except (KeyError, ValueError):
                pass
        return response

    app_module._spawn_conversion_job = enqueue_conversion_job
    app_module._get_conversion_job = durable_get
    app_module._visible_conversion_jobs_snapshot = durable_snapshot
    if original_start_view is not None:
        flask_app.view_functions["convert_start"] = durable_start_view
    if original_delete_view is not None:
        flask_app.view_functions["convert_job_delete"] = durable_delete_view

    flask_app.extensions["kindlemaster_durable_queue"] = durable_queue
    flask_app.extensions["kindlemaster_original_spawn"] = original_spawn
    setattr(flask_app, _INSTALL_MARKER, True)

    try:
        from admission_runtime_integration import install_admission_controls
    except ImportError:
        install_admission_controls = None
    if install_admission_controls is not None:
        install_admission_controls(app_module=app_module, durable_queue=durable_queue)
    return flask_app
