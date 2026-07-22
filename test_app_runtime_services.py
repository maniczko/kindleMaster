from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app_runtime_services import (
    DELETED_ARTIFACT_MARKER,
    ConversionOutcome,
    ConversionJobStore,
    ConversionRequest,
    ConversionQualityGateError,
    build_conversion_config,
    build_conversion_metadata,
    build_conversion_summary,
    build_local_app_url,
    build_conversion_job_record,
    enrich_conversion_metadata_with_output_size,
    detect_supported_source_type,
    is_allowed_cors_origin,
    pick_epubcheck_error,
    resolve_allowed_cors_origins,
    resolve_debug_mode,
    resolve_server_host,
    resolve_server_port,
    run_document_conversion,
    serve_http_app,
    _safe_delivery_repair_needed,
    _should_skip_heading_repair,
)
from epub_delivery_repair import DeliveryRepairResult


def _minimal_epub_bytes(*, language: str = "en", title: str = "Quality Sample", body: str | None = None) -> bytes:
    chapter_body = body or (
        "<h1>Introduction</h1>"
        "<p>This is a clean reader-facing chapter with enough prose to exercise the runtime premium quality gate.</p>"
        "<p>The conversion should remain downloadable, but the metadata must include a premium score and AI verifier.</p>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:quality-sample</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:language>{language}</dc:language>
    <dc:publisher>KindleMaster</dc:publisher>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{language}">
<head><title>{title}</title></head>
<body><nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Introduction</a></li></ol></nav></body>
</html>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}">
<head><title>Introduction</title></head>
<body>{chapter_body}</body>
</html>""",
        )
    return buffer.getvalue()


def _build_runtime_validation_report(
    *,
    validation_status: str = "passed",
    manifest_item_count: int = 2,
    manifest_targets_missing_count: int = 0,
    manifest_duplicate_id_count: int = 0,
    navigation_document_count: int = 1,
    spine_item_count: int = 1,
    spine_linear_item_count: int = 1,
    spine_non_linear_item_count: int = 0,
    spine_duplicate_targets: int = 0,
    spine_unknown_manifest_references: int = 0,
    links_checked: int = 4,
    external_links_checked: int = 0,
    documents_parsed: int = 1,
    documents_with_duplicate_ids: int = 0,
    package_errors: list[str] | None = None,
    internal_errors: list[str] | None = None,
    external_errors: list[str] | None = None,
    package_warnings: list[str] | None = None,
    internal_warnings: list[str] | None = None,
    external_warnings: list[str] | None = None,
    internal_href_with_fragment_count: int = 0,
    internal_href_without_fragment_count: int = 0,
    internal_href_missing_document_count: int = 0,
    internal_href_missing_fragment_count: int = 0,
) -> dict[str, Any]:
    return {
        "package": {
            "status": "failed" if package_errors else "passed" if validation_status == "passed" else "passed_with_warnings",
            "errors": list(package_errors or []),
            "warnings": list(package_warnings or []),
        },
        "internal_links": {
            "status": "failed" if internal_errors else "passed" if validation_status == "passed" else "passed_with_warnings",
            "errors": list(internal_errors or []),
            "warnings": list(internal_warnings or []),
        },
        "external_links": {
            "status": "failed" if external_errors else "passed" if validation_status == "passed" else "passed_with_warnings",
            "errors": list(external_errors or []),
            "warnings": list(external_warnings or []),
        },
        "metadata": {"title": "Quality Sample", "creator": "KindleMaster QA", "language": "en"},
        "document_stats": {
            "documents_parsed": documents_parsed,
            "documents_with_duplicate_ids": documents_with_duplicate_ids,
            "links_checked": links_checked,
            "external_links_checked": external_links_checked,
            "internal_href_with_fragment_count": internal_href_with_fragment_count,
            "internal_href_without_fragment_count": internal_href_without_fragment_count,
            "internal_href_missing_document_count": internal_href_missing_document_count,
            "internal_href_missing_fragment_count": internal_href_missing_fragment_count,
            "manifest_item_count": manifest_item_count,
            "manifest_targets_missing_count": manifest_targets_missing_count,
            "manifest_duplicate_id_count": manifest_duplicate_id_count,
            "navigation_document_count": navigation_document_count,
            "spine_item_count": spine_item_count,
            "spine_linear_item_count": spine_linear_item_count,
            "spine_non_linear_item_count": spine_non_linear_item_count,
            "spine_duplicate_targets": spine_duplicate_targets,
            "spine_unknown_manifest_references": spine_unknown_manifest_references,
            "non_linear_spine_targets": 0,
            "unreachable_non_linear_spine_targets": 0,
        },
        "summary": {
            "status": validation_status,
            "error_count": len(package_errors or []) + len(internal_errors or []) + len(external_errors or []),
            "warning_count": len(package_warnings or []) + len(internal_warnings or []) + len(external_warnings or []),
            "epubcheck_status": "passed",
        },
    }


class AppRuntimeServicesTests(unittest.TestCase):
    def test_detect_supported_source_type_accepts_pdf_and_docx_case_insensitively(self) -> None:
        self.assertEqual(detect_supported_source_type("sample.pdf"), "pdf")
        self.assertEqual(detect_supported_source_type("SAMPLE.DOCX"), "docx")
        self.assertIsNone(detect_supported_source_type("sample.epub"))
        self.assertIsNone(detect_supported_source_type(""))

    def test_build_conversion_config_propagates_interactive_runtime_budget(self) -> None:
        config = build_conversion_config(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="pl",
                interactive_runtime_budget=True,
            )
        )

        self.assertTrue(config.interactive_runtime_budget)

    def test_build_local_app_url_normalizes_path_and_optional_port(self) -> None:
        self.assertEqual(build_local_app_url(5001), "http://kindlemaster.localhost:5001/")
        self.assertEqual(
            build_local_app_url(path="convert/status/job-1"),
            "http://kindlemaster.localhost/convert/status/job-1",
        )
        self.assertEqual(build_local_app_url(" 5511 ", path=""), "http://kindlemaster.localhost:5511/")

    def test_build_conversion_job_record_creates_consistent_queued_payload(self) -> None:
        payload = build_conversion_job_record(
            job_id="job-123",
            source_path="C:/temp/job-123.pdf",
            source_type="pdf",
            filename="Raport Finalny.PDF",
            created_at="2026-04-22T12:00:00Z",
        )

        self.assertEqual(payload["job_id"], "job-123")
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["source_type"], "pdf")
        self.assertEqual(payload["download_name"], "Raport Finalny.epub")
        self.assertEqual(payload["created_at"], "2026-04-22T12:00:00Z")
        self.assertEqual(payload["updated_at"], "2026-04-22T12:00:00Z")
        self.assertEqual(payload["metadata"], {})
        self.assertEqual(payload["error"], "")
        self.assertEqual(payload["error_code"], "")

    def test_conversion_job_store_persists_and_reloads_terminal_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            jobs: dict[str, dict] = {}
            lock = threading.Lock()
            store = ConversionJobStore(jobs, lock, persistence_path=store_path)
            store.create(
                {
                    "job_id": "job-ready",
                    "status": "ready",
                    "message": "EPUB gotowy do pobrania.",
                    "created_at": "2026-04-25T10:00:00Z",
                    "updated_at": "2026-04-25T10:00:00Z",
                    "metadata": {"profile": "book_reflow"},
                    "error": "",
                }
            )

            reloaded_jobs: dict[str, dict] = {}
            reloaded_store = ConversionJobStore(reloaded_jobs, threading.Lock(), persistence_path=store_path)
            load_result = reloaded_store.load()

        self.assertTrue(load_result["loaded"])
        self.assertEqual(load_result["job_count"], 1)
        self.assertEqual(reloaded_jobs["job-ready"]["status"], "ready")
        self.assertEqual(reloaded_jobs["job-ready"]["metadata"]["profile"], "book_reflow")

    def test_conversion_job_store_reloads_external_persistence_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=store_path)
            store.create(
                {
                    "job_id": "job-original",
                    "status": "ready",
                    "filename": "original.pdf",
                    "created_at": "2026-04-25T10:00:00Z",
                    "updated_at": "2026-04-25T10:00:00Z",
                    "metadata": {},
                }
            )

            external_payload = {
                "version": 1,
                "updated_at": "2026-04-25T10:05:00Z",
                "jobs": {
                    "job-external": {
                        "job_id": "job-external",
                        "status": "ready",
                        "filename": "external.pdf",
                        "created_at": "2026-04-25T10:05:00Z",
                        "updated_at": "2026-04-25T10:05:00Z",
                        "metadata": {},
                    }
                },
            }
            store_path.write_text(json.dumps(external_payload), encoding="utf-8")
            reload_result = store.reload_if_changed()

        self.assertTrue(reload_result["reloaded"])
        self.assertIn("job-original", jobs)
        self.assertIn("job-external", jobs)
        self.assertEqual(jobs["job-external"]["filename"], "external.pdf")

    def test_conversion_job_store_load_skips_invalid_recovered_report_only_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            store_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "2026-05-22T12:00:00Z",
                        "jobs": {
                            "report-only": {
                                "job_id": "report-only",
                                "status": "ready",
                                "filename": "sample.pdf",
                                "created_at": "2026-05-22T12:00:00Z",
                                "updated_at": "2026-05-22T12:00:00Z",
                                "runtime": {},
                                "recovered_from_artifacts": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=store_path)
            result = store.load()

        self.assertTrue(result["loaded"])
        self.assertEqual(result["job_count"], 0)
        self.assertNotIn("report-only", jobs)

    def test_conversion_job_store_preserves_live_in_memory_job_during_external_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=store_path)
            store.create(
                {
                    "job_id": "job-running",
                    "status": "running",
                    "message": "Aktualny proces nadal pracuje.",
                    "created_at": "2026-04-25T10:00:00Z",
                    "updated_at": "2026-04-25T10:06:00Z",
                    "source_path": "C:/temp/job-running.pdf",
                    "metadata": {},
                    "error": "",
                }
            )

            external_payload = {
                "version": 1,
                "updated_at": "2026-04-25T10:05:00Z",
                "jobs": {
                    "job-running": {
                        "job_id": "job-running",
                        "status": "running",
                        "message": "Stary zapis z innego procesu.",
                        "created_at": "2026-04-25T10:00:00Z",
                        "updated_at": "2026-04-25T10:01:00Z",
                        "source_path": "C:/temp/stale.pdf",
                        "metadata": {},
                        "error": "",
                    }
                },
            }
            store_path.write_text(json.dumps(external_payload), encoding="utf-8")
            reload_result = store.reload_if_changed()

        self.assertTrue(reload_result["reloaded"])
        self.assertEqual(jobs["job-running"]["status"], "running")
        self.assertEqual(jobs["job-running"]["message"], "Aktualny proces nadal pracuje.")
        self.assertEqual(jobs["job-running"]["source_path"], "C:/temp/job-running.pdf")

    def test_conversion_job_store_recovers_missing_ready_jobs_from_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            job_dir = root / "job-recovered"
            (job_dir / "input").mkdir(parents=True)
            (job_dir / "output").mkdir()
            (job_dir / "report").mkdir()
            (job_dir / "log").mkdir()
            (job_dir / "input" / "report.pdf").write_bytes(b"%PDF-1.4\n")
            (job_dir / "output" / "report.epub").write_bytes(_minimal_epub_bytes())
            (job_dir / "log" / "job-recovered.runtime.json").write_text(
                json.dumps(
                    {
                        "job_id": "job-recovered",
                        "status": "ready",
                        "runtime": {
                            "created_at": "2026-05-21T10:00:00Z",
                            "updated_at": "2026-05-21T10:05:00Z",
                            "message": "EPUB gotowy do pobrania.",
                            "replay": {
                                "command": {
                                    "name": "convert",
                                    "kwargs": {
                                        "original_filename": "report.pdf",
                                        "source_type": "pdf",
                                        "profile": "auto-premium",
                                        "language": "pl",
                                    },
                                }
                            },
                        },
                        "artifact_storage": {"provider": "local", "status": "available", "reason": ""},
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "report" / "job-recovered.quality.json").write_text(
                json.dumps(
                    {
                        "job": {"filename": "report.pdf", "status": "ready", "output_size_bytes": 123},
                        "quality_state": {
                            "release_verdict": "release_ready",
                            "reading_verdict": "ready",
                            "download_available": False,
                            "metadata_summary": {"title": "Recovered Report"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=Path(temp_dir) / "jobs.json")
            result = store.recover_from_artifacts(root)

        self.assertTrue(result["recovered"])
        self.assertEqual(result["job_count"], 1)
        self.assertEqual(jobs["job-recovered"]["filename"], "report.pdf")
        self.assertEqual(jobs["job-recovered"]["status"], "ready")
        self.assertTrue(jobs["job-recovered"]["output_path"].endswith("report.epub"))
        self.assertIn("output", jobs["job-recovered"]["artifacts"])
        self.assertTrue(jobs["job-recovered"]["quality_state_snapshot"]["download_available"])

    def test_conversion_job_store_does_not_recover_tombstoned_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            job_dir = root / "job-deleted"
            (job_dir / "log").mkdir(parents=True)
            (job_dir / "log" / "job-deleted.runtime.json").write_text(
                json.dumps(
                    {
                        "job_id": "job-deleted",
                        "status": "ready",
                        "runtime": {
                            "replay": {"command": {"kwargs": {"original_filename": "deleted.pdf"}}}
                        },
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / DELETED_ARTIFACT_MARKER).write_text("2026-07-22T10:00:00Z", encoding="utf-8")
            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=Path(temp_dir) / "jobs.json")

            result = store.recover_from_artifacts(root)

        self.assertFalse(result["recovered"])
        self.assertEqual(result["job_count"], 0)
        self.assertEqual(jobs, {})

    def test_conversion_job_store_recovery_does_not_invent_missing_output_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            job_dir = root / "job-missing-output"
            (job_dir / "report").mkdir(parents=True)
            (job_dir / "log").mkdir()
            (job_dir / "log" / "job-missing-output.runtime.json").write_text(
                json.dumps(
                    {
                        "job_id": "job-missing-output",
                        "status": "ready",
                        "runtime": {
                            "created_at": "2026-05-21T10:00:00Z",
                            "updated_at": "2026-05-21T10:05:00Z",
                            "replay": {
                                "command": {
                                    "name": "convert",
                                    "kwargs": {"original_filename": "lost.pdf", "source_type": "pdf"},
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "report" / "job-missing-output.quality.json").write_text(
                json.dumps(
                    {
                        "job": {"filename": "lost.pdf", "status": "ready", "output_size_bytes": 999},
                        "quality_state": {
                            "download_available": True,
                            "artifacts": {
                                "output": {
                                    "provider": "local",
                                    "status": "stored",
                                    "location": str(job_dir / "output" / "lost.epub"),
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=Path(temp_dir) / "jobs.json")
            result = store.recover_from_artifacts(root)

        self.assertTrue(result["recovered"])
        self.assertEqual(jobs["job-missing-output"]["output_path"], "")
        self.assertNotIn("output", jobs["job-missing-output"]["artifacts"])
        self.assertFalse(jobs["job-missing-output"]["quality_state_snapshot"]["download_available"])

    def test_conversion_job_store_recovery_skips_report_only_test_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            job_dir = root / "report-only"
            (job_dir / "report").mkdir(parents=True)
            (job_dir / "report" / "report-only.quality.json").write_text(
                json.dumps({"job": {"filename": "sample.pdf", "status": "ready"}, "quality_state": {}}),
                encoding="utf-8",
            )

            jobs: dict[str, dict] = {}
            store = ConversionJobStore(jobs, threading.Lock(), persistence_path=Path(temp_dir) / "jobs.json")
            result = store.recover_from_artifacts(root)

        self.assertFalse(result["recovered"])
        self.assertEqual(jobs, {})

    def test_conversion_job_store_marks_active_jobs_failed_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            store = ConversionJobStore({}, threading.Lock(), persistence_path=store_path)
            store.create(
                {
                    "job_id": "job-running",
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "created_at": "2026-04-25T10:00:00Z",
                    "updated_at": "2026-04-25T10:00:00Z",
                    "source_path": "C:/temp/job-running.pdf",
                    "metadata": {},
                    "error": "",
                }
            )

            reloaded_jobs: dict[str, dict] = {}
            reloaded_store = ConversionJobStore(reloaded_jobs, threading.Lock(), persistence_path=store_path)
            load_result = reloaded_store.load()

        self.assertEqual(load_result["interrupted_jobs"], 1)
        self.assertEqual(reloaded_jobs["job-running"]["status"], "failed")
        self.assertIn("restart", reloaded_jobs["job-running"]["message"])
        self.assertEqual(reloaded_jobs["job-running"]["error_code"], "application_restart")
        self.assertIn("Uruchom konwersje ponownie", reloaded_jobs["job-running"]["error"])
        self.assertEqual(reloaded_jobs["job-running"]["source_path"], "")

    def test_conversion_job_store_load_handles_invalid_json_and_invalid_shape_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"

            store_path.write_text("{broken", encoding="utf-8")
            invalid_json_store = ConversionJobStore({}, threading.Lock(), persistence_path=store_path)
            invalid_json_result = invalid_json_store.load()

            store_path.write_text('{"jobs": []}', encoding="utf-8")
            malformed_store = ConversionJobStore({}, threading.Lock(), persistence_path=store_path)
            invalid_shape_result = malformed_store.load()

        self.assertFalse(invalid_json_result["loaded"])
        self.assertEqual(invalid_json_result["job_count"], 0)
        self.assertEqual(invalid_json_result["interrupted_jobs"], 0)
        self.assertIn("Expecting", invalid_json_result["error"])

        self.assertFalse(invalid_shape_result["loaded"])
        self.assertEqual(invalid_shape_result["job_count"], 0)
        self.assertEqual(invalid_shape_result["interrupted_jobs"], 0)
        self.assertEqual(invalid_shape_result["error"], "Invalid job store shape.")

    def test_resolve_server_port_and_debug_mode_use_safe_env_defaults(self) -> None:
        self.assertEqual(resolve_server_port({}), 5001)
        self.assertEqual(resolve_server_port({"PORT": "5512"}), 5512)
        self.assertEqual(resolve_server_port({"PORT": "bad"}), 5001)
        self.assertEqual(resolve_server_port({"PORT": "70000"}), 5001)

        self.assertFalse(resolve_debug_mode({}))
        self.assertTrue(resolve_debug_mode({"DEBUG": "true"}))
        self.assertTrue(resolve_debug_mode({"FLASK_DEBUG": "1"}))
        self.assertFalse(resolve_debug_mode({"FLASK_DEBUG": "off"}))

    def test_resolve_server_host_defaults_to_loopback_and_allows_container_bind(self) -> None:
        self.assertEqual(resolve_server_host({}), "127.0.0.1")
        self.assertEqual(resolve_server_host({"KINDLEMASTER_BIND_HOST": "0.0.0.0"}), "0.0.0.0")
        self.assertEqual(resolve_server_host({"HOST": "0.0.0.0"}), "0.0.0.0")
        self.assertEqual(resolve_server_host({"KINDLEMASTER_BIND_HOST": "0.0.0.0", "HOST": "127.0.0.1"}), "0.0.0.0")

    def test_cors_origin_policy_allows_configured_origins_without_wildcards(self) -> None:
        env = {
            "KINDLEMASTER_ALLOWED_ORIGINS": "https://kindlemaster.vercel.app, *, https://preview.vercel.app/",
            "KINDLEMASTER_ALLOW_LOCAL_DEV_CORS": "0",
        }

        self.assertEqual(
            resolve_allowed_cors_origins(env),
            {"https://kindlemaster.vercel.app", "https://preview.vercel.app"},
        )
        self.assertTrue(is_allowed_cors_origin("https://preview.vercel.app/", env))
        self.assertFalse(is_allowed_cors_origin("https://evil.example", env))
        self.assertFalse(is_allowed_cors_origin("*", env))

    def test_cors_origin_policy_keeps_vite_dev_origins_available_by_default(self) -> None:
        self.assertTrue(is_allowed_cors_origin("http://127.0.0.1:5173", {}))
        self.assertTrue(is_allowed_cors_origin("http://kindlemaster.localhost:5174", {}))

    def test_pick_epubcheck_error_prefers_explicit_error_line(self) -> None:
        message = pick_epubcheck_error(
            [
                "Validating using EPUB version 3.3 rules.",
                "WARNING(HTM-045): sample warning",
                "ERROR(RSC-007): broken fragment target",
            ]
        )

        self.assertEqual(message, "ERROR(RSC-007): broken fragment target")
        self.assertEqual(pick_epubcheck_error([]), "Heading/TOC repair failed.")

    def test_should_skip_heading_repair_for_fixed_layout_outputs(self) -> None:
        skip, reason = _should_skip_heading_repair(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="en",
                heading_repair_enabled=True,
                quality_gate_mode="off",
            ),
            {
                "analysis": {"profile": "fixed_layout_fallback"},
                "document_summary": {"layout_mode": "fixed-layout"},
            },
        )

        self.assertTrue(skip)
        self.assertIn("fixed-layout", reason)

    def test_run_document_conversion_keeps_base_epub_when_heading_repair_epubcheck_fails(self) -> None:
        base_epub = b"base-epub"
        convert_impl = Mock(
            return_value={
                "epub_bytes": base_epub,
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Raport koncowy",
                    "author": "Jan Kowalski",
                    "layout_mode": "reflowable",
                    "section_count": 5,
                    "asset_count": 1,
                },
            }
        )
        heading_repair_impl = Mock(
            return_value=SimpleNamespace(
                epub_bytes=b"repaired-epub",
                summary={
                    "release_status": "fail",
                    "toc_entries_before": 2,
                    "toc_entries_after": 6,
                    "headings_removed": 1,
                    "manual_review_count": 4,
                    "epubcheck_status": "failed",
                },
                epubcheck={
                    "status": "failed",
                    "messages": [
                        "Validating using EPUB version 3.3 rules.",
                        "ERROR(RSC-007): missing target",
                    ],
                },
            )
        )
        status_updates: list[tuple[str, str]] = []

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="pl",
                heading_repair_enabled=True,
                quality_gate_mode="off",
                feedback_enabled=False,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
            status_callback=lambda status, message: status_updates.append((status, message)),
        )

        self.assertEqual(outcome.epub_bytes, base_epub)
        self.assertEqual(outcome.download_name, "sample.epub")
        self.assertEqual(outcome.heading_repair_report["status"], "failed")
        self.assertIn("missing target", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "failed")
        self.assertEqual(outcome.metadata["strategy"], "text_reflowable")
        self.assertEqual(heading_repair_impl.call_args.kwargs["publication_profile"], "book_reflow")
        self.assertTrue(heading_repair_impl.call_args.kwargs["already_semantic_cleaned"])
        self.assertEqual(
            status_updates,
            [
                ("running", "Ekstrakcja tekstu z PDF..."),
                ("running", "Składanie artykułów i struktury EPUB..."),
                ("repairing_headings", "Naprawiam headingi i TOC w EPUB..."),
                ("running", "Uruchamiam audyt premium EPUB..."),
            ],
        )

    def test_run_document_conversion_blocks_invalid_epub_from_draft_quality_gate(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"not-an-epub",
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.91,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Broken input",
                    "author": "KindleMaster QA",
                    "layout_mode": "reflowable",
                    "section_count": 0,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with self.assertRaises(ConversionQualityGateError) as error_context:
            run_document_conversion(
                ConversionRequest(
                    source_path="broken.pdf",
                    source_type="pdf",
                    original_filename="broken.pdf",
                    profile="auto-premium",
                    language="en",
                ),
                convert_impl=convert_impl,
                heading_repair_impl=heading_repair_impl,
            )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "draft")
        self.assertIn("Core EPUB", str(error_context.exception))

    def test_run_document_conversion_blocks_core_structure_failures(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Missing Spine/Nav",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                manifest_item_count=2,
                manifest_targets_missing_count=0,
                navigation_document_count=0,
                spine_item_count=0,
            ),
        ):
            with self.assertRaises(ConversionQualityGateError) as error_context:
                run_document_conversion(
                    ConversionRequest(
                        source_path="broken.pdf",
                        source_type="pdf",
                        original_filename="broken.pdf",
                        profile="auto-premium",
                        language="en",
                    ),
                    convert_impl=convert_impl,
                    heading_repair_impl=heading_repair_impl,
                )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "draft")
        self.assertIn("Core EPUB structure blocked conversion", str(error_context.exception))
        self.assertGreaterEqual(len(error_context.exception.validation_report.get("core_blocker_messages", [])), 2)
        self.assertEqual(error_context.exception.validation_report.get("core_blocker_count"), 2)

    def test_run_document_conversion_allows_core_warnings_in_draft_mode(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Core warnings",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                manifest_item_count=3,
                manifest_targets_missing_count=0,
                navigation_document_count=1,
                spine_item_count=2,
                external_warnings=["External resource requires manual review."],
                external_links_checked=1,
            ),
        ):
            outcome = run_document_conversion(
                ConversionRequest(
                    source_path="broken.pdf",
                    source_type="pdf",
                    original_filename="broken.pdf",
                    profile="auto-premium",
                    language="en",
                    quality_gate_mode="draft",
                ),
                convert_impl=convert_impl,
                heading_repair_impl=heading_repair_impl,
            )

        quality_report = outcome.result["quality_report"]
        self.assertEqual(quality_report["validation_status"], "passed_with_warnings")
        self.assertEqual(quality_report["core_warning_count"], 1)
        self.assertEqual(quality_report["core_structure_gate"]["status"], "warning")

    def test_run_document_conversion_blocks_core_warnings_in_strict_mode(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Core warnings",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                manifest_item_count=3,
                manifest_targets_missing_count=0,
                navigation_document_count=1,
                spine_item_count=2,
                external_warnings=["External resource requires manual review."],
                external_links_checked=1,
            ),
        ):
            with self.assertRaises(ConversionQualityGateError) as error_context:
                run_document_conversion(
                    ConversionRequest(
                        source_path="broken.pdf",
                        source_type="pdf",
                        original_filename="broken.pdf",
                        profile="auto-premium",
                        language="en",
                        quality_gate_mode="strict",
                    ),
                    convert_impl=convert_impl,
                    heading_repair_impl=heading_repair_impl,
                )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "strict")
        self.assertIn("Core EPUB structure warnings blocked conversion", str(error_context.exception))
        self.assertEqual(error_context.exception.validation_report.get("core_warning_count"), 1)

    def test_run_document_conversion_blocks_strict_mode_when_href_readability_falls_below_threshold(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.94,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Strict readability",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                links_checked=12,
                internal_href_with_fragment_count=12,
                internal_href_without_fragment_count=0,
                internal_href_missing_document_count=0,
                internal_href_missing_fragment_count=0,
                spine_item_count=2,
                spine_linear_item_count=0,
                spine_non_linear_item_count=2,
            ),
        ):
            with self.assertRaises(ConversionQualityGateError) as error_context:
                run_document_conversion(
                    ConversionRequest(
                        source_path="broken.pdf",
                        source_type="pdf",
                        original_filename="broken.pdf",
                        profile="auto-premium",
                        language="en",
                        quality_gate_mode="strict",
                    ),
                    convert_impl=convert_impl,
                    heading_repair_impl=heading_repair_impl,
                )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "strict")
        self.assertIn(
            "Linear-spine ratio is",
            " ".join(error_context.exception.validation_report.get("core_blocker_messages", [])),
        )
        self.assertEqual(error_context.exception.validation_report.get("core_blocker_count"), 1)

    def test_run_document_conversion_blocks_strict_mode_when_epubcheck_fails_in_summary(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.94,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "EPUBCheck summary fail",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        strict_report = _build_runtime_validation_report(
            links_checked=4,
            internal_href_with_fragment_count=0,
            internal_href_without_fragment_count=0,
            internal_href_missing_document_count=0,
            internal_href_missing_fragment_count=0,
        )
        strict_report["summary"] = dict(strict_report.get("summary") or {})
        strict_report["summary"]["status"] = "failed"
        strict_report["summary"]["epubcheck_status"] = "failed"
        strict_report["summary"]["error_count"] = 2
        strict_report["summary"]["warning_count"] = 0

        with patch("epub_validation.validate_epub_bytes", return_value=strict_report):
            with self.assertRaises(ConversionQualityGateError) as error_context:
                run_document_conversion(
                    ConversionRequest(
                        source_path="broken.pdf",
                        source_type="pdf",
                        original_filename="broken.pdf",
                        profile="auto-premium",
                        language="en",
                        quality_gate_mode="strict",
                    ),
                    convert_impl=convert_impl,
                    heading_repair_impl=heading_repair_impl,
                )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "strict")
        self.assertIn("Core EPUB validation failed", str(error_context.exception))
        self.assertEqual(error_context.exception.validation_report.get("error_count"), 2)

    def test_run_document_conversion_blocks_duplicate_manifest_ids_in_core_gate(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Duplicate manifest id",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                manifest_duplicate_id_count=1,
                manifest_item_count=2,
            ),
        ):
            with self.assertRaises(ConversionQualityGateError) as error_context:
                run_document_conversion(
                    ConversionRequest(
                        source_path="broken.pdf",
                        source_type="pdf",
                        original_filename="broken.pdf",
                        profile="auto-premium",
                        language="en",
                        quality_gate_mode="draft",
                    ),
                    convert_impl=convert_impl,
                    heading_repair_impl=heading_repair_impl,
                )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "draft")
        self.assertIn("Manifest has 1 duplicate id(s).", error_context.exception.validation_report.get("core_blocker_messages", []))

    def test_run_document_conversion_blocks_unknown_spine_manifest_references(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Unknown spine idref",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                spine_unknown_manifest_references=1,
                spine_item_count=1,
                manifest_item_count=1,
            ),
        ):
            with self.assertRaises(ConversionQualityGateError) as error_context:
                run_document_conversion(
                    ConversionRequest(
                        source_path="broken.pdf",
                        source_type="pdf",
                        original_filename="broken.pdf",
                        profile="auto-premium",
                        language="en",
                        quality_gate_mode="draft",
                    ),
                    convert_impl=convert_impl,
                    heading_repair_impl=heading_repair_impl,
                )

        self.assertEqual(error_context.exception.error_code, "conversion_quality_gate_failed")
        self.assertEqual(error_context.exception.mode, "draft")
        self.assertIn(
            "Spine references 1 unknown manifest id(s).",
            error_context.exception.validation_report.get("core_blocker_messages", []),
        )

    def test_run_document_conversion_records_core_readability_metrics(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": _minimal_epub_bytes(),
                "source_type": "pdf",
                "analysis": {
                    "profile": "magazine_reflow",
                    "confidence": 0.85,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Good Structure",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        with patch(
            "epub_validation.validate_epub_bytes",
            return_value=_build_runtime_validation_report(
                manifest_item_count=3,
                manifest_targets_missing_count=0,
                navigation_document_count=1,
                spine_item_count=2,
                spine_linear_item_count=2,
                spine_non_linear_item_count=0,
                links_checked=8,
                documents_parsed=4,
            ),
        ), patch("epub_premium_scoring.score_epub_premium_quality", return_value={"premium_score": 9.2, "status": "passed", "technical_valid": True}), patch(
            "ml_quality_verifier.build_ai_quality_verification",
            return_value={"status": "passed", "score": 9.0, "features_hash": "abc"},
        ):
            outcome = run_document_conversion(
                ConversionRequest(
                    source_path="sample.pdf",
                    source_type="pdf",
                    original_filename="sample.pdf",
                    profile="auto-premium",
                    language="en",
                    quality_gate_mode="draft",
                    feedback_enabled=False,
                ),
                convert_impl=convert_impl,
                heading_repair_impl=heading_repair_impl,
            )

        validation_details = outcome.metadata["validation_details"]
        core_gate = validation_details["core_structure_gate"]
        self.assertEqual(core_gate["status"], "passed")
        self.assertEqual(core_gate["blockers"], [])
        self.assertEqual(validation_details["core_readability"]["manifest_integrity_ratio"], 1.0)
        self.assertEqual(validation_details["core_readability"]["href_error_rate"], 0.0)
        self.assertIn("internal_href_document_coverage", validation_details["core_readability"])

    def test_run_document_conversion_keeps_pre_heading_epub_when_heading_repair_worsens_quality(self) -> None:
        base_epub = _minimal_epub_bytes(title="Runtime Quality Selection")
        repaired_epub = _minimal_epub_bytes(
            title="Runtime Quality Selection",
            body="<h1>Material sponsorowany - R4</h1><p>worse-marker</p>",
        )
        convert_impl = Mock(
            return_value={
                "epub_bytes": base_epub,
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "validation_messages": [],
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Runtime Quality Selection",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock(
            return_value=SimpleNamespace(
                epub_bytes=repaired_epub,
                summary={
                    "release_status": "pass",
                    "toc_entries_before": 1,
                    "toc_entries_after": 1,
                    "headings_removed": 0,
                    "manual_review_count": 0,
                    "epubcheck_status": "passed",
                },
                epubcheck={"status": "passed", "messages": []},
            )
        )

        def scoring(epub_bytes, *, epubcheck=None):
            del epubcheck
            if epub_bytes == repaired_epub:
                return {
                    "status": "failed",
                    "technical_valid": True,
                    "kindle_ready": False,
                    "premium_ready": False,
                    "premium_score": 5.2,
                    "release_verdict": "release_blocked",
                    "issue_counts": {"blocker": 1},
                    "issues": [{"severity": "blocker", "code": "magazine_non_content_chapter"}],
                }
            return {
                "status": "passed_with_warnings",
                "technical_valid": True,
                "kindle_ready": True,
                "premium_ready": False,
                "premium_score": 9.1,
                "release_verdict": "ready_with_review",
                "issue_counts": {"review": 1},
                "issues": [{"severity": "review", "code": "toc_lead_used_as_title"}],
            }

        with patch("epub_quality_selection.score_epub_premium_quality", side_effect=scoring):
            outcome = run_document_conversion(
                ConversionRequest(
                    source_path="sample.pdf",
                    source_type="pdf",
                    original_filename="sample.pdf",
                    profile="auto-premium",
                    language="en",
                    heading_repair_enabled=True,
                    quality_gate_mode="draft",
                    feedback_enabled=False,
                ),
                convert_impl=convert_impl,
                heading_repair_impl=heading_repair_impl,
            )

        self.assertNotEqual(outcome.epub_bytes, repaired_epub)
        self.assertEqual(outcome.heading_repair_report["status"], "rejected")
        self.assertEqual(outcome.result["quality_report"]["quality_selection"]["status"], "rejected")
        self.assertEqual(outcome.metadata["quality_selection"]["selected_stage"], "pre_heading")
        self.assertEqual(outcome.metadata["quality_selection"]["rejected_stage"], "heading_repair")
        self.assertIn("quality_monotonic_regression", outcome.metadata["quality_selection"]["reason_codes"])
        heading_repair_impl.assert_called_once()

    def test_run_document_conversion_applies_runtime_quality_gate_and_ai_verifier(self) -> None:
        base_epub = _minimal_epub_bytes()
        convert_impl = Mock(
            return_value={
                "epub_bytes": base_epub,
                "source_type": "pdf",
                "analysis": {
                    "profile": "magazine_reflow",
                    "confidence": 0.81,
                    "legacy_strategy": "magazine_reflow",
                    "route_decision": {
                        "mode": "shadow",
                        "heuristic_profile": "magazine_reflow",
                        "heuristic_confidence": 0.81,
                        "selected_profile": "magazine_reflow",
                        "override_used": False,
                    },
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "validation_messages": [],
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Quality Sample",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="en",
                heading_repair_enabled=False,
                quality_gate_mode="draft",
                feedback_enabled=False,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        quality_report = outcome.result["quality_report"]
        self.assertIn("premium_scoring", quality_report)
        self.assertIn("ai_quality_verification", quality_report)
        self.assertEqual(quality_report["quality_gate_mode"], "draft")
        self.assertEqual(outcome.metadata["quality_gate_mode"], "draft")
        self.assertIn("premium_score", outcome.metadata["premium_scoring"])
        self.assertIn("features_hash", outcome.metadata["ai_quality_verification"])
        self.assertEqual(outcome.metadata["ai_quality_verification"]["quality_gate_mode"], "draft")
        self.assertFalse(heading_repair_impl.called)

    def test_run_document_conversion_runs_auto_delivery_repair_before_final_metadata(self) -> None:
        base_epub = _minimal_epub_bytes(title="Delivery Repair Base")
        repaired_epub = _minimal_epub_bytes(title="Delivery Repair Fixed")
        convert_impl = Mock(
            return_value={
                "epub_bytes": base_epub,
                "source_type": "pdf",
                "analysis": {"profile": "book_reflow", "confidence": 0.88, "legacy_strategy": "text_reflowable"},
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "validation_messages": [],
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Delivery Repair Base",
                    "author": "KindleMaster QA",
                    "language": "en",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 1,
                },
            }
        )
        repair_result = DeliveryRepairResult(
            status="applied",
            epub_bytes=repaired_epub,
            actions=["reencode_progressive_jpeg"],
            quality_selection={
                "status": "accepted",
                "selected_candidate": "auto_repair",
                "rejected_candidate": "",
                "candidates": [{"label": "auto_repair", "premium_score": 9.4}],
            },
        )
        passing_validation = _build_runtime_validation_report()

        with patch("epub_delivery_repair.has_progressive_jpeg", return_value=True):
            with patch("epub_delivery_repair.repair_epub_for_delivery", return_value=repair_result) as repair_mock:
                with patch("epub_validation.validate_epub_bytes", return_value=passing_validation):
                    outcome = run_document_conversion(
                        ConversionRequest(
                            source_path="sample.pdf",
                            source_type="pdf",
                            original_filename="sample.pdf",
                            profile="auto-premium",
                            language="en",
                            heading_repair_enabled=False,
                            quality_gate_mode="draft",
                            feedback_enabled=False,
                        ),
                        convert_impl=convert_impl,
                        heading_repair_impl=Mock(),
                    )

        self.assertEqual(outcome.epub_bytes, repaired_epub)
        self.assertEqual(outcome.metadata["auto_repair"]["status"], "applied")
        self.assertIn("reencode_progressive_jpeg", outcome.metadata["auto_repair"]["actions"])
        self.assertEqual(outcome.result["quality_report"]["auto_repair"]["selected_candidate"], "auto_repair")
        repair_mock.assert_called_once()

    def test_safe_delivery_repair_runs_for_release_not_ready_blocker(self) -> None:
        self.assertTrue(
            _safe_delivery_repair_needed(
                quality_state={
                    "send_to_kindle_blockers": [
                        {
                            "code": "kindle_delivery_release_not_ready",
                            "message": "Bramka jakosci ma status Nie publikuj.",
                        }
                    ]
                },
                quality_report={"validation_status": "passed"},
                epub_bytes=_minimal_epub_bytes(),
            )
        )

    def test_run_document_conversion_reports_progress_stages(self) -> None:
        base_epub = _minimal_epub_bytes()
        convert_impl = Mock(
            return_value={
                "epub_bytes": base_epub,
                "source_type": "pdf",
                "analysis": {"profile": "magazine_reflow", "legacy_strategy": "magazine_reflow"},
                "quality_report": {"validation_status": "passed", "validation_tool": "epubcheck"},
                "document_summary": {"title": "Stage Sample", "section_count": 1, "asset_count": 0},
            }
        )
        heading_repair_impl = Mock()
        calls: list[dict] = []

        def status_callback(status: str, message: str, **fields) -> None:
            calls.append({"status": status, "message": message, **fields})

        run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="pl",
                heading_repair_enabled=False,
                quality_gate_mode="draft",
                feedback_enabled=False,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
            status_callback=status_callback,
        )

        self.assertEqual(
            [item["stage_id"] for item in calls],
            ["extracting", "assembling", "premium_audit", "auto_repair"],
        )
        self.assertEqual(calls[0]["stage_label"], "Ekstrakcja tekstu")
        self.assertEqual(calls[-1]["percent_estimate"], 88)

    def test_build_conversion_metadata_preserves_cockpit_inputs_and_flattened_fields(self) -> None:
        result = {
            "analysis": {
                "profile": "book_reflow",
                "confidence": 0.93,
                "page_count": 42,
                "render_budget_class": "book_reflow_balanced",
                "has_toc": True,
                "has_tables": True,
                "has_diagrams": False,
                "has_meaningful_images": True,
                "estimated_sections": 8,
                "legacy_strategy": "text_reflowable",
                "detected_features": [f"feature-{index}" for index in range(25)],
                "external_tools": {"epubcheck": {"available": True}},
                "profile_reason": "Dense text document with usable heading signals.",
            },
            "quality_report": {
                "validation_status": "passed_with_warnings",
                "epubcheck_status": "passed_with_warnings",
                "validation_tool": "epubcheck",
                "validation_messages": [f"message-{index}" for index in range(16)],
                "warnings": ["Needs manual review."],
                "section_count": 8,
                "figure_count": 3,
                "diagram_count": 0,
                "table_count": 2,
                "page_marker_count": 42,
                "detected_figures": 3,
                "detected_diagrams": 0,
                "detected_tables": 2,
                "fallback_pages": list(range(30)),
                "fallback_sections": [f"Section {index}" for index in range(30)],
                "fallback_regions": [{"page": index, "reason": "low-confidence"} for index in range(30)],
                "high_risk_pages": [],
                "high_risk_sections": [],
                "archive_entry_count": 54,
                "archive_image_count": 3,
                "largest_assets": [{"href": f"image-{index}.jpg", "size": index} for index in range(25)],
                "size_budget_inspection": {
                    "entry_count": 54,
                    "largest_assets": [{"href": f"asset-{index}.jpg"} for index in range(25)],
                },
                "text_cleanup": {
                    "status": "passed_with_warnings",
                    "auto_fix_count": 11,
                    "review_needed_count": 2,
                    "blocked_count": 0,
                    "publish_blocked": False,
                    "examples": [f"cleanup-{index}" for index in range(25)],
                    "reference_cleanup": {
                        "quality_gate_status": "passed_with_warnings",
                        "visible_junk_detected": 1,
                        "records_reconstructed": 6,
                        "manual_review": [{"id": index} for index in range(20)],
                    },
                    "ai_quality": {
                        "status": "fallback",
                        "before_quality_score": 7.4,
                        "after_quality_score": 7.4,
                        "provider": {"ocr_cleanup": "none", "toc_detection": "none"},
                        "fallback_reasons": ["provider-not-configured"],
                    },
                },
                "semantic_cleanup": {
                    "status": "failed",
                    "message": "Paragraph structure gate failed.",
                    "manual_review_count": 4,
                },
                "ocr_degradation": {
                    "status": "degraded",
                    "degraded_count": 2,
                    "message": "Low confidence OCR pages.",
                },
                "reading_order": {
                    "status": "passed_with_warnings",
                    "manual_review_count": 1,
                    "regions": [{"page": index} for index in range(20)],
                },
            },
            "document_summary": {
                "title": "Contract Probe",
                "author": "Codex QA",
                "language": "pl",
                "profile": "book_reflow",
                "layout_mode": "reflowable",
                "section_count": 8,
                "asset_count": 3,
            },
        }

        metadata = build_conversion_metadata(
            result=result,
            detected_source_type="pdf",
            heading_repair_enabled=False,
            heading_repair_report={"status": "skipped", "release_status": "unavailable"},
        )

        json.dumps(metadata)
        self.assertEqual(metadata["profile"], "book_reflow")
        self.assertEqual(metadata["confidence"], 0.93)
        self.assertEqual(metadata["strategy"], "text_reflowable")
        self.assertEqual(metadata["sections"], 8)
        self.assertEqual(metadata["assets"], 3)
        self.assertEqual(metadata["layout"], "reflowable")
        self.assertEqual(metadata["validation"], "passed_with_warnings")
        self.assertEqual(metadata["validation_tool"], "epubcheck")

        self.assertEqual(metadata["content_metrics"]["table_count"], 2)
        self.assertEqual(len(metadata["content_metrics"]["fallback_pages"]), 20)
        self.assertEqual(len(metadata["content_metrics"]["largest_assets"]), 20)
        self.assertEqual(metadata["text_cleanup"]["review_needed_count"], 2)
        self.assertEqual(len(metadata["text_cleanup"]["examples"]), 20)
        self.assertEqual(metadata["ai_quality"]["status"], "fallback")
        self.assertEqual(metadata["ai_quality"]["before_quality_score"], 7.4)
        self.assertEqual(metadata["ai_quality"]["provider"]["ocr_cleanup"], "none")
        self.assertEqual(metadata["reference_cleanup"]["quality_gate_status"], "passed_with_warnings")
        self.assertEqual(metadata["reference_cleanup"]["visible_junk_detected"], 1)
        self.assertEqual(metadata["semantic_cleanup"]["status"], "failed")
        self.assertEqual(metadata["ocr_quality"]["degraded_count"], 2)
        self.assertEqual(metadata["reading_order"]["manual_review_count"], 1)
        self.assertEqual(len(metadata["reading_order"]["regions"]), 12)
        self.assertEqual(metadata["source_analysis"]["page_count"], 42)
        self.assertEqual(len(metadata["source_analysis"]["detected_features"]), 20)
        self.assertEqual(metadata["document_summary"]["title"], "Contract Probe")
        self.assertEqual(metadata["validation_details"]["epubcheck_status"], "passed_with_warnings")
        self.assertEqual(len(metadata["validation_details"]["validation_messages"]), 12)
        self.assertEqual(len(metadata["validation_details"]["size_budget_inspection"]["largest_assets"]), 12)

    def test_build_conversion_summary_includes_quality_state_and_output_size(self) -> None:
        outcome = ConversionOutcome(
            result={
                "epub_bytes": b"removed",
                "quality_report": {"validation_status": "passed", "validation_tool": "epubcheck"},
                "analysis": {"profile": "book_reflow"},
                "document_summary": {"section_count": 1, "asset_count": 2, "title": "Probe"},
            },
            epub_bytes=b"final-epub",
            heading_repair_report={"status": "skipped", "release_status": "unavailable", "toc_entries_before": 0},
            detected_source_type="pdf",
            download_name="book.epub",
            metadata={"validation": "passed"},
        )
        summary = build_conversion_summary(
            outcome,
            filename="book.pdf",
            output_size_bytes=32 * 1024 * 1024,
            download_url="https://localhost/download/book.epub",
        )

        self.assertNotIn("epub_bytes", summary)
        self.assertEqual(summary["download_name"], "book.epub")
        self.assertIn("quality_state", summary)
        self.assertEqual(summary["metadata"]["output_size_bytes"], 32 * 1024 * 1024)
        self.assertEqual(summary["output_size_bytes"], 32 * 1024 * 1024)

    def test_enrich_conversion_metadata_with_output_size_adds_warning_when_oversized(self) -> None:
        base_metadata = {
            "warnings": 12,
            "warning_list": [f"existing-{index}" for index in range(12)],
        }
        summary = enrich_conversion_metadata_with_output_size(base_metadata, 30 * 1024 * 1024)

        self.assertEqual(summary["output_size_bytes"], 30 * 1024 * 1024)
        self.assertEqual(len(summary["warning_list"]), 12)
        self.assertEqual(
            summary["warning_list"][-1],
            "EPUB ma 30.0 MB. Na Kindle pobranie i otwarcie moze byc wolniejsze.",
        )
        self.assertEqual(summary["warnings"], 12)

    def test_run_document_conversion_skips_heading_repair_for_diagram_book_profile(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"diagram-epub",
                "source_type": "pdf",
                "analysis": {
                    "profile": "diagram_book_reflow",
                    "confidence": 0.88,
                    "legacy_strategy": "hybrid",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "The Woodpecker Method",
                    "author": "Unknown",
                    "layout_mode": "reflowable",
                    "section_count": 20,
                    "asset_count": 1164,
                },
            }
        )
        heading_repair_impl = Mock()
        status_updates: list[tuple[str, str]] = []

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="woodpecker.pdf",
                source_type="pdf",
                original_filename="woodpecker.pdf",
                profile="auto-premium",
                language="en",
                heading_repair_enabled=True,
                quality_gate_mode="off",
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
            status_callback=lambda status, message: status_updates.append((status, message)),
        )

        self.assertEqual(outcome.epub_bytes, b"diagram-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "skipped")
        self.assertEqual(outcome.heading_repair_report["release_status"], "skipped")
        self.assertEqual(outcome.heading_repair_report["epubcheck_status"], "skipped")
        self.assertIn("diagram-heavy training book", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "skipped")
        heading_repair_impl.assert_not_called()
        self.assertEqual(
            status_updates,
            [
                ("running", "Ekstrakcja tekstu z PDF..."),
                ("running", "Składanie artykułów i struktury EPUB..."),
                ("running", "Uruchamiam audyt premium EPUB..."),
            ],
        )

    def test_run_document_conversion_skips_heading_repair_for_chess_notation_collections(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"notation-epub",
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.9,
                    "legacy_strategy": "text_reflowable",
                    "detected_features": ["chess-notation-collection"],
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Jobava Collection",
                    "author": "Unknown",
                    "layout_mode": "reflowable",
                    "section_count": 20,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="jobava.pdf",
                source_type="pdf",
                original_filename="jobava.pdf",
                profile="auto-premium",
                language="en",
                heading_repair_enabled=True,
                quality_gate_mode="off",
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        self.assertEqual(outcome.epub_bytes, b"notation-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "skipped")
        self.assertIn("chess-notation-collection", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "skipped")
        heading_repair_impl.assert_not_called()

    def test_run_document_conversion_omits_source_type_for_cli_style_requests(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"cli-epub",
                "analysis": {"profile": "docx_reflow", "confidence": 0.95},
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Docx Probe",
                    "author": "Codex QA",
                    "layout_mode": "reflowable",
                    "section_count": 2,
                    "asset_count": 1,
                },
            }
        )
        heading_repair_impl = Mock()

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.docx",
                original_filename="sample.docx",
                profile="auto-premium",
                language="pl",
                quality_gate_mode="off",
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        convert_kwargs = convert_impl.call_args.kwargs
        self.assertNotIn("source_type", convert_kwargs)
        self.assertFalse(convert_kwargs["config"].force_ocr)
        self.assertEqual(outcome.download_name, "sample.epub")
        self.assertEqual(outcome.metadata["source_type"], "docx")
        heading_repair_impl.assert_not_called()

    def test_run_document_conversion_uses_repaired_epub_and_conversion_flags_when_heading_repair_succeeds(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"base-epub",
                "source_type": "pdf",
                "analysis": {
                    "profile": "fixed_layout_fallback",
                    "confidence": 0.81,
                    "legacy_strategy": "layout_fixed",
                },
                "quality_report": {
                    "validation_status": "passed_with_warnings",
                    "validation_tool": "epubcheck",
                    "warnings": ["Large raster pages."],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                    "render_budget_class": "fixed_layout_dense",
                    "render_budget_attempt": "fallback",
                    "size_budget_status": "passed_with_warnings",
                    "size_budget_message": "Fallback preset was required.",
                    "target_warn_bytes": 2048,
                    "target_hard_bytes": 4096,
                    "final_output_size_bytes": 3072,
                },
                "document_summary": {
                    "title": "Layout Probe",
                    "author": "Codex QA",
                    "layout_mode": "fixed-layout",
                    "section_count": 1,
                    "asset_count": 12,
                },
            }
        )
        heading_repair_impl = Mock(
            return_value=SimpleNamespace(
                epub_bytes=b"repaired-epub",
                summary={
                    "release_status": "pass",
                    "toc_entries_before": 1,
                    "toc_entries_after": 1,
                    "headings_removed": 0,
                    "manual_review_count": 0,
                    "epubcheck_status": "passed",
                },
                epubcheck={"status": "passed", "messages": []},
            )
        )

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="visual.pdf",
                source_type="pdf",
                original_filename="visual.pdf",
                profile="preserve-layout",
                language="en",
                force_ocr=True,
                heading_repair_enabled=True,
                text_cleanup_domain_dictionary_path="docs/domain-dictionary-example.json",
                quality_gate_mode="off",
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        convert_kwargs = convert_impl.call_args.kwargs
        self.assertTrue(convert_kwargs["config"].prefer_fixed_layout)
        self.assertTrue(convert_kwargs["config"].force_ocr)
        self.assertEqual(convert_kwargs["config"].language, "en")
        self.assertEqual(convert_kwargs["config"].text_cleanup_domain_dictionary_path, "docs/domain-dictionary-example.json")
        self.assertEqual(outcome.epub_bytes, b"base-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "skipped")
        self.assertEqual(outcome.metadata["render_budget_class"], "fixed_layout_dense")
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "skipped")
        self.assertEqual(outcome.metadata["strategy"], "layout_fixed")
        self.assertEqual(outcome.result["quality_report"]["validation_status"], "passed_with_warnings")
        heading_repair_impl.assert_not_called()

    def test_run_document_conversion_marks_heading_repair_exception_as_failed_and_keeps_base_epub(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"base-epub",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.88,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Crash Probe",
                    "author": "Codex QA",
                    "layout_mode": "reflowable",
                    "section_count": 2,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock(side_effect=RuntimeError("repair exploded"))

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="pl",
                heading_repair_enabled=True,
                quality_gate_mode="off",
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        self.assertEqual(outcome.epub_bytes, b"base-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "failed")
        self.assertIn("repair exploded", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "failed")
        self.assertIn("repair exploded", outcome.metadata["heading_repair"]["error"])

    def test_run_document_conversion_records_feedback_failure(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"base-epub",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.91,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Feedback",
                    "author": "KindleMaster QA",
                    "layout_mode": "reflowable",
                    "section_count": 1,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock()
        fake_ml_feedback = SimpleNamespace(
            append_conversion_feedback_event=Mock(side_effect=RuntimeError("feedback sink is unavailable"))
        )

        with patch.dict("sys.modules", {"ml_feedback": fake_ml_feedback}):
            outcome = run_document_conversion(
                ConversionRequest(
                    source_path="sample.pdf",
                    source_type="pdf",
                    original_filename="sample.pdf",
                    profile="auto-premium",
                    language="pl",
                    heading_repair_enabled=False,
                    quality_gate_mode="off",
                ),
                convert_impl=convert_impl,
                heading_repair_impl=heading_repair_impl,
            )

        self.assertEqual(outcome.metadata["ml_feedback"]["status"], "failed")
        self.assertIn("feedback sink is unavailable", outcome.metadata["ml_feedback"]["error"])
        self.assertEqual(outcome.metadata["feedback_learning"]["status"], "failed")
        self.assertIn("feedback sink is unavailable", outcome.metadata["feedback_learning"]["error"])

    def test_run_document_conversion_uses_document_summary_profile_for_heading_repair(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"base-epub",
                "analysis": {},
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "profile": "magazine_reflow",
                    "title": "Profile fallback",
                    "author": "KindleMaster QA",
                    "layout_mode": "reflowable",
                    "section_count": 3,
                    "asset_count": 1,
                },
            }
        )
        heading_repair_impl = Mock(
            return_value=SimpleNamespace(
                epub_bytes=b"repaired-epub",
                summary={
                    "release_status": "pass",
                    "toc_entries_before": 1,
                    "toc_entries_after": 1,
                    "headings_removed": 0,
                    "manual_review_count": 0,
                    "epubcheck_status": "passed",
                },
                epubcheck={"status": "passed", "messages": []},
            )
        )

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="en",
                heading_repair_enabled=True,
                quality_gate_mode="off",
                feedback_enabled=False,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        self.assertEqual(outcome.epub_bytes, b"repaired-epub")
        self.assertEqual(heading_repair_impl.call_args.kwargs["publication_profile"], "magazine_reflow")

    def test_serve_http_app_uses_flask_runtime(self) -> None:
        application = SimpleNamespace(run=Mock())

        exit_code = serve_http_app(
            application,
            host="127.0.0.1",
            port=5001,
            debug=True,
            runtime="flask",
        )

        self.assertEqual(exit_code, 0)
        application.run.assert_called_once_with(debug=True, host="127.0.0.1", port=5001)

    def test_serve_http_app_uses_waitress_runtime(self) -> None:
        application = SimpleNamespace(run=Mock())
        waitress_module = SimpleNamespace(serve=Mock())

        with patch.dict(sys.modules, {"waitress": waitress_module}):
            exit_code = serve_http_app(
                application,
                host="127.0.0.1",
                port=5002,
                debug=False,
                runtime="waitress",
            )

        self.assertEqual(exit_code, 0)
        waitress_module.serve.assert_called_once_with(application, host="127.0.0.1", port=5002)
        application.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
