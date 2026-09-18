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


def ball_luminance(pipe: GlPipeline, spec: FrameSpec, blending: bool) -> float:
    """Relative luminance (0..1) of the lit ball surface, from the scene targets *before* master
    brightness. The frame mean is divided by the ball's share of the frame, so a bright ball on a black
    projector image counts as bright, not diluted."""
    rx, ry = spec.cal.radii
    share = max(1e-3, min(1.0, math.pi * rx * ry))
    lum1 = _luma(pipe.mean_rgb(1))
    lum = lum1
    if blending:
        lum = (1 - spec.mix) * _luma(pipe.mean_rgb(0)) + spec.mix * lum1
    return min(1.0, lum / share)


def _luma(rgb: tuple[float, float, float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def draw(pipe: GlPipeline, spec: FrameSpec, dest, measure: bool = False) -> float | None:
    """Render ``spec`` into the framebuffer ``dest`` (a window's ``ctx.screen`` or an offscreen one).

    With ``measure`` it returns the ball's luminance for the flash guard (None when nothing was rendered)."""
    if spec.black and spec.overlay is None:
        dest.use()
        dest.clear(0.0, 0.0, 0.0, 1.0)
        return None
    blending = spec.mix < 1.0 and spec.transition != "cut"
    if blending:
        _render(pipe, 0, spec.outgoing, spec.cal)
    _render(pipe, 1, spec.incoming, spec.cal)
    lum = ball_luminance(pipe, spec, blending) if measure and spec.overlay is None else None
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
