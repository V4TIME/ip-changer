"""One IP rotation: NEWNYM, wait for a fresh circuit, verify and persist."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .control import TorControlError, TorController
from .state import FileLock, LogWriter, StateStore, lock_path
from .torhttp import ExitIP, IPCheckError, TorVerificationError, get_exit_ip


@dataclass
class RotationResult:
    ok: bool
    changed: bool
    old_ip: str | None
    new_ip: str | None
    is_tor: bool | None
    source: str | None
    error: str | None
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "old_ip": self.old_ip,
            "new_ip": self.new_ip,
            "is_tor": self.is_tor,
            "source": self.source,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class Rotator:
    def __init__(
        self,
        config: Config,
        state_store: StateStore | None = None,
        log_writer: LogWriter | None = None,
        controller_factory: Callable[[Config], TorController] = TorController.from_config,
        ip_getter: Callable[[Config], ExitIP] = get_exit_ip,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        use_lock: bool = True,
    ) -> None:
        self.config = config
        self.state_store = state_store or StateStore()
        self.log = log_writer or LogWriter(config)
        self.controller_factory = controller_factory
        self.ip_getter = ip_getter
        self.sleep = sleep
        self.clock = clock
        self.use_lock = use_lock

    def fetch_ip(self, update_state: bool = True) -> ExitIP:
        result = self.ip_getter(self.config)
        if update_state:
            state = self.state_store.load()
            state.last_ip = result.ip
            state.last_is_tor = result.is_tor
            state.last_source = result.source
            if state.previous_ip == result.ip:
                state.previous_ip = None
            self.state_store.save(state)
        return result

    def rotate(self, verify: bool | None = None, wait: bool | None = None) -> RotationResult:
        if self.use_lock:
            try:
                with FileLock(lock_path(), timeout=30.0):
                    return self._rotate(verify=verify, wait=wait)
            except TimeoutError as exc:
                return RotationResult(
                    ok=False,
                    changed=False,
                    old_ip=None,
                    new_ip=None,
                    is_tor=None,
                    source=None,
                    error=str(exc),
                    duration_ms=0,
                )
        return self._rotate(verify=verify, wait=wait)

    def _rotate(self, verify: bool | None = None, wait: bool | None = None) -> RotationResult:
        started = self.clock()
        do_verify = self.config.verify if verify is None else verify
        do_wait = self.config.wait_for_change if wait is None else wait
        state = self.state_store.load()
        old_ip = state.last_ip

        # If we have never seen an IP, establish a baseline first. This also
        # proves that the SOCKS port works before sending a NEWNYM signal.
        if do_verify and old_ip is None:
            try:
                baseline = self.ip_getter(self.config)
                old_ip = baseline.ip
                state.last_ip = baseline.ip
                state.last_is_tor = baseline.is_tor
                state.last_source = baseline.source
                self.state_store.save(state)
            except (IPCheckError, OSError) as exc:
                # Continue; the post-rotation check may still succeed.
                state.last_error = f"baseline check failed: {exc}"
                self.state_store.save(state)

        # Tor accepts NEWNYM at most once every 10 seconds. Waiting here keeps
        # manual rotations from silently doing nothing.
        if state.last_newnym is not None:
            elapsed = self.clock() - float(state.last_newnym)
            remaining = max(0.0, float(self.config.min_interval) - elapsed)
            if remaining > 0:
                self.sleep(remaining)

        state.last_attempt = self.clock()
        state.last_newnym = self.clock()
        state.last_error = None
        self.state_store.save(state)

        try:
            with self.controller_factory(self.config) as controller:
                controller.signal_newnym()
        except Exception as exc:
            duration = int((self.clock() - started) * 1000)
            state = self.state_store.load()
            state.total_failures += 1
            state.last_error = str(exc)
            self.state_store.save(state)
            record = self.log.append(
                "rotation",
                ok=False,
                old_ip=old_ip,
                new_ip=None,
                changed=False,
                is_tor=None,
                duration_ms=duration,
                error=str(exc),
            )
            return RotationResult(
                ok=False,
                changed=False,
                old_ip=old_ip,
                new_ip=None,
                is_tor=None,
                source=None,
                error=str(exc),
                duration_ms=duration,
            )

        if not do_verify:
            duration = int((self.clock() - started) * 1000)
            state = self.state_store.load()
            state.total_rotations += 1
            state.last_success = self.clock()
            state.last_error = None
            self.state_store.save(state)
            record = self.log.append(
                "rotation",
                ok=True,
                old_ip=old_ip,
                new_ip=None,
                changed=None,
                is_tor=None,
                duration_ms=duration,
                note="verification disabled",
            )
            return RotationResult(
                ok=True,
                changed=False,
                old_ip=old_ip,
                new_ip=None,
                is_tor=None,
                source=None,
                error=None,
                duration_ms=duration,
            )

        new_ip: str | None = None
        is_tor: bool | None = None
        source: str | None = None
        last_check_error: str | None = None
        deadline = started + max(5.0, float(self.config.change_timeout))
        first = True
        while True:
            if not first:
                self.sleep(max(0.5, float(self.config.poll_interval)))
            first = False
            try:
                current = self.ip_getter(self.config)
                new_ip = current.ip
                is_tor = current.is_tor
                source = current.source
                if old_ip is None or new_ip != old_ip or not do_wait:
                    break
            except TorVerificationError as exc:
                last_check_error = str(exc)
                break
            except (IPCheckError, OSError) as exc:
                last_check_error = str(exc)
            if self.clock() >= deadline:
                break

        duration = int((self.clock() - started) * 1000)
        state = self.state_store.load()
        state.last_attempt = self.clock()
        state.last_source = source

        if new_ip is None:
            error = last_check_error or "could not verify a new exit IP before the timeout"
            state.total_failures += 1
            state.last_error = error
            self.state_store.save(state)
            self.log.append(
                "rotation",
                ok=False,
                old_ip=old_ip,
                new_ip=None,
                changed=False,
                is_tor=None,
                duration_ms=duration,
                error=error,
            )
            return RotationResult(
                ok=False,
                changed=False,
                old_ip=old_ip,
                new_ip=None,
                is_tor=None,
                source=source,
                error=error,
                duration_ms=duration,
            )

        if self.config.require_tor and is_tor is not True:
            error = "the check service could not confirm that traffic used Tor"
            state.total_failures += 1
            state.last_error = error
            self.state_store.save(state)
            self.log.append(
                "rotation",
                ok=False,
                old_ip=old_ip,
                new_ip=new_ip,
                changed=bool(old_ip and new_ip != old_ip),
                is_tor=is_tor,
                duration_ms=duration,
                error=error,
            )
            return RotationResult(
                ok=False,
                changed=bool(old_ip and new_ip != old_ip),
                old_ip=old_ip,
                new_ip=new_ip,
                is_tor=is_tor,
                source=source,
                error=error,
                duration_ms=duration,
            )

        changed = old_ip is None or new_ip != old_ip
        warning = None
        if old_ip is not None and not changed:
            warning = "Tor reused the same exit IP; NEWNYM does not guarantee a different node"
        if is_tor is None:
            alt_warning = "Tor status could not be confirmed by this check service"
            warning = f"{warning}; {alt_warning}" if warning else alt_warning

        state.previous_ip = old_ip if old_ip and old_ip != new_ip else state.previous_ip
        state.last_ip = new_ip
        state.last_is_tor = is_tor
        state.last_success = self.clock()
        state.total_rotations += 1
        state.last_error = warning
        self.state_store.save(state)
        self.log.append(
            "rotation",
            ok=True,
            old_ip=old_ip,
            new_ip=new_ip,
            changed=changed,
            is_tor=is_tor,
            source=source,
            duration_ms=duration,
            note=warning,
        )
        return RotationResult(
            ok=True,
            changed=changed,
            old_ip=old_ip,
            new_ip=new_ip,
            is_tor=is_tor,
            source=source,
            error=warning,
            duration_ms=duration,
        )
