"""Direction-space shape primitives.

Shapes describe *where* light should appear on the ball, purely as a
function of direction from its center (and optionally time, for
animation). All of that lives here as plain Python/scalar math -- the
actual per-pixel evaluation happens in shaders/sphere.frag. Each frame,
render.py calls params_at(t) on every active shape and uploads the
handful of resulting floats as uniforms; nothing here ever touches a
pixel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Union

Vec3 = tuple[float, float, float]
Color = tuple[float, float, float, float]

ScalarFn = Callable[[float], float]
DirFn = Callable[[float], Vec3]

NORTH: Vec3 = (0.0, 1.0, 0.0)
SOUTH: Vec3 = (0.0, -1.0, 0.0)
FRONT: Vec3 = (0.0, 0.0, 1.0)  # faces the projector/audience: lat=0, lon=0


def latlon_to_dir(lat_deg: float, lon_deg: float) -> Vec3:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    return (
        math.cos(lat) * math.sin(lon),
        math.sin(lat),
        math.cos(lat) * math.cos(lon),
    )


def clamp01(u: float) -> float:
    return 0.0 if u < 0.0 else 1.0 if u > 1.0 else u


def constant(value):
    return lambda _t: value


def looped(fn, period: float):
    """Wrap a callable so its time argument wraps every `period` seconds."""
    return lambda t: fn(t % period)


def animate_scalar(a: float, b: float, t0: float, t1: float) -> ScalarFn:
    """Linear ramp from a to b between t0 and t1; held at either end outside that range."""

    def f(t: float) -> float:
        u = clamp01((t - t0) / (t1 - t0))
        return a + (b - a) * u

    return f


def _norm(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def slerp(a: Vec3, b: Vec3, u: float) -> Vec3:
    """Spherical linear interpolation between two directions (great-circle path)."""
    dot = max(-1.0, min(1.0, _dot(a, b)))
    if dot > 0.9995:
        lerped = (
            a[0] + (b[0] - a[0]) * u,
            a[1] + (b[1] - a[1]) * u,
            a[2] + (b[2] - a[2]) * u,
        )
        return _norm(lerped)
    theta = math.acos(dot) * u
    rel = _norm((b[0] - dot * a[0], b[1] - dot * a[1], b[2] - dot * a[2]))
    return (
        a[0] * math.cos(theta) + rel[0] * math.sin(theta),
        a[1] * math.cos(theta) + rel[1] * math.sin(theta),
        a[2] * math.cos(theta) + rel[2] * math.sin(theta),
    )


def animate_dir(a: Vec3, b: Vec3, t0: float, t1: float) -> DirFn:
    """A direction that slerps from a to b between t0 and t1 (e.g. a moving circle)."""

    def f(t: float) -> Vec3:
        u = clamp01((t - t0) / (t1 - t0))
        return slerp(a, b, u)

    return f


@dataclass
class Annulus:
    """The one shape primitive: inner_radius <= angular_distance(d, center) <= outer_radius.

    center/inner_radius/outer_radius may be a plain value or a callable
    of time (seconds) -- both are evaluated once per frame on the CPU.
    """

    center: Union[Vec3, DirFn]
    inner_radius: Union[float, ScalarFn]  # radians
    outer_radius: Union[float, ScalarFn]  # radians
    color: Color = (1.0, 1.0, 1.0, 1.0)

    def params_at(self, t: float):
        center = self.center(t) if callable(self.center) else self.center
        inner = (
            self.inner_radius(t) if callable(self.inner_radius) else self.inner_radius
        )
        outer = (
            self.outer_radius(t) if callable(self.outer_radius) else self.outer_radius
        )
        return center, (inner, outer), self.color


def circle(center: Vec3, radius_deg: float, color: Color = (1, 1, 1, 1)) -> Annulus:
    """Filled circular spot."""
    return Annulus(center, 0.0, math.radians(radius_deg), color)


def ring(
    center: Vec3, radius_deg: float, width_deg: float, color: Color = (1, 1, 1, 1)
) -> Annulus:
    """Non-filled circle (outline): a thin annulus at `radius_deg`."""
    r = math.radians(radius_deg)
    w = math.radians(width_deg) / 2
    return Annulus(center, r - w, r + w, color)


def latitude_band(
    lat_deg: float, width_deg: float, color: Color = (1, 1, 1, 1)
) -> Annulus:
    """East-west band at a given latitude -- a ring centered on the north pole."""
    colat = 90.0 - lat_deg
    return ring(NORTH, colat, width_deg, color)


def meridian_band(
    lon_deg: float, width_deg: float, color: Color = (1, 1, 1, 1)
) -> Annulus:
    """North-south band (pole to pole) at a given longitude -- a ring of
    radius 90 degrees centered on the equatorial point 90 degrees away."""
    pole = latlon_to_dir(0.0, lon_deg + 90.0)
    return ring(pole, 90.0, width_deg, color)


def sweep_band(
    pole: Vec3, width_deg: float, t0: float, t1: float, color: Color = (1, 1, 1, 1)
) -> Annulus:
    """A band of angular width `width_deg` centered on `pole` that sweeps
    from the pole to the antipode between t0 and t1 (e.g. north-to-south pole)."""
    w = math.radians(width_deg)
    inner = animate_scalar(0.0, math.pi - w, t0, t1)
    outer = animate_scalar(w, math.pi, t0, t1)
    return Annulus(pole, inner, outer, color)


def growing_circle(
    center: Vec3,
    t0: float,
    t1: float,
    max_radius_deg: float = 180.0,
    color: Color = (1, 1, 1, 1),
) -> Annulus:
    """A filled circle growing from a point up to (optionally) covering the whole ball."""
    outer = animate_scalar(0.0, math.radians(max_radius_deg), t0, t1)
    return Annulus(center, 0.0, outer, color)
