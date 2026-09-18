"""Headless GPU tests (moderngl standalone context). Skipped if no GL context is available."""

import math
from pathlib import Path

import moderngl
import numpy as np
import pytest

from mirrorballs.common.geometry import Calibration, lat_lon
from mirrorballs.common.scene import ShapeParams, load_scene_text, make_env
from mirrorballs.render.gl import GlPipeline, Overlay

W, H = 320, 180
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def gl():
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no GL context: {e}")
    pipe = GlPipeline(ctx)
    pipe.set_size(W, H)
    dest = ctx.framebuffer(color_attachments=[ctx.texture((W, H), 4)])
    yield pipe, dest
    ctx.release()


def shape(**kw):
    base = dict(center=(0.0, 1.0, 0.0), inner=0.0, outer=math.pi, softness=0.0, color=(1.0, 1.0, 1.0),
                alpha=1.0, blend="over", texture=-1, tex_mapping="shape", tex_scale=(1.0, 1.0),
                tex_scroll=0.0, world_frame=False)
    base.update(kw)
    return ShapeParams(**base)


def render(gl, shapes, cal, *, spin=None, textures=None, **comp):
    pipe, dest = gl
    pipe.render_scene(0, [], cal)
    pipe.render_scene(1, shapes, cal, spin=spin, textures=textures)
    pipe.composite(dest, cal, **comp)
    return pipe.read_rgb(dest).astype(int)


CAL = Calibration.default(W / H)


