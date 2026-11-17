"""Pure helpers behind the preferences dialog and the calibration wizard (no GTK)."""

from __future__ import annotations

import math
from typing import Any

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    """MIDI note number as a name, middle C (60) = C4."""
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def trigger_label(on: dict[str, Any]) -> str:
    if on.get("note") is not None:
        text = f"Note {note_name(on['note'])} ({on['note']})"
    elif on.get("cc") is not None:
        text = f"CC {on['cc']}"
    elif on.get("program") is not None:
        text = f"Program {on['program']}"
    else:
        return "Not set"
    return text + (f" · ch {on['ch']}" if on.get("ch") else "")


def trigger_from_learned(on: dict[str, Any]) -> dict[str, Any]:
    """A ``midi_learned`` event payload as a config trigger."""
    return {k: v for k, v in on.items() if k in ("note", "cc", "program", "ch") and v is not None}


def action_options(scene_ids: list[str], names: dict[str, str]) -> list[tuple[Any, str]]:
    """(config value, label) choices for a mapping's action, in the order the dropdown shows them."""
    out: list[tuple[Any, str]] = [({"scene": sid}, f"Scene {i} · {names.get(sid, sid)}") for i, sid in enumerate(scene_ids, 1)]
    out += [("next", "Next scene"), ("master", "Master brightness"), ("blackout", "Blackout")]
    return out


def normalise_action(do: Any, scene_ids: list[str]) -> Any:
    """``{scene_index: n}`` (0-based, as the renderer accepts it) becomes ``{scene: id}``; others pass through."""
    if isinstance(do, dict) and "scene_index" in do:
        i = int(do["scene_index"])
        return {"scene": scene_ids[i]} if 0 <= i < len(scene_ids) else do
    return do


def action_index(do: Any, options: list[tuple[Any, str]]) -> int:
    for i, (value, _) in enumerate(options):
        if value == do:
            return i
    return -1


def rows_from_tile(diameter_cm: float, tile_mm: float) -> float:
    """Rows pole to pole for a ball of ``diameter_cm`` covered in ``tile_mm`` tiles (half circumference / tile)."""
    return math.pi * diameter_cm * 10 / (2 * tile_mm)


def pitch_from_rows(rows: float) -> float:
    """Latitude pitch in degrees of one tile row."""
    return 180.0 / rows
