"""The interactive terminal control panel (works in Termux and a normal TTY)."""

from __future__ import annotations

import curses
import json
import os
import queue
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import daemon
from .config import (
    Config,
    ConfigError,
    VERSION,
    config_path,
    format_duration,
    load_config,
    parse_duration,
    save_config,
)
from .doctor import format_checks, run_checks
from .rotator import Rotator, RotationResult
from .state import (
    LogWriter,
    State,
    StateStore,
    human_age,
    pretty_record,
    read_log_records,
    running_pid,
    tail_log_records,
)
from .torhttp import ExitIP


def render_text_dashboard(
    config: Config,
    state: State | None = None,
    config_file: Path | None = None,
) -> str:
    """A plain-text fallback for non-interactive terminals and documentation."""

    state = state or StateStore().load()
    pid = running_pid()
    next_text = "-"
    if pid and state.next_rotation:
        next_text = f"in {format_duration(max(0, state.next_rotation - time.time()))}"
    tor_text = (
        "verified"
        if state.last_is_tor is True
        else "NOT using Tor"
        if state.last_is_tor is False
        else "unknown"
    )
    recent = tail_log_records(1)
    last_event = pretty_record(recent[-1]) if recent else "no events yet"
    lines = [
        f"IP Changer {VERSION}",
        "=" * 58,
        f"Service       : {'RUNNING (pid ' + str(pid) + ')' if pid else 'STOPPED'}",
        f"Tor exit IP   : {state.last_ip or 'not checked'}",
        f"Tor check     : {tor_text}",
        f"Previous IP   : {state.previous_ip or '-'}",
        "Last change   : "
        + (
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.last_success))}"
            f" ({human_age(state.last_success)})"
            if state.last_success
            else "never"
        ),
        f"Next change   : {next_text}",
        f"Interval      : {format_duration(config.interval)} ({config.interval}s)",
        f"Jitter        : {config.jitter}%",
        f"Options       : verify {'on' if config.verify else 'off'}, logs {'on' if config.log_enabled else 'off'}",
        f"Rotations     : {state.total_rotations} ok / {state.total_failures} failed",
        f"Last event    : {last_event}",
        f"Last error    : {state.last_error or '-'}",
        f"Config        : {config_file or config_path()}",
        "=" * 58,
        "Start the panel with: ipchanger panel",
    ]
    return "\n".join(lines)


class _Dashboard:
    def __init__(self, config: Config, config_file: Path | None = None) -> None:
        self.config = config
        self.config_file = config_file
        self.state_store = StateStore()
        self.rotator = Rotator(config, state_store=self.state_store, log_writer=LogWriter(config))
        self.jobs: queue.Queue[tuple[str, Any, Exception | None]] = queue.Queue()
        self.job_thread: threading.Thread | None = None
        self.message = "Ready. Press ? for help."
        self.message_until = time.monotonic() + 8
        self._config_mtime = self._mtime(config_file)
        self._colors: dict[str, int] = {}

    @staticmethod
    def _mtime(path: Path | None) -> int | None:
        if path is None:
            return None
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def set_message(self, text: str, seconds: float = 6.0) -> None:
        self.message = text
        self.message_until = time.monotonic() + seconds

    def apply_config(self, new_config: Config, path: Path | None = None) -> Path:
        saved = save_config(new_config, path or self.config_file)
        self.config = new_config
        self.rotator.config = new_config
        self.rotator.log.config = new_config
        self._config_mtime = self._mtime(saved)
        return saved

    def reload_if_changed(self) -> None:
        path = self.config_file or config_path()
        mtime = self._mtime(path)
        if mtime is not None and mtime != self._config_mtime:
            try:
                self.apply_config(load_config(path), path)
                self.set_message("Configuration reloaded from disk.")
            except ConfigError as exc:
                self.set_message(f"Config reload failed: {exc}", 10)

    def _start_job(self, name: str, function: Callable[[], Any]) -> None:
        if self.job_thread is not None and self.job_thread.is_alive():
            self.set_message("Another operation is still running; please wait.")
            return

        def worker() -> None:
            try:
                self.jobs.put((name, function(), None))
            except Exception as exc:
                self.jobs.put((name, None, exc))

        self.job_thread = threading.Thread(target=worker, name=f"ipchanger-{name}", daemon=True)
        self.job_thread.start()
        self.set_message(f"{name}...")

    def poll_jobs(self) -> None:
        while True:
            try:
                name, result, error = self.jobs.get_nowait()
            except queue.Empty:
                break
            if error is not None:
                self.set_message(f"{name} failed: {error}", 10)
            elif isinstance(result, RotationResult):
                if result.ok:
                    changed = "new IP" if result.changed else "IP unchanged"
                    self.set_message(
                        f"Rotated ({changed}): {result.old_ip or '?'} -> {result.new_ip or '?'}"
                        + (f" [{result.error}]" if result.error else ""),
                        10,
                    )
                else:
                    self.set_message(f"Rotation failed: {result.error}", 12)
            elif isinstance(result, ExitIP):
                self.set_message(f"Exit IP: {result.ip} (Tor: {result.is_tor})", 8)
            else:
                self.set_message(f"{name} finished.", 5)


