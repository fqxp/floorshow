"""What to draw this frame (decided by Core, GL-free) and how to draw it (GlPipeline)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..common.geometry import Calibration
from ..common.scene import ShapeParams
from .gl import GlPipeline, Overlay


@dataclass
class SlotSpec:
    shapes: list[ShapeParams]
    spin: tuple[str, float] | None = None
    asset_paths: list[str] = field(default_factory=list)


@dataclass
class FrameSpec:
    cal: Calibration
    incoming: SlotSpec | None  # the current scene (None = black, e.g. during calibration)
    outgoing: SlotSpec | None = None  # only while a transition runs
    mix: float = 1.0  # transition progress; 1 = only `incoming`
    transition: str = "cut"
    master: float = 1.0
    overlay: Overlay | None = None
    black: bool = False  # fully blacked out: nothing needs rendering


def ball_luminance(pipe: GlPipeline, spec: FrameSpec, blending: bool, deferred: bool = False) -> float | None:
    """Relative luminance (0..1) of the lit ball surface, from the scene targets *before* master
    brightness. The frame mean is divided by the ball's share of the frame, so a bright ball on a black
    projector image counts as bright, not diluted."""
    rx, ry = spec.cal.radii
    share = max(1e-3, min(1.0, math.pi * rx * ry))
    rgb1 = pipe.mean_rgb(1, deferred)
    if blending:
        rgb0 = pipe.mean_rgb(0, deferred)
    else:
        pipe.forget_deferred(0)
        rgb0 = None
    if rgb1 is None or (blending and rgb0 is None):
        return None  # deferred: the first sample is still on its way
    lum = _luma(rgb1)
    if blending:
        lum = (1 - spec.mix) * _luma(rgb0) + spec.mix * lum
    return min(1.0, lum / share)


def _luma(rgb: tuple[float, float, float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def draw(pipe: GlPipeline, spec: FrameSpec, dest, measure: bool = False, deferred: bool = False) -> float | None:
    """Render ``spec`` into the framebuffer ``dest`` (a window's ``ctx.screen`` or an offscreen one).

    With ``measure`` it returns the ball's luminance for the flash guard (None when nothing was rendered).
    With ``deferred`` that is the luminance of the previously measured frame, which avoids a GPU stall."""
    if spec.black and spec.overlay is None:
        dest.use()
        dest.clear(0.0, 0.0, 0.0, 1.0)
        pipe.forget_deferred(0)
        pipe.forget_deferred(1)
        return None
    if spec.overlay is not None:
        pipe.forget_deferred(0)
        pipe.forget_deferred(1)
    blending = spec.mix < 1.0 and spec.transition != "cut"
    if blending:
        _render(pipe, 0, spec.outgoing, spec.cal)
    _render(pipe, 1, spec.incoming, spec.cal)
    lum = ball_luminance(pipe, spec, blending, deferred) if measure and spec.overlay is None else None
    pipe.composite(
        dest, spec.cal, mix=spec.mix if blending else 1.0, transition=spec.transition,
        master=spec.master, overlay=spec.overlay,
    )
    return lum


def _render(pipe: GlPipeline, slot: int, s: SlotSpec | None, cal: Calibration) -> None:
    if s is None:
        pipe.render_scene(slot, [], cal)
        return
    pipe.render_scene(slot, s.shapes, cal, spin=s.spin, textures=pipe.textures_for(s.asset_paths))
