from __future__ import annotations

import json
import select
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ipchanger.config import Config
from ipchanger.torhttp import (
    IPCheckError,
    TorVerificationError,
    get_exit_ip,
    http_get,
    parse_exit_payload,
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/chunked":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in (b"203.0.113.", b"9\n"):
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        if self.path == "/not-tor":
            body = json.dumps({"IsTor": False, "IP": "203.0.113.10"}).encode()
        elif self.path == "/plain":
            body = b"198.51.100.7\n"
        else:
            body = json.dumps({"IsTor": True, "IP": "203.0.113.9"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    result = b""
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise OSError("closed")
        result += chunk
    return result


class FakeSocksProxy:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(10)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def _accept_loop(self) -> None:
        while not self.stop.is_set():
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(5)
            version, count = _recv_exact(client, 2)
            methods = _recv_exact(client, count)
            if version != 5 or 0 not in methods:
                return
            client.sendall(b"\x05\x00")
            version, command, _reserved, address_type = _recv_exact(client, 4)
            if version != 5 or command != 1:
                return
            if address_type == 1:
                host = socket.inet_ntoa(_recv_exact(client, 4))
            elif address_type == 3:
                length = _recv_exact(client, 1)[0]
                host = _recv_exact(client, length).decode()
            elif address_type == 4:
                host = socket.inet_ntop(socket.AF_INET6, _recv_exact(client, 16))
            else:
                return
            port = int.from_bytes(_recv_exact(client, 2), "big")
            upstream = socket.create_connection((host, port), timeout=5)
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            while True:
                readable, _, _ = select.select([client, upstream], [], [], 5)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(4096)
                    if not data:
                        return
                    destination = upstream if source is client else client
                    destination.sendall(data)
        except OSError:
            return
        finally:
            client.close()
            if upstream is not None:
                upstream.close()

    def close(self) -> None:
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=2)


class TorHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.http_port = cls.http.server_address[1]
        cls.http_thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.http_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.http.shutdown()
        cls.http.server_close()

    def setUp(self) -> None:
        self.proxy = FakeSocksProxy()

    def tearDown(self) -> None:
        self.proxy.close()

    def test_http_get_through_socks(self) -> None:
        response = http_get(
            f"http://127.0.0.1:{self.http_port}/json",
            "127.0.0.1",
            self.proxy.port,
            timeout=5,
        )
        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertTrue(payload["IsTor"])

    def test_redirect_and_chunked(self) -> None:
        response = http_get(
            f"http://127.0.0.1:{self.http_port}/redirect",
            "127.0.0.1",
            self.proxy.port,
            timeout=5,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(parse_exit_payload(response.text)[0], "203.0.113.9")
        chunked = http_get(
            f"http://127.0.0.1:{self.http_port}/chunked",
            "127.0.0.1",
            self.proxy.port,
            timeout=5,
        )
        self.assertEqual(chunked.text, "203.0.113.9\n")

    def test_parse_payload_formats(self) -> None:
        self.assertEqual(parse_exit_payload('{"IsTor": true, "IP": "203.0.113.9"}'), ("203.0.113.9", True))
        self.assertEqual(parse_exit_payload("198.51.100.7\n"), ("198.51.100.7", None))
        self.assertEqual(parse_exit_payload("1.2.3.4, 5.6.7.8"), ("1.2.3.4", None))
        with self.assertRaises(IPCheckError):
            parse_exit_payload("no address here")

    def test_get_exit_ip(self) -> None:
        config = Config(
            check_urls=[f"http://127.0.0.1:{self.http_port}/json"],
            socks_port=self.proxy.port,
        ).validate()
        result = get_exit_ip(config)
        self.assertEqual(result.ip, "203.0.113.9")
        self.assertTrue(result.is_tor)

    def test_get_exit_ip_rejects_non_tor(self) -> None:
        config = Config(
            check_urls=[f"http://127.0.0.1:{self.http_port}/not-tor"],
            socks_port=self.proxy.port,
        ).validate()
        with self.assertRaises(TorVerificationError):
            get_exit_ip(config)


if __name__ == "__main__":
    unittest.main()
