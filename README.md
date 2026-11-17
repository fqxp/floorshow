# mirrorballs3

Mirrorballs drives a video projector aimed at a disco mirror ball. Bands, rings and spots are mapped onto
the calibrated sphere so they land on the ball, follow the music's beat and leave the rest of the room
dark. One operator runs the show from a laptop while the projector shows only the ball's light.

## Features

- **Two processes.** A fullscreen output window for the projector (`mirrorballs-render`) and a GTK4/libadwaita
  control window (`mirrorballs`). You can close, kill or restart the controller without touching the
  running show. While the beat comes from audio, the renderer runs a third one for the analysis
  (`mirrorballs-listen`).
- **Scenes as YAML files.** Layers (fill, band, meridian, circle, ring, annulus, textures) with blend modes,
  per-frame expressions over `beat`, `phase`, `bar` and so on, adjustable parameters, spin and transitions.
  Saved files reload into the running show. Bundled examples are in `scenes/`.
- **Beat clock.** Uses an internal BPM with tap tempo, standard MIDI clock, per-beat MIDI notes (for example
  Mixxx's "MIDI for light" script, including ALSA VirMIDI raw devices), or beat detection on audio. A
  Downbeat button (key D) marks bar starts. Key C goes round Internal, MIDI and Audio.
- **Beat detection on audio.** Listens to what an output plays (its monitor, e.g. a DJ program on the same
  laptop) or to an input, picked from a dropdown beside Downbeat that shows a level meter per channel.
  It finds tempo and beat after 4 to 12 seconds (longer when a track starts without a kick), takes over a
  new track's tempo and beat within about two seconds, follows ramps, keeps the beat through a breakdown,
  and holds the tempo while there is none. Line level works best; a microphone works in a pinch
  but less precisely. The bar position is not detected: press Downbeat on a "1".
- **Source pickers.** Hovering over Audio names the source it would listen to. When MIDI is chosen, every
  MIDI input port is shown with the beat it carries (clock or beat notes, and the tempo), so the right one
  is easy to pick.
- **Director.** Manual, random or sequence mode, switching every N bars. Transitions are crossfade, cut,
  latitude wipe, iris or flash.
- **Live controls.** Brightness, blackout, a trail that fades out whatever disappears, and scene
  parameters. MIDI notes, program changes or CC messages can trigger scene actions. Ctrl+Space starts and
  stops the show.
- **Accent colour.** One colour for the whole show, picked in the Main box from every mix of red, green
  and blue at off, half and full. Scenes that use `accent` follow it.
- **Scene previews.** Every scene card shows a still of the scene, half a beat in. Hovering plays it in
  time with the show's beat. The Scenes dialog shows them too. The previews are recorded in a separate,
  low-priority process when a scene file or the accent changes, so the running show doesn't slow down.
- **Scene filter.** Tag chips above the scene cards narrow them down; "Only accented" shows only the
  scenes that follow the accent colour.
- **Calibration wizard.** Records the ball's size and tile layout, its silhouette in the projector image,
  its axis (roll and tilt) and its tile rows, then confirms with a turning globe. The calibration remembers
  the output window it was measured in.
- **Scriptable.** `mirrorballs-ctl` controls a running renderer from a shell (scene, mode, master, trail,
  param, state, watch).
- **Robust output.** The renderer holds an idle inhibitor so the screen doesn't blank, and a watchdog
  writes thread stacks when frames stall. The controller shows a banner when that happens.

Not built yet: the motor that turns the ball.

## Installation

Needs Linux with GNOME (or another GTK4 desktop), Python 3.13 and [uv](https://docs.astral.sh/uv/). The
controller needs these system packages:

```bash
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository-2.0-dev libcairo2-dev pkg-config
```

Beat detection on audio needs PipeWire and these GStreamer packages:

```bash
sudo apt install gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
```

Then, in the repository:

```bash
uv sync --extra render --extra control --extra audio
```

```bash
uv run mirrorballs
```

**Start** in the controller launches the renderer if it isn't already running. Move the output window to
the projector (Super+Shift+←/→) and press **F11** in it. Run **Calibration** from the menu every time you
set up the rig.

For the audio clock, choose **Audio** in the clock strip and pick the source beside Downbeat.
**Preferences › Clock › Audio** holds the tempo range it looks in (drum and bass at 174 reads as 87 unless
the slowest tempo is above 87) and an audio offset, added to the beat offset to cancel the delay of the
sound path. `uv run mirrorballs-listen --list` lists the sources, and `--watch` shows their levels and the
first one's tempo once a second, without the rest of the app.

Config lives in `~/.config/mirrorballs/config.yaml`, your scenes in `~/.config/mirrorballs/scenes/*.yaml`.
Both reload while the app runs. Scene previews are cached in `~/.cache/mirrorballs/previews/`. To write
scenes, see [docs/scenes-tutorial.md](docs/scenes-tutorial.md) and [docs/scenes-reference.md](docs/scenes-reference.md).

## Development

[mise](https://mise.jdx.dev/) pins Python and uv (`mise.toml`). Install dependencies as above. Then:

```bash
uv run --extra render --extra control --extra audio pytest
```

The GL tests use a standalone EGL context and need no display. The controller's GTK tests need one and
skip without it. To run them headless, use GTK's Broadway backend (from `libgtk-4-bin`):

```bash
gtk4-broadwayd :7 &
```

```bash
env -u DISPLAY -u WAYLAND_DISPLAY GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 uv run --extra render --extra control --extra audio pytest
```

Helpers in `tools/`:

- `midi_clock.py --bpm 128 [--beat-notes]` fakes a MIDI clock or Mixxx-style beat notes.
- `beat_bench.py` scores beat trackers on what the projector would show: `synth` writes clips with exact
  beats, `run` benchmarks (precision, share of beats in time, lock time after tempo changes, cost per
  source), `annotate` makes reference beats for real recordings with Beat This!, `micify` makes a
  simulated-microphone copy. Clips live in `~/.cache/mirrorballs/beat-clips/`.
- `audio_spike.py` measures capture through PipeWire (timestamps, jitter, lost audio, CPU) and can record
  WAV clips for the benchmark.
- `screenshot_ui.py` and `screenshot_dialogs.py` render the windows in each design state.
- `swap_stall_repro.py` compares frame-loop structures.

The renderer logs to `~/.local/state/mirrorballs/` (`render.log`, plus `render.stacks` on a stall or after
`kill -USR1 <pid>`). The audio listener's messages go into `render.log` too.

`docs/design-brief.md` is the UI brief and `docs/design/` holds the UI design boards and their generator.

## How it works

- **Renderer.** Everything that makes the show happens here: the beat clock, the director (which scene
  runs and how it transitions), the scene library, config hot-reload and calibration mode. This core has
  no GL, window or device code, so it can be tested with simulated time. Around it, the main thread only
  handles window events, a separate thread renders and presents frames, and MIDI input, the audio listener
  and the control socket feed a single inbox.
- **GPU pipeline.** Each shape reduces to one primitive: a center direction on the ball with an inner and
  outer angular radius. A fragment shader maps each pixel through the calibrated silhouette to a direction
  on the ball and evaluates all shapes there. Later passes blend the two scenes of a transition and add
  the trail, then apply master brightness and the calibration overlay. Because those come after the
  trail, blackout is instant and calibration lines don't smear. Python only uploads a few numbers per
  shape per frame.
- **Beat detection.** `mirrorballs-listen` runs as a child of the renderer, only while the audio clock
  drives the show (its analysis never competes with the frame loop, and a broken audio stack can't take
  the show down). It captures through GStreamer's PipeWire source, one pipeline per source. The chosen
  source's samples go to a causal beat tracker: onsets from multi-band spectral flux, tempo from their
  autocorrelation, and beat phase from folding them at the beat period. A flip to the off-beat needs kick
  evidence that builds up, so hats and bass on the off-beat don't take the beat over. Every other source
  is only metered, inside GStreamer. Capture times come from a fit of arrival times against the sample
  count, which follows the sound card's drift against the system clock. Ten times a second the tracker's
  beat goes to the renderer, whose clock takes its phase and tempo but keeps its own bar position.
- **Scenes.** YAML is validated into a model, and every error in a file is reported at once. Expressions
  use a small whitelisted language, so scene files can be shared safely. They are compiled once and
  evaluated every frame.
- **Controller.** A thin remote. It shows the state the renderer streams to it and sends commands back. It
  is the only process that writes `config.yaml` (the renderer just reloads it), and it reads the scene
  files itself, so it still works offline. Its logic is kept apart from GTK so it can be tested.
- **Protocol.** JSON Lines over a Unix socket: requests with replies, plus events the renderer sends
  unprompted. The controller and `mirrorballs-ctl` both use it.

`demo.py`, `render.py`, `shapes.py` and `shaders/` at the top level are the original single-file prototype.
The app lives in `src/mirrorballs/`.
