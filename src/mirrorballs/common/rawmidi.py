"""ALSA raw MIDI devices (``/dev/snd/midiC<card>D<dev>``) as MIDI inputs.

Some setups send MIDI into a virtual raw MIDI card ("VirMIDI"): a program that writes to the sequencer port
"VirMIDI 1-0" (Mixxx does) makes the bytes appear on the raw device ``/dev/snd/midiC1D0``, and only there. Sequencer
clients (rtmidi) cannot subscribe to that, so the renderer reads the device file itself. The program never opens the
raw device, so nothing is shared; and a VirMIDI raw device can be opened by several readers and a writer at once
(tried on 2026-11-17).

Hardware MIDI interfaces are different: everything they send is on their sequencer port already, and while anything
uses that port the sequencer holds their raw device, so opening it fails as busy. Their raw devices are therefore only
duplicates, and they are not listed (``only_virtual``); one that is configured by name still opens.
"""

from __future__ import annotations

import logging
import os
import re
import select
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("mirrorballs.midi")

SND_DIR = Path("/dev/snd")
ASOUND_DIR = Path("/proc/asound")
_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2, 0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0}


VIRTUAL_DRIVER = "VirMIDI"


def virtual_cards(asound_dir: Path = ASOUND_DIR) -> set[str]:
    """Numbers of the sound cards whose driver is VirMIDI, from /proc/asound/cards ("1 [VirMIDI ]: VirMIDI - ...").
    The driver, not the card's id: the id can be renamed with a module option."""
    try:
        lines = (asound_dir / "cards").read_text().splitlines()
    except OSError:
        return set()
    out = set()
    for line in lines:
        m = re.match(r"\s*(\d+)\s+\[[^\]]*\]:\s*(\S+)", line)
        if m and m.group(2) == VIRTUAL_DRIVER:
            out.add(m.group(1))
    return out


def list_raw_ports(snd_dir: Path = SND_DIR, asound_dir: Path = ASOUND_DIR, only_virtual: bool = True) -> dict[str, Path]:
    """Readable raw MIDI devices: display name -> device file. The name has the shape of the sequencer's
    ("VirMIDI 1-0") plus "(raw hw:1,0)", so it is clear which kind of port it is. ``only_virtual``: VirMIDI devices
    only (the others duplicate their sequencer port; see the module's description)."""
    ports: dict[str, Path] = {}
    try:
        devices = sorted(snd_dir.glob("midiC*D*"))
    except OSError:
        return ports
    virtual = virtual_cards(asound_dir) if only_virtual else set()
    for dev in devices:
        try:
            card, device = dev.name[len("midiC"):].split("D")
            int(card), int(device)
        except ValueError:
            continue
        if only_virtual and card not in virtual:
            continue
        if not os.access(dev, os.R_OK):
            continue
        try:
            card_name = (asound_dir / f"card{card}" / "id").read_text().strip()
        except OSError:
            card_name = f"card {card}"
        ports[f"{card_name} {card}-{device} (raw hw:{card},{device})"] = dev
    return ports


class MidiParser:
    """Bytes of a raw MIDI stream -> complete messages (running status, interleaved realtime bytes, sysex skipped)."""

    def __init__(self) -> None:
        self._status = 0
        self._data: list[int] = []
        self._need = 0
        self._in_sysex = False

    def feed(self, chunk: bytes) -> list[bytes]:
        out: list[bytes] = []
        for b in chunk:
            if b >= 0xF8:  # realtime: may sit between the bytes of another message
                out.append(bytes([b]))
            elif b >= 0x80:
                self._in_sysex = b == 0xF0
                if b in (0xF0, 0xF7):
                    self._status, self._need = 0, 0
                    continue
                self._status, self._data = b, []
                self._need = _DATA_LEN.get(b if b >= 0xF0 else b & 0xF0, 0)
                if self._need == 0:
                    out.append(bytes([b]))
                    self._status = 0
            elif self._status and not self._in_sysex:
                self._data.append(b)
                if len(self._data) == self._need:
                    out.append(bytes([self._status, *self._data]))
                    self._data = []  # running status: the next data bytes reuse the status
                    if self._status >= 0xF0:
                        self._status = 0
        return out


class RawMidiReader:
    """Reads one raw device on a thread and hands every message to ``on_message(bytes, monotonic_time)``."""

    def __init__(self, path: Path, on_message: Callable[[bytes, float], None], time_fn: Callable[[], float] = time.monotonic):
        self.path = Path(path)
        self._on_message = on_message
        self._time = time_fn
        self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rawmidi", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        parser = MidiParser()
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.2)
                if not ready:
                    continue
                chunk = os.read(self._fd, 256)
            except BlockingIOError:
                continue
            except OSError as e:
                log.warning("raw MIDI device %s failed: %s", self.path, e)
                return
            if not chunk:  # the writer went away (a fifo in tests; a device never ends)
                return
            now = self._time()
            for msg in parser.feed(chunk):
                self._on_message(msg, now)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            os.close(self._fd)
        except OSError:
            pass
