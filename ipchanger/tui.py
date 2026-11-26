"""The interactive terminal control panel (works in Termux and a normal TTY).

Visual rules live in :mod:`ipchanger.theme`; this module only turns state into
layout. The panel is borderless on purpose: a filled status bar, hairline rules
and a strict column grid carry the structure, which stays readable on a phone
and avoids the "ASCII box drawing from 1995" look.
"""

from __future__ import annotations

import curses
import json
import locale
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
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
    read_log_records,
    running_pid,
    tail_log_records,
)
from .theme import COLOR_8, COLOR_256, GLYPHS, LAYOUT, MESSAGE_SECONDS, ROLES
from .torhttp import ExitIP


# --- Pure helpers (no curses, unit-testable) ---------------------------------


def _unicode_ok() -> bool:
    encoding = (locale.getpreferredencoding(False) or "").lower()
    if "utf" not in encoding:
        return False
    try:
        "●─→".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _glyphs(unicode_ok: bool | None = None) -> dict[str, str]:
    if unicode_ok is None:
        unicode_ok = _unicode_ok()
    return GLYPHS["unicode" if unicode_ok else "ascii"]


def _fit(text: str, width: int, glyphs: dict[str, str] | None = None) -> str:
    """Truncate to *width* columns without splitting the ellipsis in half."""

    if width <= 0:
        return ""
    text = str(text).replace("\t", "    ").replace("\r", " ").replace("\n", " ")
    if len(text) <= width:
        return text
    if width == 1:
        return text[:1]
    ellipsis = (glyphs or _glyphs()).get("ellipsis", "...")
    return text[: max(0, width - len(ellipsis))] + ellipsis


def _clock(timestamp: float | None) -> str:
    if not timestamp:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def _stamp(timestamp: float | None) -> str:
    """Short timestamp: time only for today, date + time otherwise."""

    if not timestamp:
        return "never"
    local = time.localtime(timestamp)
    if time.strftime("%Y-%m-%d", local) == time.strftime("%Y-%m-%d"):
        return time.strftime("%H:%M:%S", local)
    return time.strftime("%b %d %H:%M", local)


def _elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class ConnectionView:
    """What a VPN client would call the connection state."""

    kind: str
    label: str
    glyph: str
    role: str
    detail: str


def describe_connection(pid: int | None, state: State, now: float | None = None) -> ConnectionView:
    """Derive one honest headline from daemon + verification state.

    The old panel printed three separate jargon rows ("Service", "Tor exit IP",
    "Tor check") and left the user to combine them. A client needs one answer:
    am I connected, and is the traffic actually going through the tunnel?
    """

    if not pid:
        return ConnectionView(
            kind="disconnected",
            label="DISCONNECTED",
            glyph="off",
            role="muted",
            detail="Rotation service is not running.",
        )
    if not state.last_ip:
        if state.last_error:
            return ConnectionView(
                kind="error",
                label="CONNECTION FAILED",
                glyph="bad",
                role="error",
                detail=state.last_error,
            )
        return ConnectionView(
            kind="connecting",
            label="CONNECTING",
            glyph="pending",
            role="warn",
            detail="Waiting for the first Tor exit…",
        )
    if state.last_is_tor is False:
        return ConnectionView(
            kind="leak",
            label="NOT USING TOR",
            glyph="bad",
            role="error",
            detail="The exit check did not confirm Tor. Traffic may be going out directly.",
        )
    if state.last_is_tor is None:
        return ConnectionView(
            kind="unverified",
            label="CONNECTED · UNVERIFIED",
            glyph="on",
            role="warn",
            detail="The exit IP changed, but Tor could not be confirmed.",
        )
    return ConnectionView(
        kind="connected",
        label="CONNECTED",
        glyph="on",
        role="ok",
        detail="Tor exit confirmed.",
    )


