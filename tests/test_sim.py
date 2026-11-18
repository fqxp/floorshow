"""Haze simulation: the geometry (GL-free), the Core switch, and the GL passes (headless, skipped without GL)."""

import math
import random
from pathlib import Path
from queue import SimpleQueue

import numpy as np
import pytest

from floorshow.common import protocol as P
from floorshow.common.geometry import Calibration
from floorshow.common.scene import load_scene_text, make_env
from floorshow.render import sim as S
from floorshow.render.core import Core
from floorshow.render.dispatch import pump

# --- geometry -----------------------------------------------------------------------------


def test_facets_tile_the_ball_in_rows_of_unit_normals():
    n = S.facet_layout(4.0, 0.0)
    assert np.allclose(np.linalg.norm(n, axis=1), 1.0, atol=1e-5)
    assert len(n) == pytest.approx(4 * math.pi / math.radians(4.0) ** 2, rel=0.1)  # sphere area / tile area
    lats = np.degrees(np.arcsin(n[:, 1]))
    assert np.allclose(np.round(lats / 4.0) * 4.0, lats, atol=1e-3)  # rows every 4 degrees, one on the equator
    shifted = np.degrees(np.arcsin(S.facet_layout(4.0, 1.5)[:, 1]))
    assert np.allclose((shifted - 1.5) / 4.0, np.round((shifted - 1.5) / 4.0), atol=1e-3)


def test_a_facet_facing_the_projector_sends_the_light_back_and_one_at_45_degrees_sideways():
    assert S.reflect((0.0, 0.0, 1.0)) == pytest.approx((0.0, 0.0, 1.0))
    s = math.sqrt(0.5)
    assert S.reflect((s, 0.0, s)) == pytest.approx((1.0, 0.0, 0.0))
    assert S.reflect((0.0, s, s)) == pytest.approx((0.0, 1.0, 0.0))


def test_the_tilt_says_how_high_the_projector_is():
    assert S.projector_dir(0.0, 0.0) == pytest.approx((0.0, 0.0, 1.0))
    assert S.projector_dir(0.0, 30.0)[1] == pytest.approx(0.5)  # + tilt: the projector is above the ball


def test_a_beam_ends_at_the_wall_it_hits():
    assert S.box_exit((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)) == (pytest.approx(S.CEILING_ABOVE_M), 1)
    assert S.box_exit((0.0, 0.0, 0.0), (0.0, -1.0, 0.0)) == (pytest.approx(S.BALL_HEIGHT_M), 1)
    assert S.box_exit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)) == (pytest.approx(S.ROOM_HALF_M), 0)


def test_the_default_view_stands_at_eye_height_across_from_the_projector_and_looks_at_the_ball():
    v = S.View.default(S.projector_dir(0.0, 20.0))
    x, y, z = v.eye()
    assert y + S.BALL_HEIGHT_M == pytest.approx(S.EYE_HEIGHT_M)
    assert math.hypot(x, z) == pytest.approx(S.EYE_DISTANCE_M)
    assert z < 0  # the projector is on +z: we stand on the other side
    clip = S.view_proj(v, 16 / 9) @ np.array([0.0, 0.0, 0.0, 1.0])
    assert clip[:2] / clip[3] == pytest.approx((0.0, 0.0), abs=1e-9)  # the ball is in the middle of the picture


def test_the_view_stays_in_the_room():
    v = S.View.default((0.0, 0.0, 1.0))
    assert v.zoom(100).dist == S.MAX_DIST_M and v.zoom(0.001).dist == S.MIN_DIST_M
    assert v.orbit(0, 500).pitch_deg == S.MAX_PITCH_DEG
    low = S.View(0.0, -85.0, S.MAX_DIST_M)
    assert low.eye()[1] > S.BOX_MIN[1]  # not under the floor


def test_spin():
    assert S.spin_angle(1.0, 60.0) == pytest.approx(2 * math.pi)
    assert S.spin_angle(0.0, 60.0) == 0.0


# --- Core ---------------------------------------------------------------------------------

SCENE = "schema: 1\nname: a\nlayers:\n  - {type: fill, color: white}\n"


@pytest.fixture
def core(tmp_path):
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "a.yaml").write_text(SCENE)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ball: {diameter_cm: 40, row_pitch_deg: 5, row_offset_deg: 1, rotation_rpm: 2}\n"
                   "simulation: {haze: 0.8, room: false}\n")
    return Core(cfg, [tmp_path / "scenes"], now=100.0, rng=random.Random(1))


def cmd(c, msg, client="c1"):
    return c.handle(P.parse_command(msg), client, 100.0)


def test_the_simulation_is_off_until_asked_for_and_then_uses_the_ball_and_its_settings(core):
    assert not core.state(100.0)["simulation"] and core.frame(100.0).sim is None
    assert cmd(core, {"cmd": "simulation.set", "on": True})["ok"]
    assert core.state(100.0)["simulation"]
    s = core.frame(115.0).sim
    assert s is not None
    assert (s.radius_m, s.pitch_deg, s.offset_deg, s.haze, s.room, s.ball) == (0.2, 5.0, 1.0, 0.8, False, True)
    assert s.spin_rad == pytest.approx(math.pi)  # 15 s at 2 rpm is half a turn
    cmd(core, {"cmd": "simulation.set", "on": False})
    assert core.frame(100.0).sim is None


