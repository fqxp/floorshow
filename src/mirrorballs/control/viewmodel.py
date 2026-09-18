"""Pure functions from renderer state to what the main window shows. No GTK, so they are tested."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TRANSITIONS = [
    ("crossfade", "Crossfade"), ("cut", "Cut"), ("wipe_lat", "Wipe (latitude)"), ("iris", "Iris"), ("flash", "Flash"),
]
LENGTHS = [("¼ bar", 0.25), ("½ bar", 0.5), ("1 bar", 1.0), ("2 bars", 2.0)]
MAX_HOTKEYS = 8


@dataclass(frozen=True)
class Chip:
    text: str
    kind: str  # "ok" (teal dot) | "off" (hollow dot) | "busy" (spinner)


def renderer_chip(connected: bool, starting: bool, state: dict[str, Any] | None) -> Chip:
    if starting and not connected:
        return Chip("Connecting…", "busy")
    if not connected or state is None:
        return Chip("Not running", "off")
    return Chip(f"Renderer: running · {state.get('fps', 0):.0f} fps", "ok")


def clock_text(clock: dict[str, Any] | None, cfg_source: str = "internal", cfg_port: str | None = None,
               cfg_bpm: float = 120.0) -> tuple[str, bool]:
    """(source chip text, is_warning). Uses the live clock when connected, else the saved config."""
    if clock is None:
        if cfg_source == "midi":
            return f"MIDI · {cfg_port or 'no port selected'} · not running", False
        return f"Internal · {cfg_bpm:.0f} BPM", False
    if clock["source"] == "internal":
        return f"Internal · {clock['bpm']:.0f} BPM", False
    port = clock.get("port") or "MIDI"
    if not clock["locked"]:
        return f"MIDI · waiting for clock (holding {clock['bpm']:.0f} BPM)", True
    return f"MIDI · {port} · {clock['bpm']:.1f} BPM", False


def beat_in_bar(clock: dict[str, Any], elapsed_s: float = 0.0) -> tuple[int, float]:
    """(0-based beat within the bar, phase 0..1) extrapolated ``elapsed_s`` past the state sample."""
    beat = clock["beat"] + elapsed_s * clock["bpm"] / 60.0
    bpb = max(1, clock["beats_per_bar"])
    return int(beat // 1) % bpb, beat - int(beat // 1)


@dataclass(frozen=True)
class Banner:
    kind: str  # "midi_lost" | "scene_error" | "calibrating"
    title: str
    text: str
    action: str  # button label
    scene_id: str | None = None


def banners(connected: bool, state: dict[str, Any] | None) -> list[Banner]:
    if not connected or state is None:
        return []
    out: list[Banner] = []
    if state.get("calibrating"):
        out.append(Banner("calibrating", "Calibration in progress", "Calibration in progress", "Show window"))
    clock = state["clock"]
    if clock["source"] == "midi" and not clock["locked"]:
        out.append(Banner("midi_lost", "MIDI clock lost",
                          f"MIDI clock lost. Holding the last tempo ({clock['bpm']:.0f} BPM).", "Use internal clock"))
    for sid, problems in state.get("scene_errors", {}).items():
        out.append(Banner("scene_error", "File error", f"{sid}.yaml · {problems[0]}", "Open in editor", scene_id=sid))
        break  # one banner at a time; the config dialog lists all of them
    return out


def hotkeys(scene_ids: list[str], keys: dict[str, str] | None = None) -> dict[str, str]:
    """Scene id -> key label. Explicit ``bindings.keys`` (key -> scene id) win; the remaining scenes
    get the free digits 1-8 in order."""
    result: dict[str, str] = {}
    for key, target in (keys or {}).items():
        if target in scene_ids and target not in result and key.isdigit():
            result[target] = key
    taken = set(result.values())
    free = iter(str(n) for n in range(1, MAX_HOTKEYS + 1) if str(n) not in taken)
    for sid in scene_ids:
        if sid not in result:
            k = next(free, None)
            if k is None:
                break
            result[sid] = k
    return result


def scene_for_key(key: str, scene_ids: list[str], keys: dict[str, str] | None = None) -> str | None:
    return next((sid for sid, k in hotkeys(scene_ids, keys).items() if k == key), None)


def transition_payload(type_id: str, bars: float, beats_per_bar: int) -> dict[str, Any]:
    return {"type": type_id, "beats": round(bars * beats_per_bar, 3)}


def nearest_length(beats: float, beats_per_bar: int) -> int:
    """Index into LENGTHS closest to ``beats`` (for showing the configured transition)."""
    bars = beats / max(1, beats_per_bar)
    return min(range(len(LENGTHS)), key=lambda i: abs(LENGTHS[i][1] - bars))


def display_name(scene_id: str) -> str:
    return scene_id.replace("-", " ").replace("_", " ").title()


def key_to_action(name: str, ctrl: bool = False, shift: bool = False) -> tuple[str, Any] | None:
    """Keyboard shortcut (from the design's shortcut sheet) -> (action, argument), or None.
    ``name`` is the GDK key name, e.g. "space", "bracketleft", "1"."""
    if ctrl:
        return {"Up": ("brightness", +5), "Down": ("brightness", -5), "comma": ("config", None),
                "question": ("shortcuts", None), "slash": ("shortcuts", None) if shift else None}.get(name)
    if name == "space":
        return ("toggle-run", None)
    if name in ("t", "T"):
        return ("tap", None)
    if name in ("b", "B"):
        return ("blackout", None)
    if name in ("m", "M"):
        return ("mode", "manual")
    if name in ("r", "R"):
        return ("mode", "random")
    if name == "bracketleft":
        return ("step", -1)
    if name == "bracketright":
        return ("step", +1)
    if len(name) == 1 and name in "12345678":
        return ("scene-key", name)
    return None
