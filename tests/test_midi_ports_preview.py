"""MIDI port previews in the core (state, per-client requests) and in the MIDI manager over virtual ALSA ports."""

import random
import time
from queue import SimpleQueue

import pytest

from mirrorballs.common import protocol as P
from mirrorballs.render.core import Core
from mirrorballs.render.dispatch import pump

SCENE = "schema: 1\nname: a\nlayers:\n  - {type: fill, color: white}\n"


def make_core(tmp_path, config_text="clock: {source: midi, midi_port: mixxx}\n"):
    (tmp_path / "scenes").mkdir(exist_ok=True)
    (tmp_path / "scenes" / "a.yaml").write_text(SCENE)
    (tmp_path / "config.yaml").write_text(config_text)
    return Core(tmp_path / "config.yaml", [tmp_path / "scenes"], now=100.0, rng=random.Random(1))


def test_previews_are_per_client_and_open_every_port(tmp_path):
    c = make_core(tmp_path)
    assert c.midi_wants() == ("mixxx", None, False) and c.state(100.0)["midi_ports"] == []
    c.midi_dirty = False
    assert c.handle(P.parse_command({"cmd": "midi.preview", "on": True}), "c1", 100.0)["ok"]
    assert c.midi_wants() == ("mixxx", None, True) and c.midi_dirty
    c.client_disconnected("c1", 100.0)
    assert c.midi_wants() == ("mixxx", None, False)


def test_every_port_shows_its_beat_and_busy_ports_say_so(tmp_path):
    c = make_core(tmp_path)
    c.handle(P.parse_command({"cmd": "midi.preview", "on": True}), "c1", 100.0)
    c.midi_status = {"clock": {"wanted": "mixxx", "open": "Mixxx 1"}, "control": {"wanted": None, "open": None},
                     "ports": {"Mixxx 1": "open", "Pads": "open", "VirMIDI 1-0 (raw hw:1,0)": "busy"}}
    inbox = SimpleQueue()
    t, period = 100.0, 60.0 / (126 * 24)
    for k in range(60):  # clock ticks on the clock port: they drive the show and feed its preview
        inbox.put(("midi", {"clock"}, bytes([0xF8]), t + k * period, "Mixxx 1"))
    inbox.put(("midi", frozenset({"preview"}), bytes([0x90, 36, 90]), t, "Pads"))
    pump(c, inbox, None, t)
    st = c.state(t + 59 * period + 0.01)
    ports = {p["name"]: p for p in st["midi_ports"]}
    assert ports["Mixxx 1"]["status"] == "clock" and ports["Mixxx 1"]["bpm"] == pytest.approx(126, abs=0.2)
    assert ports["Pads"]["status"] == "active"
    assert ports["VirMIDI 1-0 (raw hw:1,0)"]["status"] == "busy"
    assert "ports" not in st["midi"]  # the old shape of the MIDI status stays as it was
    assert st["clock"]["locked"]  # and the clock port still drives the clock


def test_preview_only_messages_trigger_nothing(tmp_path):
    c = make_core(tmp_path, "clock: {source: internal}\nmidi:\n  control_port: pads\n"
                            "  map:\n    - {on: {note: 36}, do: blackout}\n")
    c.handle(P.parse_command({"cmd": "midi.preview", "on": True}), "c1", 100.0)
    c.on_midi(frozenset({"preview"}), bytes([0x90, 36, 100]), 100.0, "Other pads")
    assert c.running  # a note on a port that is only previewed is not a control message


# --- the manager over virtual ALSA ports ----------------------------------------------------


def test_the_manager_opens_every_port_while_previewing():
    rtmidi = pytest.importorskip("rtmidi")
    from mirrorballs.render.midi import MidiManager

    outs = []
    try:
        for name in ("mbtest preview a", "mbtest preview b"):
            o = rtmidi.MidiOut()
            o.open_virtual_port(name)
            outs.append(o)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no ALSA sequencer: {e}")
    inbox = SimpleQueue()
    mgr = MidiManager(inbox)
    try:
        mgr.configure("mbtest preview a", None, preview=True)
        mgr.poll(0.0)
        ports = mgr.status()["ports"]
        a = next(n for n in ports if "mbtest preview a" in n)
        b = next(n for n in ports if "mbtest preview b" in n)
        assert ports[a] == ports[b] == "open" and mgr.status()["clock"]["open"] == a
        assert not any("midi through" in n.lower() for n in ports)
        outs[0].send_message([0xF8])
        outs[1].send_message([0x90, 60, 100])
        got, end = [], time.monotonic() + 2
        while len(got) < 2 and time.monotonic() < end:
            try:
                got.append(inbox.get(timeout=0.1))
            except Exception:
                pass
        by_port = {g[4]: g for g in got}
        assert by_port[a][1] == {"clock"} and by_port[a][2] == bytes([0xF8])
        assert by_port[b][1] == frozenset({"preview"}) and by_port[b][2] == bytes([0x90, 60, 100])
        mgr.configure("mbtest preview a", None, preview=False)
        assert "ports" not in mgr.status() and mgr.status()["clock"]["open"] == a  # the clock port stays open
    finally:
        mgr.close()
        for o in outs:
            o.close_port()
