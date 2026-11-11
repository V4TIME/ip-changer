from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from ipchanger.config import Config, ConfigError
from ipchanger.rotator import Rotator, RotationResult
from ipchanger.scheduler import Scheduler
from ipchanger.state import LogWriter, State, StateStore, read_log_records, tail_log_records
from ipchanger.torhttp import ExitIP


class FakeClock:
    def __init__(self) -> None:
        self.value = time.time()

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


class FakeController:
    def __init__(self) -> None:
        self.signals = 0

    def __enter__(self) -> "FakeController":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def signal_newnym(self) -> None:
        self.signals += 1


class FailingController(FakeController):
    def signal_newnym(self) -> None:
        raise RuntimeError("control port is down")


class RotatorTests(unittest.TestCase):
    def make_rotator(
        self,
        ips: list[str],
        controller: Any | None = None,
        state: State | None = None,
    ) -> tuple[Rotator, StateStore, Path, list[float]]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = StateStore(root / "state.json")
        store.save(state or State(last_ip="1.1.1.1", last_newnym=0))
        sleeps: list[float] = []
        values = list(ips)
        clock = FakeClock()

        def ip_getter(_config: Config) -> ExitIP:
            value = values.pop(0) if values else "1.1.1.1"
            return ExitIP(ip=value, is_tor=True, source="test")

        rotator = Rotator(
            Config(interval=10, change_timeout=5, poll_interval=0.25).validate(),
            state_store=store,
            log_writer=LogWriter(Config().validate(), root / "ipchanger.log"),
            controller_factory=lambda _config: controller or FakeController(),
            ip_getter=ip_getter,
            sleep=lambda seconds: sleeps.append(seconds),
            clock=clock,
            use_lock=False,
        )
        return rotator, store, root, sleeps

    def test_successful_rotation(self) -> None:
        rotator, store, root, _ = self.make_rotator(["2.2.2.2"])
        result = rotator.rotate()
        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertEqual(result.old_ip, "1.1.1.1")
        self.assertEqual(result.new_ip, "2.2.2.2")
        state = store.load()
        self.assertEqual(state.last_ip, "2.2.2.2")
        self.assertEqual(state.previous_ip, "1.1.1.1")
        self.assertEqual(state.total_rotations, 1)
        records = read_log_records(root / "ipchanger.log")
        self.assertEqual(records[-1]["event"], "rotation")
        self.assertTrue(records[-1]["ok"])

    def test_baseline_is_taken_when_state_is_empty(self) -> None:
        rotator, store, _, _ = self.make_rotator(
            ["1.1.1.1", "2.2.2.2"],
            state=State(last_ip=None),
        )
        result = rotator.rotate()
        self.assertTrue(result.ok)
        self.assertEqual(result.old_ip, "1.1.1.1")
        self.assertEqual(result.new_ip, "2.2.2.2")

    def test_same_exit_ip_is_reported_as_warning(self) -> None:
        rotator, store, _, _ = self.make_rotator(["1.1.1.1", "1.1.1.1", "1.1.1.1"])
        result = rotator.rotate()
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertIn("reused", result.error or "")
        self.assertEqual(store.load().last_ip, "1.1.1.1")

    def test_control_failure_is_recorded(self) -> None:
        rotator, store, _, _ = self.make_rotator(["2.2.2.2"], controller=FailingController())
        result = rotator.rotate()
        self.assertFalse(result.ok)
        self.assertIn("control port", result.error or "")
        self.assertEqual(store.load().total_failures, 1)

    def test_rate_limit_sleeps(self) -> None:
        now = time.time()
        rotator, _, _, sleeps = self.make_rotator(
            ["2.2.2.2"],
            state=State(last_ip="1.1.1.1", last_newnym=now - 5),
        )
        rotator.rotate()
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0.0)

    def test_fetch_ip_updates_state(self) -> None:
        rotator, store, _, _ = self.make_rotator(["7.7.7.7"])
        result = rotator.fetch_ip()
        self.assertEqual(result.ip, "7.7.7.7")
        self.assertEqual(store.load().last_ip, "7.7.7.7")


class FakeRotator:
    def __init__(self, ok: bool = True) -> None:
        self.calls = 0
        self.ok = ok

    def rotate(self) -> RotationResult:
        self.calls += 1
        return RotationResult(
            ok=self.ok,
            changed=True,
            old_ip="1.1.1.1",
            new_ip="2.2.2.2" if self.ok else None,
            is_tor=True,
            source="test",
            error=None if self.ok else "failed",
            duration_ms=1,
        )


class LogWriterTests(unittest.TestCase):
    def test_disabled_logging_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ipchanger.log"
            writer = LogWriter(Config(log_enabled=False).validate(), path)
            record = writer.append("rotation", ok=True)
            self.assertEqual(record["event"], "rotation")
            self.assertFalse(path.exists())

    def test_tail_reads_recent_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ipchanger.log"
            writer = LogWriter(Config().validate(), path)
            for index in range(5):
                writer.append("rotation", ok=True, index=index)
            recent = tail_log_records(2, path)
            self.assertEqual([item["index"] for item in recent], [3, 4])


class SchedulerTests(unittest.TestCase):
    def test_once_calls_rotator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            fake = FakeRotator()
            scheduler = Scheduler(
                Config().validate(),
                fake,  # type: ignore[arg-type]
                state_store=store,
                sleep=lambda _seconds: None,
            )
            code = scheduler.run(interval=10, once=True)
            self.assertEqual(code, 0)
            self.assertEqual(fake.calls, 1)

    def test_interval_below_minimum_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = Scheduler(
                Config().validate(),
                FakeRotator(),  # type: ignore[arg-type]
                state_store=StateStore(Path(tmp) / "state.json"),
            )
            with self.assertRaises(ConfigError):
                scheduler.run(interval=5, once=True)

    def test_stop_before_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeRotator()
            event = threading.Event()
            event.set()
            scheduler = Scheduler(
                Config().validate(),
                fake,  # type: ignore[arg-type]
                state_store=StateStore(Path(tmp) / "state.json"),
                stop_event=event,
            )
            code = scheduler.run(interval=10, immediate=False)
            self.assertEqual(code, 0)
            self.assertEqual(fake.calls, 0)


if __name__ == "__main__":
    unittest.main()
