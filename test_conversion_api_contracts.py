from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from conversion_api_contracts import (
    DOWNLOAD_STATE_AVAILABLE,
    DOWNLOAD_STATE_MISSING_OUTPUT,
    DOWNLOAD_STATE_PENDING,
    ERROR_MISSING_OUTPUT,
    build_json_error_payload,
    resolve_conversion_download_state,
)
from conversion_jobs import (
    DEFAULT_CONVERSION_QUEUE_POLICY,
    STATUS_READY,
    STATUS_RUNNING,
    build_timed_out_job_fields,
    compute_job_elapsed_seconds,
    count_active_conversion_jobs,
    recommended_poll_interval_ms,
    should_timeout_job,
)


class ConversionApiContractsTests(unittest.TestCase):
    def test_json_error_payload_is_stable_and_optional_job_aware(self) -> None:
        payload = build_json_error_payload(
            "Brak pliku EPUB do pobrania.",
            error_code=ERROR_MISSING_OUTPUT,
            phase="download",
            job_id="job-1",
            retryable=False,
        )

        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], ERROR_MISSING_OUTPUT)
        self.assertEqual(payload["phase"], "download")
        self.assertEqual(payload["job_id"], "job-1")
        self.assertFalse(payload["retryable"])

    def test_download_state_requires_ready_job_url_and_existing_output(self) -> None:
        with NamedTemporaryFile(delete=False, suffix=".epub") as handle:
            handle.write(b"epub")
            output_path = handle.name

        try:
            state = resolve_conversion_download_state(
                job_status="ready",
                output_path=output_path,
                download_url="/convert/download/job-1",
            )
        finally:
            Path(output_path).unlink(missing_ok=True)

        self.assertEqual(state.status, DOWNLOAD_STATE_AVAILABLE)
        self.assertTrue(state.download_available)
        self.assertTrue(state.download_ready)
        self.assertEqual(state.download_url, "/convert/download/job-1")
        self.assertTrue(state.to_dict()["output_path_exists"])

    def test_download_state_blocks_ready_job_when_output_file_is_missing(self) -> None:
        state = resolve_conversion_download_state(
            job_status="ready",
            output_path="missing-output.epub",
            download_url="/convert/download/job-1",
        )

        self.assertEqual(state.status, DOWNLOAD_STATE_MISSING_OUTPUT)
        self.assertFalse(state.download_available)
        self.assertFalse(state.download_ready)
        self.assertIsNone(state.download_url)

    def test_download_state_keeps_active_jobs_pending(self) -> None:
        state = resolve_conversion_download_state(
            job_status="running",
            output_path="",
            download_url="/convert/download/job-1",
        )

        self.assertEqual(state.status, DOWNLOAD_STATE_PENDING)
        self.assertFalse(state.download_available)
        self.assertIsNone(state.download_url)

    def test_conversion_lifecycle_counts_active_jobs_and_poll_hints(self) -> None:
        now = datetime.now(UTC)
        jobs = {
            "running": {
                "status": STATUS_RUNNING,
                "created_at": (now - timedelta(seconds=90)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
            },
            "ready": {
                "status": STATUS_READY,
                "created_at": (now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            },
        }

        self.assertEqual(count_active_conversion_jobs(jobs), 1)
        self.assertEqual(
            recommended_poll_interval_ms(jobs["running"], now=now),
            DEFAULT_CONVERSION_QUEUE_POLICY.max_poll_interval_ms,
        )
        self.assertEqual(recommended_poll_interval_ms(jobs["ready"], now=now), 0)
        self.assertGreaterEqual(compute_job_elapsed_seconds(jobs["running"], now=now), 90)

    def test_conversion_lifecycle_detects_runtime_timeout(self) -> None:
        now = datetime.now(UTC)
        job = {
            "status": STATUS_RUNNING,
            "created_at": (now - timedelta(seconds=DEFAULT_CONVERSION_QUEUE_POLICY.max_runtime_seconds + 1)).isoformat().replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }

        timed_out, runtime_seconds, stale_seconds = should_timeout_job(job, now=now)
        fields = build_timed_out_job_fields(now=now, message="timeout", error="retry")

        self.assertTrue(timed_out)
        self.assertGreater(runtime_seconds or 0, DEFAULT_CONVERSION_QUEUE_POLICY.max_runtime_seconds)
        self.assertEqual(stale_seconds, 0)
        self.assertEqual(fields["status"], "timed_out")
        self.assertEqual(fields["error_code"], "conversion_timeout")


if __name__ == "__main__":
    unittest.main()
