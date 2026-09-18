# mirrorballs3

> **Work in progress: the app.** The demo below is being turned into a two-process app: a
> projector output (`mirrorballs-render`) and a GTK4/libadwaita controller (`mirrorballs`).
> The controller's main window, configuration dialog and calibration window (step 1, the silhouette) are built;
> calibration steps 2-3 (ball axis, tile rows) and the motor are not.
> `docs/design-brief.md` is the UI brief; the design itself is the "Mirrorballs Control UI" artifact.

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
**Safety.** Flashing light thrown around a room can harm people who are sensitive to it, so the renderer limits
it by default: if the output flashes more than 3 times a second (a flash is a pair of opposing brightness
changes of 10 % or more, measured on the ball before master brightness), it dims itself until it stops,
and the controller shows a notice. Tune or disable it in the configuration dialog (Output tab) or in
`config.yaml`:

```yaml
safety: {flash_limit: true, max_flashes_per_s: 3}
```

The renderer also holds a GNOME idle inhibitor while it runs, so the screen does not blank mid-show. It is
released when the renderer exits, even if it is killed.

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
