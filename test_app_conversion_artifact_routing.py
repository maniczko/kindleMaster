from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import app as app_module
from app import app
from artifact_storage import ArtifactKind


class AppConversionArtifactRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.store_temp_dir = tempfile.TemporaryDirectory()
        self.original_conversion_job_store = app_module._CONVERSION_JOB_STORE
        with app_module._CONVERSION_JOBS_LOCK:
            self.saved_jobs = {job_id: dict(job) for job_id, job in app_module._CONVERSION_JOBS.items()}
            app_module._CONVERSION_JOBS.clear()
        app_module._CONVERSION_JOB_STORE = app_module.ConversionJobStore(
            app_module._CONVERSION_JOBS,
            app_module._CONVERSION_JOBS_LOCK,
            persistence_path=Path(self.store_temp_dir.name) / "conversion_jobs.json",
            active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
        )
        self.cleanup_dirs: list[Path] = []

    def tearDown(self) -> None:
        for directory in self.cleanup_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS.clear()
            app_module._CONVERSION_JOBS.update({job_id: dict(job) for job_id, job in self.saved_jobs.items()})
        app_module._CONVERSION_JOB_STORE = self.original_conversion_job_store
        self.store_temp_dir.cleanup()

    def _artifact_root(self, job_id: str) -> Path:
        root = Path(app_module.app.root_path) / "output" / "artifacts" / job_id
        self.cleanup_dirs.append(root)
        return root

    def _register_chess_html_job(
        self,
        job_id: str,
        html_text: str,
        *,
        filename: str = "chess_games.html",
        manifest: dict | None = None,
    ) -> Path:
        job_root = self._artifact_root(job_id)
        report_dir = job_root / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        html_path = report_dir / filename
        html_path.write_text(html_text, encoding="utf-8")
        if manifest is not None:
            manifest_name = "source_html_evidence_manifest.json" if manifest.get("artifact_type") == "source_html_evidence_only" else "artifact_manifest.json"
            (report_dir / manifest_name).write_text(json.dumps(manifest), encoding="utf-8")
        artifact = app_module._local_artifact_metadata(job_id, ArtifactKind.REPORT, html_path)
        artifact["content_type"] = "text/html; charset=utf-8"
        artifact["download_url"] = f"/convert/artifact/{job_id}/chess_pgn_html"
        artifact["label"] = "HTML PGN/FEN"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB ready.",
                "source_type": "pdf",
                "filename": "study.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "study.epub",
                "metadata": {"source_type": "pdf", "profile": "chess_training"},
                "output_size_bytes": 0,
                "error": "",
                "error_code": "",
                "artifacts": {"chess_pgn_html": artifact},
            }
        return html_path

    def test_evidence_only_html_is_not_returned_as_final_artifact(self) -> None:
        job_id = "routing-evidence-only"
        self._register_chess_html_job(
            job_id,
            """
            <!doctype html>
            <html data-artifact-type="source_html_evidence_only" data-source-html-gate-decision="reject_degraded_source_html">
              <body data-artifact-type="source_html_evidence_only" data-source-html-gate-decision="reject_degraded_source_html">
                <p>Side to move: unknown</p>
              </body>
            </html>
            """,
            manifest={
                "schema": "kindlemaster.chess_study.artifact_manifest.v1",
                "artifact_type": "source_html_evidence_only",
                "pipeline_mode": "source_html_evidence_report",
                "source_html_quality_gate": {
                    "decision": "reject_degraded_source_html",
                    "source_html_evidence_only": True,
                    "used_as_final_reader": False,
                    "reasons": ["diagram_image_sources_degraded"],
                },
            },
        )

        response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "final_reader_missing")
        self.assertEqual(payload["artifact_type"], "source_html_evidence_only")
        self.assertEqual(payload["source_html_quality_gate"]["decision"], "reject_degraded_source_html")
        self.assertEqual(payload["final_reader_path"], "")
        self.assertTrue(payload["source_html_evidence_path"].endswith("chess_games.html"))
        self.assertNotIn("Side to move: unknown", response.get_data(as_text=True))

    def test_degraded_source_html_without_metadata_returns_final_reader_missing(self) -> None:
        job_id = "routing-degraded-source"
        self._register_chess_html_job(
            job_id,
            """
            <!doctype html>
            <html><body>
              <section class="chess-book-page" data-page="1">
                <div class="book-text" data-reading-order="1">Diagram 1-1</div>
                <div class="book-diagram" data-reading-order="2"><img src="" alt=""></div>
                <div class="book-text" data-reading-order="3">Diagram 1-2</div>
                <div class="book-diagram" data-reading-order="4"><img src="http://127.0.0.1:5001/missing.png" alt=""></div>
              </section>
              <p>Side to move: unknown</p>
            </body></html>
            """,
        )

        response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "final_reader_missing")
        self.assertEqual(payload["source_html_quality_gate"]["decision"], "reject_degraded_source_html")
        self.assertTrue(payload["source_html_quality_gate"]["source_html_evidence_only"])
        self.assertIn("diagram_image_sources_degraded", payload["source_html_quality_gate"]["reasons"])
        self.assertEqual(payload["final_reader_path"], "")
        self.assertNotIn("Side to move: unknown", response.get_data(as_text=True))

    def test_final_reader_artifact_is_returned_and_exposed_in_status_payload(self) -> None:
        job_id = "routing-final-reader"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Source report should not be served.</p></body></html>",
        )
        job_root = source_html.parents[1]
        semantic_dir = job_root / "semantic_chess_html"
        data_dir = semantic_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": "kindlemaster.chess_study.artifact_manifest.v1",
            "artifact_type": "final_pdf_two_crop_reader",
            "pipeline_mode": "source_html_semantic_reader",
            "generated_at": "2026-06-28T00:00:00Z",
            "source_pdf": "",
            "source_html": str(source_html),
            "commit_sha": "",
            "commit_sha_reason": "test",
            "source_html_quality_gate": {
                "decision": "use_source_html_as_final_reader",
                "source_html_evidence_only": False,
                "used_as_final_reader": True,
                "reasons": [],
            },
            "side_unknown_count": 0,
            "trusted_marker_count": 2,
            "empty_img_src_count": 0,
            "diagrams_total": 2,
            "fen_accepted": 2,
        }
        (data_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        index_path = semantic_dir / "index.html"
        index_path.write_text(
            '<!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">Final reader</body></html>',
            encoding="utf-8",
        )

        response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")
        status_response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Final reader", response.get_data(as_text=True))
        self.assertNotIn("Source report should not be served.", response.get_data(as_text=True))
        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        self.assertEqual(payload["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(payload["final_reader_path"].endswith("semantic_chess_html\\index.html") or payload["final_reader_path"].endswith("semantic_chess_html/index.html"))
        self.assertEqual(payload["source_html_evidence_path"], "")
        self.assertEqual(payload["source_html_quality_gate"]["decision"], "use_source_html_as_final_reader")
        self.assertEqual(payload["conversion"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(payload["artifacts"]["chess_pgn_html"]["artifact_type"], "final_pdf_two_crop_reader")


if __name__ == "__main__":
    unittest.main()
