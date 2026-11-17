"""Keep the screen from blanking while the show is running (spike S4).

GNOME blanks the display after a period without input, and a projector output is exactly the kind of
window nobody touches. ``gnome-session-inhibit`` holds an *idle* inhibitor for as long as its process
lives, so the renderer runs one as a child. If the renderer dies without cleaning up, the kernel
sends the child SIGTERM (PR_SET_PDEATHSIG), so a crash can never leave the screen awake forever.

Start it before creating any threads: ``preexec_fn`` is not safe in a multi-threaded process.
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import signal
import subprocess

log = logging.getLogger("floorshow.inhibit")
PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:  # runs in the child between fork and exec
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class IdleInhibitor:
    def __init__(self, reason: str = "Floorshow is projecting"):
        self.reason = reason
        self._proc: subprocess.Popen | None = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """Returns False (and logs) if no inhibit tool is available; the show runs fine without it."""
        exe = shutil.which("gnome-session-inhibit")
        if exe is None:
            log.info("gnome-session-inhibit not found: the screen may blank while projecting")
            return False
        try:
            self._proc = subprocess.Popen(
                [exe, "--app-id", "de.fqxp.Floorshow", "--reason", self.reason, "--inhibit", "idle", "--inhibit-only"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent,
            )
        except OSError as e:
            log.warning("cannot start the idle inhibitor: %s", e)
            return False
        return True

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
