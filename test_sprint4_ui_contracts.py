from __future__ import annotations

import json
import unittest
from pathlib import Path

from app import app


REPO_ROOT = Path(__file__).resolve().parent


class Sprint4UiContractsTests(unittest.TestCase):
    def test_node_workspace_exposes_sprint4_scripts_without_replacing_python_api(self) -> None:
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["scripts"]["dev:ui"], "vite --host 127.0.0.1 --port 5173")
        self.assertEqual(package["scripts"]["build:ui"], "vite build")
        self.assertEqual(package["scripts"]["test:ui"], "vitest run --config vitest.config.js --dir frontend/src")
        self.assertEqual(package["scripts"]["test:e2e"], "python kindlemaster.py test --suite runtime")
        self.assertIn("react", package["dependencies"])
        self.assertIn("vite", package["devDependencies"])

    def test_react_shell_declares_shadcn_style_operational_surfaces(self) -> None:
        source = "\n".join(
            [
                (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "button.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "card.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "tabs.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "dialog.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "progress.tsx").read_text(encoding="utf-8"),
                (REPO_ROOT / "frontend" / "src" / "components" / "ui" / "badge.tsx").read_text(encoding="utf-8"),
            ]
        )

        for marker in (
            "Conversion dashboard",
            "Quality report",
            "Artifact and download panel",
            "Error and debug panel",
            "Sentry event",
            "quality_state",
            "sendable",
            "kindle_ready",
            "premium_ready",
            "Button",
            "Card",
            "Tabs",
            "Dialog",
            "Progress",
            "Badge",
        ):
            self.assertIn(marker, source)

    def test_shadcn_registry_config_is_explicit_about_local_primitives(self) -> None:
        config = json.loads((REPO_ROOT / "components.json").read_text(encoding="utf-8"))
        docs = (REPO_ROOT / "docs" / "shadcn-status.md").read_text(encoding="utf-8")

        self.assertEqual(config["$schema"], "https://ui.shadcn.com/schema.json")
        self.assertEqual(config["aliases"]["ui"], "@/components/ui")
        self.assertEqual(config["aliases"]["utils"], "@/lib/utils")
        self.assertEqual(config["iconLibrary"], "lucide")
        self.assertIn("official shadcn registry config + shadcn-style local primitives", docs)

    def test_flask_serves_sprint4_app_route_without_breaking_legacy_root(self) -> None:
        client = app.test_client()

        legacy_response = client.get("/")
        app_response = client.get("/app")

        self.assertEqual(legacy_response.status_code, 200)
        self.assertEqual(app_response.status_code, 200)
        self.assertIn("KindleMaster", legacy_response.get_data(as_text=True))
        app_html = app_response.get_data(as_text=True)
        self.assertTrue(
            'id="root"' in app_html or "KindleMaster Sprint 4 UI" in app_html,
            app_html[:300],
        )


if __name__ == "__main__":
    unittest.main()