def test_calibrating_and_the_test_pattern_are_never_simulated(core):
    cmd(core, {"cmd": "simulation.set", "on": True})
    cmd(core, {"cmd": "testpattern.set", "on": True})
    assert core.frame(100.0).sim is None
    cmd(core, {"cmd": "testpattern.set", "on": False})
    cmd(core, {"cmd": "calibration.begin"})
    assert core.frame(100.0).sim is None


def test_a_blackout_still_shows_the_room(core):
    cmd(core, {"cmd": "simulation.set", "on": True})
    cmd(core, {"cmd": "running.set", "running": False})
    f = core.frame(110.0)
    assert f.black and f.sim is not None


def test_key_h_in_the_output_window_toggles_it(core):
    inbox: SimpleQueue = SimpleQueue()
    inbox.put(("simulation.toggle",))
    pump(core, inbox, None, 100.0)  # type: ignore[arg-type]
    assert core.simulation
    inbox.put(("simulation.toggle",))
    pump(core, inbox, None, 100.0)  # type: ignore[arg-type]
    assert not core.simulation


def test_key_h_in_the_controller():
    from floorshow.control.viewmodel import key_to_action

    assert key_to_action("h") == key_to_action("H") == ("simulation", None)


def test_menu_shortcuts():
    from floorshow.control.viewmodel import key_to_action

    assert key_to_action("e", ctrl=True) == ("scenes", None)
    assert key_to_action("k", ctrl=True) == ("calibration", None)
    assert key_to_action("Q", ctrl=True, shift=True) == ("quit-renderer", None)
    assert key_to_action("q", ctrl=True) == ("quit", None)


# --- GL -----------------------------------------------------------------------------------

W, H = 320, 180


@pytest.fixture(scope="module")
def gl():
    moderngl = pytest.importorskip("moderngl")
    from floorshow.render.gl import GlPipeline

    try:
        ctx = moderngl.create_standalone_context()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no GL context: {e}")
    pipe = GlPipeline(ctx)
    pipe.set_size(W, H)
    dest = ctx.framebuffer(color_attachments=[ctx.texture((W, H), 4)])
    yield pipe, dest
    ctx.release()


def frame(text: str | None, sim: S.SimSpec | None, *, black: bool = False):
    from floorshow.render.frame import FrameSpec, SlotSpec

    cal = Calibration.default(W / H)
    if text is None:
        return FrameSpec(cal=cal, incoming=None, black=True, sim=sim)
    sc = load_scene_text(text, "t", Path("."))
    env = make_env(beat=0.0, phase=0.0, beats_per_bar=4, scene_beat=0.0, time=0.0, bpm=120.0, trans=1.0, tile=4.0,
                   row_offset=None, accent=(1.0, 1.0, 1.0), params=None)
    return FrameSpec(cal=cal, incoming=SlotSpec(sc.evaluate(env), sc.spin(env), sc.asset_order), black=black, sim=sim)


def draw_rgb(gl, spec):
    from floorshow.render.frame import draw

    pipe, dest = gl
    draw(pipe, spec, dest)
    return pipe.read_rgb(dest).astype(int)


WHITE = "schema: 1\nname: t\nlayers:\n  - {type: fill, color: white}\n"
NORTH = "schema: 1\nname: t\nlayers:\n  - {type: band, lat: 45, width: 80, softness: 0, color: white}\n"


def test_a_lit_ball_fills_the_room_with_beams_and_a_dark_one_does_not(gl):
    only_beams = S.SimSpec(projector_beam=False, ball=False, room=False)
    lit = draw_rgb(gl, frame(WHITE, only_beams))
    dark = draw_rgb(gl, frame(None, only_beams))
    assert dark.max() == 0
    assert (lit.sum(axis=2) > 30).mean() > 0.05  # beams across a good part of the picture


def test_the_room_and_the_ball_show_in_a_blackout(gl):
    img = draw_rgb(gl, frame(None, S.SimSpec()))
    assert 0 < img.max() < 40  # faint, but there


def test_light_on_the_northern_half_is_thrown_upwards(gl):
    pipe, _ = gl
    draw_rgb(gl, frame(NORTH, S.SimSpec()))
    color = np.frombuffer(pipe.sim.facets[0].read(), dtype="f4").reshape(-1, 4)
    direction = np.frombuffer(pipe.sim.facets[1].read(), dtype="f4").reshape(-1, 4)
    lit = color[:, :3].sum(axis=1) > 0.1
    assert lit.sum() > 50
    assert (direction[lit, 1] > 0).all()


def test_the_projector_image_is_unchanged_after_a_simulated_frame(gl):
    before = draw_rgb(gl, frame(WHITE, None))
    draw_rgb(gl, frame(WHITE, S.SimSpec()))
    after = draw_rgb(gl, frame(WHITE, None))
    assert (before == after).all()  # in particular, the simulation leaves no blending switched on


def test_the_controller_shows_a_note_while_simulating():
    from floorshow.control import viewmodel as vm

    clock = dict(source="internal", bpm=120.0, beat=0.0, beats_per_bar=4, locked=True)
    assert vm.banners(True, dict(clock=clock, rev=vm.REV)) == []
    [b] = vm.banners(True, dict(clock=clock, rev=vm.REV, simulation=True))
    assert b.kind == "simulation" and b.info and b.action == "Turn off" and "double-click" in b.text
