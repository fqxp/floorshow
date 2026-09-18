# Mirrorballs: design brief for Claude Design

Paste everything below into a new chat at claude.ai/design.

**What it is.** *Mirrorballs* is a Linux desktop app (GTK4 + libadwaita) that drives a video projector aimed at a
disco mirror ball. The operator picks animated "scenes" (light bands, rings and spots that hit the ball on the
beat). The app has a **control window** on the laptop and a separate fullscreen **output window** on the
projector, which is just black or light shapes and needs no design work.

**Who and where.** One operator, usually in a **dark room**, glancing between stage and laptop, sometimes
touching or clicking quickly mid-show. **Default to dark theme**, high contrast, big hit targets (≥ 44 px on
primary actions), minimal chrome, no reliance on hover. The design must be **implementable with libadwaita
widgets** (AdwHeaderBar, AdwPreferencesDialog with pages and groups, AdwSwitchRow / SpinRow / ComboRow /
EntryRow, AdwToast, AdwBanner, AdwStatusPage). Avoid web-only patterns (glass blur, hover cards, floating
tooltips as the only affordance). Custom widgets: beat indicator, big Start/Stop, scene cards, calibration
preview.

**Screens to design (with states)**

1. **Main control window** (≈ 960×620, min 720×520)
   - **Header bar**: app title, connection chip (`Renderer: running · 60 fps` / `Not running` /
     `Connecting…`), menu (Configuration…, Quit renderer, Shortcuts, About).
   - **Start/Stop**, the most prominent control. Three states: *Stopped* (green-ish "Start", primary), *Running*
     ("Stop", clearly different), *Starting…* (spinner). Keyboard hint on the button.
   - **Clock strip**: source chip (`MIDI · Arturia BeatStep · 124.0 BPM` / `Internal · 120 BPM` /
     `MIDI · waiting for clock (holding 118 BPM)`), tap-tempo button (internal only), and the **beat indicator**:
     a large square or circle that **turns red on the beat and fades to the background colour** until the next
     beat, plus four small pips `1 2 3 4` (downbeat emphasised) so it does not rely on colour alone.
     **Do not use red for errors in this window**; use amber for warnings and errors so red always means "beat".
   - **Scene selector**: grid of cards (name, tag chips, hotkey badge, active state, thumbnail area reserved
     for later), plus Prev / Next, a **Manual / Random** segmented control with an "every N bars" spinner,
     and a transition picker (type + length).
   - **Scene controls** panel (right or below): title "Scene controls". **Empty state**:
     "This scene has no adjustable parameters yet." Also design a populated example (slider with unit,
     colour button, switch, dropdown), because scenes will declare these.
   - **Master**: brightness slider and a **Blackout** button.
   - **States**: renderer not running, running, MIDI clock lost, scene file error (banner with file and
     message), calibration in progress (whole window desaturated and inert, with a banner "Calibration in
     progress").
2. **Configuration dialog** (AdwPreferencesDialog-style, pages):
   - *Scenes*: list with add / duplicate / rename / delete / "Open in editor" / "Reveal folder", a validity
     indicator per row, and an error detail for invalid files.
   - *Clock*: segmented **MIDI clock | Internal BPM**. MIDI shows a port dropdown with refresh; Internal shows
     a BPM number field (default 120). Also beats per bar and beat offset (ms).
   - *Output*: "Test pattern" button and a hint that the output window is moved and fullscreened via GNOME.
   - *MIDI mappings*: control-port dropdown and a mapping table (trigger, action), plus "Learn" affordance.
   - *Director*: random/sequence options.
   - *Calibration*: summary (centre, size, last calibrated), **Calibrate…** and **Reset to defaults** (confirmation, no shortcut).
   - *Ball*: diameter, tile size / row count (manual entry, used as the calibration start value), motor
     (disabled, "coming later"), plus a link to advanced calibration.
3. **Calibration window** (≈ 720×560, modal, opens over the main window)
   - Header with instruction text: "Move each line until it just touches the edge of the ball."
   - Diagram: a schematic of the ball with four lines and a live-updating fitted circle/ellipse.
   - Four **line groups** (Left, Right, Top, Bottom), each with ◀▶ or ▲▼ buttons that **show their keyboard
     shortcut on the button**, and a value readout (px and %). Below: **Move ball** (arrows, moves both lines
     of an axis) and **Grow / Shrink**. Step selector (Fine / Normal / Coarse) and toggles (Blink, Verification
     grid). Live readouts: centre, size, aspect-deviation warning (amber).
   - Footer: **Cancel** (Esc) and **Finish** (Enter). **Reset to defaults** is a button with confirmation and *no* keyboard shortcut.
   - **Stepper** at the top: ① Silhouette (the screen above) → ② Ball axis → ③ Tile rows. Only ① is needed;
     ② and ③ are optional ("Skip", "Advanced"). Design ② as a crosshair-on-ball-diagram screen (arrow buttons +
     shortcuts, "Projector is above / below the equator" toggle, numeric fallback for height and distance).
     Design ③ as offset and pitch nudge controls (↑↓ and ←→ shown on the buttons), a Zebra / Single-row toggle, and a
     read-only "rows ≈ 47.3 · tile 3.8°" readout.
   - States: default values, edited (dirty), aspect warning, renderer starting, output window not fullscreen yet (hint banner).
4. **Small pieces**: keyboard-shortcuts overlay, first-run empty state ("No scenes yet: add one"), toasts
   (`Scene reloaded`, `Saved`).

**Visual tone.** Stage-tech, calm and utilitarian, not neon. Near-black backgrounds (#101114–#1b1d22),
one restrained accent for primary actions (cool blue or teal), **red reserved exclusively for the beat**, amber
for warnings and errors, neutral greys for text. System UI font (Cantarell or Inter) with tabular numerals for
BPM and pixel values. Provide both light and dark if cheap, but dark is primary.

**Sample copy to reuse.** `Start`, `Stop`, `Renderer: running`, `MIDI · waiting for clock`, `Manual`, `Random`,
`Every 8 bars`, `Blackout`, `Calibrate…`, `Move each line until it just touches the ball's edge`.

**Deliverables.** Main window in each state, config pages, calibration window, and a component sheet (beat
indicator across the fade, scene card states, Start/Stop states) with colour and spacing tokens.
