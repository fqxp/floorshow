# mirrorballs3

> **Work in progress: the app.** The demo below is being turned into a two-process app: a
> projector output (`mirrorballs-render`) and a GTK4/libadwaita controller (`mirrorballs`).
> The controller's main window, its scenes and preferences dialogs and calibration are built; the motor is not.
> Calibration sits behind its own menu item, because it is redone every time the rig is set up, and runs as a
> wizard: the silhouette, the ball axis (roll and tilt, from where the pole eyelet appears), the tile rows
> (pitch and offset, checked with a zebra of stripes, starting from the ball's diameter and tile size) and a
> confirmation step that projects a turning globe.
> `docs/design-brief.md` is the UI brief; the design itself is the "Mirrorballs Control UI" artifact.

**Writing scenes:** [docs/scenes-tutorial.md](docs/scenes-tutorial.md) is a worked introduction,
[docs/scenes-reference.md](docs/scenes-reference.md) lists every key, variable and function.

## The app

```sh
uv sync --extra render --extra control
uv run mirrorballs                 # the controller; Start launches the renderer (detached) if none is running
uv run mirrorballs-render          # or start the renderer directly: an ordinary window "Mirrorballs Output"
uv run mirrorballs-ctl scenes      # from another shell
uv run mirrorballs-ctl scene ripples --transition crossfade --beats 2
uv run mirrorballs-ctl mode random --bars 8
uv run --extra render --extra control pytest
uv run --extra control python tools/screenshot_ui.py OUTDIR   # render the main window in each design state
```

The output is a normal window: move it to the projector with Super+Shift+←/→ and press **F11** in it (fullscreen on
the second monitor, best effort). If you prefer GNOME's own fullscreen, bind a shortcut:

```sh
gsettings set org.gnome.desktop.wm.keybindings toggle-fullscreen "['<Super>F11']"
```

Config lives in `~/.config/mirrorballs/config.yaml`, scenes in `~/.config/mirrorballs/scenes/*.yaml`
(bundled examples in `scenes/`). Both are hot-reloaded. Fake a MIDI clock with
`uv run python tools/midi_clock.py --bpm 128`, then set `clock: {source: midi, midi_port: "mirrorballs test clock"}`.

**Beat notes instead of MIDI clock.** Mixxx sends no MIDI clock. Its
[MIDI for light](https://github.com/mixxxdj/mixxx/wiki/MIDI%20clock%20output) script sends a note on every beat
instead (D, velocity 100) and the tempo as a second note (E, value = BPM - 50). Use that with
(Preferences > Clock > Beat signal > Beat notes):

```yaml
clock:
  source: midi
  midi_port: mixxx            # substring of the port name
  midi_mode: beats            # "clock" (default) = standard 24-per-beat MIDI clock
  beat_note: 50               # D; a note-on with this number is one beat
  beat_velocity: 100          # 0 = any velocity
  midi_channel: 0             # 0 = any channel
  tempo_note: 52              # E; its value + tempo_offset is the announced tempo (shown only); -1 = off
  tempo_offset: 50
```

If the beat does not arrive, check what is sent with `aseqdump -l` / `aseqdump -p <port>`, or click **Detect** next to
the beat note in Preferences > Clock: the first note that arrives fills in note, velocity and channel.

**VirMIDI.** If Mixxx's output device is a "VirMIDI" port (Mixxx offers nothing else on some systems), what it
sends appears only on the matching *raw* device, which a sequencer client cannot subscribe to. Pick the entry
with "(raw hw:1,0)" in its name, e.g. "VirMIDI 1-0 (raw hw:1,0)": the renderer then reads `/dev/snd/midiC1D0`
itself (you need read access to it, which the desktop session normally grants).

The beats set the speed and phase. A beat note does not say which beat starts a bar, so press **Downbeat** (key D,
or `mirrorballs-ctl sync`) on the first beat of a bar. Test without Mixxx: `uv run python tools/midi_clock.py --bpm 100 --beat-notes`.
The renderer also holds a GNOME idle inhibitor while it runs, so the screen does not blank mid-show. It is
released when the renderer exits, even if it is killed.

**If the output window misbehaves.** The renderer is a normal window: nothing special except that it can go
fullscreen. Its main thread only pumps window events; a separate thread renders and presents frames, so a
blocked swap can never stop the window from answering the compositor. If frames stop anyway, the controller
shows a banner, and the renderer writes every thread's Python stack to `~/.local/state/mirrorballs/render.stacks`
(also on demand: `kill -USR1 <pid>`). `render.log` records window focus/minimise events, blocked swaps and
every command with the client that sent it. `tools/swap_stall_repro.py` compares the two loop structures.

Only the projector-facing hemisphere of the ball is lit, so keep scene longitudes within about -90..90.

---

Computes the 2D image to feed a real video projector so that, once
projected onto a real physical sphere placed in its light cone, chosen
shapes appear to glow on the ball while everything else stays dark.

See `~/.claude/plans/lexical-munching-blossom.md` for the full design
(math derivation, shape model, rendering pipeline).

## How it works

- **`shapes.py`** -- shapes are defined purely as functions of direction
  from the ball's center (a lat/lon-independent unit vector). Every
  shape (filled circle, ring outline, latitude band, meridian band,
  growing/moving circle) reduces to one primitive, `Annulus`: a center
  direction plus an inner/outer angular radius, either of which can be
  a callable of time for animation.
- **`shaders/sphere.frag`** -- the actual per-pixel math, entirely on
  the GPU: pixel -> ray (from the real projector's position/FOV) ->
  ray-sphere intersection -> surface direction -> evaluate every active
  shape at that direction -> composite. A ray that misses the ball is
  just black -- that's the dark surrounding, nothing extra needed.
- **`render.py`** -- moderngl setup (fullscreen quad + the shader),
  `ProjectorConfig`/`BallConfig` for the real-world calibration
  (throw distance, lens FOV, ball radius), and the per-frame uniform
  upload. Python only ever computes a few floats per shape per frame;
  it never touches a pixel.
- **`demo.py`** -- a runnable visual sanity check: a centered circle, a
  band sweeping from the north pole to the south pole, and a static
  meridian band.

## Run

```sh
mise exec -- uv run python demo.py
```

Opens fullscreen on the second monitor if one is connected (the real
projector), otherwise a windowed preview. Esc quits.

`demo.py`'s `ProjectorConfig` uses a close-in distance (1.2m) purely so
the ball fills the preview window; swap in the real projector's
measured position and lens FOV (throw distance ~3-5m) for the actual
rig.
