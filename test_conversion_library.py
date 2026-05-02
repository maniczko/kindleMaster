import tempfile
import unittest
import zipfile
from pathlib import Path

from conversion_library import (
    LibraryFilters,
    build_library_index,
    build_quality_report_payload,
    render_quality_report_markdown,
)


class ConversionLibraryTests(unittest.TestCase):
    def _write_epub(self, directory: Path, name: str, body: str) -> str:
        path = directory / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr(
                "EPUB/chapter.xhtml",
                f"<html><body><h1>Chapter</h1><p>{body}</p></body></html>",
            )
        return str(path)

    def test_library_index_filters_by_status_verdict_and_quality_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ready_output = self._write_epub(root, "ready.epub", "Balanced scorecard appendix")
            jobs = {
                "ready-job": {
                    "job_id": "ready-job",
                    "status": "ready",
                    "filename": "business-report.pdf",
                    "source_type": "pdf",
                    "output_path": ready_output,
                    "download_name": "business-report.epub",
                    "created_at": "2026-05-02T08:00:00Z",
                    "updated_at": "2026-05-02T08:05:00Z",
                },
                "failed-job": {
                    "job_id": "failed-job",
                    "status": "failed",
                    "filename": "failed.pdf",
                    "source_type": "pdf",
                    "output_path": "",
                    "created_at": "2026-05-02T07:00:00Z",
                    "updated_at": "2026-05-02T07:01:00Z",
                },
            }

            def quality_state_builder(job_id, _job):
                if job_id == "ready-job":
                    return {
                        "release_verdict": "release_blocked",
                        "reading_verdict": "ready_with_review",
                        "release_blocked": True,
                        "metadata_summary": {
                            "title": "Business Report",
                            "creator": "KindleMaster",
                            "language": "en",
                        },
                        "quality_blockers": [
                            {
                                "code": "reference_coverage_failed",
                                "message": "Reference coverage failed",
                                "source": "reference_cleanup",
                            }
                        ],
                        "issue_groups": {"blockers": [], "warnings": [], "review": []},
                    }
                return {
                    "release_verdict": "failed",
                    "reading_verdict": "failed",
                    "metadata_summary": {"title": "Failed Report"},
                }

            payload = build_library_index(
                jobs,
                quality_state_builder=quality_state_builder,
                output_size_resolver=lambda job: Path(str(job.get("output_path", ""))).stat().st_size
                if job.get("output_path")
                else None,
                filters=LibraryFilters(
                    query="reference",
                    status="ready",
                    release_verdict="release_blocked",
                    include_text=True,
                    limit=10,
                ),
            )

            self.assertTrue(payload["success"])
            self.assertEqual(payload["count"], 1)
            item = payload["items"][0]
            self.assertEqual(item["job_id"], "ready-job")
            self.assertEqual(item["release_verdict"], "release_blocked")
            self.assertEqual(item["download_url"], "/convert/download/ready-job")
            self.assertTrue(item["download_available"])
            self.assertEqual(item["download_state"]["status"], "available")
            self.assertIn("quality_blockers", item["matched_fields"])
            self.assertTrue(item["searchable_text_available"])

    def test_quality_report_payload_and_markdown_include_export_links_and_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_path = self._write_epub(root, "report.epub", "Exported report evidence text.")
            job = {
                "job_id": "report-job",
                "status": "ready",
                "filename": "report.pdf",
                "source_type": "pdf",
                "output_path": output_path,
                "download_name": "report.epub",
                "created_at": "2026-05-02T08:00:00Z",
                "updated_at": "2026-05-02T08:05:00Z",
            }
            quality_state = {
                "release_verdict": "release_ready",
                "reading_verdict": "ready",
                "release_blocked": False,
                "metadata_summary": {
                    "title": "Release Report",
                    "creator": "KindleMaster",
                    "language": "en",
                },
                "quality_blockers": [],
            }

            payload = build_quality_report_payload(
                "report-job",
                job,
                quality_state=quality_state,
                output_size_bytes=Path(output_path).stat().st_size,
                include_text=True,
            )
            markdown = render_quality_report_markdown(payload)

            self.assertEqual(payload["job"]["report_json_url"], "/convert/report/report-job.json")
            self.assertEqual(payload["job"]["report_markdown_url"], "/convert/report/report-job.md")
            self.assertIn("Exported report evidence text", payload["job"]["text_excerpt"])
            self.assertIn("# KindleMaster quality report: Release Report", markdown)
            self.assertIn("Exported report evidence text", markdown)
            self.assertIn('"release_verdict": "release_ready"', markdown)


if __name__ == "__main__":
    unittest.main()
