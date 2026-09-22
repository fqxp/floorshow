# Writing scenes

A scene is a small YAML file that says what lights up on the mirror ball and how it moves with the
beat. There is no code to compile and nothing to restart: save the file and the running show picks it
up within half a second.

This is a tutorial. Every example is a complete file you can copy into
`~/.config/mirrorballs/scenes/` and watch. The companion [reference](scenes-reference.md) lists every
key and function; come back to it once the ideas here make sense.

It assumes you can read a bit of code. It does not assume you know anything about graphics.

---

## 1. The smallest scene

```yaml
schema: 1
name: Everything
layers:
  - {type: fill}
```

Save that as `everything.yaml` and the whole ball lights up white. Three things are required:
`schema: 1`, a `name`, and at least one layer. The **scene's id is the filename**, so this one is
`everything`, and that is what appears in the controller and in `mirrorballs-ctl`.

Layers are shapes, drawn in order, later over earlier. `fill` is the whole ball; the rest of this
tutorial is about the more interesting five.

---

## 2. Where things are on the ball

Positions are latitude and longitude in degrees, like a globe:

- `lat: 90` is the north pole — the end with the hanger eyelet. `lat: -90` is the south pole,
  `lat: 0` the equator.
- `lon: 0` faces the projector. `+lon` goes right as you look at the ball.

One thing that trips everybody up on their first scene: **only the half of the ball facing the
projector can be lit.** Light aimed at the far side never gets there. So keep longitudes roughly
within `-90` to `+90`; anything beyond that is invisible until the ball turns.

A band around the equator, twenty degrees tall:

```yaml
schema: 1
name: Equator
layers:
  - {type: band, lat: 0, width: 20, color: cyan}
```

and a vertical stripe facing the projector:

```yaml
schema: 1
name: Stripe
layers:
  - {type: meridian, lon: 0, width: 10, color: orange}
```

---

## 3. Making it move: expressions

Any number in a scene can be an expression instead — a quoted string, evaluated fresh for every
frame. That is the whole animation system; there is nothing else.

```yaml
schema: 1
name: Bobbing band
layers:
  - {type: band, lat: "30 * sin(time)", width: 15, color: white}
```

`time` is seconds since the renderer started, so the band drifts up and down once every ~6 seconds,
ignoring the music entirely.

Quote expressions. YAML would otherwise try to interpret them, and an unquoted `#` starts a comment,
which is why colours are written `"#ff8800"`.

### The clock variables

These are what make a scene beat-driven. The renderer samples them once per frame, so every layer in
a frame sees exactly the same values.

| | |
|---|---|
| `beat` | Beats since the clock started: `0, 1, 2, …` as a float, forever climbing. |
| `phase` | Where you are inside the current beat, from `0` to `1`. |
| `bar` | Bars since the start, a float. `bar` is just `beat / 4` at four beats per bar. |
| `bpm` | The current tempo. |
| `time` | Seconds, for movement that should ignore the tempo. |
| `scene_beat` | Beats since this scene came on screen. |
| `trans` | `0` → `1` while the scene is entering; `1` once it has arrived. |

The set is deliberately small. Anything else you derive: the beat *number* is `floor(beat)`, the
position within a bar is `fract(bar)`, the second half of a beat is `phase > 0.5`.

Here is a band that jumps from pole to pole on every beat:

```yaml
schema: 1
name: Pole to pole
layers:
  - {type: band, lat: "-90 + 180 * phase", width: 12, color: white}
```

`phase` runs 0→1 each beat, so `lat` sweeps -90→+90 and snaps back. Swap `phase` for `fract(bar)`
and it takes a whole bar instead.

### Easing

A linear sweep looks mechanical. The easing functions all take 0..1 and return 0..1:

```yaml
schema: 1
name: Eased sweep
layers:
  - {type: band, lat: "-90 + 180 * ease_out(phase)", width: 12, color: white}
```

