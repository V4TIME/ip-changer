"""Configuration, paths and small persistence helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

APP_NAME = "ipchanger"
DISPLAY_NAME = "IP Changer"
VERSION = "1.1.1"

MIN_NEWNYM_INTERVAL = 10  # Tor accepts SIGNAL NEWNYM at most once per 10 s.
DEFAULT_CHECK_URLS = [
    "https://check.torproject.org/api/ip",
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
]


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return bool(os.environ.get("TERMUX_VERSION")) or "com.termux" in prefix


def _xdg_dir(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    if value:
        return Path(value).expanduser()
    return fallback


def config_dir() -> Path:
    override = os.environ.get("IPCHANGER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def config_path() -> Path:
    override = os.environ.get("IPCHANGER_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.json"


def state_dir() -> Path:
    override = os.environ.get("IPCHANGER_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return _xdg_dir("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_NAME


def log_path() -> Path:
    override = os.environ.get("IPCHANGER_LOG")
    if override:
        return Path(override).expanduser()
    return state_dir() / "ipchanger.log"


def daemon_log_path() -> Path:
    return state_dir() / "daemon.log"


def state_path() -> Path:
    return state_dir() / "state.json"


def pid_path() -> Path:
    return state_dir() / "ipchanger.pid"


def lock_path() -> Path:
    return state_dir() / "ipchanger.lock"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write *text* to *path* atomically and with restrictive permissions."""

    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


