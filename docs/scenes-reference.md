# Scene file reference

Every key, type, variable and function a scene file may contain. For an introduction with worked
examples, read [scenes-tutorial.md](scenes-tutorial.md) first.

A scene is one YAML file in `~/.config/mirrorballs/scenes/`. Its **scene id is the filename stem**:
`ripples.yaml` is the scene `ripples`. Files are hot-reloaded: saving one applies it to the running
show within half a second.

---

## Coordinates and units

| | |
|---|---|
| `lat` | Latitude in degrees. `+90` is the north pole (the hanger eyelet), `-90` the south pole, `0` the equator. |
| `lon` | Longitude in degrees. `0` faces the projector, `+lon` goes to the **right** as you look at the ball. |
| Angles | Always degrees in scene files. |
| Colours | `"#rrggbb"`, `"#rgb"`, a named constant, `[r, g, b]` with each 0..1, or an expression returning a colour. |

**Only the projector-facing hemisphere is lit.** A shape at `lon: 150` is behind the ball and
invisible until a motor turns it. Keep longitudes within roughly `-90..90`.

Quote every expression, and quote `#` colours — in YAML an unquoted `#` starts a comment.

---

## Top level

```yaml
schema: 1              # required, must be 1
name: Bars to poles    # required, shown on the scene card
description: ...       # optional
tags: [beat, bars]     # optional, shown as chips on the card
tempo: {beats_per_bar: 4}
params: {...}
assets: {...}
transition: {in: {...}}
spin: {axis: y, deg: "..."}
layers: [...]          # required
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `schema` | int | — | File format version. Must be `1`. |
| `name` | string | — | Display name. |
| `description` | string | `""` | Free text. |
| `tags` | list of strings | `[]` | Labels shown on the scene card. The controller's tag filter offers every tag any scene has. |
| `tempo.beats_per_bar` | 1..16 | clock's value | Overrides the clock's bar length **for this scene**, which changes `bar`. |
| `params` | map | `{}` | Live controls, see [Parameters](#parameters). |
| `assets` | map | `{}` | Texture files, see [Textures](#textures). |
| `transition.in` | object | none | How this scene enters, see [Transitions](#transitions). |
| `spin` | object | none | Rotates the whole scene, see [Spin](#spin). |
| `layers` | list | — | At least one shape. Later layers draw over earlier ones. |

Unknown keys are an error, not a warning. Misspelling `radius` as `radus` fails the file rather than
silently drawing the wrong thing.

---

## Layers

Every layer is one shape. All six types reduce to the same primitive — an *annulus*, meaning a centre
direction with an inner and outer angular radius — so they cost the same to draw.

### Shape types and their required keys

| `type` | Required | Shape |
|---|---|---|
| `band` | `lat` + `width`, **or** `lat_from` + `lat_to` | A ring of latitude. `lat` is its centre, `width` its extent in degrees. |
| `meridian` | `lon`, `width` | A vertical band of longitude, pole to pole, `width` degrees across. |
| `circle` | `at`, `radius` | A filled disc of angular radius `radius` around `at`. |
| `ring` | `at`, `radius`, `width` | A circular outline at `radius` from `at`, `width` degrees thick. |
| `annulus` | `at`, `inner`, `outer` | The raw primitive: everything between `inner` and `outer` degrees from `at`. |
| `fill` | — | The whole ball. |

`at` is `{lat: ..., lon: ...}`, each of which may be an expression. Both default to `0`.

### Keys every layer accepts

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | none | A label for you; the app does not use it. |
| `repeat` | 1..64 | `1` | Draw the layer this many times, with `i` = 0..repeat-1 and `n` = repeat. |
| `enabled` | expression | `1` | `0` skips the shape this frame. |
| `color` | colour or expression | `white` | See [Colours](#colours). |
| `alpha` | expression | `1` | Clamped to 0..1. |
| `blend` | `over` \| `add` \| `multiply` \| `screen` | `over` | How the layer combines with what is under it. |
| `softness` | expression | `1` (degree) | Angular width of the fade at the shape's edge. `0` is a hard edge. |
| `texture` | object | none | See [Textures](#textures). |
| `snap` | `rows` | none | Quantise the band's edges to tile rows. **Bands only.** |
| `frame` | `ball` \| `world` | `ball` | `ball` turns with the ball's calibrated axis (and, later, its motor); `world` stays fixed in the projector image. |

`snap: rows` also forces `softness` to `0`, because a soft edge on a row boundary defeats the point.
It only does something once the tile rows are calibrated; otherwise it is ignored.

**Budget:** the sum of `repeat` over all layers may not exceed **64** shapes.

---

## Expressions

Any numeric field may be a number or a quoted expression, evaluated once per shape per frame.

```yaml
lat: 30                                   # a constant
lat: "30 * sin(beat)"                     # an expression
radius: "26 if phase < 5/17 else 7"       # conditionals work
```

The language is a restricted subset of Python: arithmetic (`+ - * / // % **`), comparisons
(`== != < <= > >=`), `and` / `or` / `not`, `a if c else b`, and calls to the functions below.
There are no variables you define, no assignment, no attribute access except `param.<name>`, no
subscripts, no lambdas and no strings (except inside `hex()`). An expression is at most 400
characters and 200 syntax nodes.

