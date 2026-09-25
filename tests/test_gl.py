"""Headless GPU tests (moderngl standalone context). Skipped if no GL context is available."""

import math
from pathlib import Path

import moderngl
import numpy as np
from dataclasses import replace
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


def present(pipe, dest, cal, *, mix=1.0, transition="cut", decay=0.0, master=1.0, overlay=None):
    """The two output passes: the transition and trail into the history, then master and overlay out."""
    pipe.blend(cal, mix=mix, transition=transition, decay=decay)
    pipe.composite(dest, cal, master=master, overlay=overlay)
    return pipe.read_rgb(dest).astype(int)


def render(gl, shapes, cal, *, spin=None, textures=None, **comp):
    pipe, dest = gl
    pipe.render_scene(0, [], cal)
    pipe.render_scene(1, shapes, cal, spin=spin, textures=textures)
    return present(pipe, dest, cal, **comp)


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
        return present(pipe, dest, CAL, **kw)

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
    over = Overlay(lines=(0.25, 0.75, 0.2, 0.8), levels=(1.0, 0.6, 1.0, 0.6))
    lines = comp(overlay=over)
    y, x = H // 2 + 20, W // 2 + 20                             # clear of the tangent marks in the middle
    assert lines[y, int(0.25 * W)].max() >= 250                 # left line, full level
    assert 140 <= lines[y, int(0.75 * W)].max() <= 165          # right line, 0.6 level
    assert lines[int(0.2 * H), x].max() >= 250                  # top line
    assert lines[y, W // 2].max() == 0                          # nothing between the lines
    # the tangent mark: one hard pixel at full brightness, only where the line grazes the ball
    mark_y = int((1 - CAL.center[1]) * H)

    def bright(img, row):  # bright pixels of the right half, so the left line is out of the way
        return [v >= 250 for v in img[row, W // 2:].max(axis=1)].count(True)

    assert bright(lines, mark_y) == 1 and bright(lines, mark_y + 20) == 0
    assert bright(comp(overlay=replace(over, mark=0.0)), mark_y) == 0  # blinked off, the 0.6 line stays
    # verification grid draws something inside the ball only
    grid = comp(overlay=Overlay(grid=True))
    assert grid[..., 1].max() > 100 and grid[2, 2].tolist() == [0, 0, 0]
    # the confirmation step turns the grid: the meridians move, the latitude rings and the rim stay put
    turned = comp(overlay=Overlay(grid=True, spin_deg=15.0))
    # compared as lit/unlit, since antialiasing can differ by one step where lines cross
    assert not np.array_equal(grid[..., 2] > 128, turned[..., 2] > 128)  # meridians moved (blue)
    assert np.array_equal(grid[..., 0] > 128, turned[..., 0] > 128)      # equator and rim did not (red)
    assert turned[2, 2].tolist() == [0, 0, 0]


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


def _lat(cal, x, y, w=W, h=H):
    d = cal.pixel_to_ball_dir((x + 0.5) / w, (y + 0.5) / h)
    return None if d is None else lat_lon(d)[0]


TINTS = {"a": (0, 217, 191), "b": (230, 0, 179), "equator": (255, 173, 51), None: (0, 0, 0)}


def _tint_of(pixel):
    """Which of the row colours a pixel is, by nearest match. The rows are painted flat, so this is exact."""
    return min(TINTS, key=lambda k: sum((a - b) ** 2 for a, b in zip(TINTS[k], pixel)))


def _row_mismatches(gl, cal, mode, pitch, offset):
    """(pixels compared, wrongly coloured) for the row overlay against the CPU geometry. Pixels near a row
    boundary are skipped: rows thinner than a pixel near the limb cannot be shown in their own colour."""
    pipe, dest = gl
    pipe.render_scene(0, [], cal)
    pipe.render_scene(1, [], cal)
    present(pipe, dest, cal, overlay=Overlay(rows=(mode, pitch, offset)))
    img = pipe.read_rgb(dest).astype(int)
    equator_band = math.floor(-offset / pitch)
    exp = [[None] * W for _ in range(H)]  # None: outside the ball, where nothing is drawn at all
    for y in range(H):
        for x in range(W):
            lat = _lat(cal, x, y)
            if lat is None:
                assert img[y, x].tolist() == [0, 0, 0]
                continue
            band = math.floor((lat - offset) / pitch)
            if band == equator_band:
                exp[y][x] = "equator"
            elif mode == "all" and abs(lat) <= 60:
                exp[y][x] = "a" if (band - equator_band) % 2 == 0 else "b"
            else:
                exp[y][x] = None  # past the limit, or not the equator's row in "equator" mode: black
    checked = bad = 0
    for y in range(H):
        for x in range(W):
            e = exp[y][x]
            if _lat(cal, x, y) is None or any(exp[yy][xx] != e for yy in range(max(0, y - 2), min(H, y + 3))
                                              for xx in range(max(0, x - 2), min(W, x + 3))):
                continue
            checked += 1
            bad += _tint_of(img[y, x]) != e
    return checked, bad


@pytest.mark.parametrize("tilt,roll", [(0, 0), (22, 12), (-15, 0)])
def test_every_row_is_painted_and_follows_the_ball_frame_latitude(gl, tilt, roll):
    checked, bad = _row_mismatches(gl, Calibration(0.2, 0.8, 0.05, 0.95, roll_deg=roll, tilt_deg=tilt), "all", 10.0, 2.0)
    assert checked > 5000 and bad == 0, (checked, bad)


@pytest.mark.parametrize("tilt,roll", [(0, 0), (25, -10)])
def test_equator_mode_paints_the_row_the_equator_falls_in(gl, tilt, roll):
    cal = Calibration(0.2, 0.8, 0.05, 0.95, roll_deg=roll, tilt_deg=tilt)
    checked, bad = _row_mismatches(gl, cal, "equator", 8.0, 1.0)
    assert checked > 5000 and bad == 0, (checked, bad)
    # the offset is the latitude of a boundary, so the lit row is the one below it, never a row away


def test_crosshair_is_drawn_at_the_requested_place(gl):
    pipe, dest = gl
    cal = Calibration.default(W / H)
    pipe.render_scene(0, [], cal)
    pipe.render_scene(1, [], cal)
    present(pipe, dest, cal, overlay=Overlay(crosshair=(0.25, 0.30)))
    img = pipe.read_rgb(dest).astype(int)
    cx, cy = int(0.25 * W), int(0.30 * H)
    assert img[cy, cx - 8].max() > 200 and img[cy - 8, cx].max() > 200  # the arms
    assert img[cy + 40, cx + 40].max() == 0 and img[H // 2, W // 2].max() == 0
    present(pipe, dest, cal, overlay=Overlay())
    assert pipe.read_rgb(dest).max() == 0  # off by default


def test_the_trail_slows_disappearance_but_not_appearance(gl):
    """What lights up is instantly at full brightness; what goes away fades. That asymmetry is the
    point: a trail that also smoothed the onset would put every hit behind the beat."""
    pipe, dest = gl
    mid = (H // 2, W // 2)
    pipe.render_scene(0, [], CAL)

    # lighting up is instant even with a long trail
    pipe.render_scene(1, [shape(color=(1, 1, 1))], CAL)
    assert present(pipe, dest, CAL, decay=0.9)[mid].tolist() == [255, 255, 255]

    # going away fades, one frame at a time, and reaches black
    pipe.render_scene(1, [], CAL)
    faded = [present(pipe, dest, CAL, decay=0.5)[mid][0] for _ in range(4)]
    assert faded == sorted(faded, reverse=True) and faded[0] < 255
    assert faded[0] == pytest.approx(127, abs=2) and faded[-1] < 20
    for _ in range(12):
        present(pipe, dest, CAL, decay=0.5)
    assert present(pipe, dest, CAL, decay=0.5)[mid].tolist() == [0, 0, 0]


def test_a_long_trail_still_reaches_black(gl):
    """The longest trail decays by only ~4% per frame. In an 8-bit history that rounds back to the
    same value once the pixel is dim, and the ghost never leaves; the history is f16 for this."""
    pipe, dest = gl
    mid = (H // 2, W // 2)
    pipe.render_scene(0, [], CAL)
    pipe.render_scene(1, [shape(color=(1, 1, 1))], CAL)
    present(pipe, dest, CAL, decay=0.96)
    pipe.render_scene(1, [], CAL)
    for _ in range(400):  # ~13 s at 30 fps, far longer than the 3 s maximum
        last = present(pipe, dest, CAL, decay=0.96)
    assert last[mid].tolist() == [0, 0, 0]


def test_no_trail_is_the_old_behaviour_and_master_is_not_smeared(gl):
    pipe, dest = gl
    mid = (H // 2, W // 2)
    pipe.render_scene(0, [], CAL)
    pipe.render_scene(1, [shape(color=(1, 1, 1))], CAL)
    present(pipe, dest, CAL, decay=0.9)
    # blackout is instant: master applies after the trail, so a panic really does go dark
    assert present(pipe, dest, CAL, decay=0.9, master=0.0)[mid].tolist() == [0, 0, 0]
    # and with decay 0 a vanished shape leaves nothing at all
    pipe.render_scene(1, [], CAL)
    assert present(pipe, dest, CAL, decay=0.0)[mid].tolist() == [0, 0, 0]


def test_clearing_the_history_forgets_the_trail(gl):
    pipe, dest = gl
    mid = (H // 2, W // 2)
    pipe.render_scene(0, [], CAL)
    pipe.render_scene(1, [shape(color=(1, 1, 1))], CAL)
    present(pipe, dest, CAL, decay=0.9)
    pipe.clear_history()
    pipe.render_scene(1, [], CAL)
    assert present(pipe, dest, CAL, decay=0.9)[mid].tolist() == [0, 0, 0]
