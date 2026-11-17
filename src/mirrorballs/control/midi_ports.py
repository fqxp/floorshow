"""MIDI input ports available to choose from (the controller lists them; the renderer opens them).

Sequencer ports come first, then the raw devices of VirMIDI cards ("VirMIDI 1-0 (raw hw:1,0)"); a hardware interface's
raw device only duplicates its sequencer port and is not offered."""

from __future__ import annotations

from ..common.rawmidi import list_raw_ports


def list_input_ports() -> list[str]:
    try:
        import rtmidi

        probe = rtmidi.MidiIn()
        ports = probe.get_ports()
        probe.delete() if hasattr(probe, "delete") else None
    except Exception:  # noqa: BLE001 - no ALSA sequencer, or rtmidi missing
        ports = []
    return [p for p in ports if "midi through" not in p.lower()] + list(list_raw_ports())