`ease_out` starts fast and settles — which reads as a hit on the beat. `ease_in` does the opposite,
and `ease_in_out` is smooth at both ends.

---

## 4. Colour

Four ways to say the same orange:

```yaml
color: orange
color: "#ff8800"
color: [1.0, 0.53, 0.0]
color: "rgb(1, 0.53, 0)"
```

Because colours can be expressions, they can move too. `mix(a, b, u)` blends two of them, and `hsv`
walks the colour wheel:

```yaml
schema: 1
name: Colour walk
layers:
  - {type: band, lat: 0, width: 30, color: "mix(red, blue, phase)"}
  - {type: band, lat: 50, width: 10, color: "hsv(bar * 0.2, 1, 1)"}
```

The first band crossfades red to blue within every beat. The second cycles the whole hue wheel every
five bars, because `hsv` wraps its hue at 1.

`alpha` fades a layer in and out. `smoothstep(a, b, x)` is the tool for that — it is 0 below `a`,
1 above `b` and a smooth ramp between:

```yaml
schema: 1
name: Flash and fade
layers:
  - type: circle
    at: {lat: 0, lon: 0}
    radius: 25
    color: white
    alpha: "1 - smoothstep(0.0, 0.6, phase)"
```

A disc that is full brightness on the beat and gone by 60% through it.

---

## 5. Many shapes at once: `repeat`

`repeat: 4` draws a layer four times. Each copy sees `i` (0, 1, 2, 3) and `n` (4), so the copies can
differ:

```yaml
schema: 1
name: Four rings
layers:
  - type: ring
    repeat: 4
    at: {lat: 90, lon: 0}
    radius: "fract(phase + i/n) * 90"
    width: 6
    color: "hsv(i/n, 1, 1)"
    blend: add
```

Four rings expand out from the north pole, evenly spaced in time because each one's progress is
offset by `i/n`, each a different hue. This "offset by `i/n`" trick is the workhorse of the whole
language — it turns one animation into an evenly staggered chorus.

The classic two-bars-leaving-the-equator uses `i` for direction instead:

```yaml
schema: 1
name: Bars to poles
layers:
  - type: band
    repeat: 2
    lat: "(1 - 2*i) * 90 * ease_out(phase)"
    width: "8 * (1 - 0.5*phase)"
    color: "mix(red, green, phase)"
    alpha: "1 - smoothstep(0.7, 1.0, phase)"
```

`(1 - 2*i)` is `+1` for `i=0` and `-1` for `i=1`, so one band goes north and the other south. They
narrow as they go and fade out over the last third of the beat.

The budget is **64 shapes**, counting every repeat.

---

## 6. Blend modes

`blend` decides how a layer combines with what is underneath:

- `over` (the default) paints on top,
- `add` sums the light, so crossings get brighter,
- `multiply` darkens, so a layer can carve into the one below,
- `screen` brightens like `add` but eases towards white instead of blowing past it.

```yaml
schema: 1
name: Crossing beams
layers:
  - {type: meridian, lon: "-60 + 120 * fract(bar)", width: 8, color: cyan, blend: add}
  - {type: band, lat: "40 * sin(bar * pi)", width: 8, color: magenta, blend: add}
```

Two beams sweeping across each other. Where they cross, `add` makes the intersection glow white.

On a mirror ball `add` is almost always what you want for anything that overlaps: the ball is made of
mirrors, and light really does add up.

---

## 7. Steps, not slides

Continuous motion is easy; the interesting rhythms come from things that jump. `floor` and `%` do
most of that work:

```yaml
schema: 1
name: Kick spot
layers:
  - type: circle
    at: {lat: "20 * sin(beat * 0.5)", lon: "(floor(beat) % 5) * 30 - 60"}
    radius: "26 if phase < 5/17 else 7"
    color: white
    softness: 1.5
```

Two ideas here:

`(floor(beat) % 5) * 30 - 60` takes the beat *number*, wraps it to 0..4, and turns that into a
longitude of -60, -30, 0, 30 or 60. The spot hops to a new column on each beat and repeats every five
beats — an odd number, so it drifts against a four-beat bar instead of locking to it.

