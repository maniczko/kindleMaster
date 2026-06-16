from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from email_delivery import load_email_delivery_config
from user_profile import load_user_profile, save_user_profile


class UserProfileTests(unittest.TestCase):
    def test_load_profile_returns_safe_defaults_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path}, clear=False):
                profile = load_user_profile()

        self.assertEqual(profile["conversion"]["default_profile"], "auto-premium")
        self.assertEqual(profile["conversion"]["default_language"], "pl")
        self.assertTrue(profile["conversion"]["heading_repair"])
        self.assertFalse(profile["email_delivery"]["enabled"])
        self.assertEqual(profile["email_delivery"]["security"], "starttls")
        self.assertEqual(profile["email_delivery"]["default_recipient"], "")
        self.assertNotIn("password", json.dumps(profile).lower())

    def test_save_profile_persists_non_secret_smtp_settings_and_ignores_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profile.json"
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": str(profile_path)}, clear=False):
                saved = save_user_profile(
                    {
                        "conversion": {
                            "default_profile": "magazine",
                            "default_language": "en",
                            "heading_repair": False,
                        },
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.example.com",
                            "port": "2525",
                            "security": "ssl",
                            "username": "apikey",
                            "password": "must-not-persist",
                            "from_address": "operator@example.com",
                            "default_recipient": "reader@kindle.com",
                            "max_attachment_bytes": "123456",
                        },
                    }
                )
                loaded = load_user_profile()

            raw = profile_path.read_text(encoding="utf-8")

        self.assertEqual(saved["conversion"]["default_profile"], "magazine")
        self.assertEqual(loaded["conversion"]["default_language"], "en")
        self.assertEqual(loaded["email_delivery"]["port"], 2525)
        self.assertEqual(loaded["email_delivery"]["from_address"], "operator@example.com")
        self.assertEqual(loaded["email_delivery"]["default_recipient"], "reader@kindle.com")
        self.assertNotIn("must-not-persist", raw)
        self.assertNotIn("password", raw.lower())

    def test_invalid_default_recipient_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path}, clear=False):
                saved = save_user_profile({"email_delivery": {"default_recipient": "reader@kindle.com,other@example.com"}})

        self.assertEqual(saved["email_delivery"]["default_recipient"], "")

    def test_email_delivery_config_uses_profile_defaults_and_env_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path}, clear=False):
                save_user_profile(
                    {
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.profile.example",
                            "username": "profile-user",
                            "from_address": "profile@example.com",
                        }
                    }
                )
                with patch.dict(os.environ, {"KINDLEMASTER_SMTP_PASSWORD": "secret-from-env"}, clear=False):
                    config = load_email_delivery_config()

        self.assertTrue(config.enabled)
        self.assertTrue(config.configured)
        self.assertEqual(config.host, "smtp.profile.example")
        self.assertEqual(config.username, "profile-user")
        self.assertEqual(config.password, "secret-from-env")
        public_payload = config.to_public_dict()
        self.assertTrue(public_payload["profile_configured"])
        self.assertTrue(public_payload["secret_configured"])
        self.assertNotIn("secret-from-env", json.dumps(public_payload))

    def test_environment_smtp_values_override_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = str(Path(temp_dir) / "profile.json")
            with patch.dict(os.environ, {"KINDLEMASTER_USER_PROFILE_PATH": profile_path}, clear=False):
                save_user_profile(
                    {
                        "email_delivery": {
                            "enabled": True,
                            "host": "smtp.profile.example",
                            "username": "profile-user",
                            "from_address": "profile@example.com",
                        }
                    }
                )
                with patch.dict(
                    os.environ,
                    {
                        "KINDLEMASTER_EMAIL_DELIVERY": "1",
                        "KINDLEMASTER_SMTP_HOST": "smtp.env.example",
                        "KINDLEMASTER_SMTP_USERNAME": "env-user",
                        "KINDLEMASTER_SMTP_PASSWORD": "env-secret",
                        "KINDLEMASTER_SMTP_FROM": "env@example.com",
                    },
                    clear=False,
                ):
                    config = load_email_delivery_config()

        self.assertEqual(config.host, "smtp.env.example")
        self.assertEqual(config.username, "env-user")
        self.assertEqual(config.from_address, "env@example.com")
        self.assertEqual(config.to_public_dict()["config_source"], "env+profile")


if __name__ == "__main__":
    unittest.main()
