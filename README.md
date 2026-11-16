# mirrorballs3

Mirrorballs drives a video projector aimed at a disco mirror ball. Bands, rings and spots are mapped onto
the calibrated sphere so they land on the ball, follow the music's beat and leave the rest of the room
dark. One operator runs the show from a laptop while the projector shows only the ball's light.

## Features

- **Two processes.** A fullscreen output window for the projector (`mirrorballs-render`) and a GTK4/libadwaita
  control window (`mirrorballs`). You can close, kill or restart the controller without touching the
  running show.
- **Scenes as YAML files.** Layers (fill, band, meridian, circle, ring, annulus, textures) with blend modes,
  per-frame expressions over `beat`, `phase`, `bar` and so on, adjustable parameters, spin and transitions.
  Saved files reload into the running show. Bundled examples are in `scenes/`.
- **Beat clock.** Uses an internal BPM with tap tempo, standard MIDI clock, or per-beat MIDI notes (for example
  Mixxx's "MIDI for light" script), including ALSA VirMIDI raw devices. A Downbeat button (key D) marks
  bar starts.
- **Director.** Manual, random or sequence mode, switching every N bars. Transitions are crossfade, cut,
  latitude wipe, iris or flash.
- **Live controls.** Brightness, blackout, a trail that fades out whatever disappears, and scene
  parameters. MIDI notes, program changes or CC messages can trigger scene actions. Ctrl+Space starts and
  stops the show.
- **Accent colour.** One colour for the whole show, picked from named colours (menu › Accent colors…) or
  freely in the Main box. Scenes that use `accent` follow it.
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

Then, in the repository:

```bash
uv sync --extra render --extra control
```

```bash
uv run mirrorballs
```

**Start** in the controller launches the renderer if it isn't already running. Move the output window to
the projector (Super+Shift+←/→) and press **F11** in it. Run **Calibration** from the menu every time you
set up the rig.

Config lives in `~/.config/mirrorballs/config.yaml`, your scenes in `~/.config/mirrorballs/scenes/*.yaml`.
Both reload while the app runs. To write scenes, see [docs/scenes-tutorial.md](docs/scenes-tutorial.md) and
[docs/scenes-reference.md](docs/scenes-reference.md).

## Development

[mise](https://mise.jdx.dev/) pins Python and uv (`mise.toml`). Install dependencies as above. Then:

```bash
uv run --extra render --extra control pytest
```

The GL tests use a standalone EGL context and need no display. The controller's GTK tests need one and
skip without it. To run them headless, use GTK's Broadway backend (from `libgtk-4-bin`):

```bash
gtk4-broadwayd :7 &
```

```bash
env -u DISPLAY -u WAYLAND_DISPLAY GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 uv run --extra render --extra control pytest
```

Helpers in `tools/`:

- `midi_clock.py --bpm 128 [--beat-notes]` fakes a MIDI clock or Mixxx-style beat notes.
- `screenshot_ui.py` and `screenshot_dialogs.py` render the windows in each design state.
- `swap_stall_repro.py` compares frame-loop structures.

The renderer logs to `~/.local/state/mirrorballs/` (`render.log`, plus `render.stacks` on a stall or after
`kill -USR1 <pid>`).

`docs/design-brief.md` is the UI brief and `docs/design/` holds the UI design boards and their generator.

## How it works

- **Renderer.** Everything that makes the show happens here: the beat clock, the director (which scene
  runs and how it transitions), the scene library, config hot-reload and calibration mode. This core has
  no GL, window or device code, so it can be tested with simulated time. Around it, the main thread only
  handles window events, a separate thread renders and presents frames, and MIDI input and the control
  socket feed a single inbox.
- **GPU pipeline.** Each shape reduces to one primitive: a center direction on the ball with an inner and
  outer angular radius. A fragment shader maps each pixel through the calibrated silhouette to a direction
  on the ball and evaluates all shapes there. Later passes blend the two scenes of a transition and add
  the trail, then apply master brightness and the calibration overlay. Because those come after the
  trail, blackout is instant and calibration lines don't smear. Python only uploads a few numbers per
  shape per frame.
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
