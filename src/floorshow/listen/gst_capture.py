"""Audio capture through GStreamer's PipeWire elements: the list of sources, and one pipeline per captured source.

Every input (``Audio/Source``) is a "mic" source; every output (``Audio/Sink``) is a "monitor" source, captured from
what it plays (``stream.capture.sink``). The id is the PipeWire node name, which stays the same across restarts.

Tracking: ``pipewiresrc ! level ! audioconvert ! audioresample ! F32 mono 48 kHz ! audiorate ! audiobuffersplit !
appsink``. ``audiorate`` fills audio PipeWire lost with silence, so the sample count keeps matching time (its ``add``
counter is the gap figure in the health messages). ``audiobuffersplit`` hands Python 20 ms blocks instead of
PipeWire's 5 ms quanta, which halves the cost of getting them into Python (measured with tools/audio_spike.py).

Metering: ``pipewiresrc ! level ! fakesink``. ``level`` measures RMS and peak per channel and posts them on the bus
every LEVEL_INTERVAL_S; no sample is copied into Python.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import gi
import numpy as np

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import Gst  # noqa: E402

from .engine import SR  # noqa: E402

BLOCK_S = 0.02
LEVEL_INTERVAL_S = 0.1
MAX_CHANNELS = 8
GAP_TOLERANCE_NS = 2_000_000


def init() -> None:
    Gst.init(None)


def _source(device: Any) -> dict[str, Any] | None:
    props = device.get_properties()
    if props is None or not props.has_field("node.name"):
        return None
    node = props.get_value("node.name")
    ch = props.get_value("audio.channels") if props.has_field("audio.channels") else 2
    channels = max(1, min(MAX_CHANNELS, int(ch))) if str(ch).isdigit() else 2
    if device.get_device_class() == "Audio/Sink":
        return {"id": node, "name": f"Monitor of {device.get_display_name()}", "kind": "monitor", "channels": channels}
    return {"id": node, "name": device.get_display_name(), "kind": "mic", "channels": channels}


class DeviceWatch:
    """The current sources; ``on_change(sources)`` runs on the GLib main loop when one appears or goes away."""

    def __init__(self, on_change: Callable[[list[dict[str, Any]]], None]):
        self.on_change = on_change
        self.monitor = Gst.DeviceMonitor()
        self.monitor.add_filter("Audio/Source", None)
        self.monitor.add_filter("Audio/Sink", None)
        bus = self.monitor.get_bus()
        bus.add_watch(0, self._on_message)
        self.monitor.start()

    def sources(self) -> list[dict[str, Any]]:
        out = [s for d in self.monitor.get_devices() if (s := _source(d))]
        return sorted(out, key=lambda s: (s["kind"] != "monitor", s["name"]))  # outputs first: line-level audio

    def _on_message(self, _bus: Any, msg: Any) -> bool:
        if msg.type in (Gst.MessageType.DEVICE_ADDED, Gst.MessageType.DEVICE_REMOVED, Gst.MessageType.DEVICE_CHANGED):
            self.on_change(self.sources())
        return True

    def stop(self) -> None:
        self.monitor.stop()


class GstCapture:
    def __init__(self, source: dict[str, Any], mode: str, on_buffer: Callable[[np.ndarray, float], None] | None,
                 on_level: Callable[[list[float], list[float]], None], on_error: Callable[[str], None]):
        self.on_buffer = on_buffer
        self.on_level = on_level
        self.on_error = on_error
        props = "props,media.role=Music,node.dont-reconnect=true"
        if source["kind"] == "monitor":
            props += ",stream.capture.sink=true"
        name = source["id"].replace('"', "")
        # Without fixed caps GStreamer settles on the smallest format there is (8 bit, 1 Hz, mono) and the meter
        # starves; ask PipeWire for the source's own channels at the rate tracking uses.
        # More than two channels need a channel mask in the caps; 0 = unpositioned (PipeWire's AUX0, AUX1, ...).
        channels = int(source.get("channels", 2))
        mask = ",channel-mask=(bitmask)0x0" if channels > 2 else ""
        head = (f'pipewiresrc target-object="{name}" client-name=floorshow stream-properties="{props}" ! '
                f"audio/x-raw,format=F32LE,rate={SR},channels={channels}{mask},layout=interleaved ! "
                f"level interval={round(LEVEL_INTERVAL_S * 1e9)} post-messages=true ! ")
        if mode == "track":
            tail = ("audioconvert ! audioresample ! "
                    f"audio/x-raw,format=F32LE,channels=1,rate={SR},layout=interleaved ! "
                    f"audiorate name=rate tolerance={GAP_TOLERANCE_NS} skip-to-first=true silent=true ! "
                    f"audiobuffersplit output-buffer-duration={round(BLOCK_S * 1000)}/1000 ! "
                    "appsink name=sink emit-signals=true sync=false max-buffers=500 drop=false")
        else:
            tail = "fakesink sync=false async=false"
        self.pipeline = Gst.parse_launch(head + tail)
        self.rate = self.pipeline.get_by_name("rate")
        if mode == "track":
            self.pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        self.pipeline.get_bus().add_watch(0, self._on_message)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.stop()
            raise RuntimeError(f"cannot capture from {source['name']}")

    def _on_sample(self, sink: Any) -> Any:
        arrival = time.monotonic()  # first thing: this is what the sample clock is fitted to
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            data = np.frombuffer(info.data, dtype=np.float32).copy()
        finally:
            buf.unmap(info)
        if self.on_buffer is not None:
            self.on_buffer(data, arrival)
        return Gst.FlowReturn.OK

    def _on_message(self, _bus: Any, msg: Any) -> bool:
        if msg.type == Gst.MessageType.ELEMENT:
            st = msg.get_structure()
            if st is not None and st.get_name() == "level":
                self.on_level(list(st.get_value("rms")), list(st.get_value("peak")))
        elif msg.type == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            self.on_error(err.message)
        elif msg.type == Gst.MessageType.EOS:
            self.on_error("the stream ended")
        return True

    def filled_samples(self) -> int:
        return int(self.rate.get_property("add")) if self.rate is not None else 0

    def stop(self) -> None:
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_bus().remove_watch()
