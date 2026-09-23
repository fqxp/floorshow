"""moderngl pipeline: scene pass(es) into offscreen targets, then two full-screen passes out.

Pass 1 (``scene.frag``) evaluates every shape per pixel on the GPU, using the calibrated
silhouette ellipse to find the ball direction under each pixel. Pass 2 (``blend.frag``) puts the
two scene targets through the transition and keeps the trail: it reads what it wrote last frame,
dimmed. Pass 3 (``composite.frag``) applies master brightness and draws the calibration overlay.

The split matters: master brightness and the overlay come *after* the trail, so a blackout is
instant rather than fading out through the trail, and calibration lines never smear.
Python only uploads a few hundred floats per frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import moderngl
import numpy as np

from ..common.geometry import Calibration, image_to_ball
from ..common.scene import MAX_SHAPES, ShapeParams

SHADERS = Path(__file__).parent / "shaders"
TEX_SIZE = 512  # every texture layer is resized to this square
MAX_TEXTURES = 8
BLEND_ID = {"over": 0, "add": 1, "multiply": 2, "screen": 3}
TRANSITION_ID = {"cut": 0, "crossfade": 1, "wipe_lat": 2, "iris": 3, "flash": 4}
FLOATS_PER_SHAPE = 20  # 5 vec4
ZEBRA_LIMIT_DEG = 60.0  # the zebra covers +-60 degrees of latitude


@dataclass
class Overlay:
    """Calibration overlay drawn by the composite pass."""

    lines: tuple[float, float, float, float] | None = None  # L, R, T, B (fractions, top-down)
    levels: tuple[float, float, float, float] = (0.6, 0.6, 0.6, 0.6)
    grid: bool = False
    crosshair: tuple[float, float] | None = None  # window fractions, top-down
    rows: tuple[str, float, float] | None = None  # (mode "zebra" | "single", pitch deg, offset deg)
    spin_deg: float = 0.0  # turns the grid's meridians about the pole axis; latitudes are unaffected by a spin


def rotation_matrix(axis: str, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype="f4")
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype="f4")
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype="f4")


def image_to_ball_matrix(cal: Calibration) -> np.ndarray:
    """3x3 matrix M with d_ball = M @ d_img, built from geometry.image_to_ball so both agree."""
    cols = [image_to_ball(e, cal.roll_deg, cal.tilt_deg) for e in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
    return np.array(cols, dtype="f4").T


def _mat3_bytes(m: np.ndarray) -> bytes:
    return np.asarray(m, dtype="f4").tobytes(order="F")  # GLSL matrices are column-major


def pack_shapes(shapes: list[ShapeParams]) -> tuple[bytes, int]:
    n = min(len(shapes), MAX_SHAPES)
    data = np.zeros((MAX_SHAPES, FLOATS_PER_SHAPE), dtype="f4")
    for i, s in enumerate(shapes[:n]):
        data[i] = (
            *s.center, s.inner,
            s.outer, s.softness, s.alpha, BLEND_ID[s.blend],
            *s.color, 1.0 if s.world_frame else 0.0,
            float(s.texture), 0.0 if s.tex_mapping == "shape" else 1.0, s.tex_scroll, 0.0,
            s.tex_scale[0], s.tex_scale[1], 0.0, 0.0,
        )
    return data.tobytes(), n


def _source(name: str, with_common: bool = True) -> str:
    body = (SHADERS / name).read_text()
    common = (SHADERS / "common.glsl").read_text() if with_common else ""
    return "#version 330\n" + common + "\n" + body


class GlPipeline:
    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        vert = (SHADERS / "quad.vert").read_text()
        self.scene_prog = ctx.program(vertex_shader=vert, fragment_shader=_source("scene.frag"))
        self.blend_prog = ctx.program(vertex_shader=vert, fragment_shader=_source("blend.frag"))
        self.comp_prog = ctx.program(vertex_shader=vert, fragment_shader=_source("composite.frag"))
        quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self.scene_vao = ctx.vertex_array(self.scene_prog, [(quad, "2f", "in_position")])
        self.blend_vao = ctx.vertex_array(self.blend_prog, [(quad, "2f", "in_position")])
        self.comp_vao = ctx.vertex_array(self.comp_prog, [(quad, "2f", "in_position")])

        self.ubo = ctx.buffer(reserve=MAX_SHAPES * FLOATS_PER_SHAPE * 4)
        self.scene_prog["Shapes"].binding = 0
        self.ubo.bind_to_uniform_block(0)

        self._dummy_tex = ctx.texture_array((1, 1, 1), 3, bytes(3))
        self._tex_cache: dict[tuple, moderngl.TextureArray] = {}
        self.size = (0, 0)
        self.targets: list[tuple[moderngl.Texture, moderngl.Framebuffer]] = []
        # Trail history, ping-ponged: blend.frag reads one and writes the other. Half floats, because
        # repeatedly multiplying an 8-bit value by the decay rounds back to itself at low brightness
        # and leaves a ghost burnt in that never reaches black.
        self.history: list[tuple[moderngl.Texture, moderngl.Framebuffer]] = []
        self._hist = 0

    # --- resources -----------------------------------------------------------------------

    def set_size(self, width: int, height: int) -> None:
        if (width, height) == self.size or width <= 0 or height <= 0:
            return
        for tex, fbo in (*self.targets, *self.history):
            fbo.release()
            tex.release()
        self.targets, self.history = [], []
        for group, dtype in ((self.targets, "f1"), (self.history, "f2")):
            for _ in range(2):
                tex = self.ctx.texture((width, height), 4, dtype=dtype)
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                tex.repeat_x = tex.repeat_y = False
                group.append((tex, self.ctx.framebuffer(color_attachments=[tex])))
        self.size = (width, height)
        self.clear_history()

    def textures_for(self, paths: list[str]) -> moderngl.TextureArray | None:
        """Texture array for a scene's assets (cached by path and mtime), or None if it has none."""
        if not paths:
            return None
        from PIL import Image

        key = tuple((p, Path(p).stat().st_mtime_ns) for p in paths[:MAX_TEXTURES])
        if key not in self._tex_cache:
            layers = []
            for p in paths[:MAX_TEXTURES]:
                with Image.open(p) as im:
                    layers.append(np.asarray(im.convert("RGB").resize((TEX_SIZE, TEX_SIZE), Image.Resampling.LANCZOS)))
            arr = self.ctx.texture_array((TEX_SIZE, TEX_SIZE, len(layers)), 3, np.stack(layers).tobytes())
            arr.build_mipmaps()
            arr.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            arr.repeat_x = arr.repeat_y = True
            if len(self._tex_cache) >= 16:  # bounded; scenes are few
                self._tex_cache.pop(next(iter(self._tex_cache))).release()
            self._tex_cache[key] = arr
        return self._tex_cache[key]

    # --- passes --------------------------------------------------------------------------

    def _set_calibration(self, prog: moderngl.Program, cal: Calibration) -> None:
        w, h = self.size
        cx, cy = cal.center
        rx, ry = cal.radii
        prog["u_resolution"].value = (float(w), float(h))
        prog["u_cal"].value = (cx, 1.0 - cy, rx, ry)  # y up, like gl_FragCoord
        prog["u_imgToBall"].write(_mat3_bytes(image_to_ball_matrix(cal)))

    def render_scene(
        self,
        slot: int,
        shapes: list[ShapeParams],
        cal: Calibration,
        spin: tuple[str, float] | None = None,
        textures: moderngl.TextureArray | None = None,
    ) -> None:
        """Render a scene into target ``slot`` (0 or 1). ``shapes=[]`` clears it to black."""
        _, fbo = self.targets[slot]
        fbo.use()
        self.ctx.viewport = (0, 0, *self.size)
        data, n = pack_shapes(shapes)
        self.ubo.write(data)
        p = self.scene_prog
        p["u_numShapes"].value = n
        self._set_calibration(p, cal)
        # Rotating the scene by +theta means looking up each pixel at -theta.
        spin_m = rotation_matrix(spin[0], -spin[1]) if spin else np.eye(3, dtype="f4")
        p["u_spin"].write(_mat3_bytes(spin_m))
        (textures or self._dummy_tex).use(location=0)
        p["u_tex"].value = 0
        fbo.clear(0.0, 0.0, 0.0, 1.0)
        self.scene_vao.render(moderngl.TRIANGLE_STRIP)

    def clear_history(self) -> None:
        """Forget the trail. Used whenever nothing is drawn at all, so a blackout cannot fade the
        old picture back in when the show restarts."""
        for _, fbo in self.history:
            fbo.use()
            fbo.clear(0.0, 0.0, 0.0, 1.0)

    def blend(
        self,
        cal: Calibration,
        *,
        mix: float = 1.0,
        transition: str = "cut",
        decay: float = 0.0,
    ) -> None:
        """Put the two scene targets through the transition into the history buffer, keeping
        ``decay`` of what was there last frame. ``decay`` 0 overwrites it, which is the no-trail case."""
        src, dst = self._hist, 1 - self._hist
        self.history[dst][1].use()
        w, h = self.size
        self.ctx.viewport = (0, 0, w, h)
        p = self.blend_prog
        self._set_calibration(p, cal)
        self.targets[0][0].use(location=0)
        self.targets[1][0].use(location=1)
        self.history[src][0].use(location=2)
        p["u_a"].value = 0
        p["u_b"].value = 1
        p["u_hist"].value = 2
        p["u_mix"].value = mix
        p["u_trans"].value = TRANSITION_ID[transition]
        p["u_decay"].value = decay
        self.blend_vao.render(moderngl.TRIANGLE_STRIP)
        self._hist = dst

    def composite(
        self,
        dest: moderngl.Framebuffer,
        cal: Calibration,
        *,
        master: float = 1.0,
        overlay: Overlay | None = None,
    ) -> None:
        """Present the blended image, with master brightness and the calibration overlay on top."""
        dest.use()
        w, h = self.size
        self.ctx.viewport = (0, 0, w, h)
        p = self.comp_prog
        self._set_calibration(p, cal)
        self.history[self._hist][0].use(location=0)
        p["u_image"].value = 0
        p["u_master"].value = master
        ov = overlay or Overlay()
        p["u_lines"].value = ov.lines if ov.lines else (-1.0, 0.0, 0.0, 0.0)
        p["u_lineLevels"].value = ov.levels
        p["u_grid"].value = 1 if ov.grid else 0
        p["u_gridSpin"].value = ov.spin_deg
        p["u_cross"].value = ov.crosshair if ov.crosshair else (-1.0, 0.0)
        mode = {"zebra": 1.0, "single": 2.0}[ov.rows[0]] if ov.rows else 0.0
        p["u_rows"].value = (mode, ov.rows[1], ov.rows[2], ZEBRA_LIMIT_DEG) if ov.rows else (0.0, 4.0, 0.0, ZEBRA_LIMIT_DEG)
        self.comp_vao.render(moderngl.TRIANGLE_STRIP)

    def read_rgb(self, fbo: moderngl.Framebuffer) -> np.ndarray:
        """Pixels of ``fbo`` as an (H, W, 3) uint8 array, top row first (for tests)."""
        w, h = fbo.size
        raw = fbo.read(components=3)
        return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)[::-1]
