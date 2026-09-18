"""Pure helpers behind the configuration dialog (no GTK): labels, conversions, time formatting."""

from __future__ import annotations

import math
from datetime import datetime
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


def tile_from_rows(diameter_cm: float, rows: float) -> float:
    return math.pi * diameter_cm * 10 / (2 * rows)


def pitch_from_rows(rows: float) -> float:
    """Latitude pitch in degrees of one tile row."""
    return 180.0 / rows


def describe_time(iso: str | None, now: datetime | None = None) -> str:
    """"Today, 21:14" / "Yesterday, 09:02" / "12 Sep, 18:30" / "Never"."""
    if not iso:
        return "Never"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return "Never"
    now = now or datetime.now()
    days = (now.date() - t.date()).days
    if days == 0:
        return f"Today, {t:%H:%M}"
    if days == 1:
        return f"Yesterday, {t:%H:%M}"
    return f"{t.day} {t:%b}, {t:%H:%M}"


def calibration_readout(cal: dict[str, float] | None, width: int, height: int) -> tuple[str, str]:
    """(centre text, size text) of a saved calibration in pixels of the given output size."""
    if cal is None:
        return "Not calibrated", "default: 70 % of the screen height"
    cx, cy = (cal["left"] + cal["right"]) / 2 * width, (cal["top"] + cal["bottom"]) / 2 * height
    sw, sh = (cal["right"] - cal["left"]) * width, (cal["bottom"] - cal["top"]) * height
    return f"{cx:.0f}, {cy:.0f} px", f"{sw:.0f} × {sh:.0f} px"
