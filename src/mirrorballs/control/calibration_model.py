"""Calibration step 1 (silhouette) as a pure model: four lines in normalised coordinates, edited in
projector pixels. No GTK, so the arithmetic and the key map are tested directly."""

from __future__ import annotations

from dataclasses import dataclass, replace
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

# The wizard's pages, in order. Named, because every step-dependent branch would otherwise be a bare number.
STEP_BALL, STEP_SILHOUETTE, STEP_AXIS, STEP_ROWS, STEP_DONE = 1, 2, 3, 4, 5


def ball_key_to_action(name: str) -> tuple[str, Any] | None:
    """Step 1 has nothing to nudge, so the arrow keys are left to move the focus as usual."""
    return {"Escape": ("cancel", None), "Return": ("next", None), "KP_Enter": ("next", None)}.get(name)


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
        return ("next", None)
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

    def resize(self, width: int, height: int) -> bool:
        """The output window changed size. The ball is a real thing in a real room: it keeps the size in
        pixels it was calibrated to, and only its position follows the window. Returns True if anything moved."""
        if width <= 0 or height <= 0 or (width, height) == (self.width, self.height):
            return False
        cx, cy = self.cal.center
        rx = self.cal.radii[0] * self.width / width
        ry = self.cal.radii[1] * self.height / height
        self.cal = replace(self.cal, left=cx - rx, right=cx + rx, top=cy - ry, bottom=cy + ry)
        self.width, self.height = width, height
        return True

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


# --- step 2: ball axis (roll / tilt) ----------------------------------------------------------

import math  # noqa: E402

from ..common.geometry import pole_marker, roll_tilt_from_pole  # noqa: E402

AXIS_KEYS = {"Left": (-1, 0), "Right": (+1, 0), "Up": (0, -1), "Down": (0, +1)}


def axis_key_to_action(name: str) -> tuple[str, Any] | None:
    n = name.lower() if len(name) == 1 else name
    if n in AXIS_KEYS:
        return ("cross", AXIS_KEYS[n])
    if n in STEP_KEYS:
        return ("step", STEP_KEYS[n])
    return {"Escape": ("cancel", None), "Return": ("next", None), "KP_Enter": ("next", None), "BackSpace": ("back", None)}.get(n)


@dataclass
class AxisSession:
    """Where the ball's north pole (the hanger eyelet) appears in the projector image.

    One point gives both angles: the marker sits at ``(-sin(roll) cos(tilt), cos(roll) cos(tilt))`` in units of
    the ball radius. Whether the projector is above or below the equator is the sign of the tilt and cannot be
    seen from the marker, so it is a separate choice."""

    cal: Calibration
    width: int = DEFAULT_SIZE[0]
    height: int = DEFAULT_SIZE[1]
    step: str = "normal"
    above: bool = True

    def __post_init__(self) -> None:
        self.initial = (self.cal.roll_deg, self.cal.tilt_deg)
        self.above = self.cal.tilt_deg >= 0
        self.px, self.py = pole_marker(self.cal.roll_deg, abs(self.cal.tilt_deg))
        self.distance_m = 2.4
        self.height_cm = round(self.distance_m * 100 * math.tan(math.radians(abs(self.cal.tilt_deg))), 1)

    @property
    def _radii_px(self) -> tuple[float, float]:
        rx, ry = self.cal.radii
        return rx * self.width, ry * self.height

    def move(self, dx: int, dy: int) -> None:
        """Move the crosshair by one step in projector pixels (``dy`` grows downward, like the screen)."""
        rx, ry = self._radii_px
        px = self.px + dx * STEP_PX[self.step] / rx
        py = self.py - dy * STEP_PX[self.step] / ry
        r = math.hypot(px, py)
        if r > 1.0:  # the pole cannot lie outside the ball's silhouette
            px, py = px / r, py / r
        self.px, self.py = px, py
        self._sync_numbers()

    def set_above(self, above: bool) -> None:
        self.above = above

    def set_numbers(self, height_cm: float | None = None, distance_m: float | None = None) -> None:
        """Numeric fallback: the tilt is atan(height / distance). Moves the marker along its direction."""
        if height_cm is not None:
            self.height_cm = max(0.0, height_cm)
        if distance_m is not None:
            self.distance_m = max(0.3, distance_m)
        tilt = math.atan2(self.height_cm / 100.0, self.distance_m)
        r = math.hypot(self.px, self.py)
        ux, uy = (self.px / r, self.py / r) if r > 1e-9 else (0.0, 1.0)
        self.px, self.py = ux * math.cos(tilt), uy * math.cos(tilt)

    def _sync_numbers(self) -> None:
        tilt = math.acos(min(1.0, math.hypot(self.px, self.py)))
        self.height_cm = round(self.distance_m * 100 * math.tan(tilt), 1)

    def roll_tilt(self) -> tuple[float, float]:
        return roll_tilt_from_pole(self.px, self.py, self.above)

    @property
    def dirty(self) -> bool:
        r, t = self.roll_tilt()
        return abs(r - self.initial[0]) > 1e-6 or abs(t - self.initial[1]) > 1e-6

    def calibration(self) -> Calibration:
        roll, tilt = self.roll_tilt()
        from dataclasses import replace

        return replace(self.cal, roll_deg=roll, tilt_deg=tilt)

    def crosshair_norm(self) -> tuple[float, float]:
        """Marker position in the window's normalised coordinates (top-down), for the renderer's overlay."""
        cx, cy = self.cal.center
        rx, ry = self.cal.radii
        return cx + self.px * rx, cy - self.py * ry

    def texts(self) -> tuple[str, str]:
        roll, tilt = self.roll_tilt()
        side = "leans left" if roll > 0.05 else "leans right" if roll < -0.05 else "upright"
        where = "above" if tilt >= 0 else "below"
        return f"{abs(tilt):.0f}° · projector {where} the equator", f"{abs(roll):.0f}° · axis {side}"


