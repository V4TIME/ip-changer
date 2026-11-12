from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ipchanger import daemon
from ipchanger.config import Config


class DaemonTests(unittest.TestCase):
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

    def test_custom_config_precedes_run_subcommand(self) -> None:
        process = MagicMock()
        process.poll.return_value = None
        config_file = Path(self.temp.name) / "custom.json"
        with (
            patch("ipchanger.daemon.running_pid", return_value=None),
            patch("ipchanger.daemon.subprocess.Popen", return_value=process) as popen,
            patch("ipchanger.daemon.read_pid", return_value=4242),
            patch("ipchanger.daemon.pid_alive", return_value=True),
        ):
            pid = daemon.start(Config().validate(), config_file=config_file)
        self.assertEqual(pid, 4242)
        command = popen.call_args.args[0]
        self.assertLess(command.index("--config"), command.index("run"))
        self.assertEqual(command[command.index("--config") + 1], str(config_file))


if __name__ == "__main__":
    unittest.main()
