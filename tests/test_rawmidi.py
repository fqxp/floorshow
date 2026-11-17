import os
import time

import pytest

from floorshow.common.rawmidi import MidiParser, RawMidiReader, list_raw_ports
from floorshow.render.midi import MidiManager


def test_parser_handles_running_status_realtime_and_sysex():
    p = MidiParser()
    assert p.feed(bytes([0x90, 0x32, 0x64, 0x80, 0x32, 0x00])) == [bytes([0x90, 0x32, 0x64]), bytes([0x80, 0x32, 0x00])]
    assert p.feed(bytes([0x90, 60, 100, 62, 90])) == [bytes([0x90, 60, 100]), bytes([0x90, 62, 90])]  # running status
    assert p.feed(bytes([0x90, 60])) == [] and p.feed(bytes([0xF8, 100])) == [bytes([0xF8]), bytes([0x90, 60, 100])]  # split, realtime inside
    assert p.feed(bytes([0xC2, 7, 0xF0, 1, 2, 3, 0xF7, 0xFA])) == [bytes([0xC2, 7]), bytes([0xFA])]  # sysex skipped
    assert p.feed(bytes([0xF2, 5, 1])) == [bytes([0xF2, 5, 1])]  # song position: two data bytes
    assert p.feed(bytes([5])) == []  # stray data byte without a status


CARDS = (" 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n                      HDA Intel PCH at 0xdc248000\n"
         " 1 [VirMIDI        ]: VirMIDI - VirMIDI\n                      Virtual MIDI Card 1\n"
         " 2 [Interface      ]: USB-Audio - USB MIDI Interface\n                      Some Maker USB MIDI\n")


def fake_alsa(tmp_path):
    snd, asound = tmp_path / "snd", tmp_path / "asound"
    snd.mkdir()
    (asound / "card1").mkdir(parents=True)
    (asound / "card1" / "id").write_text("VirMIDI\n")
    (asound / "card2").mkdir(parents=True)
    (asound / "card2" / "id").write_text("Interface\n")
    (asound / "cards").write_text(CARDS)
    for name in ("midiC1D0", "midiC1D3", "midiC2D0", "pcmC0D0p", "controlC1", "midiCxDy"):
        (snd / name).write_text("")
    return snd, asound


def test_raw_ports_are_listed_with_the_sequencer_style_name(tmp_path):
    snd, asound = fake_alsa(tmp_path)
    ports = list_raw_ports(snd, asound)
    assert set(ports) == {"VirMIDI 1-0 (raw hw:1,0)", "VirMIDI 1-3 (raw hw:1,3)"}
    assert ports["VirMIDI 1-0 (raw hw:1,0)"] == snd / "midiC1D0"


def test_a_hardware_interfaces_raw_device_is_not_listed_but_still_found_by_name(tmp_path):
    """It only duplicates the interface's sequencer port, and the sequencer holds it while that port is used."""
    snd, asound = fake_alsa(tmp_path)
    assert "Interface 2-0 (raw hw:2,0)" not in list_raw_ports(snd, asound)
    assert list_raw_ports(snd, asound, only_virtual=False)["Interface 2-0 (raw hw:2,0)"] == snd / "midiC2D0"


def test_a_renamed_virmidi_card_is_still_recognised_by_its_driver(tmp_path):
    snd, asound = fake_alsa(tmp_path)
    (asound / "cards").write_text(CARDS.replace("[VirMIDI        ]", "[Lights         ]"))
    (asound / "card1" / "id").write_text("Lights\n")
    assert set(list_raw_ports(snd, asound)) == {"Lights 1-0 (raw hw:1,0)", "Lights 1-3 (raw hw:1,3)"}


def test_without_proc_asound_cards_no_raw_device_is_listed(tmp_path):
    snd, asound = fake_alsa(tmp_path)
    (asound / "cards").unlink()
    assert list_raw_ports(snd, asound) == {}


def wait_for(cond, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_reader_delivers_messages_with_arrival_times(tmp_path):
    fifo = tmp_path / "midi"
    os.mkfifo(fifo)
    got = []
    reader = RawMidiReader(fifo, lambda m, t: got.append((m, t)), lambda: 42.0)
    w = os.open(fifo, os.O_WRONLY)
    os.write(w, bytes([0x90, 0x32, 0x64, 0x90, 0x34, 73]))
    assert wait_for(lambda: len(got) == 2)
    assert got == [(bytes([0x90, 0x32, 0x64]), 42.0), (bytes([0x90, 0x34, 73]), 42.0)]
    os.close(w)
    assert wait_for(lambda: not reader.alive)  # the writer went away: the reader ends
    reader.close()


def test_manager_opens_a_raw_port_by_its_listed_name(tmp_path, monkeypatch):
    fifo = tmp_path / "midiC9D0"
    os.mkfifo(fifo)
    monkeypatch.setattr("floorshow.render.midi.list_raw_ports", lambda **_k: {"VirMIDI 9-0 (raw hw:9,0)": fifo})
    from queue import SimpleQueue

    inbox = SimpleQueue()
    mgr = MidiManager(inbox)
    mgr.configure("VirMIDI 9-0 (raw hw:9,0)", None)
    mgr.poll(0.0)
    assert mgr.status()["clock"]["open"] == "VirMIDI 9-0 (raw hw:9,0)"
    w = os.open(fifo, os.O_WRONLY)
    os.write(w, bytes([0x90, 50, 100]))
    assert wait_for(lambda: not inbox.empty())
    kind, roles, msg, _t, port = inbox.get()
    assert (kind, roles, msg, port) == ("midi", {"clock"}, bytes([0x90, 50, 100]), "VirMIDI 9-0 (raw hw:9,0)")
    os.close(w)
    mgr.close()


def test_manager_lists_raw_ports_with_the_sequencer_ports(monkeypatch):
    monkeypatch.setattr("floorshow.render.midi.list_raw_ports", lambda **_k: {"X 1-0 (raw hw:1,0)": None})
    assert "X 1-0 (raw hw:1,0)" in MidiManager(__import__("queue").SimpleQueue()).list_ports()