# --- step 3: tile rows (pitch / offset) ----------------------------------------------------------

SHIFT_STEP = {"fine": 0.02, "normal": 0.1, "coarse": 0.25}  # fractions of a row per key press
PITCH_STEP = {"fine": 0.005, "normal": 0.02, "coarse": 0.1}
MIN_PITCH, MAX_PITCH = 1.0, 12.0
DEFAULT_PITCH = 4.0
ROWS_KEYS = {"Up": ("offset", +1), "Down": ("offset", -1), "Left": ("pitch", -1), "Right": ("pitch", +1)}
# The three colours the renderer paints the rows with (composite.frag holds the same values).
ROW_TINTS = {"a": (0.00, 0.85, 0.75), "b": (0.90, 0.00, 0.70), "equator": (1.00, 0.68, 0.20)}


def parity_shift(pitch: float) -> float:
    """Where a row boundary sits on a ball built symmetrically about its equator, in rows. An even number of
    rows puts a seam on the equator, an odd number puts a row's middle there, so the count decides the shift."""
    return 0.0 if round(180.0 / pitch) % 2 == 0 else -0.5


def wrap_rows(shift: float) -> float:
    """Shifting by a whole row gives back the same grid, so only the fraction means anything."""
    return ((shift + 0.5) % 1.0) - 0.5


def rows_key_to_action(name: str) -> tuple[str, Any] | None:
    n = name.lower() if len(name) == 1 else name
    if n in ROWS_KEYS:
        return ROWS_KEYS[n]
    if n in STEP_KEYS:
        return ("step", STEP_KEYS[n])
    if n == "z":
        return ("pattern", None)
    return {"Escape": ("cancel", None), "Return": ("next", None), "KP_Enter": ("next", None), "BackSpace": ("back", None)}.get(n)


def done_key_to_action(name: str) -> tuple[str, Any] | None:
    """Step 4 (confirmation) only navigates: there is nothing left to adjust."""
    return {"Escape": ("cancel", None), "Return": ("finish", None), "KP_Enter": ("finish", None),
            "BackSpace": ("back", None)}.get(name)


@dataclass
class RowsSession:
    """The tile-row grid: ``pitch`` (degrees of latitude per row) and ``offset`` (latitude of a row boundary).

    Judged by eye against the painted rows: a wrong offset puts every band half on a real row everywhere at
    once, while a wrong pitch lines up in one part of the ball and drifts out of line towards the poles."""

    pitch: float = DEFAULT_PITCH
    offset: float = 0.0  # degrees, the way the config keeps it; held here as a fraction of a row
    step: str = "normal"
    mode: str = "all"  # "all" | "equator"

    def __post_init__(self) -> None:
        # The shift is what is edited, in rows rather than degrees: a nudge then means the same amount of
        # ball wherever the pitch happens to be, and it survives a change of pitch unmoved.
        self.shift = wrap_rows(self.offset / self.pitch)
        self.offset_touched = False  # until then the shift follows from the row count
        self.initial = (self.pitch, self.shift)
        self._sync()

    def _sync(self) -> None:
        self.offset = self.shift * self.pitch

    def set_shift(self, shift: float) -> None:
        self.shift = wrap_rows(round(shift, 4))
        self._sync()

    def nudge_offset(self, sign: int) -> None:
        self.set_shift(self.shift + sign * SHIFT_STEP[self.step])
        self.offset_touched = True

    def reset_shift(self) -> None:
        """Back to a row boundary on the equator, and stay there: an explicit choice, not a seed."""
        self.set_shift(0.0)
        self.offset_touched = True

    def seed_pitch(self, value: float) -> None:
        """Take a pitch worked out from the ball's size, and with it the shift that row count implies.
        Only until the shift has been nudged: after that it is the user's, and nothing moves it."""
        self.set_pitch(value)
        if not self.offset_touched:
            self.set_shift(parity_shift(self.pitch))

    def set_pitch(self, value: float) -> None:
        self.pitch = min(MAX_PITCH, max(MIN_PITCH, value))
        self._sync()

    def nudge_pitch(self, sign: int) -> None:
        self.set_pitch(self.pitch + sign * PITCH_STEP[self.step])

    def apply(self, action: str, arg: Any) -> None:
        if action == "offset":
            self.nudge_offset(arg)
        elif action == "pitch":
            self.nudge_pitch(arg)
        elif action == "step":
            self.step = arg
        elif action == "pattern":
            self.mode = "equator" if self.mode == "all" else "all"

    @property
    def rows(self) -> float:
        return 180.0 / self.pitch

    @property
    def dirty(self) -> bool:
        return abs(self.pitch - self.initial[0]) > 1e-9 or abs(self.shift - self.initial[1]) > 1e-9

    def text(self) -> str:
        return f"≈ {self.rows:.0f} rows · {self.pitch:.2f}° each"

    def shift_text(self) -> str:
        """The shift as what it does: how far the whole grid sits off a row boundary at the equator."""
        return f"{self.shift:+.2f} rows"

    def preview(self) -> dict[str, Any]:
        return {"mode": self.mode, "pitch_deg": round(self.pitch, 5), "offset_deg": round(self.offset, 5)}
