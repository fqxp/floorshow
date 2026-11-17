"""What the source picker and the Audio hover show: audio sources with their level meters, MIDI ports with their beat
previews, and the toasts when one is chosen. Pure functions of the renderer's state, so they are tested without GTK.

The renderer's state carries ``audio`` (see render/audio_state.py) and ``midi_ports`` (render/midi_preview.py).
Times in them are on the monotonic clock, which the controller shares with the renderer (same machine).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SILENT_DB = -60.0  # a source whose loudest channel stays below this has nothing on it


@dataclass(frozen=True)
class Item:
    """One chip in the picker."""

    id: str  # what is saved: the audio node name, or the MIDI port name
    name: str
    status: str  # short text after the name: "124 BPM", "no beat", "busy", "silent", ""
    kind: str = ""  # audio: "mic" | "monitor"; MIDI: the preview status ("clock", "beats", "active", "quiet", "busy")
    levels: tuple[float, ...] = field(default=(), compare=False)  # audio: rms dB per channel
    peaks: tuple[float, ...] = field(default=(), compare=False)
    bpm: float | None = field(default=None, compare=False)  # MIDI: for the beat tile
    beat_time: float | None = field(default=None, compare=False)
    dim: bool = False  # nothing on it: shown, but faded


def loudest(levels: tuple[float, ...] | list[float]) -> float:
    return max(levels) if levels else -100.0


def audio_items(audio: dict[str, Any] | None) -> list[Item]:
    """The audio sources, outputs (line level) first as the listener sorts them. The chosen one also shows its tempo."""
    if not audio:
        return []
    chosen = audio.get("source")
    tracking = audio.get("tracking")
    out = []
    for s in audio.get("sources") or []:
        levels = tuple(s.get("rms_db") or ())
        silent = bool(levels) and loudest(levels) < SILENT_DB
        status = ""
        if s["id"] == chosen and tracking:
            status = f"{tracking['bpm']:.0f} BPM" if tracking["locked"] else "no beat"
        elif silent:
            status = "silent"
        out.append(Item(s["id"], s["name"], status, s.get("kind", ""), levels, tuple(s.get("peak_db") or ()), dim=silent))
    return out


def midi_items(ports: list[dict[str, Any]] | None) -> list[Item]:
    out = []
    for p in ports or []:
        st = p["status"]
        if st in ("clock", "beats") and p.get("bpm"):
            status = f"{p['bpm']:.0f} BPM"
        else:
            status = {"clock": "clock", "active": "no beat", "busy": "busy"}.get(st, "")
        out.append(Item(p["name"], _short_port(p["name"]), status, st, bpm=p.get("bpm"), beat_time=p.get("beat_time"),
                        dim=st in ("quiet", "busy")))
    return out


def _short_port(name: str) -> str:
    """ "RtMidiOut Client:floorshow test clock 128:0" -> "floorshow test clock": the ALSA client and numbers say
    nothing to a person (the full name is the chip's tooltip)."""
    if ":" in name and not name.endswith(")"):
        client, _, rest = name.partition(":")
        parts = rest.rsplit(" ", 1)
        port = parts[0] if len(parts) == 2 and parts[1].replace(":", "").isdigit() else rest
        return port if port.strip() else client
    return name


def audio_placeholder(items: list[Item], cfg_source: str | None) -> str:
    """The dropdown's entry while the chosen source is not among the sources."""
    if cfg_source:
        return f"{cfg_source} (not there)"
    return "Choose a source…" if items else "No audio source found"


def selected_audio(audio: dict[str, Any] | None, cfg_source: str | None) -> str | None:
    """The id to show as chosen: the resolved source, else what the config names (it may be unplugged)."""
    return (audio or {}).get("source") or cfg_source


def selected_midi(ports: list[dict[str, Any]] | None, cfg_port: str | None) -> str | None:
    """The port the config's substring picks, as the renderer matches it."""
    if not cfg_port:
        return None
    want = cfg_port.lower()
    return next((p["name"] for p in ports or [] if want in p["name"].lower()), cfg_port)


def items_key(items: list[Item]) -> str:
    """Changes when the chips have to be rebuilt (not when a meter or a tempo moves)."""
    return json.dumps([(i.id, i.name, i.kind) for i in items])


def hover_text(audio: dict[str, Any] | None, cfg_source: str | None, active: bool = True,
               cfg_name: str | None = None) -> tuple[str, dict[str, Any] | None]:
    """(text, tracking or None) for the popover over the Audio option: the chosen source and whether it has a beat.

    The listener only runs while the audio clock drives the show (``active``); under another clock the popover just
    names the source that would be listened to (``cfg_name``: its name, if the window has seen it)."""
    if audio is None:
        return "Audio · the renderer is not running", None
    if not active:
        if not cfg_source:
            return "Audio · no source chosen yet: switch to Audio and choose one", None
        return f"Audio · {cfg_name or cfg_source} · listened to while the audio clock runs", None
    if audio.get("error") and not audio.get("running"):
        return f"Audio is not available: {audio['error']}", None
    if not audio.get("running"):
        return f"Audio · starting to listen to {cfg_name or cfg_source or 'audio'}…", None
    chosen = audio.get("source")
    if not chosen:
        if cfg_source:
            return f"Audio · {cfg_source} is not there", None
        return "Audio · no source chosen yet: switch to Audio and pick one", None
    name = _name(audio, chosen)
    level = next((loudest(s.get("rms_db") or []) for s in audio.get("sources") or [] if s["id"] == chosen), None)
    tracking = audio.get("tracking")
    if level is not None and level < SILENT_DB:
        return f"{name} · silent", None
    if tracking and tracking["locked"]:
        return f"{name} · {tracking['bpm']:.1f} BPM", tracking
    return f"{name} · no beat yet", None


def audio_switch_toast(cfg_source: str | None, cfg_name: str | None, holding_bpm: float) -> str:
    """The toast when the show is switched to the audio clock. The listener only starts now, so whether there is a
    beat is said a few seconds later (``no_beat_toast``)."""
    if not cfg_source:
        return "Audio clock · choose a source beside Downbeat"
    return f"Audio clock · listening to {cfg_name or cfg_source} (holding {holding_bpm:.0f} BPM until the beat is found)"


def audio_pick_toast(item: Item | None, name: str) -> str:
    if item is not None and item.levels and loudest(item.levels) < SILENT_DB:
        return f"Audio · {name} is silent"
    return f"Audio · {name}"


def no_beat_toast(audio: dict[str, Any] | None, sid: str | None) -> str | None:
    """Some seconds after a source was picked (``sid``) or the audio clock was switched on (``sid`` None: whatever
    source it listens to): say so when there is still no beat. None when there is one, or another source was picked
    meanwhile."""
    if not audio or not audio.get("source") or (sid is not None and audio.get("source") != sid):
        return None
    sid = audio["source"]
    tracking = audio.get("tracking")
    if tracking and tracking["locked"]:
        return None
    level = next((loudest(s.get("rms_db") or []) for s in audio.get("sources") or [] if s["id"] == sid), None)
    if level is not None and level < SILENT_DB:
        return f"{_name(audio, sid)} is silent"
    return f"No beat found on {_name(audio, sid)} so far"


def midi_pick_toast(item: Item) -> str:
    if item.kind in ("clock", "beats") and item.bpm:
        return f"MIDI · {item.name} · {item.bpm:.0f} BPM"
    if item.kind == "busy":
        return f"MIDI · {item.name} is in use by another program"
    return f"MIDI · {item.name} · no beat on this port yet"


def _name(audio: dict[str, Any], sid: str) -> str:
    return next((s["name"] for s in audio.get("sources") or [] if s["id"] == sid), sid)


def stable_audio(audio: dict[str, Any] | None) -> Any:
    """The part of the audio state that the window's layout depends on (for view_key): not levels or tempo."""
    if not audio:
        return None
    t = audio.get("tracking")
    return {"sources": [(s["id"], s["name"]) for s in audio.get("sources") or []], "source": audio.get("source"),
            "error": audio.get("error"), "running": audio.get("running"), "locked": bool(t and t["locked"])}


def stable_midi(ports: list[dict[str, Any]] | None) -> Any:
    return [(p["name"], p["status"]) for p in ports or []]


def beat_phase(bpm: float | None, beat_time: float | None, now: float) -> float | None:
    """Phase 0..1 of a preview beat at ``now``, or None without a beat."""
    if not bpm or beat_time is None:
        return None
    b = (now - beat_time) * bpm / 60.0
    return b - int(b // 1) if b >= 0 else b % 1.0


def tracking_phase(tracking: dict[str, Any] | None, now: float) -> float | None:
    if not tracking or not tracking.get("locked"):
        return None
    b = tracking["anchor_beat"] + (now - tracking["anchor_time"]) * tracking["bpm"] / 60.0
    return b % 1.0
