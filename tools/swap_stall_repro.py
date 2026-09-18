"""Does an unfocused / covered GLFW window keep presenting frames? Compares two loop structures.

  uv run --extra render python tools/swap_stall_repro.py blocking|threaded [seconds] [--monitor N]

blocking : poll_events() and swap_buffers() on ONE thread (what the renderer used to do)
threaded : the main thread only pumps events; a second thread renders and swaps (what it does now)

Prints frames presented per second. Take focus away (click another window) while it runs and watch.
"""
import sys
import threading
import time

import glfw
import moderngl

mode = sys.argv[1] if len(sys.argv) > 1 else "blocking"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 12.0
monitor_idx = int(sys.argv[sys.argv.index("--monitor") + 1]) if "--monitor" in sys.argv else None

glfw.init()
glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
mon = glfw.get_monitors()[monitor_idx] if monitor_idx is not None else None
if mon is not None:
    vm = glfw.get_video_mode(mon)
    win = glfw.create_window(vm.size.width, vm.size.height, "repro", mon, None)
else:
    win = glfw.create_window(400, 300, "swap-stall-repro", None, None)
frames = 0
stop = threading.Event()
events = []
glfw.set_window_focus_callback(win, lambda _w, f: events.append((time.monotonic(), "focus" if f else "unfocus")))


def render():
    global frames
    ctx = moderngl.create_context()
    n = 0
    while not stop.is_set():
        ctx.clear((n % 60) / 60, 0.2, 0.4)
        n += 1
        glfw.swap_buffers(win)
        frames += 1


t0 = time.monotonic()
if mode == "threaded":
    glfw.make_context_current(None)
    th = threading.Thread(target=lambda: (glfw.make_context_current(win), glfw.swap_interval(1), render()), daemon=True)
    th.start()
else:
    glfw.make_context_current(win)
    glfw.swap_interval(1)
    ctx = moderngl.create_context()

last_frames, last_t, n = 0, t0, 0
while time.monotonic() - t0 < seconds:
    if mode == "threaded":
        glfw.wait_events_timeout(0.05)
    else:
        glfw.poll_events()
        ctx.clear((n % 60) / 60, 0.2, 0.4)
        n += 1
        glfw.swap_buffers(win)
        frames += 1
    now = time.monotonic()
    if now - last_t >= 1.0:
        print(f"{now - t0:5.1f}s  {mode:8s}  fps={(frames - last_frames) / (now - last_t):5.1f}  focused={glfw.get_window_attrib(win, glfw.FOCUSED)}", flush=True)
        last_frames, last_t = frames, now
stop.set()
print("focus events:", [(round(t - t0, 1), e) for t, e in events])
os._exit(0) if False else None
