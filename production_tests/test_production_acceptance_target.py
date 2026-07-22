from __future__ import annotations

import unittest

from production_acceptance_target import UnsafeAcceptanceTarget, validate_staging_target


class ProductionAcceptanceTargetTests(unittest.TestCase):
    def test_accepts_clear_https_staging_hostname(self) -> None:
        target = validate_staging_target("https://kindlemaster-staging.up.railway.app")
        self.assertEqual(target.host, "kindlemaster-staging.up.railway.app")
        self.assertEqual(target.source, "hostname_marker")

    def test_accepts_exact_configured_staging_host(self) -> None:
        target = validate_staging_target(
            "https://gentle-appreciation.up.railway.app",
            env={"KINDLEMASTER_STAGING_ALLOWED_HOSTS": "gentle-appreciation.up.railway.app"},
        )
        self.assertEqual(target.source, "explicit_allowlist")

    def test_accepts_loopback_http_for_local_verification(self) -> None:
        target = validate_staging_target("http://127.0.0.1:5001")
        self.assertEqual(target.source, "loopback")

    def test_rejects_known_production_host(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target("https://kindlemaster-production.up.railway.app")
        self.assertEqual(context.exception.code, "production_target_blocked")

    def test_production_denylist_takes_precedence_over_staging_allowlist(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target(
                "https://api.example.com",
                env={
                    "KINDLEMASTER_PRODUCTION_HOSTS": "api.example.com",
                    "KINDLEMASTER_STAGING_ALLOWED_HOSTS": "api.example.com",
                },
            )
        self.assertEqual(context.exception.code, "production_target_blocked")

    def test_rejects_production_named_host_even_when_not_in_default_list(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target("https://kindlemaster-production-2.example.com")
        self.assertEqual(context.exception.code, "production_named_target_blocked")

    def test_rejects_custom_production_host(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target(
                "https://api.example.com",
                env={"KINDLEMASTER_PRODUCTION_HOSTS": "api.example.com"},
            )
        self.assertEqual(context.exception.code, "production_target_blocked")

    def test_rejects_ambiguous_remote_host_without_allowlist(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target("https://gentle-appreciation.up.railway.app")
        self.assertEqual(context.exception.code, "ambiguous_staging_target")

    def test_rejects_remote_http(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target("http://kindlemaster-staging.example.com")
        self.assertEqual(context.exception.code, "insecure_remote_target")

    def test_rejects_credentials_embedded_in_url(self) -> None:
        with self.assertRaises(UnsafeAcceptanceTarget) as context:
            validate_staging_target("https://user:secret@kindlemaster-staging.example.com")
        self.assertEqual(context.exception.code, "credentialed_target_url")


if __name__ == "__main__":
    unittest.main()
