"""Small blocking client for the renderer socket (used by mirrorballs-ctl and tests)."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

from ..common.ipc import LineDecoder, encode


class NotRunning(ConnectionError):
    pass


class SyncClient:
    def __init__(self, path: Path, timeout: float = 3.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(path))
        except OSError as e:  # missing socket, refused, or an unusable path (e.g. longer than 107 bytes)
            self.sock.close()
            why = "no renderer is listening on" if isinstance(e, (FileNotFoundError, ConnectionRefusedError)) else f"cannot connect ({e}):"
            raise NotRunning(f"{why} {path}") from None
        self._dec = LineDecoder()
        self._pending: list[dict[str, Any]] = []
        self._next_id = 1

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> SyncClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read(self) -> dict[str, Any]:
        """Next message (reply or event). Raises socket.timeout if nothing arrives."""
        while not self._pending:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("renderer closed the connection")
            self._pending += self._dec.feed(data)
        return self._pending.pop(0)

    def request(self, cmd: str, **fields: Any) -> dict[str, Any]:
        """Send a command and return its reply, skipping unrelated state events."""
        rid = self._next_id
        self._next_id += 1
        self.sock.sendall(encode({"id": rid, "cmd": cmd, **fields}))
        deadline = time.monotonic() + (self.sock.gettimeout() or 3.0)
        while time.monotonic() < deadline:
            msg = self.read()
            if msg.get("id") == rid:
                return msg
        raise TimeoutError(f"no reply to {cmd}")
