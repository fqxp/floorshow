"""Filesystem locations. Environment overrides exist so tests never touch the real config."""

from __future__ import annotations

import os
from pathlib import Path

APP = "floorshow"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def config_dir() -> Path:
    return Path(os.environ["FLOORSHOW_CONFIG_DIR"]) if "FLOORSHOW_CONFIG_DIR" in os.environ else _xdg("XDG_CONFIG_HOME", ".config") / APP


def config_file() -> Path:
    return config_dir() / "config.yaml"


def user_scenes_dir() -> Path:
    return config_dir() / "scenes"


def bundled_scenes_dir() -> Path | None:
    """Read-only example scenes shipped in the repository (None when installed without them)."""
    p = Path(__file__).resolve().parents[3] / "scenes"
    return p if p.is_dir() else None


def scene_dirs() -> list[Path]:
    """Scene search path, lowest priority first (user scenes override bundled ones by id)."""
    dirs = [d for d in (bundled_scenes_dir(), user_scenes_dir()) if d is not None]
    return dirs


def cache_dir() -> Path:
    return Path(os.environ["FLOORSHOW_CACHE_DIR"]) if "FLOORSHOW_CACHE_DIR" in os.environ else _xdg("XDG_CACHE_HOME", ".cache") / APP


def previews_dir() -> Path:
    return cache_dir() / "previews"


def socket_path() -> Path:
    if "FLOORSHOW_SOCKET" in os.environ:
        return Path(os.environ["FLOORSHOW_SOCKET"])
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{APP}-{os.getuid()}"
    return Path(runtime) / APP / "render.sock"


def log_file() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP / "render.log"
