from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


class ArtifactLinksJavaScriptTests(unittest.TestCase):
    def test_artifact_shell_url_supports_shared_payload_shapes(self) -> None:
        script_path = Path("static/js/artifact-links.js")
        node_script = textwrap.dedent(
            f"""
            global.window = {{}};
            require({json.dumps(str(script_path.resolve()))});
            const helper = window.KindleMasterArtifactLinks.artifactShellUrl;
            const results = {{
              direct: helper({{
                job_id: "job one",
                artifacts: {{ pdf_layout_preview: {{ filename: "pdf_layout_preview.html" }} }},
              }}, "pdf_layout_preview"),
              qualityState: helper({{
                job_id: "quality-job",
                quality_state: {{
                  artifacts: {{ pdf_layout_preview: {{ filename: "pdf_layout_preview.html" }} }},
                }},
              }}, "pdf_layout_preview"),
              conversion: helper({{
                conversion: {{
                  artifacts: {{
                    pdf_layout_preview: {{
                      job_id: "conversion-job",
                      filename: "pdf_layout_preview.html",
                    }},
                  }},
                }},
              }}, "pdf_layout_preview"),
              fallback: helper({{
                artifacts: {{
                  pdf_layout_preview: {{
                    download_url: "https://signed.example.invalid/pdf_layout_preview.html",
                  }},
                }},
              }}, "pdf_layout_preview"),
              missing: helper({{ job_id: "missing-job", artifacts: {{}} }}, "pdf_layout_preview"),
            }};
            console.log(JSON.stringify(results));
            """
        )

        result = subprocess.run(
            ["node", "-e", node_script],
            check=True,
            capture_output=True,
            text=True,
        )
        results = json.loads(result.stdout)

        self.assertEqual(results["direct"], "/convert/artifact/job%20one/pdf_layout_preview")
        self.assertEqual(results["qualityState"], "/convert/artifact/quality-job/pdf_layout_preview")
        self.assertEqual(results["conversion"], "/convert/artifact/conversion-job/pdf_layout_preview")
        self.assertEqual(results["fallback"], "https://signed.example.invalid/pdf_layout_preview.html")
        self.assertEqual(results["missing"], "")


if __name__ == "__main__":
    unittest.main()
