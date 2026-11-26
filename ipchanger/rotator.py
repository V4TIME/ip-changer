"""One IP rotation: NEWNYM or a fresh SOCKS identity, then verify and persist.

Two rotation strategies exist:

* **control** -- ``SIGNAL NEWNYM`` on the Tor control port. This is the clean
  way, but Orbot never exposes its control socket to other apps.
* **socks** -- a fresh SOCKS5 username/password pair per attempt. Tor isolates
  circuits by SOCKS credentials (``IsolateSOCKSAuth`` is on by default), so a
  new pair yields a new circuit and, almost always, a new exit node. This is
  the only strategy that works against Orbot.

``rotation_mode=auto`` probes the control port and falls back to SOCKS.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .control import TorController
from .state import FileLock, LogWriter, StateStore, lock_path
from .torhttp import ExitIP, IPCheckError, TorVerificationError, get_exit_ip

# A fresh SOCKS identity almost always lands on a new exit node. Retrying a
# few times covers the case where Tor happens to pick the same one again.
SOCKS_ATTEMPTS = 8


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
    mode: str | None = None

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
            "mode": self.mode,
        }


class Rotator:
    def __init__(
        self,
        config: Config,
        state_store: StateStore | None = None,
        log_writer: LogWriter | None = None,
        controller_factory: Callable[[Config], TorController] = TorController.from_config,
        ip_getter: Callable[..., ExitIP] = get_exit_ip,
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
                    return self._dispatch(verify=verify, wait=wait)
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
                    mode=self.config.rotation_mode,
                )
        return self._dispatch(verify=verify, wait=wait)

    def _dispatch(self, verify: bool | None = None, wait: bool | None = None) -> RotationResult:
        mode = self.config.rotation_mode
        if mode == "control":
            return self._rotate_control(verify=verify, wait=wait)
        if mode == "socks":
            return self._rotate_socks(verify=verify, wait=wait)
        # auto: use the control port when it answers, otherwise isolate by
        # SOCKS credentials. A refused connection is normal on Android/Orbot.
        if self._control_usable():
            return self._rotate_control(verify=verify, wait=wait)
        return self._rotate_socks(verify=verify, wait=wait)

    def _control_usable(self) -> bool:
        try:
            with self.controller_factory(self.config):
                return True
        except Exception:
            return False

    @staticmethod
    def _new_identity() -> tuple[str, str]:
        # Tor isolates circuits by the SOCKS username/password pair, so a fresh
        # pair means a fresh circuit even without the control port.
        return f"ipc-{secrets.token_hex(8)}", secrets.token_hex(16)

    def _rotate_control(self, verify: bool | None = None, wait: bool | None = None) -> RotationResult:
        started = self.clock()
        do_verify = self.config.verify if verify is None else verify
        do_wait = self.config.wait_for_change if wait is None else wait
        state = self.state_store.load()
        old_ip = state.last_ip

        # Establish a baseline when no IP is known. This also proves that the
        # SOCKS port works before sending a NEWNYM signal.
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
            return self._fail(
                started=started,
                mode="control",
                old_ip=old_ip,
                new_ip=None,
                is_tor=None,
                source=None,
                error=str(exc),
            )

        if not do_verify:
            return self._succeed_unverified(started=started, mode="control", old_ip=old_ip)

        new_ip: str | None = None
        is_tor: bool | None = None
        source: str | None = None
        last_check_error: str | None = None
        deadline = self.clock() + max(5.0, float(self.config.change_timeout))
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
                new_ip = None
                is_tor = False
                last_check_error = str(exc)
                break
            except (IPCheckError, OSError) as exc:
                last_check_error = str(exc)
            if self.clock() >= deadline:
                break

        return self._finish(
            started=started,
            mode="control",
            old_ip=old_ip,
            new_ip=new_ip,
            is_tor=is_tor,
            source=source,
            last_check_error=last_check_error,
        )

    def _rotate_socks(self, verify: bool | None = None, wait: bool | None = None) -> RotationResult:
        started = self.clock()
        do_verify = self.config.verify if verify is None else verify
        do_wait = self.config.wait_for_change if wait is None else wait
        state = self.state_store.load()
        old_ip = state.last_ip

        if do_verify and old_ip is None:
            try:
                baseline = self.ip_getter(self.config)
                old_ip = baseline.ip
                state.last_ip = baseline.ip
                state.last_is_tor = baseline.is_tor
                state.last_source = baseline.source
                self.state_store.save(state)
            except (IPCheckError, OSError) as exc:
                state.last_error = f"baseline check failed: {exc}"
                self.state_store.save(state)

        state.last_attempt = self.clock()
        state.last_error = None
        self.state_store.save(state)

        if not do_verify:
            # There is no persistent signal in SOCKS mode: the fresh identity
            # has to be used at least once, otherwise nothing rotates.
            username, password = self._new_identity()
            try:
                self.ip_getter(self.config, username=username, password=password)
            except (IPCheckError, OSError) as exc:
                return self._fail(
                    started=started,
                    mode="socks",
                    old_ip=old_ip,
                    new_ip=None,
                    is_tor=None,
                    source=None,
                    error=str(exc),
                )
            return self._succeed_unverified(started=started, mode="socks", old_ip=old_ip)

        new_ip: str | None = None
        is_tor: bool | None = None
        source: str | None = None
        last_check_error: str | None = None
        attempts = 0
        deadline = self.clock() + max(5.0, float(self.config.change_timeout))

        while True:
            username, password = self._new_identity()
            attempts += 1
            try:
                current = self.ip_getter(self.config, username=username, password=password)
                new_ip = current.ip
                is_tor = current.is_tor
                source = current.source
                if old_ip is None or new_ip != old_ip or not do_wait:
                    break
            except TorVerificationError as exc:
                new_ip = None
                is_tor = False
                last_check_error = str(exc)
                break
            except (IPCheckError, OSError) as exc:
                last_check_error = str(exc)
            if attempts >= SOCKS_ATTEMPTS or self.clock() >= deadline:
                break
            self.sleep(max(0.5, float(self.config.poll_interval)))

        if new_ip is not None and old_ip is not None and new_ip == old_ip and do_wait:
            warning = (
                f"the exit IP stayed {new_ip} after {attempts} fresh SOCKS identities; "
                "Tor may not isolate circuits by SOCKS credentials (IsolateSOCKSAuth)"
            )
        elif new_ip is not None and old_ip is not None and new_ip == old_ip:
            warning = "Tor reused the same exit IP; SOCKS rotation does not guarantee a different node"
        else:
            warning = None

        return self._finish(
            started=started,
            mode="socks",
            old_ip=old_ip,
            new_ip=new_ip,
            is_tor=is_tor,
            source=source,
            last_check_error=last_check_error,
            unchanged_warning=warning,
            attempts=attempts,
        )

    def _fail(
        self,
        *,
        started: float,
        mode: str,
        old_ip: str | None,
        new_ip: str | None,
        is_tor: bool | None,
        source: str | None,
        error: str,
    ) -> RotationResult:
        duration = int((self.clock() - started) * 1000)
        state = self.state_store.load()
        state.last_attempt = self.clock()
        state.last_source = source
        state.total_failures += 1
        state.last_error = error
        self.state_store.save(state)
        self.log.append(
            "rotation",
            ok=False,
            old_ip=old_ip,
            new_ip=new_ip,
            changed=False,
            is_tor=is_tor,
            source=source,
            duration_ms=duration,
            error=error,
            mode=mode,
        )
        return RotationResult(
            ok=False,
            changed=False,
            old_ip=old_ip,
            new_ip=new_ip,
            is_tor=is_tor,
            source=source,
            error=error,
            duration_ms=duration,
            mode=mode,
        )

    def _succeed_unverified(self, *, started: float, mode: str, old_ip: str | None) -> RotationResult:
        duration = int((self.clock() - started) * 1000)
        state = self.state_store.load()
        state.total_rotations += 1
        state.last_success = self.clock()
        state.last_error = None
        self.state_store.save(state)
        self.log.append(
            "rotation",
            ok=True,
            old_ip=old_ip,
            new_ip=None,
            changed=None,
            is_tor=None,
            duration_ms=duration,
            note="verification disabled",
            mode=mode,
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
            mode=mode,
        )

    def _finish(
        self,
        *,
        started: float,
        mode: str,
        old_ip: str | None,
        new_ip: str | None,
        is_tor: bool | None,
        source: str | None,
        last_check_error: str | None,
        unchanged_warning: str | None = None,
        attempts: int | None = None,
    ) -> RotationResult:
        duration = int((self.clock() - started) * 1000)
        state = self.state_store.load()
        state.last_attempt = self.clock()
        state.last_source = source
        extra = {"attempts": attempts} if attempts is not None else {}

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
                is_tor=is_tor,
                source=source,
                duration_ms=duration,
                error=error,
                mode=mode,
                **extra,
            )
            return RotationResult(
                ok=False,
                changed=False,
                old_ip=old_ip,
                new_ip=None,
                is_tor=is_tor,
                source=source,
                error=error,
                duration_ms=duration,
                mode=mode,
            )

        changed = old_ip is None or new_ip != old_ip

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
                changed=changed,
                is_tor=is_tor,
                source=source,
                duration_ms=duration,
                error=error,
                mode=mode,
                **extra,
            )
            return RotationResult(
                ok=False,
                changed=changed,
                old_ip=old_ip,
                new_ip=new_ip,
                is_tor=is_tor,
                source=source,
                error=error,
                duration_ms=duration,
                mode=mode,
            )

        warning = None
        if old_ip is not None and not changed:
            warning = unchanged_warning or (
                "Tor reused the same exit IP; NEWNYM does not guarantee a different node"
            )
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
            mode=mode,
            **extra,
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
            mode=mode,
        )
