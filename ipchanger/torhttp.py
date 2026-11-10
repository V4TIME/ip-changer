"""Minimal SOCKS5 + HTTP client, used to verify the Tor exit IP.

Python's standard library does not provide SOCKS support, so this module
implements the small part of RFC 1928 (SOCKS5) and RFC 1929 (username/password
authentication) that Tor exposes. HTTPS is handled with :mod:`ssl`.

No third-party package such as PySocks or requests is required, which keeps the
Termux installation small and auditable.
"""

from __future__ import annotations

import gzip
import ipaddress
import json
import re
import socket
import ssl
import struct
import zlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .config import Config

USER_AGENT = "ip-changer/1.0"


class ProxyError(RuntimeError):
    """The SOCKS proxy could not establish a connection."""


class IPCheckError(RuntimeError):
    """The public exit IP could not be obtained."""


class TorVerificationError(IPCheckError):
    """A check endpoint explicitly reported that the connection is not Tor."""


@dataclass
class ExitIP:
    ip: str
    is_tor: bool | None
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {"ip": self.ip, "is_tor": self.is_tor, "source": self.source}


@dataclass
class HttpResponse:
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes
    url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ProxyError("SOCKS proxy closed the connection during the handshake")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _encode_socks_address(host: str) -> bytes:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ProxyError(f"hostname is too long for SOCKS5: {host}")
        return b"\x03" + bytes([len(encoded)]) + encoded
    if address.version == 4:
        return b"\x01" + address.packed
    return b"\x04" + address.packed


def socks5_connect(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float = 15.0,
    username: str | None = None,
    password: str | None = None,
) -> socket.socket:
    try:
        sock = socket.create_connection((proxy_host, int(proxy_port)), timeout=timeout)
    except OSError as exc:
        raise ProxyError(f"cannot connect to SOCKS proxy {proxy_host}:{proxy_port}: {exc}") from exc
    sock.settimeout(timeout)
    try:
        methods = [0x00]
        if username is not None:
            methods.append(0x02)
        sock.sendall(b"\x05" + bytes([len(methods)]) + bytes(methods))
        version, method = _recv_exact(sock, 2)
        if version != 5:
            raise ProxyError(f"unexpected SOCKS version from proxy: {version}")
        if method == 0xFF:
            raise ProxyError("SOCKS proxy does not accept any offered authentication method")
        if method == 0x02:
            if username is None or password is None:
                raise ProxyError("SOCKS proxy requires username/password authentication")
            user = username.encode("utf-8")
            secret = password.encode("utf-8")
            if len(user) > 255 or len(secret) > 255:
                raise ProxyError("SOCKS username/password is too long")
            sock.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
            _auth_version, status = _recv_exact(sock, 2)
            if status != 0:
                raise ProxyError("SOCKS username/password authentication failed")
        elif method != 0x00:
            raise ProxyError(f"unsupported SOCKS authentication method: {method}")

        request = b"\x05\x01\x00" + _encode_socks_address(target_host) + struct.pack("!H", int(target_port))
        sock.sendall(request)
        _version, reply, _reserved, address_type = _recv_exact(sock, 4)
        if reply != 0x00:
            reasons = {
                0x01: "general SOCKS server failure",
                0x02: "connection not allowed by ruleset",
                0x03: "network unreachable",
                0x04: "host unreachable",
                0x05: "connection refused",
                0x06: "TTL expired",
                0x07: "command not supported",
                0x08: "address type not supported",
            }
            raise ProxyError(f"SOCKS CONNECT failed: {reasons.get(reply, f'error {reply}')}")
        if address_type == 0x01:
            _recv_exact(sock, 4)
        elif address_type == 0x03:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length)
        elif address_type == 0x04:
            _recv_exact(sock, 16)
        else:
            raise ProxyError(f"unknown SOCKS address type in reply: {address_type}")
        _recv_exact(sock, 2)  # bound port
        return sock
    except Exception:
        sock.close()
        raise


def _read_until(sock: socket.socket, marker: bytes, limit: int = 65536) -> tuple[bytes, bytes]:
    buffer = bytearray()
    while marker not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise IPCheckError("connection closed before HTTP headers were complete")
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise IPCheckError("HTTP response headers are too large")
    head, _, rest = bytes(buffer).partition(marker)
    return head, rest


