"""MIDI input ports for the renderer (rtmidi callbacks -> inbox).

Ports are matched by name substring (ALSA client numbers change between runs) and re-opened
when a device appears later or disappears. One physical port can serve both roles: ``clock``
(realtime messages drive the beat clock) and ``control`` (notes / program change / CC trigger
scene actions). Callbacks run on rtmidi's thread and only enqueue, stamped with
``time.monotonic()`` so the clock sees arrival times, not the time the frame loop got to them.

While a client previews (``configure(..., preview=True)``) every other input port is opened too, with the role
``preview``: its messages only feed the beat previews of the port selector. A port that is chosen for a role is
handed over from the preview, so the renderer never reads a device twice. Raw devices are only listed for VirMIDI
cards (see common/rawmidi.py), which several programs can open at once; one that cannot be opened all the same is
reported as busy.

Inbox items: ``("midi", roles, message bytes, time, port name)``.
"""

from __future__ import annotations

import logging
import time
from queue import SimpleQueue
from typing import Any

import rtmidi

from ..common.rawmidi import RawMidiReader, list_raw_ports

log = logging.getLogger("floorshow.midi")
POLL_S = 1.0
PREVIEW = frozenset({"preview"})


def _is_own_or_through(name: str) -> bool:
    low = name.lower()
    return "midi through" in low or low.startswith("floorshow")


class MidiManager:
    def __init__(self, inbox: SimpleQueue, time_fn=time.monotonic):
        self.inbox = inbox
        self.time_fn = time_fn
        self._wanted: dict[str, set[str]] = {}  # port substring (lowercase) -> roles
        self._open: dict[str, tuple[Any, str]] = {}  # substring -> (rtmidi input or RawMidiReader, full port name)
        self._preview = False
        self._preview_open: dict[str, Any] = {}  # full port name -> input, opened only for the preview
        self._busy: set[str] = set()  # ports the preview could not open
        self._probe = rtmidi.MidiIn()
        self._next_poll = 0.0

    def configure(self, clock_port: str | None, control_port: str | None, preview: bool = False) -> None:
        wanted: dict[str, set[str]] = {}
        for role, port in (("clock", clock_port), ("control", control_port)):
            if port:
                wanted.setdefault(port.lower(), set()).add(role)
        self._wanted = wanted
        for key in [k for k in self._open if k not in wanted]:
            self._close(key)
        self._preview = preview
        if not preview:
            for name in list(self._preview_open):
                self._close_preview(name)
            self._busy.clear()
        self._next_poll = 0.0

    def poll(self, now: float) -> None:
        if now < self._next_poll:
            return
        self._next_poll = now + POLL_S
        names = self._probe.get_ports()
        raw = list_raw_ports(only_virtual=False)  # a configured port opens even if it is not listed
        for key in list(self._open):
            inp, name = self._open[key]
            gone = not inp.alive if isinstance(inp, RawMidiReader) else name not in names
            if gone or (isinstance(inp, RawMidiReader) and name not in raw):  # unplugged
                log.info("MIDI port '%s' went away", name)
                self._close(key)
        for name in list(self._preview_open):
            inp = self._preview_open[name]
            if (not inp.alive) if isinstance(inp, RawMidiReader) else name not in names:
                self._close_preview(name)
        for key in self._wanted:
            if key in self._open:
                continue
            idx = next((i for i, n in enumerate(names) if key in n.lower()), None)
            if idx is not None:
                self._close_preview(names[idx])  # the preview hands the port over to its role
                self._open_port(key, idx, names[idx])
                continue
            raw_name = next((n for n in raw if key in n.lower()), None)
            if raw_name is not None:
                self._close_preview(raw_name)
                self._open_raw(key, raw_name, raw[raw_name])
        if self._preview:
            self._poll_preview(names, list_raw_ports())  # listed raw devices only (VirMIDI)

    def _poll_preview(self, names: list[str], raw: dict[str, Any]) -> None:
        in_role = {name for _, name in self._open.values()}
        self._busy &= set(names) | set(raw)
        for idx, name in enumerate(names):
            if name in in_role or name in self._preview_open or _is_own_or_through(name):
                continue
            inp = self._make_input(idx, name, lambda: PREVIEW)
            if inp is not None:
                self._preview_open[name] = inp
        for name, path in raw.items():
            if name in in_role or name in self._preview_open:
                continue
            try:
                self._preview_open[name] = RawMidiReader(path, self._raw_callback(name, lambda: PREVIEW), self.time_fn)
                self._busy.discard(name)
            except OSError:
                self._busy.add(name)  # another program has the device; tried again on the next poll

    def _make_input(self, idx: int, name: str, roles_fn) -> Any:
        try:
            inp = rtmidi.MidiIn(name="floorshow")
            inp.ignore_types(sysex=True, timing=False, active_sense=True)

            def callback(event: Any, _data: Any = None) -> None:
                msg, _delta = event
                roles = roles_fn()
                if roles:
                    self.inbox.put(("midi", roles, bytes(msg), self.time_fn(), name))

            inp.set_callback(callback)
            inp.open_port(idx)
            return inp
        except Exception as e:  # noqa: BLE001 - a bad device must not take the renderer down
            log.warning("cannot open MIDI port '%s': %s", name, e)
            return None

    def _raw_callback(self, name: str, roles_fn):
        def callback(msg: bytes, t: float) -> None:
            roles = roles_fn()
            if roles:
                self.inbox.put(("midi", roles, msg, t, name))

        return callback

    def _open_port(self, key: str, idx: int, name: str) -> None:
        inp = self._make_input(idx, name, lambda: self._wanted.get(key))
        if inp is not None:
            self._open[key] = (inp, name)
            log.info("MIDI input opened: %s (%s)", name, "+".join(sorted(self._wanted[key])))

    def _open_raw(self, key: str, name: str, path: Any) -> None:
        try:
            self._open[key] = (RawMidiReader(path, self._raw_callback(name, lambda: self._wanted.get(key)),
                                             self.time_fn), name)
            log.info("MIDI input opened: %s (%s)", name, "+".join(sorted(self._wanted[key])))
        except OSError as e:
            log.warning("cannot open MIDI device '%s': %s", name, e)

    def _close(self, key: str) -> None:
        inp, _ = self._open.pop(key)
        _close_input(inp)

    def _close_preview(self, name: str) -> None:
        inp = self._preview_open.pop(name, None)
        if inp is not None:
            _close_input(inp)

    def status(self) -> dict[str, Any]:
        def role(r: str) -> dict[str, Any]:
            key = next((k for k, roles in self._wanted.items() if r in roles), None)
            return {"wanted": key, "open": self._open[key][1] if key in self._open else None} if key else {"wanted": None, "open": None}

        out: dict[str, Any] = {"clock": role("clock"), "control": role("control")}
        if self._preview:
            ports = {name: "open" for _, name in self._open.values()}
            ports.update({name: "open" for name in self._preview_open})
            ports.update({name: "busy" for name in self._busy})
            out["ports"] = ports
        return out

    def list_ports(self) -> list[str]:
        return self._probe.get_ports() + list(list_raw_ports())

    def close(self) -> None:
        for key in list(self._open):
            self._close(key)
        for name in list(self._preview_open):
            self._close_preview(name)


def _close_input(inp: Any) -> None:
    try:
        if isinstance(inp, RawMidiReader):
            inp.close()
        else:
            inp.cancel_callback()
            inp.close_port()
    except Exception:  # noqa: BLE001
        pass