Mistakes are caught when the file loads, with the field path and a suggestion:

```
layers[1].radius: unknown name 'fase' (did you mean 'phase'?) at column 8
```

A *runtime* error — dividing by zero, `sqrt` of a negative — makes that one field `0` for that frame
and shows up in the controller rather than stopping the show.

### Variables

| Name | Meaning |
|---|---|
| `beat` | Beats since the clock started, as a float. Grows forever. |
| `phase` | Position within the current beat, 0..1. Same as `fract(beat)`. |
| `bar` | Bars since the clock started, as a float: `beat / beats_per_bar`. |
| `scene_beat` | Beats since **this scene** became the active one. |
| `time` | Seconds since the renderer started. Use it for motion that should ignore the tempo. |
| `bpm` | The current tempo. |
| `trans` | `0` → `1` while this scene is entering, `1` afterwards. |
| `tile` | Degrees of latitude per tile row. `4` until the rows are calibrated. |
| `accent` | The show's accent colour, set in the controller's Main box. White until one is picked. A scene that uses it anywhere counts as *accented* for the "Only accented" filter. |
| `i` | Index within `repeat`, `0` .. `n-1`. In a layer without `repeat` it is `0`. |
| `n` | The layer's `repeat` count. `1` without `repeat`. |
| `param.<name>` | A declared parameter's current value. |

`i` and `n` belong to a layer. `spin.deg` is evaluated once per frame, outside any layer, so using
them there is a load-time error.

### Constants

`pi`, `tau`, and the colours `red`, `green`, `blue`, `white`, `black`, `yellow`, `cyan`, `magenta`,
`orange`, `pink`, `purple`.

### Functions

| | |
|---|---|
| **Maths** | `sin(x)` `cos(x)` `tan(x)` — radians. `abs` `min(a,b)` `max(a,b)` `floor` `ceil` `round` `sqrt` `pow(a,b)` |
| **Fractions** | `fract(x)` — the part after the decimal point. `clamp(x, lo, hi)` |
| **Blending** | `mix(a, b, u)` — linear between `a` and `b`; works on two numbers or two colours. `step(edge, x)` — 0 below `edge`, 1 at or above. `smoothstep(e0, e1, x)` — a smooth 0→1 ramp between the edges. |
| **Easing** | `ease_in(u)` `ease_out(u)` `ease_in_out(u)` — all take and return 0..1, clamped. |
| **Waves** | `tri(x)` — triangle, 0 at integers, 1 at half-integers, period 1. `saw(x)` — ramp 0→1 per period (same as `fract`). `pulse(x, width)` — 1 for the first `width` of each period, else 0. |
| **Randomness** | `rand(seed)` — a fixed number in 0..1 for each whole `seed`. `noise(x)` — the same, smoothed between whole numbers. Both are deterministic: the same seed always gives the same value. |
| **Rows** | `snap_row(lat)` — the centre latitude of the tile row containing `lat`. `snap_edge(lat)` — the nearest row boundary. Both return `lat` unchanged while the rows are uncalibrated. |
| **Colour** | `rgb(r,g,b)` `hsv(h,s,v)` — `h` wraps, `s` and `v` clamp. `hex("#ff8800")` — the argument must be a literal string. |

`rand` and `noise` take the *floor* of their argument, so `rand(beat)` is one value per beat and
`rand(floor(beat) * 7 + i)` gives each repeat its own.

---

## Colours

Four ways to write one, all equivalent to the renderer:

```yaml
color: white                      # a named constant
color: "#ff8800"                  # quoted: # starts a YAML comment
color: [1.0, 0.53, 0.0]           # r, g, b, each 0..1
color: "hsv(beat * 0.1, 1, 1)"    # an expression returning a colour
```

`color: accent` (or any expression using `accent`) follows the show's accent colour.

A `[r, g, b]` list must stay within 0..1. An expression may return larger values, which survive
blending and are only clamped when the finished pixel is written — so an over-1 colour still shows
through a low `alpha` or a `multiply` underneath.

---

## Blend modes