`"26 if phase < 5/17 else 7"` is a conditional. The spot is large for the first 5/17 of each beat and
small for the rest. Reach for conditionals whenever you would otherwise fight with a smooth curve to
get a hard edge.

### Randomness you can rely on

`rand(seed)` returns a fixed number in 0..1 for each whole-number seed. Same seed, same value,
always — which is what lets a scene be a pure function of the clock and still look random:

```yaml
schema: 1
name: Sparkle
layers:
  - type: circle
    repeat: 12
    at: {lat: "rand(floor(beat)*12 + i) * 140 - 70",
         lon: "rand(floor(beat)*12 + i + 99) * 160 - 80"}
    radius: 2.5
    alpha: "1 - phase"
    color: white
    blend: add
```

Twelve sparks. The seed `floor(beat)*12 + i` changes on every beat and differs per spark, so they
jump to new places on each beat and fade out over it. The `+ 99` gives longitude a different sequence
from latitude — reuse the same seed for both and every spark sits on a diagonal.

`noise(x)` is the smooth cousin: it interpolates between the same fixed values, for drifting rather
than jumping.

---

## 8. Making the scene adjustable

Declare parameters and they appear in the controller's **Scene controls** panel while the scene is
live, so you can tune it mid-show:

```yaml
schema: 1
name: Adjustable band
params:
  width: {type: float, default: 12, min: 2, max: 40, unit: deg, label: Width}
  tint:  {type: color, default: "#33ddaa"}
  spin:  {type: bool, default: true}
layers:
  - type: band
    lat: "30 * sin(time) * (1 if param.spin else 0)"
    width: "param.width"
    color: "mix(param.tint, white, phase)"
```

Read them as `param.<name>`. A `bool` arrives as 1 or 0, so it multiplies nicely; a `color` can be
mixed like any other colour.

There are also `int` (a stepper) and `choice` (a dropdown). A `choice` is a string, and expressions
have no strings, so it cannot be tested in an expression — when a scene needs to branch, use a
`bool`.

---

## 9. Using the tile rows

If you ran the tile-rows step of calibration, the app knows how tall one row of mirror tiles is, and
two things become available.

`tile` is that height in degrees (4° until you calibrate, which is about right for a 30 cm ball).
Sizing things in tiles instead of degrees makes a scene look the same on a different ball:

```yaml
schema: 1
name: Three rows
layers:
  - {type: band, lat: 0, width: "3 * tile", color: white}
```

`snap: rows` quantises a band's edges to real row boundaries, so it lights whole rings of tiles
instead of cutting some in half. It only applies to bands, and it turns off edge softness, since a
soft edge is exactly what you are trying to avoid:

```yaml
schema: 1
name: Row stepper
layers:
  - type: band
    lat: "snap_row(-60 + 120 * fract(bar))"
    width: "3 * tile"
    color: "hsv(bar * 0.13, 1, 1)"
    snap: rows
```

`snap_row(lat)` moves a latitude to the centre of the row it falls in, so the band climbs one row at
a time rather than sliding. Without calibration both are no-ops and the band just slides — the scene
still works, it is only less crisp.

---

## 10. Entrances and spin

`transition.in` says how the scene should arrive:

```yaml
schema: 1
name: Iris in
transition:
  in: {type: iris, beats: 2, ease: in_out}
layers:
  - {type: fill, color: "hsv(bar * 0.1, 1, 1)"}
```

While it arrives, `trans` climbs from 0 to 1, so the scene can animate its own entrance — multiply a
radius by `trans` and the shape grows into place.

Note that this is the scene's *preference*. Clicking a scene on the main window sends the transition
picked there, which wins; `transition.in` applies when the change comes from Random or Sequence mode,
a MIDI trigger, or `mirrorballs-ctl`.

`spin` turns the whole scene:

