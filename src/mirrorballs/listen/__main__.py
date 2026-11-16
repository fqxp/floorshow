"""mirrorballs-listen: beat detection on audio sources, as its own process (its analysis never competes with the
renderer's frame loop for the GIL, and a broken audio stack cannot take the show down).

    mirrorballs-listen --list               # the sources
    mirrorballs-listen --watch [SUBSTR...]  # every (matching) source, a line per second: tempo, confidence, health
    mirrorballs-listen                      # serve: JSON lines, commands on stdin, messages on stdout

Serving, stdin takes ``{"cmd": "configure", "active": <source id or null>, "scan": <bool>}`` and ``{"cmd": "quit"}``;
end of input (the renderer went away) quits too. stdout carries the messages described in ``engine``.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from typing import Any

from gi.repository import GLib

from . import gst_capture
from .engine import Listener


def _serve() -> int:
    out_lock = threading.Lock()

    def emit(msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":"), default=_plain)
        with out_lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                GLib.idle_add(loop.quit)

    loop = GLib.MainLoop()
    listener = Listener(gst_capture.GstCapture, emit)
    watch = gst_capture.DeviceWatch(listener.set_sources)
    listener.set_sources(watch.sources())

    def command(line: str) -> bool:
        try:
            msg = json.loads(line)
            if msg.get("cmd") == "configure":
                listener.configure(msg.get("active"), bool(msg.get("scan")))
            elif msg.get("cmd") == "quit":
                loop.quit()
            else:
                emit({"type": "error", "source": None, "message": f"unknown command: {line.strip()[:80]}"})
        except (ValueError, AttributeError) as e:
            emit({"type": "error", "source": None, "message": f"bad command: {e}"})
        return False

    def read_stdin() -> None:
        for line in sys.stdin:
            if line.strip():
                GLib.idle_add(command, line)
        GLib.idle_add(loop.quit)  # end of input: the renderer is gone

    threading.Thread(target=read_stdin, name="stdin", daemon=True).start()
    GLib.timeout_add(1000, lambda: listener.check_load() or True)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    listener.close()
    watch.stop()
    return 0


def _watch(only: list[str]) -> int:
    loop = GLib.MainLoop()
    latest: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def emit(msg: dict[str, Any]) -> None:
        if msg["type"] in ("estimate", "health"):
            with lock:
                latest.setdefault(msg["source"], {})[msg["type"]] = msg
        elif msg["type"] != "sources":
            print(json.dumps(msg), file=sys.stderr)

    listener = Listener(gst_capture.GstCapture, emit)
    watch = gst_capture.DeviceWatch(lambda s: listener.set_sources(_match(s, only)))
    sources = _match(watch.sources(), only)
    if not sources:
        print("no matching source", file=sys.stderr)
        return 1
    listener.set_sources(sources)
    listener.configure(sources[0]["id"], scan=True)
    names = {s["id"]: s["name"] for s in sources}

    def show() -> bool:
        with lock:
            rows = dict(latest)
        print(f"{'source':40} {'BPM':>7} {'conf':>5} {'lock':>4} {'dBFS':>6} {'rtf':>6} {'age ms':>6} {'gap ms':>6} ok")
        for sid, m in rows.items():
            e, h = m.get("estimate", {}), m.get("health", {})
            print(f"{names.get(sid, sid)[:40]:40} {e.get('bpm', 0):7.2f} {e.get('confidence', 0):5.2f} "
                  f"{'yes' if e.get('locked') else 'no':>4} {e.get('level_db', -99):6.1f} {h.get('rtf', 0):6.4f} "
                  f"{h.get('age_ms', 0):6.1f} {h.get('gap_ms', 0):6.1f} {'yes' if h.get('ok') else 'NO'}")
        print(flush=True)
        listener.check_load()
        return True

    GLib.timeout_add(1000, show)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    listener.close()
    watch.stop()
    return 0


def _plain(x: Any) -> Any:
    """numpy scalars (a fit's slope, a comparison of them) as plain numbers, for json."""
    if hasattr(x, "item"):
        return x.item()
    raise TypeError(f"{type(x).__name__} is not JSON serializable")


def _match(sources: list[dict[str, Any]], only: list[str]) -> list[dict[str, Any]]:
    if not only:
        return sources
    return [s for s in sources if any(o.lower() in (s["id"] + " " + s["name"]).lower() for o in only)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mirrorballs-listen", description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="list the audio sources and exit")
    ap.add_argument("--watch", nargs="*", metavar="SUBSTR", help="show tempo and health of (matching) sources")
    args = ap.parse_args(argv)
    gst_capture.init()
    if args.list:
        for s in gst_capture.DeviceWatch(lambda _s: None).sources():
            print(f"{s['kind']:8} {s['name']:44} {s['id']}")
        return 0
    if args.watch is not None:
        return _watch(args.watch)
    return _serve()


if __name__ == "__main__":
    sys.exit(main())
