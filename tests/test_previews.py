"""The controller's side of scene previews: cache keys, pruning, frame choice. No GTK, no GL."""

from pathlib import Path

import numpy as np

from floorshow.common.scene import load_scene
from floorshow.control.previews import Clip, Look, PreviewCache, frame_index

LOOK = Look(width=120, height=68, accent=(1.0, 1.0, 1.0), tile=4.0, row_offset=None, beats_per_bar=4, bpm=120.0)
PLAIN = "schema: 1\nname: Plain\nlayers:\n  - {type: fill}\n"
ACCENTED = "schema: 1\nname: Acc\nlayers:\n  - {type: fill, color: accent}\n"


def write(d: Path, sid: str, text: str):
    p = d / f"{sid}.yaml"
    p.write_text(text)
    return load_scene(p)


def test_the_key_follows_the_file(tmp_path):
    cache = PreviewCache(tmp_path / "cache")
    k1 = cache.key(write(tmp_path, "a", PLAIN), LOOK)
    assert k1 == cache.key(load_scene(tmp_path / "a.yaml"), LOOK)
    k2 = cache.key(write(tmp_path, "a", PLAIN.replace("Plain", "Plainer")), LOOK)
    assert k1 != k2


def test_the_accent_counts_only_for_scenes_that_use_it(tmp_path):
    cache = PreviewCache(tmp_path / "cache")
    plain, acc = write(tmp_path, "p", PLAIN), write(tmp_path, "q", ACCENTED)
    assert acc.uses_accent and not plain.uses_accent
    red = Look(**{**LOOK.__dict__, "accent": (1.0, 0.0, 0.0)})
    assert cache.key(plain, LOOK) == cache.key(plain, red)
    assert cache.key(acc, LOOK) != cache.key(acc, red)


def test_the_size_counts(tmp_path):
    cache = PreviewCache(tmp_path / "cache")
    sc = write(tmp_path, "a", PLAIN)
    assert cache.key(sc, LOOK) != cache.key(sc, Look(**{**LOOK.__dict__, "width": 240, "height": 136}))


def test_prune_keeps_only_current_versions(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    for name in ("a-k1.png", "a-k1.npz", "a-old.png", "a-old.npz", "gone-k.png", "a.b-k2.npz", "a-k1.npz.tmp", "notes.txt"):
        (d / name).write_bytes(b"x")
    PreviewCache(d).prune({"a": "k1", "a.b": "k2"})
    assert sorted(p.name for p in d.iterdir()) == ["a-k1.npz", "a-k1.npz.tmp", "a-k1.png", "a.b-k2.npz", "notes.txt"]


def test_has_and_load_clip(tmp_path):
    cache = PreviewCache(tmp_path)
    assert not cache.has("a", "k") and cache.load_clip("a", "k") is None
    frames = np.zeros((8, 2, 3, 3), dtype=np.uint8)
    np.savez_compressed(cache.clip_path("a", "k"), frames=frames, fpb=4, loop_beats=2)
    cache.still_path("a", "k").write_bytes(b"png")
    assert cache.has("a", "k")
    clip = cache.load_clip("a", "k")
    assert clip is not None and clip.frames.shape == (8, 2, 3, 3) and (clip.fpb, clip.loop_beats) == (4, 2)


def test_frame_index_wraps_with_the_loop():
    clip = Clip(np.zeros((16, 1, 1, 3), dtype=np.uint8), fpb=8, loop_beats=2)
    assert frame_index(0.0, clip) == 0
    assert frame_index(0.5, clip) == 4
    assert frame_index(1.99, clip) == 15
    assert frame_index(2.0, clip) == 0
    assert frame_index(37.25, clip) == 10  # 37.25 % 2 = 1.25 beats in
