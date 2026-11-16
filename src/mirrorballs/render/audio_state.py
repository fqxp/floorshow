"""What the renderer knows about audio: the sources mirrorballs-listen reports, their levels, the tracking of the
chosen source, and who wants previews. Pure (no process, no I/O), so the core and its tests can use it directly.

The chosen source comes from ``clock.audio_source``: a node name, matched exactly first, else as a substring of a
source's id or name (like the MIDI port). It is tracked while the audio clock drives the show, and also while a
client previews, so the tempo of a source shows before the show is switched to it.
"""

from __future__ import annotations

from typing import Any

STALE_S = 1.0  # levels and tracking older than this are not shown (the source stopped, or the listener did)


class AudioState:
    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.levels: dict[str, tuple[float, list[float], list[float]]] = {}  # id -> (time, rms_db, peak_db)
        self.estimate: dict[str, Any] | None = None  # newest estimate of the tracked source
        self.estimate_time = 0.0
        self.health: dict[str, Any] | None = None
        self.throttled = False
        self.error: str | None = None  # the newest problem reported by the listener (or about it)
        self.preview_clients: set[Any] = set()
        self.process: dict[str, Any] = {"running": False, "error": None}  # filled by the app
        self.dirty = True  # what the listener should do changed

    # --- the chosen source -----------------------------------------------------------------

    def resolve(self, wanted: str | None) -> str | None:
        """The id of the source ``wanted`` names, or None."""
        if not wanted:
            return None
        if any(s["id"] == wanted for s in self.sources):
            return wanted
        w = wanted.lower()
        return next((s["id"] for s in self.sources if w in s["id"].lower() or w in s["name"].lower()), None)

    @property
    def previewing(self) -> bool:
        return bool(self.preview_clients)

    def wants(self, clock_source: str, audio_source: str | None, min_bpm: float, max_bpm: float
              ) -> tuple[str | None, bool, float, float] | None:
        """What the listener should do: (tracked source id, meter the others, tempo range), or None when the
        listener is not needed at all. While the chosen source is unknown (the listener has not said yet, or it is
        unplugged) the substring stands in for it, so the listener starts and reports its sources."""
        needed = clock_source == "audio" or self.previewing
        if not needed:
            return None
        active = self.resolve(audio_source) or audio_source
        return active, self.previewing, min_bpm, max_bpm

    # --- messages from the listener -----------------------------------------------------------

    def on_message(self, msg: dict[str, Any], now: float) -> dict[str, Any] | None:
        """Take one listener message. Returns the estimate when it is one for the clock to use."""
        kind = msg.get("type")
        if kind == "sources":
            self.sources = list(msg.get("sources") or [])
            self.dirty = True  # the chosen source may resolve differently now
        elif kind == "level":
            self.levels[msg["source"]] = (now, list(msg.get("rms_db") or []), list(msg.get("peak_db") or []))
        elif kind == "estimate":
            self.estimate, self.estimate_time = msg, now
            return msg
        elif kind == "health":
            self.health = msg
        elif kind == "load":
            self.throttled = bool(msg.get("throttled"))
        elif kind == "error":
            name = self._name(msg.get("source"))
            self.error = f"{name}: {msg.get('message')}" if name else str(msg.get("message"))
        return None

    def preview(self, client: Any, on: bool) -> None:
        before = self.previewing
        (self.preview_clients.add if on else self.preview_clients.discard)(client)
        if self.previewing != before:
            self.dirty = True

    def _name(self, sid: str | None) -> str | None:
        return next((s["name"] for s in self.sources if s["id"] == sid), sid)

    # --- state for clients ---------------------------------------------------------------

    def state(self, now: float, audio_source: str | None, tracked: bool) -> dict[str, Any]:
        chosen = self.resolve(audio_source)
        out_sources = []
        for s in self.sources:
            item = {"id": s["id"], "name": s["name"], "kind": s["kind"], "channels": s.get("channels", 2)}
            lv = self.levels.get(s["id"])
            if lv and now - lv[0] <= STALE_S:
                item["rms_db"], item["peak_db"] = lv[1], lv[2]
            out_sources.append(item)
        tracking = None
        e = self.estimate
        if tracked and e and e.get("source") == chosen and now - self.estimate_time <= STALE_S:
            tracking = {"bpm": e["bpm"], "confidence": e["confidence"], "locked": e["locked"],
                        "anchor_time": e["anchor_time"], "anchor_beat": e["anchor_beat"]}
        health = self.health if self.health and self.health.get("source") == chosen else None
        return {
            "running": self.process.get("running", False),
            "error": self.process.get("error") or self.error,
            "source": chosen,  # None: not chosen, or not there
            "wanted": audio_source,
            "sources": out_sources,
            "tracking": tracking,  # the chosen source's beat, when it is tracked
            "health": {k: v for k, v in health.items() if k not in ("type", "source")} if health else None,
            "throttled": self.throttled,
            "previewing": self.previewing,
        }
