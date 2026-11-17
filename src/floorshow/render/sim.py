"""Haze simulation: what the mirror ball's light looks like in a hazy room, drawn in the output window.

GL-free, so the geometry is testable: the facet layout, the reflection, the room box and the camera.
The drawing is in ``gl.GlPipeline.simulate`` and ``shaders/sim_*.{vert,frag}``.

Frames. The room is the ball frame (``+y`` up: the ball hangs on its axis), centred on the ball, in metres.
The projector light is parallel, along ``-z`` of the image frame (as in ``common/geometry.py``), so the
projector lies along ``image_to_ball((0, 0, 1))`` and the calibrated tilt says how high it is.
A facet's normal is its tile centre; it reflects the parallel light into one beam, ``r = i - 2 (i.n) n``.
The ball turning (``ball.rotation_rpm``) moves the facets under the projector image, which stays put.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..common.geometry import Vec3, image_to_ball

DEFAULT_DIAMETER_CM = 30.0
BALL_HEIGHT_M = 2.5  # ball centre above the floor
ROOM_HALF_M = 5.0  # the room is 10 m x 10 m
CEILING_ABOVE_M = 1.0  # ceiling above the ball centre
CHAIN_RADIUS_M = 0.005  # the ball hangs from the ceiling on a chain along its axis
PROJECTOR_DISTANCE_M = 3.0  # only for where the projector's own beam starts
EYE_HEIGHT_M = 1.7
EYE_DISTANCE_M = 3.0  # default distance from the ball's axis
DEFAULT_YAW_FROM_PROJECTOR_DEG = 140.0  # stand across the room from the projector, a little to the side
FOV_Y_DEG = 60.0
MIN_DIST_M, MAX_DIST_M = 0.5, 4.8
MAX_PITCH_DEG = 85.0

BOX_MIN = (-ROOM_HALF_M, -BALL_HEIGHT_M, -ROOM_HALF_M)
BOX_MAX = (ROOM_HALF_M, CEILING_ABOVE_M, ROOM_HALF_M)


# --- facets -------------------------------------------------------------------------------


def facet_layout(pitch_deg: float, offset_deg: float = 0.0) -> np.ndarray:
    """Ball-frame unit normals (N, 3) of the tiles: rows of latitude ``pitch_deg`` apart, shifted by
    ``offset_deg``, each row holding as many tiles as fit around it (``round(360 cos(lat) / pitch)``)."""
    out = []
    k0 = math.ceil((-90.0 - offset_deg) / pitch_deg)
    k1 = math.floor((90.0 - offset_deg) / pitch_deg)
    for k in range(k0, k1 + 1):
        lat = offset_deg + k * pitch_deg
        if abs(lat) > 90.0 - pitch_deg / 2:
            continue  # the cap at the pole: no room for a row
        cl = math.cos(math.radians(lat))
        n = max(1, round(360.0 * cl / pitch_deg))
        lon = np.radians((np.arange(n) + 0.5) * 360.0 / n)
        sl = math.sin(math.radians(lat))
        out.append(np.stack([cl * np.sin(lon), np.full(n, sl), cl * np.cos(lon)], axis=1))
    return np.concatenate(out).astype("f4") if out else np.zeros((0, 3), dtype="f4")


def reflect(n_img: Vec3) -> Vec3:
    """Direction (image frame) of the light reflected by a facet with image-frame normal ``n_img``.
    The light comes from the projector, travelling along -z."""
    x, y, z = n_img
    return (2 * z * x, 2 * z * y, -1.0 + 2 * z * z)


def spin_angle(rpm: float, t: float) -> float:
    """Radians the ball has turned after ``t`` seconds at ``rpm`` (+ = to the right, facing the ball)."""
    return 2 * math.pi * rpm / 60.0 * t


def box_exit(start: Vec3, d: Vec3) -> tuple[float, int]:
    """Distance along unit ``d`` from ``start`` (inside the room) to the wall it hits, and which axis
    that wall is across (0 x, 1 y, 2 z)."""
    best, axis = math.inf, 0
    for i in range(3):
        if abs(d[i]) < 1e-9:
            continue
        t = ((BOX_MAX[i] if d[i] > 0 else BOX_MIN[i]) - start[i]) / d[i]
        if t < best:
            best, axis = t, i
    return best, axis


def projector_dir(roll_deg: float, tilt_deg: float) -> Vec3:
    """Unit vector from the ball towards the projector, in the room."""
    return image_to_ball((0.0, 0.0, 1.0), roll_deg, tilt_deg)


# --- camera -------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """Where the viewer stands: orbit angles about the ball centre and the distance from it."""

    yaw_deg: float
    pitch_deg: float
    dist: float

    @staticmethod
    def default(proj: Vec3) -> View:
        """On the dance floor across from the projector, at eye height, looking up at the ball."""
        yaw = math.degrees(math.atan2(proj[0], proj[2])) + DEFAULT_YAW_FROM_PROJECTOR_DEG
        dy = EYE_HEIGHT_M - BALL_HEIGHT_M
        return View(yaw, math.degrees(math.atan2(dy, EYE_DISTANCE_M)), math.hypot(dy, EYE_DISTANCE_M))

    def orbit(self, dyaw: float, dpitch: float) -> View:
        p = max(-MAX_PITCH_DEG, min(MAX_PITCH_DEG, self.pitch_deg + dpitch))
        return View((self.yaw_deg + dyaw) % 360.0, p, self.dist)

    def zoom(self, factor: float) -> View:
        return View(self.yaw_deg, self.pitch_deg, max(MIN_DIST_M, min(MAX_DIST_M, self.dist * factor)))

    def eye(self) -> Vec3:
        """Eye position, kept inside the room."""
        y, p = math.radians(self.yaw_deg), math.radians(self.pitch_deg)
        e = (self.dist * math.cos(p) * math.sin(y), self.dist * math.sin(p), self.dist * math.cos(p) * math.cos(y))
        margin = 0.05
        return tuple(max(lo + margin, min(hi - margin, v)) for v, lo, hi in zip(e, BOX_MIN, BOX_MAX))  # type: ignore[return-value]


def look_at(eye: Vec3, target: Vec3 = (0.0, 0.0, 0.0), up: Vec3 = (0.0, 1.0, 0.0)) -> np.ndarray:
    e, t = np.asarray(eye, "f8"), np.asarray(target, "f8")
    f = t - e
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-6:  # looking straight up or down
        s = np.cross(f, (0.0, 0.0, 1.0))
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ e
    return m


def perspective(fov_y_deg: float, aspect: float, near: float = 0.05, far: float = 50.0) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_y_deg) / 2)
    m = np.zeros((4, 4))
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def view_proj(view: View, aspect: float) -> np.ndarray:
    return perspective(FOV_Y_DEG, aspect) @ look_at(view.eye())


# --- per frame ----------------------------------------------------------------------------


@dataclass
class SimSpec:
    """What the simulation needs for one frame, besides the projector image and the calibration."""

    radius_m: float = DEFAULT_DIAMETER_CM / 200.0
    pitch_deg: float = 4.0
    offset_deg: float = 0.0
    spin_rad: float = 0.0
    haze: float = 0.5  # 0..1
    projector_beam: bool = True
    ball: bool = True
    room: bool = True
    view: View | None = None  # None: the default view (filled in by the output window's mouse)