def format_event(
    record: dict[str, Any],
    glyphs: dict[str, str] | None = None,
) -> tuple[str, str, str, str]:
    """Turn one JSONL record into ``(clock, kind, message, role)``.

    Used by both the live pane and the plain-text fallback so the two never
    drift apart.
    """

    glyphs = glyphs or _glyphs()
    dot, arrow, check = glyphs["dot"], glyphs["arrow"], glyphs["check"]
    timestamp = str(record.get("ts") or "")
    clock = timestamp[11:19] if len(timestamp) >= 19 else timestamp
    event = str(record.get("event") or "event")
    role = "text"

    if event == "rotation":
        kind = "rotate"
        old_ip = record.get("old_ip")
        new_ip = record.get("new_ip")
        if old_ip or new_ip:
            message = f"{old_ip or '?'} {arrow} {new_ip or '?'}"
        else:
            message = "requested a new circuit"
        extras: list[str] = []
        if record.get("is_tor") is True:
            extras.append(f"tor {check}")
        elif record.get("is_tor") is False:
            extras.append("not tor")
            role = "warn"
        if record.get("duration_ms") is not None:
            extras.append(f"{record['duration_ms']} ms")
        note = record.get("error") or record.get("note")
        if note:
            extras.append(str(note))
        if extras:
            message += "  " + f"  {dot}  ".join(extras)
        if record.get("ok") is False:
            role = "error"
    elif event == "daemon_start":
        kind = "session"
        interval = format_duration(record.get("interval") or 0)
        message = f"rotation started {dot} every {interval}"
        if record.get("jitter"):
            message += f" {dot} jitter {record['jitter']}%"
    elif event == "daemon_stop":
        kind = "session"
        message = "rotation stopped"
        role = "muted"
    else:
        kind = event[:7]
        fields = [
            f"{key}={value}"
            for key, value in record.items()
            if key not in {"ts", "event"} and not isinstance(value, (dict, list))
        ]
        message = " ".join(fields) or event
        if record.get("ok") is False or record.get("error"):
            role = "error"
    return clock, kind, message, role


def _verification_text(view: ConnectionView, state: State) -> str:
    if state.last_is_tor is True:
        return "Tor exit confirmed"
    if state.last_is_tor is False:
        return "failed"
    if view.kind == "connecting":
        return "checking…"
    if state.last_ip:
        return "not confirmed"
    return "not checked"


def _rotation_text(config: Config, state: State, running: bool, now: float) -> str:
    if not running:
        return "paused"
    if state.next_rotation:
        return f"in {format_duration(max(0, state.next_rotation - now))}"
    return "starting…"


def _session_text(state: State, running: bool, now: float) -> str:
    if not running or not state.started_at:
        return "-"
    return _elapsed(now - state.started_at)


