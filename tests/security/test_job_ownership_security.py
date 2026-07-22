import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as app_module
from conversion_job_access import create_job_access_token, guest_owner_id
from durable_job_queue import DurableJobDatabase, SQLiteConversionJobStore
from supabase_auth import AuthContext


app = app_module.app


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

    @staticmethod
    def _authenticated_context(user_id: str) -> AuthContext:
        return AuthContext(authenticated=True, user_id=user_id, email_masked=f"{user_id[:1]}***@example.com")

    def test_authenticated_user_cannot_delete_another_users_job(self) -> None:
        self._register_job("job-user-b", user_id="user-b")
        cloud_client = MagicMock()
        cloud_client.get_user_job.return_value = None

        with (
            patch("conversion_job_store_security.validate_bearer_token", return_value=self._authenticated_context("user-a")),
            patch("app.validate_bearer_token", return_value=self._authenticated_context("user-a")),
            patch("app._supabase_library_client", return_value=cloud_client),
        ):
            response = self.client.delete(
                "/convert/jobs/job-user-b",
                base_url="https://api.example.com",
                headers={"Authorization": "Bearer token-a"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(app_module._CONVERSION_JOB_STORE.get("job-user-b"))

    def test_cloud_delete_does_not_remove_conflicting_local_job_owned_by_another_user(self) -> None:
        self._register_job("shared-job-id", user_id="user-b")
        cloud_job = self._register_job("cloud-user-a-copy", user_id="user-a")
        cloud_job.update({"job_id": "shared-job-id", "cloud": True})
        app_module._CONVERSION_JOB_STORE.delete("cloud-user-a-copy")
        auth_context = self._authenticated_context("user-a")

        with (
            patch("conversion_job_store_security.validate_bearer_token", return_value=auth_context),
            patch("app.validate_bearer_token", return_value=auth_context),
            patch("app._authenticated_request_context", return_value=({"id": "user-a"}, "token-a")),
            patch("app._load_supabase_conversion_jobs", return_value={"shared-job-id": cloud_job}),
            patch(
                "app._delete_supabase_conversion_job",
                return_value={"status": "deleted", "provider": "supabase"},
            ),
            patch("app._cleanup_deleted_conversion_job_files", return_value={"failed_paths": []}) as cleanup,
        ):
            response = self.client.delete(
                "/convert/jobs/shared-job-id",
                base_url="https://api.example.com",
                headers={"Authorization": "Bearer token-a"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["local_state_cleanup"]["status"], "protected")
        self.assertIsNotNone(app_module._CONVERSION_JOB_STORE.get("shared-job-id"))
        cleanup.assert_called_once()
        self.assertFalse(cleanup.call_args.kwargs["remove_artifact_job_dir"])

    def test_authenticated_user_cannot_retry_another_users_input(self) -> None:
        self._register_job("job-user-b", status="failed", user_id="user-b")
        cloud_client = MagicMock()
        cloud_client.get_user_job.return_value = None

        with (
            patch("conversion_job_store_security.validate_bearer_token", return_value=self._authenticated_context("user-a")),
            patch("app.validate_bearer_token", return_value=self._authenticated_context("user-a")),
            patch("app._supabase_library_client", return_value=cloud_client),
            patch("app._read_retry_input_artifact") as read_input,
        ):
            response = self.client.post(
                "/convert/retry/job-user-b",
                base_url="https://api.example.com",
                headers={"Authorization": "Bearer token-a"},
            )

        self.assertEqual(response.status_code, 404)
        read_input.assert_not_called()

    def test_authenticated_owner_can_delete_own_job(self) -> None:
        self._register_job("job-user-a", user_id="user-a")

        with (
            patch(
                "conversion_job_store_security.validate_bearer_token",
                return_value=self._authenticated_context("user-a"),
            ),
            patch("app.validate_bearer_token", return_value=self._authenticated_context("user-a")),
        ):
            response = self.client.delete(
                "/convert/jobs/job-user-a",
                base_url="https://api.example.com",
                headers={"Authorization": "Bearer token-a"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(app_module._CONVERSION_JOB_STORE.get("job-user-a"))

    def test_guest_history_is_filtered_by_opaque_owner_and_links_are_signed(self) -> None:
        guest_a = "guest-session-aaaaaaaaaaaaaaaa"
        guest_b = "guest-session-bbbbbbbbbbbbbbbb"
        self._register_job("job-guest-a", status="queued", guest_id=guest_a)
        self._register_job("job-guest-b", status="queued", guest_id=guest_b)

        with (
            patch("app._merge_cloud_jobs_into_store_for_request", return_value={"status": "skipped"}),
            patch("app._ensure_local_artifact_history_loaded", return_value={"status": "skipped"}),
            patch.dict("os.environ", {"KINDLEMASTER_JOB_ACCESS_SECRET": "test-secret"}, clear=False),
        ):
            response = self.client.get(
                "/convert/jobs",
                base_url="https://api.example.com",
                headers={"X-KindleMaster-Guest-Id": guest_a},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([job["job_id"] for job in payload["jobs"]], ["job-guest-a"])
        self.assertIn("access=", payload["jobs"][0]["quality_state_url"])
        self.assertEqual(response.headers.get("X-KindleMaster-Job-Links"), "signed")

    def test_public_guest_history_does_not_scan_or_expose_recovered_shared_artifacts(self) -> None:
        guest_id = "guest-session-aaaaaaaaaaaaaaaa"
        for index, recovery_field in enumerate(
            ("recovered_from_artifacts", "restored_from_artifacts", "restored_from_smoke", "imported_from_local")
        ):
            job_id = f"recovered-shared-job-{index}"
            self._register_job(job_id, guest_id=guest_id)
            app_module._set_conversion_job(job_id, **{recovery_field: True})

        with (
            patch("app._merge_cloud_jobs_into_store_for_request") as merge_cloud,
            patch("app._ensure_local_artifact_history_loaded") as import_artifacts,
        ):
            response = self.client.get(
                "/convert/jobs",
                base_url="https://api.example.com",
                headers={"X-KindleMaster-Guest-Id": guest_id},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["jobs"], [])
        self.assertEqual(payload["library_scope"], "guest")
        self.assertEqual(payload["import"]["source"], "disabled_for_public_guest")
        merge_cloud.assert_not_called()
        import_artifacts.assert_not_called()

    def test_durable_store_guest_history_is_scoped_by_owner(self) -> None:
        guest_a = "guest-session-aaaaaaaaaaaaaaaa"
        guest_b = "guest-session-bbbbbbbbbbbbbbbb"
        database = DurableJobDatabase(Path(self.store_temp_dir.name) / "runtime.sqlite3")
        app_module._CONVERSION_JOB_STORE = SQLiteConversionJobStore(database)
        self._register_job("job-guest-a", guest_id=guest_a)
        self._register_job("job-guest-b", guest_id=guest_b)
        self._register_job("job-ownerless")

        response_a = self.client.get(
            "/convert/jobs",
            base_url="https://api.example.com",
            headers={"X-KindleMaster-Guest-Id": guest_a},
        )
        response_b = self.client.get(
            "/convert/jobs",
            base_url="https://api.example.com",
            headers={"X-KindleMaster-Guest-Id": guest_b},
        )

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual([job["job_id"] for job in response_a.get_json()["jobs"]], ["job-guest-a"])
        self.assertEqual([job["job_id"] for job in response_b.get_json()["jobs"]], ["job-guest-b"])

    def test_durable_store_records_guest_owner_when_starting_conversion(self) -> None:
        guest_id = "guest-session-aaaaaaaaaaaaaaaa"
        database = DurableJobDatabase(Path(self.store_temp_dir.name) / "runtime.sqlite3")
        app_module._CONVERSION_JOB_STORE = SQLiteConversionJobStore(database)

        with (
            patch("app._active_conversion_job_count", return_value=0),
            patch("app._store_artifact_bytes", return_value={"provider": "local", "kind": "input"}),
            patch("app._submit_runtime_job", return_value={"mode": "durable"}),
            patch("app._spawn_conversion_job") as spawn_job,
        ):
            response = self.client.post(
                "/convert/start",
                base_url="https://api.example.com",
                headers={"X-KindleMaster-Guest-Id": guest_id},
                data={"file": (io.BytesIO(b"%PDF-1.4\n"), "guest.pdf")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["job_id"]
        stored = app_module._CONVERSION_JOB_STORE.get(job_id)
        self.assertEqual(stored["guest_owner_id"], guest_owner_id(guest_id))
        self.assertNotIn("user_id", stored)
        spawn_job.assert_called_once()

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

    def test_guest_owner_can_delete_own_job(self) -> None:
        guest_a = "guest-session-aaaaaaaaaaaaaaaa"
        self._register_job("job-guest-a", guest_id=guest_a)

        response = self.client.delete(
            "/convert/jobs/job-guest-a",
            base_url="https://api.example.com",
            headers={"X-KindleMaster-Guest-Id": guest_a},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(app_module._CONVERSION_JOB_STORE.get("job-guest-a"))

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

    def test_signed_capability_is_read_only(self) -> None:
        self._register_job("job-user-b", user_id="user-b")
        with patch.dict("os.environ", {"KINDLEMASTER_JOB_ACCESS_SECRET": "test-secret"}, clear=False):
            token = create_job_access_token("job-user-b")
            response = self.client.delete(
                f"/convert/jobs/job-user-b?access={token}",
                base_url="https://api.example.com",
            )

        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(app_module._CONVERSION_JOB_STORE.get("job-user-b"))

    def test_invalid_capability_does_not_disclose_job(self) -> None:
        self._register_job("job-user-b", status="running", user_id="user-b")

        response = self.client.get(
            "/convert/status/job-user-b?access=invalid",
            base_url="https://api.example.com",
        )

        self.assertEqual(response.status_code, 404)

    def test_public_async_start_requires_guest_identity_before_upload(self) -> None:
        response = self.client.post(
            "/convert/start",
            base_url="https://api.example.com",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error_code"], "guest_identity_required")


if __name__ == "__main__":
    unittest.main()
