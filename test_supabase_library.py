import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supabase_library import (
    SupabaseLibraryConfig,
    SupabaseLibraryClient,
    build_storage_path,
    job_row_to_runtime_job,
    load_supabase_library_config,
)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[object] = []

    def queue(self, response: object) -> None:
        self.responses.append(response)

    def __call__(self, url: str, *, method: str = "GET", headers=None, body: bytes | None = None):
        self.calls.append({"url": url, "method": method, "headers": dict(headers or {}), "body": body})
        if self.responses:
            return self.responses.pop(0)
        return {}


class SupabaseLibraryTests(unittest.TestCase):
    def _config(self) -> SupabaseLibraryConfig:
        return SupabaseLibraryConfig(
            enabled=True,
            configured=True,
            url="https://project.supabase.co",
            service_role_key="service-role-secret",
            bucket="kindlemaster-artifacts",
        )

    def test_library_config_loads_local_env_file_for_cloud_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.local"
            env_path.write_text(
                "\n".join(
                    [
                        "KINDLEMASTER_AUTH_PROVIDER=supabase",
                        "SUPABASE_URL=https://project.supabase.co",
                        "SUPABASE_SERVICE_ROLE_KEY=service-role-secret",
                        "SUPABASE_ARTIFACT_BUCKET=custom-bucket",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("supabase_library.DEFAULT_SUPABASE_ENV_FILES", (str(env_path),)), patch.dict(os.environ, {}, clear=True):
                config = load_supabase_library_config()

        self.assertTrue(config.enabled)
        self.assertTrue(config.configured)
        self.assertEqual(config.url, "https://project.supabase.co")
        self.assertEqual(config.service_role_key, "service-role-secret")
        self.assertEqual(config.bucket, "custom-bucket")

    def test_library_config_strips_utf8_bom_from_environment_values(self) -> None:
        config = load_supabase_library_config(
            {
                "KINDLEMASTER_AUTH_PROVIDER": "supabase",
                "SUPABASE_URL": "\ufeffhttps://project.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "\ufeffservice-role-secret",
            }
        )

        self.assertEqual(config.url, "https://project.supabase.co")
        self.assertEqual(config.service_role_key, "service-role-secret")

    def test_build_storage_path_is_user_job_scoped_and_sanitized(self) -> None:
        path = build_storage_path(
            user_id="user/../id",
            job_id="job:123",
            kind="output",
            filename="../Final EPUB.epub",
        )

        self.assertEqual(path, "user-id/job-123/output/Final-EPUB.epub")

    def test_upsert_job_snapshot_omits_source_path_and_uses_service_role_only_server_side(self) -> None:
        transport = FakeTransport()
        transport.queue([{"job_id": "job-1"}])
        client = SupabaseLibraryClient(self._config(), transport=transport)

        client.upsert_job_snapshot(
            user_id="9d0c32f5-9c1e-4686-9a3b-000000000001",
            job={
                "job_id": "job-1",
                "status": "ready",
                "filename": "input.pdf",
                "source_type": "pdf",
                "source_path": "C:/tmp/private.pdf",
                "output_path": "C:/tmp/output.epub",
                "download_name": "output.epub",
                "created_at": "2026-05-21T10:00:00Z",
                "updated_at": "2026-05-21T10:01:00Z",
                "metadata": {"profile": "auto-premium"},
                "auto_repair": {"status": "not_run"},
                "email_delivery": {"status": "not_sent"},
                "runtime": {"provider": "local"},
                "output_size_bytes": 123,
            },
            quality_state={"release_verdict": "release_ready"},
            imported_from_local=True,
        )

        call = transport.calls[0]
        self.assertIn("/rest/v1/conversion_jobs", call["url"])
        self.assertEqual(call["headers"]["apikey"], "service-role-secret")
        payload = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(payload["user_id"], "9d0c32f5-9c1e-4686-9a3b-000000000001")
        self.assertEqual(payload["quality_state_snapshot"]["release_verdict"], "release_ready")
        self.assertTrue(payload["imported_from_local"])
        self.assertNotIn("source_path", payload)
        self.assertNotIn("output_path", payload)

    def test_upload_artifact_records_storage_and_database_metadata(self) -> None:
        transport = FakeTransport()
        transport.queue({"Key": "stored"})
        transport.queue([{"id": "artifact-id"}])
        client = SupabaseLibraryClient(self._config(), transport=transport)

        record = client.upload_artifact_bytes(
            user_id="user-id",
            job_id="job-1",
            kind="output",
            filename="Final.epub",
            data=b"epub",
            content_type="application/epub+zip",
        )

        upload_call, metadata_call = transport.calls
        self.assertIn("/storage/v1/object/kindlemaster-artifacts/user-id/job-1/output/Final.epub", upload_call["url"])
        self.assertEqual(upload_call["headers"]["x-upsert"], "true")
        self.assertIn("/rest/v1/conversion_artifacts", metadata_call["url"])
        self.assertEqual(record["storage_path"], "user-id/job-1/output/Final.epub")
        self.assertEqual(record["kind"], "output")

    def test_job_row_to_runtime_job_preserves_quality_snapshot_and_cloud_artifacts(self) -> None:
        job = job_row_to_runtime_job(
            {
                "job_id": "job-1",
                "user_id": "user-1",
                "status": "ready",
                "filename": "input.pdf",
                "source_type": "pdf",
                "download_name": "input.epub",
                "metadata": {"profile": "auto-premium"},
                "quality_state_snapshot": {"release_verdict": "release_ready"},
                "output_size_bytes": 12,
                "created_at": "2026-05-21T10:00:00Z",
                "updated_at": "2026-05-21T10:01:00Z",
            },
            artifacts=[
                {
                    "kind": "output",
                    "filename": "input.epub",
                    "content_type": "application/epub+zip",
                    "size_bytes": 12,
                    "storage_bucket": "kindlemaster-artifacts",
                    "storage_path": "user-1/job-1/output/input.epub",
                }
            ],
        )

        self.assertTrue(job["cloud"])
        self.assertEqual(job["quality_state_snapshot"]["release_verdict"], "release_ready")
        self.assertEqual(job["artifacts"]["output"]["provider"], "supabase")
        self.assertEqual(job["artifacts"]["output"]["storage_path"], "user-1/job-1/output/input.epub")

    def test_get_job_by_id_loads_owner_and_artifacts_for_authorized_recovery(self) -> None:
        transport = FakeTransport()
        transport.queue(
            [
                {
                    "job_id": "job-1",
                    "user_id": "user-1",
                    "status": "ready",
                    "filename": "input.pdf",
                    "source_type": "pdf",
                }
            ]
        )
        transport.queue(
            [
                {
                    "job_id": "job-1",
                    "user_id": "user-1",
                    "kind": "chess_rebuild_bundle",
                    "filename": "job-1.chess-rebuild.zip",
                    "storage_bucket": "kindlemaster-artifacts",
                    "storage_path": "user-1/job-1/chess_rebuild_bundle/job-1.chess-rebuild.zip",
                }
            ]
        )
        client = SupabaseLibraryClient(self._config(), transport=transport)

        job = client.get_job_by_id(job_id="job-1")

        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job["user_id"], "user-1")
        self.assertTrue(job["cloud"])
        self.assertIn("chess_rebuild_bundle", job["artifacts"])
        self.assertIn("job_id=eq.job-1", transport.calls[0]["url"])
        self.assertNotIn("user_id=", transport.calls[0]["url"])

    def test_import_local_jobs_skips_missing_outputs_and_reports_counts(self) -> None:
        transport = FakeTransport()
        transport.queue([{"job_id": "job-ready"}])
        transport.queue({"Key": "stored"})
        transport.queue([{"id": "artifact-id"}])
        client = SupabaseLibraryClient(self._config(), transport=transport)
        fixture = Path(__file__).resolve()

        result = client.import_local_jobs(
            user_id="user-id",
            jobs={
                "job-ready": {
                    "job_id": "job-ready",
                    "status": "ready",
                    "filename": "input.pdf",
                    "source_type": "pdf",
                    "download_name": "input.epub",
                    "output_path": str(fixture),
                    "created_at": "2026-05-21T10:00:00Z",
                    "updated_at": "2026-05-21T10:01:00Z",
                    "metadata": {},
                },
                "job-running": {"job_id": "job-running", "status": "running"},
                "job-missing": {"job_id": "job-missing", "status": "ready", "output_path": "missing.epub"},
            },
            quality_state_builder=lambda _job_id, _job: {"release_verdict": "release_ready"},
        )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