def dashboard_rows(
    config: Config,
    state: State,
    view: ConnectionView,
    running: bool,
    now: float,
    glyphs: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Ordered key/value rows; the layout fills 4 per column."""

    glyphs = glyphs or _glyphs()
    dot = glyphs["dot"]
    socks = f"{config.socks_host}:{config.socks_port}"
    last_change = (
        f"{_stamp(state.last_success)} {dot} {human_age(state.last_success)}"
        if state.last_success
        else "never"
    )
    return [
        ("Exit IP", state.last_ip or "not assigned"),
        ("Verification", _verification_text(view, state)),
        ("Rotates", _rotation_text(config, state, running, now)),
        ("Session", _session_text(state, running, now)),
        ("Interval", f"{format_duration(config.interval)} {dot} jitter {config.jitter}%"),
        ("Last change", last_change),
        ("Rotations", f"{state.total_rotations} ok {dot} {state.total_failures} failed"),
        ("SOCKS5", f"{socks} {dot} {config.rotation_mode}"),
    ]


def render_text_dashboard(
    config: Config,
    state: State | None = None,
    config_file: Path | None = None,
) -> str:
    """A plain-text fallback for non-interactive terminals and documentation."""

    state = state or StateStore().load()
    pid = running_pid()
    now = time.time()
    glyphs = _glyphs()
    rule = glyphs["rule"]
    view = describe_connection(pid, state, now)
    rows = dashboard_rows(config, state, view, bool(pid), now, glyphs)
    records = tail_log_records(6)

    width = 62
    lines = [
        f"IP Changer {VERSION}",
        rule * width,
        f"{glyphs[view.glyph]} {view.label}",
        f"  {view.detail}",
        "",
    ]
    label_width = max(len(label) for label, _ in rows) + 2
    for label, value in rows:
        lines.append(f"{label:<{label_width}}{value}")
    lines.append(rule * width)
    lines.append("Recent activity")
    if records:
        for record in records:
            clock, kind, message, _ = format_event(record, glyphs)
            lines.append(f"{clock}  {kind:<7}{_fit(message, 44, glyphs)}")
    else:
        lines.append("No activity yet.")
    lines.append(rule * width)
    lines.append(f"Config        {config_file or config_path()}")
    if state.last_error and view.kind not in {"error", "leak"}:
        lines.append(f"Warning       {state.last_error}")
    lines.append("Run 'ipchanger panel' in an interactive terminal for the live view.")
    return "\n".join(lines)


# --- Curses rendering --------------------------------------------------------


@dataclass
class Visual:
    unicode: bool
    attrs: dict[str, int]

    def glyph(self, name: str) -> str:
        table = GLYPHS["unicode" if self.unicode else "ascii"]
        return table.get(name, "?")

    def attr(self, role: str) -> int:
        return self.attrs.get(role, 0)

    @property
    def bold(self) -> int:
        return getattr(curses, "A_BOLD", 0)


def _build_visual() -> Visual:
    unicode_ok = _unicode_ok()
    attrs: dict[str, int] = {}
    try:
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            palette = COLOR_256 if getattr(curses, "COLORS", 8) >= 256 else COLOR_8
            for index, role in enumerate(ROLES, start=1):
                curses.init_pair(index, palette[role], background)
                attrs[role] = curses.color_pair(index)
    except curses.error:
        attrs = {}
    if not attrs:
        # Monochrome: structure comes from glyphs and weight, never dim text.
        attrs = {role: 0 for role in ROLES}
        attrs["accent"] = curses.A_BOLD
    return Visual(unicode_ok, attrs)


def _put(win: Any, y: int, x: int, text: str, attr: int = 0, width: int | None = None) -> None:
    """Write text, clipped to the window, swallowing curses geometry errors."""

    if not text:
        return
    try:
        height, cols = win.getmaxyx()
    except curses.error:
        return
    if y < 0 or y >= height or x < 0 or x >= cols:
        return
    limit = cols - x - 1 if width is None else min(width, cols - x - 1)
    if limit <= 0:
        return
    try:
        win.addstr(y, x, str(text)[:limit], attr)
    except (curses.error, UnicodeEncodeError):
        pass


def _fill_rule(win: Any, y: int, x: int, width: int, visual: Visual) -> None:
    if width > 0:
        _put(win, y, x, visual.glyph("rule") * width, visual.attr("rule"))


def _shield(
    win: Any,
    y: int,
    x: int,
    view: ConnectionView,
    visual: Visual,
) -> int:
    """Solid, reverse-video status bar: the compact form of a "connection shield"."""

    label = f" {visual.glyph(view.glyph)} {view.label} "
    attr = visual.attr(view.role) | visual.bold | curses.A_REVERSE
    _put(win, y, x, label, attr)
    return x + len(label)


class _Dashboard:
    def __init__(self, config: Config, config_file: Path | None = None) -> None:
        self.config = config
        self.config_file = config_file
        self.state_store = StateStore()
        self.rotator = Rotator(config, state_store=self.state_store, log_writer=LogWriter(config))
        self.jobs: queue.Queue[tuple[str, Any, Exception | None]] = queue.Queue()
        self.job_thread: threading.Thread | None = None
        self.message = "Ready."
        self.message_role = "muted"
        self.message_until = 0.0
        self._config_mtime = self._mtime(config_file)

    @staticmethod
    def _mtime(path: Path | None) -> int | None:
        if path is None:
            return None
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def set_message(self, text: str, seconds: float = MESSAGE_SECONDS, role: str = "text") -> None:
        self.message = text
        self.message_role = role
        self.message_until = time.monotonic() + seconds

    def active_message(self) -> tuple[str, str] | None:
        if time.monotonic() <= self.message_until:
            return self.message, self.message_role
        return None

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
                self.set_message(f"Config reload failed: {exc}", 10, "error")

    def _start_job(self, name: str, function: Callable[[], Any]) -> None:
        if self.job_thread is not None and self.job_thread.is_alive():
            self.set_message("Another operation is still running; please wait.", role="warn")
            return

        def worker() -> None:
            try:
                self.jobs.put((name, function(), None))
            except Exception as exc:  # noqa: BLE001 - surfaced in the message line
                self.jobs.put((name, None, exc))

        self.job_thread = threading.Thread(target=worker, name=f"ipchanger-{name}", daemon=True)
        self.job_thread.start()
        self.set_message(f"{name}…", 30, "muted")

    def poll_jobs(self) -> None:
        while True:
            try:
                name, result, error = self.jobs.get_nowait()
            except queue.Empty:
                break
            if error is not None:
                self.set_message(f"{name} failed: {error}", 10, "error")
            elif isinstance(result, RotationResult):
                if result.ok:
                    changed = "new IP" if result.changed else "IP unchanged"
                    role = "warn" if result.error else "ok"
                    self.set_message(
                        f"Rotated ({changed}): {result.old_ip or '?'} → {result.new_ip or '?'}"
                        + (f" · {result.error}" if result.error else ""),
                        10,
                        role,
                    )
                else:
                    self.set_message(f"Rotation failed: {result.error}", 12, "error")
            elif isinstance(result, ExitIP):
                self.set_message(
                    f"Exit IP {result.ip} · Tor {result.is_tor}",
                    8,
                    "ok" if result.is_tor else "warn",
                )
            else:
                self.set_message(f"{name} finished.", 5, "ok")


def _prompt(stdscr: Any, visual: Visual, label: str, default: str) -> str:
    """Inline prompt on the message row; keeps the rest of the frame intact."""

    height, width = stdscr.getmaxyx()
    row = max(0, height - 2)
    _put(stdscr, row, 0, " " * max(0, width - 1))
    prefix = f"{label} [{default}] {visual.glyph('prompt')} "
    _put(stdscr, row, LAYOUT["margin"], prefix, visual.attr("accent") | visual.bold)
    stdscr.timeout(-1)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        curses.echo()
        start = min(LAYOUT["margin"] + len(prefix), max(0, width - 2))
        raw = stdscr.getstr(row, start, max(10, min(60, width - start - 1)))
        value = raw.decode("utf-8", errors="replace").strip()
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.timeout(200)
    return value or default


def _pager(stdscr: Any, visual: Visual, title: str, lines: list[str]) -> None:
    offset = 0
    stdscr.timeout(-1)
    try:
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            margin = LAYOUT["margin"]
            content = max(10, width - margin * 2)
            _put(stdscr, 0, margin, title, visual.attr("text") | visual.bold)
            _fill_rule(stdscr, 1, margin, content, visual)
            body_top = 2
            body_bottom = max(body_top, height - 3)
            body_height = body_bottom - body_top
            visible = lines[offset : offset + body_height]
            for index, line in enumerate(visible):
                _put(stdscr, body_top + index, margin, line, visual.attr("text"), content)
            first = offset + 1 if lines else 0
            last = min(len(lines), offset + body_height)
            footer = f"{visual.glyph('dot')} {first}-{last} of {len(lines)}   j/k scroll   g/G ends   q back"
            _put(
                stdscr,
                height - 2,
                margin,
                _fit(footer, content, _glyphs(visual.unicode)),
                visual.attr("muted"),
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
            elif key == ord("g"):
                offset = 0
            elif key == ord("G"):
                offset = max(0, len(lines) - body_height)
            elif key == curses.KEY_RESIZE:
                offset = min(offset, max(0, len(lines) - 1))
    finally:
        stdscr.timeout(200)


def _hint_pairs(running: bool) -> list[tuple[str, str]]:
    if not running:
        return [("s", "connect"), ("i", "interval"), ("d", "doctor"), ("?", "help"), ("q", "quit")]
    return [
        ("s", "disconnect"),
        ("r", "rotate now"),
        ("v", "verify"),
        ("l", "log"),
        ("i", "interval"),
        ("d", "doctor"),
        ("?", "help"),
        ("q", "quit"),
    ]


def _draw_hints(win: Any, y: int, x: int, width: int, pairs: list[tuple[str, str]], visual: Visual) -> None:
    cursor = x
    limit = x + width
    for index, (key, description) in enumerate(pairs):
        token = f"[{key}]"
        chunk = len(token) + 1 + len(description)
        if cursor + chunk > limit:
            break
        if index:
            cursor += 2
        _put(win, y, cursor, token, visual.attr("accent") | visual.bold)
        cursor += len(token) + 1
        _put(win, y, cursor, description, visual.attr("muted"))
        cursor += len(description)


def _draw_compact(stdscr: Any, dash: _Dashboard, visual: Visual) -> None:
    height, width = stdscr.getmaxyx()
    margin = 2
    state = dash.state_store.load()
    pid = running_pid()
    now = time.time()
    view = describe_connection(pid, state, now)
    content = max(8, width - margin - 1)
    _put(stdscr, 0, margin, _fit("IP Changer", content), visual.attr("text") | visual.bold)
    _put(stdscr, 0, margin + max(0, content - len(VERSION) - 1), f"v{VERSION}", visual.attr("muted"))
    _fill_rule(stdscr, 1, margin, content, visual)
    if height > 2:
        _put(stdscr, 2, margin, visual.glyph(view.glyph), visual.attr(view.role) | visual.bold)
        _put(stdscr, 2, margin + 2, _fit(view.label, content - 2), visual.attr(view.role) | visual.bold)
    if height > 5:
        _put(stdscr, 3, margin, _fit(state.last_ip or "no exit IP yet", content), visual.attr("text"))
    if height > 6:
        next_text = _rotation_text(dash.config, state, bool(pid), now)
        _put(stdscr, 4, margin, _fit(f"next {next_text}", content), visual.attr("muted"))
    if height > 7:
        hints = f"[s] {'stop' if pid else 'start'}   [q] quit"
        _put(stdscr, height - 2, margin, _fit(hints, content), visual.attr("muted"))
    message = dash.active_message()
    if message and height > 8:
        text, role = message
        _put(stdscr, height - 1, margin, _fit(text, content), visual.attr(role))


def _draw(stdscr: Any, dash: _Dashboard, visual: Visual) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    if width < LAYOUT["min_width"] or height < LAYOUT["min_height"]:
        _draw_compact(stdscr, dash, visual)
        stdscr.refresh()
        return

    now = time.time()
    state = dash.state_store.load()
    pid = running_pid()
    running = bool(pid)
    view = describe_connection(pid, state, now)
    glyphs = _glyphs(visual.unicode)
    margin = LAYOUT["margin"]
    content = min(max(20, width - margin - 1), LAYOUT["max_width"])
    right_edge = margin + content

    # Header: product name left, build right. No version banner, no ASCII art.
    _put(stdscr, 0, margin, "IP Changer", visual.attr("text") | visual.bold)
    version = f"v{VERSION}"
    _put(stdscr, 0, right_edge - len(version), version, visual.attr("muted"))
    _fill_rule(stdscr, 1, margin, content, visual)

    # Connection shield: one solid bar, then a plain-language detail.
    cursor = _shield(stdscr, 3, margin, view, visual)

    uptime = _elapsed(now - state.started_at) if running and state.started_at else ""
    meta = f"up {uptime}" if uptime else ""
    if meta:
        _put(stdscr, 3, right_edge - len(meta), meta, visual.attr("muted"))

    detail_x = cursor + 2
    detail_width = (right_edge - len(meta) - 2) - detail_x if meta else right_edge - detail_x
    _put(stdscr, 3, detail_x, _fit(view.detail, detail_width, glyphs), visual.attr("muted"))

    # A warning is only worth a line when it is not already the headline.
    if state.last_error and view.kind not in {"error", "leak"} and height > 20:
        warning = f"{glyphs['dot']} {state.last_error}"
        _put(stdscr, 4, margin, _fit(warning, content, glyphs), visual.attr("warn"))

    # Two dense columns at desktop widths, one at phone widths.
    rows = dashboard_rows(dash.config, state, view, running, now, glyphs)
    two_column = content >= LAYOUT["two_column_min"]
    label_width = LAYOUT["label_width"]
    top = 5
    if two_column:
        half = content // 2
        for column_index, column in enumerate((rows[:4], rows[4:8])):
            x = margin + column_index * half
            value_x = x + label_width
            value_width = half - label_width - 2
            for row_index, (label, value) in enumerate(column):
                y = top + row_index
                _put(stdscr, y, x, f"{label:<{label_width}}", visual.attr("muted"))
                _put(stdscr, y, value_x, _fit(value, value_width, glyphs), visual.attr("text"))
    else:
        for row_index, (label, value) in enumerate(rows[:4]):
            y = top + row_index
            _put(stdscr, y, margin, f"{label:<{label_width}}", visual.attr("muted"))
            _put(stdscr, y, margin + label_width, _fit(value, content - label_width, glyphs), visual.attr("text"))

    # Activity section: label + hairline, then a bottom-anchored tail.
    section_y = top + 4
    section = "Recent activity"
    _put(stdscr, section_y, margin, section, visual.attr("muted") | visual.bold)
    _fill_rule(stdscr, section_y, margin + len(section) + 2, content - len(section) - 2, visual)

    body_top = section_y + 1
    body_bottom = height - 5
    body_rows = body_bottom - body_top + 1
    if body_rows >= 1:
        records = tail_log_records(body_rows)
        if not records:
            _put(stdscr, body_top, margin, f"{glyphs['dot']} No activity yet.", visual.attr("muted"))
        else:
            start = body_bottom - len(records) + 1
            for index, record in enumerate(records):
                clock, kind, message, role = format_event(record, glyphs)
                y = start + index
                _put(stdscr, y, margin, clock, visual.attr("muted"))
                _put(stdscr, y, margin + 9, f"{kind:<7}", visual.attr("muted"))
                _put(
                    stdscr,
                    y,
                    margin + 17,
                    _fit(message, content - 17, glyphs),
                    visual.attr(role),
                )

    # Footer: rule, shortcuts, one status line.
    _fill_rule(stdscr, height - 4, margin, content, visual)
    pairs = _hint_pairs(running)
    if content < 70:
        pairs = pairs[:2] + [("q", "quit")]
    _draw_hints(stdscr, height - 3, margin, content, pairs, visual)

    message = dash.active_message()
    if message:
        text, role = message
    elif running:
        socks = f"{dash.config.socks_host}:{dash.config.socks_port}"
        if dash.config.rotation_mode == "socks":
            text = (
                f"SOCKS mode: each rotation uses a fresh identity on {socks}; "
                "apps still need to use that proxy themselves."
            )
        else:
            text = f"Apps only get a new exit IP when they use the SOCKS5 proxy at {socks}."
        role = "muted"
        if not dash.config.log_enabled:
            text += " Activity logging is off."
    else:
        text = "Rotation service stopped. Press s to start it; q leaves the panel."
        role = "muted"
    _put(stdscr, height - 2, margin, _fit(text, content, glyphs), visual.attr(role))
    stdscr.refresh()


def _panel_main(stdscr: Any, config: Config, config_file: Path | None) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(200)
    visual = _build_visual()
    dash = _Dashboard(config, config_file)
    quit_requested = False
    last_signature: tuple[Any, ...] | None = None

    while not quit_requested:
        dash.reload_if_changed()
        dash.poll_jobs()

        state = dash.state_store.load()
        pid = running_pid()
        message = dash.active_message()
        signature = (
            int(time.time()),
            pid,
            state.last_ip,
            state.last_is_tor,
            state.last_success,
            state.total_rotations,
            state.total_failures,
            message,
            dash.config.interval,
            dash.config.jitter,
            dash.config.verify,
            dash.config.log_enabled,
            stdscr.getmaxyx(),
        )
        if signature != last_signature:
            _draw(stdscr, dash, visual)
            last_signature = signature

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
                    dash.set_message("Rotation stopped.", role="ok")
                except daemon.DaemonError as exc:
                    dash.set_message(f"Stop failed: {exc}", 10, "error")
            else:
                try:
                    pid = daemon.start(
                        dash.config,
                        interval=dash.config.interval,
                        jitter=dash.config.jitter,
                        config_file=dash.config_file,
                    )
                    dash.set_message(f"Rotation started (pid {pid}).", 8, "ok")
                except daemon.DaemonError as exc:
                    dash.set_message(f"Start failed: {exc}", 12, "error")
        elif key in (ord("r"), ord("R")):
            dash._start_job("Rotate now", dash.rotator.rotate)
        elif key in (ord("v"), ord("V")):
            dash._start_job("Verify IP", dash.rotator.fetch_ip)
        elif key in (ord("i"), ord("I")):
            raw = _prompt(stdscr, visual, "Interval (90, 5m, 1h)", str(dash.config.interval))
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
                    dash.set_message(f"Interval set to {format_duration(seconds)}; restarted.", 8, "ok")
                else:
                    dash.set_message(f"Interval set to {format_duration(seconds)}.", 8, "ok")
            except (ConfigError, daemon.DaemonError) as exc:
                dash.set_message(f"Invalid interval: {exc}", 10, "error")
        elif key in (ord("j"), ord("J")):
            raw = _prompt(stdscr, visual, "Jitter percent (0-100)", str(dash.config.jitter))
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
                dash.set_message(f"Jitter set to {jitter}%.", 8, "ok")
            except (ValueError, ConfigError, daemon.DaemonError) as exc:
                dash.set_message(f"Invalid jitter: {exc}", 10, "error")
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
                    dash.set_message(f"Logging changed, but restart failed: {exc}", 12, "error")
                    continue
            dash.set_message(f"Activity logging {'on' if new_value else 'off'}.", 8, "ok")
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
                    dash.set_message(f"Verification changed, but restart failed: {exc}", 12, "error")
                    continue
            dash.set_message(f"Exit verification {'on' if new_value else 'off'}.", 8, "ok")
        elif key in (ord("l"), ord("L")):
            records = read_log_records(limit=1000)
            glyphs = _glyphs(visual.unicode)
            rendered = [
                f"{clock}  {kind:<7}{message}"
                for clock, kind, message, _ in (format_event(record, glyphs) for record in records)
            ] or ["No activity yet."]
            _pager(stdscr, visual, f"Activity log · {len(records)} entries", rendered)
            stdscr.timeout(200)
        elif key in (ord("d"), ord("D")):
            dash.set_message("Running diagnostics (this can take a few seconds)…", 30, "muted")
            _draw(stdscr, dash, visual)
            checks = run_checks(dash.config, config_file, check_ip=True)
            _pager(stdscr, visual, "Diagnostics", format_checks(checks).splitlines())
            stdscr.timeout(200)
        elif key in (ord("c"), ord("C")):
            values = dash.config.to_dict()
            if values.get("password"):
                values["password"] = "***"
            lines = [
                f"Config file: {config_file or config_path()}",
                "",
                *json.dumps(values, indent=2).splitlines(),
            ]
            _pager(stdscr, visual, "Configuration", lines)
            stdscr.timeout(200)
        elif key in (ord("?"), ord("h"), ord("H")):
            lines = [
                "IP Changer control panel",
                "",
                "Connection",
                "  s   Start or stop the rotation daemon",
                "  r   Send SIGNAL NEWNYM now and verify a fresh exit IP",
                "  v   Fetch and display the current Tor exit IP",
                "",
                "Settings",
                "  i   Rotation interval (accepts 90, 5m, 1h30m)",
                "  j   Jitter percentage (0-100)",
                "  g   Activity logging on or off",
                "  t   Exit-IP verification on or off",
                "",
                "Inspect",
                "  l   Browse the activity log",
                "  d   Run environment diagnostics",
                "  c   Show the active configuration",
                "",
                "Scope",
                "  The panel rotates the Tor exit node. It does not change your",
                "  ISP-assigned address, and it is not a full-device VPN: an app",
                "  only gets a new exit IP when it sends traffic through the Tor",
                "  SOCKS proxy (127.0.0.1:9050).",
                "",
                "  q   Leave the panel (the daemon keeps running)",
            ]
            _pager(stdscr, visual, "Help", lines)
            stdscr.timeout(200)


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
