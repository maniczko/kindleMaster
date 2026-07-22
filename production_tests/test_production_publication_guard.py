from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from production_publication_guard import (
    QualityArtifactPublicationError,
    install_production_publication_guard,
)


def _atomic_only_module(upload_dir: str):
    return types.SimpleNamespace(
        UPLOAD_DIR=upload_dir,
        _get_conversion_job=lambda _job_id: None,
        _set_conversion_job=lambda *_args, **_kwargs: None,
        _store_quality_report_artifacts=lambda *_args, **_kwargs: None,
    )


class ProductionPublicationGuardTests(unittest.TestCase):
    def test_epub_is_replaced_only_after_successful_context_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "job.epub"
            target.write_bytes(b"old-complete-artifact")
            module = _atomic_only_module(temp_dir)
            install_production_publication_guard(module)

            with module.open(target, "wb") as handle:
                handle.write(b"new-complete-artifact")
                self.assertEqual(target.read_bytes(), b"old-complete-artifact")

            self.assertEqual(target.read_bytes(), b"new-complete-artifact")
            self.assertEqual(list(Path(temp_dir).glob(".job.epub.*.tmp")), [])

    def test_failed_epub_write_keeps_previous_artifact_and_removes_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "job.epub"
            target.write_bytes(b"old-complete-artifact")
            module = _atomic_only_module(temp_dir)
            install_production_publication_guard(module)

            with self.assertRaisesRegex(RuntimeError, "simulated packaging failure"):
                with module.open(target, "wb") as handle:
                    handle.write(b"partial-new-artifact")
                    raise RuntimeError("simulated packaging failure")

            self.assertEqual(target.read_bytes(), b"old-complete-artifact")
            self.assertEqual(list(Path(temp_dir).glob(".job.epub.*.tmp")), [])

    def test_non_epub_write_delegates_to_regular_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "quality.json"
            module = _atomic_only_module(temp_dir)
            install_production_publication_guard(module)

            with module.open(target, "wb") as handle:
                handle.write(b"report")
                handle.flush()
                self.assertEqual(target.read_bytes(), b"report")

    def test_ready_is_published_only_after_all_quality_artifacts_exist(self) -> None:
        state: dict[str, dict] = {}
        events: list[tuple[str, str]] = []
        module = None

        def getter(job_id: str):
            job = state.get(job_id)
            return dict(job) if job else None

        def setter(job_id: str, **fields):
            state.setdefault(job_id, {}).update(fields)
            if "status" in fields:
                events.append(("set", str(fields.get("status") or "")))
            return dict(state[job_id])

        def quality_writer(job_id: str):
            visible_to_writer = module._get_conversion_job(job_id)
            self.assertEqual(visible_to_writer["status"], "ready")
            self.assertEqual(state[job_id]["status"], "running")
            self.assertEqual(state[job_id]["progress"]["stage_id"], "finalizing_quality")
            artifacts = dict(visible_to_writer.get("artifacts") or {})
            artifacts.update(
                {
                    "report_json": {"status": "stored"},
                    "report_markdown": {"status": "stored"},
                    "log": {"status": "stored"},
                }
            )
            module._set_conversion_job(job_id, artifacts=artifacts)
            events.append(("quality", "stored"))
            return {"stored": True}

        module = types.SimpleNamespace(
            UPLOAD_DIR=".",
            _get_conversion_job=getter,
            _set_conversion_job=setter,
            _store_quality_report_artifacts=quality_writer,
        )
        install_production_publication_guard(module)

        module._set_conversion_job(
            "job-a",
            status="ready",
            message="EPUB gotowy do pobrania.",
            output_path="job-a.epub",
            artifacts={"output": {"status": "stored"}},
            progress={"stage_id": "ready", "percent_estimate": 100},
        )

        self.assertEqual(state["job-a"]["status"], "running")
        self.assertIn("job-a", module._PRODUCTION_PENDING_READY)
        module._store_quality_report_artifacts("job-a")

        self.assertEqual(state["job-a"]["status"], "ready")
        self.assertEqual(state["job-a"]["output_path"], "job-a.epub")
        self.assertEqual(state["job-a"]["progress"]["stage_id"], "ready")
        self.assertTrue(
            {"report_json", "report_markdown", "log"}.issubset(state["job-a"]["artifacts"])
        )
        self.assertNotIn("job-a", module._PRODUCTION_PENDING_READY)
        self.assertEqual(events, [("set", "running"), ("quality", "stored"), ("set", "ready")])

    def test_silently_failed_quality_writer_is_promoted_to_terminal_failure(self) -> None:
        state: dict[str, dict] = {}
        module = None

        def getter(job_id: str):
            return dict(state.get(job_id) or {})

        def setter(job_id: str, **fields):
            state.setdefault(job_id, {}).update(fields)
            return dict(state[job_id])

        def quality_writer(job_id: str):
            visible_to_writer = module._get_conversion_job(job_id)
            artifacts = dict(visible_to_writer.get("artifacts") or {})
            artifacts["report_error"] = {
                "status": "failed",
                "error": "storage unavailable",
            }
            module._set_conversion_job(job_id, artifacts=artifacts)
            return None

        module = types.SimpleNamespace(
            UPLOAD_DIR=".",
            _get_conversion_job=getter,
            _set_conversion_job=setter,
            _store_quality_report_artifacts=quality_writer,
        )
        install_production_publication_guard(module)
        module._set_conversion_job(
            "job-a",
            status="ready",
            artifacts={"output": {"status": "stored"}},
            progress={"stage_id": "ready"},
        )

        with self.assertRaisesRegex(QualityArtifactPublicationError, "not durable") as context:
            module._store_quality_report_artifacts("job-a")

        self.assertEqual(context.exception.error_code, "quality_artifact_storage_failed")
        self.assertEqual(state["job-a"]["status"], "running")
        self.assertIn("job-a", module._PRODUCTION_PENDING_READY)
        module._set_conversion_job(
            "job-a",
            status="failed",
            error_code=context.exception.error_code,
        )
        self.assertNotIn("job-a", module._PRODUCTION_PENDING_READY)
        self.assertEqual(state["job-a"]["status"], "failed")

    def test_quality_exception_never_publishes_ready(self) -> None:
        state: dict[str, dict] = {}
        statuses: list[str] = []

        def getter(job_id: str):
            return dict(state.get(job_id) or {})

        def setter(job_id: str, **fields):
            state.setdefault(job_id, {}).update(fields)
            if "status" in fields:
                statuses.append(str(fields.get("status") or ""))
            return dict(state[job_id])

        def quality_writer(_job_id: str):
            raise OSError("quality storage unavailable")

        module = types.SimpleNamespace(
            UPLOAD_DIR=".",
            _get_conversion_job=getter,
            _set_conversion_job=setter,
            _store_quality_report_artifacts=quality_writer,
        )
        install_production_publication_guard(module)
        module._set_conversion_job("job-a", status="ready", progress={"stage_id": "ready"})

        with self.assertRaisesRegex(OSError, "quality storage unavailable"):
            module._store_quality_report_artifacts("job-a")

        self.assertEqual(state["job-a"]["status"], "running")
        self.assertEqual(statuses, ["running"])
        module._set_conversion_job(
            "job-a",
            status="failed",
            message="Nie udało się utrwalić raportów jakości.",
            error_code="quality_artifact_storage_failed",
        )
        self.assertEqual(state["job-a"]["status"], "failed")
        self.assertNotIn("job-a", module._PRODUCTION_PENDING_READY)
        self.assertNotIn("ready", statuses)


if __name__ == "__main__":
    unittest.main()
