"""Calibration step 1 (silhouette) as a pure model: four lines in normalised coordinates, edited in
projector pixels. No GTK, so the arithmetic and the key map are tested directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common.geometry import Calibration, Side

STEP_PX = {"fine": 0.25, "normal": 1.0, "coarse": 10.0}
STEP_KEYS = {"1": "fine", "2": "normal", "3": "coarse"}
LINE_KEYS: dict[str, tuple[Side, int]] = {
    "a": ("left", -1), "d": ("left", +1), "j": ("right", -1), "l": ("right", +1),
    "w": ("top", -1), "s": ("top", +1), "i": ("bottom", -1), "k": ("bottom", +1),
}
ARROWS = {"Left": (-1, 0), "Right": (+1, 0), "Up": (0, -1), "Down": (0, +1)}
DEFAULT_SIZE = (1920, 1080)  # until the renderer reports its window size


def key_to_action(name: str) -> tuple[str, Any] | None:
    """GDK key name -> (action, argument), following the design's shortcut sheet."""
    n = name.lower() if len(name) == 1 else name
    if n in LINE_KEYS:
        return ("line", LINE_KEYS[n])
    if n in ARROWS:
        return ("move", ARROWS[n])
    if n in ("plus", "equal", "KP_Add"):
        return ("grow", +1)
    if n in ("minus", "KP_Subtract"):
        return ("grow", -1)
    if n in STEP_KEYS:
        return ("step", STEP_KEYS[n])
    if n == "b":
        return ("blink", None)
    if n == "g":
        return ("grid", None)
    if n == "Escape":
        return ("cancel", None)
    if n in ("Return", "KP_Enter"):
        return ("finish", None)
    return None


@dataclass
class Session:
    initial: Calibration
    width: int = DEFAULT_SIZE[0]
    height: int = DEFAULT_SIZE[1]
    step: str = "normal"
    blink: bool = False
    grid: bool = False
    active: Side | None = None  # last-moved line, drawn brighter on the projector

    def __post_init__(self) -> None:
        self.cal = self.initial

    # --- editing -------------------------------------------------------------------------

    @property
    def _px(self) -> float:
        return STEP_PX[self.step]

    def nudge(self, side: Side, direction: int) -> None:
        span = self.width if side in ("left", "right") else self.height
        self.cal = self.cal.shift(side, direction * self._px / span)
        self.active = side

    def move(self, dx: int, dy: int) -> None:
        self.cal = self.cal.move(dx * self._px / self.width, dy * self._px / self.height)

    def grow(self, sign: int) -> None:
        self.cal = self.cal.grow(sign * self._px / self.width, sign * self._px / self.height)

    def reset(self, default: Calibration) -> None:
        self.cal, self.active = default, None

    def apply(self, action: str, arg: Any) -> bool:
        """Apply a keyboard/button action. Returns True if the calibration itself changed."""
        before = self.cal
        if action == "line":
            self.nudge(*arg)
        elif action == "move":
            self.move(*arg)
        elif action == "grow":
            self.grow(arg)
        elif action == "step":
            self.step = arg
        elif action == "blink":
            self.blink = not self.blink
        elif action == "grid":
            self.grid = not self.grid
        return self.cal != before

    # --- readouts ------------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return self.cal != self.initial

    @property
    def aspect(self) -> float:
        rx, ry = self.cal.radii
        return (rx * self.width) / (ry * self.height)

    @property
    def aspect_warning(self) -> bool:
        return self.cal.aspect_warning(self.width, self.height)

    def aspect_message(self) -> str:
        return f"Aspect off by {abs(self.aspect - 1) * 100:.1f} %. The ball should look round: re-check Left and Right."

    def centre_px(self) -> tuple[float, float]:
        cx, cy = self.cal.center
        return cx * self.width, cy * self.height

    def size_px(self) -> tuple[float, float]:
        rx, ry = self.cal.radii
        return 2 * rx * self.width, 2 * ry * self.height

    def line_px(self, side: Side) -> tuple[float, float]:
        """(pixels, percent) of a line along its own axis."""
        span = self.width if side in ("left", "right") else self.height
        v = getattr(self.cal, side)
        return v * span, v * 100

    def preview(self) -> dict[str, Any]:
        """Arguments for the renderer's ``calibration.preview`` command."""
        c = self.cal
        return dict(left=c.left, right=c.right, top=c.top, bottom=c.bottom, roll_deg=c.roll_deg, tilt_deg=c.tilt_deg,
                    blink=self.blink, grid=self.grid, active=self.active)


def fmt_px(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.005 else f"{v:.2f}".rstrip("0").rstrip(".")
