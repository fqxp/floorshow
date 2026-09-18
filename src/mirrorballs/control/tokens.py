"""Design tokens from the "Mirrorballs Control UI" design (Tokens board), dark primary, light same roles.
Rules from the design: red is the beat and nothing else; amber is warning/error; one teal accent;
never colour alone."""

from __future__ import annotations

from pathlib import Path

DARK = {
    "bg0": "#101114", "bg1": "#15171b", "bg2": "#1b1d22", "bg3": "#23262c", "bg4": "#2d3138",
    "well": "#0b0c0e", "line": "#2a2e35", "line2": "#3b404a",
    "t1": "#eceef1", "t2": "#b0b5bf", "t3": "#8b919c",
    "acc": "#3fbfae", "acc_bg": "#143630", "acc_line": "#2c8f82", "on_acc": "#04211d",
    "amber": "#f0b03c", "amber_bg": "#2b2210", "amber_line": "#6f5519", "amber_t": "#f6d58f",
    "beat": "#ff3b30",
}
LIGHT = {
    "bg0": "#eceef1", "bg1": "#f7f8fa", "bg2": "#ffffff", "bg3": "#eef0f3", "bg4": "#dde1e6",
    "well": "#dfe3e8", "line": "#d8dce2", "line2": "#b9bec8",
    "t1": "#181a1f", "t2": "#444a54", "t3": "#5b626e",
    "acc": "#0d7c70", "acc_bg": "#d8f0ec", "acc_line": "#0d7c70", "on_acc": "#ffffff",
    "amber": "#8a5600", "amber_bg": "#fff0cc", "amber_line": "#d6a237", "amber_t": "#5a3900",
    "beat": "#e5271d",
}

STYLE_FILE = Path(__file__).with_name("style.css")


def palette(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def css(dark: bool) -> str:
    """style.css preceded by @define-color lines for the chosen palette."""
    defs = "".join(f"@define-color {k} {v};\n" for k, v in palette(dark).items())
    return defs + STYLE_FILE.read_text()


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a: str, b: str, u: float) -> tuple[float, float, float]:
    """Colour between hex colours ``a`` and ``b`` (u=0 gives a), as an rgb tuple for Cairo."""
    ca, cb = hex_to_rgb(a), hex_to_rgb(b)
    return tuple(x + (y - x) * u for x, y in zip(ca, cb))  # type: ignore[return-value]


def beat_intensity(phase: float) -> float:
    """1 on the beat, fading to 0 by the next one (ease-out, so the flash reads sharply)."""
    return max(0.0, 1.0 - phase) ** 2
