"""The offline preview helper (GL, standalone context; skipped without one). Must run before the GTK
tests, like test_gl.py."""

import io
import json
from pathlib import Path

import moderngl
import numpy as np
import pytest

from mirrorballs.common.scene import load_scene, load_scene_text
from mirrorballs.render import preview
from mirrorballs.render.gl import GlPipeline

ROOT = Path(__file__).resolve().parent.parent
W, H = 120, 68


@pytest.fixture(scope="module")
def gl():
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no GL context: {e}")
    yield GlPipeline(ctx), ctx.framebuffer(color_attachments=[ctx.texture((W, H), 4)])
    ctx.release()


def clip_of(gl, name, **kw):
    pipe, dest = gl
    args = dict(beats_per_bar=4, bpm=120.0, accent=(1.0, 1.0, 1.0), tile=4.0, row_offset=None) | kw
    return preview.render_clip(pipe, dest, load_scene(ROOT / "scenes" / f"{name}.yaml"), **args)


def test_a_loop_is_two_bars_and_the_still_is_lit(gl):
    clip = clip_of(gl, "kick-spot")
    assert clip.shape == (preview.LOOP_BARS * 4 * preview.FRAMES_PER_BEAT, H, W, 3)
    assert clip[preview.still_index()].max() > 0
    assert clip[:, 0, 0].max() == 0  # outside the ball stays black


def test_the_loop_moves(gl):
    clip = clip_of(gl, "sweep-and-sparkle")
    assert any(not np.array_equal(clip[0], f) for f in clip[1:])


def test_the_accent_colours_an_accented_scene(gl, tmp_path):
    sc = load_scene_text("schema: 1\nname: Acc\nlayers:\n  - {type: fill, color: accent}\n", "acc", tmp_path)
    pipe, dest = gl
    args = dict(beats_per_bar=4, bpm=120.0, tile=4.0, row_offset=None)
    red = preview.render_clip(pipe, dest, sc, accent=(1.0, 0.0, 0.0), **args)[0, H // 2, W // 2]
    blue = preview.render_clip(pipe, dest, sc, accent=(0.0, 0.0, 1.0), **args)[0, H // 2, W // 2]
    assert red.tolist() == [255, 0, 0] and blue.tolist() == [0, 0, 255]


def test_run_writes_both_files_and_reports_each_scene(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema: 1\nlayers: [{type: nonsense}]\n")
    job = {"out": str(tmp_path / "out"), "width": W, "height": H, "scenes": [
        {"sid": "kick-spot", "key": "k1", "path": str(ROOT / "scenes" / "kick-spot.yaml")},
        {"sid": "bad", "key": "k2", "path": str(bad)},
    ]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    assert preview.main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[0] == {"sid": "kick-spot", "key": "k1"}
    assert lines[1]["sid"] == "bad" and "error" in lines[1]
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["kick-spot-k1.npz", "kick-spot-k1.png"]  # nothing half-written left behind
    with np.load(tmp_path / "out" / "kick-spot-k1.npz") as d:
        assert d["frames"].shape[1:] == (H, W, 3) and int(d["fpb"]) == preview.FRAMES_PER_BEAT
