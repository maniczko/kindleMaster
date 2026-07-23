from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from conversion_rebuild_bundle import (
    build_conversion_rebuild_bundle,
    encode_conversion_rebuild_chunk_manifest,
    split_conversion_rebuild_bundle,
)


class FakeLibraryClient:
    def __init__(self, *, fail_kind: str = "") -> None:
        self.uploads: list[dict] = []
        self.fail_kind = fail_kind

    def upload_artifact_bytes(self, **kwargs):
        if kwargs["kind"] == self.fail_kind:
            raise RuntimeError(f"upload_failed:{self.fail_kind}")
        self.uploads.append(dict(kwargs))
        return {
            "kind": kwargs["kind"],
            "filename": kwargs["filename"],
            "storage_path": f"owner/job/{kwargs['kind']}/{kwargs['filename']}",
            "size_bytes": len(kwargs["data"]),
        }


class FakeDownloadClient:
    def __init__(self, payload: bytes | dict[str, bytes]) -> None:
        self.payloads = (
            payload
            if isinstance(payload, dict)
            else {"owner/job-restore/chess-rebuild.zip": payload}
        )
        self.download_count = 0

    def download_artifact_bytes(self, *, storage_path: str) -> bytes:
        self.download_count += 1
        if storage_path not in self.payloads:
            raise AssertionError(storage_path)
        return self.payloads[storage_path]


