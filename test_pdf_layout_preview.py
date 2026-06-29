from __future__ import annotations

import json
import os
import shutil
import unittest
from datetime import UTC, datetime
from pathlib import Path

import app as app_module
from app import app


class PdfLayoutPreviewArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.cleanup_paths: list[str] = []
        self.cleanup_dirs: list[Path] = []
        with app_module._CONVERSION_JOBS_LOCK:
            self.original_conversion_jobs = {
                job_id: dict(job)
                for job_id, job in app_module._CONVERSION_JOBS.items()
            }
            app_module._CONVERSION_JOBS.clear()

    def tearDown(self) -> None:
        for path in self.cleanup_paths:
            if path and os.path.exists(path):
                os.remove(path)
        for path in self.cleanup_dirs:
            if path.exists():
                shutil.rmtree(path)
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS.clear()
            app_module._CONVERSION_JOBS.update(
                {job_id: dict(job) for job_id, job in self.original_conversion_jobs.items()}
            )
        app_module._CONVERSION_JOB_STORE.persist()

    def _write_final_reader_artifact(
        self,
        job_id: str,
        job: dict,
        *,
        health_gate_status: str = "PASS",
        blockers: list[str] | None = None,
    ) -> dict:
        artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.REPORT,
            filename="chess_games.html",
            data=b"<html><body>source evidence only</body></html>",
        )
        artifact_path = Path(artifact["location"])
        self.cleanup_paths.append(str(artifact_path))
        job_root = artifact_path.parents[1]
        self.cleanup_dirs.append(job_root)
        semantic_dir = job_root / "semantic_chess_html"
        data_dir = semantic_dir / "data"
        reports_dir = semantic_dir / "reports"
        data_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": "kindlemaster.chess_study.artifact_manifest.v1",
            "artifact_type": "final_pdf_two_crop_reader",
            "pipeline_mode": "pdf_two_crop_reader",
            "generated_at": "2026-06-29T00:00:00Z",
            "source_pdf": "",
            "source_html": str(artifact_path),
            "commit_sha": "",
            "commit_sha_reason": "test",
            "source_html_quality_gate": {
                "decision": "use_source_html_as_final_reader",
                "source_html_evidence_only": False,
                "used_as_final_reader": True,
                "reasons": [],
            },
            "side_unknown_count": 0 if health_gate_status == "PASS" else 30,
            "trusted_marker_count": 2 if health_gate_status == "PASS" else 0,
            "empty_img_src_count": 0,
            "diagrams_total": 2,
            "fen_accepted": 2 if health_gate_status == "PASS" else 0,
        }
        (data_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (semantic_dir / "index.html").write_text(
            """
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article data-side-marker-status="trusted_marker">Side to move: white</article>
            </body></html>
            """,
            encoding="utf-8",
        )
        (reports_dir / "final_reader_health_gate.json").write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
                    "decision": "pass" if health_gate_status == "PASS" else "fail",
                    "status": health_gate_status,
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "pdf_two_crop_reader",
                    "blockers": blockers or ([] if health_gate_status == "PASS" else ["final_reader_health_gate_failed"]),
                    "side_unknown_count": 0 if health_gate_status == "PASS" else 30,
                    "trusted_marker_count": 2 if health_gate_status == "PASS" else 0,
                    "empty_img_src_count": 0,
                    "fen_accepted": 2 if health_gate_status == "PASS" else 0,
                    "fen_evidence_count": 2 if health_gate_status == "PASS" else 0,
                }
            ),
            encoding="utf-8",
        )
        artifact["download_url"] = f"/convert/artifact/{job_id}/chess_pgn_html"
        artifact["label"] = "HTML PGN/FEN"
        artifact["content_type"] = "text/html; charset=utf-8"
        job["artifacts"]["chess_pgn_html"] = artifact
        app_module._CONVERSION_JOB_STORE.create(job)
        return artifact

    def test_pdf_layout_preview_artifact_renders_app_shell_viewer(self) -> None:
        job_id = "pdf-layout-preview-shell"
        raw_preview_html = b"<html><body>old standalone layout</body></html>"
        artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.REPORT,
            filename="pdf_layout_preview.html",
            data=raw_preview_html,
        )
        self.cleanup_paths.append(str(artifact["location"]))
        self.cleanup_dirs.append(Path(artifact["location"]).parents[1])
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="layout.pdf",
            created_at=created_at,
        )
        job.update(
            {
                "status": "ready",
                "artifacts": {"pdf_layout_preview": artifact},
            }
        )
        app_module._CONVERSION_JOB_STORE.create(job)

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn('data-vr-hook="vat-209-shell"', html)
        self.assertIn('data-vr-hook="km-pdf-layout-preview-handoff"', html)
        self.assertIn('id="pdfCanvas"', html)
        self.assertIn('data-artifact-view="pdf_layout_preview"', html)
        self.assertIn("Artefakt audytowy", html)
        self.assertIn("To nie jest finalny reader szachowy", html)
        self.assertIn("Ten widok nie służy do oceny FEN/PGN/side-to-move.", html)
        self.assertIn("Final reader niedostępny", html)
        self.assertIn("final_reader_missing", html)
        self.assertNotIn("final_pdf_two_crop_reader", html)
        self.assertIn('class="layout-preview-frame"', html)
        self.assertIn("sandbox", html)
        self.assertIn('srcdoc="', html)
        self.assertIn("old standalone layout", html)
        self.assertIn("app-shell.css?v=", html)
        self.assertIn("conversion-ui.js?v=", html)
        self.assertIn("library.js?v=", html)
        self.assertNotIn("<html><body>old standalone layout</body></html>", html)
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(response.headers["X-KindleMaster-Artifact-View"], "app-shell")

    def test_remote_pdf_layout_preview_is_embedded_as_sandboxed_frame(self) -> None:
        job_id = "remote-pdf-layout-preview-shell"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="remote-layout.pdf",
            created_at=created_at,
        )
        job.update(
            {
                "status": "ready",
                "artifacts": {
                    "pdf_layout_preview": {
                        "provider": "supabase",
                        "status": "stored",
                        "kind": "report",
                        "job_id": job_id,
                        "filename": "pdf_layout_preview.html",
                        "location": "supabase://bucket/pdf_layout_preview.html",
                        "size_bytes": 128,
                        "content_type": "text/html; charset=utf-8",
                        "signed_url": {
                            "available": True,
                            "url": "https://signed.example.invalid/pdf_layout_preview.html",
                            "expires_in_seconds": 900,
                            "reason": "",
                        },
                    }
                },
            }
        )
        app_module._CONVERSION_JOB_STORE.create(job)

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-vr-hook="km-pdf-layout-preview-handoff"', html)
        self.assertIn('class="layout-preview-frame"', html)
        self.assertIn("sandbox", html)
        self.assertIn('src="https://signed.example.invalid/pdf_layout_preview.html"', html)
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))

    def test_pdf_layout_preview_warning_links_to_available_html_pgn_fen_reader(self) -> None:
        job_id = "pdf-layout-preview-final-reader-link"
        raw_preview_html = b"<html><body>layout audit only</body></html>"
        preview_artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.REPORT,
            filename="pdf_layout_preview.html",
            data=raw_preview_html,
        )
        self.cleanup_paths.append(str(preview_artifact["location"]))
        self.cleanup_dirs.append(Path(preview_artifact["location"]).parents[1])
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="reader-link.pdf",
            created_at=created_at,
        )
        job.update(
            {
                "status": "ready",
                "artifacts": {"pdf_layout_preview": preview_artifact},
            }
        )
        self._write_final_reader_artifact(job_id, job)

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("To nie jest finalny reader szachowy", html)
        self.assertIn("Otwórz HTML PGN/FEN", html)
        self.assertIn(f'href="/convert/artifact/{job_id}/chess_pgn_html"', html)
        self.assertIn('data-primary-chess-artifact="chess_pgn_html"', html)
        self.assertNotIn('data-primary-chess-artifact="pdf_layout_preview"', html)
        self.assertNotIn("Final reader niedostępny", html)

    def test_pdf_layout_preview_warning_blocks_when_final_reader_unhealthy(self) -> None:
        job_id = "pdf-layout-preview-unhealthy-reader"
        raw_preview_html = b"<html><body>layout audit only</body></html>"
        preview_artifact = app_module._store_artifact_bytes(
            job_id=job_id,
            kind=app_module.ArtifactKind.REPORT,
            filename="pdf_layout_preview.html",
            data=raw_preview_html,
        )
        self.cleanup_paths.append(str(preview_artifact["location"]))
        self.cleanup_dirs.append(Path(preview_artifact["location"]).parents[1])
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        job = app_module.build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename="reader-blocked.pdf",
            created_at=created_at,
        )
        job.update(
            {
                "status": "ready",
                "artifacts": {"pdf_layout_preview": preview_artifact},
            }
        )
        self._write_final_reader_artifact(
            job_id,
            job,
            health_gate_status="FAIL",
            blockers=["mass_side_to_move_unknown"],
        )

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Final reader niedostępny", html)
        self.assertIn("mass_side_to_move_unknown", html)
        self.assertIn(f'href="/convert/quality/{job_id}"', html)
        self.assertNotIn('data-primary-chess-artifact="chess_pgn_html"', html)

    def test_restored_local_pdf_layout_preview_keeps_distinct_artifact_key(self) -> None:
        job_id = "restored-pdf-layout-preview-shell"
        job_dir = Path(app_module.app.root_path) / "output" / "artifacts" / job_id
        report_dir = job_dir / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_dirs.append(job_dir)
        (report_dir / "restored.quality.json").write_text("{}", encoding="utf-8")
        (report_dir / "pdf_layout_preview.html").write_text(
            "<html><body>restored old layout</body></html>",
            encoding="utf-8",
        )

        response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        html = response.get_data(as_text=True)
        restored_job = app_module._get_conversion_job(job_id)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(restored_job, dict)
        self.assertIn("pdf_layout_preview", restored_job["artifacts"])
        self.assertNotIn("chess_pgn_html", restored_job["artifacts"])
        self.assertIn('data-vr-hook="vat-209-shell"', html)
        self.assertIn('data-vr-hook="km-pdf-layout-preview-handoff"', html)
        self.assertIn('id="pdfCanvas"', html)
        self.assertIn("restored old layout", html)


if __name__ == "__main__":
    unittest.main()
