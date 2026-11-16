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
from .core import Core
from .dispatch import pump
from .frame import FrameSpec, draw
from .inhibit import IdleInhibitor
from .ipc_server import AlreadyRunning, IpcServer
from .listen import ListenManager
from .midi import MidiManager
from .watchdog import StallWatchdog, file_dumper, install_signal_dump

log = logging.getLogger("mirrorballs.render")
STATE_HZ = 30.0
MAX_FPS = 30.0  # the output is drawn at most this often; scenes are functions of the clock, so a lower rate only costs smoothness
FRAME_LEAD_S = 0.002  # start this long before a frame is due, so rendering is done on time
CONTROL_TICK_S = 0.02  # 50 Hz: MIDI clock ticks are timestamped in the MIDI callback, so this only bounds trigger latency
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


def configure_output_window(glfw, window) -> None:
    """Make the output window an ordinary window that can be fullscreen.

    GLFW minimises a fullscreen window as soon as it loses input focus (GLFW_AUTO_ICONIFY, on by default) so
    that games can restore the video mode. Here it made the projector image vanish whenever another window
    was clicked. Wayland does not tell a client it was minimised, so nothing showed in the log except that
    frames stopped. A window that another program covers or focuses should just stay where it is."""
    glfw.set_window_attrib(window, glfw.AUTO_ICONIFY, glfw.FALSE)
    glfw.set_input_mode(window, glfw.CURSOR, glfw.CURSOR_HIDDEN)


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

    config_path = args.config or paths.config_file()
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
    glfw.window_hint(glfw.AUTO_ICONIFY, glfw.FALSE)  # see configure_output_window
    w, h = (int(x) for x in args.size.lower().split("x"))
    window = glfw.create_window(w, h, TITLE, None, None)
    if not window:
        log.error("cannot create the output window")
        glfw.terminate()
        ipc.stop()
        return 1
    configure_output_window(glfw, window)

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

    fb = {"size": glfw.get_framebuffer_size(window)}  # kept current by the callback; the render thread must not call GLFW queries

    glfw.set_key_callback(window, toggle_fullscreen)
    glfw.set_framebuffer_size_callback(window, lambda _w, fw, fh: fb.__setitem__("size", (fw, fh)))
    # Window events go to the log, so a window that misbehaves leaves evidence (focus lost? iconified?).
    glfw.set_window_focus_callback(window, lambda _w, focused: log.info("window %s", "focused" if focused else "lost focus"))
    glfw.set_window_iconify_callback(window, lambda _w, iconified: log.info("window %s", "iconified" if iconified else "restored"))
    glfw.set_window_size_callback(window, lambda _w, sw, sh: log.info("window size %dx%d", sw, sh))

    core = Core(config_path, [*paths.scene_dirs(), *args.scenes], now=time.monotonic())
    midi = MidiManager(inbox)
    listen = ListenManager(inbox)  # beat detection on audio, a child process while it is needed
    try:  # stall stacks and the SIGUSR1 dump go to a file next to the log
        dump_stream = open(paths.log_file().with_suffix(".stacks"), "a", buffering=1)
        install_signal_dump(dump_stream)
        watchdog = StallWatchdog(file_dumper(dump_stream))
    except OSError:
        watchdog = StallWatchdog(lambda _h: None)

    # Three threads, one lock.
    #  main    : nothing but window events. On Wayland the compositor talks to a client through events that are
    #            only handled while they are pumped, so this thread must never block on anything.
    #  render  : owns the GL context and presents frames. swap_buffers blocks while the compositor does not
    #            want frames (vsync, window hidden), and that is harmless here because events keep flowing.
    #  control : IPC, MIDI, the audio listener, director, files, state stream. Never waits for the display either.
    lock = threading.Lock()
    stop = threading.Event()
    render_failed = threading.Event()

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
                    if core.audio.dirty:
                        listen.configure(core.audio_wants())
                        core.audio.dirty = False
                    listen.poll(now)
                    core.audio.process = listen.status()
                    if now >= next_state:
                        state = core.state(now)
                        next_state = now + 1.0 / STATE_HZ
                    events = core.take_outbox()
                    out = core.output_state(now)
                watchdog.check(now, out["presenting"], out["stalled_s"])
                if state is not None:
                    ipc.broadcast(state)
                for ev in events:
                    ipc.broadcast(ev)
            except Exception:  # noqa: BLE001 - keep controlling even if one iteration fails
                log.exception("control loop error")
            stop.wait(CONTROL_TICK_S)

    def render_loop() -> None:
        try:
            glfw.make_context_current(window)
            glfw.swap_interval(1)  # vsync: swap_buffers returns right after a display refresh, which paces the loop
            ctx = moderngl.create_context()
            pipe = GlPipeline(ctx)
            log.info("GL: %s | %s", ctx.info["GL_RENDERER"], ctx.info["GL_VERSION"])
            log.info("renderer ready: %d scene(s), socket %s", len(core.scenes), ipc.path)
            last, failed_msg = time.monotonic(), ""
            due = last
            while not stop.is_set():
                # Wayland ignores swap intervals above 1, so the cap is a fixed schedule: one frame every
                # 1/MAX_FPS, started shortly before it is due. A schedule rather than "sleep after each swap"
                # keeps the period exact, so frames stay in step with every second display refresh.
                if (wait := due - FRAME_LEAD_S - time.monotonic()) > 0:
                    time.sleep(wait)
                now = time.monotonic()
                fw, fh = fb["size"]
                if fw <= 0 or fh <= 0:  # minimised
                    time.sleep(0.05)
                    continue
                pipe.set_size(fw, fh)
                try:
                    with lock:
                        spec = core.frame(now, (fw, fh))
                except Exception as e:  # noqa: BLE001 - a scene bug must never stop the show
                    if str(e) != failed_msg:
                        log.exception("frame evaluation failed")
                        failed_msg = str(e)
                    spec = FrameSpec(cal=core.calibration((fw, fh)), incoming=None, black=True)
                draw(pipe, spec, ctx.screen)
                swap_start = time.monotonic()
                glfw.swap_buffers(window)
                swapped = time.monotonic()
                core.frame_heartbeat = swapped
                if swapped - swap_start > SWAP_STALL_LOG_S:
                    log.warning("swap_buffers blocked for %.1f s: the compositor was not asking for frames", swapped - swap_start)
                due = max(due + 1.0 / MAX_FPS, swapped - 1.0 / MAX_FPS)  # after a stall, do not try to catch up
                dt = swapped - last
                last = swapped
                if dt > 0:
                    core.fps = 0.9 * core.fps + 0.1 / dt if core.fps else 1.0 / dt
        except Exception:  # noqa: BLE001
            log.exception("render thread failed")
            render_failed.set()
        finally:
            try:
                glfw.make_context_current(None)
            except Exception:  # noqa: BLE001
                pass

    control = threading.Thread(target=control_loop, name="control", daemon=True)
    render = threading.Thread(target=render_loop, name="render", daemon=True)
    control.start()
    render.start()

    next_probe = 0.0
    try:
        while not glfw.window_should_close(window) and not core.quit_requested and not render_failed.is_set():
            glfw.wait_events_timeout(0.05)  # returns at once when an event arrives
            now = time.monotonic()
            if now >= next_probe:
                next_probe = now + 1.0
                core.window = probe_window(glfw, window, *fb["size"])
    except KeyboardInterrupt:
        pass
    finally:
        log.info("shutting down")
        stop.set()
        render.join(2)
        control.join(2)
        midi.close()
        listen.close()
        ipc.stop()
        inhibitor.stop()
        if render.is_alive():  # still blocked in swap_buffers: tearing GLFW down under it could crash
            log.warning("render thread did not stop; exiting without terminating GLFW")
            logging.shutdown()
            os._exit(0)
        glfw.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
