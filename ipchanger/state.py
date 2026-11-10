"""Durable state, JSONL logs, PID files and an inter-process lock."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable

from .config import (
    Config,
    atomic_write_text,
    ensure_private_dir,
    lock_path,
    log_path,
    pid_path,
    state_path,
)

try:  # Linux and Termux both have fcntl; keep the import guard for portability.
    import fcntl
except ImportError:  # pragma: no cover - not expected on supported platforms
    fcntl = None  # type: ignore[assignment]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def human_age(timestamp: float | None) -> str:
    if not timestamp:
        return "never"
    delta = max(0, int(time.time() - timestamp))
    if delta < 60:
        return f"{delta}s ago"
    minutes, seconds = divmod(delta, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s ago"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m ago"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h ago"


@dataclass
class State:
    last_ip: str | None = None
    previous_ip: str | None = None
    last_is_tor: bool | None = None
    last_attempt: float | None = None
    last_newnym: float | None = None
    last_success: float | None = None
    last_error: str | None = None
    total_rotations: int = 0
    total_failures: int = 0
    started_at: float | None = None
    next_rotation: float | None = None
    pid: int | None = None
    last_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "State":
        known = {item.name for item in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_path()

    def load(self) -> State:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return State()
        if not isinstance(raw, dict):
            return State()
        return State.from_dict(raw)

    def save(self, state: State) -> None:
        ensure_private_dir(self.path.parent)
        atomic_write_text(self.path, json.dumps(state.to_dict(), indent=2) + "\n")

    def update(self, **values: Any) -> State:
        state = self.load()
        for key, value in values.items():
            if not hasattr(state, key):
                raise AttributeError(key)
            setattr(state, key, value)
        self.save(state)
        return state


class FileLock:
    """Best-effort exclusive lock so a manual rotation cannot race the daemon."""

    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        self.path = path
        self.timeout = timeout
        self._handle = None

    def __enter__(self) -> "FileLock":
        ensure_private_dir(self.path.parent)
        self._handle = open(self.path, "a+", encoding="utf-8")
        if fcntl is None:  # pragma: no cover
            return self
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise TimeoutError("another IP rotation is already running")
                time.sleep(0.1)
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._handle is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        self._handle.close()
        self._handle = None


class LogWriter:
    """Small JSONL log with size-based rotation."""

    def __init__(self, config: Config, path: Path | None = None) -> None:
        self.config = config
        self.path = path or log_path()

    def _rotate(self) -> None:
        if self.config.log_max_bytes <= 0 or not self.path.exists():
            return
        try:
            if self.path.stat().st_size < self.config.log_max_bytes:
                return
        except OSError:
            return
        backups = self.config.log_backups
        if backups <= 0:
            try:
                self.path.unlink()
            except OSError:
                pass
            return
        for index in range(backups, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists() and index == backups:
                try:
                    source.unlink()
                except OSError:
                    pass
            elif source.exists():
                try:
                    os.replace(source, target)
                except OSError:
                    pass
        try:
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        except OSError:
            pass

    def append(self, event: str, **values: Any) -> dict[str, Any]:
        ensure_private_dir(self.path.parent)
        self._rotate()
        record: dict[str, Any] = {
            "ts": now_iso(),
            "event": event,
            **values,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return record


def read_log_records(path: Path | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    target = path or log_path()
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    if limit is not None:
        records = records[-limit:]
    return records


def write_pid(path: Path, pid: int) -> None:
    ensure_private_dir(path.parent)
    atomic_write_text(path, f"{pid}\n")


def read_pid(path: Path | None = None) -> int | None:
    target = path or pid_path()
    try:
        text = target.read_text(encoding="utf-8").strip()
        return int(text)
    except (OSError, ValueError):
        return None


def remove_pid(path: Path | None = None) -> None:
    target = path or pid_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")


def is_our_process(pid: int) -> bool:
    command = _cmdline(pid)
    if not command:
        # /proc is unavailable (or process exited); fall back to liveness.
        return pid_alive(pid)
    return "ipchanger" in command or "ip-changer" in command


def running_pid(path: Path | None = None) -> int | None:
    target = path or pid_path()
    pid = read_pid(target)
    if pid and pid_alive(pid) and is_our_process(pid):
        return pid
    if pid:
        remove_pid(target)
    return None


def pretty_record(record: dict[str, Any]) -> str:
    event = str(record.get("event", "?"))
    timestamp = str(record.get("ts", ""))
    bits = [timestamp, event]
    if record.get("old_ip") or record.get("new_ip"):
        bits.append(f"{record.get('old_ip') or '?'} -> {record.get('new_ip') or '?'}")
    if record.get("is_tor") is not None:
        bits.append("tor=yes" if record["is_tor"] else "tor=no")
    if record.get("duration_ms") is not None:
        bits.append(f"{record['duration_ms']}ms")
    if record.get("error"):
        bits.append(f"error={record['error']}")
    return "  ".join(str(part) for part in bits if part != "")


def last_records(limit: int = 10, path: Path | None = None) -> Iterable[dict[str, Any]]:
    return read_log_records(path, limit=limit)