def test_outside_the_silhouette_is_black_and_fill_lights_the_ball(gl):
    img = render(gl, [shape()], CAL)
    assert img[H // 2, W // 2].tolist() == [255, 255, 255]
    assert img[2, 2].tolist() == [0, 0, 0] and img[H - 3, W - 3].tolist() == [0, 0, 0]
    lit = (img[..., 0] > 0).sum()
    expected = math.pi * (CAL.radii[0] * W) * (CAL.radii[1] * H)
    assert lit == pytest.approx(expected, rel=0.02)


def test_gpu_matches_cpu_geometry_with_tilt_and_roll(gl):
    cal = Calibration(left=0.27, right=0.71, top=0.12, bottom=0.90, roll_deg=12.0, tilt_deg=22.0)
    sc = load_scene_text(
        "schema: 1\nname: t\nlayers:\n  - {type: band, lat: 15, width: 30, softness: 0, color: red}\n", "t", ROOT
    )
    shapes = sc.evaluate(make_env(beat=0, phase=0, beats_per_bar=4, scene_beat=0, time=0, bpm=120))
    img = render(gl, shapes, cal)
    checked = mismatches = 0
    for y in range(H):
        for x in range(W):
            d = cal.pixel_to_ball_dir((x + 0.5) / W, (y + 0.5) / H)
            got = img[y, x, 0] > 127
            if d is None:
                assert img[y, x].tolist() == [0, 0, 0]
                continue
            lat, _ = lat_lon(d)
            if min(abs(lat - 0), abs(lat - 30)) < 0.7:  # too close to an edge to call
                continue
            checked += 1
            mismatches += got != (0.0 < lat < 30.0)
    assert checked > 10000
    assert mismatches == 0


def test_spin_rotates_the_scene(gl):
    c = shape(center=(0.0, 0.0, 1.0), outer=math.radians(10))
    d45 = (math.sin(math.radians(45)), 0.0, math.cos(math.radians(45)))
    x45 = int((CAL.center[0] + CAL.radii[0] * d45[0]) * W)
    y0 = int(CAL.center[1] * H)
    plain = render(gl, [c], CAL)
    spun = render(gl, [c], CAL, spin=("y", math.radians(45)))
    assert plain[y0, W // 2, 0] == 255 and plain[y0, x45, 0] == 0
    assert spun[y0, W // 2, 0] == 0 and spun[y0, x45, 0] == 255


def test_blend_modes_and_alpha(gl):
    a = shape(color=(0.3, 0.3, 0.3))
    px = lambda img: img[H // 2, W // 2, 0]
    assert px(render(gl, [a, a], CAL)) == pytest.approx(0.3 * 255, abs=2)  # over: same colour
    assert px(render(gl, [a, shape(color=(0.3, 0.3, 0.3), blend="add")], CAL)) == pytest.approx(0.6 * 255, abs=2)
    assert px(render(gl, [a, shape(color=(0.5, 0.5, 0.5), blend="multiply")], CAL)) == pytest.approx(0.15 * 255, abs=2)
    assert px(render(gl, [a, shape(color=(0.5, 0.5, 0.5), blend="screen")], CAL)) == pytest.approx(0.65 * 255, abs=2)
    assert px(render(gl, [shape(color=(1, 1, 1), alpha=0.5)], CAL)) == pytest.approx(127, abs=2)


def test_soft_edges_fade_and_hard_edges_do_not(gl):
    ring = dict(center=(0.0, 0.0, 1.0), outer=math.radians(30))
    hard = render(gl, [shape(**ring, softness=0.0)], CAL)[H // 2]
    soft = render(gl, [shape(**ring, softness=math.radians(8))], CAL)[H // 2]
    assert set(np.unique(hard[:, 0])) == {0, 255}
    assert len(np.unique(soft[:, 0])) > 5


def test_transitions_master_and_lines(gl):
    red, blue = shape(color=(1, 0, 0)), shape(color=(0, 0, 1))
    pipe, dest = gl
    pipe.render_scene(0, [red], CAL)
    pipe.render_scene(1, [blue], CAL)
    mid = (H // 2, W // 2)

    def comp(**kw):
        pipe.composite(dest, CAL, **kw)
        return pipe.read_rgb(dest).astype(int)

    assert comp(mix=0.5, transition="crossfade")[mid].tolist() == pytest.approx([127, 0, 127], abs=2)
    assert comp(mix=0.5, transition="cut")[mid].tolist() == [0, 0, 255]
    assert comp(mix=1.0, transition="crossfade", master=0.5)[mid].tolist() == pytest.approx([0, 0, 127], abs=2)
    assert comp(mix=0.0, transition="crossfade", master=0.0)[mid].tolist() == [0, 0, 0]
    # wipe_lat: at 25% the northern part already shows B, the southern part still A
    wipe = comp(mix=0.25, transition="wipe_lat")
    top_y, bot_y = int(CAL.center[1] * H - CAL.radii[1] * H * 0.8), int(CAL.center[1] * H + CAL.radii[1] * H * 0.8)
    assert wipe[top_y, W // 2].tolist() == [0, 0, 255] and wipe[bot_y, W // 2].tolist() == [255, 0, 0]
    # iris: opens from the centre
    iris = comp(mix=0.3, transition="iris")
    assert iris[mid].tolist() == [0, 0, 255]
    edge = (H // 2, int(CAL.center[0] * W + CAL.radii[0] * W * 0.95))
    assert iris[edge].tolist() == [255, 0, 0]
    # flash goes through white at the midpoint
    assert comp(mix=0.5, transition="flash")[mid].tolist() == [255, 255, 255]

    # calibration lines over a black scene
    pipe.render_scene(1, [], CAL)
    lines = comp(overlay=Overlay(lines=(0.25, 0.75, 0.2, 0.8), levels=(1.0, 0.6, 1.0, 0.6)))
    assert lines[H // 2, int(0.25 * W)].max() >= 250            # left line, full level
    assert 140 <= lines[H // 2, int(0.75 * W)].max() <= 165     # right line, 0.6 level
    assert lines[int(0.2 * H), W // 2].max() >= 250             # top line
    assert lines[H // 2, W // 2].max() == 0                     # nothing between the lines
    assert lines[H // 2, int(0.5 * W)].tolist() == [0, 0, 0]
    # verification grid draws something inside the ball only
    grid = comp(overlay=Overlay(grid=True))
    assert grid[..., 1].max() > 100 and grid[2, 2].tolist() == [0, 0, 0]


def test_texture_modulates_colour(gl, tmp_path):
    from PIL import Image

    im = Image.new("RGB", (64, 64), (0, 0, 0))
    for x in range(0, 64, 8):
        im.paste((255, 255, 255), (x, 0, x + 4, 64))
    p = tmp_path / "stripes.png"
    im.save(p)
    pipe, _ = gl
    tex = pipe.textures_for([str(p)])
    assert pipe.textures_for([str(p)]) is tex  # cached
    band = shape(center=(0.0, 1.0, 0.0), inner=math.radians(40), outer=math.radians(70), texture=0, tex_scale=(8.0, 1.0))
    img = render(gl, [band], CAL, textures=tex)
    lit = img[..., 0][img[..., 0] > 0]
    assert lit.size > 500 and lit.max() > 200 and len(np.unique(lit)) > 10  # stripes: many levels
    plain = render(gl, [shape(center=(0.0, 1.0, 0.0), inner=math.radians(40), outer=math.radians(70))], CAL)
    assert (plain[..., 0] > 0).sum() > (img[..., 0] > 0).sum()  # dark stripes remove light


def test_mean_luminance_of_the_ball_is_measured_from_the_scene_before_master(gl):
    from mirrorballs.render.frame import FrameSpec, SlotSpec, draw

    pipe, dest = gl
    white = SlotSpec([shape(color=(1, 1, 1))])
    dim = SlotSpec([shape(color=(0.5, 0.5, 0.5))])
    half = SlotSpec([shape(center=(0.0, 1.0, 0.0), outer=math.pi / 2, color=(1, 1, 1))])  # the upper half of the ball
    lum = lambda slot, master=1.0: draw(pipe, FrameSpec(cal=CAL, incoming=slot, master=master), dest, measure=True)
    assert lum(white) == pytest.approx(1.0, abs=0.02)  # a fully lit ball reads as 1.0 even though it fills ~40 % of the frame
    assert lum(dim) == pytest.approx(0.5, abs=0.02)
    assert lum(white, master=0.2) == pytest.approx(1.0, abs=0.02)  # master brightness does not change the measurement
    assert lum(half) == pytest.approx(0.5, abs=0.06)
    assert draw(pipe, FrameSpec(cal=CAL, incoming=white), dest, measure=False) is None
    black = FrameSpec(cal=CAL, incoming=None, black=True)
    assert draw(pipe, black, dest, measure=True) is None


@pytest.mark.parametrize("size", [(320, 180), (1280, 720), (1920, 1080), (1000, 777)])
def test_measured_luminance_is_exact_at_any_frame_size(gl, size):
    """Regression: a mip-chain average was 0.76 (1280x720) or 3.2x too high (1920x1080) because
    non power-of-two mipmaps are not box averages."""
    from mirrorballs.render.frame import FrameSpec, SlotSpec, draw

    pipe = GlPipeline(gl[0].ctx)
    pipe.set_size(*size)
    dest = gl[0].ctx.framebuffer(color_attachments=[gl[0].ctx.texture(size, 4)])
    cal = Calibration.default(size[0] / size[1])
    for color, expect in (((1, 1, 1), 1.0), ((0.5, 0.5, 0.5), 0.5), ((1, 0, 0), 0.2126)):
        got = draw(pipe, FrameSpec(cal=cal, incoming=SlotSpec([shape(color=color)])), dest, measure=True)
        assert got == pytest.approx(expect, abs=0.03), (size, color)
