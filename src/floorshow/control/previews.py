"""Scene previews on the controller side, GTK-free: which files belong to which scene version, which
are missing, and which loop frame to show at a given beat. The pictures themselves are made by
``floorshow.render.preview`` in a child process (see preview_widgets.py)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..common.scene import Scene

PREVIEW_VERSION = 1  # bump when the helper's output changes, so old previews are made again
SUFFIXES = (".png.tmp", ".npz.tmp", ".png", ".npz")  # longest first
DEFAULT_TILE_DEG = 4.0  # what the renderer uses while the ball's rows are not calibrated (render.core)
THUMB_W, THUMB_H = 176, 99  # logical px of a card's preview at its smallest (16:9); it sets the card's minimum width
OVERSAMPLE = 1.5  # previews are made this much larger: a card grows up to that much to fill a row (two per row)


@dataclass(frozen=True)
class Look:
    """Everything outside the scene file that changes how its preview looks."""

    width: int  # device px: the thumb size times the window's scale factor
    height: int
    accent: tuple[float, float, float]
    tile: float
    row_offset: float | None
    beats_per_bar: int
    bpm: float


@dataclass
class Clip:
    frames: np.ndarray  # uint8 (n, h, w, 3)
    fpb: int  # frames per beat
    loop_beats: int


def frame_index(beat: float, clip: Clip) -> int:
    """The loop frame to show at ``beat`` (the live beat, so the preview pulses with the show)."""
    n = len(clip.frames)
    return int((beat % clip.loop_beats) * clip.fpb) % n if n else 0


class PreviewCache:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._hashes: dict[tuple, str] = {}  # (path, mtime, size) -> sha1 of its bytes

    def _file_hash(self, path: Path) -> str:
        st = path.stat()
        sig = (str(path), st.st_mtime_ns, st.st_size)
        if sig not in self._hashes:
            self._hashes[sig] = hashlib.sha1(path.read_bytes()).hexdigest()
        return self._hashes[sig]

    def key(self, scene: Scene, look: Look) -> str | None:
        """Short hash of the scene's file, its textures and the ``look``; None if the file is gone. The
        accent counts only for a scene that uses it, so changing it remakes only those previews."""
        if scene.source is None:
            return None
        try:
            parts: list[Any] = [PREVIEW_VERSION, self._file_hash(Path(scene.source))]
            parts += [(p, Path(p).stat().st_mtime_ns) for p in scene.asset_order]
        except OSError:
            return None
        parts += [look.width, look.height, look.tile, look.row_offset, look.beats_per_bar, round(look.bpm, 3)]
        if scene.uses_accent:
            parts.append(tuple(round(c, 4) for c in look.accent))
        return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]

    def still_path(self, sid: str, key: str) -> Path:
        return self.dir / f"{sid}-{key}.png"

    def clip_path(self, sid: str, key: str) -> Path:
        return self.dir / f"{sid}-{key}.npz"

    def has(self, sid: str, key: str) -> bool:
        return self.still_path(sid, key).exists() and self.clip_path(sid, key).exists()

    def load_clip(self, sid: str, key: str) -> Clip | None:
        try:
            with np.load(self.clip_path(sid, key)) as d:
                return Clip(d["frames"], int(d["fpb"]), int(d["loop_beats"]))
        except (OSError, KeyError, ValueError):
            return None

    def prune(self, current: dict[str, str]) -> None:
        """Delete preview files that belong to no current scene version (``current``: sid -> key)."""
        if not self.dir.is_dir():
            return
        keep = {f"{sid}-{key}" for sid, key in current.items()}
        for p in self.dir.iterdir():
            suffix = next((s for s in SUFFIXES if p.name.endswith(s)), None)
            if suffix and p.name[: -len(suffix)] not in keep:
                try:
                    p.unlink()
                except OSError:
                    pass

    def job(self, look: Look, todo: list[tuple[str, str, Path]]) -> dict[str, Any]:
        """The helper's job for ``todo`` = [(sid, key, scene file)]."""
        return {
            "out": str(self.dir), "width": look.width, "height": look.height, "bpm": look.bpm,
            "beats_per_bar": look.beats_per_bar, "accent": list(look.accent), "tile": look.tile,
            "row_offset": look.row_offset,
            "scenes": [{"sid": sid, "key": key, "path": str(path)} for sid, key, path in todo],
        }