class AppDurableArtifactSyncTests(unittest.TestCase):
    def test_uploads_source_outputs_reports_and_rebuild_bundle_as_distinct_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "job-1"
            (root / "input").mkdir(parents=True)
            (root / "output").mkdir(parents=True)
            (root / "review").mkdir(parents=True)
            (root / "semantic_chess_html").mkdir(parents=True)
            source = root / "input" / "source.pdf"
            output = root / "output" / "book.epub"
            source.write_bytes(b"pdf")
            output.write_bytes(b"epub")
            (root / "review" / "fen_manual_review.html").write_text("review", encoding="utf-8")
            (root / "semantic_chess_html" / "index.html").write_text("reader", encoding="utf-8")
            job = {
                "job_id": "job-1",
                "status": "ready",
                "output_path": str(output),
                "artifacts": {
                    "input": {"filename": source.name, "location": str(source), "content_type": "application/pdf"},
                    "output": {"filename": output.name, "location": str(output), "content_type": "application/epub+zip"},
                },
            }
            client = FakeLibraryClient()
            with (
                patch.object(
                    app_module,
                    "_resolve_local_artifact_path",
                    side_effect=lambda artifact: Path(artifact["location"]) if artifact else None,
                ),
                patch.object(app_module, "_local_artifact_job_dir", return_value=root),
            ):
                uploaded = app_module._upload_durable_job_artifacts(
                    client,
                    user_id="owner",
                    job_id="job-1",
                    job=job,
                    quality_state={},
                )

        kinds = {row["kind"] for row in uploaded}
        self.assertEqual(kinds, {"input", "output", "report_json", "chess_rebuild_bundle"})
        bundle_upload = next(row for row in client.uploads if row["kind"] == "chess_rebuild_bundle")
        self.assertGreater(len(bundle_upload["data"]), 0)

    def test_materializes_cloud_bundle_once_and_rebuilds_reader_artifacts(self) -> None:
        job_id = "job-restore"
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source" / job_id
            (source_root / "review").mkdir(parents=True)
            (source_root / "semantic_chess_html").mkdir(parents=True)
            (source_root / "report").mkdir(parents=True)
            (source_root / "review" / "fen_manual_review.html").write_text("review", encoding="utf-8")
            (source_root / "semantic_chess_html" / "index.html").write_text("reader", encoding="utf-8")
            (source_root / "report" / "chess_games.html").write_text("source", encoding="utf-8")
            (source_root / "report" / "chess_diagrams.json").write_text('{"records": []}', encoding="utf-8")
            bundle, _manifest = build_conversion_rebuild_bundle(source_root)
            cloud_root = Path(temp_dir) / "cloud"
            cloud_root.mkdir()
            client = FakeDownloadClient(bundle)
            job = app_module.build_conversion_job_record(
                job_id=job_id,
                source_path="",
                source_type="pdf",
                filename="source.pdf",
                created_at="2026-07-22T10:00:00Z",
            )
            job.update(
                {
                    "status": "ready",
                    "user_id": "owner",
                    "cloud": True,
                    "artifacts": {
                        "chess_rebuild_bundle": {
                            "provider": "supabase",
                            "storage_path": "owner/job-restore/chess-rebuild.zip",
                        }
                    },
                }
            )
            app_module._CONVERSION_JOB_STORE.create(job)
            try:
                with (
                    patch.object(app_module, "ARTIFACT_ROOT", cloud_root),
                    patch.object(app_module, "_supabase_library_client", return_value=client),
                ):
                    first = app_module._materialize_cloud_rebuild_bundle(job_id, job)
                    second = app_module._materialize_cloud_rebuild_bundle(job_id, first or job)
            finally:
                with app_module._CONVERSION_JOBS_LOCK:
                    app_module._CONVERSION_JOBS.pop(job_id, None)
                app_module._CONVERSION_JOB_STORE.persist()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIn("chess_fen_review", first["artifacts"])
        self.assertIn("chess_pgn_html", first["artifacts"])
        self.assertEqual(client.download_count, 1)

    def test_materializes_durable_source_input_with_cloud_rebuild_bundle(self) -> None:
        job_id = "job-source-restore"
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source" / job_id
            (source_root / "review").mkdir(parents=True)
            (source_root / "semantic_chess_html").mkdir(parents=True)
            (source_root / "review" / "fen_manual_review.html").write_text("review", encoding="utf-8")
            (source_root / "semantic_chess_html" / "index.html").write_text("reader", encoding="utf-8")
            bundle, _manifest = build_conversion_rebuild_bundle(source_root)
            bundle_path = "owner/job-source-restore/chess-rebuild.zip"
            input_path = "owner/job-source-restore/input/source.pdf"
            client = FakeDownloadClient({bundle_path: bundle, input_path: b"durable-pdf"})
            cloud_root = Path(temp_dir) / "cloud"
            cloud_root.mkdir()
            job = app_module.build_conversion_job_record(
                job_id=job_id,
                source_path="",
                source_type="pdf",
                filename="source.pdf",
                created_at="2026-07-22T10:00:00Z",
            )
            job.update(
                {
                    "status": "ready",
                    "user_id": "owner",
                    "cloud": True,
                    "artifacts": {
                        "input": {
                            "provider": "supabase",
                            "filename": "source.pdf",
                            "size_bytes": len(b"durable-pdf"),
                            "storage_path": input_path,
                        },
                        "chess_rebuild_bundle": {
                            "provider": "supabase",
                            "storage_path": bundle_path,
                        },
                    },
                }
            )
            app_module._CONVERSION_JOB_STORE.create(job)
            try:
                with (
                    patch.object(app_module, "ARTIFACT_ROOT", cloud_root),
                    patch.object(app_module, "_supabase_library_client", return_value=client),
                ):
                    restored = app_module._materialize_cloud_rebuild_bundle(job_id, job)
                    restored_again = app_module._materialize_cloud_rebuild_bundle(job_id, restored or job)
            finally:
                with app_module._CONVERSION_JOBS_LOCK:
                    app_module._CONVERSION_JOBS.pop(job_id, None)
                app_module._CONVERSION_JOB_STORE.persist()
            restored_source = (cloud_root / job_id / "input" / "source.pdf").read_bytes()

        self.assertIsNotNone(restored)
        self.assertIsNotNone(restored_again)
        self.assertEqual(restored_source, b"durable-pdf")
        self.assertEqual(client.download_count, 2)

    def test_large_rebuild_bundle_uses_bounded_parts_and_json_commit_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "job-chunked"
            (root / "input").mkdir(parents=True)
            (root / "output").mkdir(parents=True)
            (root / "review").mkdir(parents=True)
            (root / "semantic_chess_html").mkdir(parents=True)
            source = root / "input" / "source.pdf"
            output = root / "output" / "book.epub"
            source.write_bytes(b"pdf")
            output.write_bytes(b"epub")
            (root / "review" / "fen_manual_review.html").write_bytes(bytes(range(256)) * 4)
            (root / "semantic_chess_html" / "index.html").write_text("reader", encoding="utf-8")
            job = {
                "job_id": "job-chunked",
                "status": "ready",
                "output_path": str(output),
                "artifacts": {
                    "input": {"filename": source.name, "location": str(source), "content_type": "application/pdf"},
                    "output": {"filename": output.name, "location": str(output), "content_type": "application/epub+zip"},
                },
            }
            client = FakeLibraryClient()
            with (
                patch.dict("os.environ", {"KINDLEMASTER_SUPABASE_OBJECT_MAX_BYTES": "128"}),
                patch.object(
                    app_module,
                    "_resolve_local_artifact_path",
                    side_effect=lambda artifact: Path(artifact["location"]) if artifact else None,
                ),
                patch.object(app_module, "_local_artifact_job_dir", return_value=root),
            ):
                uploaded = app_module._upload_durable_job_artifacts(
                    client,
                    user_id="owner",
                    job_id="job-chunked",
                    job=job,
                    quality_state={},
                )

        primary = next(row for row in client.uploads if row["kind"] == "chess_rebuild_bundle")
        parts = [row for row in client.uploads if row["kind"].startswith("chess_rebuild_bundle_part_")]
        self.assertEqual(primary["content_type"], "application/json")
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(row["data"]) <= 128 for row in parts))
        self.assertEqual({row["kind"] for row in uploaded}, {row["kind"] for row in client.uploads})

    def test_partial_sync_preserves_successful_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "job-partial"
            root.mkdir()
            source = root / "source.pdf"
            output = root / "book.epub"
            source.write_bytes(b"pdf")
            output.write_bytes(b"epub")
            job = {
                "job_id": "job-partial",
                "status": "ready",
                "output_path": str(output),
                "artifacts": {
                    "input": {"filename": source.name, "location": str(source), "content_type": "application/pdf"},
                    "output": {"filename": output.name, "location": str(output), "content_type": "application/epub+zip"},
                },
            }
            client = FakeLibraryClient(fail_kind="output")
            with (
                patch.object(
                    app_module,
                    "_resolve_local_artifact_path",
                    side_effect=lambda artifact: Path(artifact["location"]) if artifact else None,
                ),
                patch.object(app_module, "_local_artifact_job_dir", return_value=None),
            ):
                with self.assertRaises(app_module.DurableArtifactSyncError) as raised:
                    app_module._upload_durable_job_artifacts(
                        client,
                        user_id="owner",
                        job_id="job-partial",
                        job=job,
                        quality_state={},
                    )

        self.assertIn("input", {row["kind"] for row in raised.exception.uploaded})
        self.assertIn("report_json", {row["kind"] for row in raised.exception.uploaded})
        self.assertEqual(raised.exception.failures[0]["kind"], "output")

    def test_materializes_chunked_cloud_bundle_and_verifies_all_parts(self) -> None:
        job_id = "job-chunk-restore"
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source" / job_id
            (source_root / "review").mkdir(parents=True)
            (source_root / "semantic_chess_html").mkdir(parents=True)
            (source_root / "review" / "fen_manual_review.html").write_text("review", encoding="utf-8")
            (source_root / "semantic_chess_html" / "index.html").write_text("reader", encoding="utf-8")
            bundle, _manifest = build_conversion_rebuild_bundle(source_root)
            parts, chunk_manifest = split_conversion_rebuild_bundle(bundle, chunk_size_bytes=32)
            manifest_path = "owner/job-chunk-restore/chess-rebuild.json"
            payloads = {manifest_path: encode_conversion_rebuild_chunk_manifest(chunk_manifest)}
            artifacts = {
                "chess_rebuild_bundle": {
                    "provider": "supabase",
                    "filename": f"{job_id}.chess-rebuild.json",
                    "content_type": "application/json",
                    "storage_path": manifest_path,
                }
            }
            for row, payload in zip(chunk_manifest["parts"], parts, strict=True):
                storage_path = f"owner/job-chunk-restore/{row['kind']}.part"
                payloads[storage_path] = payload
                artifacts[row["kind"]] = {
                    "provider": "supabase",
                    "storage_path": storage_path,
                }
            cloud_root = Path(temp_dir) / "cloud"
            cloud_root.mkdir()
            client = FakeDownloadClient(payloads)
            job = app_module.build_conversion_job_record(
                job_id=job_id,
                source_path="",
                source_type="pdf",
                filename="source.pdf",
                created_at="2026-07-22T10:00:00Z",
            )
            job.update({"status": "ready", "user_id": "owner", "cloud": True, "artifacts": artifacts})
            app_module._CONVERSION_JOB_STORE.create(job)
            try:
                with (
                    patch.object(app_module, "ARTIFACT_ROOT", cloud_root),
                    patch.object(app_module, "_supabase_library_client", return_value=client),
                ):
                    restored = app_module._materialize_cloud_rebuild_bundle(job_id, job)
            finally:
                with app_module._CONVERSION_JOBS_LOCK:
                    app_module._CONVERSION_JOBS.pop(job_id, None)
                app_module._CONVERSION_JOB_STORE.persist()

        self.assertIsNotNone(restored)
        self.assertIn("chess_fen_review", restored["artifacts"])
        self.assertEqual(client.download_count, len(parts) + 1)


if __name__ == "__main__":
    unittest.main()