def _read_chunked(sock: socket.socket, initial: bytes) -> bytes:
    data = bytearray(initial)
    body = bytearray()
    while True:
        while b"\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise IPCheckError("connection closed inside chunked HTTP body")
            data.extend(chunk)
        line, _, remainder = bytes(data).partition(b"\r\n")
        data = bytearray(remainder)
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise IPCheckError(f"invalid HTTP chunk size: {line!r}") from exc
        if size == 0:
            # Consume trailers up to the final blank line.
            while b"\r\n\r\n" not in data and not data.startswith(b"\r\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    return bytes(body)
                data.extend(chunk)
            return bytes(body)
        while len(data) < size + 2:
            chunk = sock.recv(4096)
            if not chunk:
                raise IPCheckError("connection closed inside chunked HTTP body")
            data.extend(chunk)
        body.extend(data[:size])
        data = data[size + 2 :]


def _read_response(sock: socket.socket, url: str) -> HttpResponse:
    head, rest = _read_until(sock, b"\r\n\r\n")
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    if not lines:
        raise IPCheckError("empty HTTP response")
    status_line = lines[0].split(" ", 2)
    if len(status_line) < 2 or not status_line[0].startswith("HTTP/"):
        raise IPCheckError(f"invalid HTTP status line: {lines[0]!r}")
    try:
        status = int(status_line[1])
    except ValueError as exc:
        raise IPCheckError(f"invalid HTTP status code: {status_line[1]!r}") from exc
    reason = status_line[2] if len(status_line) > 2 else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _read_chunked(sock, rest)
    elif "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError as exc:
            raise IPCheckError("invalid HTTP Content-Length") from exc
        body = bytearray(rest)
        while len(body) < length:
            chunk = sock.recv(length - len(body))
            if not chunk:
                break
            body.extend(chunk)
        body = bytes(body[:length])
    else:
        body = bytearray(rest)
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            body.extend(chunk)
        body = bytes(body)

    encoding = headers.get("content-encoding", "").lower()
    try:
        if encoding == "gzip":
            body = gzip.decompress(body)
        elif encoding == "deflate":
            body = zlib.decompress(body)
    except (OSError, zlib.error):
        pass
    return HttpResponse(status=status, reason=reason, headers=headers, body=body, url=url)


def http_get(
    url: str,
    proxy_host: str,
    proxy_port: int,
    timeout: float = 20.0,
    user_agent: str = USER_AGENT,
    max_redirects: int = 5,
    username: str | None = None,
    password: str | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> HttpResponse:
    """Fetch *url* through a SOCKS5 proxy, following redirects."""

    current = url
    for _ in range(max_redirects + 1):
        parsed = urlsplit(current)
        if parsed.scheme not in {"http", "https"}:
            raise IPCheckError(f"unsupported URL scheme: {parsed.scheme}")
        if not parsed.hostname:
            raise IPCheckError(f"URL has no host: {current}")
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        sock = socks5_connect(
            proxy_host,
            proxy_port,
            host,
            port,
            timeout=timeout,
            username=username,
            password=password,
        )
        try:
            if parsed.scheme == "https":
                context = ssl_context or ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=host)
            host_header = host if port in (80, 443) else f"{host}:{port}"
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                f"User-Agent: {user_agent}\r\n"
                "Accept: application/json, text/plain, */*\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("iso-8859-1"))
            response = _read_response(sock, current)
        finally:
            try:
                sock.close()
            except OSError:
                pass
        if response.status in {301, 302, 303, 307, 308} and "location" in response.headers:
            current = urljoin(current, response.headers["location"])
            continue
        if response.status < 200 or response.status >= 300:
            raise IPCheckError(f"{current} returned HTTP {response.status} {response.reason}".strip())
        return response
    raise IPCheckError(f"too many HTTP redirects fetching {url}")


_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def _extract_ip(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    candidate = value.split(",")[0].strip()
    for item in (candidate, value):
        try:
            return str(ipaddress.ip_address(item))
        except ValueError:
            pass
    match = _IPV4_RE.search(value)
    return match.group(0) if match else None


def parse_exit_payload(text: str) -> tuple[str, bool | None]:
    """Return ``(ip, is_tor)`` from the common IP-check response formats."""

    stripped = text.strip()
    is_tor: bool | None = None
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("IsTor", "is_tor", "isTor"):
                if key in payload:
                    is_tor = bool(payload[key])
                    break
            for key in ("IP", "ip", "origin", "query", "address", "ip_addr"):
                if key in payload and isinstance(payload[key], str):
                    found = _extract_ip(payload[key])
                    if found:
                        return found, is_tor
    found = _extract_ip(stripped)
    if found:
        return found, is_tor
    raise IPCheckError(f"response did not contain an IP address: {stripped[:160]!r}")


def get_exit_ip(config: Config) -> ExitIP:
    """Ask the configured check services which exit IP Tor is currently using."""

    errors: list[str] = []
    for url in config.check_urls:
        try:
            response = http_get(
                url,
                config.socks_host,
                config.socks_port,
                timeout=min(30.0, max(5.0, float(config.change_timeout))),
                user_agent=config.user_agent,
            )
            ip, is_tor = parse_exit_payload(response.text)
            if is_tor is False:
                raise TorVerificationError(
                    f"{url} says this connection is NOT using Tor; refusing to report success"
                )
            return ExitIP(ip=ip, is_tor=is_tor, source=url)
        except TorVerificationError:
            raise
        except (IPCheckError, ProxyError, OSError, ssl.SSLError) as exc:
            errors.append(f"{url}: {exc}")
    raise IPCheckError("could not determine the Tor exit IP: " + " | ".join(errors))


def check_socks_port(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False