| `blend` | Effect |
|---|---|
| `over` | Normal: the layer replaces what is under it, in proportion to `alpha`. |
| `add` | Sums the colours. Overlaps get brighter. Good for glows and sparks. |
| `multiply` | Multiplies them. Overlaps get darker; use it to cut into a layer below. |
| `screen` | Inverse multiply. Brightens like `add` but saturates towards white instead of past it. |

---

## Parameters

Declared parameters appear in the controller's **Scene controls** panel while the scene is live, and
are read in expressions as `param.<name>`.

```yaml
params:
  width:  {type: float,  default: 8, min: 1, max: 30, unit: deg, label: Width}
  rows:   {type: int,    default: 3, min: 1, max: 12}
  invert: {type: bool,   default: false}
  tint:   {type: color,  default: "#ff0044"}
  shape:  {type: choice, default: soft, choices: [soft, hard]}
```

| Key | Meaning |
|---|---|
| `type` | `float` (slider), `int` (stepper), `bool` (switch), `color` (colour button), `choice` (dropdown). |
| `default` | Starting value. Required in practice. |
| `min`, `max` | Range for `float` and `int`. |
| `unit` | Suffix shown next to the value, e.g. `deg`. |
| `label` | Display name; the key is used if absent. |
| `choices` | Required for `type: choice`. |

In expressions: numbers and bools arrive as numbers (`true` is `1`), a `color` arrives as a colour
usable in `mix(param.tint, white, phase)`. A `choice` cannot be compared in an expression — use a
`bool` when the scene has to branch.

---

## Textures

An image can modulate a shape's colour.

```yaml
assets:
  weave: ../assets/weave.jpg     # relative to this scene file
layers:
  - type: band
    lat: 0
    width: 20
    texture: {asset: weave, mapping: shape, scale: [2, 1], scroll: "beat * 0.25"}
```

| Key | Default | Meaning |
|---|---|---|
| `asset` | — | A key from `assets`. |
| `mapping` | `shape` | `shape`: u runs around the shape's centre, v across it — so the image becomes a pattern *inside* the band. `latlon`: the image is wrapped over the ball by latitude and longitude. |
| `scale` | `[1, 1]` | Repetitions in u and v. Both may be expressions. |
| `scroll` | `0` | Offset along u. An expression such as `"beat * 0.25"` makes it drift. |

Paths are relative to the scene file's own directory. A missing file is a load error.

---

## Transitions

`transition.in` sets how this scene enters **when whoever switches to it does not say otherwise**.
It beats the default configured for the show, but not an explicit choice: the scene cards and hotkeys
on the main window always send the transition picked there, so `transition.in` applies to automatic
changes (Random and Sequence), MIDI triggers, and `mirrorballs-ctl scene <id>` without
`--transition`.

```yaml
transition:
  in: {type: iris, beats: 2, ease: in_out}
```

| Key | Values | Default |
|---|---|---|
| `type` | `crossfade`, `cut`, `wipe_lat` (pole to pole), `iris` (out from the centre), `flash` (through white), `random` (one of the others, chosen per transition) | `crossfade` |
| `beats` | any number ≥ 0 | `1` |
| `ease` | `linear`, `in`, `out`, `in_out` | `in_out` |

`ease` is the one part only a scene can choose: a transition requested over the wire is always
`in_out`.

While the transition runs, `trans` climbs from 0 to 1 in the **incoming** scene, so a scene can
animate its own entrance.

---

## Spin

Rotates the entire scene about one axis.

```yaml
spin: {axis: y, deg: "fract(bar) * 360"}   # one full turn per bar
```

| Key | Values | Default |
|---|---|---|
| `axis` | `x`, `y`, `z` | `y` |
| `deg` | expression, degrees | `0` |

`y` is the ball's own axis, so spinning about it leaves latitude bands exactly where they are — only
meridians and off-axis shapes move. Layers with `frame: world` ignore the spin.

`deg` is evaluated once per frame, so it cannot use `i` or `n`.

---

## What a scene does not control

**Trail** (main window, Master panel) fades out whatever disappears, over up to two seconds. The
renderer applies it to the finished image, after the transition and before master brightness, so it
is the same for every scene and no scene can opt out. A shape that lights up is still instantly at
full brightness; only its disappearance is slowed, and a moving shape leaves a tail along its path.
It is off during calibration and the test pattern, and a blackout is still instant.

---

## Errors

A file that does not load is **not** applied; the previously loaded version of that scene keeps
running. The controller shows a banner and the Scenes dialog lists every problem in the file, each
as a path and a message:

```
layers[0]: 'ring' needs 'width'
layers[1].color: bad hex colour '#gg0000'
assets.weave: file not found: ../assets/wveae.jpg
layers: 96 shapes after 'repeat' exceeds the maximum of 64
```

All problems in a file are reported at once, not one per save.
