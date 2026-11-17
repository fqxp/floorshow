"""Scene previews, rendered offline: a still half a beat in and a short loop for hover.

Run by the controller as a short-lived, niced child (``python -m floorshow.render.preview``), never in
the show's render thread, so previews cost the running show nothing but a little GPU time while one is
made. The controller itself cannot do this: it has no GL, and an EGL context in a process that has
opened a Wayland display deadlocks.

The job is one JSON object on stdin::

    {"out": dir, "width": w, "height": h, "bpm": 120, "beats_per_bar": 4, "accent": [r, g, b],
     "tile": 4.0, "row_offset": null, "scenes": [{"sid": ..., "path": ..., "key": ...}, ...]}

For each scene it writes ``<sid>-<key>.png`` (the still) and ``<sid>-<key>.npz`` (the loop) and then
prints one JSON line, so the controller can show previews as they arrive. Scene output is a pure
function of the beat (``rand`` and ``noise`` are hashes), so a loop recorded against ``scene_beat`` and
played back on the live beat looks just like the scene would if it had started on a bar.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

from ..common.geometry import Calibration
from ..common.scene import Scene, SceneError, load_scene, make_env

FRAMES_PER_BEAT = 8
LOOP_BARS = 2
STILL_BEAT = 0.5  # half a beat in: scenes that start dark (a fade-in, an off-beat hit) are lit by then


BALL_HEIGHT = 0.9  # of the picture: bigger than a projector's default, since the thumbnail is all ball


def preview_calibration(aspect: float) -> Calibration:
    """A centred, round ball filling most of a picture of ``aspect`` = width / height."""
    ry = BALL_HEIGHT / 2
    rx = ry / aspect
    return Calibration(left=0.5 - rx, right=0.5 + rx, top=0.5 - ry, bottom=0.5 + ry)


def loop_beats(scene: Scene, beats_per_bar: int) -> int:
    return LOOP_BARS * (scene.beats_per_bar or beats_per_bar)


def render_clip(pipe: Any, dest: Any, scene: Scene, *, beats_per_bar: int, bpm: float,
                accent: tuple[float, float, float], tile: float, row_offset: float | None) -> np.ndarray:
    """The loop as uint8 (frames, H, W, 3), frame ``i`` at ``scene_beat = i / FRAMES_PER_BEAT``. Built
    the way Core._slot builds a frame, with the scene's default parameters and no trail."""
    from .frame import FrameSpec, SlotSpec, draw

    w, h = dest.size
    pipe.set_size(w, h)
    cal = preview_calibration(w / h)
    bpb = scene.beats_per_bar or beats_per_bar
    params = scene.default_params()
    textures = scene.asset_order
    n = loop_beats(scene, beats_per_bar) * FRAMES_PER_BEAT
    out = np.empty((n, h, w, 3), dtype=np.uint8)
    for i in range(n):
        b = i / FRAMES_PER_BEAT
        env = make_env(beat=b, phase=b % 1.0, beats_per_bar=bpb, scene_beat=b, time=b * 60.0 / bpm, bpm=bpm,
                       tile=tile, row_offset=row_offset, accent=accent, params=params)
        slot = SlotSpec(scene.evaluate(env), scene.spin(env), textures)
        draw(pipe, FrameSpec(cal=cal, incoming=slot), dest)
        out[i] = pipe.read_rgb(dest)
    return out


def still_index() -> int:
    return int(STILL_BEAT * FRAMES_PER_BEAT)


def write(out: Path, sid: str, key: str, clip: np.ndarray, beats: int) -> None:
    from PIL import Image

    out.mkdir(parents=True, exist_ok=True)
    base = out / f"{sid}-{key}"
    tmp = base.with_name(base.name + ".png.tmp")
    Image.fromarray(clip[still_index()]).save(tmp, format="PNG")
    os.replace(tmp, base.with_name(base.name + ".png"))  # atomic: never half a file
    tmp = base.with_name(base.name + ".npz.tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, frames=clip, fpb=FRAMES_PER_BEAT, loop_beats=beats)
    os.replace(tmp, base.with_name(base.name + ".npz"))


def _rgb(v: Any) -> tuple[float, float, float]:
    r, g, b = v if v else (1.0, 1.0, 1.0)
    return float(r), float(g), float(b)


def _say(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()  # a closed pipe (the controller went away) raises here and ends the job


def run(job: dict[str, Any]) -> int:
    import moderngl

    from .gl import GlPipeline

    try:
        ctx = moderngl.create_standalone_context()
    except Exception as e:  # noqa: BLE001
        _say({"fatal": f"no GL context: {e}"})
        return 1
    w, h = int(job["width"]), int(job["height"])
    pipe = GlPipeline(ctx)
    dest = ctx.framebuffer(color_attachments=[ctx.texture((w, h), 4)])
    out = Path(job["out"])
    parent = os.getppid()
    for item in job["scenes"]:
        if os.getppid() != parent:  # the controller died: nobody wants the rest
            return 0
        sid, key = item["sid"], item["key"]
        try:
            scene = load_scene(Path(item["path"]))
            bpb = int(job.get("beats_per_bar", 4))
            clip = render_clip(pipe, dest, scene, beats_per_bar=bpb, bpm=float(job.get("bpm", 120.0)),
                               accent=_rgb(job.get("accent")), tile=float(job.get("tile", 4.0)),
                               row_offset=job.get("row_offset"))
            write(out, sid, key, clip, loop_beats(scene, bpb))
            _say({"sid": sid, "key": key})
        except (SceneError, OSError, ValueError, KeyError) as e:
            _say({"sid": sid, "key": key, "error": str(e)})
    ctx.release()
    return 0


def main() -> int:
    try:
        os.nice(10)  # the show's renderer comes first
    except OSError:
        pass
    try:
        return run(json.load(sys.stdin))
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
