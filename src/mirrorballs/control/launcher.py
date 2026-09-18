"""Starts the renderer as its own session so closing or killing the controller never touches it."""

from __future__ import annotations

import subprocess
import sys


def start_renderer(extra_args: list[str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "mirrorballs.render.app", *(extra_args or [])],
        start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # it logs to a file itself
    )
