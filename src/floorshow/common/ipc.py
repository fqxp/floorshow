"""JSON Lines framing over a stream socket: one JSON object per line, UTF-8."""

from __future__ import annotations

import json
from typing import Any

MAX_LINE = 1 << 20


def encode(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n"


class LineDecoder:
    """Feed raw bytes, get complete decoded messages. Malformed lines are returned as
    ``{"_error": "..."}`` so one bad client message cannot break the connection."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buf += data
        out: list[dict[str, Any]] = []
        while (i := self._buf.find(b"\n")) >= 0:
            line = bytes(self._buf[:i])
            del self._buf[: i + 1]
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError as e:
                out.append({"_error": f"invalid JSON: {e}"})
                continue
            out.append(msg if isinstance(msg, dict) else {"_error": "message must be a JSON object"})
        if len(self._buf) > MAX_LINE:
            self._buf.clear()
            out.append({"_error": "line too long"})
        return out
