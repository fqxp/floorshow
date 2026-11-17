"""Pure functions from renderer state to what the main window shows. No GTK, so they are tested."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common.protocol import REV

TRANSITIONS = [
    ("crossfade", "Crossfade"), ("cut", "Cut"), ("wipe_lat", "Wipe (latitude)"), ("iris", "Iris"), ("flash", "Flash"),
    ("random", "Random"),
]
LENGTHS = [("¼ bar", 0.25), ("½ bar", 0.5), ("1 bar", 1.0), ("2 bars", 2.0)]
HOTKEY_DIGITS = "1234567890"  # scene 10 is on the 0 key, where it sits on the keyboard
MAX_HOTKEYS = len(HOTKEY_DIGITS)


def change_progress(state: dict[str, Any] | None, elapsed_s: float = 0.0) -> float | None:
    """Fraction (1 -> 0) of the current automatic-scene period that is left; None when there is no such period."""
    nxt = state.get("next_change_beat") if state else None
    length = state.get("change_interval_beats") if state else None
    if nxt is None or not length:
        return None
    clock = state["clock"]
    beat = clock["beat"] + elapsed_s * clock["bpm"] / 60.0
    return min(1.0, max(0.0, (nxt - beat) / length))


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


def midi_input_status(state: dict[str, Any]) -> tuple[str, str]:
    """(text, kind) for the MIDI clock input: what is wrong, or that it works. kind: "ok" | "warn" | "" (not used)."""
    clock = state["clock"]
    if clock["source"] != "midi":
        return f"Not used ({clock['source']} clock)", ""
    midi = state.get("midi")
    port = (midi or {}).get("clock") or {}
    if midi and not port.get("wanted"):  # an empty "midi" means the renderer did not say
        return "No port selected", "warn"
    if midi and not port.get("open"):
        return "Port not found", "warn"
    if not clock["locked"]:
        return f"Waiting for {'beat notes' if clock.get('midi_mode') == 'beats' else 'clock'}", "warn"
    return f"Receiving · {clock['bpm']:.1f} BPM", "ok"


def clock_text(clock: dict[str, Any] | None, cfg_source: str = "internal", cfg_port: str | None = None,
               cfg_bpm: float = 120.0, midi: dict[str, Any] | None = None, cfg_audio: str | None = None,
               audio: dict[str, Any] | None = None) -> tuple[str, bool]:
    """(source chip text, is_warning). Uses the live clock when connected, else the saved config."""
    if clock is None:
        if cfg_source == "midi":
            return f"MIDI · {cfg_port or 'no port selected'} · not running", False
        if cfg_source == "audio":
            return f"Audio · {cfg_audio or 'no source chosen'} · not running", False
        return f"Internal · {cfg_bpm:.0f} BPM", False
    if clock["source"] == "internal":
        return f"Internal · {clock['bpm']:.0f} BPM", False
    if clock["source"] == "audio":
        return audio_clock_text(clock, audio or {})
    port = clock.get("port") or "MIDI"
    wired = (midi or {}).get("clock") or {}
    if midi and not wired.get("wanted"):
        return "MIDI · no port selected", True
    if midi and not wired.get("open"):
        return f"MIDI · {port} not found", True
    if not clock["locked"]:
        what = "beat notes" if clock.get("midi_mode") == "beats" else "clock"
        return f"MIDI · waiting for {what} (holding {clock['bpm']:.0f} BPM)", True
    return f"MIDI · {clock['bpm']:.1f} BPM · {port}", False  # tempo before the port: the chip truncates from the right


def audio_clock_text(clock: dict[str, Any], audio: dict[str, Any]) -> tuple[str, bool]:
    """The source chip while the audio clock drives the show."""
    if audio.get("error") and not audio.get("running"):
        return "Audio · not available", True
    sid = audio.get("source")
    if not sid:
        wanted = audio.get("wanted")
        return (f"Audio · {wanted} not found", True) if wanted else ("Audio · no source chosen", True)
    name = next((s["name"] for s in audio.get("sources") or [] if s["id"] == sid), sid)
    if not clock["locked"]:
        return f"Audio · listening for a beat (holding {clock['bpm']:.0f} BPM)", True
    return f"Audio · {clock['bpm']:.1f} BPM · {name}", False  # tempo first: the chip truncates from the right


def live_beat(clock: dict[str, Any], elapsed_s: float = 0.0) -> float:
    """The renderer's beat extrapolated ``elapsed_s`` past the state sample."""
    return clock["beat"] + elapsed_s * clock["bpm"] / 60.0


