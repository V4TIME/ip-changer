"""Environment diagnostics shared by the CLI and the control panel."""

from __future__ import annotations

import grp
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import (
    Config,
    ConfigError,
    config_path,
    ensure_private_dir,
    is_termux,
    log_path,
    state_dir,
)
from .control import TorControlError, TorController
from .state import running_pid
from .torhttp import IPCheckError, ProxyError, check_socks_port, get_exit_ip


@dataclass
class Check:
    status: str  # OK | WARN | FAIL | INFO
    name: str
    detail: str


def _group_names() -> set[str]:
    names: set[str] = set()
    for gid in os.getgroups():
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except (KeyError, OSError):
            pass
    return names


def _tor_binary() -> tuple[str | None, str | None]:
    binary = shutil.which("tor")
    if not binary:
        return None, None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return binary, None
    output = (result.stdout or result.stderr).strip().splitlines()
    return binary, output[0] if output else None


def _systemd_service() -> tuple[bool, str]:
    systemctl = shutil.which("systemctl")
    if not systemctl or not Path("/run/systemd/system").exists():
        return False, "systemd not available"
    try:
        result = subprocess.run(
            [systemctl, "is-active", "ipchanger.service"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return True, f"could not query ipchanger.service: {exc}"
    return True, result.stdout.strip() or "unknown"


def run_checks(
    config: Config,
    config_file: Path | None = None,
    check_ip: bool = True,
) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "OK" if sys.version_info >= (3, 10) else "FAIL",
            "Python",
            f"{platform.python_version()} ({sys.executable})",
        )
    )
    checks.append(
        Check(
            "INFO",
            "Platform",
            f"{platform.system()} {platform.release()}"
            + (" (Termux)" if is_termux() else ""),
        )
    )
    checks.append(Check("INFO", "Config file", str(config_file or config_path())))
    checks.append(
        Check(
            "INFO",
            "Rotation logging",
            f"enabled ({log_path()})"
            if config.log_enabled
            else "disabled (enable with: ipchanger logs --on)",
        )
    )
    checks.append(
        Check(
            "OK",
            "Interval",
            f"{config.interval}s (minimum {config.min_interval}s, jitter {config.jitter}%)",
        )
    )

    try:
        ensure_private_dir(state_dir())
        probe = state_dir() / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("OK", "State directory", f"{state_dir()} is writable"))
    except OSError as exc:
        checks.append(Check("FAIL", "State directory", f"cannot write to {state_dir()}: {exc}"))

    binary, version = _tor_binary()
    if binary:
        checks.append(Check("OK", "Tor binary", f"{binary}: {version or 'version unknown'}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "Tor binary",
                "tor was not found in PATH (install the 'tor' package)",
            )
        )

    controller: TorController | None = None
    try:
        controller = TorController.from_config(config)
        info = controller.connect()
        tor_version = info.tor_version or controller.get_version() or "unknown"
        methods = ", ".join(sorted(info.methods)) or "unknown"
        checks.append(
            Check(
                "OK",
                "Control port",
                f"{config.control_host}:{config.control_port} authenticated; "
                f"Tor {tor_version}; auth methods: {methods}",
            )
        )
        cookie_path = config.cookie_path or (str(info.cookie_path) if info.cookie_path else "")
        if cookie_path:
            cookie = Path(cookie_path)
            readable = os.access(cookie, os.R_OK)
            checks.append(
                Check(
                    "OK" if readable else "WARN",
                    "Cookie file",
                    f"{cookie} ({'readable' if readable else 'not readable by this user'})",
                )
            )
    except (TorControlError, OSError) as exc:
        checks.append(Check("FAIL", "Control port", str(exc)))
    finally:
        if controller is not None:
            controller.close()

    if check_socks_port(config.socks_host, config.socks_port):
        checks.append(
            Check("OK", "SOCKS port", f"{config.socks_host}:{config.socks_port} is accepting connections")
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "SOCKS port",
                f"cannot connect to {config.socks_host}:{config.socks_port}",
            )
        )

    groups = _group_names()
    tor_groups = groups & {"tor", "debian-tor", "toranon", "_tor"}
    if tor_groups:
        checks.append(Check("OK", "Tor group", "member of " + ", ".join(sorted(tor_groups))))
    elif controller is not None:
        checks.append(
            Check(
                "WARN",
                "Tor group",
                "not a member of a Tor group; cookie authentication may fail in a new shell",
            )
        )

    has_systemd, service_state = _systemd_service()
    if has_systemd:
        status = "OK" if service_state == "active" else "WARN"
        checks.append(Check(status, "systemd service", f"ipchanger.service: {service_state}"))
    else:
        checks.append(Check("INFO", "systemd service", "not used on this platform"))

    pid = running_pid()
    checks.append(
        Check(
            "OK" if pid else "INFO",
            "Background daemon",
            f"running with pid {pid}" if pid else "not running (use: ipchanger start)",
        )
    )

    if check_ip:
        try:
            result = get_exit_ip(config)
            if result.is_tor is True:
                status = "OK"
                tor_text = "confirmed Tor exit"
            elif result.is_tor is False:
                status = "FAIL"
                tor_text = "NOT using Tor"
            else:
                status = "WARN"
                tor_text = "Tor status not confirmed by the check service"
            checks.append(
                Check(status, "Exit IP check", f"{result.ip} via {result.source} ({tor_text})")
            )
        except (IPCheckError, ProxyError, OSError) as exc:
            checks.append(Check("FAIL", "Exit IP check", str(exc)))

    return checks


def has_failures(checks: list[Check]) -> bool:
    return any(check.status == "FAIL" for check in checks)


def format_checks(checks: list[Check]) -> str:
    labels = {"OK": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[INFO]"}
    width = max((len(check.name) for check in checks), default=0)
    return "\n".join(
        f"{labels.get(check.status, check.status):6} {check.name:<{width}}  {check.detail}"
        for check in checks
    )


def parse_config_safely(path: Path | None = None) -> tuple[Config | None, Check | None]:
    from .config import load_config

    try:
        return load_config(path), None
    except ConfigError as exc:
        return None, Check("FAIL", "Configuration", str(exc))
