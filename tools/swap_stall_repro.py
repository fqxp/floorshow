"""Does an unfocused GLFW window keep being presented? Run it, then click another window and watch.

  uv run --extra render python tools/swap_stall_repro.py MODE [seconds] [--monitor N] [--keep]

MODE
  blocking     poll_events() + swap_buffers() on one thread, vsync on
  threaded     main thread pumps events, a second thread renders + swaps, vsync on
  nonblocking  swap interval 0, frames paced by a timer (swap_buffers never waits for the compositor)

--keep       turn off GLFW's AUTO_ICONIFY (a fullscreen window is minimised when it loses focus, by default)

The whole window flips between red and blue twice a second, so you can SEE on the projector whether it is
still being updated. Once a second one line is appended to ~/.local/state/floorshow/swap_repro.log:
time, frames presented in that second, focus. Nothing else is needed: just tell me what you saw.
"""
import os
import sys
import threading
import time
from pathlib import Path

import glfw
import moderngl

mode = sys.argv[1] if len(sys.argv) > 1 else "blocking"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 30.0
monitor_idx = int(sys.argv[sys.argv.index("--monitor") + 1]) if "--monitor" in sys.argv else None
LOG = Path.home() / ".local/state/floorshow/swap_repro.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
logf = LOG.open("a", buffering=1)


def note(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} [{mode}] {text}"
    print(line, flush=True)
    logf.write(line + "\n")


keep = "--keep" in sys.argv
glfw.init()
glfw.window_hint(glfw.AUTO_ICONIFY, glfw.FALSE if keep else glfw.TRUE)
glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
mon = glfw.get_monitors()[monitor_idx] if monitor_idx is not None else None
if mon is not None:
    vm = glfw.get_video_mode(mon)
    win = glfw.create_window(vm.size.width, vm.size.height, "swap-stall-repro", mon, None)
else:
    win = glfw.create_window(500, 300, "swap-stall-repro", None, None)
note(f"started, {'fullscreen on monitor ' + str(monitor_idx) if mon else 'windowed'}, {seconds:.0f} s, "
     f"auto_iconify={glfw.get_window_attrib(win, glfw.AUTO_ICONIFY)}")

frames = 0
stop = threading.Event()
glfw.set_window_focus_callback(win, lambda _w, f: note("FOCUSED" if f else "LOST FOCUS"))
glfw.set_window_iconify_callback(win, lambda _w, i: note("iconified" if i else "restored"))


def colour(t: float):
    return (0.8, 0.05, 0.05) if int(t * 2) % 2 == 0 else (0.05, 0.05, 0.8)


def draw_and_swap(ctx):
    global frames
    ctx.clear(*colour(time.monotonic()))
    glfw.swap_buffers(win)
    frames += 1


def render_thread():
    glfw.make_context_current(win)
    glfw.swap_interval(1)
    ctx = moderngl.create_context()
    while not stop.is_set():
        draw_and_swap(ctx)


t0 = time.monotonic()
ctx = None
if mode == "threaded":
    glfw.make_context_current(None)
    threading.Thread(target=render_thread, daemon=True).start()
else:
    glfw.make_context_current(win)
    glfw.swap_interval(0 if mode == "nonblocking" else 1)
    ctx = moderngl.create_context()

last_frames, last_t, next_frame = 0, t0, t0
while time.monotonic() - t0 < seconds:
    if mode == "threaded":
        glfw.wait_events_timeout(0.05)
    else:
        glfw.poll_events()
        if mode == "nonblocking":
            next_frame += 1 / 60
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.monotonic()
        draw_and_swap(ctx)
    now = time.monotonic()
    if now - last_t >= 1.0:
        note(f"{now - t0:4.0f}s frames_this_second={frames - last_frames:3d}")
        last_frames, last_t = frames, now
stop.set()
note("done")
os._exit(0)
