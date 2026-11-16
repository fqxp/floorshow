"""Runs mirrorballs-listen (beat detection on audio) as a child process while it is needed, and feeds its messages
into the inbox as ``("audio", msg)``.

The listener is its own process so that its analysis never competes with the frame loop for Python's GIL, and so
that a broken audio stack cannot take the show down. It is started when the core wants it (the audio clock drives the
show, or a controller previews sources), stopped when nobody does, and restarted with a growing pause when it dies.
Its stderr goes to the renderer's log; its last line is the error shown when it keeps failing.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from queue import SimpleQueue
from typing import Any

log = logging.getLogger("mirrorballs.listen")
COMMAND = [sys.executable, "-m", "mirrorballs.listen"]
RESTART_MIN_S, RESTART_MAX_S = 1.0, 30.0
STOP_WAIT_S = 1.0

Wants = tuple[str | None, bool, float, float] | None  # see Core.audio_wants


class ListenManager:
    def __init__(self, inbox: SimpleQueue, command: list[str] | None = None, time_fn=time.monotonic):
        self.inbox = inbox
        self.command = command or COMMAND
        self.time_fn = time_fn
        self._wants: Wants = None
        self._sent: Wants = None
        self._proc: subprocess.Popen | None = None
        self._stderr: deque[str] = deque(maxlen=20)
        self._error: str | None = None
        self._restart_at = 0.0
        self._backoff = RESTART_MIN_S
        self._started_at = 0.0

    def configure(self, wants: Wants) -> None:
        was, self._wants = self._wants, wants
        if wants is None:
            self._stop()
            self._error = None
            return
        if was is None and self._proc is None:
            # wanted again after a rest: start at the next poll. (After a crash the pause stands, whatever changed.)
            self._restart_at, self._backoff = 0.0, RESTART_MIN_S
        self._send()

    def poll(self, now: float) -> None:
        if self._wants is None:
            return
        if self._proc is not None and self._proc.poll() is not None:
            code = self._proc.returncode
            last = next((line for line in reversed(self._stderr) if line.strip()), "")
            self._error = _explain(last) or f"mirrorballs-listen exited with {code}"
            log.warning("mirrorballs-listen exited with %s; restarting in %.0f s", code, self._backoff)
            self._proc = None
            self._restart_at = now + self._backoff
            self._backoff = min(RESTART_MAX_S, self._backoff * 2)
        if self._proc is None and now >= self._restart_at:
            self._start(now)
        elif self._proc is not None and now - self._started_at > 2 * RESTART_MAX_S:
            self._backoff = RESTART_MIN_S  # it has been running fine for a while

    def status(self) -> dict[str, Any]:
        return {"running": self._proc is not None and self._proc.poll() is None, "error": self._error}

    def close(self) -> None:
        self._wants = None
        self._stop()

    # --- internals -------------------------------------------------------------------------

    def _start(self, now: float) -> None:
        self._stderr.clear()
        try:
            self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            self._error = f"cannot start mirrorballs-listen: {e}"
            self._proc = None
            self._restart_at = now + self._backoff
            self._backoff = min(RESTART_MAX_S, self._backoff * 2)
            return
        self._started_at = now
        self._sent = None
        proc = self._proc
        threading.Thread(target=self._read_stdout, args=(proc,), name="listen stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), name="listen stderr", daemon=True).start()
        log.info("mirrorballs-listen started (pid %d)", proc.pid)
        self._send()

    def _send(self) -> None:
        if self._proc is None or self._wants is None or self._wants == self._sent:
            return
        active, scan, min_bpm, max_bpm = self._wants
        msg = {"cmd": "configure", "active": active, "scan": scan, "min_bpm": min_bpm, "max_bpm": max_bpm}
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
            self._sent = self._wants
        except (BrokenPipeError, OSError, ValueError):
            pass  # it died; poll() notices and restarts it

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        self._sent = None
        if proc is None:
            return
        try:
            assert proc.stdin is not None
            proc.stdin.close()  # end of input: the listener quits
            proc.wait(STOP_WAIT_S)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            proc.kill()
        log.info("mirrorballs-listen stopped")

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                log.warning("mirrorballs-listen: %s", line.strip()[:200])
                continue
            if isinstance(msg, dict):
                self.inbox.put(("audio", msg))

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._stderr.append(line.rstrip())
            log.info("mirrorballs-listen: %s", line.rstrip())


def _explain(line: str) -> str | None:
    """The listener's last words, as something a person can act on."""
    if "No module named 'gi'" in line or "Namespace Gst not available" in line:
        return "audio needs PyGObject and GStreamer: uv sync --extra audio, and the system package gstreamer1.0-pipewire"
    return line[-200:] or None
