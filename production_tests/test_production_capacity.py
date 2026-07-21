from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify

from production_capacity_guard import (
    MemoryAdmissionPolicy,
    MemoryHeadroom,
    install_memory_admission_guard,
    memory_headroom,
)


class ProductionCapacityTests(unittest.TestCase):
    def test_cgroup_v2_memory_allows_safe_headroom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "memory.max").write_text(str(1024 * 1024 * 1024), encoding="utf-8")
            (root / "memory.current").write_text(str(512 * 1024 * 1024), encoding="utf-8")
            decision = memory_headroom(
                MemoryAdmissionPolicy(
                    min_available_bytes=256 * 1024 * 1024,
                    min_available_ratio=0.10,
                ),
                cgroup_v2_root=root,
                cgroup_v1_root=root / "missing-v1",
                proc_meminfo=root / "missing-meminfo",
            )

        self.assertTrue(decision.measurable)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "cgroup-v2")
        self.assertEqual(decision.available_bytes, 512 * 1024 * 1024)

    def test_cgroup_v2_memory_blocks_low_headroom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "memory.max").write_text(str(1024 * 1024 * 1024), encoding="utf-8")
            (root / "memory.current").write_text(str(900 * 1024 * 1024), encoding="utf-8")
            decision = memory_headroom(
                MemoryAdmissionPolicy(
                    min_available_bytes=256 * 1024 * 1024,
                    min_available_ratio=0.10,
                ),
                cgroup_v2_root=root,
                cgroup_v1_root=root / "missing-v1",
                proc_meminfo=root / "missing-meminfo",
            )

        self.assertTrue(decision.measurable)
        self.assertFalse(decision.allowed)
        self.assertLess(decision.available_bytes, 256 * 1024 * 1024)

    def test_proc_meminfo_is_used_when_cgroup_limit_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meminfo = root / "meminfo"
            meminfo.write_text(
                "MemTotal:       1048576 kB\nMemAvailable:    524288 kB\n",
                encoding="utf-8",
            )
            decision = memory_headroom(
                MemoryAdmissionPolicy(
                    min_available_bytes=128 * 1024 * 1024,
                    min_available_ratio=0.10,
                ),
                cgroup_v2_root=root / "missing-v2",
                cgroup_v1_root=root / "missing-v1",
                proc_meminfo=meminfo,
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "proc-meminfo")
        self.assertEqual(decision.available_bytes, 512 * 1024 * 1024)

    def test_unavailable_memory_telemetry_does_not_break_local_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            decision = memory_headroom(
                MemoryAdmissionPolicy(),
                cgroup_v2_root=root / "missing-v2",
                cgroup_v1_root=root / "missing-v1",
                proc_meminfo=root / "missing-meminfo",
            )

        self.assertFalse(decision.measurable)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "unavailable")

    def test_flask_rejects_conversion_when_memory_is_low(self) -> None:
        app = Flask("memory-admission-test")
        route_calls = {"count": 0}

        def json_error(message: str, **kwargs):
            response = jsonify(
                {
                    "error": message,
                    "error_code": kwargs["error_code"],
                    "retryable": kwargs["retryable"],
                    **dict(kwargs.get("extra") or {}),
                }
            )
            response.status_code = kwargs["status_code"]
            return response

        module = types.SimpleNamespace(app=app, _json_error=json_error)
        install_memory_admission_guard(module, policy=MemoryAdmissionPolicy())

        @app.post("/convert/start")
        def convert_start():
            route_calls["count"] += 1
            return jsonify({"success": True}), 202

        with patch(
            "production_capacity_guard.memory_headroom",
            return_value=MemoryHeadroom(
                measurable=True,
                allowed=False,
                total_bytes=512 * 1024 * 1024,
                available_bytes=64 * 1024 * 1024,
                available_ratio=0.125,
                source="cgroup-v2",
            ),
        ):
            response = app.test_client().post("/convert/start")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error_code"], "memory_capacity_exceeded")
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertEqual(route_calls["count"], 0)


if __name__ == "__main__":
    unittest.main()
