from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from ipchanger.control import AuthenticationError, TorControlError, TorController


class FakeControlServer:
    """A tiny Tor control-port server for the commands we implement."""

    def __init__(
        self,
        methods: tuple[str, ...] = ("COOKIE",),
        cookie: bytes = b"0123456789abcdef0123456789abcdef",
        password: str | None = None,
    ) -> None:
        self.methods = methods
        self.cookie = cookie
        self.password = password
        self.newnym_count = 0
        self.tempdir = tempfile.TemporaryDirectory()
        self.cookie_path = Path(self.tempdir.name) / "control.authcookie"
        self.cookie_path.write_bytes(cookie)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _send(self, handle, text: str) -> None:
        handle.write(text.encode("utf-8"))
        handle.flush()

    def _serve(self) -> None:
        try:
            connection, _ = self.sock.accept()
        except OSError:
            return
        with connection:
            with connection.makefile("rwb") as handle:
                while True:
                    raw = handle.readline()
                    if not raw:
                        return
                    command = raw.decode("utf-8", errors="replace").strip()
                    if command.startswith("PROTOCOLINFO"):
                        methods = ",".join(self.methods)
                        self._send(
                            handle,
                            "250-PROTOCOLINFO 1\r\n"
                            f"250-AUTH METHODS={methods} COOKIEFILE=\"{self.cookie_path}\"\r\n"
                            "250-VERSION Tor=\"0.4.8.10\"\r\n"
                            "250 OK\r\n",
                        )
                    elif command.startswith("AUTHENTICATE"):
                        argument = command[len("AUTHENTICATE") :].strip()
                        authenticated = False
                        if "COOKIE" in self.methods and argument == self.cookie.hex():
                            authenticated = True
                        if "NULL" in self.methods and argument == "":
                            authenticated = True
                        if (
                            "HASHEDPASSWORD" in self.methods
                            and self.password is not None
                            and argument == f'"{self.password}"'
                        ):
                            authenticated = True
                        if authenticated:
                            self._send(handle, "250 OK\r\n")
                        else:
                            self._send(handle, "515 Authentication failed: wrong credentials\r\n")
                            return
                    elif command == "SIGNAL NEWNYM":
                        self.newnym_count += 1
                        self._send(handle, "250 OK\r\n")
                    elif command.startswith("GETINFO version"):
                        self._send(handle, "250-version=0.4.8.10\r\n250 OK\r\n")
                    elif command == "QUIT":
                        self._send(handle, "250 closing connection\r\n")
                        return
                    else:
                        self._send(handle, "510 Unrecognized command\r\n")
                        return

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def __enter__(self) -> "FakeControlServer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()


class ControlTests(unittest.TestCase):
    def test_cookie_authentication_and_newnym(self) -> None:
        with FakeControlServer() as server:
            controller = TorController("127.0.0.1", server.port, timeout=2)
            info = controller.connect()
            self.assertEqual(info.tor_version, "0.4.8.10")
            self.assertIn("COOKIE", info.methods)
            controller.signal_newnym()
            self.assertEqual(server.newnym_count, 1)
            self.assertEqual(controller.get_version(), "0.4.8.10")
            controller.close()

    def test_explicit_cookie_path(self) -> None:
        with FakeControlServer() as server:
            controller = TorController(
                "127.0.0.1",
                server.port,
                timeout=2,
                cookie_path=server.cookie_path,
                auth_method="cookie",
            )
            controller.connect()
            controller.signal_newnym()
            self.assertEqual(server.newnym_count, 1)
            controller.close()

    def test_password_authentication(self) -> None:
        with FakeControlServer(methods=("HASHEDPASSWORD",), password="s3cret") as server:
            controller = TorController(
                "127.0.0.1",
                server.port,
                timeout=2,
                password="s3cret",
                auth_method="password",
            )
            controller.connect()
            controller.close()

    def test_null_authentication(self) -> None:
        with FakeControlServer(methods=("NULL",)) as server:
            controller = TorController("127.0.0.1", server.port, timeout=2, auth_method="auto")
            controller.connect()
            controller.close()

    def test_bad_cookie_raises_authentication_error(self) -> None:
        with FakeControlServer() as server:
            controller = TorController(
                "127.0.0.1",
                server.port,
                timeout=2,
                cookie_path=Path(tempfile.gettempdir()) / "does-not-exist-cookie",
            )
            with self.assertRaises(AuthenticationError):
                controller.connect()
            controller.close()

    def test_wrong_password_raises_authentication_error(self) -> None:
        with FakeControlServer(methods=("HASHEDPASSWORD",), password="right") as server:
            controller = TorController(
                "127.0.0.1",
                server.port,
                timeout=2,
                password="wrong",
                auth_method="password",
            )
            with self.assertRaises(AuthenticationError):
                controller.connect()

    def test_connection_refused_is_wrapped(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        controller = TorController("127.0.0.1", port, timeout=1)
        with self.assertRaises(TorControlError):
            controller.connect()


if __name__ == "__main__":
    unittest.main()
