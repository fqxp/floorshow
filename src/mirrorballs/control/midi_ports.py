"""MIDI input ports available to choose from (the controller lists them; the renderer opens them)."""

from __future__ import annotations


def list_input_ports() -> list[str]:
    try:
        import rtmidi

        probe = rtmidi.MidiIn()
        ports = probe.get_ports()
        probe.delete() if hasattr(probe, "delete") else None
    except Exception:  # noqa: BLE001 - no ALSA sequencer, or rtmidi missing
        return []
    return [p for p in ports if "midi through" not in p.lower()]
