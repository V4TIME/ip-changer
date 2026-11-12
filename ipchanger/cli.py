"""Command-line interface for IP Changer."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from . import daemon
from .config import (
    Config,
    ConfigError,
    DISPLAY_NAME,
    VERSION,
    config_path,
    coerce_value,
    format_duration,
    load_config,
    log_path,
    parse_duration,
    save_config,
    set_config_value,
    state_path,
)
from .control import TorControlError
from .doctor import format_checks, has_failures, run_checks
from .rotator import Rotator
from .scheduler import Scheduler
from .state import (
    LogWriter,
    StateStore,
    human_age,
    now_iso,
    pid_path,
    pretty_record,
    read_log_records,
    remove_pid,
    running_pid,
    write_pid,
)
from .torhttp import IPCheckError, ProxyError
from .tui import run_panel


def _load(args: argparse.Namespace) -> Config:
    return load_config(Path(args.config).expanduser() if args.config else None)


def _apply_overrides(config: Config, interval: str | None, jitter: int | None) -> Config:
    values = config.to_dict()
    if interval is not None:
        values["interval"] = parse_duration(interval)
    if jitter is not None:
        values["jitter"] = jitter
    return Config.from_dict(values)


def _iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _state_payload(config: Config, config_file: Path | None) -> dict[str, Any]:
    state = StateStore().load()
    pid = running_pid()
    next_in = None
    if pid and state.next_rotation:
        next_in = max(0, int(round(state.next_rotation - time.time())))
    return {
        "version": VERSION,
        "running": bool(pid),
        "pid": pid,
        "interval": config.interval,
        "interval_human": format_duration(config.interval),
        "jitter": config.jitter,
        "last_ip": state.last_ip,
        "previous_ip": state.previous_ip,
        "is_tor": state.last_is_tor,
        "source": state.last_source,
        "last_attempt": _iso(state.last_attempt),
        "last_success": _iso(state.last_success),
        "last_error": state.last_error,
        "next_rotation": _iso(state.next_rotation),
        "next_in_seconds": next_in,
        "total_rotations": state.total_rotations,
        "total_failures": state.total_failures,
        "config_file": str(config_file or config_path()),
        "state_file": str(state_path()),
        "log_file": str(log_path()),
    }


def cmd_panel(args: argparse.Namespace) -> int:
    config = _load(args)
    config_file = Path(args.config).expanduser() if args.config else None
    return run_panel(config, config_file)


def cmd_run(args: argparse.Namespace) -> int:
    config = _apply_overrides(_load(args), args.interval, args.jitter)
    pid = running_pid()
    if pid and pid != os.getpid():
        print(f"IP Changer is already running with pid {pid}.", file=sys.stderr)
        return 1

    write_pid(pid_path(), os.getpid())
    state_store = StateStore()
    state = state_store.load()
    state.pid = os.getpid()
    state.started_at = state.started_at or time.time()
    state_store.save(state)
    log_writer = LogWriter(config)
    log_writer.append("daemon_start", pid=os.getpid(), interval=config.interval, jitter=config.jitter)

    def log(message: str) -> None:
        print(f"[{now_iso()}] {message}", flush=True)

    rotator = Rotator(config, state_store=state_store, log_writer=log_writer)
    scheduler = Scheduler(
        config,
        rotator,
        state_store=state_store,
        on_result=lambda result: None,
        log=log,
    )

    def handle_signal(signum: int, frame: Any) -> None:  # noqa: ARG001
        scheduler.stop()

    old_term = signal.signal(signal.SIGTERM, handle_signal)
    old_int = signal.signal(signal.SIGINT, handle_signal)
    try:
        return scheduler.run(
            interval=config.interval,
            jitter=config.jitter,
            once=args.once,
            immediate=not args.no_immediate,
        )
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        remove_pid()
        state = state_store.load()
        state.pid = None
        state.next_rotation = None
        state_store.save(state)
        log_writer.append("daemon_stop", pid=os.getpid())


def cmd_start(args: argparse.Namespace) -> int:
    config = _apply_overrides(_load(args), args.interval, args.jitter)
    existing = running_pid()
    if existing:
        print(f"IP Changer is already running with pid {existing}.")
        return 0
    config_file = Path(args.config).expanduser() if args.config else None
    pid = daemon.start(
        config,
        interval=config.interval,
        jitter=config.jitter,
        config_file=config_file,
    )
    print(f"IP Changer started with pid {pid} (interval {format_duration(config.interval)}).")
    print(f"Daemon output: {daemon.daemon_log_path()}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:  # noqa: ARG001
    if daemon.stop():
        print("IP Changer stopped.")
    else:
        print("IP Changer is not running.")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    config = _apply_overrides(_load(args), args.interval, args.jitter)
    config_file = Path(args.config).expanduser() if args.config else None
    daemon.stop()
    pid = daemon.start(
        config,
        interval=config.interval,
        jitter=config.jitter,
        config_file=config_file,
    )
    print(f"IP Changer restarted with pid {pid}.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    config_file = Path(args.config).expanduser() if args.config else None
    payload = _state_payload(config, config_file)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    state = StateStore().load()
    print(f"{DISPLAY_NAME} {VERSION}")
    print("=" * 60)
    print(f"Service       : {'RUNNING (pid ' + str(payload['pid']) + ')' if payload['running'] else 'STOPPED'}")
    print(f"Tor exit IP   : {state.last_ip or 'not checked'}")
    tor_text = (
        "verified"
        if state.last_is_tor is True
        else "NOT using Tor"
        if state.last_is_tor is False
        else "unknown"
    )
    print(f"Tor check     : {tor_text}")
    print(f"Previous IP   : {state.previous_ip or '-'}")
    last_change = (
        f"{payload['last_success']} ({human_age(state.last_success)})"
        if payload["last_success"]
        else "never"
    )
    print(f"Last change   : {last_change}")
    next_text = "not scheduled"
    if payload["running"] and payload["next_in_seconds"] is not None:
        next_text = f"in {format_duration(payload['next_in_seconds'])}"
    elif payload["running"]:
        next_text = "starting..."
    print(f"Next change   : {next_text}")
    print(f"Interval      : {format_duration(config.interval)} ({config.interval}s), jitter {config.jitter}%")
    print(f"Rotations     : {state.total_rotations} ok / {state.total_failures} failed")
    print(f"Logging       : {'enabled' if config.log_enabled else 'disabled'}")
    print(f"Last error    : {state.last_error or '-'}")
    print(f"Config        : {payload['config_file']}")
    print(f"State         : {payload['state_file']}")
    print(f"Log           : {payload['log_file']}")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    config = _load(args)
    rotator = Rotator(config)
    result = rotator.rotate(verify=not args.no_verify, wait=not args.no_wait)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    elif result.ok:
        if result.new_ip:
            change = "changed" if result.changed else "unchanged"
            print(
                f"OK: {result.old_ip or '?'} -> {result.new_ip} ({change}, "
                f"{result.duration_ms} ms, Tor={result.is_tor})"
            )
        else:
            print(f"OK: NEWNYM sent ({result.duration_ms} ms; verification disabled)")
        if result.error:
            print(f"Warning: {result.error}")
    else:
        print(f"FAILED: {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_ip(args: argparse.Namespace) -> int:
    config = _load(args)
    result = Rotator(config).fetch_ip()
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(result.ip)
    return 0


def _redact(config: Config) -> dict[str, Any]:
    values = config.to_dict()
    if values.get("password"):
        values["password"] = "***"
    return values


def cmd_config(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else None
    config = load_config(path)
    target = path or config_path()
    if args.action == "list":
        values = _redact(config)
        if args.json:
            print(json.dumps(values, indent=2))
        else:
            width = max(len(key) for key in values)
            for key, value in values.items():
                if isinstance(value, (dict, list)):
                    rendered = json.dumps(value)
                elif isinstance(value, bool):
                    rendered = "true" if value else "false"
                else:
                    rendered = str(value)
                print(f"{key:<{width}} = {rendered}")
        return 0
    if args.action == "get":
        if not args.key:
            print("ipchanger config get: a key is required", file=sys.stderr)
            return 2
        values = _redact(config)
        if args.key not in values:
            print(f"unknown configuration key: {args.key}", file=sys.stderr)
            return 2
        value = values[args.key]
        if isinstance(value, (dict, list)):
            print(json.dumps(value, indent=2))
        elif isinstance(value, bool):
            print("true" if value else "false")
        else:
            print(value)
        return 0
    if args.action == "set":
        if not args.key or args.value is None:
            print("ipchanger config set: KEY and VALUE are required", file=sys.stderr)
            return 2
        try:
            config = set_config_value(config, args.key, args.value)
        except ConfigError as exc:
            print(f"invalid value: {exc}", file=sys.stderr)
            return 2
        save_config(config, target)
        shown = "***" if args.key == "password" and args.value else args.value
        print(f"{args.key} = {shown}")
        if running_pid():
            print("Note: restart the daemon to apply this change: ipchanger restart")
        return 0
    if args.action == "reset":
        config = Config().validate()
        save_config(config, target)
        print(f"Configuration reset to defaults: {target}")
        if running_pid():
            print("Note: restart the daemon to apply this change: ipchanger restart")
        return 0
    print(f"unknown config action: {args.action}", file=sys.stderr)
    return 2


def _print_log_record(record: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(record, ensure_ascii=False))
    else:
        print(pretty_record(record))


def cmd_logs(args: argparse.Namespace) -> int:
    config_file = Path(args.config).expanduser() if args.config else None
    config = load_config(config_file)
    target = config_file or config_path()
    if args.on or args.off or args.status:
        if args.on or args.off:
            config.log_enabled = bool(args.on)
            save_config(config, target)
        state = "enabled" if config.log_enabled else "disabled"
        print(f"Logging {state}.")
        print(f"Log file: {log_path()}")
        if (args.on or args.off) and running_pid():
            print("Note: restart the daemon to apply this change: ipchanger restart")
        return 0

    path = log_path()
    records = read_log_records(path, limit=args.lines)
    if not args.follow:
        if not records:
            if not config.log_enabled:
                print(f"Logging is disabled; no entries will be written (log: {path}).")
                print("Enable it with: ipchanger logs --on")
            else:
                print(f"No log entries yet (log: {path})")
            return 0
        for record in records:
            _print_log_record(record, args.json)
        return 0

    for record in records:
        _print_log_record(record, args.json)
    offset = 0
    try:
        offset = path.stat().st_size
    except OSError:
        pass
    sys.stdout.flush()
    try:
        while True:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size < offset:
                offset = 0
            if size > offset:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        _print_log_record(record, args.json)
                sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else None
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"[FAIL] Configuration  {exc}")
        return 1
    checks = run_checks(config, path, check_ip=not args.quick)
    print(format_checks(checks))
    return 1 if has_failures(checks) else 0


def build_parser() -> argparse.ArgumentParser:
    examples = """examples:
  ipchanger                     open the control panel
  ipchanger start               start automatic rotation in the background
  ipchanger status              show the current exit IP and countdown
  ipchanger rotate              rotate once and verify the new exit IP
  ipchanger config set interval 5m
  ipchanger doctor              check Tor, cookies and network access
  ipchanger run --once          one rotation in the foreground (tests/CI)