def beat_in_bar(clock: dict[str, Any], elapsed_s: float = 0.0) -> tuple[int, float]:
    """(0-based beat within the bar, phase 0..1) extrapolated ``elapsed_s`` past the state sample."""
    beat = live_beat(clock, elapsed_s)
    bpb = max(1, clock["beats_per_bar"])
    return int(beat // 1) % bpb, beat - int(beat // 1)


@dataclass(frozen=True)
class Banner:
    kind: str  # "renderer_outdated" | "midi_lost" | "audio_unavailable" | "scene_error" | "config_error" | "output_hidden"
    # | "calibrating"
    title: str
    text: str
    action: str | None  # button label; None = a plain notice
    scene_id: str | None = None


def banners(connected: bool, state: dict[str, Any] | None) -> list[Banner]:
    if not connected or state is None:
        return []
    out: list[Banner] = []
    if state.get("calibrating"):
        out.append(Banner("calibrating", "Calibration in progress", "Calibration in progress", "Show window"))
    if state.get("rev", 0) < REV:
        out.append(Banner("renderer_outdated", "Renderer is outdated",
                          "The renderer was started before the controller was updated, so newer features do not work. "
                          "Restart it to use the new version.", "Restart renderer"))
    output = state.get("output") or {}
    if output.get("presenting") is False:
        out.append(Banner("output_hidden", "Output window",
                          "The output window is hidden or covered, so the projector is not showing the show. "
                          "Switch to it (Alt+Tab) to bring it back.", None))
    if state.get("config_error"):
        out.append(Banner("config_error", "Configuration error",
                          f"config.yaml is invalid, so the last good settings are in use. {state['config_error']}", "Open in editor"))
    clock = state["clock"]
    if clock["source"] == "midi" and not clock["locked"]:
        out.append(Banner("midi_lost", "MIDI clock lost",
                          f"MIDI clock lost. Holding the last tempo ({clock['bpm']:.0f} BPM).", "Use internal clock"))
    audio = state.get("audio") or {}
    # A break without a beat is normal music, so a missing beat is only the chip's warning; a listener that cannot
    # run at all is worth a banner.
    if clock["source"] == "audio" and audio.get("error") and not audio.get("running"):
        out.append(Banner("audio_unavailable", "Audio not available",
                          f"Beat detection on audio is not running: {audio['error']}. Holding {clock['bpm']:.0f} BPM.",
                          "Use internal clock"))
    for sid, problems in state.get("scene_errors", {}).items():
        out.append(Banner("scene_error", "File error", f"{sid}.yaml · {problems[0]}", "Open in editor", scene_id=sid))
        break  # one banner at a time; the scenes dialog lists all of them
    return out


def hotkeys(scene_ids: list[str], keys: dict[str, str] | None = None) -> dict[str, str]:
    """Scene id -> key label. Explicit ``bindings.keys`` (key -> scene id) win; the remaining scenes
    get the free digits 1-9 and 0 in order."""
    result: dict[str, str] = {}
    for key, target in (keys or {}).items():
        if target in scene_ids and target not in result and key.isdigit():
            result[target] = key
    taken = set(result.values())
    free = iter(d for d in HOTKEY_DIGITS if d not in taken)
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


def all_tags(scenes: dict[str, Any]) -> list[str]:
    """Every tag any scene has, sorted, for the tag filter."""
    return sorted({t for sc in scenes.values() for t in sc.tags}, key=str.lower)


def toggle_tag(selected: frozenset[str], tag: str | None) -> frozenset[str]:
    """The tag filter after a click. ``tag`` None is "All", which clears the selection; an empty
    selection is "All" too, so there is no way to select nothing."""
    if tag is None:
        return frozenset()
    return selected - {tag} if tag in selected else selected | {tag}


def filter_scenes(scenes: dict[str, Any], ids: list[str], tags: frozenset[str], only_accented: bool = False) -> list[str]:
    """The ids the grid shows: scenes with any of ``tags`` (all of them when there are none), and
    with ``only_accented`` only scenes that read the accent colour."""
    out = []
    for sid in ids:
        sc = scenes[sid]
        if tags and not tags.intersection(sc.tags):
            continue
        if only_accented and not sc.uses_accent:
            continue
        out.append(sid)
    return out


def rgb_to_hex(rgb: Any) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, float(c))) * 255):02x}" for c in rgb)


