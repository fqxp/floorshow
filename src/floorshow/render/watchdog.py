"""Stall watchdog: when the render thread stops presenting frames, write every thread's Python stack to the
log once, so the log says exactly what it is blocked on (swap_buffers, a lock, ...) instead of leaving a guess.

Also registers SIGUSR1 to dump the stacks on demand:   kill -USR1 <renderer pid>
"""

from __future__ import annotations

import faulthandler
import logging
import signal
import time
from collections.abc import Callable
from typing import IO

log = logging.getLogger("floorshow.watchdog")


class StallWatchdog:
    """Call ``check(now, presenting)`` from any loop. Dumps once per stall, re-arms when frames flow again."""

    def __init__(self, dump: Callable[[str], None]):
        self._dump = dump
        self._armed = True
        self.dumps = 0

    def check(self, now: float, presenting: bool, stalled_s: float = 0.0) -> bool:
        if presenting:
            self._armed = True
            return False
        if not self._armed:
            return False
        self._armed = False
        self.dumps += 1
        log.warning("no frame presented for %.1f s; thread stacks follow", stalled_s)
        self._dump(f"=== stall at {time.strftime('%H:%M:%S')}, no frame for {stalled_s:.1f} s ===\n")
        return True


def file_dumper(stream: IO[str]) -> Callable[[str], None]:
    """A dump function writing a header and then all thread tracebacks to ``stream`` (must have a fileno)."""

    def dump(header: str) -> None:
        stream.write(header)
        stream.flush()
        faulthandler.dump_traceback(file=stream, all_threads=True)
        stream.flush()

    return dump


def install_signal_dump(stream: IO[str]) -> None:
    """kill -USR1 <pid> writes all thread stacks to ``stream``."""
    try:
        faulthandler.register(signal.SIGUSR1, file=stream, all_threads=True)
    except (AttributeError, ValueError, OSError):  # not available on this platform
        log.info("SIGUSR1 stack dump not available")
