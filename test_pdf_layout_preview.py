from __future__ import annotations

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
