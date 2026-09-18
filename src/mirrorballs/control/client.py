"""Reconnecting connection to the renderer. Reading happens on a thread; everything the UI needs is
delivered on the GLib main loop via ``GLib.idle_add`` so widgets are only touched from the main thread."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gi.repository import GLib

from ..common.ipc import LineDecoder, encode
from ..common.protocol import PROTO

log = logging.getLogger("mirrorballs.client")
RECONNECT_S = 0.5


class RendererClient:
    def __init__(
        self,
        path: Path,
        on_state: Callable[[dict[str, Any], float], None],
        on_connection: Callable[[bool], None],
        on_error: Callable[[str], None],
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.path = Path(path)
        self._on_state, self._on_connection, self._on_error = on_state, on_connection, on_error
        self._on_event = on_event
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._next_id = 1
        self.connected = False
        self._thread = threading.Thread(target=self._run, name="renderer-client", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._sock:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def send(self, cmd: str, **fields: Any) -> bool:
        """Fire-and-forget command. Errors come back through ``on_error``. False if not connected."""
        with self._lock:
            sock = self._sock
            if sock is None:
                return False
            rid = self._next_id
            self._next_id += 1
            try:
                sock.sendall(encode({"id": rid, "cmd": cmd, **fields}))
            except OSError:
                return False
        return True

    # --- thread --------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(self.path))
            except OSError:
                sock.close()
                self._stop.wait(RECONNECT_S)
                continue
            with self._lock:
                self._sock = sock
            try:
                sock.sendall(encode({"id": 0, "cmd": "hello", "proto": PROTO}))
                GLib.idle_add(self._deliver_connection, True)
                self._read_loop(sock)
            except OSError:
                pass
            finally:
                with self._lock:
                    self._sock = None
                sock.close()
                GLib.idle_add(self._deliver_connection, False)
            self._stop.wait(RECONNECT_S)

    def _read_loop(self, sock: socket.socket) -> None:
        dec = LineDecoder()
        while not self._stop.is_set():
            data = sock.recv(65536)
            if not data:
                return
            now = time.monotonic()
            for msg in dec.feed(data):
                if msg.get("event") == "state":
                    GLib.idle_add(self._deliver_state, msg, now)
                elif "event" in msg and self._on_event is not None:
                    GLib.idle_add(self._deliver_event, msg)
                elif msg.get("ok") is True and "state" in msg:
                    GLib.idle_add(self._deliver_state, {"event": "state", **msg["state"]}, now)
                elif msg.get("ok") is False:
                    GLib.idle_add(self._deliver_error, msg.get("error", "unknown error"))

    def _deliver_state(self, msg: dict[str, Any], t: float) -> bool:
        self._on_state(msg, t)
        return False

    def _deliver_event(self, msg: dict[str, Any]) -> bool:
        if self._on_event:
            self._on_event(msg)
        return False

    def _deliver_connection(self, up: bool) -> bool:
        self.connected = up
        self._on_connection(up)
        return False

    def _deliver_error(self, message: str) -> bool:
        self._on_error(message)
        return False
