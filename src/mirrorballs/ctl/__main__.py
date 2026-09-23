"""mirrorballs-ctl: talk to a running renderer from a shell or a script."""

from __future__ import annotations

import argparse
import json
import sys

from ..common import paths
from .client import NotRunning, SyncClient


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mirrorballs-ctl", description="Control a running mirrorballs-render.")
    sub = ap.add_subparsers(dest="what", required=True, metavar="COMMAND")
    sub.add_parser("state", help="print the renderer state")
    sub.add_parser("scenes", help="list scene ids (marks the current one)")
    s = sub.add_parser("scene", help="switch scene")
    s.add_argument("name")
    s.add_argument("--transition", choices=["cut", "crossfade", "wipe_lat", "iris", "flash", "random"])
    s.add_argument("--beats", type=float, default=1.0, help="transition length in beats")
    for name, help_ in (("next", "next scene"), ("prev", "previous scene"), ("start", "un-blank the output"),
                        ("stop", "fade to black"), ("tap", "tap tempo (internal clock)"),
                        ("sync", "the current moment is the first beat of a bar"), ("reload", "re-read config and scenes"),
                        ("quit", "quit the renderer")):
        sub.add_parser(name, help=help_)
    m = sub.add_parser("master", help="master brightness 0..1")
    m.add_argument("value", type=float)
    tr = sub.add_parser("trail", help="how long what disappears keeps glowing, in seconds (0 = off)")
    tr.add_argument("seconds", type=float)
    mo = sub.add_parser("mode", help="scene mode")
    mo.add_argument("mode", choices=["manual", "random", "sequence"])
    g = mo.add_mutually_exclusive_group()
    g.add_argument("--bars", type=int)
    g.add_argument("--beats", type=int)
    p = sub.add_parser("param", help="set a parameter of the current scene")
    p.add_argument("name")
    p.add_argument("value")
    sub.add_parser("watch", help="print state events until interrupted")
    return ap


def _coerce(text: str):
    for conv in (int, float):
        try:
            return conv(text)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(text.lower(), text)


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        with SyncClient(paths.socket_path()) as c:
            c.request("hello")
            if a.what == "watch":
                while True:
                    print(json.dumps(c.read()), flush=True)
            if a.what == "scenes":
                st = c.request("get_state")["state"]
                for sid in st["scenes"]:
                    print(("* " if sid == st["scene"] else "  ") + sid)
                return 0
            reply = _dispatch(c, a)
    except NotRunning as e:
        print(f"mirrorballs-ctl: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    print(json.dumps(reply.get("state", reply), indent=2))
    return 0 if reply.get("ok") else 1


def _dispatch(c: SyncClient, a: argparse.Namespace) -> dict:
    w = a.what
    if w == "state":
        return c.request("get_state")
    if w == "scene":
        extra = {"transition": {"type": a.transition, "beats": a.beats}} if a.transition else {}
        return c.request("scene.set", scene=a.name, **extra)
    if w in ("next", "prev"):
        return c.request(f"scene.{w}")
    if w in ("start", "stop"):
        return c.request("running.set", running=w == "start")
    if w == "tap":
        return c.request("tap")
    if w == "sync":
        return c.request("sync_downbeat")
    if w in ("reload", "quit"):
        return c.request(w)
    if w == "master":
        return c.request("master.set", value=a.value)
    if w == "trail":
        return c.request("trail.set", seconds=a.seconds)
    if w == "mode":
        every = {"bars": a.bars} if a.bars else {"beats": a.beats} if a.beats else None
        return c.request("mode.set", mode=a.mode, **({"every": every} if every else {}))
    if w == "param":
        return c.request("param.set", name=a.name, value=_coerce(a.value))
    raise SystemExit(f"unknown command {w}")


if __name__ == "__main__":
    raise SystemExit(main())
