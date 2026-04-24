from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

import kindlemaster
from app_runtime_services import LOCALHOST, LOCAL_APP_HOSTNAME, build_local_app_url


class LocalHostnameContractTests(unittest.TestCase):
    def test_build_local_app_url_uses_branded_hostname(self) -> None:
        self.assertEqual(LOCALHOST, "127.0.0.1")
        self.assertEqual(LOCAL_APP_HOSTNAME, "kindlemaster.localhost")
        self.assertEqual(build_local_app_url(5001), "http://kindlemaster.localhost:5001/")

    def test_run_serve_prints_branded_url_but_binds_loopback(self) -> None:
        stdout = io.StringIO()

        with patch("app_runtime_services.serve_http_app", return_value=0) as serve_mock:
            with patch("app_runtime_services.resolve_debug_mode", return_value=False):
                with contextlib.redirect_stdout(stdout):
                    exit_code = kindlemaster._run_serve(
                        port=5511,
                        debug=False,
                        runtime="waitress",
                    )

        self.assertEqual(exit_code, 0)
        self.assertIn("http://kindlemaster.localhost:5511/", stdout.getvalue())
        self.assertIn("bind=127.0.0.1", stdout.getvalue())
        self.assertEqual(serve_mock.call_args.kwargs["host"], LOCALHOST)
        self.assertEqual(serve_mock.call_args.kwargs["port"], 5511)
        self.assertEqual(serve_mock.call_args.kwargs["runtime"], "waitress")


if __name__ == "__main__":
    unittest.main()
