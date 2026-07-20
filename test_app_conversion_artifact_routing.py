from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import app as app_module
from app import app
from artifact_storage import ArtifactKind


class AppConversionArtifactRoutingTests(unittest.TestCase):
    TEST_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

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

    def _write_test_png(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.TEST_PNG)

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

    def test_semantic_reader_rewrites_all_safe_local_asset_urls(self) -> None:
        semantic_index = self._artifact_root("rewrite-reader-assets") / "semantic_chess_html" / "index.html"
        rewritten = app_module._rewrite_semantic_chess_asset_urls(
            """
            <link href="styles.css"><script src="app.js"></script>
            <img src="assets/board.png"><img src="review/chess_fen/marker.png">
            <a href="qa_report.html">QA</a><a href="#chapter-1">Chapter</a>
            <img src="https://example.invalid/external.png">
            """,
            asset_base="/convert/artifact/job/chess_reader_asset/",
            semantic_index=semantic_index,
        )

        self.assertIn('/chess_reader_asset/styles.css', rewritten)
        self.assertIn('/chess_reader_asset/app.js', rewritten)
        self.assertIn('/chess_reader_asset/assets/board.png', rewritten)
        self.assertIn('/chess_reader_asset/review/chess_fen/marker.png', rewritten)
        self.assertIn('/chess_reader_asset/qa_report.html', rewritten)
        self.assertIn('href="#chapter-1"', rewritten)
        self.assertIn('src="https://example.invalid/external.png"', rewritten)

    def test_semantic_reader_rejects_asset_path_escape(self) -> None:
        semantic_index = self._artifact_root("reader-asset-escape") / "semantic_chess_html" / "index.html"

        self.assertEqual(
            app_module._semantic_reader_asset_route_path("../../outside.png", semantic_index),
            "",
        )
        self.assertEqual(
            app_module._semantic_reader_asset_route_path("%2e%2e/%2e%2e/outside.png", semantic_index),
            "",
        )

    def test_recovered_fen_review_is_served_with_source_bound_crop_assets(self) -> None:
        job_id = "fen-review-artifact"
        job_root = self._artifact_root(job_id)
        input_dir = job_root / "input"
        review_dir = job_root / "review"
        assets_dir = review_dir / "fen_manual_assets"
        input_dir.mkdir(parents=True)
        assets_dir.mkdir(parents=True)
        (input_dir / "study.pdf").write_bytes(b"%PDF-1.4\n")
        (review_dir / "fen_manual_review.html").write_text(
            '<!doctype html><html><body><img src="fen_manual_assets/diagram.png"></body></html>',
            encoding="utf-8",
        )
        fingerprint = "1" * 64
        source_digest = "a" * 64
        (review_dir / "fen_manual_draft.jsonl").write_text(
            json.dumps(
                {
                    "artifact_id": job_id,
                    "diagram_id": "p001-d1",
                    "diagram_fingerprint": fingerprint,
                    "source_document_sha256": source_digest,
                    "crop_sha256": "2" * 64,
                    "square_labels": [""] * 64,
                    "label_status": "needs_piece_labels",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        asset_bytes = b"\x89PNG\r\n\x1a\nsource-bound-crop"
        (assets_dir / "diagram.png").write_bytes(asset_bytes)

        job = app_module._rebuild_job_from_local_artifact_dir(job_root)

        self.assertIsNotNone(job)
        assert job is not None
        self.assertIn("chess_fen_review", job["artifacts"])
        self.assertEqual(
            job["artifacts"]["chess_fen_review"]["download_url"],
            f"/convert/artifact/{job_id}/chess_fen_review",
        )
        stale_job = dict(job)
        stale_job["artifacts"] = {}
        app_module._CONVERSION_JOB_STORE.create(stale_job)
        html_response = self.client.get(f"/convert/artifact/{job_id}/chess_fen_review")
        crop_response = self.client.get(f"/convert/artifact/{job_id}/fen_manual_assets/diagram.png")
        cells = [""] * 64
        cells[4] = "k"
        cells[60] = "K"
        save_response = self.client.put(
            f"/convert/artifact/{job_id}/chess_fen_review_progress",
            json={
                "source_digest": source_digest,
                "rows": [
                    {
                        "diagram_fingerprint": fingerprint,
                        "square_labels": cells,
                        "piece_labels_verified": True,
                        "manual_side_to_move": "w",
                        "manual_side_evidence": "marker",
                        "manual_visible_marker": "outline_triangle",
                        "board_crop_label": "correct",
                        "marker_crop_label": "clear",
                        "label_status": "verified",
                        "verified_by": "PM",
                    }
                ],
            },
        )
        progress_response = self.client.get(f"/convert/artifact/{job_id}/chess_fen_review_progress")

        self.assertEqual(html_response.status_code, 200)
        self.assertIn("text/html", html_response.content_type)
        self.assertIn(b'id="metric-completed"', html_response.data)
        self.assertIn(b'id="metric-excluded"', html_response.data)
        self.assertNotIn(b'id="metric-closed"', html_response.data)
        self.assertEqual(crop_response.status_code, 200)
        self.assertEqual(crop_response.data, asset_bytes)
        self.assertEqual(crop_response.headers.get("X-KindleMaster-Artifact-Source"), "fen-manual-review-asset")
        self.assertEqual(save_response.status_code, 200)
        self.assertEqual(save_response.get_json()["summary"]["verified"], 1)
        self.assertEqual(progress_response.status_code, 200)
        self.assertEqual(progress_response.get_json()["rows"][0]["manual_fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        html_response.close()
        crop_response.close()
        save_response.close()
        progress_response.close()

    def test_fen_review_only_artifact_is_restored_without_epub_or_input(self) -> None:
        job_id = "fen-review-only"
        review_dir = self._artifact_root(job_id) / "review"
        review_dir.mkdir(parents=True)
        (review_dir / "fen_manual_review.html").write_text("<!doctype html><title>Review</title>", encoding="utf-8")

        job = app_module._rebuild_job_from_local_artifact_dir(review_dir.parent)

        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job["status"], "ready")
        self.assertIn("chess_fen_review", job["artifacts"])

    def _register_chess_pgn_artifact(self, job_id: str, pgn_text: str) -> Path:
        job_root = self._artifact_root(job_id)
        report_dir = job_root / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        pgn_path = report_dir / "chess_games.pgn"
        pgn_path.write_text(pgn_text, encoding="utf-8")
        artifact = app_module._local_artifact_metadata(job_id, ArtifactKind.REPORT, pgn_path)
        artifact["content_type"] = "application/x-chess-pgn; charset=utf-8"
        artifact["download_url"] = f"/convert/artifact/{job_id}/chess_pgn"
        artifact["label"] = "PGN"
        artifact["available"] = True
        artifact["status"] = "available"
        artifact["message"] = "PGN gotowy do pobrania."
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["artifacts"]["chess_pgn"] = artifact
        return pgn_path

    def _register_engine_analysis_gate_artifact(self, job_id: str, gate: dict) -> Path:
        job_root = self._artifact_root(job_id)
        report_dir = job_root / "reports" / "chess_engine"
        report_dir.mkdir(parents=True, exist_ok=True)
        gate_path = report_dir / "engine_analysis_gate.json"
        gate_path.write_text(json.dumps(gate), encoding="utf-8")
        artifact = app_module._local_artifact_metadata(job_id, ArtifactKind.REPORT, gate_path)
        artifact["content_type"] = "application/json; charset=utf-8"
        artifact["download_url"] = f"/convert/artifact/{job_id}/engine_analysis_gate"
        artifact["label"] = "Engine analysis gate"
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["artifacts"]["engine_analysis_gate"] = artifact
        return gate_path

    def _write_final_reader_sidecar(self, source_html: Path, *, diagrams_total: int = 1) -> Path:
        job_root = source_html.parents[1]
        semantic_dir = job_root / "semantic_chess_html"
        data_dir = semantic_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": "kindlemaster.chess_study.artifact_manifest.v1",
            "artifact_type": "final_pdf_two_crop_reader",
            "pipeline_mode": "pdf_two_crop_reader",
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
            "trusted_marker_count": diagrams_total,
            "empty_img_src_count": 0,
            "diagrams_total": diagrams_total,
            "diagram_cards_count": diagrams_total,
            "data_side_marker_attr_count": diagrams_total,
            "side_marker_crop_count": diagrams_total,
            "board_crop_count": diagrams_total,
            "fen_accepted": diagrams_total,
        }
        (data_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        reports_dir = semantic_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "final_reader_health_gate.json").write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
                    "decision": "pass",
                    "status": "PASS",
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "pdf_two_crop_reader",
                    "diagram_cards_count": diagrams_total,
                    "side_unknown_count": 0,
                    "data_side_marker_attr_count": diagrams_total,
                    "trusted_marker_count": diagrams_total,
                    "side_marker_crop_count": diagrams_total,
                    "board_crop_count": diagrams_total,
                    "empty_img_src_count": 0,
                    "asset_missing_empty_src_count": 0,
                    "fen_accepted": diagrams_total,
                    "blockers": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        index_path = semantic_dir / "index.html"
        index_path.write_text(
            """
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article class="card" data-diagram-id="one" data-position-status="accepted"
                       data-side-marker-status="trusted_marker" data-side-to-move="w"
                       data-fen="8/8/8/8/8/8/4K3/4k3 w - - 0 1"
                       data-has-board-crop="true" data-has-side-marker-crop="true">
                <img src="assets/board-one.png" alt="board crop">
                <p>side_to_move: white</p>
                <p>FEN: 8/8/8/8/8/8/4K3/4k3 w - - 0 1</p>
              </article>
              <article class="card" data-diagram-id="two" data-position-status="requires_review"
                       data-side-marker-status="marker_missing" data-fen-blocker="board_grid_not_detected">
                <img src="assets/board-two.png" alt="board crop">
                <p>Review reason: board_grid_not_detected</p>
                <p>FEN blocker: board_grid_not_detected</p>
              </article>
            </body></html>
            """,
            encoding="utf-8",
        )
        self._write_test_png(semantic_dir / "assets" / "board-one.png")
        self._write_test_png(semantic_dir / "assets" / "board-two.png")
        return index_path

    def _register_pdf_layout_preview_artifact(self, job_id: str, html_text: str) -> Path:
        job_root = self._artifact_root(job_id)
        report_dir = job_root / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        preview_path = report_dir / "pdf_layout_preview.html"
        preview_path.write_text(html_text, encoding="utf-8")
        artifact = app_module._local_artifact_metadata(job_id, ArtifactKind.REPORT, preview_path)
        artifact["content_type"] = "text/html; charset=utf-8"
        artifact["download_url"] = f"/convert/artifact/{job_id}/pdf_layout_preview"
        artifact["label"] = "PDF layout preview"
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["artifacts"]["pdf_layout_preview"] = artifact
        return preview_path

    def _yusupov_style_training_data_gap(self) -> dict[str, object]:
        fixture_roots = [Path("reference_inputs"), Path("example")]
        candidates: list[str] = []
        for root in fixture_roots:
            if not root.exists():
                continue
            for pattern in ("*yusupov*.pdf", "*Yusupov*.pdf", "*yusupov*.html", "*Yusupov*.html"):
                candidates.extend(str(path) for path in root.rglob(pattern))
        def has_verified_sidecars(source_path: Path) -> bool:
            evidence_roots = [
                source_path.with_suffix(""),
                source_path.parent / f"{source_path.stem}_evidence",
                source_path.parent / f"{source_path.stem}_semantic_chess_html",
            ]
            return any(
                (root / "data" / "artifact_manifest.json").is_file()
                and (root / "reports" / "final_reader_health_gate.json").is_file()
                for root in evidence_roots
            )
        usable = [
            path
            for path in candidates
            if Path(path).is_file()
            and any(token in Path(path).name.lower() for token in ("yusupov", "build-up-your-chess"))
            and has_verified_sidecars(Path(path))
        ]
        return {
            "status": "TRAINING_DATA_GAP" if not usable else "ready",
            "fixture_family": "yusupov_style_chess_training",
            "candidate_paths": candidates,
            "usable_source_count": len(usable),
            "required_evidence": [
                "source PDF or source HTML",
                "artifact_manifest.json",
                "final_reader_health_gate.json",
                "trusted side-marker evidence or explicit blocker",
            ],
            "reason": "" if usable else "No verified Yusupov-style source fixture with crop/FEN/side-marker evidence is present.",
        }

    def test_store_extra_artifacts_builds_final_reader_sidecar_from_diagram_records(self) -> None:
        job_id = "real-extra-artifact-sidecar"
        self._artifact_root(job_id)
        fen = "8/8/8/8/8/8/4K3/7k w - - 0 1"
        stored = app_module._store_extra_conversion_artifacts(
            job_id,
            [
                {
                    "key": "chess_pgn_html",
                    "filename": "chess_games.html",
                    "content_type": "text/html; charset=utf-8",
                    "label": "HTML PGN/FEN",
                    "data": "<!doctype html><html><body><p>Source evidence HTML</p></body></html>",
                },
                {
                    "key": "chess_diagrams",
                    "filename": "chess_diagrams.json",
                    "content_type": "application/json; charset=utf-8",
                    "label": "Chess diagrams",
                    "data": json.dumps(
                        {
                            "diagram_count": 1,
                            "records": [
                                {
                                    "id": "diagram-one",
                                    "page": 1,
                                    "caption": "Diagram 1",
                                    "fen": fen,
                                    "full_fen": fen,
                                    "full_fen_status": "accepted",
                                    "requires_review": False,
                                    "side_to_move": "w",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_symbol": "△",
                                    "board_crop_path": "data:image/png;base64,AA==",
                                    "side_marker_crop_path": "data:image/png;base64,BB==",
                                    "confidence": 0.97,
                                }
                            ],
                        }
                    ),
                },
            ],
        )
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
                "artifacts": stored,
            }

        html_artifact = stored["chess_pgn_html"]
        source_html = Path(str(html_artifact["location"]))
        semantic_dir = source_html.parents[1] / "semantic_chess_html"
        manifest = json.loads((semantic_dir / "data" / "artifact_manifest.json").read_text(encoding="utf-8"))
        health_gate = json.loads((semantic_dir / "reports" / "final_reader_health_gate.json").read_text(encoding="utf-8"))
        status_response = self.client.get(f"/convert/status/{job_id}")
        html_response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")

        self.assertTrue((semantic_dir / "index.html").is_file())
        self.assertEqual(manifest["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(manifest["pipeline_mode"], "pdf_two_crop_reader")
        self.assertEqual(manifest["side_unknown_count"], 0)
        self.assertEqual(manifest["trusted_marker_count"], 1)
        self.assertEqual(manifest["side_marker_crop_count"], 1)
        self.assertEqual(manifest["board_crop_count"], 1)
        self.assertEqual(manifest["empty_img_src_count"], 0)
        self.assertEqual(manifest["diagrams_total"], 1)
        self.assertEqual(manifest["fen_accepted"], 1)
        self.assertEqual(health_gate["decision"], "pass")
        self.assertEqual(health_gate["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(html_artifact["final_reader_available"])
        self.assertEqual(html_artifact["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(str(html_artifact["final_reader_path"]).endswith("semantic_chess_html\\index.html") or str(html_artifact["final_reader_path"]).endswith("semantic_chess_html/index.html"))
        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        self.assertEqual(payload["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(payload["final_reader_available"])
        self.assertEqual(payload["chess_files"]["chess_pgn_html"]["download_url"], f"/convert/artifact/{job_id}/chess_pgn_html")
        self.assertEqual(html_response.status_code, 200)
        html_text = html_response.get_data(as_text=True)
        self.assertIn('data-artifact-type="final_pdf_two_crop_reader"', html_text)
        self.assertIn('data-side-marker-status="trusted_marker"', html_text)
        self.assertIn(fen, html_text)
        self.assertNotIn("Source evidence HTML", html_text)

    def test_store_extra_artifacts_opens_reader_with_component_review_without_accepted_fen_or_marker(self) -> None:
        job_id = "real-extra-artifact-blocked-sidecar"
        self._artifact_root(job_id)
        stored = app_module._store_extra_conversion_artifacts(
            job_id,
            [
                {
                    "key": "chess_pgn_html",
                    "filename": "chess_games.html",
                    "content_type": "text/html; charset=utf-8",
                    "label": "HTML PGN/FEN",
                    "data": "<!doctype html><html><body><p>Bad source evidence HTML</p></body></html>",
                },
                {
                    "key": "chess_diagrams",
                    "filename": "chess_diagrams.json",
                    "content_type": "application/json; charset=utf-8",
                    "label": "Chess diagrams",
                    "data": json.dumps(
                        {
                            "diagram_count": 1,
                            "records": [
                                {
                                    "id": "diagram-review",
                                    "page": 1,
                                    "caption": "Diagram 1",
                                    "fen_candidate": "",
                                    "requires_review": True,
                                    "side_to_move": "unknown",
                                    "side_marker_status": "",
                                    "board_crop_path": "data:image/png;base64,AA==",
                                    "confidence": 0.41,
                                    "warnings": ["side_marker_missing"],
                                }
                            ],
                        }
                    ),
                },
            ],
        )
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
                "artifacts": stored,
            }

        response = self.client.get(f"/convert/artifact/{job_id}/chess_reader")
        status_response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-KindleMaster-Reader-Health"), "component-review")
        self.assertNotIn("Bad source evidence HTML", response.get_data(as_text=True))
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertTrue(status_payload["final_reader_available"])
        self.assertIn("fen_accepted_zero", status_payload["final_reader_blockers"])
        self.assertIn("missing_side_marker_evidence", status_payload["final_reader_blockers"])
        self.assertTrue(status_payload["chess_files"]["chess_reader"]["available"])

    def test_yusupov_style_conversion_opens_component_review_reader_and_keeps_preview_audit_only(self) -> None:
        job_id = "yusupov-style-artifact-e2e-blocked"
        self._artifact_root(job_id)
        stored = app_module._store_extra_conversion_artifacts(
            job_id,
            [
                {
                    "key": "chess_pgn_html",
                    "filename": "chess_games.html",
                    "content_type": "text/html; charset=utf-8",
                    "label": "HTML PGN/FEN",
                    "data": "<!doctype html><html><body><p>Bad Yusupov-style source evidence HTML</p></body></html>",
                },
                {
                    "key": "pdf_layout_preview",
                    "filename": "pdf_layout_preview.html",
                    "content_type": "text/html; charset=utf-8",
                    "label": "PDF layout preview",
                    "data": "<!doctype html><html><body>PDF layout audit only</body></html>",
                },
                {
                    "key": "chess_diagrams",
                    "filename": "chess_diagrams.json",
                    "content_type": "application/json; charset=utf-8",
                    "label": "Chess diagrams",
                    "data": json.dumps(
                        {
                            "schema": "kindlemaster.yusupov_style_chess_diagrams.v1",
                            "diagram_count": 2,
                            "records": [
                                {
                                    "id": "yusupov-style-001",
                                    "page": 14,
                                    "caption": "Diagram 14-1",
                                    "fen": "",
                                    "fen_candidate": "",
                                    "requires_review": True,
                                    "side_to_move": "unknown",
                                    "side_marker_status": "",
                                    "board_crop_path": "data:image/png;base64,AA==",
                                    "confidence": 0.22,
                                    "warnings": ["side_marker_missing", "fen_not_accepted"],
                                },
                                {
                                    "id": "yusupov-style-002",
                                    "page": 15,
                                    "caption": "Diagram 15-1",
                                    "fen": "",
                                    "fen_candidate": "",
                                    "requires_review": True,
                                    "side_to_move": "unknown",
                                    "side_marker_status": "",
                                    "board_crop_path": "data:image/png;base64,BB==",
                                    "confidence": 0.18,
                                    "warnings": ["side_marker_missing", "fen_not_accepted"],
                                },
                            ],
                        }
                    ),
                },
                {
                    "key": "chess_glyph_diagnostics",
                    "filename": "chess_glyph_diagnostics.json",
                    "content_type": "application/json; charset=utf-8",
                    "label": "Chess glyph diagnostics",
                    "data": json.dumps({"status": "review_required", "accepted_pgn": 0, "fen_accepted": 0}),
                },
            ],
        )
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB ready.",
                "source_type": "pdf",
                "filename": "yusupov-style-training.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "yusupov-style-training.epub",
                "metadata": {
                    "source_type": "pdf",
                    "profile": "chess_training",
                    "chess_pgn": {
                        "candidate_game_count": 0,
                        "valid_pgn_count": 0,
                        "legal_pgn_count": 0,
                        "strict_export_count": 0,
                        "exportable_pgn_count": 0,
                        "manual_review_count": 2,
                    },
                },
                "output_size_bytes": 0,
                "error": "",
                "error_code": "",
                "artifacts": stored,
            }

        status_response = self.client.get(f"/convert/status/{job_id}")
        html_response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")
        preview_response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertIn("chess_pgn_html", status_payload["chess_files"])
        self.assertTrue(status_payload["chess_files"]["chess_reader"]["available"])
        self.assertTrue(status_payload["chess_files"]["chess_pgn_html"]["available"])
        self.assertTrue(status_payload["final_reader_available"])
        self.assertIn("fen_accepted_zero", status_payload["final_reader_blockers"])
        self.assertIn("missing_side_marker_evidence", status_payload["final_reader_blockers"])
        self.assertIn("chess_diagrams", status_payload["artifacts"])
        self.assertIn("chess_glyph_diagnostics", status_payload["artifacts"])
        self.assertIn("pdf_layout_preview", status_payload["artifacts"])
        self.assertNotIn("chess_diagrams", status_payload["chess_files"])
        self.assertNotIn("chess_glyph_diagnostics", status_payload["chess_files"])

        self.assertEqual(html_response.status_code, 200)
        html_payload = status_payload
        self.assertNotIn("Bad Yusupov-style source evidence HTML", html_response.get_data(as_text=True))
        self.assertIn("fen_accepted_zero", html_payload["final_reader_blockers"])
        self.assertIn("missing_side_marker_evidence", html_payload["final_reader_blockers"])

        self.assertEqual(preview_response.status_code, 200)
        preview_text = preview_response.get_data(as_text=True)
        self.assertIn("To nie jest finalny reader szachowy", preview_text)
        self.assertIn("Artefakt audytowy", preview_text)
        self.assertNotEqual(html_response.get_data(as_text=True), preview_text)
        self.assertNotIn('data-primary-chess-artifact="pdf_layout_preview"', preview_text)

    def test_yusupov_style_real_fixture_gap_is_reported_without_fabricating_labels(self) -> None:
        gap = self._yusupov_style_training_data_gap()
        gap_path = Path(self.store_temp_dir.name) / "yusupov_style_training_data_gap.json"
        gap_path.write_text(json.dumps(gap, indent=2), encoding="utf-8")

        payload = json.loads(gap_path.read_text(encoding="utf-8"))
        if payload["status"] == "TRAINING_DATA_GAP":
            self.assertEqual(payload["usable_source_count"], 0)
            self.assertIn("No verified Yusupov-style source fixture", payload["reason"])
            self.assertIn("trusted side-marker evidence or explicit blocker", payload["required_evidence"])
        else:
            self.assertGreater(payload["usable_source_count"], 0)

    def test_final_reader_missing_health_gate_is_blocked(self) -> None:
        job_id = "routing-missing-final-reader-health-gate"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Source report should not be served.</p></body></html>",
        )
        job_root = source_html.parents[1]
        semantic_dir = job_root / "semantic_chess_html"
        data_dir = semantic_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "artifact_manifest.json").write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_study.artifact_manifest.v1",
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "pdf_two_crop_reader",
                    "diagrams_total": 1,
                    "side_unknown_count": 0,
                    "trusted_marker_count": 1,
                    "side_marker_crop_count": 1,
                    "board_crop_count": 1,
                    "empty_img_src_count": 0,
                    "fen_accepted": 1,
                }
            ),
            encoding="utf-8",
        )
        (semantic_dir / "index.html").write_text(
            """
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article class="card" data-position-status="accepted" data-side-marker-status="trusted_marker">
                <p>This HTML must not be served without health gate.</p>
              </article>
            </body></html>
            """,
            encoding="utf-8",
        )
        response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")
        status_response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "final_reader_health_gate_failed")
        self.assertIn("final_reader_health_gate_missing", payload["final_reader_health_gate"]["blockers"])
        self.assertNotIn("This HTML must not be served", response.get_data(as_text=True))
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertFalse(status_payload["final_reader_available"])
        self.assertIn("final_reader_health_gate_missing", status_payload["final_reader_blockers"])

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
            "<!doctype html><html><body><p>Source report should not be served.</p><p>Side to move: unknown</p></body></html>",
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
        job_root = source_html.parents[1]
        report_dir = job_root / "report"
        (report_dir / "source_html_quality_gate.json").write_text(
            json.dumps(
                {
                    "decision": "reject_degraded_source_html",
                    "source_html_evidence_only": True,
                    "used_as_final_reader": False,
                    "source_html_evidence_path": str(source_html),
                    "reasons": ["diagram_image_sources_degraded"],
                }
            ),
            encoding="utf-8",
        )
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
        reports_dir = semantic_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "final_reader_health_gate.json").write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
                    "decision": "pass",
                    "status": "PASS",
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "source_html_semantic_reader",
                    "diagram_cards_count": 2,
                    "side_unknown_count": 0,
                    "data_side_marker_attr_count": 2,
                    "trusted_marker_count": 2,
                    "side_marker_crop_count": 2,
                    "board_crop_count": 2,
                    "empty_img_src_count": 0,
                    "asset_missing_empty_src_count": 0,
                    "fen_accepted": 2,
                    "blockers": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        pdf_preview_path = report_dir / "pdf_layout_preview.html"
        pdf_preview_path.write_text("<!doctype html><html><body>PDF layout audit</body></html>", encoding="utf-8")
        pdf_preview_artifact = app_module._local_artifact_metadata(job_id, ArtifactKind.REPORT, pdf_preview_path)
        pdf_preview_artifact["content_type"] = "text/html; charset=utf-8"
        pdf_preview_artifact["download_url"] = f"/convert/artifact/{job_id}/pdf_layout_preview"
        pdf_preview_artifact["label"] = "PDF layout preview"
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["artifacts"]["pdf_layout_preview"] = pdf_preview_artifact
        index_path = semantic_dir / "index.html"
        index_path.write_text(
            """
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article class="card" data-position-status="accepted" data-side-marker-status="trusted_marker"
                       data-has-board-crop="true" data-has-side-marker-crop="true">
                <img src="assets/board.png" alt="board crop">
                <p>Side to move: white</p>
              </article>
              Final reader
            </body></html>
            """,
            encoding="utf-8",
        )
        self._write_test_png(semantic_dir / "assets" / "board.png")

        response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")
        status_response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        response_text = response.get_data(as_text=True)
        self.assertIn("Final reader", response_text)
        self.assertIn('data-side-marker-status="trusted_marker"', response_text)
        self.assertNotIn("Source report should not be served.", response_text)
        self.assertNotIn("Side to move: unknown", response_text)
        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        self.assertEqual(payload["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(payload["final_reader_available"])
        self.assertEqual(payload["final_reader_blockers"], [])
        self.assertEqual(payload["final_reader_health"]["side_unknown_count"], 0)
        self.assertEqual(payload["final_reader_health"]["trusted_marker_count"], 2)
        self.assertEqual(payload["final_reader_health"]["empty_img_src_count"], 0)
        self.assertEqual(payload["side_unknown_count"], 0)
        self.assertEqual(payload["trusted_marker_count"], 2)
        self.assertEqual(payload["empty_img_src_count"], 0)
        self.assertTrue(payload["final_reader_path"].endswith("semantic_chess_html\\index.html") or payload["final_reader_path"].endswith("semantic_chess_html/index.html"))
        self.assertTrue(payload["source_html_evidence_path"].endswith("chess_games.html"))
        self.assertEqual(payload["source_html_quality_gate"]["decision"], "reject_degraded_source_html")
        self.assertTrue(payload["source_html_quality_gate"]["source_html_evidence_only"])
        self.assertFalse(payload["source_html_quality_gate"]["used_as_final_reader"])
        self.assertEqual(payload["conversion"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(payload["conversion"]["final_reader_available"])
        self.assertEqual(payload["artifacts"]["chess_pgn_html"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(payload["artifacts"]["chess_pgn_html"]["download_url"], f"/convert/artifact/{job_id}/chess_pgn_html")
        self.assertTrue(payload["artifacts"]["chess_pgn_html"]["final_reader_available"])
        self.assertTrue(payload["artifacts"]["chess_pgn_html"]["final_reader_path"].endswith("semantic_chess_html\\index.html") or payload["artifacts"]["chess_pgn_html"]["final_reader_path"].endswith("semantic_chess_html/index.html"))
        self.assertTrue(payload["artifacts"]["chess_pgn_html"]["source_html_evidence_path"].endswith("chess_games.html"))
        self.assertEqual(payload["artifacts"]["pdf_layout_preview"]["download_url"], f"/convert/artifact/{job_id}/pdf_layout_preview")
        self.assertNotEqual(payload["artifacts"]["pdf_layout_preview"].get("artifact_type"), "final_pdf_two_crop_reader")

    def test_status_exposes_pgn_and_final_html_as_chess_files(self) -> None:
        job_id = "routing-pgn-and-final-reader"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Source evidence report.</p></body></html>",
        )
        self._write_final_reader_sidecar(source_html, diagrams_total=3)
        self._register_chess_pgn_artifact(
            job_id,
            '[Event "Study"]\n[Date "????.??.??"]\n[Round "?"]\n[White "White"]\n[Black "Black"]\n[Result "*"]\n\n1. e4 e5 *\n',
        )
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["metadata"]["chess_pgn"] = {
                "candidate_game_count": 1,
                "valid_pgn_count": 1,
                "legal_pgn_count": 1,
                "strict_export_count": 1,
                "exportable_pgn_count": 1,
                "manual_review_count": 0,
            }

        status_response = self.client.get(f"/convert/status/{job_id}")
        pgn_response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn")

        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        self.assertIn("chess_files", payload)
        self.assertEqual(payload["chess_files"]["chess_pgn"]["label"], "PGN")
        self.assertTrue(payload["chess_files"]["chess_pgn"]["available"])
        self.assertEqual(payload["chess_files"]["chess_pgn"]["download_url"], f"/convert/artifact/{job_id}/chess_pgn")
        self.assertEqual(payload["chess_files"]["chess_pgn"]["exportable_pgn_count"], 1)
        self.assertEqual(payload["chess_files"]["chess_pgn_html"]["label"], "HTML PGN/FEN")
        self.assertTrue(payload["chess_files"]["chess_pgn_html"]["available"])
        self.assertEqual(payload["chess_files"]["chess_pgn_html"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(payload["chess_files"]["chess_pgn_html"]["download_url"], f"/convert/artifact/{job_id}/chess_pgn_html")
        self.assertEqual(payload["artifacts"]["chess_pgn"]["status"], "available")
        self.assertEqual(payload["artifacts"]["chess_pgn_html"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(pgn_response.status_code, 200)
        self.assertEqual(pgn_response.mimetype, "application/x-chess-pgn")
        self.assertIn('[Event "Study"]', pgn_response.get_data(as_text=True))
        self.assertIn("attachment", pgn_response.headers.get("Content-Disposition", ""))
        pgn_response.close()

    def test_yusupov_reader_serves_job_root_crops_and_reports_missing_assets(self) -> None:
        job_id = "routing-yusupov-reader-assets"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body>Source evidence.</body></html>",
        )
        semantic_index = self._write_final_reader_sidecar(source_html, diagrams_total=1)
        job_root = source_html.parents[1]
        board_path = job_root / "review" / "chess_fen" / "two_crop" / "notation_layout_p010_01_board.png"
        marker_path = job_root / "review" / "chess_fen" / "two_crop" / "notation_layout_p010_01_marker.png"
        self._write_test_png(board_path)
        self._write_test_png(marker_path)
        semantic_index.write_text(
            """
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article class="card" data-position-status="needs_review"
                       data-side-marker-status="trusted_marker"
                       data-has-board-crop="true" data-has-side-marker-crop="true">
                <img src="review/chess_fen/two_crop/notation_layout_p010_01_board.png" alt="source crop">
                <img src="review/chess_fen/two_crop/notation_layout_p010_01_marker.png" alt="side marker crop">
                <img src="review/chess_fen/two_crop/notation_layout_p010_01_overlay.png" alt="debug overlay">
              </article>
            </body></html>
            """,
            encoding="utf-8",
        )

        reader_response = self.client.get(f"/convert/artifact/{job_id}/chess_reader")
        status_response = self.client.get(f"/convert/status/{job_id}")
        board_response = self.client.get(
            f"/convert/artifact/{job_id}/chess_reader_asset/review/chess_fen/two_crop/{board_path.name}"
        )
        marker_response = self.client.get(
            f"/convert/artifact/{job_id}/chess_reader_asset/review/chess_fen/two_crop/{marker_path.name}"
        )

        self.assertEqual(reader_response.status_code, 200)
        reader_html = reader_response.get_data(as_text=True)
        self.assertIn(
            f'/convert/artifact/{job_id}/chess_reader_asset/review/chess_fen/two_crop/{board_path.name}',
            reader_html,
        )
        self.assertEqual(board_response.status_code, 200)
        self.assertEqual(marker_response.status_code, 200)
        self.assertEqual(board_response.data, self.TEST_PNG)
        health = status_response.get_json()["final_reader_health"]
        self.assertEqual(health["referenced_image_asset_count"], 3)
        self.assertEqual(health["missing_required_asset_count"], 0)
        self.assertEqual(health["missing_optional_asset_count"], 1)
        self.assertEqual(health["status"], "PASS_WITH_WARNINGS")

        board_response.close()
        marker_response.close()
        board_path.unlink()
        failed_health = self.client.get(f"/convert/status/{job_id}").get_json()["final_reader_health"]
        self.assertEqual(failed_health["status"], "FAIL")
        self.assertEqual(failed_health["missing_required_asset_count"], 1)
        self.assertIn("missing_reader_assets", failed_health["blockers"])

        reader_response.close()
        status_response.close()

    def test_yusupov_reader_recovers_two_crop_assets_from_retained_zip(self) -> None:
        job_id = "routing-yusupov-reader-zip-recovery"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body>Source evidence.</body></html>",
        )
        semantic_index = self._write_final_reader_sidecar(source_html, diagrams_total=1)
        job_root = source_html.parents[1]
        report_dir = job_root / "report"
        (report_dir / "recovery.quality.json").write_text(
            json.dumps({"job": {"job_id": job_id, "status": "ready", "filename": "Yusupov.pdf"}}),
            encoding="utf-8",
        )
        (report_dir / "chess_diagrams.json").write_text(
            json.dumps({"schema": "kindlemaster.yusupov_style_chess_diagrams.v1", "diagrams": []}),
            encoding="utf-8",
        )
        crop_root = "review/chess_fen/two_crop"
        board_name = "notation_layout_p010_01_board.png"
        marker_name = "notation_layout_p010_01_marker.png"
        overlay_name = "notation_layout_p010_01_overlay.png"
        archive_path = report_dir / "chess_fen_two_crop_review_artifacts.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{crop_root}/{board_name}", self.TEST_PNG)
            archive.writestr(f"{crop_root}/{marker_name}", self.TEST_PNG)
            archive.writestr(f"{crop_root}/{overlay_name}", self.TEST_PNG)
            archive.writestr("../../outside.png", self.TEST_PNG)
            archive.writestr(f"{crop_root}/unexpected.txt", b"not an image")
        semantic_index.write_text(
            f"""
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article class="card">
                <img src="{crop_root}/{board_name}" alt="source crop">
                <img src="{crop_root}/{marker_name}" alt="side marker crop">
                <img src="{crop_root}/{overlay_name}" alt="debug overlay">
              </article>
            </body></html>
            """,
            encoding="utf-8",
        )

        target_dir = job_root / "review" / "chess_fen" / "two_crop"
        self.assertFalse(target_dir.exists())
        rebuilt = app_module._rebuild_job_from_local_artifact_dir(job_root)
        self.assertIsNotNone(rebuilt)
        assert rebuilt is not None
        self.assertIn("chess_diagrams", rebuilt["artifacts"])
        self.assertIn("chess_fen_two_crop_review_artifacts", rebuilt["artifacts"])

        self.assertTrue(target_dir.exists())
        reader_response = self.client.get(f"/convert/artifact/{job_id}/chess_reader")
        status_response = self.client.get(f"/convert/status/{job_id}")
        asset_responses = [
            self.client.get(f"/convert/artifact/{job_id}/chess_reader_asset/{crop_root}/{name}")
            for name in (board_name, marker_name, overlay_name)
        ]

        self.assertEqual(reader_response.status_code, 200)
        self.assertEqual([response.status_code for response in asset_responses], [200, 200, 200])
        self.assertTrue(all(response.data == self.TEST_PNG for response in asset_responses))
        health = status_response.get_json()["final_reader_health"]
        self.assertEqual(health["status"], "PASS")
        self.assertEqual(health["missing_required_asset_count"], 0)
        self.assertEqual(health["missing_optional_asset_count"], 0)
        self.assertEqual(health["asset_recovery"]["status"], "recovered")
        self.assertEqual(health["asset_recovery"]["recovered_count"], 3)
        self.assertEqual(health["asset_recovery"]["ignored_count"], 2)
        self.assertFalse((job_root / "outside.png").exists())
        mtimes = {path.name: path.stat().st_mtime_ns for path in target_dir.glob("*.png")}

        repeated_health = self.client.get(f"/convert/status/{job_id}").get_json()["final_reader_health"]
        self.assertTrue(repeated_health["asset_recovery"]["cached"])
        self.assertEqual(mtimes, {path.name: path.stat().st_mtime_ns for path in target_dir.glob("*.png")})

        reader_response.close()
        status_response.close()
        for response in asset_responses:
            response.close()

    def test_status_exposes_engine_analysis_gate_summary(self) -> None:
        job_id = "routing-engine-analysis-gate"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Source evidence report.</p></body></html>",
        )
        self._write_final_reader_sidecar(source_html, diagrams_total=2)
        self._register_engine_analysis_gate_artifact(
            job_id,
            {
                "schema": "kindlemaster.chess_engine.gate.v1",
                "diagram_count": 2,
                "eligible_count": 1,
                "analyzed_count": 0,
                "unavailable_count": 2,
                "engine_available": False,
                "top_reasons": [{"reason": "fen_not_accepted", "count": 1}, {"reason": "engine_unavailable", "count": 1}],
                "engine_reader_available": False,
                "availability": "unavailable",
                "message": "Engine analysis unavailable. Reason: fen_not_accepted.",
            },
        )

        status_response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        self.assertEqual(payload["engine_analysis_availability"], "unavailable")
        self.assertFalse(payload["engine_reader_available"])
        self.assertEqual(payload["engine_analysis_gate"]["diagram_count"], 2)
        self.assertEqual(payload["engine_analysis_gate"]["top_reasons"][0]["reason"], "fen_not_accepted")
        self.assertEqual(payload["quality_state"]["engine_analysis_gate"]["availability"], "unavailable")
        self.assertEqual(payload["conversion"]["engine_analysis_gate"]["unavailable_count"], 2)

    def test_chess_download_endpoints_expose_pgn_final_reader_and_audit_preview(self) -> None:
        job_id = "routing-chess-download-e2e"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Source evidence report should not be served.</p></body></html>",
        )
        self._write_final_reader_sidecar(source_html, diagrams_total=2)
        self._register_chess_pgn_artifact(
            job_id,
            '[Event "Study"]\n[Date "????.??.??"]\n[Round "?"]\n[White "White"]\n[Black "Black"]\n[Result "*"]\n\n1. e4 e5 *\n',
        )
        self._register_pdf_layout_preview_artifact(
            job_id,
            "<!doctype html><html><body>PDF layout audit only</body></html>",
        )
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["metadata"]["chess_pgn"] = {
                "candidate_game_count": 1,
                "valid_pgn_count": 1,
                "legal_pgn_count": 1,
                "strict_export_count": 1,
                "exportable_pgn_count": 1,
                "manual_review_count": 0,
            }

        status_response = self.client.get(f"/convert/status/{job_id}")
        pgn_response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn")
        reader_response = self.client.get(f"/convert/artifact/{job_id}/chess_reader")
        html_response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn_html")
        preview_response = self.client.get(f"/convert/artifact/{job_id}/pdf_layout_preview")

        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertEqual(status_payload["chess_files"]["chess_pgn"]["label"], "PGN")
        self.assertTrue(status_payload["chess_files"]["chess_pgn"]["available"])
        self.assertEqual(status_payload["chess_files"]["chess_pgn"]["download_url"], f"/convert/artifact/{job_id}/chess_pgn")
        self.assertEqual(status_payload["chess_files"]["chess_reader"]["label"], "Chess Reader")
        self.assertTrue(status_payload["chess_files"]["chess_reader"]["available"])
        self.assertEqual(status_payload["chess_files"]["chess_reader"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(status_payload["chess_files"]["chess_reader"]["download_url"], f"/convert/artifact/{job_id}/chess_reader")
        self.assertEqual(status_payload["chess_files"]["chess_pgn_html"]["label"], "HTML PGN/FEN")
        self.assertTrue(status_payload["chess_files"]["chess_pgn_html"]["available"])
        self.assertEqual(status_payload["chess_files"]["chess_pgn_html"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(status_payload["chess_files"]["chess_pgn_html"]["download_url"], f"/convert/artifact/{job_id}/chess_pgn_html")
        self.assertEqual(status_payload["final_reader_health"]["diagram_cards_count"], 2)
        self.assertEqual(status_payload["final_reader_health"]["empty_img_src_count"], 0)
        self.assertEqual(status_payload["final_reader_health"]["side_unknown_count"], 0)
        self.assertEqual(status_payload["final_reader_health"]["trusted_marker_count"], 2)
        self.assertEqual(status_payload["artifacts"]["pdf_layout_preview"]["label"], "PDF layout preview")
        self.assertNotEqual(status_payload["artifacts"]["pdf_layout_preview"].get("artifact_type"), "final_pdf_two_crop_reader")

        self.assertEqual(pgn_response.status_code, 200)
        self.assertEqual(pgn_response.mimetype, "application/x-chess-pgn")
        self.assertIn('[Event "Study"]', pgn_response.get_data(as_text=True))
        self.assertIn("attachment", pgn_response.headers.get("Content-Disposition", ""))

        self.assertEqual(reader_response.status_code, 200)
        self.assertEqual(reader_response.headers.get("X-KindleMaster-Artifact-View"), "chess_reader")
        reader_text = reader_response.get_data(as_text=True)
        self.assertIn('data-artifact-type="final_pdf_two_crop_reader"', reader_text)
        self.assertIn('data-side-marker-status="trusted_marker"', reader_text)
        self.assertIn("side_to_move: white", reader_text)
        self.assertIn("FEN: 8/8/8/8/8/8/4K3/4k3 w - - 0 1", reader_text)
        self.assertIn("Review reason: board_grid_not_detected", reader_text)
        self.assertNotIn('src=""', reader_text)
        self.assertNotIn("Source evidence report should not be served.", reader_text)
        self.assertNotIn("PDF layout audit only", reader_text)
        self.assertNotIn("Side to move: unknown", reader_text)

        self.assertEqual(html_response.status_code, 200)
        html_text = html_response.get_data(as_text=True)
        self.assertIn('data-artifact-type="final_pdf_two_crop_reader"', html_text)
        self.assertNotIn("Source evidence report should not be served.", html_text)

        self.assertEqual(preview_response.status_code, 200)
        preview_text = preview_response.get_data(as_text=True)
        self.assertIn("To nie jest finalny reader szachowy", preview_text)
        self.assertIn(f'href="/convert/artifact/{job_id}/chess_reader"', preview_text)
        self.assertIn('data-primary-chess-artifact="chess_reader"', preview_text)
        self.assertIn("PDF layout audit only", preview_text)
        self.assertNotIn('data-primary-chess-artifact="pdf_layout_preview"', preview_text)
        pgn_response.close()

    def test_missing_accepted_pgn_returns_review_safe_status(self) -> None:
        job_id = "routing-pgn-unavailable"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Source evidence report.</p></body></html>",
        )
        self._write_final_reader_sidecar(source_html, diagrams_total=2)
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["metadata"]["chess_pgn"] = {
                "candidate_game_count": 2,
                "valid_pgn_count": 0,
                "strict_export_count": 0,
                "exportable_pgn_count": 0,
                "manual_review_count": 2,
            }

        status_response = self.client.get(f"/convert/status/{job_id}")
        pgn_response = self.client.get(f"/convert/artifact/{job_id}/chess_pgn")

        self.assertEqual(status_response.status_code, 200)
        payload = status_response.get_json()
        chess_pgn = payload["chess_files"]["chess_pgn"]
        self.assertEqual(chess_pgn["label"], "PGN")
        self.assertFalse(chess_pgn["available"])
        self.assertEqual(chess_pgn["reason"], "no_accepted_pgn_records")
        self.assertEqual(chess_pgn["message"], "PGN niedostepny: brak zaakceptowanych partii")
        self.assertEqual(chess_pgn["candidate_game_count"], 2)
        self.assertEqual(chess_pgn["manual_review_count"], 2)
        self.assertEqual(chess_pgn["download_url"], "")
        self.assertEqual(payload["chess_files"]["chess_pgn_html"]["artifact_type"], "final_pdf_two_crop_reader")
        self.assertEqual(pgn_response.status_code, 409)
        error_payload = pgn_response.get_json()
        self.assertEqual(error_payload["error_code"], "chess_pgn_unavailable")
        self.assertEqual(error_payload["chess_pgn"]["reason"], "no_accepted_pgn_records")
        self.assertEqual(error_payload["chess_pgn"]["message"], "PGN niedostepny: brak zaakceptowanych partii")

    def test_partial_final_reader_with_mass_unknown_opens_as_component_review(self) -> None:
        job_id = "routing-unhealthy-final-reader"
        source_html = self._register_chess_html_job(
            job_id,
            "<!doctype html><html><body><p>Evidence report should not be served.</p></body></html>",
            manifest={
                "schema": "kindlemaster.chess_study.artifact_manifest.v1",
                "artifact_type": "source_html_evidence_only",
                "pipeline_mode": "source_html_evidence_report",
            },
        )
        job_root = source_html.parents[1]
        report_dir = job_root / "report"
        pdf_preview_path = report_dir / "pdf_layout_preview.html"
        pdf_preview_path.write_text("<!doctype html><html><body>PDF preview is audit only</body></html>", encoding="utf-8")
        pdf_preview_artifact = app_module._local_artifact_metadata(job_id, ArtifactKind.REPORT, pdf_preview_path)
        pdf_preview_artifact["content_type"] = "text/html; charset=utf-8"
        pdf_preview_artifact["download_url"] = f"/convert/artifact/{job_id}/pdf_layout_preview"
        pdf_preview_artifact["label"] = "PDF layout preview"
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id]["artifacts"]["pdf_layout_preview"] = pdf_preview_artifact
        semantic_dir = job_root / "semantic_chess_html"
        data_dir = semantic_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "artifact_manifest.json").write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_study.artifact_manifest.v1",
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "pdf_two_crop_reader",
                    "diagrams_total": 4,
                    "side_unknown_count": 0,
                    "trusted_marker_count": 0,
                    "empty_img_src_count": 0,
                    "fen_accepted": 0,
                }
            ),
            encoding="utf-8",
        )
        (semantic_dir / "index.html").write_text(
            """
            <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
              <article class="card" data-position-status="accepted"><img src=""><p>Side to move: unknown</p></article>
              <article class="card" data-position-status="accepted"><img src=""><p>Side to move: unknown</p></article>
              <article class="card" data-position-status="accepted"><img src=""><p>Side to move: unknown</p></article>
              <article class="card" data-position-status="accepted"><img src=""><p>Side to move: unknown</p></article>
            </body></html>
            """,
            encoding="utf-8",
        )
        reports_dir = semantic_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "final_reader_health_gate.json").write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
                    "decision": "fail",
                    "status": "FAIL",
                    "artifact_type": "final_pdf_two_crop_reader",
                    "pipeline_mode": "pdf_two_crop_reader",
                    "diagram_cards_count": 4,
                    "side_unknown_count": 4,
                    "data_side_marker_attr_count": 0,
                    "trusted_marker_count": 0,
                    "side_marker_crop_count": 0,
                    "board_crop_count": 0,
                    "empty_img_src_count": 4,
                    "asset_missing_empty_src_count": 4,
                    "blockers": ["mass_side_to_move_unknown", "empty_img_src", "missing_side_marker_evidence"],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )

        response = self.client.get(f"/convert/artifact/{job_id}/chess_reader")
        status_response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-KindleMaster-Artifact-View"), "chess_reader")
        self.assertEqual(response.headers.get("X-KindleMaster-Reader-Health"), "component-review")
        response_text = response.get_data(as_text=True)
        self.assertIn('data-artifact-type="final_pdf_two_crop_reader"', response_text)
        self.assertIn("Side to move unavailable", response_text)
        self.assertNotIn("Side to move: unknown", response_text)
        self.assertNotIn("mass_side_to_move_unknown", response_text)
        self.assertNotIn('src=""', response_text)
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertEqual(status_payload["artifact_type"], "final_pdf_two_crop_reader")
        self.assertTrue(status_payload["final_reader_available"])
        self.assertEqual(status_payload["final_reader_health"]["status"], "FAIL")
        self.assertIn("mass_side_to_move_unknown", status_payload["final_reader_blockers"])
        self.assertEqual(status_payload["chess_files"]["chess_reader"]["download_url"], f"/convert/artifact/{job_id}/chess_reader")
        self.assertTrue(status_payload["chess_files"]["chess_reader"]["available"])
        self.assertEqual(status_payload["artifacts"]["chess_reader"]["download_url"], f"/convert/artifact/{job_id}/chess_reader")
        self.assertEqual(status_payload["artifacts"]["pdf_layout_preview"]["download_url"], f"/convert/artifact/{job_id}/pdf_layout_preview")
        self.assertTrue(status_payload["artifacts"]["chess_reader"]["final_reader_available"])


if __name__ == "__main__":
    unittest.main()