Traffic must be sent through Tor's SOCKS port, normally 127.0.0.1:9050.
"""
    parser = argparse.ArgumentParser(
        prog="ipchanger",
        description="Rotate the Tor exit IP from a terminal or Termux control panel.",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--config", metavar="PATH", help="use a custom configuration file")
    subparsers = parser.add_subparsers(dest="command")

    panel = subparsers.add_parser("panel", help="open the interactive control panel")
    panel.set_defaults(func=cmd_panel)

    for name, help_text in (
        ("run", "run the scheduler in the foreground"),
        ("start", "start the background rotation daemon"),
        ("restart", "restart the background rotation daemon"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--interval", help="rotation interval (90, 5m, 1h30m)")
        command.add_argument("--jitter", type=int, help="random extra delay percentage (0-100)")
        if name == "run":
            command.add_argument("--once", action="store_true", help="rotate once and exit")
            command.add_argument(
                "--no-immediate",
                action="store_true",
                help="wait for the first interval instead of rotating at startup",
            )
        command.set_defaults(func={"run": cmd_run, "start": cmd_start, "restart": cmd_restart}[name])

    stop = subparsers.add_parser("stop", help="stop the background rotation daemon")
    stop.set_defaults(func=cmd_stop)

    status = subparsers.add_parser("status", help="show daemon, IP and countdown status")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.set_defaults(func=cmd_status)

    rotate = subparsers.add_parser("rotate", help="send NEWNYM once and verify the new IP")
    rotate.add_argument("--json", action="store_true", help="machine-readable output")
    rotate.add_argument("--no-verify", action="store_true", help="do not check the new exit IP")
    rotate.add_argument("--no-wait", action="store_true", help="do not wait for the IP to change")
    rotate.set_defaults(func=cmd_rotate)

    ip_command = subparsers.add_parser("ip", help="show the current Tor exit IP")
    ip_command.add_argument("--json", action="store_true", help="machine-readable output")
    ip_command.set_defaults(func=cmd_ip)

    config = subparsers.add_parser("config", help="inspect or change configuration")
    config.add_argument("action", choices=["list", "get", "set", "reset"])
    config.add_argument("key", nargs="?", help="configuration key")
    config.add_argument("value", nargs="?", help="new value for 'set'")
    config.add_argument("--json", action="store_true", help="machine-readable output")
    config.set_defaults(func=cmd_config)

    logs = subparsers.add_parser("logs", help="show, follow, enable or disable the rotation log")
    logs.add_argument("--lines", type=int, default=50, help="number of entries (default 50)")
    logs.add_argument("--follow", "-f", action="store_true", help="follow new entries")
    logs.add_argument("--json", action="store_true", help="print raw JSONL")
    toggle = logs.add_mutually_exclusive_group()
    toggle.add_argument("--on", action="store_true", help="enable rotation logging")
    toggle.add_argument("--off", action="store_true", help="disable rotation logging")
    toggle.add_argument("--status", action="store_true", help="show whether logging is enabled")
    logs.set_defaults(func=cmd_logs)

    doctor = subparsers.add_parser("doctor", help="diagnose Tor, authentication and networking")
    doctor.add_argument("--quick", action="store_true", help="skip the live exit IP check")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments)
    if not getattr(args, "command", None):
        args = parser.parse_args([*arguments, "panel"])
    if args.command == "logs" and args.lines < 0:
        parser.error("--lines must be >= 0")
    try:
        return int(args.func(args))
    except (ConfigError, TorControlError, IPCheckError, ProxyError, daemon.DaemonError) as exc:
        print(f"ipchanger: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"ipchanger: {exc}", file=sys.stderr)
        return 1
