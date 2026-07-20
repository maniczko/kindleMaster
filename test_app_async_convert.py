import io
import os
import tempfile
import threading
import unittest
import json
import urllib.error
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from app import app
from epub_delivery_repair import DeliveryRepairResult
from supabase_auth import AuthContext


class AppAsyncConvertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.cleanup_paths: list[str] = []
        self.cleanup_job_ids: list[str] = []
        self.store_temp_dir = tempfile.TemporaryDirectory()
        self.original_conversion_job_store = app_module._CONVERSION_JOB_STORE
        with app_module._CONVERSION_JOBS_LOCK:
            self._saved_jobs = dict(app_module._CONVERSION_JOBS)
            app_module._CONVERSION_JOBS.clear()
        app_module._CONVERSION_JOB_STORE = app_module.ConversionJobStore(
            app_module._CONVERSION_JOBS,
            app_module._CONVERSION_JOBS_LOCK,
            persistence_path=Path(self.store_temp_dir.name) / "conversion_jobs.json",
            active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
        )

    def tearDown(self) -> None:
        for job_id, job in app_module._CONVERSION_JOBS.items():
            if job_id not in self.cleanup_job_ids:
                continue
            for artifact in (job.get("artifacts", {}) or {}).values():
                if not isinstance(artifact, dict) or artifact.get("provider") != "local":
                    continue
                location = str(artifact.get("location", "") or "")
                if location and os.path.exists(location):
                    os.remove(location)
        for path in self.cleanup_paths:
            if path and os.path.exists(path):
                os.remove(path)
        with app_module._CONVERSION_JOBS_LOCK:
            for job_id in self.cleanup_job_ids:
                app_module._CONVERSION_JOBS.pop(job_id, None)
            app_module._CONVERSION_JOBS.clear()
            app_module._CONVERSION_JOBS.update(
                {job_id: dict(job) for job_id, job in self._saved_jobs.items()}
            )
        app_module._CONVERSION_JOB_STORE = self.original_conversion_job_store
        self.store_temp_dir.cleanup()

    def _write_epub_fixture(self, job_id: str, body: str) -> str:
        output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with zipfile.ZipFile(output_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr(
                "EPUB/chapter.xhtml",
                f"<html><body><h1>Chapter</h1><p>{body}</p></body></html>",
            )
        self.cleanup_paths.append(output_path)
        return output_path

    def _release_ready_metadata(self) -> dict:
        return {
            "source_type": "pdf",
            "profile": "book_reflow",
            "validation": "passed",
            "validation_tool": "epubcheck",
            "epubcheck_detail": {"status": "passed", "tool": "epubcheck"},
            "toc_preview": {"status": "passed"},
            "metadata_health": {"status": "passed"},
            "link_health": {"status": "passed"},
            "visible_junk": {"status": "passed"},
            "asset_summary": {"status": "passed", "asset_budget_status": "passed"},
            "text_cleanup": {"status": "passed"},
            "reference_cleanup": {"status": "passed"},
            "semantic_cleanup": {"status": "passed"},
            "ocr_quality": {"status": "passed"},
            "reading_order": {"status": "passed"},
            "content_metrics": {"source_table_count": 0, "xhtml_table_count": 0},
            "quality_gate_mode": "off",
        }

    def _register_delivery_job(
        self,
        job_id: str,
        *,
        status: str = "ready",
        metadata: dict | None = None,
        output_path: str | None = None,
        output_size_bytes: int = 4096,
    ) -> None:
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if output_path is None and status == "ready":
            output_path = self._write_epub_fixture(job_id, "ready")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": status,
                "message": "EPUB gotowy do pobrania." if status == "ready" else "Konwersja w toku.",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": output_path or "",
                "download_name": "sample.epub",
                "metadata": metadata if metadata is not None else self._release_ready_metadata(),
                "output_size_bytes": output_size_bytes,
                "error": "",
                "error_code": "",
            }
        self.cleanup_job_ids.append(job_id)

    def _attach_input_pdf_artifact(self, job_id: str, *, filename: str = "source.pdf", data: bytes = b"%PDF-1.7\n") -> str:
        artifact_dir = Path("output") / "artifacts" / job_id / "input"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        artifact_path.write_bytes(data)
        with app_module._CONVERSION_JOBS_LOCK:
            job = app_module._CONVERSION_JOBS[job_id]
            job.setdefault("artifacts", {})["input"] = {
                "provider": "local",
                "kind": "input",
                "filename": filename,
                "content_type": "application/pdf",
                "location": str(artifact_path),
                "size_bytes": len(data),
            }
            job["source_preview_url"] = f"/convert/preview/{job_id}/input"
        return str(artifact_path)

    def test_convert_start_status_and_download_roundtrip(self) -> None:
        def fake_spawn(**kwargs) -> None:
            job_id = kwargs["job_id"]
            output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
            with open(output_path, "wb") as handle:
                handle.write(b"async-epub")
            self.cleanup_paths.append(output_path)
            self.cleanup_paths.append(os.path.join(app_module.UPLOAD_DIR, f"{job_id}.pdf"))
            app_module._set_conversion_job(
                job_id,
                status="ready",
                message="EPUB gotowy do pobrania.",
                output_path=output_path,
                download_name="sample.epub",
                metadata={
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "confidence": 0.94,
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "strategy": "premium",
                    "sections": 3,
                    "assets": 1,
                    "layout": "reflowable",
                    "warnings": 0,
                    "warning_list": [],
                    "high_risk_pages": 0,
                    "high_risk_page_list": [],
                    "high_risk_sections": 0,
                    "high_risk_section_list": [],
                    "render_budget_class": "fixed_layout_dense",
                    "render_budget_attempt": "fallback",
                    "size_budget_status": "passed",
                    "size_budget_message": "fallback ok",
                    "target_warn_bytes": 2048,
                    "target_hard_bytes": 4096,
                    "final_output_size_bytes": len(b"async-epub"),
                    "heading_repair": {
                        "status": "applied",
                        "release": "pass_with_review",
                        "toc_before": 1,
                        "toc_after": 3,
                        "removed": 1,
                        "review": 2,
                        "epubcheck": "passed",
                        "error": "",
                    },
                },
                error="",
            )

        with patch("app._spawn_conversion_job", side_effect=fake_spawn):
            response = self.client.post(
                "/convert/start",
                data={
                    "file": (io.BytesIO(b"%PDF-1.4\n%synthetic\n"), "sample.pdf"),
                    "profile": "auto-premium",
                    "ocr": "false",
                    "language": "pl",
                    "route_model_mode": "shadow",
                    "quality_gate_mode": "strict",
                    "heading_repair": "true",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["poll_after_ms"], app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS)
        self.assertEqual(payload["source_preview_url"], f"/convert/preview/{payload['job_id']}/input")
        self.assertEqual(payload["runtime"]["provider"], "local")
        self.assertEqual(payload["runtime"]["replay"]["command"]["name"], "convert")
        replay_kwargs = payload["runtime"]["replay"]["command"]["kwargs"]
        self.assertEqual(replay_kwargs["route_model_mode"], "shadow")
        self.assertEqual(replay_kwargs["quality_gate_mode"], "strict")
        self.assertEqual(payload["artifacts"]["input"]["kind"], "input")
        self.assertIn(payload["artifacts"]["input"]["status"], {"stored", "unavailable", "failed"})
        job_id = payload["job_id"]
        self.cleanup_job_ids.append(job_id)

        status_response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertEqual(status_payload["status"], "ready")
        self.assertEqual(status_payload["conversion"]["profile"], "book_reflow")
        self.assertEqual(status_payload["conversion"]["output_size_bytes"], len(b"async-epub"))
        self.assertEqual(status_payload["conversion"]["render_budget_class"], "fixed_layout_dense")
        self.assertEqual(status_payload["conversion"]["render_budget_attempt"], "fallback")
        self.assertEqual(status_payload["conversion"]["size_budget_status"], "passed")
        self.assertEqual(status_payload["output_size_bytes"], len(b"async-epub"))
        self.assertEqual(status_payload["poll_after_ms"], 0)
        self.assertEqual(status_payload["download_url"], f"/convert/download/{job_id}")
        self.assertEqual(status_payload["source_preview_url"], f"/convert/preview/{job_id}/input")
        self.assertTrue(status_payload["download_available"])
        self.assertEqual(status_payload["download_state"]["status"], "available")
        self.assertEqual(status_payload["runtime"]["provider"], "local")
        self.assertIn("input", status_payload["artifacts"])
        self.assertTrue(status_payload["quality_state"]["download_available"])
        self.assertEqual(status_payload["quality_state"]["download_state"]["status"], "available")
        self.assertIn("input", status_payload["quality_state"]["artifacts"])

        jobs_response = self.client.get("/convert/jobs")
        self.assertEqual(jobs_response.status_code, 200)
        jobs_payload = jobs_response.get_json()
        history_job = next(job for job in jobs_payload["jobs"] if job["job_id"] == job_id)
        self.assertEqual(history_job["source_preview_url"], f"/convert/preview/{job_id}/input")

        download_response = self.client.get(f"/convert/download/{job_id}")
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.data, b"async-epub")
        self.assertEqual(download_response.headers.get("X-Source-Type"), "pdf")
        self.assertEqual(download_response.headers.get("X-Heading-Repair-Status"), "applied")
        self.assertEqual(download_response.headers.get("X-Render-Budget-Class"), "fixed_layout_dense")
        self.assertEqual(download_response.headers.get("X-Render-Budget-Attempt"), "fallback")
        download_response.close()

        preview_response = self.client.get(f"/convert/preview/{job_id}/input")
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.data, b"%PDF-1.4\n%synthetic\n")
        self.assertEqual(preview_response.mimetype, "application/pdf")
        self.assertIn("inline", preview_response.headers.get("Content-Disposition", ""))
        self.assertEqual(preview_response.headers.get("X-Source-Type"), "pdf")
        preview_response.close()

    def test_root_serves_react_shell_and_legacy_remains_available(self) -> None:
        root_response = self.client.get("/")

        self.assertEqual(root_response.status_code, 200)
        self.assertIn('id="root"', root_response.get_data(as_text=True))
        with patch.dict(os.environ, {"KINDLEMASTER_ENABLE_LEGACY_UI": "1"}):
            legacy_response = self.client.get("/legacy")
        self.assertEqual(legacy_response.status_code, 200)
        self.assertIn("Lokalny panel EPUB", legacy_response.get_data(as_text=True))

    def test_delivery_config_reports_smtp_capability_without_secrets(self) -> None:
        with patch.dict(os.environ, {"KINDLEMASTER_EMAIL_DELIVERY": "0"}, clear=False):
            response = self.client.get("/convert/delivery/config")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["delivery"]["provider"], "smtp")
        self.assertFalse(payload["delivery"]["enabled"])
        self.assertFalse(payload["delivery"]["configured"])
        self.assertNotIn("password", json.dumps(payload).lower())

    def test_auth_config_hides_supabase_service_role_key(self) -> None:
        env = {
            "KINDLEMASTER_AUTH_PROVIDER": "supabase",
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_public",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            response = self.client.get("/auth/config")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["auth"]["enabled"])
        self.assertTrue(payload["auth"]["configured"])
        self.assertEqual(payload["auth"]["publishable_key"], "sb_publishable_public")
        serialized = json.dumps(payload)
        self.assertNotIn("service-role-secret", serialized)
        self.assertNotIn("service_role", serialized.lower())

    def test_auth_me_validates_bearer_token_and_masks_email(self) -> None:
        with patch("app.validate_bearer_token") as validate_token:
            validate_token.return_value = AuthContext(
                authenticated=True,
                user_id="user-1",
                email_masked="r***@example.com",
            )
            response = self.client.get("/auth/me", headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["auth"]["authenticated"])
        self.assertEqual(payload["auth"]["user_id"], "user-1")
        self.assertEqual(payload["auth"]["email_masked"], "r***@example.com")

    def test_authenticated_convert_start_attaches_user_id_to_job(self) -> None:
        def fake_spawn(**_kwargs) -> None:
            return None

        with patch("app.validate_bearer_token") as validate_token, patch("app._spawn_conversion_job", side_effect=fake_spawn):
            validate_token.return_value = AuthContext(
                authenticated=True,
                user_id="user-1",
                email_masked="r***@example.com",
            )
            response = self.client.post(
                "/convert/start",
                headers={"Authorization": "Bearer token"},
                data={"file": (io.BytesIO(b"%PDF-1.4\n%synthetic\n"), "sample.pdf")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["job_id"]
        self.cleanup_job_ids.append(job_id)
        job = app_module._get_conversion_job(job_id)
        self.assertEqual(job["user_id"], "user-1")
        self.assertEqual(job["auth"]["provider"], "supabase")

    def test_cross_user_status_does_not_leak_local_job(self) -> None:
        self._register_delivery_job("owned-job")
        app_module._set_conversion_job("owned-job", user_id="owner-a")

        with patch("app.validate_bearer_token") as validate_token:
            validate_token.return_value = AuthContext(
                authenticated=True,
                user_id="owner-b",
                email_masked="b***@example.com",
            )
            response = self.client.get("/convert/status/owned-job", headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 404)

    def test_cross_user_quality_does_not_leak_owned_local_job(self) -> None:
        self._register_delivery_job("owned-quality-job")
        app_module._set_conversion_job("owned-quality-job", user_id="owner-a")

        with patch("app.validate_bearer_token") as validate_token:
            validate_token.return_value = AuthContext(
                authenticated=True,
                user_id="owner-b",
                email_masked="b***@example.com",
            )
            response = self.client.get("/convert/quality/owned-quality-job", headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 404)

    def test_authenticated_local_fallback_quality_keeps_ownerless_local_job_accessible(self) -> None:
        self._register_delivery_job("fallback-ready")
        app_module._set_conversion_job("fallback-ready", user_id="")

        class FailingCloudLibrary:
            def list_user_jobs(self, *, user_id, limit):
                raise RuntimeError("supabase offline")

            def get_user_job(self, *, user_id, job_id):
                raise RuntimeError("supabase offline")

        with patch("app.validate_bearer_token") as validate_token, patch("app._supabase_library_client", return_value=FailingCloudLibrary()):
            validate_token.return_value = AuthContext(
                authenticated=True,
                user_id="user-1",
                email_masked="r***@example.com",
            )
            jobs_response = self.client.get("/convert/jobs", headers={"Authorization": "Bearer token"})
            quality_response = self.client.get("/convert/quality/fallback-ready", headers={"Authorization": "Bearer token"})

        self.assertEqual(jobs_response.status_code, 200)
        jobs_payload = jobs_response.get_json()
        self.assertEqual(jobs_payload["library_scope"], "local_fallback")
        self.assertIn("fallback-ready", {job["job_id"] for job in jobs_payload["jobs"]})
        self.assertEqual(quality_response.status_code, 200)
        quality_payload = quality_response.get_json()
        self.assertTrue(quality_payload["success"])
        self.assertEqual(quality_payload["job_id"], "fallback-ready")

    def test_import_local_history_requires_authenticated_user_and_calls_supabase(self) -> None:
        self._register_delivery_job("import-ready")

        class FakeCloudLibrary:
            def import_local_jobs(self, *, user_id, jobs, quality_state_builder):
                self.user_id = user_id
                self.jobs = jobs
                self.quality_state_builder = quality_state_builder
                return {"success": True, "imported": 1, "skipped": 0, "failed": 0, "failures": []}

        fake_library = FakeCloudLibrary()
        with patch("app.validate_bearer_token") as validate_token, patch("app._supabase_library_client", return_value=fake_library):
            validate_token.return_value = AuthContext(
                authenticated=True,
                user_id="user-1",
                email_masked="r***@example.com",
            )
            response = self.client.post("/user/library/import-local", headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["import"]["imported"], 1)
        self.assertEqual(fake_library.user_id, "user-1")
        self.assertIn("import-ready", fake_library.jobs)

    def test_user_profile_roundtrip_stores_smtp_defaults_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            secrets_path = str(Path(temp_dir) / "missing-secrets.env")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path, "KINDLEMASTER_ENV_FILE": secrets_path}, clear=False):
                put_response = self.client.put(
                    "/user/profile",
                    json={
                        "conversion": {
                            "default_profile": "magazine",
                            "default_language": "en",
                            "heading_repair": False,
                        },
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.example.com",
                            "port": 587,
                            "security": "starttls",
                            "username": "apikey",
                            "password": "must-not-leak",
                            "from_address": "operator@example.com",
                            "default_recipient": "reader@kindle.com",
                            "max_attachment_bytes": 123456,
                        },
                    },
                )
                get_response = self.client.get("/user/profile")

        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(get_response.status_code, 200)
        payload = get_response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["profile"]["conversion"]["default_profile"], "magazine")
        self.assertEqual(payload["profile"]["email_delivery"]["host"], "smtp.example.com")
        self.assertEqual(payload["profile"]["email_delivery"]["default_recipient"], "reader@kindle.com")
        self.assertFalse(payload["profile"]["email_delivery"]["secret_configured"])
        serialized = json.dumps(payload)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("password", serialized.lower())

    def test_authenticated_user_profile_loads_cloud_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path}, clear=False):
                with patch("app.validate_bearer_token") as validate_token, patch("app.load_cloud_user_profile") as load_cloud:
                    validate_token.return_value = AuthContext(
                        authenticated=True,
                        user_id="user-1",
                        email_masked="r***@example.com",
                    )
                    load_cloud.return_value = {
                        "conversion": {"default_profile": "book", "default_language": "en", "force_ocr": False, "heading_repair": True},
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.example.com",
                            "port": 587,
                            "security": "starttls",
                            "username": "apikey",
                            "from_address": "operator@example.com",
                            "default_recipient": "reader@kindle.com",
                            "max_attachment_bytes": 52428800,
                        },
                    }
                    response = self.client.get("/user/profile", headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["profile_scope"], "account")
        self.assertEqual(payload["cloud_sync"]["status"], "synced")
        self.assertEqual(payload["profile"]["email_delivery"]["default_recipient"], "reader@kindle.com")
        load_cloud.assert_called_once()

    def test_authenticated_user_profile_save_persists_to_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path}, clear=False):
                with patch("app.validate_bearer_token") as validate_token, patch("app.save_cloud_user_profile") as save_cloud:
                    validate_token.return_value = AuthContext(
                        authenticated=True,
                        user_id="user-1",
                        email_masked="r***@example.com",
                    )
                    save_cloud.return_value = {
                        "conversion": {"default_profile": "book", "default_language": "pl", "force_ocr": False, "heading_repair": True},
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.example.com",
                            "port": 587,
                            "security": "starttls",
                            "username": "apikey",
                            "from_address": "operator@example.com",
                            "default_recipient": "reader@kindle.com",
                            "max_attachment_bytes": 52428800,
                        },
                    }
                    response = self.client.put(
                        "/user/profile",
                        headers={"Authorization": "Bearer token"},
                        json={"email_delivery": {"enabled": True, "default_recipient": "reader@kindle.com"}},
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["profile_scope"], "account")
        self.assertEqual(payload["cloud_sync"]["status"], "synced")
        self.assertEqual(payload["profile"]["email_delivery"]["default_recipient"], "reader@kindle.com")
        save_cloud.assert_called_once()

    def test_release_ready_job_exposes_send_to_kindle_test_surface_before_delivery(self) -> None:
        self._register_delivery_job("delivery-testable")

        smtp_env = {
            "KINDLEMASTER_EMAIL_DELIVERY": "1",
            "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
            "KINDLEMASTER_SMTP_USERNAME": "apikey",
            "KINDLEMASTER_SMTP_PASSWORD": "smtp-api-token",
            "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
        }
        with patch.dict(os.environ, smtp_env, clear=False):
            config_response = self.client.get("/convert/delivery/config")
            status_response = self.client.get("/convert/status/delivery-testable")
            quality_response = self.client.get("/convert/quality/delivery-testable")

        self.assertEqual(config_response.status_code, 200)
        config_payload = config_response.get_json()
        self.assertTrue(config_payload["delivery"]["enabled"])
        self.assertTrue(config_payload["delivery"]["configured"])
        self.assertEqual(config_payload["delivery"]["provider"], "smtp")
        self.assertNotIn("smtp-api-token", json.dumps(config_payload).lower())

        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertTrue(status_payload["download_available"])
        self.assertEqual(status_payload["email_delivery"]["status"], "not_sent")
        self.assertTrue(status_payload["quality_state"]["send_to_kindle_ready"])
        self.assertEqual(status_payload["quality_state"]["send_to_kindle_blockers"], [])

        self.assertEqual(quality_response.status_code, 200)
        quality_payload = quality_response.get_json()
        self.assertEqual(quality_payload["email_delivery"]["status"], "not_sent")
        self.assertTrue(quality_payload["quality_state"]["send_to_kindle_ready"])

    def test_convert_delivery_email_sends_release_ready_epub_and_persists_masked_result(self) -> None:
        self._register_delivery_job("delivery-ready")

        with patch.dict(
            os.environ,
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_USERNAME": "apikey",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            },
            clear=False,
        ):
            with patch("email_delivery.smtplib.SMTP") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                response = self.client.post(
                    "/convert/delivery/delivery-ready/email",
                    json={"to": "reader-device@kindle.com"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["delivery"]["status"], "sent")
        self.assertEqual(payload["delivery"]["masked_recipient"], "r***@kindle.com")
        self.assertEqual(payload["delivery"]["attachment_filename"], "sample.epub")
        diagnostics = payload["delivery"]["diagnostics"]
        self.assertEqual(diagnostics["smtp"]["host"], "smtp.example.com")
        self.assertTrue(diagnostics["smtp"]["accepted_by_smtp"])
        self.assertEqual(diagnostics["message"]["content_type"], "multipart/mixed")
        self.assertTrue(diagnostics["message"]["has_plain_text_body"])
        self.assertTrue(diagnostics["message"]["has_date_header"])
        self.assertTrue(diagnostics["message"]["has_message_id_header"])
        self.assertEqual(diagnostics["attachment"]["content_type"], "application/epub+zip")
        self.assertEqual(diagnostics["attachment"]["content_disposition"], "attachment")
        smtp.send_message.assert_called_once()
        stored = app_module._get_conversion_job("delivery-ready")["email_delivery"]
        self.assertEqual(stored["status"], "sent")
        self.assertIn("diagnostics", stored)
        serialized = json.dumps(stored)
        self.assertNotIn("reader-device@kindle.com", serialized)
        self.assertNotIn("secret", serialized)

        quality_response = self.client.get("/convert/quality/delivery-ready")
        self.assertEqual(quality_response.status_code, 200)
        self.assertEqual(quality_response.get_json()["email_delivery"]["status"], "sent")
        self.assertIn("diagnostics", quality_response.get_json()["email_delivery"])

    def test_convert_delivery_email_can_send_source_pdf_attachment(self) -> None:
        self._register_delivery_job("delivery-pdf")
        self._attach_input_pdf_artifact("delivery-pdf", filename="cropped-source.pdf", data=b"%PDF-1.7 cropped")

        with patch.dict(
            os.environ,
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_USERNAME": "apikey",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            },
            clear=False,
        ):
            with patch("email_delivery.smtplib.SMTP") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                response = self.client.post(
                    "/convert/delivery/delivery-pdf/email",
                    json={"to": "reader-device@kindle.com", "artifact": "pdf"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["delivery"]["artifact"], "pdf")
        self.assertEqual(payload["delivery"]["attachment_filename"], "cropped-source.pdf")
        self.assertEqual(payload["delivery"]["attachment_content_type"], "application/pdf")
        self.assertEqual(payload["delivery"]["diagnostics"]["attachment"]["content_type"], "application/pdf")
        self.assertEqual(payload["delivery"]["quality_gate"]["artifact"], "pdf")
        smtp.send_message.assert_called_once()
        stored = app_module._get_conversion_job("delivery-pdf")["email_delivery"]
        self.assertEqual(stored["artifact"], "pdf")
        self.assertNotIn("reader-device@kindle.com", json.dumps(stored))

    def test_convert_delivery_email_returns_503_when_smtp_is_disabled(self) -> None:
        self._register_delivery_job("delivery-disabled")

        with patch.dict(os.environ, {"KINDLEMASTER_EMAIL_DELIVERY": "0"}, clear=False):
            response = self.client.post(
                "/convert/delivery/delivery-disabled/email",
                json={"to": "reader@kindle.com"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "delivery_unavailable")
        self.assertIn("delivery", payload)

    def test_convert_delivery_email_rejects_invalid_recipient(self) -> None:
        self._register_delivery_job("delivery-invalid")

        with patch.dict(
            os.environ,
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_USERNAME": "apikey",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            },
            clear=False,
        ):
            response = self.client.post(
                "/convert/delivery/delivery-invalid/email",
                json={"to": "not-an-email"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "invalid_delivery_request")

    def test_convert_delivery_email_blocks_unknown_active_and_missing_outputs_but_not_quality_review(self) -> None:
        unknown_response = self.client.post("/convert/delivery/missing/email", json={"to": "reader@kindle.com"})
        self.assertEqual(unknown_response.status_code, 404)
        self.assertEqual(unknown_response.get_json()["error_code"], "delivery_not_ready")

        smtp_env = {
            "KINDLEMASTER_EMAIL_DELIVERY": "1",
            "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
            "KINDLEMASTER_SMTP_USERNAME": "apikey",
            "KINDLEMASTER_SMTP_PASSWORD": "secret",
            "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
        }
        with patch.dict(os.environ, smtp_env, clear=False):
            self._register_delivery_job("delivery-running", status="running", output_path="")
            running_response = self.client.post("/convert/delivery/delivery-running/email", json={"to": "reader@kindle.com"})
            self.assertEqual(running_response.status_code, 409)

            self._register_delivery_job(
                "delivery-review",
                metadata={**self._release_ready_metadata(), "quality_gate_mode": "draft"},
            )
            with patch("email_delivery.smtplib.SMTP") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                review_response = self.client.post("/convert/delivery/delivery-review/email", json={"to": "reader@kindle.com"})
            self.assertEqual(review_response.status_code, 200)
            review_payload = review_response.get_json()
            self.assertTrue(review_payload["success"])
            self.assertTrue(review_payload["delivery"]["quality_gate"]["warning_only"])
            self.assertEqual(review_payload["delivery"]["quality_gate"]["release_verdict"], "release_blocked")
            smtp.send_message.assert_called_once()

            self._register_delivery_job("delivery-missing-output", output_path="", output_size_bytes=0)
            missing_output_response = self.client.post(
                "/convert/delivery/delivery-missing-output/email",
                json={"to": "reader@kindle.com"},
            )
        self.assertEqual(missing_output_response.status_code, 409)

    def test_convert_delivery_email_maps_smtp_exception_to_502_without_recipient_leak(self) -> None:
        self._register_delivery_job("delivery-smtp-fail")

        with patch.dict(
            os.environ,
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_USERNAME": "apikey",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            },
            clear=False,
        ):
            with patch("email_delivery.smtplib.SMTP") as smtp_cls:
                smtp_cls.return_value.__enter__.return_value.send_message.side_effect = RuntimeError("smtp down")
                response = self.client.post(
                    "/convert/delivery/delivery-smtp-fail/email",
                    json={"to": "reader-device@kindle.com"},
                )

        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "delivery_failed")
        self.assertEqual(payload["delivery"]["diagnostics"]["smtp"]["response_summary"], "transport_error")
        self.assertFalse(payload["delivery"]["diagnostics"]["smtp"]["accepted_by_smtp"])
        self.assertEqual(payload["delivery"]["diagnostics"]["attachment"]["content_type"], "application/epub+zip")
        self.assertNotIn("reader-device@kindle.com", json.dumps(payload))
        stored = app_module._get_conversion_job("delivery-smtp-fail")["email_delivery"]
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["diagnostics"]["smtp"]["response_summary"], "transport_error")
        self.assertNotIn("reader-device@kindle.com", json.dumps(stored))

    def test_convert_repair_applies_safe_candidate_and_updates_quality_payload(self) -> None:
        self._register_delivery_job(
            "repair-ready",
            metadata={**self._release_ready_metadata(), "quality_gate_mode": "draft"},
        )
        output_path = app_module._get_conversion_job("repair-ready")["output_path"]
        with open(output_path, "rb") as handle:
            repaired_bytes = handle.read()
        repair_result = DeliveryRepairResult(
            status="applied",
            epub_bytes=repaired_bytes,
            actions=["reencode_progressive_jpeg"],
            quality_selection={
                "status": "accepted",
                "selected_candidate": "auto_repair",
                "rejected_candidate": "",
                "candidates": [{"label": "auto_repair", "premium_score": 9.1}],
            },
        )

        with patch("epub_delivery_repair.repair_epub_for_delivery", return_value=repair_result):
            response = self.client.post("/convert/repair/repair-ready")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["auto_repair"]["status"], "applied")
        self.assertEqual(payload["selected_candidate"], "auto_repair")
        self.assertIn("reencode_progressive_jpeg", payload["actions"])
        stored = app_module._get_conversion_job("repair-ready")
        self.assertEqual(stored["auto_repair"]["status"], "applied")
        self.assertEqual(stored["metadata"]["auto_repair"]["status"], "applied")
        quality_payload = self.client.get("/convert/quality/repair-ready").get_json()
        self.assertEqual(quality_payload["auto_repair"]["status"], "applied")
        self.assertEqual(quality_payload["quality_state"]["auto_repair"]["status"], "applied")

    def test_convert_repair_blocks_unknown_not_ready_and_missing_output(self) -> None:
        unknown_response = self.client.post("/convert/repair/missing-repair")
        self.assertEqual(unknown_response.status_code, 404)

        self._register_delivery_job("repair-running", status="running", output_path="")
        running_response = self.client.post("/convert/repair/repair-running")
        self.assertEqual(running_response.status_code, 409)

        self._register_delivery_job("repair-missing-output", output_path="", output_size_bytes=0)
        missing_output_response = self.client.post("/convert/repair/repair-missing-output")
        self.assertEqual(missing_output_response.status_code, 409)

    def test_convert_repair_keeps_original_when_candidate_is_rejected(self) -> None:
        self._register_delivery_job(
            "repair-rejected",
            metadata={**self._release_ready_metadata(), "quality_gate_mode": "draft"},
        )
        output_path = app_module._get_conversion_job("repair-rejected")["output_path"]
        with open(output_path, "rb") as handle:
            original_bytes = handle.read()
        repair_result = DeliveryRepairResult(
            status="rejected",
            epub_bytes=original_bytes,
            actions=["package_recovery"],
            quality_selection={
                "status": "rejected",
                "selected_candidate": "active",
                "rejected_candidate": "auto_repair",
                "candidates": [{"label": "active", "premium_score": 8.2}],
            },
            reason="quality_selection_rejected_candidate",
        )

        with patch("epub_delivery_repair.repair_epub_for_delivery", return_value=repair_result):
            response = self.client.post("/convert/repair/repair-rejected")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["auto_repair"]["status"], "rejected")
        self.assertEqual(payload["rejected_candidate"], "auto_repair")
        with open(output_path, "rb") as handle:
            self.assertEqual(handle.read(), original_bytes)

    def test_convert_status_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/status/missing-job")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Nie znaleziono zadania konwersji.")
        self.assertEqual(response.get_json()["error_code"], "missing_output")

    def test_convert_status_recovers_orphan_source_as_application_restart(self) -> None:
        job_id = "ffffffffffffffffffffffffffffffff"
        source_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.pdf")
        with open(source_path, "wb") as handle:
            handle.write(b"%PDF-orphan")
        self.cleanup_paths.append(source_path)

        response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["job_id"], job_id)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "application_restart")
        self.assertIn("utracila stan zadania", payload["error"])
        self.assertFalse(payload["download_available"])

    def test_convert_start_returns_structured_upload_error_for_missing_file(self) -> None:
        response = self.client.post("/convert/start", data={}, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "upload_failed")
        self.assertEqual(payload["phase"], "upload")

    def test_convert_start_returns_structured_upload_error_for_unsupported_file(self) -> None:
        response = self.client.post(
            "/convert/start",
            data={"file": (io.BytesIO(b"text"), "notes.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "upload_failed")
        self.assertEqual(payload["phase"], "upload")

    def test_convert_status_surfaces_running_state_and_poll_hint(self) -> None:
        job_id = "running-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertIsNone(payload["conversion"])
        self.assertIsNone(payload["download_url"])
        self.assertEqual(payload["error"], "")
        self.assertEqual(payload["poll_after_ms"], app_module.MAX_CONVERSION_POLL_INTERVAL_MS)
        self.assertGreaterEqual(payload["elapsed_seconds"], 89)

    def test_convert_status_uses_heading_repair_poll_hint_even_before_long_runtime(self) -> None:
        job_id = "repairing-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "repairing_headings",
                "message": "Naprawiam headingi i TOC w EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "repairing_headings")
        self.assertEqual(payload["poll_after_ms"], 2500)
        self.assertGreaterEqual(payload["elapsed_seconds"], 9)

    def test_convert_status_caps_poll_hint_for_very_long_running_job(self) -> None:
        job_id = "long-running-job"
        created_at = (datetime.now(UTC) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["poll_after_ms"], app_module.MAX_CONVERSION_POLL_INTERVAL_MS)
        self.assertGreaterEqual(payload["elapsed_seconds"], 359)

    def test_convert_status_surfaces_failed_state_without_download(self) -> None:
        job_id = "failed-job"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "filename": "broken.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "broken.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "timeout while reading source",
                "error_code": "conversion_failed",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "timeout while reading source")
        self.assertEqual(payload["error_code"], "conversion_failed")
        self.assertIsNone(payload["conversion"])
        self.assertIsNone(payload["download_url"])
        self.assertEqual(payload["poll_after_ms"], 0)

    def test_convert_status_surfaces_quality_gate_failure_conversion_payload(self) -> None:
        job_id = "failed-quality-gate-job"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "failed",
                "message": "Walidacja EPUB zakończyła się niepowodzeniem.",
                "source_type": "docx",
                "filename": "quality-gate.docx",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "quality-gate.epub",
                "metadata": {
                    "source_type": "docx",
                    "profile": "docx_reflow",
                    "validation": "passed_with_warnings",
                    "validation_tool": "epub_validation",
                    "validation_details": {
                        "core_warning_count": 2,
                        "core_warning_messages": [
                            "Manifest integrity ratio is 0.5000, below minimum 1.0.",
                            "Internal hrefs reference missing fragments at ratio 0.0000, below minimum 0.75.",
                        ],
                    },
                    "quality_gate_mode": "strict",
                },
                "output_size_bytes": 0,
                "error": "Core EPUB structure warnings blocked conversion in strict mode. Details: ...",
                "error_code": "conversion_quality_gate_failed",
                "quality_gate_mode": "strict",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "conversion_quality_gate_failed")
        self.assertIsNotNone(payload["conversion"])
        self.assertEqual(payload["conversion"]["quality_gate_mode"], "strict")
        self.assertEqual(payload["conversion"]["validation"], "passed_with_warnings")
        self.assertGreater(len(payload["conversion"].get("validation_details", {}).get("core_warning_messages", [])), 0)
        self.assertIsNone(payload["download_url"])
        self.assertEqual(payload["poll_after_ms"], 0)

    def test_convert_quality_report_available_for_quality_gate_failure(self) -> None:
        job_id = "failed-quality-gate-report"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "failed",
                "message": "Walidacja EPUB zakończyła się niepowodzeniem.",
                "source_type": "pdf",
                "filename": "quality-gate.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "quality-gate.epub",
                "metadata": {
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "validation": "passed_with_warnings",
                    "validation_tool": "epub_validation",
                    "validation_details": {
                        "core_blocker_count": 0,
                        "core_warning_count": 1,
                        "core_warning_messages": ["Manifest integrity ratio is 0.5000, below minimum 1.0."],
                    },
                    "quality_gate_mode": "strict",
                },
                "output_size_bytes": 0,
                "error": "Core EPUB structure warnings blocked conversion in strict mode. Details: Manifest integrity ratio is 0.5000, below minimum 1.0.",
                "error_code": "conversion_quality_gate_failed",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/report/{job_id}.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["success"], True)
        self.assertEqual(payload["job"]["job_id"], job_id)
        self.assertEqual(payload["job"]["status"], "failed")
        self.assertEqual(payload["quality_state"]["quality_gate_mode"], "strict")
        self.assertEqual(payload["quality_state"]["validation"]["status"], "passed_with_warnings")

    def test_convert_jobs_lists_ready_running_and_failed_summaries(self) -> None:
        now = datetime.now(UTC)
        ready_id = "history-ready-job"
        running_id = "history-running-job"
        failed_id = "history-failed-job"
        ready_output_path = os.path.join(app_module.UPLOAD_DIR, f"{ready_id}.epub")
        with open(ready_output_path, "wb") as handle:
            handle.write(b"history-ready-epub")
        self.cleanup_paths.append(ready_output_path)

        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[ready_id] = {
                "job_id": ready_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "ready.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": ready_output_path,
                "download_name": "ready.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
            app_module._CONVERSION_JOBS[running_id] = {
                "job_id": running_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "docx",
                "filename": "running.docx",
                "created_at": (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": "",
                "download_name": "running.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
            app_module._CONVERSION_JOBS[failed_id] = {
                "job_id": failed_id,
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "filename": "failed.pdf",
                "created_at": (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": "",
                "download_name": "failed.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "timeout while reading source",
                "error_code": "conversion_failed",
            }
        self.cleanup_job_ids.extend([ready_id, running_id, failed_id])

        response = self.client.get("/convert/jobs?limit=100")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        jobs = {job["job_id"]: job for job in payload["jobs"]}
        self.assertIn(ready_id, jobs)
        self.assertIn(running_id, jobs)
        self.assertIn(failed_id, jobs)

        ready_job = jobs[ready_id]
        self.assertEqual(ready_job["download_url"], f"/convert/download/{ready_id}")
        self.assertEqual(ready_job["quality_state_url"], f"/convert/quality/{ready_id}")
        self.assertIn("quality_state", ready_job)
        self.assertEqual(ready_job["quality_state"]["job_id"], ready_id)
        self.assertTrue(ready_job["quality_state"]["download_available"])
        self.assertEqual(ready_job["output_size_bytes"], len(b"history-ready-epub"))
        self.assertTrue(ready_job["download_available"])
        self.assertEqual(ready_job["download_state"]["status"], "available")
        self.assertNotIn("error", ready_job)

        running_job = jobs[running_id]
        self.assertEqual(running_job["status"], "running")
        self.assertEqual(running_job["source_type"], "docx")
        self.assertFalse(running_job["download_available"])
        self.assertEqual(running_job["download_state"]["status"], "pending")
        self.assertNotIn("download_url", running_job)
        self.assertNotIn("error", running_job)

        failed_job = jobs[failed_id]
        self.assertEqual(failed_job["status"], "failed")
        self.assertEqual(failed_job["error"], "timeout while reading source")
        self.assertEqual(failed_job["error_code"], "conversion_failed")
        self.assertFalse(failed_job["download_available"])
        self.assertEqual(failed_job["download_state"]["status"], "unavailable")
        self.assertNotIn("download_url", failed_job)

    def test_delete_conversion_job_removes_history_and_local_artifacts(self) -> None:
        job_id = "delete-job"
        input_artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.INPUT,
            filename="delete-me.pdf",
            data=b"pdf-source",
        )
        output_path = self._write_epub_fixture(job_id, "delete me")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="delete-me.pdf",
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        job.update(
            {
                "status": "ready",
                "output_path": output_path,
                "artifacts": {"input": input_artifact},
            }
        )
        app_module._CONVERSION_JOB_STORE.create(job)
        artifact_path = Path(input_artifact["location"])
        self.assertTrue(artifact_path.exists())
        self.assertTrue(Path(output_path).exists())

        response = self.client.delete(f"/convert/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "deleted")
        self.assertIsNone(app_module._get_conversion_job(job_id))
        self.assertFalse(artifact_path.exists())
        self.assertFalse(Path(output_path).exists())

    def test_delete_conversion_job_blocks_active_job(self) -> None:
        job_id = "active-delete-job"
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="active.pdf",
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        job["status"] = "running"
        app_module._CONVERSION_JOB_STORE.create(job)

        response = self.client.delete(f"/convert/jobs/{job_id}")

        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(app_module._get_conversion_job(job_id))

    def test_convert_library_filters_ready_jobs_and_exposes_report_links(self) -> None:
        now = datetime.now(UTC)
        ready_id = "library-ready-job"
        failed_id = "library-failed-job"
        ready_output_path = self._write_epub_fixture(ready_id, "Diagram handbook chapter")

        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[ready_id] = {
                "job_id": ready_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "diagram-book.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": ready_output_path,
                "download_name": "diagram-book.epub",
                "metadata": {
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "title": "Diagram Handbook",
                    "creator": "KindleMaster",
                    "language": "pl",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": 0,
                },
                "artifacts": {
                    "chess_pgn": {
                        "provider": "local",
                        "status": "stored",
                        "kind": "report",
                        "job_id": ready_id,
                        "filename": "chess_games.pgn",
                        "location": os.path.join("output", "artifacts", ready_id, "report", "chess_games.pgn"),
                        "size_bytes": 42,
                        "content_type": "application/x-chess-pgn; charset=utf-8",
                        "download_url": f"/convert/artifact/{ready_id}/chess_pgn",
                        "label": "PGN",
                    },
                    "chess_pgn_html": {
                        "provider": "local",
                        "status": "stored",
                        "kind": "report",
                        "job_id": ready_id,
                        "filename": "chess_games.html",
                        "location": os.path.join("output", "artifacts", ready_id, "report", "chess_games.html"),
                        "size_bytes": 96,
                        "content_type": "text/html; charset=utf-8",
                        "download_url": f"/convert/artifact/{ready_id}/chess_pgn_html",
                        "label": "HTML PGN",
                    },
                },
                "artifact_storage": {"provider": "local", "status": "available", "reason": ""},
                "output_size_bytes": 0,
                "error": "",
            }
            app_module._CONVERSION_JOBS[failed_id] = {
                "job_id": failed_id,
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "filename": "broken-diagram.pdf",
                "created_at": (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": "",
                "download_name": "broken-diagram.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "conversion failed",
                "error_code": "conversion_failed",
            }
        self.cleanup_job_ids.extend([ready_id, failed_id])

        response = self.client.get("/convert/library?status=ready&q=diagram&limit=100")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["index_version"], "kindlemaster-library-v1")
        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["job_id"], ready_id)
        self.assertEqual(item["title"], "Diagram Handbook")
        self.assertEqual(item["download_url"], f"/convert/download/{ready_id}")
        self.assertEqual(item["quality_state_url"], f"/convert/quality/{ready_id}")
        self.assertEqual(item["report_json_url"], f"/convert/report/{ready_id}.json")
        self.assertEqual(item["report_markdown_url"], f"/convert/report/{ready_id}.md")
        self.assertTrue(item["download_available"])
        self.assertEqual(item["download_state"]["status"], "available")
        self.assertEqual(item["artifacts"]["chess_pgn"]["download_url"], f"/convert/artifact/{ready_id}/chess_pgn")
        self.assertEqual(item["artifacts"]["chess_pgn_html"]["download_url"], f"/convert/artifact/{ready_id}/chess_pgn_html")
        self.assertEqual(item["artifact_storage"]["provider"], "local")
        self.assertIn("title", item["matched_fields"])

    def test_history_and_library_hide_internal_ocr_probe_fixture_jobs(self) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        internal_id = "internal-ocr-probe-job"
        visible_id = "visible-user-job"
        visible_output_path = self._write_epub_fixture(visible_id, "Visible user job")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[internal_id] = {
                "job_id": internal_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "ocr_probe.pdf",
                "created_at": now,
                "updated_at": now,
                "source_path": "",
                "output_path": "",
                "download_name": "ocr_probe.epub",
                "metadata": {},
                "runtime": {"workflow": {"kwargs": {"original_filename": "ocr_probe.pdf"}}},
                "output_size_bytes": 1024,
                "error": "",
            }
            app_module._CONVERSION_JOBS[visible_id] = {
                "job_id": visible_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "visible.pdf",
                "created_at": now,
                "updated_at": now,
                "source_path": "",
                "output_path": visible_output_path,
                "download_name": "visible.epub",
                "metadata": {"title": "Visible User Job"},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.extend([internal_id, visible_id])

        jobs_payload = self.client.get("/convert/jobs?limit=100").get_json()
        library_payload = self.client.get("/convert/library?limit=100").get_json()

        self.assertNotIn(internal_id, {job["job_id"] for job in jobs_payload["jobs"]})
        self.assertIn(visible_id, {job["job_id"] for job in jobs_payload["jobs"]})
        self.assertNotIn(internal_id, {item["job_id"] for item in library_payload["items"]})
        self.assertIn(visible_id, {item["job_id"] for item in library_payload["items"]})

    def test_rebuild_local_history_keeps_pdf_layout_preview_separate_from_chess_html(self) -> None:
        job_id = "ready-layout-preview-history"
        job_dir = Path(app_module.app.root_path) / "output" / "artifacts" / job_id
        report_dir = job_dir / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        quality_path = report_dir / f"{job_id}.quality.json"
        chess_html_path = report_dir / "chess_games.html"
        glyph_diagnostics_path = report_dir / "chess_glyph_diagnostics.json"
        preview_path = report_dir / "pdf_layout_preview.html"
        quality_path.write_text(
            json.dumps(
                {
                    "job": {
                        "job_id": job_id,
                        "status": "ready",
                        "filename": "layout.pdf",
                        "download_name": "layout.epub",
                    },
                    "quality_state": {"message": "ready"},
                }
            ),
            encoding="utf-8",
        )
        chess_html_path.write_text("<html><body>PGN/FEN</body></html>", encoding="utf-8")
        glyph_diagnostics_path.write_text('{"diagnostic_count":1}', encoding="utf-8")
        preview_path.write_text('<html><body><section class="pdf-page"></section></body></html>', encoding="utf-8")
        self.cleanup_paths.extend([str(quality_path), str(chess_html_path), str(glyph_diagnostics_path), str(preview_path)])

        rebuilt = app_module._rebuild_job_from_local_artifact_dir(job_dir)

        self.assertIsNotNone(rebuilt)
        self.assertTrue(rebuilt["recovered_from_artifacts"])
        self.assertTrue(rebuilt["restored_from_artifacts"])
        artifacts = rebuilt["artifacts"]
        self.assertEqual(artifacts["chess_pgn_html"]["filename"], "chess_games.html")
        self.assertEqual(artifacts["chess_glyph_diagnostics"]["filename"], "chess_glyph_diagnostics.json")
        self.assertEqual(
            artifacts["chess_glyph_diagnostics"]["download_url"],
            f"/convert/artifact/{job_id}/chess_glyph_diagnostics",
        )
        self.assertEqual(artifacts["chess_glyph_diagnostics"]["label"], "Chess glyph diagnostics")
        self.assertEqual(artifacts["pdf_layout_preview"]["filename"], "pdf_layout_preview.html")
        self.assertEqual(
            artifacts["pdf_layout_preview"]["download_url"],
            f"/convert/artifact/{job_id}/pdf_layout_preview",
        )
        self.assertEqual(artifacts["pdf_layout_preview"]["label"], "Audit View / Source Preview")

    def test_convert_artifact_serves_local_input_inline_with_source_header(self) -> None:
        job_id = "local-input-artifact"
        input_artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.INPUT,
            filename="local-input.pdf",
            data=b"%PDF-1.4\nlocal input",
        )
        self.cleanup_paths.append(str(input_artifact["location"]))
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="local-input.pdf",
            created_at=created_at,
        )
        job.update({"status": "ready", "artifacts": {"input": input_artifact}})
        app_module._CONVERSION_JOB_STORE.create(job)
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/artifact/{job_id}/input")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"%PDF-1.4\nlocal input")
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Source"], "local")
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))

    def test_convert_artifact_serves_pdf_layout_preview_in_app_shell(self) -> None:
        job_id = "local-preview-artifact"
        preview_artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.REPORT,
            filename="pdf_layout_preview.html",
            data=b'<html><body><section class="pdf-page"></section></body></html>',
        )
        preview_artifact["content_type"] = "text/html; charset=utf-8"
        self.cleanup_paths.append(str(preview_artifact["location"]))
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="layout.pdf",
            created_at=created_at,
        )
        job.update({"status": "ready", "artifacts": {"pdf_layout_preview": preview_artifact}})
        app_module._CONVERSION_JOB_STORE.create(job)
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"KindleMaster", response.data)
        self.assertIn(b'data-km-view="pdf-layout-preview"', response.data)
        self.assertIn(b"pdf-page", response.data)
        self.assertIn(b"srcdoc=", response.data)
        self.assertIn("text/html", response.headers.get("Content-Type", ""))
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Source"], "local-shell")

    def test_convert_artifact_rehydrates_pdf_layout_preview_shell_from_local_history(self) -> None:
        job_id = "local-preview-rehydrated"
        job_dir = Path(app_module.app.root_path) / "output" / "artifacts" / job_id
        report_dir = job_dir / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        quality_path = report_dir / f"{job_id}.quality.json"
        preview_path = report_dir / "pdf_layout_preview.html"
        quality_path.write_text(
            json.dumps(
                {
                    "job": {
                        "job_id": job_id,
                        "status": "ready",
                        "filename": "layout-after-restart.pdf",
                        "download_name": "layout-after-restart.epub",
                    },
                    "quality_state": {"message": "ready"},
                }
            ),
            encoding="utf-8",
        )
        preview_path.write_text('<html><body><section class="pdf-page"></section></body></html>', encoding="utf-8")
        self.cleanup_paths.extend([str(quality_path), str(preview_path)])

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-km-view="pdf-layout-preview"', response.data)
        self.assertIn(b"pdf-page", response.data)
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Source"], "local-shell")

    def test_legacy_index_uses_static_asset_cache_busting(self) -> None:
        with patch.dict(os.environ, {"KINDLEMASTER_ENABLE_LEGACY_UI": "1"}):
            response = self.client.get("/legacy")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("css/app-shell.css?v=", html)
        self.assertIn("js/conversion-ui.js?v=", html)
        self.assertIn("js/quality-cockpit.js?v=", html)
        self.assertIn("js/library.js?v=", html)

    def test_convert_artifact_uses_local_input_fallback_before_remote(self) -> None:
        job_id = "fallback-input-artifact"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as temp_dir:
            fallback_root = Path(temp_dir)
            source_path = fallback_root / "fallback-source.pdf"
            source_path.write_bytes(b"%PDF-1.4\nfallback source")
            job = app_module.build_conversion_job_record(
                job_id=job_id,
                source_path="",
                source_type="pdf",
                filename="fallback-source.pdf",
                created_at=created_at,
            )
            job.update(
                {
                    "status": "ready",
                    "artifacts": {
                        "input": {
                            "provider": "supabase",
                            "status": "stored",
                            "kind": "input",
                            "job_id": job_id,
                            "filename": "fallback-source.pdf",
                            "location": "supabase://bucket/missing.pdf",
                            "size_bytes": source_path.stat().st_size,
                            "content_type": "application/pdf",
                            "signed_url": {"available": True, "url": "https://signed.example.invalid/missing.pdf"},
                        }
                    },
                }
            )
            app_module._CONVERSION_JOB_STORE.create(job)
            self.cleanup_job_ids.append(job_id)
            with patch.object(app_module, "_pdf_source_fallback_roots", return_value=[fallback_root]), patch(
                "app.urllib.request.urlopen"
            ) as urlopen_mock:
                response = self.client.get(f"/convert/artifact/{job_id}/input")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"%PDF-1.4\nfallback source")
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Source"], "fallback")
        urlopen_mock.assert_not_called()

    def test_convert_artifact_maps_missing_remote_input_to_source_unavailable(self) -> None:
        job_id = "missing-remote-input"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="missing-remote.pdf",
            created_at=created_at,
        )
        job.update(
            {
                "status": "ready",
                "artifacts": {
                    "input": {
                        "provider": "supabase",
                        "status": "stored",
                        "kind": "input",
                        "job_id": job_id,
                        "filename": "missing-remote.pdf",
                        "location": "supabase://bucket/missing-remote.pdf",
                        "size_bytes": 999999,
                        "content_type": "application/pdf",
                        "signed_url": {"available": True, "url": "https://signed.example.invalid/missing-remote.pdf"},
                    }
                },
            }
        )
        app_module._CONVERSION_JOB_STORE.create(job)
        self.cleanup_job_ids.append(job_id)
        http_error = urllib.error.HTTPError(
            "https://signed.example.invalid/missing-remote.pdf",
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )
        with patch("app.urllib.request.urlopen", side_effect=http_error):
            response = self.client.get(f"/convert/artifact/{job_id}/input")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error_code"], "source_artifact_unavailable")
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Source"], "missing")
        self.assertEqual(response.headers["X-KindleMaster-Remote-Status"], "404")

    def test_convert_artifact_rehydrates_supabase_job_before_proxying_input(self) -> None:
        job_id = "cloud-input-artifact"
        user_id = "33333333-3333-3333-3333-333333333333"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def fake_json(path, *, token, method="GET", payload=None, prefer=""):
            if path.startswith("/rest/v1/conversion_jobs"):
                return 200, [
                    {
                        "job_id": job_id,
                        "user_id": user_id,
                        "filename": "cloud-source.pdf",
                        "source_type": "pdf",
                        "status": "ready",
                        "message": "EPUB gotowy.",
                        "created_at": created_at,
                        "updated_at": created_at,
                        "download_url": "",
                        "quality_state_url": "",
                        "report_json_url": "",
                        "report_markdown_url": "",
                        "download_name": "cloud-source.epub",
                        "metadata": {"source_type": "pdf"},
                        "quality_state_snapshot": {},
                        "auto_repair": {},
                        "email_delivery": {},
                        "runtime": {"provider": "local", "status": "succeeded"},
                        "output_size_bytes": 0,
                        "error": "",
                        "error_code": "",
                    }
                ]
            if path.startswith("/rest/v1/conversion_artifacts"):
                return 200, [
                    {
                        "job_id": job_id,
                        "user_id": user_id,
                        "kind": "input",
                        "filename": "cloud-source.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 123,
                        "storage_bucket": "kindlemaster-artifacts",
                        "storage_path": f"{user_id}/{job_id}/cloud-source.pdf",
                        "signed_url_metadata": {},
                        "retention_days": 90,
                    }
                ]
            if path.startswith("/storage/v1/object/sign/"):
                return 200, {"signedURL": "https://signed.example.invalid/cloud-source.pdf"}
            return 0, {}

        class RemoteResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return b"%PDF-1.4\ncloud source"

        with patch("app._authenticated_request_context", return_value=({"id": user_id}, "unit-token")), patch(
            "app._supabase_request_json",
            side_effect=fake_json,
        ), patch("app._supabase_public_settings", return_value=("https://supabase.example.invalid", "publishable")), patch(
            "app.urllib.request.urlopen",
            return_value=RemoteResponse(),
        ):
            response = self.client.get(f"/convert/artifact/{job_id}/input")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"%PDF-1.4\ncloud source")
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Proxy"], "remote")
        self.assertIsNotNone(app_module._get_conversion_job(job_id))

    def test_supabase_cloud_sync_uploads_pgn_html_artifact_metadata(self) -> None:
        job_id = "cloud-sync-chess-job"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        artifact_dir = Path(app_module.app.root_path) / "output" / "artifacts" / job_id / "report"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        html_path = artifact_dir / "chess_games.html"
        html_path.write_text("<html><body>PGN</body></html>", encoding="utf-8")
        self.cleanup_paths.append(str(html_path))
        artifact = {
            "provider": "local",
            "status": "stored",
            "kind": "report",
            "job_id": job_id,
            "filename": "chess_games.html",
            "location": str(html_path),
            "size_bytes": html_path.stat().st_size,
            "content_type": "text/html; charset=utf-8",
            "download_url": f"/convert/artifact/{job_id}/chess_pgn_html",
            "label": "HTML PGN/FEN",
        }
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "user-report.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "user-report.epub",
                "metadata": {"title": "User Report"},
                "artifacts": {"chess_pgn_html": artifact},
                "output_size_bytes": 2048,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)
        artifact_rows: list[dict] = []

        def fake_json(path, *, token, method="GET", payload=None, prefer=""):
            if path.startswith("/rest/v1/conversion_jobs"):
                return 201, [payload]
            if path.startswith("/storage/v1/object/sign/"):
                return 200, {"signedURL": "https://signed.example.invalid/chess_games.html"}
            if path.startswith("/rest/v1/conversion_artifacts"):
                artifact_rows.append(dict(payload))
                return 201, [payload]
            return 500, {}

        def fake_bytes(path, *, token, method="GET", data=b"", content_type="", extra_headers=None):
            return 200, {"path": path, "size": len(data), "content_type": content_type}

        with patch.object(app_module, "_supabase_request_json", side_effect=fake_json), patch.object(
            app_module,
            "_supabase_request_bytes",
            side_effect=fake_bytes,
        ):
            result = app_module._sync_conversion_job_to_supabase(
                job_id,
                token="unit-token",
                user_id="user-1",
                upload_artifacts=True,
            )

        self.assertEqual(result["status"], "synced")
        self.assertEqual(len(artifact_rows), 1)
        self.assertEqual(artifact_rows[0]["kind"], "chess_pgn_html")
        self.assertEqual(artifact_rows[0]["filename"], "chess_games.html")
        self.assertEqual(artifact_rows[0]["content_type"], "text/html; charset=utf-8")

    def test_ready_missing_output_reports_consistent_download_state_without_availability(self) -> None:
        now = datetime.now(UTC)
        job_id = "contract-ready-missing-output"
        missing_output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "missing-output.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": missing_output_path,
                "download_name": "missing-output.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        status_payload = self.client.get(f"/convert/status/{job_id}").get_json()
        quality_payload = self.client.get(f"/convert/quality/{job_id}").get_json()
        jobs_payload = self.client.get("/convert/jobs?limit=100").get_json()
        library_payload = self.client.get("/convert/library?status=ready&limit=100").get_json()

        self.assertFalse(status_payload["download_available"])
        self.assertIsNone(status_payload["download_url"])
        self.assertEqual(status_payload["download_state"]["status"], "missing_output")
        self.assertFalse(status_payload["quality_state"]["download_available"])
        self.assertEqual(status_payload["quality_state"]["download_state"]["status"], "missing_output")

        quality_state = quality_payload["quality_state"]
        self.assertFalse(quality_state["download_available"])
        self.assertEqual(quality_state["download_state"]["status"], "missing_output")

        job_summary = {
            item["job_id"]: item
            for item in jobs_payload["jobs"]
        }[job_id]
        self.assertFalse(job_summary["download_available"])
        self.assertEqual(job_summary["download_state"]["status"], "missing_output")
        self.assertNotIn("download_url", job_summary)

        library_item = {
            item["job_id"]: item
            for item in library_payload["items"]
        }[job_id]
        self.assertFalse(library_item["download_available"])
        self.assertEqual(library_item["download_state"]["status"], "missing_output")
        self.assertEqual(library_item["download_url"], "")

    def test_convert_search_uses_local_epub_full_text_excerpt(self) -> None:
        now = datetime.now(UTC)
        job_id = "library-search-job"
        output_path = self._write_epub_fixture(
            job_id,
            "The blockade motif appears in this generated EPUB text.",
        )
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "quiet-title.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": output_path,
                "download_name": "quiet-title.epub",
                "metadata": {
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "title": "Quiet Title",
                    "creator": "KindleMaster",
                    "language": "en",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                },
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get("/convert/search?q=blockade&limit=100")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["job_id"], job_id)
        self.assertTrue(item["searchable_text_available"])
        self.assertIn("blockade motif", item["text_excerpt"])
        self.assertIn("full_text", item["matched_fields"])

    def test_convert_quality_report_exports_json_and_markdown(self) -> None:
        now = datetime.now(UTC)
        job_id = "library-report-job"
        output_path = self._write_epub_fixture(job_id, "Report export excerpt")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "docx",
                "filename": "report-source.docx",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": output_path,
                "download_name": "report-source.epub",
                "metadata": {
                    "source_type": "docx",
                    "profile": "book_reflow",
                    "title": "Report Source",
                    "creator": "KindleMaster",
                    "language": "pl",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                },
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        json_response = self.client.get(f"/convert/report/{job_id}.json")
        markdown_response = self.client.get(f"/convert/report/{job_id}.md")

        self.assertEqual(json_response.status_code, 200)
        json_payload = json_response.get_json()
        self.assertEqual(json_payload["job"]["job_id"], job_id)
        self.assertEqual(json_payload["job"]["report_markdown_url"], f"/convert/report/{job_id}.md")
        self.assertIn("quality_state", json_payload)
        self.assertEqual(markdown_response.status_code, 200)
        markdown = markdown_response.get_data(as_text=True)
        self.assertIn("# KindleMaster quality report: Report Source", markdown)
        self.assertIn("Report export excerpt", markdown)

    def test_convert_jobs_uses_reloaded_store_without_dropping_active_jobs(self) -> None:
        active_id = "history-active-memory-job"
        reloaded_id = "history-reloaded-ready-job"
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "conversion_jobs.json"
            seed_store = app_module.ConversionJobStore(
                {},
                threading.Lock(),
                persistence_path=store_path,
                active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
            )
            seed_store.create(
                {
                    "job_id": reloaded_id,
                    "status": "ready",
                    "message": "EPUB gotowy do pobrania.",
                    "source_type": "pdf",
                    "filename": "reloaded.pdf",
                    "created_at": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
                    "updated_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                    "source_path": "",
                    "output_path": "",
                    "download_name": "reloaded.epub",
                    "metadata": {},
                    "output_size_bytes": 1234,
                    "error": "",
                }
            )
            with app_module._CONVERSION_JOBS_LOCK:
                app_module._CONVERSION_JOBS[active_id] = {
                    "job_id": active_id,
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "source_type": "pdf",
                    "filename": "active.pdf",
                    "created_at": (now - timedelta(seconds=15)).isoformat().replace("+00:00", "Z"),
                    "updated_at": (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "source_path": "",
                    "output_path": "",
                    "download_name": "active.epub",
                    "metadata": {},
                    "output_size_bytes": 0,
                    "error": "",
                }
            self.cleanup_job_ids.extend([active_id, reloaded_id])

            reloaded_store = app_module.ConversionJobStore(
                app_module._CONVERSION_JOBS,
                app_module._CONVERSION_JOBS_LOCK,
                persistence_path=store_path,
                active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
            )
            with patch.object(app_module, "_CONVERSION_JOB_STORE", reloaded_store):
                load_result = reloaded_store.load()
                response = self.client.get("/convert/jobs?limit=100")

        self.assertTrue(load_result["loaded"])
        self.assertEqual(load_result["job_count"], 1)
        self.assertIsNotNone(app_module._get_conversion_job(active_id))
        self.assertEqual(response.status_code, 200)
        jobs = {job["job_id"]: job for job in response.get_json()["jobs"]}
        self.assertIn(active_id, jobs)
        self.assertIn(reloaded_id, jobs)
        self.assertEqual(jobs[active_id]["status"], "running")
        self.assertNotIn("download_url", jobs[active_id])
        self.assertFalse(jobs[reloaded_id]["download_available"])
        self.assertEqual(jobs[reloaded_id]["download_state"]["status"], "missing_output")
        self.assertNotIn("download_url", jobs[reloaded_id])

    def test_cleanup_preserves_recovered_artifact_history_jobs(self) -> None:
        now = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
        old = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        app_module._CONVERSION_JOB_STORE.create(
            {
                "job_id": "recovered-history",
                "status": "ready",
                "message": "Odtworzono wpis Biblioteki z lokalnych artefaktow.",
                "source_type": "pdf",
                "filename": "old-report.pdf",
                "created_at": old,
                "updated_at": old,
                "source_path": "",
                "output_path": "",
                "download_name": "old-report.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "recovered_from_artifacts": True,
            }
        )

        result = app_module._cleanup_expired_conversion_jobs(now=now, force=True)

        self.assertEqual(result["removed_jobs"], 0)
        self.assertIsNotNone(app_module._get_conversion_job("recovered-history"))

    def test_attach_output_size_metadata_warns_for_oversized_epub(self) -> None:
        metadata = {"warnings": 0, "warning_list": []}
        enriched = app_module._attach_output_size_metadata(
            metadata,
            app_module.OVERSIZED_EPUB_WARNING_BYTES + 1,
        )
        self.assertIn("output_size_bytes", enriched)
        self.assertGreaterEqual(enriched["warnings"], 1)
        self.assertTrue(any("EPUB ma" in message for message in enriched["warning_list"]))

    def test_convert_start_accepts_docx_and_queues_async_job(self) -> None:
        with patch("app._spawn_conversion_job") as spawn_mock:
            response = self.client.post(
                "/convert/start",
                data={
                    "file": (io.BytesIO(b"docx-bytes"), "sample.docx"),
                    "profile": "auto-premium",
                    "ocr": "false",
                    "language": "pl",
                    "heading_repair": "false",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        job_id = payload["job_id"]
        self.cleanup_job_ids.append(job_id)
        with app_module._CONVERSION_JOBS_LOCK:
            job = dict(app_module._CONVERSION_JOBS[job_id])
        self.cleanup_paths.append(job["source_path"])

        self.assertEqual(payload["source_type"], "docx")
        self.assertEqual(payload["poll_after_ms"], app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS)
        self.assertEqual(job["source_type"], "docx")
        self.assertEqual(job["download_name"], "sample.epub")
        spawn_mock.assert_called_once()
        self.assertEqual(spawn_mock.call_args.kwargs["source_type"], "docx")
        self.assertEqual(spawn_mock.call_args.kwargs["original_filename"], "sample.docx")

    def test_convert_start_rejects_when_active_queue_is_full(self) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        active_ids = ["active-queue-1", "active-queue-2"]
        with app_module._CONVERSION_JOBS_LOCK:
            for job_id in active_ids:
                app_module._CONVERSION_JOBS[job_id] = {
                    "job_id": job_id,
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "source_type": "pdf",
                    "filename": f"{job_id}.pdf",
                    "created_at": now,
                    "updated_at": now,
                    "source_path": "",
                    "output_path": "",
                    "download_name": f"{job_id}.epub",
                    "metadata": {},
                    "output_size_bytes": 0,
                    "error": "",
                }
        self.cleanup_job_ids.extend(active_ids)

        with patch("app._spawn_conversion_job") as spawn_mock:
            response = self.client.post(
                "/convert/start",
                data={"file": (io.BytesIO(b"%PDF-1.4"), "queued.pdf")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 429)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "queue_failed")
        self.assertEqual(payload["phase"], "queue")
        self.assertTrue(payload["retryable"])
        spawn_mock.assert_not_called()

    def test_convert_retry_uses_temp_input_when_artifact_file_is_missing(self) -> None:
        job_id = "retry-source-fallback"
        source_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.pdf")
        Path(source_path).write_bytes(b"%PDF-1.4\nretry-source")
        self.cleanup_paths.append(source_path)
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "failed",
                "message": "Konwersja przerwana przez restart aplikacji.",
                "source_type": "pdf",
                "filename": "retry-source.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "retry-source.epub",
                "metadata": {},
                "runtime": {"replay": {"command": {"kwargs": {"profile": "auto-premium", "language": "pl"}}}},
                "artifacts": {
                    "input": {
                        "provider": "local",
                        "status": "stored",
                        "kind": "input",
                        "job_id": job_id,
                        "filename": "retry-source.pdf",
                        "location": f"output/artifacts/{job_id}/input/missing.pdf",
                    }
                },
                "output_size_bytes": 0,
                "error": "restart",
                "error_code": "application_restart",
            }
        self.cleanup_job_ids.append(job_id)

        with patch("app._spawn_conversion_job") as spawn_mock:
            response = self.client.post(f"/convert/retry/{job_id}", json={})

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        retry_job_id = payload["job_id"]
        self.cleanup_job_ids.append(retry_job_id)
        with app_module._CONVERSION_JOBS_LOCK:
            retry_job = dict(app_module._CONVERSION_JOBS[retry_job_id])
        self.cleanup_paths.append(retry_job["source_path"])

        self.assertEqual(retry_job["retry_of"], job_id)
        self.assertEqual(Path(retry_job["source_path"]).read_bytes(), b"%PDF-1.4\nretry-source")
        self.assertEqual(payload["artifacts"]["input"]["filename"], "retry-source.pdf")
        spawn_mock.assert_called_once()

    def test_stale_active_job_is_marked_timed_out_by_status_route(self) -> None:
        job_id = "stale-running-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=app_module.MAX_CONVERSION_JOB_RUNTIME_SECONDS + 60)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "stale.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "stale.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "timed_out")
        self.assertEqual(payload["error_code"], "conversion_timeout")
        self.assertEqual(payload["poll_after_ms"], 0)
        self.assertEqual(payload["progress"]["health"], "timed_out")
        self.assertEqual(payload["progress"]["stage_id"], "timed_out")
        self.assertEqual(payload["quality_state"]["release_verdict"], "failed")
        self.assertEqual(payload["quality_state"]["quality_blockers"][0]["code"], "conversion_timeout")

    def test_stale_job_is_not_timed_out_when_local_runtime_worker_is_still_running(self) -> None:
        job_id = "stale-but-live-worker"
        created_at = (
            datetime.now(UTC) - timedelta(seconds=app_module.MAX_CONVERSION_JOB_STALE_SECONDS + 60)
        ).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "repairing_headings",
                "message": "Naprawiam headingi i TOC w EPUB...",
                "source_type": "pdf",
                "filename": "long-magazine.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "long-magazine.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        with patch.object(
            app_module.RUNTIME_JOB_ADAPTER,
            "get",
            return_value=SimpleNamespace(status=app_module.RuntimeJobStatus.RUNNING),
        ):
            response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "repairing_headings")
        self.assertNotEqual(payload.get("error_code"), "conversion_timeout")
        self.assertGreater(payload["poll_after_ms"], 0)
        self.assertEqual(payload["progress"]["health"], "stalled")
        self.assertEqual(payload["progress"]["stage_id"], "repairing_toc")

    def test_progress_payload_reports_live_heartbeat_for_active_job(self) -> None:
        job_id = "heartbeat-job"
        created_at = (
            datetime.now(UTC) - timedelta(seconds=app_module.CONVERSION_PROGRESS_LONG_RUNNING_SECONDS + 30)
        ).isoformat().replace("+00:00", "Z")
        heartbeat_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Uruchamiam audyt premium EPUB...",
                "source_type": "pdf",
                "filename": "long.pdf",
                "created_at": created_at,
                "updated_at": heartbeat_at,
                "source_path": "",
                "output_path": "",
                "download_name": "long.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
                "progress": {
                    "stage_id": "premium_audit",
                    "stage_label": "Audyt premium",
                    "percent_estimate": 82,
                    "heartbeat_at": heartbeat_at,
                },
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["progress"]["stage_id"], "premium_audit")
        self.assertEqual(payload["progress"]["stage_label"], "Audyt premium")
        self.assertEqual(payload["progress"]["percent_estimate"], 82)
        self.assertEqual(payload["progress"]["health"], "long_running")
        self.assertLess(payload["progress"]["heartbeat_age_seconds"], app_module.CONVERSION_PROGRESS_STALLED_SECONDS)

    def test_conversion_timeout_writes_structured_recovery_log(self) -> None:
        job_id = "stale-log-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=app_module.MAX_CONVERSION_JOB_RUNTIME_SECONDS + 60)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "stale.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "stale.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        with patch.object(app_module.app.logger, "error") as log_mock:
            self.client.get(f"/convert/status/{job_id}")

        log_payload = json.loads(log_mock.call_args.args[0])
        self.assertEqual(log_payload["event"], "convert.job.failed")
        self.assertEqual(log_payload["job_id"], job_id)
        self.assertEqual(log_payload["phase"], "conversion")
        self.assertEqual(log_payload["status"], "timed_out")
        self.assertEqual(log_payload["error_code"], "conversion_timeout")
        self.assertIn("safe_message", log_payload)

    def test_convert_download_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/download/missing-job")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Nie znaleziono zadania konwersji.")
        self.assertEqual(response.get_json()["error_code"], "missing_output")

    def test_convert_download_returns_409_when_job_is_not_ready(self) -> None:
        job_id = "queued-job"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/download/{job_id}")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "EPUB nie jest jeszcze gotowy do pobrania.")
        self.assertEqual(response.get_json()["error_code"], "queue_failed")
        self.assertTrue(response.get_json()["retryable"])

    def test_convert_download_redirects_to_signed_output_artifact_when_available(self) -> None:
        job_id = "ready-signed-output"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "runtime": {"provider": "local", "status": "succeeded"},
                "artifacts": {
                    "output": {
                        "provider": "r2",
                        "status": "stored",
                        "kind": "output",
                        "job_id": job_id,
                        "filename": "sample.epub",
                        "location": "r2://kindlemaster/ready-signed-output/output/sample.epub",
                        "size_bytes": 1234,
                        "content_type": "application/epub+zip",
                        "retention": {"days": 30, "expires_at": ""},
                        "signed_url": {
                            "available": True,
                            "url": "https://signed.example.invalid/sample.epub",
                            "expires_in_seconds": 900,
                            "reason": "",
                        },
                    }
                },
                "artifact_storage": {"provider": "r2", "status": "available", "reason": ""},
                "output_size_bytes": 1234,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        status_response = self.client.get(f"/convert/status/{job_id}")
        response = self.client.get(f"/convert/download/{job_id}")

        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.get_json()["download_available"])
        self.assertEqual(status_response.get_json()["download_state"]["status"], "available")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://signed.example.invalid/sample.epub")

    def test_convert_download_returns_500_and_marks_job_failed_when_ready_file_is_missing(self) -> None:
        job_id = "ready-missing-file"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        missing_output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": missing_output_path,
                "download_name": "sample.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/download/{job_id}")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "Brak pliku EPUB do pobrania.")
        self.assertEqual(response.get_json()["error_code"], "missing_output")
        with app_module._CONVERSION_JOBS_LOCK:
            job = app_module._CONVERSION_JOBS[job_id]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error"], "Brak pliku EPUB do pobrania.")
            self.assertEqual(job["error_code"], "missing_output")

    def test_missing_download_writes_structured_recovery_log(self) -> None:
        job_id = "ready-missing-log-file"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        missing_output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": missing_output_path,
                "download_name": "sample.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        with patch.object(app_module.app.logger, "error") as log_mock:
            self.client.get(f"/convert/download/{job_id}")

        log_payload = json.loads(log_mock.call_args.args[0])
        self.assertEqual(log_payload["event"], "convert.job.download_missing")
        self.assertEqual(log_payload["job_id"], job_id)
        self.assertEqual(log_payload["phase"], "download")
        self.assertEqual(log_payload["error_code"], "missing_output")


if __name__ == "__main__":
    unittest.main()
