"""mirrorballs-render: the output process. A normal window (move it to the projector and make it
fullscreen with GNOME), vsync-driven frame loop, autonomous once started."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from queue import SimpleQueue

from ..common import paths
from .core import Core
from .dispatch import pump
from .frame import FrameSpec, draw
from .ipc_server import AlreadyRunning, IpcServer
from .midi import MidiManager

log = logging.getLogger("mirrorballs.render")
STATE_HZ = 30.0
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

    inbox: SimpleQueue = SimpleQueue()
    ipc = IpcServer(paths.socket_path(), inbox)
    try:
        ipc.start()
    except AlreadyRunning as e:
        print(f"mirrorballs-render: {e}", file=sys.stderr)
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

    ctx = moderngl.create_context()
    pipe = GlPipeline(ctx)
    log.info("GL: %s | %s", ctx.info["GL_RENDERER"], ctx.info["GL_VERSION"])

    config_path = args.config or paths.config_file()
    core = Core(config_path, [*paths.scene_dirs(), *args.scenes], now=time.monotonic())
    midi = MidiManager(inbox)
    log.info("renderer ready: %d scene(s), socket %s", len(core.scenes), ipc.path)

    next_state = 0.0
    last = time.monotonic()
    failed_msg = ""
    try:
        while not glfw.window_should_close(window) and not core.quit_requested:
            glfw.poll_events()
            now = time.monotonic()
            pump(core, inbox, ipc, now)
            core.poll_files(now)
            if core.midi_dirty:
                midi.configure(*core.midi_wants())
                core.midi_dirty = False
            midi.poll(now)
            core.midi_status = midi.status()

            fw, fh = glfw.get_framebuffer_size(window)
            if fw <= 0 or fh <= 0:  # minimised
                time.sleep(0.05)
                continue
            pipe.set_size(fw, fh)
            try:
                spec = core.frame(now, fw / fh)
            except Exception as e:  # noqa: BLE001 - a scene bug must never stop the show
                if str(e) != failed_msg:
                    log.exception("frame evaluation failed")
                    failed_msg = str(e)
                spec = FrameSpec(cal=core.calibration(fw / fh), incoming=None, black=True)
            draw(pipe, spec, ctx.screen)
            glfw.swap_buffers(window)

            dt = time.monotonic() - last
            last = time.monotonic()
            if dt > 0:
                core.fps = 0.9 * core.fps + 0.1 / dt if core.fps else 1.0 / dt
            if now >= next_state:
                ipc.broadcast(core.state(now))
                next_state = now + 1.0 / STATE_HZ
    except KeyboardInterrupt:
        pass
    finally:
        log.info("shutting down")
        midi.close()
        ipc.stop()
        glfw.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
