"""Scene previews in GTK: the manager that keeps them up to date (one niced helper process at a time)
and the picture that shows a scene's still and plays its loop on the live beat while hovered."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .library import Library  # noqa: E402
from .previews import Clip, Look, PreviewCache, frame_index  # noqa: E402

log = logging.getLogger("mirrorballs.previews")
SYNC_DELAY_MS = 300


class PreviewManager:
    """Works out which previews are missing and has ``mirrorballs.render.preview`` make them. Pictures
    register as listeners and are told when their scene's preview changed."""

    def __init__(self, library: Library, cache: PreviewCache, look: Callable[[], Look], beat: Callable[[], float]):
        self.library, self.cache, self.look, self.beat = library, cache, look, beat
        self.keys: dict[str, str] = {}  # sid -> key of the scene version shown now
        self._listeners: set[Callable[[str], None]] = set()
        self._stills: dict[tuple[str, str], Gdk.Texture] = {}
        self._proc: Gio.Subprocess | None = None
        self._again = False  # something changed while the helper ran
        self._timer = 0
        self._failed = False  # the helper cannot run here (no render extras, no GL): stop trying

    # --- listeners -------------------------------------------------------------------------

    def listen(self, fn: Callable[[str], None]) -> None:
        self._listeners.add(fn)

    def unlisten(self, fn: Callable[[str], None]) -> None:
        self._listeners.discard(fn)

    def _changed(self, sid: str) -> None:
        for fn in list(self._listeners):
            fn(sid)

    # --- what is shown -----------------------------------------------------------------------

    def still(self, sid: str) -> Gdk.Texture | None:
        key = self.keys.get(sid)
        if key is None or not self.cache.has(sid, key):
            return None
        if (sid, key) not in self._stills:
            try:
                self._stills[(sid, key)] = Gdk.Texture.new_from_filename(str(self.cache.still_path(sid, key)))
            except GLib.Error:
                return None
        return self._stills[(sid, key)]

    def clip(self, sid: str) -> Clip | None:
        key = self.keys.get(sid)
        return self.cache.load_clip(sid, key) if key and self.cache.has(sid, key) else None

    # --- keeping them current ----------------------------------------------------------------

    def request_sync(self, delay_ms: int = SYNC_DELAY_MS, restart: bool = False) -> None:
        """Sync soon. ``restart`` pushes a pending sync back (for things that change in bursts, like
        dragging through accent colours)."""
        if self._timer and restart:
            GLib.source_remove(self._timer)
            self._timer = 0
        if not self._timer:
            self._timer = GLib.timeout_add(delay_ms, self._on_timer)

    def _on_timer(self) -> bool:
        self._timer = 0
        self.sync()
        return False

    def sync(self) -> None:
        look = self.look()
        keys: dict[str, str] = {}
        for sid, sc in self.library.scenes.items():
            if (k := self.cache.key(sc, look)) is not None:
                keys[sid] = k
        old, self.keys = self.keys, keys
        self._stills = {sk: t for sk, t in self._stills.items() if keys.get(sk[0]) == sk[1]}
        for sid, k in keys.items():
            if old.get(sid) != k and self.cache.has(sid, k):  # back to a version that is still on disk
                self._changed(sid)
        if self._proc is not None:
            self._again = True  # the running job's files are kept: prune only between jobs
            return
        self.cache.prune(keys)
        todo = [(sid, k, self.library.scenes[sid].source) for sid, k in sorted(keys.items()) if not self.cache.has(sid, k)]
        if todo and not self._failed:
            self._start(self.cache.job(look, todo))  # type: ignore[arg-type]

    def _start(self, job: dict[str, Any]) -> None:
        flags = Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        try:
            proc = Gio.Subprocess.new([sys.executable, "-m", "mirrorballs.render.preview"], flags)
        except GLib.Error as e:
            log.warning("cannot start the preview helper: %s", e.message)
            self._failed = True
            return
        self._proc = proc
        proc.get_stdin_pipe().write_all(json.dumps(job).encode())
        proc.get_stdin_pipe().close()
        self._read(Gio.DataInputStream.new(proc.get_stdout_pipe()), proc)

    def _read(self, stream: Gio.DataInputStream, proc: Gio.Subprocess) -> None:
        stream.read_line_async(GLib.PRIORITY_LOW, None, self._on_line, proc)

    def _on_line(self, stream: Gio.DataInputStream, result: Gio.AsyncResult, proc: Gio.Subprocess) -> None:
        try:
            line, _n = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:  # the helper is done
            proc.wait_async(None, self._on_exit)
            return
        try:
            msg = json.loads(line)
        except ValueError:
            msg = {}
        if "fatal" in msg:
            log.warning("previews: %s", msg["fatal"])
            self._failed = True
        elif "error" in msg:
            log.warning("preview of %s: %s", msg.get("sid"), msg["error"])
        elif msg.get("sid") and self.keys.get(msg["sid"]) == msg.get("key"):
            self._changed(msg["sid"])
        self._read(stream, proc)

    def _on_exit(self, proc: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass
        if proc.get_if_exited() and proc.get_exit_status() != 0 and not self._failed:
            log.warning("the preview helper failed (exit %d)", proc.get_exit_status())
            self._failed = True  # e.g. the render extras are not installed: do not respawn it on every change
        self._proc = None
        if self._again:
            self._again = False
            self.request_sync()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.force_exit()


class PreviewPicture(Gtk.Picture):
    """A scene's preview at a fixed size: the still, and the loop on the live beat while ``hover_on``
    (default: the picture itself) is hovered. Adds ``has-preview`` to itself and ``frame`` once there
    is something to show, so the empty placeholder can be styled apart."""

    def __init__(self, manager: PreviewManager, sid: str, width: int, height: int,
                 hover_on: Gtk.Widget | None = None, frame: Gtk.Widget | None = None, grow: bool = False):
        super().__init__(content_fit=Gtk.ContentFit.FILL, can_shrink=True)
        self.add_css_class("preview")
        self.set_overflow(Gtk.Overflow.HIDDEN)  # so the rounded corners clip the image
        self.manager, self.sid, self._size, self._frame, self._grow = manager, sid, (width, height), frame, grow
        self._clip: Clip | None = None
        self._textures: dict[int, Gdk.Texture] = {}
        self._shown = -1
        self._tick = 0
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._play())
        motion.connect("leave", lambda *_a: self._still())
        (hover_on or self).add_controller(motion)
        self.connect("realize", lambda *_a: manager.listen(self._on_changed))
        self.connect("unrealize", lambda *_a: (manager.unlisten(self._on_changed), self._stop()))
        self._still()

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH if self._grow else Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        """The given size; with ``grow``, as wide as it is let be and as tall as that width needs for
        the same aspect ratio."""
        w, h = self._size
        if orientation == Gtk.Orientation.HORIZONTAL:
            return w, w, -1, -1
        if self._grow and for_size > w:
            h = round(for_size * h / w)
        return h, h, -1, -1

    def _on_changed(self, sid: str) -> None:
        if sid != self.sid:
            return
        playing = self._tick != 0
        self._stop()
        if playing:
            self._play()
        else:
            self._still()

    def _still(self) -> None:
        self._stop()
        tex = self.manager.still(self.sid)
        self.set_paintable(tex)
        for w in (self, self._frame):
            if w is not None:
                w.remove_css_class("has-preview") if tex is None else w.add_css_class("has-preview")

    def _play(self) -> None:
        if self._tick:
            return
        self._clip = self.manager.clip(self.sid)
        if self._clip is None:
            return
        self._tick = self.add_tick_callback(self._on_tick)

    def _stop(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self._clip, self._textures, self._shown = None, {}, -1

    def _on_tick(self, _w: Gtk.Widget, _clock: Any) -> bool:
        clip = self._clip
        if clip is None:
            return False
        i = frame_index(self.manager.beat(), clip)
        if i != self._shown:
            self._shown = i
            if i not in self._textures:
                f = clip.frames[i]
                h, w = f.shape[:2]
                self._textures[i] = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(f.tobytes()), w * 3)
            self.set_paintable(self._textures[i])
        return True
