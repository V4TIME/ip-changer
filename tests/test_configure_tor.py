from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "configure_tor.py"
SPEC = importlib.util.spec_from_file_location("configure_tor", SCRIPT)
assert SPEC and SPEC.loader
configure_tor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_tor)


class ConfigureTorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "torrc"

    def run_helper(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return configure_tor.main([str(self.path), *args])

    def test_existing_cookie_directives_are_not_duplicated(self) -> None:
        self.path.write_text(
            "ControlPort 9051\n"
            "SocksPort 9050\n"
            "CookieAuthentication 1\n"
            "CookieAuthFileGroupReadable 1\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_helper(), 0)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("CookieAuthentication 1"), 1)
        self.assertEqual(text.count("CookieAuthFileGroupReadable 1"), 1)
        self.assertIn(configure_tor.BEGIN, text)

    def test_disabled_cookie_authentication_is_enabled(self) -> None:
        self.path.write_text("CookieAuthentication 0\n", encoding="utf-8")
        self.assertEqual(self.run_helper(), 0)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("CookieAuthentication 0", text)
        self.assertIn("CookieAuthentication 1", text)

    def test_unix_endpoints_get_tcp_replacements(self) -> None:
        self.path.write_text(
            "ControlPort unix:/run/tor/control\nSocksPort unix:/run/tor/socks\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_helper(), 0)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("ControlPort 9051", text)
        self.assertIn("SocksPort 9050", text)

    def test_backup_names_do_not_collide(self) -> None:
        self.path.write_text("ControlPort 9051\n", encoding="utf-8")
        first = self.path.with_name(f"{self.path.name}.ipchanger.bak.20260101000000")
        first.write_text("older backup\n", encoding="utf-8")
        with patch.object(configure_tor.time, "strftime", return_value="20260101000000"):
            self.assertEqual(self.run_helper(), 0)
        second = self.path.with_name(
            f"{self.path.name}.ipchanger.bak.20260101000000.1"
        )
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_remove_is_idempotent(self) -> None:
        self.path.write_text("ControlPort 9051\n", encoding="utf-8")
        self.assertEqual(self.run_helper(), 0)
        self.assertEqual(self.run_helper("--remove"), 0)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(configure_tor.BEGIN, text)
        self.assertNotIn(configure_tor.END, text)
        self.assertEqual(self.run_helper("--remove"), 0)

    def test_dry_run_leaves_file_unchanged(self) -> None:
        original = "ControlPort 9051\n"
        self.path.write_text(original, encoding="utf-8")
        self.assertEqual(self.run_helper("--dry-run"), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
