"""The controller's own view of the scene files (names, tags, params, errors), refreshed when they
change on disk. It works with no renderer running, so the cards are always there."""

from __future__ import annotations

from pathlib import Path

from ..common import paths
from ..common.scene import Scene, SceneError, load_library


class Library:
    def __init__(self, dirs: list[Path] | None = None):
        self.dirs = dirs if dirs is not None else paths.scene_dirs()
        self.scenes: dict[str, Scene] = {}
        self.errors: dict[str, SceneError] = {}
        self._sig: tuple | None = None
        self.refresh()

    def _signature(self) -> tuple:
        sig = []
        for d in self.dirs:
            if d.is_dir():
                for p in sorted(d.glob("*.yaml")):
                    try:
                        sig.append((str(p), p.stat().st_mtime_ns))
                    except OSError:
                        pass
        return tuple(sig)

    def refresh(self) -> bool:
        """Reload if anything changed. True if the library content changed."""
        sig = self._signature()
        if sig == self._sig:
            return False
        self._sig = sig
        self.scenes, self.errors = load_library(*self.dirs)
        return True

    def ids(self) -> list[str]:
        return sorted(self.scenes)

    def path_of(self, scene_id: str) -> Path | None:
        for d in reversed(self.dirs):  # later directories win, as in the renderer
            p = d / f"{scene_id}.yaml"
            if p.exists():
                return p
        return None
