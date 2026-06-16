from __future__ import annotations

import json
import unittest
from typing import Any, Mapping

from supabase_auth import SupabaseAuthConfig
from supabase_profile import load_cloud_user_profile, save_cloud_user_profile


class SupabaseProfileTests(unittest.TestCase):
    def _config(self) -> SupabaseAuthConfig:
        return SupabaseAuthConfig(
            enabled=True,
            configured=True,
            url="https://example.supabase.co",
            publishable_key="publishable-key",
        )

    def test_load_cloud_user_profile_maps_supabase_row_to_safe_profile(self) -> None:
        calls: list[dict[str, Any]] = []

        def transport(url: str, *, method: str, headers: Mapping[str, str], body: bytes | None = None) -> Any:
            calls.append({"url": url, "method": method, "headers": dict(headers), "body": body})
            return [
                {
                    "conversion_defaults": {"default_profile": "magazine", "default_language": "en"},
                    "smtp_defaults": {
                        "enabled": True,
                        "host": "smtp.example.com",
                        "username": "kindle@example.com",
                        "default_recipient": "reader@kindle.com",
                        "password": "must-not-load",
                    },
                }
            ]

        profile = load_cloud_user_profile(
            user_id="user-123",
            access_token="user-token",
            config=self._config(),
            transport=transport,
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["conversion"]["default_profile"], "magazine")
        self.assertEqual(profile["email_delivery"]["default_recipient"], "reader@kindle.com")
        self.assertNotIn("password", json.dumps(profile).lower())
        self.assertEqual(calls[0]["method"], "GET")
        self.assertIn("/rest/v1/user_profiles?", calls[0]["url"])
        self.assertEqual(calls[0]["headers"]["apikey"], "publishable-key")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer user-token")

    def test_save_cloud_user_profile_uses_rls_user_token_and_strips_secret_fields(self) -> None:
        calls: list[dict[str, Any]] = []

        def transport(url: str, *, method: str, headers: Mapping[str, str], body: bytes | None = None) -> Any:
            calls.append({"url": url, "method": method, "headers": dict(headers), "body": body})
            payload = json.loads((body or b"{}").decode("utf-8"))
            return [payload]

        saved = save_cloud_user_profile(
            user_id="user-123",
            access_token="user-token",
            profile={
                "conversion": {"default_profile": "book", "default_language": "pl"},
                "email_delivery": {
                    "enabled": True,
                    "host": "smtp.example.com",
                    "username": "kindle@example.com",
                    "from_address": "kindle@example.com",
                    "default_recipient": "reader@kindle.com",
                    "password": "must-not-save",
                },
            },
            config=self._config(),
            transport=transport,
        )

        self.assertEqual(saved["email_delivery"]["default_recipient"], "reader@kindle.com")
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["headers"]["apikey"], "publishable-key")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer user-token")
        self.assertIn("resolution=merge-duplicates", calls[0]["headers"]["Prefer"])
        raw_body = (calls[0]["body"] or b"").decode("utf-8")
        self.assertIn('"user_id": "user-123"', raw_body)
        self.assertIn('"smtp_defaults"', raw_body)
        self.assertIn("reader@kindle.com", raw_body)
        self.assertNotIn("must-not-save", raw_body)
        self.assertNotIn("service", raw_body.lower())


if __name__ == "__main__":
    unittest.main()