def _safe_addstr(win: Any, y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        height, width = win.getmaxyx()
    except curses.error:
        return
    if y < 0 or y >= height or x < 0 or x >= width:
        return
    text = str(text).replace("\t", "    ")
    text = text[: max(0, width - x - 1)]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _init_colors() -> dict[str, int]:
    colors: dict[str, int] = {}
    try:
        if not curses.has_colors():
            return colors
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        curses.init_pair(1, curses.COLOR_CYAN, background)
        curses.init_pair(2, curses.COLOR_GREEN, background)
        curses.init_pair(3, curses.COLOR_YELLOW, background)
        curses.init_pair(4, curses.COLOR_RED, background)
        curses.init_pair(5, curses.COLOR_WHITE, background)
        colors = {
            "title": curses.color_pair(1) | curses.A_BOLD,
            "ok": curses.color_pair(2) | curses.A_BOLD,
            "warn": curses.color_pair(3) | curses.A_BOLD,
            "error": curses.color_pair(4) | curses.A_BOLD,
            "value": curses.color_pair(5),
            "dim": curses.A_DIM,
        }
    except curses.error:
        return {}
    return colors


def _prompt(stdscr: Any, label: str, default: str) -> str:
    height, width = stdscr.getmaxyx()
    row = max(0, height - 2)
    stdscr.timeout(-1)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        curses.echo()
        _safe_addstr(stdscr, row, 0, " " * max(0, width - 1))
        _safe_addstr(stdscr, row, 0, label[: max(0, width - len(label) - 1)])
        stdscr.refresh()
        start = min(len(label), max(0, width - 2))
        raw = stdscr.getstr(row, start, max(10, min(60, width - start - 1)))
        value = raw.decode("utf-8", errors="replace").strip()
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.timeout(1000)
    return value or default


def _text_view(stdscr: Any, title: str, lines: list[str]) -> None:
    offset = 0
    stdscr.timeout(-1)
    try:
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            _safe_addstr(stdscr, 0, 0, title[: width - 1], curses.A_BOLD)
            _safe_addstr(stdscr, 1, 0, "-" * max(0, width - 1))
            body_height = max(1, height - 4)
            visible = lines[offset : offset + body_height]
            for index, line in enumerate(visible):
                _safe_addstr(stdscr, 2 + index, 0, line[: width - 1])
            _safe_addstr(
                stdscr,
                height - 2,
                0,
                f"[up/down] scroll  [q] back   {offset + 1}-{min(len(lines), offset + body_height)}/{len(lines)}"[
                    : width - 1
                ],
                curses.A_DIM,
            )
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), 27):
                break
            if key in (curses.KEY_UP, ord("k")):
                offset = max(0, offset - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                offset = min(max(0, len(lines) - 1), offset + 1)
            elif key == curses.KEY_PPAGE:
                offset = max(0, offset - body_height)
            elif key == curses.KEY_NPAGE:
                offset = min(max(0, len(lines) - 1), offset + body_height)
            elif key == curses.KEY_RESIZE:
                continue
    finally:
        stdscr.timeout(1000)


def _draw(stdscr: Any, dash: _Dashboard, colors: dict[str, int]) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < 14 or width < 50:
        _safe_addstr(stdscr, 0, 0, "IP Changer - terminal is too small", curses.A_BOLD)
        _safe_addstr(stdscr, 2, 0, f"{width}x{height}; resize to at least 50x14")
        _safe_addstr(stdscr, height - 1, 0, "q = quit")
        stdscr.refresh()
        return

    state = dash.state_store.load()
    pid = running_pid()
    running = bool(pid)
    tor_text = (
        "verified"
        if state.last_is_tor is True
        else "NOT using Tor"
        if state.last_is_tor is False
        else "unknown"
    )
    tor_attr = (
        colors.get("ok", 0)
        if state.last_is_tor is True
        else colors.get("error", 0)
        if state.last_is_tor is False
        else colors.get("warn", 0)
    )
    next_text = "-"
    if running and state.next_rotation:
        next_text = f"in {format_duration(max(0, state.next_rotation - time.time()))}"
    elif running:
        next_text = "starting..."

    title = f" IP Changer {VERSION} "
    _safe_addstr(stdscr, 0, 0, "+" + title + "-" * max(0, width - len(title) - 2) + "+", colors.get("title", 0))
    for y in range(1, height - 1):
        _safe_addstr(stdscr, y, 0, "|")
        _safe_addstr(stdscr, y, width - 1, "|")
    _safe_addstr(stdscr, height - 1, 0, "+" + "-" * max(0, width - 2) + "+")

    recent = tail_log_records(1)
    last_event = pretty_record(recent[-1]) if recent else "no events yet"
    rows: list[tuple[str, str, int]] = [
        ("Service", f"RUNNING  pid {pid}" if running else "STOPPED", colors.get("ok" if running else "dim", 0)),
        ("Tor exit IP", state.last_ip or "not checked", colors.get("value", 0)),
        ("Tor check", tor_text, tor_attr),
        ("Previous IP", state.previous_ip or "-", colors.get("value", 0)),
        (
            "Last change",
            (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.last_success))
                + f"  ({human_age(state.last_success)})"
            )
            if state.last_success
            else "never",
            colors.get("value", 0),
        ),
        ("Next change", next_text, colors.get("value", 0)),
        (
            "Interval",
            f"{format_duration(dash.config.interval)}  ({dash.config.interval}s, jitter {dash.config.jitter}%)",
            colors.get("value", 0),
        ),
        (
            "Options",
            f"verify {'on' if dash.config.verify else 'off'}   logs {'on' if dash.config.log_enabled else 'off'}",
            colors.get("value", 0),
        ),
        (
            "Rotations",
            f"{state.total_rotations} ok  /  {state.total_failures} failed",
            colors.get("value", 0),
        ),
        ("Last event", last_event, colors.get("dim", 0)),
    ]

    y = 2
    for label, value, attr in rows:
        if y >= height - 6:
            break
        _safe_addstr(stdscr, y, 2, f"{label:<14}", colors.get("dim", 0))
        _safe_addstr(stdscr, y, 17, value, attr)
        y += 1

    if state.last_error and y < height - 6:
        _safe_addstr(stdscr, y, 2, "Last error:", colors.get("warn", 0))
        wrapped = textwrap.wrap(state.last_error, width=max(20, width - 18))[:2]
        for index, line in enumerate(wrapped):
            _safe_addstr(stdscr, y + index, 17, line, colors.get("warn", 0))
        y += len(wrapped)

    _safe_addstr(stdscr, height - 5, 0, "+" + "-" * max(0, width - 2) + "+")
    keys_one = "[s] start/stop  [r] rotate now  [v] check IP  [i] interval  [j] jitter"
    keys_two = "[g] logging  [t] verification  [l] logs  [d] doctor  [c] config  [?] help  [q] quit"
    _safe_addstr(stdscr, height - 4, 2, keys_one, colors.get("dim", 0))
    _safe_addstr(stdscr, height - 3, 2, keys_two, colors.get("dim", 0))
    message = dash.message if time.monotonic() <= dash.message_until else "Ready."
    _safe_addstr(stdscr, height - 2, 2, message, colors.get("value", 0))
    stdscr.refresh()