@dataclass
class Config:
    interval: int = 120
    control_host: str = "127.0.0.1"
    control_port: int = 9051
    socks_host: str = "127.0.0.1"
    socks_port: int = 9050
    cookie_path: str = ""
    auth_method: str = "auto"  # auto | cookie | password | null
    password: str = ""
    verify: bool = True
    wait_for_change: bool = True
    require_tor: bool = False
    change_timeout: int = 45
    poll_interval: float = 2.0
    check_urls: list[str] = field(default_factory=lambda: list(DEFAULT_CHECK_URLS))
    jitter: int = 0
    min_interval: int = MIN_NEWNYM_INTERVAL
    log_enabled: bool = True
    log_max_bytes: int = 1_048_576
    log_backups: int = 3
    user_agent: str = f"ip-changer/{VERSION} (+https://github.com/V4TIME/ip-changer)"

    def validate(self) -> "Config":
        try:
            self.interval = int(self.interval)
            self.control_port = int(self.control_port)
            self.socks_port = int(self.socks_port)
            self.change_timeout = int(self.change_timeout)
            self.jitter = int(self.jitter)
            self.min_interval = int(self.min_interval)
            self.log_max_bytes = int(self.log_max_bytes)
            self.log_backups = int(self.log_backups)
            self.poll_interval = float(self.poll_interval)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"numeric configuration value is invalid: {exc}") from exc

        if self.interval < MIN_NEWNYM_INTERVAL:
            raise ConfigError(
                f"interval must be at least {MIN_NEWNYM_INTERVAL} seconds; "
                "Tor ignores NEWNYM requests that arrive faster than that"
            )
        if self.interval > 30 * 24 * 3600:
            raise ConfigError("interval is unreasonably large (maximum 30 days)")
        if not (1 <= self.control_port <= 65535):
            raise ConfigError("control_port must be between 1 and 65535")
        if not (1 <= self.socks_port <= 65535):
            raise ConfigError("socks_port must be between 1 and 65535")
        if not self.control_host.strip():
            raise ConfigError("control_host must not be empty")
        if not self.socks_host.strip():
            raise ConfigError("socks_host must not be empty")
        if self.auth_method not in {"auto", "cookie", "password", "null"}:
            raise ConfigError("auth_method must be auto, cookie, password or null")
        if self.auth_method == "password" and not self.password:
            raise ConfigError("auth_method=password requires a password")
        if self.change_timeout < 5:
            raise ConfigError("change_timeout must be at least 5 seconds")
        if self.poll_interval < 0.25:
            raise ConfigError("poll_interval must be at least 0.25 seconds")
        if self.jitter < 0 or self.jitter > 100:
            raise ConfigError("jitter must be a percentage between 0 and 100")
        if self.min_interval < MIN_NEWNYM_INTERVAL:
            raise ConfigError(f"min_interval must be at least {MIN_NEWNYM_INTERVAL}")
        if not isinstance(self.check_urls, list) or not self.check_urls:
            raise ConfigError("check_urls must contain at least one URL")
        for url in self.check_urls:
            if not isinstance(url, str) or not re.match(r"^https?://", url):
                raise ConfigError(f"invalid check URL: {url!r}")
        if not isinstance(self.log_enabled, bool):
            raise ConfigError("log_enabled must be true or false")
        if self.log_max_bytes < 0:
            raise ConfigError("log_max_bytes must be >= 0")
        if self.log_backups < 0:
            raise ConfigError("log_backups must be >= 0")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        known = {item.name for item in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        return cls(**values).validate()

    def save(self, path: Path | None = None) -> Path:
        target = path or config_path()
        atomic_write_text(target, json.dumps(self.to_dict(), indent=2) + "\n")
        return target


def default_config() -> Config:
    return Config().validate()


def load_config(path: Path | None = None) -> Config:
    target = path or config_path()
    if not target.exists():
        return default_config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{target} must contain a JSON object")
    return Config.from_dict(raw)


def save_config(config: Config, path: Path | None = None) -> Path:
    config.validate()
    return config.save(path)


_DURATION_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[smhdw]?)", re.I)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str | int | float) -> int:
    """Parse ``90``, ``90s``, ``5m``, ``1h30m`` or ``00:02:00`` into seconds."""

    if isinstance(text, (int, float)):
        seconds = float(text)
    else:
        value = str(text).strip().lower()
        if not value:
            raise ConfigError("duration must not be empty")
        if ":" in value:
            parts = value.split(":")
            if len(parts) not in (2, 3):
                raise ConfigError(f"invalid duration: {text!r}")
            try:
                numbers = [float(part) for part in parts]
            except ValueError as exc:
                raise ConfigError(f"invalid duration: {text!r}") from exc
            if len(numbers) == 2:
                seconds = numbers[0] * 60 + numbers[1]
            else:
                seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        else:
            position = 0
            seconds = 0.0
            matched = False
            pure_number = bool(re.fullmatch(r"\d+(?:\.\d+)?", value))
            for match in _DURATION_RE.finditer(value):
                if match.start() != position:
                    raise ConfigError(f"invalid duration: {text!r}")
                number = float(match.group("value"))
                unit = match.group("unit").lower()
                if not unit and not pure_number:
                    raise ConfigError(f"invalid duration: {text!r} (missing unit)")
                seconds += number * _UNIT_SECONDS[unit]
                position = match.end()
                matched = True
            if not matched or position != len(value):
                raise ConfigError(f"invalid duration: {text!r}")
    if seconds <= 0:
        raise ConfigError("duration must be greater than zero")
    if seconds > 30 * 24 * 3600:
        raise ConfigError("duration must not exceed 30 days")
    return int(round(seconds))


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    total = max(0, int(round(float(seconds))))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def coerce_value(current: Any, raw: str) -> Any:
    """Convert a CLI string to the same type as the current config value."""

    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ConfigError(f"expected a boolean, got {raw!r}")
    if isinstance(current, int):
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"expected an integer, got {raw!r}") from exc
    if isinstance(current, float):
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"expected a number, got {raw!r}") from exc
    if isinstance(current, list):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = [part.strip() for part in raw.split(",") if part.strip()]
        if not isinstance(value, list):
            raise ConfigError("expected a JSON list or comma-separated values")
        return value
    return raw


def set_config_value(config: Config, key: str, raw: str) -> Config:
    if key not in {item.name for item in fields(Config)}:
        raise ConfigError(f"unknown configuration key: {key}")
    if key == "interval":
        setattr(config, key, parse_duration(raw))
    else:
        current = getattr(config, key)
        setattr(config, key, coerce_value(current, raw))
    return config.validate()


def iter_known_keys() -> Iterable[str]:
    return (item.name for item in fields(Config))
