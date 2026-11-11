from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ipchanger.cli import main
from ipchanger.rotator import RotationResult


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = {
            "IPCHANGER_CONFIG_DIR": str(Path(self.temp.name) / "config"),
            "IPCHANGER_STATE_DIR": str(Path(self.temp.name) / "state"),
        }
        self.patcher = patch.dict(os.environ, self.env, clear=False)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_config_set_and_status_json(self) -> None:
        code, out, err = self.run_cli("config", "set", "interval", "5m")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("status", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["interval"], 300)
        self.assertFalse(payload["running"])

    def test_config_list_and_get(self) -> None:
        self.run_cli("config", "set", "password", "hunter2")
        code, out, err = self.run_cli("config", "get", "password")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "***")
        code, out, err = self.run_cli("config", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("interval", out)
        self.assertNotIn("hunter2", out)

    def test_config_rejects_bad_interval(self) -> None:
        code, _, err = self.run_cli("config", "set", "interval", "5")
        self.assertEqual(code, 2)
        self.assertIn("at least 10", err)

    def test_empty_logs(self) -> None:
        code, out, err = self.run_cli("logs")
        self.assertEqual(code, 0, err)
        self.assertIn("No log entries", out)

    def test_logging_can_be_disabled_and_enabled(self) -> None:
        code, out, err = self.run_cli("logs", "--off")
        self.assertEqual(code, 0, err)
        self.assertIn("disabled", out)
        code, out, err = self.run_cli("config", "get", "log_enabled")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "false")
        code, out, err = self.run_cli("logs")
        self.assertEqual(code, 0, err)
        self.assertIn("Logging is disabled", out)
        code, out, err = self.run_cli("logs", "--status")
        self.assertEqual(code, 0, err)
        self.assertIn("Logging disabled", out)
        code, out, err = self.run_cli("logs", "--on")
        self.assertEqual(code, 0, err)
        self.assertIn("enabled", out)

    def test_panel_falls_back_to_text(self) -> None:
        code, out, err = self.run_cli("panel")
        self.assertEqual(code, 0, err)
        self.assertIn("IP Changer", out)

    def test_rotate_json_with_mocked_rotator(self) -> None:
        class FakeRotator:
            def __init__(self, _config: object) -> None:
                pass

            def rotate(self, verify: bool = True, wait: bool = True) -> RotationResult:
                return RotationResult(
                    ok=True,
                    changed=True,
                    old_ip="1.1.1.1",
                    new_ip="2.2.2.2",
                    is_tor=True,
                    source="test",
                    error=None,
                    duration_ms=12,
                )

        with patch("ipchanger.cli.Rotator", FakeRotator):
            code, out, err = self.run_cli("rotate", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["new_ip"], "2.2.2.2")
        self.assertTrue(payload["ok"])

    def test_doctor_reports_invalid_config(self) -> None:
        config_path = Path(self.temp.name) / "config" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{not json", encoding="utf-8")
        code, out, _ = self.run_cli("doctor", "--quick")
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", out)

    def test_version_exits(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("--version")
        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
