import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as app_module
from app import app
from conversion_job_access import create_job_access_token, guest_owner_id
from supabase_auth import AuthContext


class JobOwnershipRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.store_temp_dir = tempfile.TemporaryDirectory()
        self.original_conversion_job_store = app_module._CONVERSION_JOB_STORE
        with app_module._CONVERSION_JOBS_LOCK:
            self.saved_jobs = dict(app_module._CONVERSION_JOBS)
            app_module._CONVERSION_JOBS.clear()
        app_module._CONVERSION_JOB_STORE = app_module.ConversionJobStore(
            app_module._CONVERSION_JOBS,
            app_module._CONVERSION_JOBS_LOCK,
            persistence_path=Path(self.store_temp_dir.name) / "conversion_jobs.json",
            active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
        )

    def tearDown(self) -> None:
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS.clear()
            app_module._CONVERSION_JOBS.update({key: dict(value) for key, value in self.saved_jobs.items()})
        app_module._CONVERSION_JOB_STORE = self.original_conversion_job_store
        self.store_temp_dir.cleanup()

    def _register_job(
        self,
        job_id: str,
        *,
        status: str = "ready",
        user_id: str = "",
        guest_id: str = "",
    ) -> dict:
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = {
            "job_id": job_id,
            "status": status,
            "message": "test",
            "source_type": "pdf",
            "filename": f"{job_id}.pdf",
            "created_at": timestamp,
            "updated_at": timestamp,
            "source_path": "",
            "output_path": "",
            "download_name": f"{job_id}.epub",
            "metadata": {},
            "artifacts": {},
            "artifact_storage": {},
            "runtime": {},
            "output_size_bytes": 0,
            "error": "",
            "error_code": "",
        }
        if user_id:
            job["user_id"] = user_id
        if guest_id:
            job["guest_owner_id"] = guest_owner_id(guest_id)
        app_module._CONVERSION_JOB_STORE.create(job)
        return job

    def test_authenticated_user_cannot_delete_another_users_job(self) -> None:
        self._register_job("job-user-b", user_id="user-b")
        cloud_client = MagicMock()
        cloud_client.get_user_job.return_value = None

        with (
            patch("app._resolve_request_auth_context", return_value=AuthContext(authenticated=True, user_id="user-a")),
            patch("app._supabase_library_client", return_value=cloud_client),
            patch("app._delete_supabase_conversion_job") as cloud_delete,
        ):
            response = self.client.delete(
                "/convert/jobs/job-user-b",
                headers={"Authorization": "Bearer token-a"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(app_module._CONVERSION_JOB_STORE.get("job-user-b"))
        cloud_delete.assert_not_called()

    def test_authenticated_user_cannot_retry_another_users_input(self) -> None:
        self._register_job("job-user-b", status="failed", user_id="user-b")
        cloud_client = MagicMock()
        cloud_client.get_user_job.return_value = None

        with (
            patch("app._resolve_request_auth_context", return_value=AuthContext(authenticated=True, user_id="user-a")),
            patch("app._supabase_library_client", return_value=cloud_client),
            patch("app._read_retry_input_artifact") as read_input,
        ):
            response = self.client.post(
                "/convert/retry/job-user-b",
                headers={"Authorization": "Bearer token-a"},
            )

        self.assertEqual(response.status_code, 404)
        read_input.assert_not_called()

    def test_guest_history_is_filtered_by_opaque_owner(self) -> None:
        guest_a = "guest-session-aaaaaaaaaaaaaaaa"
        guest_b = "guest-session-bbbbbbbbbbbbbbbb"
        self._register_job("job-guest-a", status="queued", guest_id=guest_a)
        self._register_job("job-guest-b", status="queued", guest_id=guest_b)

        with (
            patch("app._merge_cloud_jobs_into_store_for_request", return_value={"status": "skipped"}),
            patch("app._ensure_local_artifact_history_loaded", return_value={"status": "skipped"}),
        ):
            response = self.client.get(
                "/convert/jobs",
                base_url="https://api.example.com",
                headers={"X-KindleMaster-Guest-Id": guest_a},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([job["job_id"] for job in payload["jobs"]], ["job-guest-a"])

    def test_guest_cannot_delete_another_guest_job(self) -> None:
        guest_a = "guest-session-aaaaaaaaaaaaaaaa"
        guest_b = "guest-session-bbbbbbbbbbbbbbbb"
        self._register_job("job-guest-a", guest_id=guest_a)

        response = self.client.delete(
            "/convert/jobs/job-guest-a",
            base_url="https://api.example.com",
            headers={"X-KindleMaster-Guest-Id": guest_b},
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(app_module._CONVERSION_JOB_STORE.get("job-guest-a"))

    def test_signed_capability_allows_read_only_direct_navigation(self) -> None:
        self._register_job("job-user-b", status="running", user_id="user-b")
        with patch.dict("os.environ", {"KINDLEMASTER_JOB_ACCESS_SECRET": "test-secret"}, clear=False):
            token = create_job_access_token("job-user-b")
            response = self.client.get(
                f"/convert/status/job-user-b?access={token}",
                base_url="https://api.example.com",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["job_id"], "job-user-b")

    def test_invalid_capability_does_not_disclose_job(self) -> None:
        self._register_job("job-user-b", status="running", user_id="user-b")

        response = self.client.get(
            "/convert/status/job-user-b?access=invalid",
            base_url="https://api.example.com",
        )

        self.assertEqual(response.status_code, 404)

    def test_public_async_start_requires_guest_identity(self) -> None:
        response = self.client.post(
            "/convert/start",
            base_url="https://api.example.com",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error_code"], "guest_identity_required")


if __name__ == "__main__":
    unittest.main()