# The accent colours on offer: every mix of red, green and blue at 0, half and full, black excepted. In
# rows of ACCENT_COLUMNS, hue aligned by column: the full hues, the hues halfway between them, pale, dim,
# white. They are lights, so the names say hue, paleness and intensity, not paint ("dim", never "dark").
ACCENT_COLUMNS = 6
ACCENT_COLORS = [
    ("Red", "#ff0000"), ("Yellow", "#ffff00"), ("Green", "#00ff00"),
    ("Cyan", "#00ffff"), ("Blue", "#0000ff"), ("Magenta", "#ff00ff"),
    ("Orange", "#ff7f00"), ("Yellow-green", "#7fff00"), ("Green-cyan", "#00ff7f"),
    ("Cyan-blue", "#007fff"), ("Violet", "#7f00ff"), ("Magenta-red", "#ff007f"),
    ("Pale red", "#ff7f7f"), ("Pale yellow", "#ffff7f"), ("Pale green", "#7fff7f"),
    ("Pale cyan", "#7fffff"), ("Pale blue", "#7f7fff"), ("Pale magenta", "#ff7fff"),
    ("Dim red", "#7f0000"), ("Dim yellow", "#7f7f00"), ("Dim green", "#007f00"),
    ("Dim cyan", "#007f7f"), ("Dim blue", "#00007f"), ("Dim magenta", "#7f007f"),
    ("White", "#ffffff"), ("Dim white", "#7f7f7f"),
]


def accent_name(color: str) -> str:
    """The name of ``color`` ("#rrggbb") among ACCENT_COLORS, or the hex itself for any other colour."""
    return next((n for n, c in ACCENT_COLORS if c == color.lower()), color.lower())


def display_name(scene_id: str) -> str:
    return scene_id.replace("-", " ").replace("_", " ").title()


PANIC_ESCAPES = 4  # Escape presses that black the show out
PANIC_WINDOW_S = 2.0  # ... when they all land within this long


def panic_press(times: list[float], now: float) -> bool:
    """Record an Escape press in ``times`` and say whether the panic blackout fires. Escape alone does
    nothing, so a stray press cannot kill the show; four in a row is unmistakably deliberate."""
    times.append(now)
    del times[: max(0, len(times) - PANIC_ESCAPES)]
    if len(times) == PANIC_ESCAPES and now - times[0] <= PANIC_WINDOW_S:
        times.clear()
        return True
    return False


def key_to_action(name: str, ctrl: bool = False, shift: bool = False) -> tuple[str, Any] | None:
    """Keyboard shortcut (from the design's shortcut sheet) -> (action, argument), or None.
    ``name`` is the GDK key name, e.g. "space", "bracketleft", "1"."""
    if ctrl:
        return {"space": ("toggle-run", None), "Up": ("brightness", +5), "Down": ("brightness", -5), "comma": ("preferences", None),
                "b": ("blackout", None), "B": ("blackout", None),
                "q": ("quit", None), "Q": ("quit", None),
                "question": ("shortcuts", None), "slash": ("shortcuts", None) if shift else None}.get(name)
    if name in ("t", "T"):
        return ("tap", None)
    if name in ("d", "D"):
        return ("downbeat", None)
    if name in ("m", "M"):
        return ("mode", "manual")
    if name in ("r", "R"):
        return ("mode", "random")
    if name in ("s", "S"):
        return ("mode", "sequence")
    if name in ("c", "C"):
        return ("clock-source", None)
    if name in ("h", "H"):
        return ("simulation", None)
    if name == "bracketleft":
        return ("step", -1)
    if name == "bracketright":
        return ("step", +1)
    if len(name) == 1 and name in HOTKEY_DIGITS:
        return ("scene-key", name)
    return None
