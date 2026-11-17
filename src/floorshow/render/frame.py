"""What to draw this frame (decided by Core, GL-free) and how to draw it (GlPipeline)."""

from __future__ import annotations

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
    trail_decay: float = 0.0  # fraction of last frame that survives; 0 = no trail (see Core.trail_decay)


def draw(pipe: GlPipeline, spec: FrameSpec, dest) -> None:
    """Render ``spec`` into the framebuffer ``dest`` (a window's ``ctx.screen`` or an offscreen one)."""
    if spec.black and spec.overlay is None:
        pipe.clear_history()  # nothing is drawn, so nothing may fade back in when the show restarts
        dest.use()
        dest.clear(0.0, 0.0, 0.0, 1.0)
        return
    blending = spec.mix < 1.0 and spec.transition != "cut"
    if blending:
        _render(pipe, 0, spec.outgoing, spec.cal)
    _render(pipe, 1, spec.incoming, spec.cal)
    pipe.blend(spec.cal, mix=spec.mix if blending else 1.0, transition=spec.transition, decay=spec.trail_decay)
    pipe.composite(dest, spec.cal, master=spec.master, overlay=spec.overlay)


def _render(pipe: GlPipeline, slot: int, s: SlotSpec | None, cal: Calibration) -> None:
    if s is None:
        pipe.render_scene(slot, [], cal)
        return
    pipe.render_scene(slot, s.shapes, cal, spin=s.spin, textures=pipe.textures_for(s.asset_paths))
