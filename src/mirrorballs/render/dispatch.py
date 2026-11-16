"""Drains the inbox (IPC clients, MIDI, mirrorballs-listen) into Core. GL-free, so it is testable with a real socket."""

from __future__ import annotations

from queue import Empty, SimpleQueue
from typing import Any, Protocol

import logging

from ..common import protocol as P
from .core import Core

log = logging.getLogger("mirrorballs.commands")
QUIET = {"hello", "get_state", "calibration.preview", "param.set", "master.set"}  # too chatty to log each one


class Sender(Protocol):
    def send(self, cid: int, msg: dict[str, Any]) -> None: ...


def pump(core: Core, inbox: SimpleQueue, ipc: Sender, now: float) -> int:
    """Handle everything queued so far. Returns the number of items handled."""
    n = 0
    while True:
        try:
            item = inbox.get_nowait()
        except Empty:
            return n
        n += 1
        kind = item[0]
        if kind == "midi":
            _, roles, data, t = item
            core.on_midi(roles, data, t)
        elif kind == "audio":
            core.on_audio(item[1], now)
        elif kind == "disconnect":
            core.client_disconnected(item[1], now)
        elif kind == "msg":
            _, cid, msg = item
            if "_error" in msg:
                ipc.send(cid, P.error(None, msg["_error"]))
                continue
            try:
                cmd = P.parse_command(msg)
            except ValueError as e:
                ipc.send(cid, P.error(msg.get("id"), str(e)))
                continue
            if cmd.cmd not in QUIET:
                log.info("client %s: %s", cid, {k: v for k, v in msg.items() if k != "id"})
            ipc.send(cid, core.handle(cmd, cid, now))
