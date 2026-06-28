import json
import tempfile
import unittest
from pathlib import Path

from chess_study_export import (
    FINAL_READER_ARTIFACT_TYPE,
    SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE,
    _build_final_reader_health_gate,
    _write_final_reader_health_gate,
)


class ChessFinalReaderHealthGateTests(unittest.TestCase):
    def test_final_reader_with_marker_evidence_and_assets_passes(self) -> None:
        html_text = """
        <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
          <article class="card" data-position-status="accepted" data-side-marker-status="trusted_marker"
                   data-has-board-crop="true" data-has-side-marker-crop="true">
            <div class="diagram">
              <img src="assets/board-1.png" alt="position source crop">
              <img src="assets/marker-1.png" alt="position side marker crop">
            </div>
            <p>Side to move: white</p>
          </article>
          <article class="card" data-position-status="accepted" data-side-marker-status="trusted_marker"
                   data-has-board-crop="true" data-has-side-marker-crop="true">
            <div class="diagram">
              <img src="assets/board-2.png" alt="position source crop">
              <img src="assets/marker-2.png" alt="position side marker crop">
            </div>
            <p>Side to move: black</p>
          </article>
        </body></html>
        """

        payload = _build_final_reader_health_gate(
            html_text=html_text,
            artifact_manifest={
                "artifact_type": FINAL_READER_ARTIFACT_TYPE,
                "pipeline_mode": "pdf_two_crop_reader",
                "diagrams_total": 2,
            },
            positions=[
                {
                    "side_to_move": "white",
                    "side_marker_status": "trusted_marker",
                    "board_crop_path": "assets/board-1.png",
                    "side_marker_crop_path": "assets/marker-1.png",
                },
                {
                    "side_to_move": "black",
                    "side_marker_status": "trusted_marker",
                    "board_crop_path": "assets/board-2.png",
                    "side_marker_crop_path": "assets/marker-2.png",
                },
            ],
        )

        self.assertEqual(payload["decision"], "pass")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["diagram_cards_count"], 2)
        self.assertEqual(payload["side_unknown_count"], 0)
        self.assertEqual(payload["data_side_marker_attr_count"], 2)
        self.assertEqual(payload["trusted_marker_count"], 2)
        self.assertEqual(payload["side_marker_crop_count"], 2)
        self.assertEqual(payload["board_crop_count"], 2)
        self.assertEqual(payload["empty_img_src_count"], 0)
        self.assertEqual(payload["blockers"], [])

    def test_broken_final_reader_with_mass_unknown_and_empty_images_fails(self) -> None:
        html_text = """
        <!doctype html><html><body data-artifact-type="final_pdf_two_crop_reader">
          <article class="card"><img src=""><p>Side to move: unknown</p></article>
          <article class="card"><img src=""><p>Side to move: unknown</p></article>
          <article class="card"><img src=""><p>Side to move: unknown</p></article>
          <article class="card"><img src=""><p>Side to move: unknown</p></article>
        </body></html>
        """

        payload = _build_final_reader_health_gate(
            html_text=html_text,
            artifact_manifest={
                "artifact_type": FINAL_READER_ARTIFACT_TYPE,
                "pipeline_mode": "pdf_two_crop_reader",
                "diagrams_total": 4,
            },
        )

        self.assertEqual(payload["decision"], "fail")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["diagram_cards_count"], 4)
        self.assertEqual(payload["side_unknown_count"], 4)
        self.assertEqual(payload["empty_img_src_count"], 4)
        self.assertEqual(payload["data_side_marker_attr_count"], 0)
        self.assertIn("mass_side_to_move_unknown", payload["blockers"])
        self.assertIn("empty_img_src", payload["blockers"])

    def test_evidence_only_html_is_not_marked_as_final_reader(self) -> None:
        html_text = """
        <!doctype html><html><body data-artifact-type="source_html_evidence_only">
          <section class="artifact-provenance">Evidence-only report</section>
          <p>Side to move: unknown</p>
        </body></html>
        """

        payload = _build_final_reader_health_gate(
            html_text=html_text,
            artifact_manifest={
                "artifact_type": SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE,
                "pipeline_mode": "source_html_evidence_report",
            },
        )

        self.assertEqual(payload["decision"], "fail")
        self.assertEqual(payload["artifact_type"], SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE)
        self.assertIn("not_final_reader_artifact", payload["blockers"])

    def test_writer_persists_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            index_path = out / "index.html"
            index_path.write_text(
                """
                <!doctype html><html><body>
                  <article class="card" data-position-status="accepted" data-side-marker-status="trusted_marker"
                           data-has-board-crop="true" data-has-side-marker-crop="true">
                    <img src="assets/board.png"><p>Side to move: white</p>
                  </article>
                </body></html>
                """,
                encoding="utf-8",
            )

            payload = _write_final_reader_health_gate(
                out,
                artifact_manifest={
                    "artifact_type": FINAL_READER_ARTIFACT_TYPE,
                    "pipeline_mode": "pdf_two_crop_reader",
                    "diagrams_total": 1,
                },
                positions=[
                    {
                        "side_to_move": "white",
                        "side_marker_status": "trusted_marker",
                        "board_crop_path": "assets/board.png",
                        "side_marker_crop_path": "assets/marker.png",
                    }
                ],
            )

            persisted = json.loads((out / "reports" / "final_reader_health_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, payload)
            self.assertEqual(persisted["artifact_type"], FINAL_READER_ARTIFACT_TYPE)
            self.assertFalse((out / "reports" / "final_reader_health_gate.md").exists())


if __name__ == "__main__":
    unittest.main()
