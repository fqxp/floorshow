"""mirrorballs-render: the output process. A normal window (move it to the projector and make it
fullscreen with GNOME), vsync-driven frame loop, autonomous once started."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path
from queue import SimpleQueue
from typing import Any

from ..common import paths
from .core import Core
from .dispatch import pump
from .frame import FrameSpec, draw
from .inhibit import IdleInhibitor
from .ipc_server import AlreadyRunning, IpcServer
from .midi import MidiManager

log = logging.getLogger("mirrorballs.render")
STATE_HZ = 30.0
CONTROL_TICK_S = 0.004
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
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)

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

    ctx = moderngl.create_context()
    pipe = GlPipeline(ctx)
    log.info("GL: %s | %s", ctx.info["GL_RENDERER"], ctx.info["GL_VERSION"])

    config_path = args.config or paths.config_file()
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
            glfw.swap_buffers(window)

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
