"""MIDI input ports for the renderer (rtmidi callbacks -> inbox).

Ports are matched by name substring (ALSA client numbers change between runs) and re-opened
when a device appears later or disappears. One physical port can serve both roles: ``clock``
(realtime messages drive the beat clock) and ``control`` (notes / program change / CC trigger
scene actions). Callbacks run on rtmidi's thread and only enqueue, stamped with
``time.monotonic()`` so the clock sees arrival times, not the time the frame loop got to them.
"""

from __future__ import annotations

import logging
import time
from queue import SimpleQueue
from typing import Any

import rtmidi

log = logging.getLogger("mirrorballs.midi")
POLL_S = 1.0


class MidiManager:
    def __init__(self, inbox: SimpleQueue, time_fn=time.monotonic):
        self.inbox = inbox
        self.time_fn = time_fn
        self._wanted: dict[str, set[str]] = {}  # port substring (lowercase) -> roles
        self._open: dict[str, tuple[rtmidi.MidiIn, str]] = {}  # substring -> (input, full port name)
        self._probe = rtmidi.MidiIn()
        self._next_poll = 0.0

    def configure(self, clock_port: str | None, control_port: str | None) -> None:
        wanted: dict[str, set[str]] = {}
        for role, port in (("clock", clock_port), ("control", control_port)):
            if port:
                wanted.setdefault(port.lower(), set()).add(role)
        self._wanted = wanted
        for key in [k for k in self._open if k not in wanted]:
            self._close(key)
        self._next_poll = 0.0

    def poll(self, now: float) -> None:
        if now < self._next_poll:
            return
        self._next_poll = now + POLL_S
        names = self._probe.get_ports()
        for key in list(self._open):
            if self._open[key][1] not in names:  # unplugged
                log.info("MIDI port '%s' went away", self._open[key][1])
                self._close(key)
        for key in self._wanted:
            if key in self._open:
                continue
            idx = next((i for i, n in enumerate(names) if key in n.lower()), None)
            if idx is not None:
                self._open_port(key, idx, names[idx])

    def _open_port(self, key: str, idx: int, name: str) -> None:
        try:
            inp = rtmidi.MidiIn(name="mirrorballs")
            inp.ignore_types(sysex=True, timing=False, active_sense=True)

            def callback(event: Any, _data: Any = None, key: str = key) -> None:
                msg, _delta = event
                roles = self._wanted.get(key)
                if roles:
                    self.inbox.put(("midi", roles, bytes(msg), self.time_fn()))

            inp.set_callback(callback)
            inp.open_port(idx)
            self._open[key] = (inp, name)
            log.info("MIDI input opened: %s (%s)", name, "+".join(sorted(self._wanted[key])))
        except Exception as e:  # noqa: BLE001 - a bad device must not take the renderer down
            log.warning("cannot open MIDI port '%s': %s", name, e)

    def _close(self, key: str) -> None:
        inp, _ = self._open.pop(key)
        try:
            inp.cancel_callback()
            inp.close_port()
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict[str, Any]:
        def role(r: str) -> dict[str, Any]:
            key = next((k for k, roles in self._wanted.items() if r in roles), None)
            return {"wanted": key, "open": self._open[key][1] if key in self._open else None} if key else {"wanted": None, "open": None}

        return {"clock": role("clock"), "control": role("control")}

    def list_ports(self) -> list[str]:
        return self._probe.get_ports()

    def close(self) -> None:
        for key in list(self._open):
            self._close(key)
