"""GL side of the haze simulation (see sim.py for the model). Four passes, after the projector image has been
drawn offscreen:

1. ``sim_facets.frag``: one texel per facet, the light it catches from the projector image and the direction
   it reflects it in.
2. ``sim_beams.vert/.frag``: one instance per facet, a ribbon from the facet to the wall facing the eye, plus
   (with the room shown) the spot it makes there; added up in a half-float target.
3. ``sim_fog.frag``: the projector's own beam, ray-marched at half resolution (the costly part; haze is soft).
4. ``sim_room.frag``: per pixel, the ball and the room, plus 2 and 3; tone-mapped into the window.
"""

from __future__ import annotations

import math

import moderngl
import numpy as np

from ..common.geometry import Calibration
from . import sim as S
from .gl import SHADERS, _mat3_bytes, _source, image_to_ball_matrix, rotation_matrix

FACET_COLS = 64  # facet textures are this wide, with as many rows as needed
# Brightness is in display units: a white projector pixel is 1. A beam's glow in haze is far fainter than the
# light it carries; the gain makes it look the way an eye used to a dark room sees it.
BEAM_GAIN = 400.0
# The projector's own beam carries all the light the facets split up, so at the same gain it swamps them. Turned
# down: the beams are what the simulation is for.
PROJECTOR_GAIN = 0.3 * BEAM_GAIN
# A beam spreads out on its way to the wall: a spot 4 m away gets about 4 % of the light its tile caught. A real
# projector puts thousands of lux on the ball, so the spots are still bright against a dim room; in display units
# (white pixel on the ball = 1) that needs a large gain.
SPOT_GAIN = 20.0
WALL_GAIN = 2.0  # the projector's light that misses the ball and lands on the wall directly, undiluted
AMBIENT = 0.08  # the room's own dim light: enough to see walls, floor and ceiling while walking round the ball
SIGMA = 0.075  # scattering per metre at haze 1
EXTINCTION = 0.024  # per metre at haze 1
ANISOTROPY = 0.6  # haze scatters forwards
DIVERGE = 0.01  # beam widening, m per m: the projector is not a point light at infinity


def _mat4_bytes(m: np.ndarray) -> bytes:
    return np.asarray(m, dtype="f4").tobytes(order="F")


def _set(prog: moderngl.Program, values: dict) -> None:
    for k, v in values.items():
        if k in prog:  # the compiler drops uniforms a shader does not use
            prog[k].value = v


