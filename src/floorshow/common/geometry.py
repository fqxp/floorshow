"""Calibration geometry: where the ball sits in the projector image, and how a pixel maps to a
direction on the ball.

Coordinates. Window/image coordinates ``u, v`` are normalised to [0, 1], ``u`` left to right and
``v`` top to bottom. Ball directions are unit vectors with ``+x`` right, ``+y`` up and ``+z``
toward the projector. ``lat`` +90 is the north pole.

The silhouette (four lines) gives an ellipse; a pixel inside it is the hit point of an
orthographic ray on the sphere. Roll and tilt (phase 2) rotate that image-frame direction into
the ball's own frame:  ``d_img = Rz(roll) . Rx(tilt) . d_ball``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

Vec3 = tuple[float, float, float]
Side = Literal["left", "right", "top", "bottom"]

MIN_SIZE = 0.02  # smallest allowed distance between opposite lines (fraction of width/height)
DEFAULT_BALL_HEIGHT = 0.70  # default ball height as a fraction of the screen height
ASPECT_WARN = 0.05


@dataclass(frozen=True)
class Calibration:
    left: float
    right: float
    top: float
    bottom: float
    roll_deg: float = 0.0
    tilt_deg: float = 0.0

    # --- construction --------------------------------------------------------------------

    @staticmethod
    def default(aspect: float = 16 / 9) -> Calibration:
        """Centred, round, 70% of the screen height, for a window of ``aspect`` = width / height."""
        ry = DEFAULT_BALL_HEIGHT / 2
        rx = ry / aspect
        return Calibration(left=0.5 - rx, right=0.5 + rx, top=0.5 - ry, bottom=0.5 + ry)

    # --- derived -------------------------------------------------------------------------

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)

    @property
    def radii(self) -> tuple[float, float]:
        """Semi-axes as fractions of width and height."""
        return ((self.right - self.left) / 2, (self.bottom - self.top) / 2)

    def for_window(self, width: float, height: float, ref_width: float, ref_height: float) -> Calibration:
        """The same ball in a window of a different size to the one it was measured in.

        The lines are kept as fractions, which only means anything alongside the window they are fractions of:
        shown in a window of another shape they stretch, and the ball comes out an ellipse. So a ball keeps the
        size in pixels it was measured at -- it is a real object hanging in a real room, and resizing a window
        does not reshape it -- and only its centre follows the window."""
        cx, cy = self.center
        rx = self.radii[0] * ref_width / width
        ry = self.radii[1] * ref_height / height
        return replace(self, left=cx - rx, right=cx + rx, top=cy - ry, bottom=cy + ry)

    def is_valid(self) -> bool:
        eps = 1e-9  # the clamps in shift()/grow() land exactly on MIN_SIZE up to float rounding
        return self.right - self.left >= MIN_SIZE - eps and self.bottom - self.top >= MIN_SIZE - eps

    def aspect_deviation(self, width_px: float, height_px: float) -> float:
        """Relative difference between the ball's pixel width and height (0 = perfectly round)."""
        rx, ry = self.radii
        return (rx * width_px) / (ry * height_px) - 1.0

    def aspect_warning(self, width_px: float, height_px: float) -> bool:
        return abs(self.aspect_deviation(width_px, height_px)) > ASPECT_WARN

    # --- editing (used by the calibration window) ----------------------------------------

    def shift(self, side: Side, delta: float) -> Calibration:
        """Move one line by ``delta`` (fraction of width for left/right, of height for top/bottom).
        A line can never cross its opposite."""
        value = getattr(self, side) + delta
        if side == "left":
            value = min(value, self.right - MIN_SIZE)
        elif side == "right":
            value = max(value, self.left + MIN_SIZE)
        elif side == "top":
            value = min(value, self.bottom - MIN_SIZE)
        else:
            value = max(value, self.top + MIN_SIZE)
        return replace(self, **{side: value})

    def move(self, dx: float = 0.0, dy: float = 0.0) -> Calibration:
        """Move the whole ball: both lines of an axis together, size unchanged."""
        return replace(
            self, left=self.left + dx, right=self.right + dx, top=self.top + dy, bottom=self.bottom + dy
        )

    def grow(self, dx: float = 0.0, dy: float = 0.0) -> Calibration:
        """Grow (or shrink, if negative) symmetrically around the centre, by ``dx`` / ``dy`` per side."""
        dx = max(dx, -(self.right - self.left - MIN_SIZE) / 2)
        dy = max(dy, -(self.bottom - self.top - MIN_SIZE) / 2)
        return replace(
            self, left=self.left - dx, right=self.right + dx, top=self.top - dy, bottom=self.bottom + dy
        )

    # --- mapping -------------------------------------------------------------------------

    def pixel_to_image_dir(self, u: float, v: float) -> Vec3 | None:
        """Direction of the sphere point seen at (u, v), or None outside the silhouette."""
        cx, cy = self.center
        rx, ry = self.radii
        qx, qy = (u - cx) / rx, (cy - v) / ry
        r2 = qx * qx + qy * qy
        if r2 > 1.0:
            return None
        return (qx, qy, math.sqrt(1.0 - r2))

    def pixel_to_ball_dir(self, u: float, v: float) -> Vec3 | None:
        d = self.pixel_to_image_dir(u, v)
        return None if d is None else image_to_ball(d, self.roll_deg, self.tilt_deg)


# --- roll / tilt (phase 2) ---------------------------------------------------------------


def _rx(d: Vec3, a: float) -> Vec3:
    x, y, z = d
    c, s = math.cos(a), math.sin(a)
    return (x, y * c - z * s, y * s + z * c)


def _rz(d: Vec3, a: float) -> Vec3:
    x, y, z = d
    c, s = math.cos(a), math.sin(a)
    return (x * c - y * s, x * s + y * c, z)


def ball_to_image(d: Vec3, roll_deg: float, tilt_deg: float) -> Vec3:
    """d_img = Rz(roll) . Rx(tilt) . d_ball"""
    return _rz(_rx(d, math.radians(tilt_deg)), math.radians(roll_deg))


def image_to_ball(d: Vec3, roll_deg: float, tilt_deg: float) -> Vec3:
    """d_ball = Rx(-tilt) . Rz(-roll) . d_img"""
    return _rx(_rz(d, -math.radians(roll_deg)), -math.radians(tilt_deg))


def pole_marker(roll_deg: float, tilt_deg: float) -> tuple[float, float]:
    """Image position of the north pole, in units of the ball radius from its centre
    (x right, y up): ``(-sin(roll) cos(tilt), cos(roll) cos(tilt))``."""
    r, t = math.radians(roll_deg), math.radians(tilt_deg)
    return (-math.sin(r) * math.cos(t), math.cos(r) * math.cos(t))


def roll_tilt_from_pole(px: float, py: float, projector_above: bool = True) -> tuple[float, float]:
    """Inverse of :func:`pole_marker`. One marker position gives both angles.

    ``projector_above`` (the pole faces the projector) selects the sign of the tilt; the marker
    alone cannot tell, since the pole is hidden when the projector is below the equator."""
    mag = min(1.0, math.hypot(px, py))
    tilt = math.degrees(math.acos(mag))
    roll = math.degrees(math.atan2(-px, py)) if mag > 1e-9 else 0.0
    return roll, (tilt if projector_above else -tilt)


def lat_lon(d: Vec3) -> tuple[float, float]:
    """(latitude, longitude) in degrees of a ball-frame direction."""
    x, y, z = d
    return math.degrees(math.asin(max(-1.0, min(1.0, y)))), math.degrees(math.atan2(x, z))
