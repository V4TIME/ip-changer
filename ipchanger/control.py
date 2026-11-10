"""A tiny, dependency-free implementation of the Tor control protocol.

Only the commands IP Changer needs are implemented:

* ``PROTOCOLINFO 1`` -- discover authentication methods and the cookie file
* ``AUTHENTICATE ...`` -- cookie, password or null authentication
* ``SIGNAL NEWNYM`` -- ask Tor to build fresh circuits
* ``GETINFO version`` -- display the Tor version in ``doctor``

The protocol is line based. Replies use ``250-`` for continuation lines and
``250 `` for the final line. See ``docs/ARCHITECTURE.md`` for a walk-through.
"""

from __future__ import annotations

import binascii
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import Config


class TorControlError(RuntimeError):
    """A control-port command failed or the port could not be reached."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthenticationError(TorControlError):
    """Authentication with the Tor control port failed."""


@dataclass
class ControlReply:
    code: str
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code.startswith("2")

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class ProtocolInfo:
    methods: set[str] = field(default_factory=set)
    cookie_path: Path | None = None
    tor_version: str | None = None


def _unescape_quoted(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def _parse_protocol_info(lines: Iterable[str]) -> ProtocolInfo:
    info = ProtocolInfo()
    for line in lines:
        if line.startswith("AUTH "):
            methods = re.search(r"METHODS=([^\s]+)", line)
            if methods:
                info.methods = {item.strip().upper() for item in methods.group(1).split(",") if item.strip()}
            cookie = re.search(r'COOKIEFILE="((?:\\.|[^"])*)"', line)
            if not cookie:
                cookie = re.search(r"COOKIEFILE=([^\s]+)", line)
            if cookie:
                info.cookie_path = Path(_unescape_quoted(cookie.group(1)))
        elif line.startswith("VERSION "):
            version = re.search(r'Tor="((?:\\.|[^"])*)"', line)
            if version:
                info.tor_version = _unescape_quoted(version.group(1))
            else:
                info.tor_version = line[len("VERSION ") :].strip()
    return info


def _quote_password(password: str) -> str:
    escaped = password.replace("\\", r"\\").replace('"', r"\"")
    return f'"{escaped}"'


class TorController:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9051,
        timeout: float = 5.0,
        cookie_path: str | Path | None = None,
        password: str = "",
        auth_method: str = "auto",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.cookie_path = Path(cookie_path).expanduser() if cookie_path else None
        self.password = password
        self.auth_method = auth_method
        self.sock: socket.socket | None = None
        self._reader = None
        self.info = ProtocolInfo()

    @classmethod
    def from_config(cls, config: Config) -> "TorController":
        return cls(
            host=config.control_host,
            port=config.control_port,
            timeout=min(10.0, max(2.0, config.change_timeout)),
            cookie_path=config.cookie_path or None,
            password=config.password,
            auth_method=config.auth_method,
        )

    def __enter__(self) -> "TorController":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def connect(self) -> ProtocolInfo:
        if self.sock is not None:
            return self.info
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise TorControlError(
                f"cannot connect to the Tor control port at {self.host}:{self.port}: {exc}. "
                "Is Tor running and is ControlPort enabled?"
            ) from exc
        self.sock.settimeout(self.timeout)
        self._reader = self.sock.makefile("rb")
        try:
            reply = self.command("PROTOCOLINFO 1")
            self.info = _parse_protocol_info(reply.lines)
            self._authenticate()
        except Exception:
            self.close()
            raise
        return self.info

    def _readline(self) -> str:
        if self._reader is None:
            raise TorControlError("control connection is closed")
        raw = self._reader.readline()
        if not raw:
            raise TorControlError("Tor closed the control connection unexpectedly")
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def _read_reply(self) -> ControlReply:
        lines: list[str] = []
        while True:
            line = self._readline()
            if len(line) < 3:
                continue
            code, separator, payload = line[:3], line[3:4], line[4:]
            if code.startswith("6"):
                # Asynchronous event (650). Discard it and wait for our reply.
                if separator == "-":
                    while True:
                        more = self._readline()
                        if len(more) >= 4 and more[:3] == code and more[3:4] == " ":
                            break
                continue
            if separator == "+":
                lines.append(payload)
                while True:
                    data = self._readline()
                    if data == ".":
                        break
                    lines.append(data)
                return ControlReply(code, lines)
            lines.append(payload)
            if separator == " ":
                return ControlReply(code, lines)
            if separator == "-":
                continue
            raise TorControlError(f"malformed control reply: {line!r}")

    def command(self, command: str) -> ControlReply:
        if self.sock is None:
            raise TorControlError("not connected to the Tor control port")
        try:
            self.sock.sendall(command.encode("utf-8") + b"\r\n")
        except OSError as exc:
            raise TorControlError(f"failed to send control command: {exc}") from exc
        reply = self._read_reply()
        if not reply.ok:
            message = reply.text or f"Tor returned status {reply.code}"
            if reply.code == "514" or "authentication" in message.lower():
                raise AuthenticationError(message, reply.code)
            raise TorControlError(message, reply.code)
        return reply

    def _read_cookie(self) -> bytes:
        path = self.cookie_path or self.info.cookie_path
        if path is None:
            raise AuthenticationError(
                "Tor did not advertise a cookie file and no cookie_path is configured"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AuthenticationError(
                f"cannot read Tor cookie {path}: {exc}. "
                "Add your user to the Tor group and log in again, or set auth_method=password."
            ) from exc
        if not data or len(data) > 4096:
            raise AuthenticationError(f"Tor cookie {path} has an unexpected size")
        return data

    def _authenticate(self) -> None:
        methods = self.info.methods
        method = self.auth_method
        if method == "auto":
            if "COOKIE" in methods:
                method = "cookie"
            elif "NULL" in methods:
                method = "null"
            elif self.password:
                method = "password"
            elif "SAFECOOKIE" in methods:
                raise AuthenticationError(
                    "Tor only offers SAFECOOKIE authentication; enable CookieAuthentication "
                    "or configure a control password"
                )
            else:
                method = "cookie"

        if method == "cookie":
            cookie = binascii.hexlify(self._read_cookie()).decode("ascii")
            self.command(f"AUTHENTICATE {cookie}")
        elif method == "password":
            if not self.password:
                raise AuthenticationError("auth_method=password but no password is configured")
            self.command(f"AUTHENTICATE {_quote_password(self.password)}")
        elif method == "null":
            self.command("AUTHENTICATE")
        else:  # pragma: no cover - Config.validate prevents this
            raise AuthenticationError(f"unknown authentication method: {method}")

    def signal_newnym(self) -> ControlReply:
        return self.command("SIGNAL NEWNYM")

    def get_version(self) -> str | None:
        try:
            reply = self.command("GETINFO version")
        except TorControlError:
            return self.info.tor_version
        for line in reply.lines:
            if line.startswith("version="):
                return line.split("=", 1)[1]
        return self.info.tor_version

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self.sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        try:
            if self._reader is not None:
                self._reader.close()
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None
        self._reader = None
