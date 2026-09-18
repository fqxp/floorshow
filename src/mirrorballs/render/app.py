"""mirrorballs-render: the output process. A normal window (move it to the projector and make it
fullscreen with GNOME), vsync-driven frame loop, autonomous once started."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from queue import SimpleQueue
from typing import Any

from ..common import paths
from ..common.config import ConfigError, load_config
from .core import Core
from .dispatch import pump
from .frame import FrameSpec, draw
from .inhibit import IdleInhibitor
from .ipc_server import AlreadyRunning, IpcServer
from .midi import MidiManager

log = logging.getLogger("mirrorballs.render")
STATE_HZ = 30.0
CONTROL_TICK_S = 0.004
SWAP_STALL_LOG_S = 0.5
TITLE = "Mirrorballs Output"


def _setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        paths.log_file().parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(paths.log_file()))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def wants_x11(config_path: Path, cli_flag: bool) -> bool:
    """X11 (XWayland) mode from the command line or config.yaml. Decided before GLFW is imported, because the
    GLFW build (native Wayland or X11) is chosen at import time."""
    if cli_flag or os.environ.get("MIRRORBALLS_X11") == "1":
        return True
    try:
        return load_config(config_path).output.x11
    except ConfigError:
        return False


def probe_window(glfw, window, fw: int, fh: int) -> dict:
    """Size of the output window in pixels, and whether it covers a whole monitor.

    GNOME (not GLFW) makes the window fullscreen, so GLFW cannot say so directly. A window whose
    framebuffer matches a monitor's video mode is treated as fullscreen on that monitor."""
    monitor_name, fullscreen = None, False
    try:
        for m in glfw.get_monitors():
            mode = glfw.get_video_mode(m)
            if mode and (mode.size.width, mode.size.height) == (fw, fh):
                monitor_name, fullscreen = glfw.get_monitor_name(m), True
                if isinstance(monitor_name, bytes):
                    monitor_name = monitor_name.decode(errors="replace")
                break
    except Exception:  # noqa: BLE001 - purely informational
        pass
    return {"width": fw, "height": fh, "fullscreen": fullscreen, "monitor": monitor_name}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mirrorballs-render", description="Projector output for the mirror ball.")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (default: ~/.config/mirrorballs/config.yaml)")
    ap.add_argument("--scenes", type=Path, action="append", default=[], help="extra scene directory (repeatable)")
    ap.add_argument("--size", default="1280x720", help="initial window size, WxH")
    ap.add_argument("--x11", action="store_true", help="run through XWayland so the window can stay on top")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)

    config_path = args.config or paths.config_file()
    x11 = wants_x11(config_path, args.x11)
    if x11:
        os.environ["PYGLFW_LIBRARY_VARIANT"] = "x11"  # must be set before glfw is imported
    import glfw
    import moderngl

    from .gl import GlPipeline

    # The idle inhibitor forks a child, so it must start before any thread does.
    inhibitor = IdleInhibitor()
    inhibitor.start()

    inbox: SimpleQueue = SimpleQueue()
    ipc = IpcServer(paths.socket_path(), inbox)
    try:
        ipc.start()
    except AlreadyRunning as e:
        print(f"mirrorballs-render: {e}", file=sys.stderr)
        inhibitor.stop()
        return 1

    if not glfw.init():
        log.error("cannot initialise GLFW")
        ipc.stop()
        return 1
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    on_top = False
    try:
        on_top = x11 and load_config(config_path).output.always_on_top
    except ConfigError:
        pass
    glfw.window_hint(glfw.FLOATING, on_top)  # honoured on X11 only; Wayland has no such request
    w, h = (int(x) for x in args.size.lower().split("x"))
    window = glfw.create_window(w, h, TITLE, None, None)
    if not window:
        log.error("cannot create the output window")
        glfw.terminate()
        ipc.stop()
        return 1
    glfw.make_context_current(window)
    glfw.swap_interval(1)  # vsync: one frame per display refresh is the render tick
    glfw.set_input_mode(window, glfw.CURSOR, glfw.CURSOR_HIDDEN)

    def toggle_fullscreen(win: Any, key: int, _sc: int, action: int, _mods: int) -> None:
        """F11: fullscreen on the second monitor (normally the projector), or back to a window."""
        if key != glfw.KEY_F11 or action != glfw.PRESS:
            return
        if glfw.get_window_monitor(win):
            glfw.set_window_monitor(win, None, 100, 100, w, h, 0)
            return
        monitors = glfw.get_monitors()
        if monitors:
            m = monitors[1] if len(monitors) > 1 else monitors[0]
            mode = glfw.get_video_mode(m)
            glfw.set_window_monitor(win, m, 0, 0, mode.size.width, mode.size.height, mode.refresh_rate)

    glfw.set_key_callback(window, toggle_fullscreen)
    # Window events go to the log: when the output "disappears", this is the evidence (focus lost? iconified?).
    glfw.set_window_focus_callback(window, lambda _w, focused: log.info("window %s", "focused" if focused else "lost focus"))
    glfw.set_window_iconify_callback(window, lambda _w, iconified: log.info("window %s", "iconified" if iconified else "restored"))
    glfw.set_window_size_callback(window, lambda _w, sw, sh: log.info("window size %dx%d", sw, sh))
    glfw.set_window_refresh_callback(window, lambda _w: log.debug("window refresh requested"))

    ctx = moderngl.create_context()
    pipe = GlPipeline(ctx)
    log.info("GL: %s | %s | platform %s%s", ctx.info["GL_RENDERER"], ctx.info["GL_VERSION"],
             "X11 (XWayland)" if x11 else "native (Wayland)", ", always on top" if on_top else "")
    core = Core(config_path, [*paths.scene_dirs(), *args.scenes], now=time.monotonic())
    midi = MidiManager(inbox)
    log.info("renderer ready: %d scene(s), socket %s", len(core.scenes), ipc.path)

    # Two threads, one lock. The control thread (IPC, MIDI, director, files, state stream) never
    # waits for the display: swap_buffers can block for a long time when the compositor throttles
    # frame callbacks (occluded window, projector asleep), and control must not stall with it.
    lock = threading.Lock()
    stop = threading.Event()

    def control_loop() -> None:
        next_state = 0.0
        while not stop.is_set():
            now = time.monotonic()
            state, events = None, []
            try:
                with lock:
                    pump(core, inbox, ipc, now)
                    core.poll_files(now)
                    core.tick(now)
                    if core.midi_dirty:
                        midi.configure(*core.midi_wants())
                        core.midi_dirty = False
                    midi.poll(now)
                    core.midi_status = midi.status()
                    if now >= next_state:
                        state = core.state(now)
                        next_state = now + 1.0 / STATE_HZ
                    events = core.take_outbox()
                if state is not None:
                    ipc.broadcast(state)
                for ev in events:
                    ipc.broadcast(ev)
            except Exception:  # noqa: BLE001 - keep controlling even if one iteration fails
                log.exception("control loop error")
            stop.wait(CONTROL_TICK_S)

    control = threading.Thread(target=control_loop, name="control", daemon=True)
    control.start()

    last = time.monotonic()
    failed_msg = ""
    next_window_probe = 0.0
    frame_no = 0
    try:
        while not glfw.window_should_close(window) and not core.quit_requested:
            glfw.poll_events()
            now = time.monotonic()
            fw, fh = glfw.get_framebuffer_size(window)
            if fw <= 0 or fh <= 0:  # minimised
                time.sleep(0.05)
                continue
            pipe.set_size(fw, fh)
            if now >= next_window_probe:
                next_window_probe = now + 1.0
                core.window = probe_window(glfw, window, fw, fh)
            try:
                with lock:
                    spec = core.frame(now, fw / fh)
            except Exception as e:  # noqa: BLE001 - a scene bug must never stop the show
                if str(e) != failed_msg:
                    log.exception("frame evaluation failed")
                    failed_msg = str(e)
                spec = FrameSpec(cal=core.calibration(fw / fh), incoming=None, black=True)
            frame_no += 1
            lum = draw(pipe, spec, ctx.screen, measure=frame_no % 2 == 0)  # 30 Hz sampling still sees 15 Hz flashing
            if lum is not None:
                with lock:
                    core.observe_luminance(now, lum)
            swap_start = time.monotonic()
            glfw.swap_buffers(window)  # blocks while the compositor withholds frame callbacks (window hidden or covered)
            swapped = time.monotonic()
            core.frame_heartbeat = swapped
            if swapped - swap_start > SWAP_STALL_LOG_S:
                log.warning("swap_buffers blocked for %.1f s: the compositor was not showing the output window "
                            "(hidden, covered by another window, or on an output that was off)", swapped - swap_start)

            dt = time.monotonic() - last
            last = time.monotonic()
            if dt > 0:
                core.fps = 0.9 * core.fps + 0.1 / dt if core.fps else 1.0 / dt
    except KeyboardInterrupt:
        pass
    finally:
        log.info("shutting down")
        stop.set()
        control.join(2)
        midi.close()
        ipc.stop()
        inhibitor.stop()
        glfw.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
