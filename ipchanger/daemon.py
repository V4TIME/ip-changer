"""Start, stop and inspect the detached rotation daemon."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import Config, daemon_log_path, ensure_private_dir, pid_path
from .state import (
    StateStore,
    pid_alive,
    read_pid,
    remove_pid,
    running_pid,
    write_pid,
)


class DaemonError(RuntimeError):
    pass


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _tail(path: Path, lines: int = 20) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def start(
    config: Config,
    interval: int | None = None,
    jitter: int | None = None,
    wait_seconds: float = 5.0,
    config_file: Path | None = None,
) -> int:
    running = running_pid()
    if running:
        return running

    ensure_private_dir(daemon_log_path().parent)
    log_path = daemon_log_path()
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    command = [sys.executable, "-m", "ipchanger", "run"]
    if interval is not None:
        command.extend(["--interval", str(interval)])
    if jitter is not None:
        command.extend(["--jitter", str(jitter)])
    if config_file is not None:
        command.extend(["--config", str(config_file)])
    env = os.environ.copy()
    root = str(_package_root())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (os.pathsep + existing if existing else "")
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                cwd=str(Path.home()),
                env=env,
            )
    except OSError as exc:
        raise DaemonError(f"could not start the daemon: {exc}") from exc

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        pid = read_pid()
        if pid and pid_alive(pid):
            state = StateStore().load()
            state.pid = pid
            state.started_at = state.started_at or time.time()
            StateStore().save(state)
            return pid
        if process.poll() is not None:
            break
        time.sleep(0.1)

    try:
        process.terminate()
    except OSError:
        pass
    detail = _tail(log_path)
    raise DaemonError(
        "the daemon exited during startup"
        + (f"\n--- {log_path} ---\n{detail}" if detail else f" (see {log_path})")
    )


def stop(timeout: float = 10.0) -> bool:
    pid = running_pid()
    if not pid:
        remove_pid()
        state = StateStore().load()
        state.pid = None
        state.next_rotation = None
        StateStore().save(state)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_pid()
        return False
    except OSError as exc:
        raise DaemonError(f"could not stop pid {pid}: {exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        time.sleep(0.2)

    remove_pid()
    state = StateStore().load()
    state.pid = None
    state.next_rotation = None
    StateStore().save(state)
    return True


def restart(
    config: Config,
    interval: int | None = None,
    jitter: int | None = None,
    config_file: Path | None = None,
) -> int:
    stop()
    return start(config, interval=interval, jitter=jitter, config_file=config_file)


def pid_file() -> Path:
    return pid_path()
