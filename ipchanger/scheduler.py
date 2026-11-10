"""The automatic rotation loop used by the background daemon."""

from __future__ import annotations

import random
import threading
import time
from typing import Callable

from .config import Config, ConfigError
from .rotator import Rotator, RotationResult
from .state import StateStore


class Scheduler:
    def __init__(
        self,
        config: Config,
        rotator: Rotator,
        state_store: StateStore | None = None,
        stop_event: threading.Event | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        jitter_random: Callable[[float, float], float] = random.uniform,
        on_result: Callable[[RotationResult], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.rotator = rotator
        self.state_store = state_store or StateStore()
        self.stop_event = stop_event or threading.Event()
        self.sleep = sleep
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self.jitter_random = jitter_random
        self.on_result = on_result
        self.log = log or (lambda message: None)

    def stop(self) -> None:
        self.stop_event.set()

    def _delay(self, interval: int, jitter: int) -> float:
        delay = float(interval)
        if jitter > 0:
            delay += self.jitter_random(0.0, delay * (jitter / 100.0))
        return delay

    def run(
        self,
        interval: int | None = None,
        jitter: int | None = None,
        once: bool = False,
        immediate: bool = True,
    ) -> int:
        interval = int(self.config.interval if interval is None else interval)
        jitter = int(self.config.jitter if jitter is None else jitter)
        if interval < self.config.min_interval:
            raise ConfigError(
                f"interval must be at least {self.config.min_interval} seconds; "
                "Tor rate-limits NEWNYM requests"
            )
        if not 0 <= jitter <= 100:
            raise ConfigError("jitter must be between 0 and 100 percent")

        state = self.state_store.load()
        if state.started_at is None:
            state.started_at = self.wall_clock()
        self.state_store.save(state)
        self.log(f"scheduler started: interval={interval}s jitter={jitter}% once={once}")

        failures = 0
        try:
            if immediate or once:
                result = self.rotator.rotate()
                failures = 0 if result.ok else 1
                if self.on_result:
                    self.on_result(result)
                if once:
                    return 0 if result.ok else 1

            while not self.stop_event.is_set():
                delay = self._delay(interval, jitter)
                state = self.state_store.load()
                state.next_rotation = self.wall_clock() + delay
                self.state_store.save(state)

                deadline = self.monotonic() + delay
                while not self.stop_event.is_set():
                    remaining = deadline - self.monotonic()
                    if remaining <= 0:
                        break
                    self.sleep(min(0.5, remaining))

                if self.stop_event.is_set():
                    break

                result = self.rotator.rotate()
                failures = 0 if result.ok else failures + 1
                if self.on_result:
                    self.on_result(result)
                if not result.ok:
                    self.log(f"rotation failed: {result.error}")
                    # Keep trying, but do not spin: the next iteration still
                    # waits for the configured interval.
                else:
                    self.log(
                        f"rotated: {result.old_ip or '?'} -> {result.new_ip or '?'}"
                        + (f" ({result.error})" if result.error else "")
                    )
        finally:
            state = self.state_store.load()
            state.next_rotation = None
            self.state_store.save(state)
            self.log("scheduler stopped")
        return 0 if failures == 0 else 1
