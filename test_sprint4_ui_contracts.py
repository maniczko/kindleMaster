from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from app import app


REPO_ROOT = Path(__file__).resolve().parent


class Sprint4UiContractsTests(unittest.TestCase):
    def test_node_workspace_exposes_sprint4_scripts_without_replacing_python_api(self) -> None:
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["scripts"]["dev:ui"], "vite --host 127.0.0.1 --port 5173")
        self.assertEqual(package["scripts"]["build:ui"], "vite build")
        self.assertEqual(package["scripts"]["test:ui"], "vitest run --config vitest.config.js --dir frontend/src")
        self.assertEqual(package["scripts"]["test:e2e"], "python kindlemaster.py test --suite runtime")
        self.assertIn("@supabase/supabase-js", package["dependencies"])
        self.assertIn("react", package["dependencies"])
        self.assertIn("vite", package["devDependencies"])

    def test_react_shell_declares_shadcn_style_operational_surfaces(self) -> None:
        source = "\n".join(
            [
                (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "button.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "card.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "tabs.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "dialog.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "progress.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "badge.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "lib" / "auth.ts").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "lib" / "quality-state.ts").read_text(encoding="utf-8"),
            ]
        )

        for marker in (
            "Konwersja",
            "Biblioteka",
            "Ustawienia",
            "Wyślij na Kindle",
            "Status SMTP",
            "Diagnostyka SMTP",
            "Zdarzenie Sentry",
            "Supabase",
            "quality_state",
            "send_to_kindle_ready",
            "sendable",
            "kindle_ready",
            "premium_ready",
            "Button",
            "Card",
            "Tabs",
            "Dialog",
            "Progress",
            "Badge",
        ):
            self.assertIn(marker, source)

    def test_shadcn_registry_config_is_explicit_about_local_primitives(self) -> None:
        config = json.loads((REPO_ROOT / "components.json").read_text(encoding="utf-8"))
        docs = (REPO_ROOT / "docs" / "shadcn-status.md").read_text(encoding="utf-8")

        self.assertEqual(config["$schema"], "https://ui.shadcn.com/schema.json")
        self.assertEqual(config["aliases"]["ui"], "@/components/ui")
        self.assertEqual(config["aliases"]["utils"], "@/lib/utils")
        self.assertEqual(config["iconLibrary"], "lucide")
        self.assertIn("official shadcn registry config + shadcn-style local primitives", docs)

    def test_flask_serves_sprint4_app_route_and_uses_it_as_root_when_built(self) -> None:
        client = app.test_client()
        react_index = REPO_ROOT / "static" / "react" / "index.html"

        legacy_response = client.get("/")
        legacy_direct_response = client.get("/legacy")
        app_response = client.get("/app")

        if react_index.exists():
            self.assertEqual(legacy_response.status_code, 302)
            self.assertEqual(legacy_response.headers["Location"], "/app")
        else:
            self.assertEqual(legacy_response.status_code, 200)
            self.assertIn("KindleMaster", legacy_response.get_data(as_text=True))
        self.assertEqual(legacy_direct_response.status_code, 200)
        self.assertIn("Lokalny panel EPUB", legacy_direct_response.get_data(as_text=True))
        self.assertEqual(app_response.status_code, 200)
        app_html = app_response.get_data(as_text=True)
        self.assertTrue(
            'id="root"' in app_html or "KindleMaster Sprint 4 UI" in app_html,
            app_html[:300],
        )
        self.assertEqual(client.get("/favicon.ico").status_code, 204)

    def test_premium_react_shell_support_endpoints_have_local_fallbacks(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = str(Path(tmpdir) / "profile.json")
            with patch.dict(
                os.environ,
                {
                    "KINDLEMASTER_USER_PROFILE_PATH": profile_path,
                    "KINDLEMASTER_SUPABASE_AUTH": "0",
                },
            ):
                auth_response = client.get("/auth/config")
                profile_response = client.get("/user/profile")
                delivery_response = client.get("/convert/delivery/config")

                self.assertEqual(auth_response.status_code, 200)
                self.assertEqual(profile_response.status_code, 200)
                self.assertEqual(delivery_response.status_code, 200)
                self.assertFalse(auth_response.get_json()["auth"]["configured"])
                self.assertEqual(profile_response.get_json()["profile"]["conversion"]["default_profile"], "auto-premium")
                self.assertFalse(delivery_response.get_json()["delivery"]["configured"])

                save_response = client.put(
                    "/user/profile",
                    json={
                        "conversion": {"default_profile": "magazine", "default_language": "en"},
                        "email_delivery": {"default_recipient": "reader@kindle.com", "password": "secret"},
                    },
                )

                self.assertEqual(save_response.status_code, 200)
                saved = save_response.get_json()["profile"]
                self.assertEqual(saved["conversion"]["default_profile"], "magazine")
                self.assertEqual(saved["email_delivery"]["default_recipient"], "reader@kindle.com")
                self.assertNotIn("password", Path(profile_path).read_text(encoding="utf-8"))

    def test_smtp_registered_secret_counts_as_configured_without_exposing_secret(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = str(Path(tmpdir) / "profile.json")
            with patch.dict(
                os.environ,
                {
                    "KINDLEMASTER_USER_PROFILE_PATH": profile_path,
                    "KINDLEMASTER_SUPABASE_AUTH": "0",
                    "KINDLEMASTER_SMTP_PASSWORD": "",
                    "KINDLEMASTER_SMTP_API_KEY": "",
                    "SMTP_PASSWORD": "",
                },
            ):
                client.put(
                    "/user/profile",
                    json={
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.example.com",
                            "port": 587,
                            "security": "starttls",
                            "username": "apikey",
                            "from_address": "kindle@example.com",
                            "default_recipient": "reader@kindle.com",
                            "secret_registered": True,
                        },
                    },
                )
                response = client.get("/convert/delivery/config")

        self.assertEqual(response.status_code, 200)
        delivery = response.get_json()["delivery"]
        self.assertTrue(delivery["configured"])
        self.assertTrue(delivery["secret_registered"])
        self.assertFalse(delivery["secret_configured"])
        self.assertFalse(delivery["send_ready"])
        self.assertEqual(delivery["missing_config"], [])

    def test_interrupted_conversion_can_be_retried_from_preserved_input_artifact(self) -> None:
        client = app.test_client()
        previous_job_id = "interrupted-job"
        previous_job = {
            "job_id": previous_job_id,
            "status": "failed",
            "filename": "Fundamenty 1-1.pdf",
            "source_type": "pdf",
            "error_code": "application_restart",
            "runtime": {
                "replay": {
                    "command": {
                        "kwargs": {
                            "profile": "auto-premium",
                            "force_ocr": False,
                            "language": "pl",
                            "heading_repair_enabled": True,
                        }
                    }
                }
            },
            "artifacts": {"input": {"filename": "Fundamenty-1-1.pdf", "location": "output/artifacts/interrupted/input.pdf"}},
        }
        created_jobs: list[dict] = []

        def fake_get(job_id: str):
            return previous_job if job_id == previous_job_id else None

        with patch.object(app_module, "_active_conversion_job_count", return_value=0), patch.object(
            app_module,
            "_get_conversion_job",
            side_effect=fake_get,
        ), patch.object(
            app_module,
            "_read_retry_input_artifact",
            return_value=(b"%PDF-1.4 retry", "Fundamenty 1-1.pdf"),
        ), patch.object(
            app_module,
            "_store_artifact_bytes",
            return_value={"provider": "local", "status": "stored", "kind": "input", "location": "retry/input.pdf"},
        ), patch.object(
            app_module,
            "_submit_runtime_job",
            return_value={"status": "queued", "replay": {"command": {"name": "convert"}}},
        ), patch.object(
            app_module,
            "_artifact_storage_status",
            return_value={"provider": "local", "status": "available"},
        ), patch.object(
            app_module._CONVERSION_JOB_STORE,
            "create",
            side_effect=lambda job: created_jobs.append(job) or job,
        ), patch.object(app_module, "_spawn_conversion_job") as spawn:
            response = client.post(f"/convert/retry/{previous_job_id}")

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["retry_of"], previous_job_id)
        self.assertNotEqual(payload["job_id"], previous_job_id)
        self.assertEqual(payload["filename"], "Fundamenty 1-1.pdf")
        self.assertEqual(created_jobs[0]["retry_of"], previous_job_id)
        self.assertEqual(created_jobs[0]["retry_reason"], "application_restart")
        spawn.assert_called_once()

    def test_auth_config_exposes_only_public_supabase_browser_config(self) -> None:
        client = app.test_client()

        with patch.dict(
            os.environ,
            {
                "KINDLEMASTER_SUPABASE_AUTH": "1",
                "KINDLEMASTER_SUPABASE_URL": "https://example.supabase.co",
                "KINDLEMASTER_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
                "KINDLEMASTER_SUPABASE_ANON_KEY": "legacy_anon_should_not_win",
            },
        ):
            response = client.get("/auth/config")

        self.assertEqual(response.status_code, 200)
        auth = response.get_json()["auth"]
        self.assertTrue(auth["enabled"])
        self.assertTrue(auth["configured"])
        self.assertEqual(auth["provider"], "supabase")
        self.assertEqual(auth["supabase_url"], "https://example.supabase.co")
        self.assertEqual(auth["publishable_key"], "sb_publishable_test")
        self.assertNotIn("service", json.dumps(auth).lower())

    def test_authenticated_profile_is_synced_to_supabase_without_secrets(self) -> None:
        client = app.test_client()
        calls: list[tuple[str, dict | None]] = []

        def fake_supabase_request(path, *, token, method="GET", payload=None, prefer=""):
            calls.append((path, payload))
            self.assertEqual(token, "user-token")
            if path == "/auth/v1/user":
                return 200, {"id": "00000000-0000-0000-0000-000000000001", "email": "reader@example.com"}
            if path.startswith("/rest/v1/user_profiles") and method == "GET":
                return 200, [
                    {
                        "user_id": "00000000-0000-0000-0000-000000000001",
                        "conversion_defaults": {"default_profile": "book", "default_language": "en"},
                        "smtp_defaults": {
                            "enabled": True,
                            "host": "smtp.example.com",
                            "port": 587,
                            "security": "starttls",
                            "username": "apikey",
                            "from_address": "kindle@example.com",
                            "default_recipient": "reader@kindle.com",
                        },
                    }
                ]
            if path.startswith("/rest/v1/user_profiles") and method == "POST":
                self.assertIn("secret_configured", payload["smtp_defaults"])
                self.assertNotIn("password", json.dumps(payload).lower())
                return 201, [
                    {
                        "user_id": payload["user_id"],
                        "conversion_defaults": payload["conversion_defaults"],
                        "smtp_defaults": payload["smtp_defaults"],
                    }
                ]
            return 500, {}

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "KINDLEMASTER_SUPABASE_AUTH": "1",
                "KINDLEMASTER_SUPABASE_URL": "https://example.supabase.co",
                "KINDLEMASTER_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
                "KINDLEMASTER_USER_PROFILE_PATH": str(Path(tmpdir) / "profile.json"),
            },
        ), patch.object(app_module, "_supabase_request_json", side_effect=fake_supabase_request):
            get_response = client.get("/user/profile", headers={"Authorization": "Bearer user-token"})
            put_response = client.put(
                "/user/profile",
                headers={"Authorization": "Bearer user-token"},
                json={
                    "conversion": {"default_profile": "magazine", "default_language": "pl"},
                    "email_delivery": {
                        "enabled": True,
                        "host": "smtp.example.com",
                        "port": 587,
                        "security": "starttls",
                        "username": "apikey",
                        "from_address": "kindle@example.com",
                        "default_recipient": "reader@kindle.com",
                        "password": "must-not-persist",
                    },
                },
            )

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.get_json()["profile_scope"], "cloud")
        self.assertEqual(put_response.status_code, 200)
        saved = put_response.get_json()
        self.assertEqual(saved["profile_scope"], "cloud")
        self.assertEqual(saved["cloud_sync"]["status"], "synced")
        self.assertEqual(saved["profile"]["email_delivery"]["default_recipient"], "reader@kindle.com")
        self.assertTrue(any(path.startswith("/rest/v1/user_profiles") and payload for path, payload in calls))


if __name__ == "__main__":
    unittest.main()
