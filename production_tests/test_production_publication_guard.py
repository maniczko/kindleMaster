from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from production_publication_guard import install_production_publication_guard


class ProductionPublicationGuardTests(unittest.TestCase):
    def test_epub_is_replaced_only_after_successful_context_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "job.epub"
            target.write_bytes(b"old-complete-artifact")
            module = types.SimpleNamespace(
                UPLOAD_DIR=temp_dir,
                _set_conversion_job=lambda *_args, **_kwargs: None,
                _store_quality_report_artifacts=lambda *_args, **_kwargs: None,
            )
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
            module = types.SimpleNamespace(
                UPLOAD_DIR=temp_dir,
                _set_conversion_job=lambda *_args, **_kwargs: None,
                _store_quality_report_artifacts=lambda *_args, **_kwargs: None,
            )
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
            module = types.SimpleNamespace(
                UPLOAD_DIR=temp_dir,
                _set_conversion_job=lambda *_args, **_kwargs: None,
                _store_quality_report_artifacts=lambda *_args, **_kwargs: None,
            )
            install_production_publication_guard(module)

            with module.open(target, "wb") as handle:
                handle.write(b"report")
                handle.flush()
                self.assertEqual(target.read_bytes(), b"report")

    def test_ready_is_published_only_after_quality_writer_returns(self) -> None:
        state: dict[str, dict] = {}
        events: list[tuple[str, str]] = []

        def setter(job_id: str, **fields):
            state.setdefault(job_id, {}).update(fields)
            events.append(("set", str(fields.get("status") or "")))
            return dict(state[job_id])

        def quality_writer(job_id: str):
            self.assertEqual(state[job_id]["status"], "running")
            self.assertEqual(state[job_id]["progress"]["stage_id"], "finalizing_quality")
            events.append(("quality", "stored"))
            return {"stored": True}

        module = types.SimpleNamespace(
            UPLOAD_DIR=".",
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
        self.assertNotIn("job-a", module._PRODUCTION_PENDING_READY)
        self.assertEqual(events, [("set", "running"), ("quality", "stored"), ("set", "ready")])

    def test_quality_failure_never_publishes_ready(self) -> None:
        state: dict[str, dict] = {}
        statuses: list[str] = []

        def setter(job_id: str, **fields):
            state.setdefault(job_id, {}).update(fields)
            statuses.append(str(fields.get("status") or ""))
            return dict(state[job_id])

        def quality_writer(_job_id: str):
            raise OSError("quality storage unavailable")

        module = types.SimpleNamespace(
            UPLOAD_DIR=".",
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