class SimPipeline:
    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        vert = (SHADERS / "quad.vert").read_text()
        self.facet_prog = ctx.program(vertex_shader=vert, fragment_shader=_source("sim_facets.frag", with_common=False))
        self.beam_prog = ctx.program(vertex_shader=_source("sim_beams.vert", common="sim_common.glsl"),
                                     fragment_shader=_source("sim_beams.frag", common="sim_common.glsl"))
        self.room_prog = ctx.program(vertex_shader=vert, fragment_shader=_source("sim_room.frag", common="sim_common.glsl"))
        self.fog_prog = ctx.program(vertex_shader=vert, fragment_shader=_source("sim_fog.frag", common="sim_common.glsl"))
        quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        corners = ctx.buffer(np.array([0, -1, 1, -1, 0, 1, 1, 1], dtype="f4").tobytes())
        self.facet_vao = ctx.vertex_array(self.facet_prog, [(quad, "2f", "in_position")])
        self.room_vao = ctx.vertex_array(self.room_prog, [(quad, "2f", "in_position")])
        self.fog_vao = ctx.vertex_array(self.fog_prog, [(quad, "2f", "in_position")])
        self.beam_vao = ctx.vertex_array(self.beam_prog, [(corners, "2f", "in_corner")])
        self.layout_key: tuple[float, float] | None = None
        self.count = 0
        self.normals: moderngl.Texture | None = None
        self.facets: list[moderngl.Texture] = []
        self.facet_fbo: moderngl.Framebuffer | None = None
        self.size = (0, 0)
        self.proj: moderngl.Texture | None = None
        self.proj_fbo: moderngl.Framebuffer | None = None
        self.beams: moderngl.Texture | None = None
        self.beams_fbo: moderngl.Framebuffer | None = None
        self.fog: moderngl.Texture | None = None
        self.fog_fbo: moderngl.Framebuffer | None = None

    # --- resources -----------------------------------------------------------------------

    def set_layout(self, pitch: float, offset: float) -> None:
        if self.layout_key == (pitch, offset):
            return
        for o in (self.facet_fbo, self.normals, *self.facets):
            if o is not None:
                o.release()
        n = S.facet_layout(pitch, offset)
        rows = max(1, -(-len(n) // FACET_COLS))
        data = np.zeros((rows * FACET_COLS, 4), dtype="f4")
        data[: len(n), :3], data[: len(n), 3] = n, 1.0
        self.normals = self.ctx.texture((FACET_COLS, rows), 4, data.tobytes(), dtype="f4")
        self.facets = [self.ctx.texture((FACET_COLS, rows), 4, dtype="f4") for _ in range(3)]
        for t in (self.normals, *self.facets):
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.facet_fbo = self.ctx.framebuffer(color_attachments=self.facets)
        self.count, self.layout_key = len(n), (pitch, offset)

    def projector_target(self, width: int, height: int) -> moderngl.Framebuffer:
        """Where the projector image goes while the simulation shows: offscreen and mipmapped, so a facet can
        look up the average over its tile."""
        if (width, height) != self.size:
            for o in (self.proj_fbo, self.proj, self.beams_fbo, self.beams, self.fog_fbo, self.fog):
                if o is not None:
                    o.release()
            self.fog = self.ctx.texture((max(1, width // 2), max(1, height // 2)), 4, dtype="f2")
            self.fog.repeat_x = self.fog.repeat_y = False
            self.fog_fbo = self.ctx.framebuffer(color_attachments=[self.fog])
            self.proj = self.ctx.texture((width, height), 4, dtype="f1")  # build_mipmaps() adds the levels
            self.proj.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            self.proj.repeat_x = self.proj.repeat_y = False
            self.proj_fbo = self.ctx.framebuffer(color_attachments=[self.proj])
            self.beams = self.ctx.texture((width, height), 4, dtype="f2")
            self.beams.repeat_x = self.beams.repeat_y = False
            self.beams_fbo = self.ctx.framebuffer(color_attachments=[self.beams])
            self.size = (width, height)
        assert self.proj_fbo is not None
        return self.proj_fbo

    # --- drawing -------------------------------------------------------------------------

    def draw(self, dest: moderngl.Framebuffer, cal: Calibration, spec: S.SimSpec) -> None:
        """The hazy room lit by the ball, into ``dest``, from what was drawn into ``projector_target``."""
        if self.proj is None or self.beams_fbo is None or self.beams is None:
            raise RuntimeError("draw the projector image into projector_target() first")
        ctx = self.ctx
        w, h = self.size
        self.proj.build_mipmaps()
        self.set_layout(spec.pitch_deg, spec.offset_deg)
        assert self.facet_fbo is not None and self.normals is not None

        cx, cy = cal.center
        rx, ry = cal.radii
        cal_u = (cx, 1.0 - cy, rx, ry)  # y up, like gl_FragCoord
        room_to_img = image_to_ball_matrix(cal).T  # a rotation: the inverse is the transpose
        spin = rotation_matrix("y", spec.spin_rad)  # the turning ball's frame -> room
        view = spec.view or S.View.default(S.projector_dir(cal.roll_deg, cal.tilt_deg))
        vp = S.view_proj(view, w / h)
        eye = view.eye()
        haze = max(0.0, min(1.0, spec.haze))
        common = {"u_radius": spec.radius_m, "u_boxMin": S.BOX_MIN, "u_boxMax": S.BOX_MAX,
                  "u_sigma": SIGMA * haze, "u_ext": EXTINCTION * haze, "u_g": ANISOTROPY}

        # 1. facets
        self.facet_fbo.use()
        ctx.viewport = (0, 0, *self.normals.size)
        p = self.facet_prog
        self.normals.use(location=0)
        self.proj.use(location=1)
        tile_px = math.radians(spec.pitch_deg) * max(rx * w, ry * h)
        _set(p, {"u_normals": 0, "u_proj": 1, "u_cal": cal_u, "u_lod": math.log2(max(1.0, tile_px))})
        p["u_spinRoom"].write(_mat3_bytes(spin))
        p["u_roomToImg"].write(_mat3_bytes(room_to_img))
        self.facet_vao.render(moderngl.TRIANGLE_STRIP)

        # 2. beams and spots
        self.beams_fbo.use()
        ctx.viewport = (0, 0, w, h)
        self.beams_fbo.clear(0.0, 0.0, 0.0, 0.0)
        p = self.beam_prog
        for i, t in enumerate(self.facets):
            t.use(location=i)
        _set(p, {**common, "u_fColor": 0, "u_fDir": 1, "u_fPos": 2, "u_cols": FACET_COLS, "u_eye": eye,
                 "u_width0": spec.radius_m * math.radians(spec.pitch_deg), "u_diverge": DIVERGE,
                 "u_beamGain": BEAM_GAIN, "u_spotGain": SPOT_GAIN})
        p["u_viewProj"].write(_mat4_bytes(vp))
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE
        try:
            for mode in (0, 1) if spec.room else (0,):
                p["u_mode"].value = mode
                self.beam_vao.render(moderngl.TRIANGLE_STRIP, instances=self.count)
        finally:
            ctx.disable(moderngl.BLEND)

        light = {**common, "u_proj": 0, "u_eye": eye, "u_cal": cal_u, "u_projDist": S.PROJECTOR_DISTANCE_M}
        inv_vp = _mat4_bytes(np.linalg.inv(vp))

        # 3. the projector's beam, at half resolution
        if spec.projector_beam and haze > 0 and self.fog_fbo is not None:
            self.fog_fbo.use()
            ctx.viewport = (0, 0, *self.fog_fbo.size)
            p = self.fog_prog
            self.proj.use(location=0)
            _set(p, {**light, "u_res": tuple(float(v) for v in self.fog_fbo.size), "u_projGain": PROJECTOR_GAIN})
            p["u_invViewProj"].write(inv_vp)
            p["u_roomToImg"].write(_mat3_bytes(room_to_img))
            self.fog_vao.render(moderngl.TRIANGLE_STRIP)

        # 4. room and ball, plus everything above, tone-mapped
        dest.use()
        ctx.viewport = (0, 0, w, h)
        p = self.room_prog
        self.proj.use(location=0)
        self.beams.use(location=1)
        if self.fog is not None:
            self.fog.use(location=2)
        _set(p, {**light, "u_beams": 1, "u_fog": 2, "u_res": (float(w), float(h)),
                 "u_tile": (spec.pitch_deg, spec.offset_deg), "u_wallGain": WALL_GAIN,
                 "u_ambient": AMBIENT if spec.room else 0.0, "u_chainR": S.CHAIN_RADIUS_M,
                 "u_showBeam": int(spec.projector_beam and haze > 0), "u_showBall": int(spec.ball),
                 "u_showRoom": int(spec.room)})
        p["u_invViewProj"].write(inv_vp)
        p["u_roomToImg"].write(_mat3_bytes(room_to_img))
        p["u_roomToBall"].write(_mat3_bytes(spin.T))
        self.room_vao.render(moderngl.TRIANGLE_STRIP)
