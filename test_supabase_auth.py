import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supabase_auth import (
    SupabaseAuthConfig,
    load_supabase_auth_config,
    mask_email,
    public_auth_config,
    resolve_bearer_token,
    validate_bearer_token,
)


class SupabaseAuthTests(unittest.TestCase):
    def test_public_config_hides_service_role_key(self) -> None:
        env = {
            "KINDLEMASTER_AUTH_PROVIDER": "supabase",
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_public",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-secret",
        }

        payload = public_auth_config(env)

        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["provider"], "supabase")
        self.assertEqual(payload["supabase_url"], "https://project.supabase.co")
        self.assertEqual(payload["publishable_key"], "sb_publishable_public")
        self.assertNotIn("service-role-secret", json.dumps(payload))
        self.assertNotIn("service_role", json.dumps(payload).lower())

    def test_missing_config_reports_required_non_secret_status(self) -> None:
        payload = public_auth_config({"KINDLEMASTER_AUTH_PROVIDER": "supabase"})

        self.assertTrue(payload["enabled"])
        self.assertFalse(payload["configured"])
        self.assertIn("SUPABASE_URL", payload["missing_config"])
        self.assertIn("SUPABASE_PUBLISHABLE_KEY", payload["missing_config"])

    def test_public_config_loads_local_env_file_without_exposing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.local"
            env_path.write_text(
                "\n".join(
                    [
                        "KINDLEMASTER_AUTH_PROVIDER=supabase",
                        "SUPABASE_URL=https://project.supabase.co",
                        "SUPABASE_PUBLISHABLE_KEY=sb_publishable_public",
                        "SUPABASE_SERVICE_ROLE_KEY=service-role-secret",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("supabase_auth.DEFAULT_SUPABASE_ENV_FILES", (str(env_path),)), patch.dict(os.environ, {}, clear=True):
                payload = public_auth_config()

        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["supabase_url"], "https://project.supabase.co")
        self.assertEqual(payload["publishable_key"], "sb_publishable_public")
        self.assertNotIn("service-role-secret", json.dumps(payload))

    def test_explicit_environment_overrides_local_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.local"
            env_path.write_text(
                "\n".join(
                    [
                        "KINDLEMASTER_AUTH_PROVIDER=supabase",
                        "SUPABASE_URL=https://file.supabase.co",
                        "SUPABASE_PUBLISHABLE_KEY=from_file",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("supabase_auth.DEFAULT_SUPABASE_ENV_FILES", (str(env_path),)):
                payload = public_auth_config(
                    {
                        "KINDLEMASTER_AUTH_PROVIDER": "supabase",
                        "SUPABASE_URL": "https://explicit.supabase.co",
                        "SUPABASE_PUBLISHABLE_KEY": "from_explicit_env",
                    }
                )

        self.assertEqual(payload["supabase_url"], "https://explicit.supabase.co")
        self.assertEqual(payload["publishable_key"], "from_explicit_env")

    def test_resolve_bearer_token_accepts_only_bearer_scheme(self) -> None:
        self.assertEqual(resolve_bearer_token("Bearer abc.def"), "abc.def")
        self.assertEqual(resolve_bearer_token("bearer token"), "token")
        self.assertEqual(resolve_bearer_token("Basic abc"), "")
        self.assertEqual(resolve_bearer_token("Bearer   "), "")

    def test_validate_bearer_token_with_valid_user_masks_email(self) -> None:
        calls: list[dict] = []

        def fake_request(url: str, *, method: str = "GET", headers=None, body: bytes | None = None):
            calls.append({"url": url, "method": method, "headers": dict(headers or {}), "body": body})
            return {
                "id": "9d0c32f5-9c1e-4686-9a3b-000000000001",
                "email": "reader@example.com",
            }

        config = SupabaseAuthConfig(
            enabled=True,
            configured=True,
            url="https://project.supabase.co",
            publishable_key="sb_publishable_public",
            service_role_key="service-role-secret",
            require_login=False,
        )

        context = validate_bearer_token("access-token", config=config, http_request=fake_request)

        self.assertTrue(context.authenticated)
        self.assertEqual(context.user_id, "9d0c32f5-9c1e-4686-9a3b-000000000001")
        self.assertEqual(context.email_masked, "r***@example.com")
        self.assertEqual(calls[0]["url"], "https://project.supabase.co/auth/v1/user")
        self.assertEqual(calls[0]["headers"]["apikey"], "sb_publishable_public")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer access-token")
        self.assertNotIn("service-role-secret", json.dumps(calls))

    def test_validate_bearer_token_with_invalid_user_is_sanitized(self) -> None:
        def fake_request(_url: str, *, method: str = "GET", headers=None, body: bytes | None = None):
            raise RuntimeError("401 invalid JWT access-token")

        config = load_supabase_auth_config(
            {
                "KINDLEMASTER_AUTH_PROVIDER": "supabase",
                "SUPABASE_URL": "https://project.supabase.co",
                "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_public",
            }
        )

        context = validate_bearer_token("access-token", config=config, http_request=fake_request)

        self.assertFalse(context.authenticated)
        self.assertEqual(context.error_code, "invalid_auth_token")
        self.assertNotIn("access-token", context.error)

    def test_mask_email_avoids_exposing_full_address(self) -> None:
        self.assertEqual(mask_email("reader@example.com"), "r***@example.com")
        self.assertEqual(mask_email("ab@example.com"), "a***@example.com")
        self.assertEqual(mask_email("invalid"), "")


if __name__ == "__main__":
    unittest.main()
