from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ipchanger.config import (
    Config,
    ConfigError,
    format_duration,
    load_config,
    parse_duration,
    save_config,
    set_config_value,
)


class DurationTests(unittest.TestCase):
    def test_plain_seconds(self) -> None:
        self.assertEqual(parse_duration("90"), 90)
        self.assertEqual(parse_duration(90), 90)

    def test_suffixes(self) -> None:
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)

    def test_clock_format(self) -> None:
        self.assertEqual(parse_duration("00:02:00"), 120)
        self.assertEqual(parse_duration("01:30"), 90)

    def test_invalid(self) -> None:
        for value in ("", "abc", "-5", "5x", "1h30", "1:2:3:4"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    parse_duration(value)

    def test_format(self) -> None:
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(90), "1m 30s")
        self.assertEqual(format_duration(3661), "1h 1m 1s")
        self.assertEqual(format_duration(None), "unknown")


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        config = Config().validate()
        self.assertEqual(config.interval, 120)
        self.assertEqual(config.control_port, 9051)
        self.assertEqual(config.socks_port, 9050)
        self.assertGreaterEqual(config.min_interval, 10)

    def test_interval_below_rate_limit_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Config(interval=5).validate()

    def test_set_interval_accepts_duration(self) -> None:
        config = Config().validate()
        config = set_config_value(config, "interval", "5m")
        self.assertEqual(config.interval, 300)

    def test_set_bool_and_list(self) -> None:
        config = Config().validate()
        config = set_config_value(config, "verify", "false")
        self.assertFalse(config.verify)
        config = set_config_value(config, "check_urls", "https://example.com/ip,https://example.org/ip")
        self.assertEqual(len(config.check_urls), 2)

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = Config(interval=600, jitter=20).validate()
            save_config(config, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = load_config(path)
            self.assertEqual(loaded.interval, 600)
            self.assertEqual(loaded.jitter, 20)

    def test_unknown_keys_are_ignored(self) -> None:
        config = Config.from_dict({"interval": 60, "not_a_real_key": 1})
        self.assertEqual(config.interval, 60)

    def test_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_paths_honor_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "IPCHANGER_CONFIG_DIR": str(Path(tmp) / "cfg"),
                "IPCHANGER_STATE_DIR": str(Path(tmp) / "state"),
            }
            with patch.dict(os.environ, env, clear=False):
                from ipchanger.config import config_dir, config_path, state_dir

                self.assertEqual(config_dir(), Path(tmp) / "cfg")
                self.assertEqual(config_path(), Path(tmp) / "cfg" / "config.json")
                self.assertEqual(state_dir(), Path(tmp) / "state")


if __name__ == "__main__":
    unittest.main()
