# 3. Audio capture through GStreamer's PipeWire source

- Status: accepted
- Date: 2026-11-16
- Code: `src/mirrorballs/listen/gst_capture.py`, spike `tools/audio_spike.py`

## Context

Beat detection needs audio from any PipeWire source: inputs, and the monitors of outputs (what a DJ program on the
same laptop plays). Several at once, for the level meters of the source picker. Each sample needs a capture time on
the monotonic clock the renderer uses, precise to about a millisecond. The machine runs PipeWire (with its
PulseAudio and JACK layers installed).

## Options considered

- **JACK-Client through PipeWire's JACK layer.** Exact frame times from the graph, one client with a port per source.
  Needs `libjack` resolved to PipeWire's copy (`pw-jack` or `LD_LIBRARY_PATH`): the default one on this machine is
  jack2's. Rejected by the project owner: no JACK.
- **SoundCard (the PulseAudio API through pipewire-pulse).** Simple API, monitors via `include_loopback`. Timestamps
  only from reported latency, the pulse layer adds latency, and it has open bugs with loopback capture (all-zero
  recordings, failing when a stream starts in silence).
- **sounddevice (PortAudio).** Debian's PortAudio only sees ALSA devices and the `pipewire`/`default` device, so it
  cannot choose a source per stream.
- **pipewire-gobject**, a native binding: describes itself as still maturing. **pipewire_python** wraps the
  `pw-cat` command, as would calling `pw-record` directly; the project owner preferred a library to subprocesses.
- **GStreamer's `pipewiresrc` through PyGObject.** Installed already (the plugin ships with PipeWire 1.4, and the
  controller uses PyGObject anyway), built on PipeWire's own stream API, `target-object` picks the source, and
  `stream.capture.sink=true` captures an output's monitor. `Gst.DeviceMonitor` lists sources and reports hotplug.

## How it was tested

`tools/audio_spike.py` captured every source at once (the built-in input and two outputs' monitors) for 10 to 60 s,
recording for every buffer its timestamp, its arrival in Python and the sample count:

- All three captured at once, monitors included, and the device monitor listed them. (Sources that appear or go
  away are handled through its messages and the engine's tests, but were not tried live.)
- PipeWire ignored the requested `node.latency`: the graph's quantum is set by other clients, so buffers came every
  5.3 ms whatever was asked. Receiving them in Python cost about 46 % of a core for three sources.
- Re-chunking to 20 ms inside GStreamer (`audiobuffersplit`) brought that to about 25 %, 50 ms to 16 %.
- A few 5 ms gaps per minute appeared while two captures ran: `audiorate` now fills lost audio with silence, so the
  sample count stays in step with time, and its counter is reported as lost audio.
- Without fixed caps, a capture that only meters settled on 8 bit at 1 Hz and the meter starved; with more than two
  channels GStreamer needs a channel mask (0, unpositioned). Both are in the pipelines now.
- End to end: a temporary null sink played real tracks; the listener locked at 145.1 to 145.4 BPM (reference
  145.2) and 109.9 (110.2), with the analysed audio 8 to 14 ms old and no audio lost.

The timestamps are the subject of ADR 4.

## Decision

Capture with `pipewiresrc` in one GStreamer pipeline per source:
`pipewiresrc ! level ! audioconvert ! audioresample ! F32 mono 48 kHz ! audiorate ! audiobuffersplit 20 ms ! appsink`
for the tracked source; `pipewiresrc ! fixed caps ! level ! fakesink` for sources that are only metered (no sample
reaches Python).

## Consequences

- No JACK, no subprocesses, no new native dependency beyond GStreamer packages most GNOME systems have.
- The system needs `gstreamer1.0-pipewire` and the base, good and bad plugin sets plus their typelibs; the README
  lists them. Without them the listener fails and the controller shows a banner with an install hint.
- PyGObject is now needed by the renderer side too (the `audio` extra).
- Python sees 20 ms blocks: a few ms more latency than the quantum, irrelevant because the tracker predicts the beat.