```yaml
schema: 1
name: Ripples
tempo: {beats_per_bar: 4}
transition:
  in: {type: iris, beats: 2, ease: in_out}
spin: {axis: y, deg: "fract(bar) * 360"}
layers:
  - type: ring
    repeat: 4
    at: {lat: 90, lon: 0}
    radius: "fract(phase + i/n) * 90 * trans"
    width: "3 + 4 * (1 - fract(phase + i/n))"
    color: "hsv((beat + i/n) * 0.1, 1, 1)"
    blend: add
```

One full turn per bar about `y`, the ball's own axis. Watch what that does — and does not — do:
latitude bands are unchanged by a spin about the pole, because a ring of latitude maps onto itself.
Only meridians and off-axis shapes visibly move. That is not a limitation of the app, it is what
would happen to a real ball on a real motor, which is exactly why scenes written latitude-first keep
working when the motor arrives.

---

## 11. Textures

An image file can modulate a shape's colour:

```yaml
schema: 1
name: Woven band
assets:
  weave: ../assets/weave.jpg
layers:
  - type: band
    lat: 0
    width: 25
    color: white
    texture: {asset: weave, mapping: shape, scale: [2, 1], scroll: "beat * 0.25"}
```

`mapping: shape` wraps the image *inside* the band — one axis runs around it, the other across — so a
photo becomes a pattern in the stripe. `mapping: latlon` wraps it over the ball like a globe texture
instead. `scroll` slides it along; driving that from `beat` makes the pattern travel in time.

Paths in `assets` are relative to the scene file itself.

---

## 12. When something is wrong

Scene files are checked when they load, and the checker reports **every** problem in the file at
once, each with the path to the field:

```
layers[1].radius: unknown name 'fase' (did you mean 'phase'?) at column 8
layers[2]: 'ring' needs 'width'
assets.weave: file not found: ../assets/wveae.jpg
```

You will see those in the Scenes dialog, and the controller shows a banner. A file that fails to load
is not applied — the last version that did load keeps running, so a typo mid-show is harmless.

Errors that only appear while running — dividing by zero, `sqrt` of a negative — make that one field
`0` for that frame and are reported too, rather than stopping anything.

A few things the checker deliberately rejects, so nobody's scene file can do something surprising on
your machine: attribute access other than `param.<name>`, subscripts, lambdas, comprehensions,
imports and strings. If you find yourself wanting them, the answer is usually `repeat` and `i`.

---

## 13. Ideas that are worth knowing

**Scenes are pure functions of the clock.** Nothing carries over from the previous frame. Two
consequences: a scene can start on any beat and look right immediately, and the same scene at the
same beat always looks the same. It also means trails and particles are not expressible — that would
need state, which the language does not have.

**Everything is one primitive.** A band, a meridian, a ring and a circle are all an *annulus*: a
centre direction plus an inner and outer angular radius. A band is an annulus around the north pole;
a meridian is an annulus of 90° radius around a point on the equator. Once you see it, `annulus` with
raw `at` / `inner` / `outer` becomes a useful escape hatch.

**The smallest feature that reads is about one tile.** On a 30 cm ball with 10 mm tiles that is
roughly 4°. Shapes thinner than that do not become finer, they just get dimmer and flicker as the
ball turns.

**Odd numbers keep things alive.** A pattern that repeats every 5 or 7 beats against a 4-beat bar
takes 20 or 28 beats to come back round, so it reads as composed rather than looped.

---

## Where to go next

- The [reference](scenes-reference.md) has every key, variable and function.
- The bundled examples in `scenes/` are the five scenes this tutorial draws from, in their final
  form: `bars-to-poles`, `kick-spot`, `ripples`, `row-stepper`, `sweep-and-sparkle`.
- To test a scene without a projector, run the renderer in a window: `uv run mirrorballs-render`, and
  switch to your scene with `uv run mirrorballs-ctl scene <id>`. Fake a tempo with
  `uv run python tools/midi_clock.py --bpm 128` if you have no music running.