def _panel_main(stdscr: Any, config: Config, config_file: Path | None) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(1000)
    colors = _init_colors()
    dash = _Dashboard(config, config_file)
    quit_requested = False
    while not quit_requested:
        dash.reload_if_changed()
        dash.poll_jobs()
        _draw(stdscr, dash, colors)
        try:
            key = stdscr.getch()
        except curses.error:
            continue
        if key in (ord("q"), ord("Q")):
            quit_requested = True
        elif key in (ord("s"), ord("S")):
            if running_pid():
                try:
                    daemon.stop()
                    dash.set_message("Background rotation stopped.")
                except daemon.DaemonError as exc:
                    dash.set_message(f"Stop failed: {exc}", 10)
            else:
                try:
                    pid = daemon.start(
                        dash.config,
                        interval=dash.config.interval,
                        jitter=dash.config.jitter,
                        config_file=dash.config_file,
                    )
                    dash.set_message(f"Background rotation started (pid {pid}).")
                except daemon.DaemonError as exc:
                    dash.set_message(f"Start failed: {exc}", 12)
        elif key in (ord("r"), ord("R")):
            dash._start_job("Rotate now", dash.rotator.rotate)
        elif key in (ord("v"), ord("V")):
            dash._start_job("Verify IP", dash.rotator.fetch_ip)
        elif key in (ord("i"), ord("I")):
            raw = _prompt(stdscr, "New interval (e.g. 90, 5m, 1h): ", str(dash.config.interval))
            try:
                seconds = parse_duration(raw)
                new_config = Config.from_dict({**dash.config.to_dict(), "interval": seconds})
                was_running = bool(running_pid())
                dash.apply_config(new_config, config_file)
                if was_running:
                    daemon.restart(
                        new_config,
                        interval=new_config.interval,
                        jitter=new_config.jitter,
                        config_file=dash.config_file,
                    )
                    dash.set_message(f"Interval set to {format_duration(seconds)}; daemon restarted.")
                else:
                    dash.set_message(f"Interval set to {format_duration(seconds)}.")
            except (ConfigError, daemon.DaemonError) as exc:
                dash.set_message(f"Invalid interval: {exc}", 12)
        elif key in (ord("j"), ord("J")):
            raw = _prompt(stdscr, "Jitter percentage (0-100): ", str(dash.config.jitter))
            try:
                jitter = int(raw)
                if not 0 <= jitter <= 100:
                    raise ValueError("jitter must be 0-100")
                new_config = Config.from_dict({**dash.config.to_dict(), "jitter": jitter})
                was_running = bool(running_pid())
                dash.apply_config(new_config, config_file)
                if was_running:
                    daemon.restart(
                        new_config,
                        interval=new_config.interval,
                        jitter=new_config.jitter,
                        config_file=dash.config_file,
                    )
                dash.set_message(f"Jitter set to {jitter}%.")
            except (ValueError, ConfigError, daemon.DaemonError) as exc:
                dash.set_message(f"Invalid jitter: {exc}", 10)
        elif key in (ord("g"), ord("G")):
            new_value = not dash.config.log_enabled
            new_config = Config.from_dict({**dash.config.to_dict(), "log_enabled": new_value})
            was_running = bool(running_pid())
            dash.apply_config(new_config, config_file)
            if was_running:
                try:
                    daemon.restart(
                        new_config,
                        interval=new_config.interval,
                        jitter=new_config.jitter,
                        config_file=dash.config_file,
                    )
                except daemon.DaemonError as exc:
                    dash.set_message(f"Logging changed, but restart failed: {exc}", 12)
                    continue
            dash.set_message(f"Rotation logging {'enabled' if new_value else 'disabled'}.")
        elif key in (ord("t"), ord("T")):
            new_value = not dash.config.verify
            new_config = Config.from_dict({**dash.config.to_dict(), "verify": new_value})
            was_running = bool(running_pid())
            dash.apply_config(new_config, config_file)
            if was_running:
                try:
                    daemon.restart(
                        new_config,
                        interval=new_config.interval,
                        jitter=new_config.jitter,
                        config_file=dash.config_file,
                    )
                except daemon.DaemonError as exc:
                    dash.set_message(f"Verification changed, but restart failed: {exc}", 12)
                    continue
            dash.set_message(f"Exit-IP verification {'enabled' if new_value else 'disabled'}.")
        elif key in (ord("l"), ord("L")):
            records = read_log_records(limit=1000)
            lines = [pretty_record(record) for record in records] or ["No rotation log entries yet."]
            _text_view(stdscr, f"IP Changer log - {len(records)} entries", lines)
            stdscr.timeout(1000)
        elif key in (ord("d"), ord("D")):
            dash.set_message("Running diagnostics (this can take a few seconds)...", 30)
            _draw(stdscr, dash, colors)
            checks = run_checks(dash.config, config_file, check_ip=True)
            lines = format_checks(checks).splitlines()
            _text_view(stdscr, "IP Changer doctor", lines)
            stdscr.timeout(1000)
        elif key in (ord("c"), ord("C")):
            values = dash.config.to_dict()
            if values.get("password"):
                values["password"] = "***"
            lines = [
                f"Config file: {config_file or config_path()}",
                "",
                *json.dumps(values, indent=2).splitlines(),
            ]
            _text_view(stdscr, "Configuration", lines)
            stdscr.timeout(1000)
        elif key in (ord("?"), ord("h"), ord("H")):
            lines = [
                "IP Changer control panel",
                "",
                "s  Start or stop the background rotation daemon",
                "r  Send SIGNAL NEWNYM now and verify a fresh exit IP",
                "v  Fetch and display the current Tor exit IP",
                "i  Change the rotation interval (accepts 90, 5m, 1h30m)",
                "j  Change the jitter percentage (0-100)",
                "g  Turn rotation logging on or off",
                "t  Turn exit-IP verification on or off",
                "l  Browse the JSONL rotation log",
                "d  Run environment diagnostics",
                "c  Show the active configuration",
                "q  Leave the panel (the daemon keeps running)",
                "",
                "Traffic must use the Tor SOCKS port. The panel changes the",
                "Tor exit node; it does not change your ISP-assigned IP.",
            ]
            _text_view(stdscr, "Help", lines)
            stdscr.timeout(1000)


def run_panel(config: Config, config_file: Path | None = None) -> int:
    if not sys.stdout.isatty() or os.environ.get("TERM", "") in {"", "dumb"}:
        print(render_text_dashboard(config, config_file=config_file))
        print("\nNon-interactive terminal detected; use 'ipchanger status' or 'ipchanger run'.")
        return 0
    try:
        curses.wrapper(_panel_main, config, config_file)
    except KeyboardInterrupt:
        return 130
    except curses.error as exc:
        print(f"Could not start the control panel: {exc}", file=sys.stderr)
        print(render_text_dashboard(config, config_file=config_file))
        return 1
    return 0
