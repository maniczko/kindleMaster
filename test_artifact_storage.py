from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact_storage import (
    ArtifactKind,
    ArtifactStorageConfig,
    LocalArtifactStorage,
    RetentionPolicy,
    R2ArtifactStorage,
    build_artifact_storage,
)


class ArtifactStorageTests(unittest.TestCase):
    def test_local_fallback_writes_artifact_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = build_artifact_storage({}, local_root=Path(temp_dir))
            result = storage.put_bytes(
                job_id="job-1",
                kind=ArtifactKind.OUTPUT,
                filename="book.epub",
                data=b"epub",
            )

            stored_path = Path(result.location)
            stored_bytes = stored_path.read_bytes()

        self.assertEqual(result.provider, "local")
        self.assertEqual(result.kind, "output")
        self.assertEqual(result.size_bytes, 4)
        self.assertEqual(result.content_type, "application/epub+zip")
        self.assertEqual(result.retention["days"], 30)
        self.assertEqual(result.signed_url["available"], False)
        self.assertEqual(result.signed_url["reason"], "local_storage")
        self.assertTrue(stored_path.name.endswith("book.epub"))
        self.assertEqual(stored_bytes, b"epub")

    def test_unconfigured_r2_uses_local_storage(self) -> None:
        storage = build_artifact_storage(
            {
                "KINDLEMASTER_ARTIFACT_STORAGE": "r2",
                "R2_BUCKET": "",
                "R2_ENDPOINT_URL": "https://example.invalid",
            },
            local_root=Path("artifacts-test"),
        )

        self.assertIsInstance(storage, LocalArtifactStorage)
        self.assertEqual(storage.availability()["provider"], "local")
        self.assertEqual(storage.availability()["status"], "available")

    def test_configured_r2_reports_unavailable_without_boto3(self) -> None:
        config = ArtifactStorageConfig.from_env(
            {
                "KINDLEMASTER_ARTIFACT_STORAGE": "r2",
                "R2_BUCKET": "kindlemaster",
                "R2_ENDPOINT_URL": "https://r2.example.invalid",
                "R2_ACCESS_KEY_ID": "test-key",
                "R2_SECRET_ACCESS_KEY": "test-secret",
            }
        )
        with patch("artifact_storage.importlib.import_module", side_effect=ImportError("missing")):
            storage = R2ArtifactStorage(config)

        status = storage.availability()

        self.assertEqual(status["provider"], "r2")
        self.assertEqual(status["status"], "unavailable")
        self.assertIn("boto3", status["reason"])

    def test_r2_metadata_shape_does_not_require_network_when_unavailable(self) -> None:
        config = ArtifactStorageConfig.from_env(
            {
                "KINDLEMASTER_ARTIFACT_STORAGE": "r2",
                "R2_BUCKET": "kindlemaster",
                "R2_ENDPOINT_URL": "https://r2.example.invalid",
                "R2_ACCESS_KEY_ID": "test-key",
                "R2_SECRET_ACCESS_KEY": "test-secret",
            }
        )
        with patch("artifact_storage.importlib.import_module", side_effect=ImportError("missing")):
            storage = R2ArtifactStorage(config)

        result = storage.put_bytes(
            job_id="job-2",
            kind=ArtifactKind.REPORT,
            filename="quality.json",
            data=b"{}",
            retention=RetentionPolicy(days=14, expires_at="2026-05-24T00:00:00Z"),
        )

        self.assertEqual(result.provider, "r2")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.kind, "report")
        self.assertEqual(result.location, "r2://kindlemaster/job-2/report/quality.json")
        self.assertEqual(result.retention["days"], 14)
        self.assertEqual(result.retention["expires_at"], "2026-05-24T00:00:00Z")
        self.assertEqual(result.signed_url["available"], False)
        self.assertEqual(result.signed_url["reason"], "storage_unavailable")
        self.assertEqual(result.signed_url["expires_in_seconds"], 900)

    def test_configured_r2_signed_url_contract_uses_object_key(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.put_calls: list[dict[str, object]] = []
                self.presign_calls: list[dict[str, object]] = []

            def put_object(self, **kwargs: object) -> None:
                self.put_calls.append(kwargs)

            def generate_presigned_url(self, operation: str, **kwargs: object) -> str:
                self.presign_calls.append({"operation": operation, **kwargs})
                return "https://signed.example.invalid/job-4/output/book.epub"

        class FakeBoto3:
            def __init__(self) -> None:
                self.client_instance = FakeClient()

            def client(self, *_args: object, **_kwargs: object) -> FakeClient:
                return self.client_instance

        fake_boto3 = FakeBoto3()
        config = ArtifactStorageConfig.from_env(
            {
                "KINDLEMASTER_ARTIFACT_STORAGE": "r2",
                "R2_BUCKET": "kindlemaster",
                "R2_ENDPOINT_URL": "https://r2.example.invalid",
                "R2_ACCESS_KEY_ID": "test-key",
                "R2_SECRET_ACCESS_KEY": "test-secret",
            }
        )
        with patch("artifact_storage.importlib.import_module", return_value=fake_boto3):
            storage = R2ArtifactStorage(config)

        record = storage.put_bytes(
            job_id="job-4",
            kind=ArtifactKind.OUTPUT,
            filename="book.epub",
            data=b"epub",
        )
        signed_url = storage.signed_url(record, expires_in_seconds=120)

        self.assertEqual(record.status, "stored")
        self.assertEqual(fake_boto3.client_instance.put_calls[0]["Key"], "job-4/output/book.epub")
        self.assertEqual(signed_url["available"], True)
        self.assertEqual(signed_url["expires_in_seconds"], 120)
        self.assertEqual(
            fake_boto3.client_instance.presign_calls[0]["Params"],
            {"Bucket": "kindlemaster", "Key": "job-4/output/book.epub"},
        )

    def test_artifact_kinds_have_retention_defaults(self) -> None:
        self.assertEqual(RetentionPolicy.for_kind(ArtifactKind.INPUT).days, 7)
        self.assertEqual(RetentionPolicy.for_kind(ArtifactKind.OUTPUT).days, 30)
        self.assertEqual(RetentionPolicy.for_kind(ArtifactKind.REPORT).days, 90)
        self.assertEqual(RetentionPolicy.for_kind(ArtifactKind.LOG).days, 14)

    def test_environment_detection_accepts_s3_compatible_names(self) -> None:
        previous = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(
                {
                    "S3_BUCKET": "kindlemaster",
                    "S3_ENDPOINT_URL": "https://s3.example.invalid",
                    "AWS_ACCESS_KEY_ID": "test-key",
                    "AWS_SECRET_ACCESS_KEY": "test-secret",
                }
            )
            config = ArtifactStorageConfig.from_env()
        finally:
            os.environ.clear()
            os.environ.update(previous)

        self.assertTrue(config.is_remote_requested)
        self.assertTrue(config.is_remote_configured)
        self.assertEqual(config.bucket, "kindlemaster")
        self.assertEqual(config.endpoint_url, "https://s3.example.invalid")


if __name__ == "__main__":
    unittest.main()
