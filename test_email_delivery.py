from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from email_delivery import (
    EmailDeliveryError,
    load_email_delivery_config,
    mask_email_address,
    send_attachment_email,
    send_epub_email,
    validate_single_email_address,
)
from local_env import resolve_app_env_file


class FakeSmtp:
    instances: list["FakeSmtp"] = []
    refused_recipients: dict = {}

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.messages = []
        FakeSmtp.instances.append(self)

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:
        self.messages.append(message)
        return dict(FakeSmtp.refused_recipients)


class EmailDeliveryTests(unittest.TestCase):
    def tearDown(self) -> None:
        FakeSmtp.instances.clear()
        FakeSmtp.refused_recipients = {}

    def test_load_config_reports_missing_non_secret_fields(self) -> None:
        config = load_email_delivery_config(
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            }
        )

        self.assertTrue(config.enabled)
        self.assertFalse(config.configured)
        self.assertEqual(config.missing_config, ["KINDLEMASTER_SMTP_USERNAME", "KINDLEMASTER_SMTP_PASSWORD"])
        self.assertEqual(config.to_public_dict()["provider"], "smtp")
        self.assertTrue(config.to_public_dict()["from_configured"])
        self.assertFalse(config.to_public_dict()["secret_configured"])

    def test_email_validation_requires_single_address(self) -> None:
        self.assertEqual(validate_single_email_address(" Reader+kindle@example.com "), "Reader+kindle@example.com")

        for value in ["", "bad", "a@example.com,b@example.com", "Name <a@example.com>"]:
            with self.subTest(value=value):
                with self.assertRaises(EmailDeliveryError):
                    validate_single_email_address(value)

    def test_mask_email_address_preserves_no_full_recipient(self) -> None:
        masked = mask_email_address("reader-device@kindle.com")

        self.assertEqual(masked, "r***@kindle.com")
        self.assertNotIn("reader-device", masked)

    def test_send_epub_email_uses_starttls_login_and_epub_attachment(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as handle:
            handle.write(b"epub-bytes")
            epub_path = Path(handle.name)
        self.addCleanup(lambda: epub_path.unlink(missing_ok=True))
        config = load_email_delivery_config(
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_PORT": "2525",
                "KINDLEMASTER_SMTP_SECURITY": "starttls",
                "KINDLEMASTER_SMTP_USERNAME": "apikey",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            }
        )

        with patch("email_delivery.smtplib.SMTP", FakeSmtp):
            result = send_epub_email(
                config=config,
                to_address="reader@kindle.com",
                subject="KindleMaster EPUB",
                body="Attached.",
                attachment_path=epub_path,
                attachment_filename="book.epub",
            )

        self.assertEqual(result.status, "sent")
        self.assertEqual(result.masked_recipient, "r***@kindle.com")
        self.assertNotIn("reader@kindle.com", str(result.to_public_dict()))
        self.assertNotIn("secret", str(result.to_public_dict()).lower())
        smtp = FakeSmtp.instances[0]
        self.assertEqual((smtp.host, smtp.port), ("smtp.example.com", 2525))
        self.assertTrue(smtp.started_tls)
        self.assertEqual(smtp.login_args, ("apikey", "secret"))
        message = smtp.messages[0]
        attachments = list(message.iter_attachments())
        self.assertTrue(message["Date"])
        self.assertIn("@example.com>", message["Message-ID"])
        self.assertEqual(message.get_content_type(), "multipart/mixed")
        self.assertTrue(any(part.get_content_type() == "text/plain" for part in message.walk()))
        self.assertEqual(attachments[0].get_content_type(), "application/epub+zip")
        self.assertEqual(attachments[0].get_filename(), "book.epub")
        self.assertEqual(attachments[0].get_content_disposition(), "attachment")
        self.assertEqual(attachments[0].get("Content-Transfer-Encoding"), "base64")
        diagnostics = result.to_public_dict()["diagnostics"]
        self.assertTrue(diagnostics["smtp"]["accepted_by_smtp"])
        self.assertEqual(diagnostics["smtp"]["response_summary"], "all_recipients_accepted")
        self.assertEqual(diagnostics["smtp"]["error"], "")
        self.assertTrue(diagnostics["smtp"]["starttls"])
        self.assertEqual(diagnostics["message"]["from"], "k***@example.com")
        self.assertEqual(diagnostics["message"]["to"], "r***@kindle.com")
        self.assertTrue(diagnostics["message"]["has_date_header"])
        self.assertTrue(diagnostics["message"]["has_message_id_header"])
        self.assertEqual(diagnostics["message"]["attachment_count"], 1)
        self.assertFalse(diagnostics["message"]["raw_mime_logged"])
        self.assertEqual(diagnostics["attachment"]["content_type"], "application/epub+zip")
        self.assertEqual(diagnostics["attachment"]["content_disposition"], "attachment")
        self.assertEqual(diagnostics["attachment"]["size_bytes"], len(b"epub-bytes"))
        self.assertEqual(len(diagnostics["attachment"]["sha256"]), 64)

    def test_send_epub_email_uses_ssl_transport_when_configured(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as handle:
            handle.write(b"epub-bytes")
            epub_path = Path(handle.name)
        self.addCleanup(lambda: epub_path.unlink(missing_ok=True))
        config = load_email_delivery_config(
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_SECURITY": "ssl",
                "KINDLEMASTER_SMTP_USERNAME": "user",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            }
        )

        with patch("email_delivery.smtplib.SMTP_SSL", FakeSmtp):
            send_epub_email(
                config=config,
                to_address="reader@kindle.com",
                subject="KindleMaster EPUB",
                body="Attached.",
                attachment_path=epub_path,
                attachment_filename="book.epub",
            )

        self.assertFalse(FakeSmtp.instances[0].started_tls)

    def test_send_attachment_email_can_send_pdf_for_kindle_delivery(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            handle.write(b"%PDF-1.7 cropped")
            pdf_path = Path(handle.name)
        self.addCleanup(lambda: pdf_path.unlink(missing_ok=True))
        config = load_email_delivery_config(
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_USERNAME": "kindlemaster@example.com",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            }
        )

        with patch("email_delivery.smtplib.SMTP", FakeSmtp):
            result = send_attachment_email(
                config=config,
                to_address="reader@kindle.com",
                subject="KindleMaster PDF",
                body="Attached.",
                attachment_path=pdf_path,
                attachment_filename="cropped.pdf",
                attachment_content_type="application/pdf",
                attachment_label="PDF",
            )

        smtp = FakeSmtp.instances[0]
        attachments = list(smtp.messages[0].iter_attachments())
        self.assertEqual(result.status, "sent")
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        self.assertEqual(attachments[0].get_filename(), "cropped.pdf")
        self.assertEqual(result.to_public_dict()["diagnostics"]["attachment"]["content_type"], "application/pdf")

    def test_send_epub_email_reports_smtp_refusal_with_safe_diagnostics(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as handle:
            handle.write(b"epub-bytes")
            epub_path = Path(handle.name)
        self.addCleanup(lambda: epub_path.unlink(missing_ok=True))
        config = load_email_delivery_config(
            {
                "KINDLEMASTER_EMAIL_DELIVERY": "1",
                "KINDLEMASTER_SMTP_HOST": "smtp.example.com",
                "KINDLEMASTER_SMTP_USERNAME": "kindlemaster@example.com",
                "KINDLEMASTER_SMTP_PASSWORD": "secret",
                "KINDLEMASTER_SMTP_FROM": "kindlemaster@example.com",
            }
        )
        FakeSmtp.refused_recipients = {"reader@kindle.com": (550, b"Rejected reader@kindle.com")}

        with patch("email_delivery.smtplib.SMTP", FakeSmtp):
            with self.assertRaises(EmailDeliveryError) as caught:
                send_epub_email(
                    config=config,
                    to_address="reader@kindle.com",
                    subject="Convert",
                    body="Attached.",
                    attachment_path=epub_path,
                    attachment_filename="book.epub",
                )

        error = caught.exception
        self.assertEqual(error.code, "delivery_failed")
        self.assertFalse(error.diagnostics["smtp"]["accepted_by_smtp"])
        self.assertEqual(error.diagnostics["smtp"]["response_summary"], "recipient_refused")
        self.assertTrue(error.diagnostics["smtp"]["from_matches_smtp_username"])
        self.assertEqual(error.diagnostics["smtp"]["refused_recipient_count"], 1)
        self.assertEqual(error.diagnostics["smtp"]["refused_recipients"][0]["recipient"], "r***@kindle.com")
        self.assertIn("r***@kindle.com", error.diagnostics["smtp"]["refused_recipients"][0]["smtp_message"])
        self.assertNotIn("reader@kindle.com", str(error.diagnostics))
        self.assertNotIn("secret", str(error.diagnostics).lower())

    def test_load_config_reads_appdata_secret_file_when_env_is_not_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profile.json"
            secrets_path = Path(temp_dir) / "secrets.env"
            profile_path.write_text(
                (
                    '{"email_delivery": {'
                    '"enabled": true, '
                    '"host": "smtp.profile.example", '
                    '"username": "profile-user", '
                    '"from_address": "profile@example.com"'
                    "}}"
                ),
                encoding="utf-8",
            )
            secrets_path.write_text('KINDLEMASTER_SMTP_PASSWORD="secret-from-file"\n', encoding="utf-8")

            config = load_email_delivery_config(
                {
                    "KINDLEMASTER_USER_PROFILE_PATH": str(profile_path),
                    "KINDLEMASTER_ENV_FILE": str(secrets_path),
                }
            )

        self.assertTrue(config.configured)
        self.assertEqual(config.password, "secret-from-file")
        self.assertTrue(config.to_public_dict()["secret_configured"])

    def test_app_env_file_resolution_survives_empty_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", side_effect=RuntimeError("no home")):
            path = resolve_app_env_file({})

        self.assertEqual(path, Path.cwd() / ".kindlemaster" / "secrets.env")


if __name__ == "__main__":
    unittest.main()
