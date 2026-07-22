from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact_storage import ArtifactKind, LocalArtifactStorage


class ProductionArtifactAtomicityTests(unittest.TestCase):
    def test_local_artifact_is_published_with_complete_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalArtifactStorage(temp_dir)
            record = storage.put_bytes(
                job_id="job-a",
                kind=ArtifactKind.OUTPUT,
                filename="book.epub",
                data=b"complete-epub",
            )

            target = Path(record.location)
            self.assertEqual(record.status, "stored")
            self.assertEqual(target.read_bytes(), b"complete-epub")
            self.assertEqual(list(target.parent.glob(".book.epub.*.tmp")), [])

    def test_replace_failure_preserves_previous_canonical_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalArtifactStorage(temp_dir)
            first = storage.put_bytes(
                job_id="job-a",
                kind=ArtifactKind.OUTPUT,
                filename="book.epub",
                data=b"old-complete-epub",
            )
            target = Path(first.location)

            with patch("artifact_storage.os.replace", side_effect=OSError("disk replace failed")):
                with self.assertRaisesRegex(OSError, "disk replace failed"):
                    storage.put_bytes(
                        job_id="job-a",
                        kind=ArtifactKind.OUTPUT,
                        filename="book.epub",
                        data=b"new-partial-epub",
                    )

            self.assertEqual(target.read_bytes(), b"old-complete-epub")
            self.assertEqual(list(target.parent.glob(".book.epub.*.tmp")), [])

    def test_report_and_log_artifacts_use_the_same_atomic_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalArtifactStorage(temp_dir)
            report = storage.put_bytes(
                job_id="job-a",
                kind=ArtifactKind.REPORT,
                filename="job-a.quality.json",
                data=b'{"status":"passed"}',
            )
            log = storage.put_bytes(
                job_id="job-a",
                kind=ArtifactKind.LOG,
                filename="job-a.runtime.json",
                data=b'{"status":"ready"}',
            )

            self.assertEqual(Path(report.location).read_bytes(), b'{"status":"passed"}')
            self.assertEqual(Path(log.location).read_bytes(), b'{"status":"ready"}')


if __name__ == "__main__":
    unittest.main()
