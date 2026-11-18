"""Main control window, built from the "Floorshow Control UI" design (Main boards).

The window never decides anything about the show: it shows the renderer's state (streamed ~30 Hz)
and sends commands. Offline it still shows the scene files, dimmed.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..common.scene import ParamDef  # noqa: E402
from . import sources_model as sm, tokens, viewmodel as vm  # noqa: E402
from .client import RendererClient  # noqa: E402
from .library import Library  # noqa: E402
from .preview_widgets import PreviewManager, PreviewPicture  # noqa: E402
from .previews import DEFAULT_TILE_DEG, OVERSAMPLE, THUMB_H, THUMB_W, Look, PreviewCache  # noqa: E402
from .source_picker import AudioSourceDropdown, HoverPreview, MidiPortDropdown  # noqa: E402
from .store import ConfigStore  # noqa: E402
from .widgets import Segmented, hbox, icon, kbd, label, push_button, set_classes, vbox  # noqa: E402

START_TIMEOUT_S = 12.0
TAP_SETTLE_S = 2.5  # save the tapped tempo this long after the last tap
BPM_HOLD_S = 0.6  # likewise for a tempo changed with the scroll wheel
EVERY_HOLD_S = 1.0  # after a click on the every-N-bars stepper, ignore the streamed value until the renderer has applied it
CLOCK_HOLD_S = 1.5  # likewise for the clock switch: the renderer has to reload the file before it reports the new source
ACCENT_HOLD_S = 0.6  # likewise for the accent colour
RIGHT_COLUMN_W = 272
MAX_CARDS_PER_ROW = 32
CONTROLS_MIN_H = 160
COUNTDOWN_W = 120
LIBRARY_CHECK_S = 1.0
RESTART_DELAY_MS = 600  # let the old renderer release its socket before starting the new one
ACCENT_PREVIEW_DELAY_MS = 1500  # remake accented previews once the accent has settled
TRAIL_MAX_S = 2.0  # the slider's top end; the protocol allows more, but a trail this long is already a smear
NO_BEAT_CHECK_MS = 8000  # after an audio source is picked, say so if it still has no beat by then
CLOCK_SOURCES = [("internal", "Internal"), ("midi", "MIDI"), ("audio", "Audio")]


def view_key(state: dict[str, Any]) -> str:
    """Everything in a state that the window shows, minus what moves every sample (the beat position, which
    the per-frame tick extrapolates itself). Equal keys mean a refresh would change nothing."""
    c = state["clock"]
    volatile = {"clock", "fps", "output", "audio", "midi_ports"}  # levels and preview beats move all the time
    rest = {k: v for k, v in state.items() if k not in volatile}
    clock = (c["source"], c.get("port"), round(c["bpm"], 1), c["beats_per_bar"], c["locked"], c.get("status"))
    return json.dumps([rest, clock, round(state.get("fps", 0)), state.get("output"), sm.stable_audio(state.get("audio")),
                       sm.stable_midi(state.get("midi_ports"))], sort_keys=True, default=str)


def param_title(text: str) -> Gtk.Label:
    lb = label(text, hexpand=True)
    lb.set_ellipsize(Pango.EllipsizeMode.END)  # a long declared label must not widen the column
    lb.set_tooltip_text(text)
    return lb


class BeatTile(Gtk.DrawingArea):
    """Small square, the size of the clock chip's text, that is red on the beat and fades into its ground
    until the next one. Red is used for nothing else in the app. Its size is in the stylesheet (em)."""

    MAX_FPS = 30  # repaints are costly (the whole window is re-rendered) and 30 per second fades smoothly enough

    def __init__(self) -> None:
        super().__init__()
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("beat-tile")
        self.intensity = 0.0
        self._painted_at = 0.0
        self.dark = True
        self.set_draw_func(self._draw)

    def set_intensity(self, value: float) -> None:
        if abs(value - self.intensity) < 0.01:
            return
        now = time.monotonic()
        if now - self._painted_at < 1 / self.MAX_FPS:
            return  # too soon; the next frame tries again
        self._painted_at = now
        self.intensity = value
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        pal = tokens.palette(self.dark)
        r, g, b = tokens.mix(pal["well"], pal["beat"], self.intensity)
        rad = min(w, h) / 4
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(r, g, b)
        cr.fill()


class Swatch(Gtk.DrawingArea):
    """A rounded square of one colour, for the accent picker. Its size is in the stylesheet (em)."""

    def __init__(self, color: str = "#ffffff") -> None:
        super().__init__()
        self.add_css_class("swatch")
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.color = color
        self.set_draw_func(self._draw)

    def set_color(self, color: str) -> None:
        if color != self.color:
            self.color = color
            self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        rad = min(w, h) / 4
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(*tokens.hex_to_rgb(self.color))
        cr.fill()


class CountdownBar(Gtk.DrawingArea):
    """Thin bar for the time left until the next automatic scene change. It shrinks towards the right
    and is empty (the ground colour) at the moment the transition starts."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(COUNTDOWN_W)
        self.set_content_height(6)
        self.set_valign(Gtk.Align.CENTER)
        self.fraction: float | None = None
        self.dark = True
        self.set_draw_func(self._draw)

    def set_fraction(self, value: float | None) -> None:
        if value is not None:
            value = round(value * COUNTDOWN_W) / COUNTDOWN_W  # whole pixels: nothing to repaint in between
        if value != self.fraction:
            self.fraction = value
            self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        if self.fraction is None:
            return
        pal = tokens.palette(self.dark)
        cr.set_source_rgb(*tokens.hex_to_rgb(pal["well"]))
        cr.rectangle(0, 0, w, h)
        cr.fill()
        left = w * self.fraction
        cr.set_source_rgb(*tokens.hex_to_rgb(pal["acc"]))
        cr.rectangle(w - left, 0, left, h)
        cr.fill()


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app: Adw.Application,
        client: RendererClient | None,
        library: Library,
        store: ConfigStore,
        launch_renderer: Any = None,
        previews: PreviewCache | None = None,
    ):
        super().__init__(application=app, title="Floorshow", default_width=960, default_height=700)
        self.set_size_request(720, 520)
        self.add_css_class("mb")
        self.client, self.library, self.store, self.launch_renderer = client, library, store, launch_renderer

        self.connected = False
        self.starting = False
        self.state: dict[str, Any] | None = None
        self.state_time = 0.0
        self._start_deadline = 0.0
        self._ui_guard = False
        self._pips: list[Gtk.Label] = []
        self._pip_current = -1
        self._cards_key: tuple | None = None
        self._tags_key: tuple | None = None
        self._tag_filter: frozenset[str] = frozenset()  # empty = All
        self._tag_buttons: dict[str | None, Gtk.ToggleButton] = {}  # None = All
        self._accent_hold = 0.0
        self._accent_target = "#ffffff"
        self._params_key: tuple | None = None
        self._banner_key: tuple | None = None
        self._param_widgets: dict[str, Any] = {}
        self._master_before_blackout = 0.85
        self._brightness_timer = 0
        self._trail_timer = 0
        self._pending_scene: str | None = None
        self._every_hold = 0.0
        self._bpm_hold = 0.0
        self._clock_hold = 0.0
        self._clock_target = "internal"
        self._midi_previewing = False  # midi.preview is on (only while the MIDI picker shows: it opens every port)
        self._no_beat_timer = 0
        self._audio_names: dict[str, str] = {}  # audio source id -> name, as last listed (kept while nothing listens)
        self._view_key: str | None = None
        self._library_check = 0.0
        self._restart_pending = False
        self._bpm_target = 0.0
        self._scroll_acc = 0.0
        self._tap_timer = 0
        self._quit_asked = False
        self._escapes: list[float] = []  # recent Escape presses (the panic blackout)
        self.preferences_dialog: Any = None
        self.scenes_dialog: Any = None
        self.calibration_window: Any = None

        cfg = store.load()
        self.cfg = cfg
        self.previews = PreviewManager(library, previews, self._preview_look, self._preview_beat) if previews else None
        self.trans_idx = max(0, next((i for i, (k, _) in enumerate(vm.TRANSITIONS) if k == cfg.director.transition.type), 0))
        self.len_idx = vm.nearest_length(cfg.director.transition.beats, cfg.clock.beats_per_bar)

        self._build()
        self._install_keys()
        self.add_tick_callback(self._on_tick)
        self.connect("close-request", self._on_close_request)
        if self.previews:
            self.connect("notify::scale-factor", lambda *_a: self.previews and self.previews.request_sync())
            self.connect("destroy", lambda *_a: self.previews and self.previews.stop())
        self.refresh()

    # --- construction --------------------------------------------------------------------

    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        self.conn_chip = self._make_chip()
        header.pack_start(self.conn_chip["box"])
        header.set_title_widget(label("Floorshow", "title-label"))
        menu = Gio.Menu()
        menu.append("Scenes…", "win.scenes")
        menu.append("Calibration…", "win.calibration")
        menu.append("Preferences…", "win.preferences")
        menu.append("Quit renderer", "win.quit-renderer")
        sim = Gio.Menu()
        sim.append("Haze simulation", "win.simulation")  # a check item: the action has a boolean state
        menu.append_section(None, sim)
        section = Gio.Menu()
        section.append("Keyboard shortcuts", "win.shortcuts")
        section.append("About Floorshow", "win.about")
        menu.append_section(None, section)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        mb.add_css_class("flat-icon")
        mb.set_tooltip_text("Main menu")
        header.pack_end(mb)
        view.add_top_bar(header)

        self.banner_box = vbox(0)
        view.add_top_bar(self.banner_box)

        self.content = hbox(12, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        view.set_content(self.content)

        left = vbox(12, hexpand=True)
        left.append(self._build_top_row())
        left.append(self._build_scenes_panel())
        self.content.append(left)

        right = vbox(12, width_request=RIGHT_COLUMN_W, hexpand=False)
        right.set_overflow(Gtk.Overflow.HIDDEN)  # whatever a scene declares, this column keeps its width
        right.append(self._build_controls_panel())
        right.append(self._build_master_panel())
        self.content.append(right)

        self._register_actions()

    def _make_chip(self) -> dict[str, Any]:
        dot = Gtk.Box()
        dot.add_css_class("dot")
        dot.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner(spinning=True, visible=False)
        text = label("")
        box = hbox(8, dot, spinner, text, valign=Gtk.Align.CENTER)
        box.add_css_class("chip")
        return {"box": box, "dot": dot, "spinner": spinner, "text": text}

    def _build_top_row(self) -> Gtk.Widget:
        self.run_btn = Gtk.Button()
        self.run_btn.add_css_class("startstop")
        self.run_btn.set_size_request(200, 92)
        self.run_btn.set_focusable(False)  # else a focused button toggles on bare Space/Return; Ctrl+Space only
        self.run_icon = icon("media-playback-start-symbolic", 26)
        self.run_spinner = Gtk.Spinner(visible=False)
        self.run_spinner.set_size_request(26, 26)
        self.run_label = label("Start", "big")
        self.run_kbd = hbox(4, kbd("Ctrl"), kbd("Space"), halign=Gtk.Align.CENTER)
        col = vbox(6, hbox(10, self.run_icon, self.run_spinner, self.run_label, halign=Gtk.Align.CENTER), self.run_kbd,
                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.run_btn.set_child(col)
        self.run_btn.connect("clicked", lambda *_: self.act_toggle_run())

        self.beat_tile = BeatTile()
        self.source_chip = label("")
        # A MIDI port name can be long ("VirMIDI 1-0 (raw hw:1,0)"); the chip shrinks instead of the window,
        # never below the width of an internal-clock reading. The full text is in the tooltip.
        self.source_chip.set_ellipsize(Pango.EllipsizeMode.END)
        self.source_chip.set_width_chars(20)  # "Internal · 120 BPM" always fits; MIDI readings truncate
        self.source_chip.set_max_width_chars(40)
        self.source_box = hbox(6, self.beat_tile, self.source_chip, halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.source_box.add_css_class("chip")
        self.source_box.add_css_class("source")
        for n in range(1, 5):
            p = label(str(n), "pip", xalign=0.5)
            p.set_size_request(26, 26)
            if n == 1:
                p.add_css_class("down")
            self._pips.append(p)
        self.pips_box = hbox(8, *self._pips, margin_start=3)
        self.tap_btn = push_button("Tap tempo", kbd_text="T")
        self.tap_btn.connect("clicked", lambda *_: self.act_tap())
        self.downbeat_btn = push_button("Downbeat")
        self.downbeat_btn.set_tooltip_text("The current moment is the first beat of a bar (D)")
        self.downbeat_btn.connect("clicked", lambda *_: self.act_downbeat())
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_bpm_scroll)
        self.source_box.add_controller(scroll)
        # The clock can change mid-show (a DJ program is started, or stops), so the switch belongs here and
        # not only in the configuration. Both write the same clock.source.
        self.clock_seg = Segmented(CLOCK_SOURCES, self.cfg.clock.source, self.set_clock_source)
        # Resting the pointer on "Audio" shows the chosen audio source's tempo, so it is clear before switching
        # whether that source has a beat at all.
        self.audio_hover = HoverPreview(self.clock_seg.buttons["audio"], self._audio_hover_text)
        # Beside Downbeat, the sources of the clock that is shown: for the audio clock the sources with level meters,
        # for the MIDI clock the ports, each with the beat it carries.
        self.audio_picker = AudioSourceDropdown(self._pick_audio)
        self.audio_picker.set_tooltip_text("Audio source: what the beat is taken from")
        self.midi_picker = MidiPortDropdown(self._pick_midi)
        self.midi_picker.set_tooltip_text("MIDI port: what the beat is taken from")
        strip = vbox(10, hbox(12, self.source_box, Gtk.Box(hexpand=True), self.clock_seg),
                     hbox(8, self.pips_box, Gtk.Box(hexpand=True), self.audio_picker, self.midi_picker,
                          self.downbeat_btn, self.tap_btn),
                     hexpand=True, valign=Gtk.Align.CENTER)
        self.clock_panel = hbox(16, strip, hexpand=True)
        self.clock_panel.add_css_class("panel")
        self.clock_panel.set_margin_top(0)
        return hbox(12, self.run_btn, self.clock_panel)

    def _build_scenes_panel(self) -> Gtk.Widget:
        self.prev_btn = push_button("Prev", "tall", kbd_text="[", icon_name="go-previous-symbolic")
        self.next_btn = push_button("Next", "tall", kbd_text="]", icon_name="go-next-symbolic")
        self.prev_btn.connect("clicked", lambda *_: self.act_step(-1))
        self.next_btn.connect("clicked", lambda *_: self.act_step(+1))
        self.countdown = CountdownBar()
        self.countdown.set_tooltip_text("Time until the next automatic scene change")
        head = hbox(8, label("Scenes", "panel-title", hexpand=True), self.countdown, self.prev_btn, self.next_btn)

        self.manual_btn = Gtk.ToggleButton(label="Manual")
        self.random_btn = Gtk.ToggleButton(label="Random")
        self.random_btn.set_group(self.manual_btn)
        self.sequence_btn = Gtk.ToggleButton(label="Sequence")
        self.sequence_btn.set_group(self.manual_btn)
        seg = hbox(2, self.manual_btn, self.random_btn, self.sequence_btn, valign=Gtk.Align.CENTER)
        seg.add_css_class("segmented")
        self.manual_btn.connect("toggled", self._on_mode_toggled)
        self.random_btn.connect("toggled", self._on_mode_toggled)
        self.sequence_btn.connect("toggled", self._on_mode_toggled)
        self.every_bars = 8
        self.every_label = label("", xalign=0.5)
        self.every_label.set_width_chars(11)  # fixed width: the stepper must not grow with its text
        minus, plus = Gtk.Button(icon_name="list-remove-symbolic"), Gtk.Button(icon_name="list-add-symbolic")
        minus.connect("clicked", lambda *_: self._set_every(self.every_bars - 1))
        plus.connect("clicked", lambda *_: self._set_every(self.every_bars + 1))
        stepper = hbox(0, minus, self.every_label, plus)
        stepper.add_css_class("stepper")
        stepper.set_size_request(150, 36)
        self.trans_dd = Gtk.DropDown.new_from_strings([n for _, n in vm.TRANSITIONS])
        self.len_dd = Gtk.DropDown.new_from_strings([n for n, _ in vm.LENGTHS])
        for dd, w in ((self.trans_dd, 140), (self.len_dd, 84)):
            dd.add_css_class("mb-dd")
            dd.set_size_request(w, 36)
        self.trans_dd.set_tooltip_text("Transition between scenes")
        self.len_dd.set_tooltip_text("Transition length")
        self.trans_dd.set_selected(self.trans_idx)
        self.len_dd.set_selected(self.len_idx)
        self.trans_dd.connect("notify::selected", self._on_transition_changed)
        self.len_dd.connect("notify::selected", self._on_transition_changed)
        # A wrap box, not a row: on a narrow window the dropdowns drop to a second line instead of
        # forcing the whole window wider.
        options = Adw.WrapBox(child_spacing=8, line_spacing=8, justify=Adw.JustifyMode.SPREAD)
        for w in (seg, stepper, self.trans_dd, self.len_dd):
            options.append(w)

        # The tag filter only changes which cards are shown (and what Prev / Next step through); the
        # director's Random and Sequence still pick from every scene.
        self.tags_wrap = Adw.WrapBox(child_spacing=6, line_spacing=6, hexpand=True)
        self.accented_check = Gtk.CheckButton(label="Only accented", valign=Gtk.Align.CENTER)
        self.accented_check.add_css_class("mb-check")
        self.accented_check.set_tooltip_text("Show only scenes that use the accent color")
        self.accented_check.connect("toggled", lambda *_: self._refilter())
        tag_row = hbox(8, self.tags_wrap, self.accented_check)

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                                # no real cap: a wider window gets more cards per row, not wider cards.
                                # Each row is still filled, so a card is at most one card's share wider.
                                max_children_per_line=MAX_CARDS_PER_ROW, column_spacing=8, row_spacing=8, valign=Gtk.Align.START)
        self.grid.add_css_class("scene-grid")
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.grid)

        self.empty_scenes = vbox(
            10,
            Gtk.Box(),  # spacer replaced below
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True,
        )
        self._fill_empty(
            self.empty_scenes, "view-grid-symbolic", "No scenes yet",
            "Add a scene to get started. Scenes are small files you can edit in any text editor.",
            [("Add scene…", self.open_scenes), ("Reveal folder", self.act_reveal_folder)],
        )
        no_match = vbox(10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
        self._fill_empty(no_match, "edit-find-symbolic", "No matching scenes", "No scene has these tags.", small=True)
        self.scene_stack = Gtk.Stack(vexpand=True)
        self.scene_stack.add_named(scroller, "grid")
        self.scene_stack.add_named(self.empty_scenes, "empty")
        self.scene_stack.add_named(no_match, "no-match")
        panel = vbox(10, head, options, tag_row, self.scene_stack, vexpand=True)
        panel.add_css_class("panel")
        self.scenes_panel = panel
        return panel

    def _fill_empty(self, box: Gtk.Box, icon_name: str, title: str, body: str, actions: list[tuple[str, Any]] | None = None,
                    small: bool = False) -> None:
        while (c := box.get_first_child()) is not None:
            box.remove(c)
        circle = Gtk.Box(halign=Gtk.Align.CENTER)
        circle.add_css_class("empty-icon")
        ic = icon(icon_name, 24)
        ic.set_hexpand(True)
        ic.set_halign(Gtk.Align.CENTER)
        circle.append(ic)
        box.append(circle)
        box.append(label(title, "empty-title", *(["small"] if small else []), xalign=0.5))
        box.append(label(body, "secondary", xalign=0.5, wrap=True))
        if actions:
            row = hbox(8, halign=Gtk.Align.CENTER, margin_top=6)
            for text, fn in actions:
                b = push_button(text, "tall")
                b.connect("clicked", lambda *_a, fn=fn: fn())
                row.append(b)
            box.append(row)

    def _build_controls_panel(self) -> Gtk.Widget:
        self.controls_title = label("", "secondary")
        self.controls_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.controls_body = vbox(14, vexpand=True)
        # The body scrolls instead of asking for room: its size must not depend on the scene, or the
        # window and both columns would change size on every scene change.
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=CONTROLS_MIN_H,
                                      propagate_natural_height=False, propagate_natural_width=False)
        scroller.set_child(self.controls_body)
        panel = vbox(12, vbox(2, label("Scene controls", "panel-title"), self.controls_title), scroller, vexpand=True)
        panel.add_css_class("panel")
        self.controls_panel = panel
        return panel

    def _build_master_panel(self) -> Gtk.Widget:
        # One button showing the accent; it opens the colours to pick from.
        self.accent_swatch = Swatch()
        self.accent_swatch.add_css_class("current")
        self.accent_label = label("")
        self.accent_btn = Gtk.MenuButton(hexpand=True)
        self.accent_btn.add_css_class("accent-btn")
        self.accent_btn.set_child(hbox(8, self.accent_swatch, self.accent_label))
        self.accent_btn.set_tooltip_text("Scenes that use the accent color follow it")
        self.accent_choices: dict[str, Gtk.Button] = {}
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        for i, (name, color) in enumerate(vm.ACCENT_COLORS):
            b = Gtk.Button(child=Swatch(color), tooltip_text=name)
            b.add_css_class("swatch-choice")
            b.connect("clicked", lambda _b, color=color: self._on_accent_chosen(color))
            grid.attach(b, i % vm.ACCENT_COLUMNS, i // vm.ACCENT_COLUMNS, 1, 1)
            self.accent_choices[color] = b
        self.accent_popover = Gtk.Popover(child=grid)
        self.accent_btn.set_popover(self.accent_popover)
        self.brightness_label = label("85 %", "secondary")
        self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness.set_draw_value(False)
        self.brightness.add_css_class("mb-scale")
        self.brightness.set_value(85)
        self.brightness.connect("value-changed", self._on_brightness_changed)
        self.blackout_btn = Gtk.ToggleButton()
        self.blackout_btn.add_css_class("mb-btn")
        self.blackout_btn.add_css_class("blackout")
        self.blackout_btn.set_child(hbox(8, label("Blackout"), kbd("Ctrl"), kbd("B"), halign=Gtk.Align.CENTER))
        self.blackout_btn.connect("toggled", self._on_blackout_toggled)
        self.trail_label = label("Off", "secondary")
        self.trail = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, TRAIL_MAX_S * 100, 5)
        self.trail.set_draw_value(False)
        self.trail.add_css_class("mb-scale")
        self.trail.set_tooltip_text("How long what disappears keeps glowing. Smooths the movement without "
                                    "softening the hit on the beat.")
        # Set before connecting: building the window must not send anything or touch config.yaml.
        self.trail.set_value(min(TRAIL_MAX_S, self.cfg.trail.seconds) * 100)  # what the renderer starts with
        self._show_trail()
        self.trail.connect("value-changed", self._on_trail_changed)
        panel = vbox(10, label("Main", "panel-title"),
                     vbox(4, label("Accent"), self.accent_btn),
                     vbox(4, hbox(0, label("Brightness", hexpand=True), self.brightness_label), self.brightness),
                     vbox(4, hbox(0, label("Trail", hexpand=True), self.trail_label), self.trail),
                     self.blackout_btn)
        panel.add_css_class("panel")
        self.master_panel = panel
        return panel

    # --- keyboard and actions ------------------------------------------------------------

    def _install_keys(self) -> None:
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # before a focused button can eat Space
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

    def _on_key(self, _c: Any, keyval: int, _code: int, mods: Gdk.ModifierType) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        if mods & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.META_MASK):
            return False
        if name == "Escape":
            if not vm.panic_press(self._escapes, time.monotonic()):
                return False
            self.run_action("blackout")
            return True
        hit = vm.key_to_action(name, ctrl, shift)
        if hit is None:
            return False
        self.run_action(*hit)
        return True

    def run_action(self, action: str, arg: Any = None) -> None:
        if self.calibrating and action not in ("shortcuts",):
            return  # inert while calibrating
        if action == "toggle-run":
            self.act_toggle_run()
        elif action == "tap":
            if self.tap_btn.get_visible():  # the tempo is the MIDI clock's to set, not ours
                self.act_tap()
        elif action == "downbeat":
            self.act_downbeat()
        elif action == "blackout":
            self.blackout_btn.set_active(not self.blackout_btn.get_active())
        elif action == "mode":
            {"random": self.random_btn, "sequence": self.sequence_btn}.get(arg, self.manual_btn).set_active(True)
        elif action == "step":
            self.act_step(arg)
        elif action == "scene-key":
            sid = vm.scene_for_key(arg, self.visible_ids(), self.cfg.bindings.keys)  # numbered as the cards show
            if sid:
                self.act_scene(sid)
        elif action == "brightness":
            self.brightness.set_value(min(100, max(0, self.brightness.get_value() + arg)))
        elif action == "preferences":
            self.win_action("preferences")
        elif action == "shortcuts":
            self.win_action("shortcuts")
        elif action == "quit":
            self.close()  # asks about the renderer first if the show is live
        elif action == "clock-source":
            self.toggle_clock_source()
        elif action == "simulation":
            self.win_action("simulation")

    def _register_actions(self) -> None:
        for name, fn in (
            ("preferences", lambda *_: self.open_preferences()),
            ("scenes", lambda *_: self.open_scenes()),
            ("calibration", lambda *_: self.open_calibration()),
            ("quit", lambda *_: self.close()),
            ("quit-renderer", lambda *_: self.act_quit_renderer()),
            ("shortcuts", lambda *_: self.show_shortcuts()),
            ("about", lambda *_: self.show_about()),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", fn)
            self.add_action(act)
        # Its state follows the renderer's: activating only asks for the change, and the next state shows it.
        sim = Gio.SimpleAction.new_stateful("simulation", None, GLib.Variant.new_boolean(False))
        sim.connect("activate", lambda a, _p: self.send("simulation.set", on=not a.get_state().get_boolean()))
        sim.set_enabled(False)
        self.add_action(sim)

    def win_action(self, name: str) -> None:
        self.lookup_action(name).activate(None)

    @property
    def calibrating(self) -> bool:
        return bool(self.connected and self.state and self.state.get("calibrating"))

    # --- commands ------------------------------------------------------------------------

    def send(self, cmd: str, **fields: Any) -> bool:
        return bool(self.client and self.client.send(cmd, **fields))

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(text))

    def transition_payload(self) -> dict[str, Any]:
        bpb = self.state["clock"]["beats_per_bar"] if self.state else self.cfg.clock.beats_per_bar
        return vm.transition_payload(vm.TRANSITIONS[self.trans_idx][0], vm.LENGTHS[self.len_idx][1], bpb)

    def act_toggle_run(self) -> None:
        if self.starting and not self.connected:
            return
        if not self.connected:
            self.start_renderer()
        else:
            self.send("running.set", running=not (self.state or {}).get("running", True))

    def start_renderer(self) -> None:
        try:
            if self.launch_renderer:
                self.launch_renderer()
        except OSError as e:
            self.toast(f"Could not start the renderer: {e}")
            return
        self.starting = True
        self._start_deadline = time.monotonic() + START_TIMEOUT_S
        self.refresh()

    def _on_bpm_scroll(self, _ctl: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        """Scroll up = faster, one BPM per wheel step. Only the internal clock has a tempo to set."""
        clock = (self.state or {}).get("clock")
        if not self.connected or self.calibrating or not clock or clock["source"] != "internal":
            return False
        self._scroll_acc += -dy
        steps = int(self._scroll_acc)
        if steps == 0:
            return True
        self._scroll_acc -= steps
        now = time.monotonic()
        base = self._bpm_target if now < self._bpm_hold else clock["bpm"]
        self._bpm_target = float(max(20, min(300, round(base) + steps)))
        self._bpm_hold = now + BPM_HOLD_S
        clock["bpm"] = self._bpm_target
        self.send("bpm.set", bpm=self._bpm_target)
        self._refresh_clock(self.state)
        self._schedule_persist_tempo()
        return True

    def set_clock_source(self, key: str) -> None:
        """Switch the beat clock, from the strip on the main window or from the MIDI-lost banner. It is a
        setting, not a command: the renderer reads clock.source from the file, so it is saved and reloaded."""
        if self._ui_guard:
            return
        try:
            self.store.update({"clock": {"source": key}})
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the clock source: {e}")
            self._sync_clock_seg()
            return
        self.cfg = self.store.load()
        self._clock_hold, self._clock_target = time.monotonic() + CLOCK_HOLD_S, key
        if self.preferences_dialog:
            self.preferences_dialog.sync_clock_source()
        self.send("reload")
        self.audio_hover.hide()
        st = self.state if self.connected else None
        if key == "internal":
            self.toast("Internal clock.")
        elif key == "midi":
            self.toast(f"MIDI clock · {self.cfg.clock.midi_port}" if self.cfg.clock.midi_port
                       else "MIDI clock · pick a port below")
        else:
            # the listener starts only now: whether there is a beat is said once it had time to find one
            holding = st["clock"]["bpm"] if st else self.cfg.clock.bpm
            self.toast(sm.audio_switch_toast(self.cfg.clock.audio_source, self._audio_name(), holding))
            self._schedule_beat_check(None)
        self._refresh_clock(st)

    def toggle_clock_source(self) -> None:
        """Key C: the next clock, Internal → MIDI → Audio → Internal."""
        keys = [k for k, _ in CLOCK_SOURCES]
        self.set_clock_source(keys[(keys.index(self.clock_seg.selected()) + 1) % len(keys)])

    def _sync_clock_seg(self) -> None:
        """Follow the renderer's clock (it is the authority)."""
        st = self.state
        if time.monotonic() < self._clock_hold:
            self.clock_seg.select(self._clock_target)  # the renderer has not reloaded yet; do not snap back
        else:
            self.clock_seg.select(st["clock"]["source"] if st else self.cfg.clock.source)

    def _shown_source(self) -> str:
        return self.clock_seg.selected()

    def _audio_hover_text(self) -> tuple[str, dict[str, Any] | None]:
        st = self.state if self.connected else None
        active = bool(st) and st["clock"]["source"] == "audio"
        return sm.hover_text(st.get("audio") if st else None, self.cfg.clock.audio_source, active, self._audio_name())

    def _audio_name(self) -> str | None:
        """The configured source's name as last seen (the listener, and so its list, only runs with the audio
        clock), or None."""
        sid = self.cfg.clock.audio_source
        return self._audio_names.get(sid) if sid else None

    def _schedule_beat_check(self, sid: str | None) -> None:
        if self._no_beat_timer:
            GLib.source_remove(self._no_beat_timer)
        self._no_beat_timer = GLib.timeout_add(NO_BEAT_CHECK_MS, self._check_beat, sid)

    def _pick_audio(self, sid: str) -> None:
        """A chip in the audio picker: listen to that source from now on."""
        if self._ui_guard:
            return
        try:
            self.store.update({"clock": {"audio_source": sid}})
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the audio source: {e}")
            return
        self.cfg = self.store.load()
        self.send("reload")
        items = {i.id: i for i in sm.audio_items((self.state or {}).get("audio"))}
        item = items.get(sid)
        self.toast(sm.audio_pick_toast(item, item.name if item else sid))
        self._schedule_beat_check(sid)

    def _check_beat(self, sid: str | None) -> bool:
        self._no_beat_timer = 0
        st = self.state if self.connected else None
        text = sm.no_beat_toast(st.get("audio") if st else None, sid)
        if text and self._shown_source() == "audio":
            self.toast(text)
        return False

    def _pick_midi(self, name: str) -> None:
        """A port chosen in the MIDI dropdown: it drives the MIDI clock from now on."""
        if self._ui_guard:
            return
        try:
            self.store.update({"clock": {"midi_port": name}})
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the MIDI port: {e}")
            return
        self.cfg = self.store.load()
        self.send("reload")
        item = next((i for i in sm.midi_items((self.state or {}).get("midi_ports")) if i.id == name), None)
        self.toast(sm.midi_pick_toast(item) if item else f"MIDI · {name}")
        if self.preferences_dialog:
            self.preferences_dialog.sync_clock_source()

    def _set_midi_preview(self, on: bool) -> None:
        """The renderer opens every MIDI input port while this is on, for nothing but the picker's beat previews, so it
        is only on while the MIDI picker is on screen."""
        if on != self._midi_previewing and self.connected:
            if self.send("midi.preview", on=on):
                self._midi_previewing = on

    def _update_pickers(self, st: dict[str, Any] | None) -> None:
        """The source dropdowns and their meters: on every state, without the window's full refresh."""
        shown = self._shown_source()
        self.midi_picker.set_visible(shown == "midi" and st is not None)
        self.audio_picker.set_visible(shown == "audio" and st is not None)
        self._set_midi_preview(shown == "midi" and st is not None)
        if st is None:
            return
        guard, self._ui_guard = self._ui_guard, True  # restored after: refresh() calls this with the guard already up
        try:
            for s in (st.get("audio") or {}).get("sources") or []:
                self._audio_names[s["id"]] = s["name"]
            if shown == "audio":
                audio = st.get("audio")
                items = sm.audio_items(audio)
                self.audio_picker.set_items(items, sm.selected_audio(audio, self.cfg.clock.audio_source),
                                            sm.audio_placeholder(items, self.cfg.clock.audio_source))
            elif shown == "midi":
                ports = st.get("midi_ports")
                items = sm.midi_items(ports)
                self.midi_picker.set_items(items, sm.selected_midi(ports, self.cfg.clock.midi_port),
                                           sm.midi_placeholder(items, self.cfg.clock.midi_port))
        finally:
            self._ui_guard = guard
        if self.audio_hover.visible:
            self.audio_hover.refresh()

    def act_downbeat(self) -> None:
        self.send("sync_downbeat")

    def act_tap(self) -> None:
        self.send("tap")
        self._schedule_persist_tempo()

    def _schedule_persist_tempo(self) -> None:
        # Once the tapping stops, remember the tempo: the design says tap tempo sets the internal BPM.
        if self._tap_timer:
            GLib.source_remove(self._tap_timer)
        self._tap_timer = GLib.timeout_add(int(TAP_SETTLE_S * 1000), self._persist_tempo)

    def _persist_tempo(self) -> bool:
        self._tap_timer = 0
        clock = (self.state or {}).get("clock")
        if not clock or clock["source"] != "internal":
            return False  # a MIDI clock owns the tempo
        bpm = round(clock["bpm"], 1)
        if abs(bpm - self.store.load().clock.bpm) >= 0.05:
            try:
                self.store.update({"clock": {"bpm": bpm}})
                self.cfg = self.store.load()
            except Exception as e:  # noqa: BLE001
                self.toast(f"Could not save the tempo: {e}")
        return False

    def act_scene(self, sid: str) -> None:
        if self.connected and sid != (self.state or {}).get("scene"):
            self.send("scene.set", scene=sid, transition=self.transition_payload())

    def act_step(self, delta: int) -> None:
        ids = self.visible_ids()
        if not ids or not self.connected:
            return
        cur = (self.state or {}).get("scene")
        i = ids.index(cur) if cur in ids else -1
        self.act_scene(ids[(i + delta) % len(ids)])

    def act_quit_renderer(self) -> None:
        if self.connected:
            self.send("quit")
        else:
            self.toast("The renderer is not running.")

    def _on_close_request(self, *_a: Any) -> bool:
        """The renderer outlives the controller by design, so closing the window while the show is live is
        ambiguous: ask. Once the show is stopped there is nothing on the projector to protect."""
        if self._quit_asked or not self.connected or not (self.state or {}).get("running"):
            return False
        self._quit_asked = True
        dlg = Adw.AlertDialog(heading="Quit the renderer too?",
                              body="The show is running. Left alone, the renderer keeps projecting without the controller.")
        dlg.add_response("keep", "Keep it running")
        dlg.add_response("quit", "Quit renderer")
        dlg.set_response_appearance("quit", Adw.ResponseAppearance.SUGGESTED)  # red is the beat's colour, never a button's
        dlg.set_default_response("quit")
        dlg.set_close_response("keep")
        dlg.connect("response", self._on_quit_response)
        dlg.present(self)
        return True  # hold the window open until the question is answered

    def _on_quit_response(self, _d: Any, response: str) -> None:
        if response == "quit":
            self.send("quit")
        self.close()

    def open_preferences(self, tab: str | None = None) -> None:
        from .preferences import PreferencesDialog

        if self.preferences_dialog is None:
            self.preferences_dialog = PreferencesDialog(self)
        if tab:
            self.preferences_dialog.show_tab(tab)
        self.preferences_dialog.present()

    def open_scenes(self) -> None:
        from .scenes_dialog import ScenesDialog

        if self.scenes_dialog is None:
            self.scenes_dialog = ScenesDialog(self)
        self.scenes_dialog.present()

    def open_calibration(self) -> None:
        from .calibration import CalibrationWindow

        if self.calibration_window is not None:
            self.calibration_window.present()
            return
        if not self.connected and not self.starting:
            self.start_renderer()  # the window shows "Starting the renderer..." until it connects
        self.calibration_window = CalibrationWindow(self)
        self.calibration_window.present()
        if self.state:
            self.calibration_window.on_state(self.state)

    def act_reveal_folder(self) -> None:
        d = self.library.dirs[-1]
        d.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(d))).launch(self, None, None)

    def act_banner(self, banner: vm.Banner) -> None:
        if banner.kind in ("midi_lost", "audio_unavailable"):
            self.set_clock_source("internal")
        elif banner.kind == "scene_error" and banner.scene_id:
            p = self.library.path_of(banner.scene_id)
            if p:
                Gtk.FileLauncher.new(Gio.File.new_for_path(str(p))).launch(self, None, None)
        elif banner.kind == "renderer_outdated":
            self._restart_pending = True
            self.send("quit")
        elif banner.kind == "config_error":
            Gtk.FileLauncher.new(Gio.File.new_for_path(str(self.store.path))).launch(self, None, None)
        elif banner.kind == "calibrating":
            if self.calibration_window:
                self.calibration_window.present()
            else:
                self.toast("Another controller is calibrating.")

    def _on_mode_toggled(self, btn: Gtk.ToggleButton) -> None:
        if self._ui_guard or not btn.get_active():
            return  # state-driven changes and the button that just turned off send nothing
        if btn is self.random_btn:
            self.send("mode.set", mode="random", every={"bars": self.every_bars})
        elif btn is self.sequence_btn:
            self.send("mode.set", mode="sequence")
        else:
            self.send("mode.set", mode="manual")

    def _set_every(self, bars: int) -> None:
        self.every_bars = max(1, min(64, bars))
        self._every_hold = time.monotonic() + EVERY_HOLD_S
        self.every_label.set_markup(f"Every <b>{self.every_bars}</b> bars")
        if self._ui_guard:
            return
        if self.random_btn.get_active():
            self.send("mode.set", mode="random", every={"bars": self.every_bars})
        elif self.sequence_btn.get_active():
            self.send("mode.set", mode="sequence", every={"bars": self.every_bars})
        try:  # remembered for the next start: this stepper is the only place that sets it
            self.store.update({"director": {"every": {"bars": self.every_bars, "beats": None}}})
            self.cfg = self.store.load()
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the setting: {e}")

    def _on_transition_changed(self, *_a: Any) -> None:
        if self._ui_guard:
            return
        self.trans_idx, self.len_idx = self.trans_dd.get_selected(), self.len_dd.get_selected()
        try:
            self.store.update({"director": {"transition": self.transition_payload()}})
            self.send("reload")
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the transition: {e}")

    def accent_hex(self) -> str:
        """The accent colour as the window shows it: just picked, else the renderer's, else the saved one."""
        st = self.state if self.connected else None
        if time.monotonic() < self._accent_hold:
            return self._accent_target
        if st and st.get("accent"):
            return vm.rgb_to_hex(st["accent"])
        return self.cfg.accent.color.lower()

    def set_accent(self, color: str) -> None:
        """Make ``color`` ("#rrggbb") the accent: live in the renderer, and saved as what it starts with."""
        color = color.lower()
        self._accent_target, self._accent_hold = color, time.monotonic() + ACCENT_HOLD_S
        self.send("accent.set", color=[round(c, 4) for c in tokens.hex_to_rgb(color)])
        try:
            self.store.update({"accent": {"color": color}})
            self.cfg = self.store.load()
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the accent color: {e}")
        self._refresh_accent()
        if self.previews:
            self.previews.request_sync(ACCENT_PREVIEW_DELAY_MS, restart=True)

    def _on_accent_chosen(self, color: str) -> None:
        self.accent_popover.popdown()
        self.set_accent(color)

    def _refresh_accent(self) -> None:
        current = self.accent_hex()
        self.accent_swatch.set_color(current)
        self.accent_label.set_text(vm.accent_name(current))
        for color, b in self.accent_choices.items():
            set_classes(b, {"chosen": color == current})

    def _on_brightness_changed(self, scale: Gtk.Scale) -> None:
        v = scale.get_value()
        self.brightness_label.set_text(f"{v:.0f} %")
        if self._ui_guard:
            return
        if self._brightness_timer:
            GLib.source_remove(self._brightness_timer)
        self._brightness_timer = GLib.timeout_add(40, self._send_brightness, v)

    def _show_trail(self) -> float:
        seconds = self.trail.get_value() / 100
        self.trail_label.set_text("Off" if seconds <= 0 else f"{seconds:.2f} s")
        return seconds

    def _on_trail_changed(self, *_a: Any) -> None:
        seconds = self._show_trail()
        if self._ui_guard:
            return
        if self._trail_timer:
            GLib.source_remove(self._trail_timer)
        self._trail_timer = GLib.timeout_add(40, self._send_trail, seconds)

    def _send_trail(self, seconds: float) -> bool:
        self._trail_timer = 0
        self.send("trail.set", seconds=seconds)
        try:  # the renderer takes it live; the file is only what it starts with next time
            self.store.update({"trail": {"seconds": round(seconds, 3)}})
            self.cfg = self.store.load()
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the setting: {e}")
        return False

    def _send_brightness(self, v: float) -> bool:
        self._brightness_timer = 0
        if self.blackout_btn.get_active():
            self._ui_guard = True
            self.blackout_btn.set_active(False)
            self._ui_guard = False
        self.send("master.set", value=round(v / 100, 3))
        return False

    def _on_blackout_toggled(self, btn: Gtk.ToggleButton) -> None:
        if self._ui_guard:
            return
        if btn.get_active():
            self._master_before_blackout = max(0.05, self.brightness.get_value() / 100)
            self.send("master.set", value=0.0)
        else:
            self.send("master.set", value=self._master_before_blackout)

    # --- state in ------------------------------------------------------------------------

    def set_connection(self, up: bool) -> None:
        self.connected = up
        self._midi_previewing = False  # a new connection starts without previews; a lost one ended them
        if up:
            self.starting = False
            # Level meters of every audio source and the tempo of the chosen one, for the picker and the hover:
            # cheap, so always on while this window is connected (the renderer ends it when we disconnect).
            self.send("audio.preview", on=True)
        else:
            self.state = None
            if self._restart_pending:
                self._restart_pending = False
                GLib.timeout_add(RESTART_DELAY_MS, lambda: self.start_renderer() or False)
        self.refresh()
        for w in (self.preferences_dialog, self.calibration_window):
            if w:
                w.on_connection(up)

    def apply_state(self, state: dict[str, Any], t: float | None = None) -> None:
        self.state = state
        if time.monotonic() < self._bpm_hold and state["clock"]["source"] == "internal":
            state["clock"]["bpm"] = self._bpm_target  # the renderer may not have applied the scroll yet
        self.state_time = t if t is not None else time.monotonic()
        was_connected = self.connected
        self.starting = False
        self.connected = True
        now = time.monotonic()
        changed = not was_connected
        if now >= self._library_check:  # a stat() per scene file: once a second is plenty
            self._library_check = now + LIBRARY_CHECK_S
            if self.library.refresh():
                self._cards_key = None
                changed = True
        if was_connected is False and self.connected:
            self.send("audio.preview", on=True)  # the first state after a (re)start, if set_connection was skipped
        key = view_key(state)
        if changed or key != self._view_key:
            self._view_key = key
            self.refresh()
        else:
            self._update_pickers(state)  # levels and preview tempos, without the full refresh
        for w in (self.preferences_dialog, self.calibration_window):
            if w:
                w.on_state(state)

    def apply_event(self, ev: dict[str, Any]) -> None:
        if self.preferences_dialog:
            self.preferences_dialog.on_event(ev)

    # --- rendering -----------------------------------------------------------------------

    def refresh(self) -> None:
        if self.starting and not self.connected and time.monotonic() > self._start_deadline:
            self.starting = False
            self.toast("The renderer did not start. See ~/.local/state/floorshow/render.log")
        st = self.state if self.connected else None
        self._ui_guard = True
        try:
            self._refresh_header_and_run(st)
            self._refresh_clock(st)
            self._refresh_scenes(st)
            self._refresh_controls(st)
            self._refresh_master(st)
            self._refresh_banners(st)
            offline = not self.connected
            live = not offline and not self.calibrating
            # Scene files exist without a renderer, so the empty state's "Reveal folder" stays usable;
            # everything that needs the renderer is dimmed and disabled instead.
            for w in (self.prev_btn, self.next_btn, self.manual_btn.get_parent(), self.every_label.get_parent(),
                      self.trans_dd, self.len_dd, self.grid):
                w.set_sensitive(live)
            set_classes(self.scenes_panel, {"disabled-area": offline and bool(self.library.ids())})
            for w in (self.controls_panel, self.master_panel):
                set_classes(w, {"disabled-area": offline})
                w.set_sensitive(live)
            self.scenes_panel.set_sensitive(not self.calibrating)
            set_classes(self.clock_panel, {"disabled-area": offline})
            self.clock_panel.set_sensitive(not self.calibrating)
            self.tap_btn.set_sensitive(self.connected and not self.calibrating)
            self.downbeat_btn.set_sensitive(self.connected and not self.calibrating)
            self.lookup_action("quit-renderer").set_enabled(self.connected)
            sim = self.lookup_action("simulation")
            sim.set_enabled(self.connected and not self.calibrating)
            simulating = bool(self.connected and st and st.get("simulation"))
            if sim.get_state().get_boolean() != simulating:
                sim.set_state(GLib.Variant.new_boolean(simulating))
            self.run_btn.set_sensitive(not self.calibrating)
        finally:
            self._ui_guard = False

    def _refresh_header_and_run(self, st: dict[str, Any] | None) -> None:
        chip = vm.renderer_chip(self.connected, self.starting, st)
        c = self.conn_chip
        c["text"].set_text(chip.text)
        c["spinner"].set_visible(chip.kind == "busy")
        c["dot"].set_visible(chip.kind != "busy")
        set_classes(c["box"], {"off": chip.kind == "off"})
        running = bool(st and st.get("running", True))
        busy = self.starting and not self.connected
        stop_mode = self.connected and running
        set_classes(self.run_btn, {"start": not stop_mode and not busy, "stop": stop_mode, "busy": busy, "on-accent": not stop_mode and not busy})
        self.run_icon.set_visible(not busy)
        self.run_spinner.set_visible(busy)
        self.run_spinner.set_spinning(busy)
        self.run_icon.set_from_icon_name("media-playback-stop-symbolic" if stop_mode else "media-playback-start-symbolic")
        self.run_label.set_text("Starting…" if busy else "Stop" if stop_mode else "Start")

    def _refresh_clock(self, st: dict[str, Any] | None) -> None:
        c = self.cfg.clock
        text, warn = vm.clock_text(st["clock"] if st else None, c.source, c.midi_port, c.bpm, st.get("midi") if st else None,
                                   c.audio_source, st.get("audio") if st else None)
        self.source_chip.set_text(text)
        source = st["clock"]["source"] if st else c.source
        internal = source == "internal"
        error = ((st or {}).get("audio") or {}).get("error") if source == "audio" else None
        follows = {"internal": "Scroll to change the tempo", "midi": "The tempo follows the MIDI clock",
                   "audio": "The tempo follows the music. Press Downbeat on the first beat of a bar."}[source]
        self.source_box.set_tooltip_text(f"{text}\n{follows}" + (f"\n{error}" if error else ""))
        self.tap_btn.set_visible(internal)  # tapping cannot move a clock that another program owns
        set_classes(self.source_box, {"warn": warn})
        set_classes(self.pips_box, {"dim": st is None})
        self._sync_clock_seg()
        self._update_pickers(st)
        if st:
            self.cfg_bpb = st["clock"]["beats_per_bar"]
            for i, p in enumerate(self._pips):
                p.set_visible(i < st["clock"]["beats_per_bar"])

    def visible_ids(self) -> list[str]:
        """The scenes the tag filter lets through, in grid order."""
        return vm.filter_scenes(self.library.scenes, self.library.ids(), self._tag_filter, self.accented_check.get_active())

    def _refresh_tags(self) -> None:
        tags = vm.all_tags(self.library.scenes)
        self._tag_filter = self._tag_filter.intersection(tags)  # a tag no scene has any more is not selected
        if tuple(tags) != self._tags_key:
            self._tags_key = tuple(tags)
            while (c := self.tags_wrap.get_first_child()) is not None:
                self.tags_wrap.remove(c)
            self._tag_buttons = {}
            for tag in (None, *tags):
                b = Gtk.ToggleButton(label="All" if tag is None else tag, valign=Gtk.Align.CENTER)
                b.add_css_class("chip-toggle")
                b.add_css_class("small")
                b.connect("toggled", lambda _b, tag=tag: self._on_tag_clicked(tag))
                self._tag_buttons[tag] = b
                self.tags_wrap.append(b)
        guard, self._ui_guard = self._ui_guard, True
        try:
            for tag, b in self._tag_buttons.items():
                b.set_active(not self._tag_filter if tag is None else tag in self._tag_filter)
        finally:
            self._ui_guard = guard

    def _on_tag_clicked(self, tag: str | None) -> None:
        if self._ui_guard:
            return
        self._tag_filter = vm.toggle_tag(self._tag_filter, tag)
        self._refilter()

    def _refilter(self) -> None:
        self._ui_guard = True  # the refresh sets the mode buttons, which must not send anything
        try:
            self._refresh_scenes(self.state if self.connected else None)
        finally:
            self._ui_guard = False

    def _refresh_scenes(self, st: dict[str, Any] | None) -> None:
        all_ids = self.library.ids()
        self._refresh_tags()
        ids = self.visible_ids()
        current = st.get("scene") if st else None
        mode = st.get("mode") if st else "manual"
        key = (tuple(ids), current, self.connected)
        if key != self._cards_key:
            self._cards_key = key
            self._rebuild_cards(ids, current)
        if self.previews:
            self.previews.request_sync()
        self.scene_stack.set_visible_child_name("grid" if ids else "no-match" if all_ids else "empty")
        self.manual_btn.set_active(mode not in ("random", "sequence"))
        self.random_btn.set_active(mode == "random")
        self.sequence_btn.set_active(mode == "sequence")
        if st and mode in ("random", "sequence") and time.monotonic() >= self._every_hold:
            self.every_bars = max(1, round(st["every_beats"] / st["clock"]["beats_per_bar"]))
        self.every_label.set_markup(f"Every <b>{self.every_bars}</b> bars")
        self.trans_dd.set_selected(self.trans_idx)
        self.len_dd.set_selected(self.len_idx)

    def _rebuild_cards(self, ids: list[str], current: str | None) -> None:
        while (c := self.grid.get_first_child()) is not None:
            self.grid.remove(c)
        keys = vm.hotkeys(ids, self.cfg.bindings.keys)  # renumbered by the filter: the first card shown is always 1
        for sid in ids:
            sc = self.library.scenes[sid]
            live = sid == current
            btn = Gtk.Button()
            thumb = Gtk.Overlay()
            thumb.add_css_class("thumb")
            if self.previews:  # the card's full width, at least THUMB_W, always 16:9
                thumb.set_child(PreviewPicture(self.previews, sid, THUMB_W, THUMB_H, hover_on=btn, frame=thumb, grow=True))
            else:
                thumb.set_size_request(THUMB_W, THUMB_H)
            if live:
                chip = label("Live", "live-chip", xalign=0.5)
                chip.set_halign(Gtk.Align.START)
                chip.set_valign(Gtk.Align.START)
                thumb.add_overlay(chip)
            name = label(sc.name or vm.display_name(sid), "name", hexpand=True)
            name.set_ellipsize(3)  # Pango.EllipsizeMode.END
            name.set_max_width_chars(14)  # natural width: the preview, not the name, sets the card's width
            top = hbox(6, name, *([kbd(keys[sid])] if sid in keys else []))
            tags = Adw.WrapBox(child_spacing=8, line_spacing=2)  # wraps, so tags never force the card wider
            for t in sc.tags[:3]:
                tags.append(label(t, "card-tag"))  # plain text: unlike the filter chips above, these are not buttons
            btn.add_css_class("scene-card")
            set_classes(btn, {"live": live})
            btn.set_child(vbox(6, thumb, top, tags))
            btn.set_size_request(-1, 112)
            btn.connect("clicked", lambda _b, sid=sid: self.act_scene(sid))
            self.grid.append(btn)

    def _refresh_controls(self, st: dict[str, Any] | None) -> None:
        sid = st.get("scene") if st else None
        sc = self.library.scenes.get(sid) if sid else None
        self.controls_title.set_text(sc.name if sc else "")
        key = (sid, tuple((k, p.type) for k, p in (sc.params.items() if sc else [])))
        if key != self._params_key:
            self._params_key = key
            self._rebuild_params(sc)
        if sc and st:
            self._update_param_values(st.get("params", {}))

    def _rebuild_params(self, sc: Any) -> None:
        while (c := self.controls_body.get_first_child()) is not None:
            self.controls_body.remove(c)
        self._param_widgets = {}
        if sc is None:
            box = vbox(10, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True)
            self._fill_empty(box, "preferences-other-symbolic", "No scene selected", "Select a scene to adjust it.", small=True)
            self.controls_body.append(box)
        elif not sc.params:
            box = vbox(10, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True)
            self._fill_empty(box, "preferences-other-symbolic", "No parameters", "This scene has no adjustable parameters yet.",
                             small=True)
            self.controls_body.append(box)
        else:
            for name, p in sc.params.items():
                self.controls_body.append(self._param_row(name, p))

    def _param_row(self, name: str, p: ParamDef) -> Gtk.Widget:
        title = p.label or vm.display_name(name)
        if p.type in ("float", "int"):
            lo, hi = (p.min if p.min is not None else 0.0), (p.max if p.max is not None else 100.0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1 if p.type == "int" else (hi - lo) / 200)
            scale.set_draw_value(False)
            scale.add_css_class("mb-scale")
            value = label("", "secondary")
            unit = f" {p.unit}" if p.unit else ""

            def changed(s: Gtk.Scale, name: str = name, value: Gtk.Label = value, unit: str = unit, t: str = p.type) -> None:
                v = s.get_value()
                value.set_text(f"{v:.0f}{unit}" if t == "int" or (hi - lo) >= 20 else f"{v:.2f}{unit}")
                if not self._ui_guard:
                    self.send("param.set", name=name, value=v)

            scale.connect("value-changed", changed)
            self._param_widgets[name] = ("scale", scale, changed)
            return vbox(4, hbox(0, param_title(title), value), scale)
        if p.type == "bool":
            sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            sw.add_css_class("mb-switch")
            sw.connect("notify::active", lambda s, _p, name=name: (not self._ui_guard) and self.send("param.set", name=name, value=s.get_active()))
            self._param_widgets[name] = ("switch", sw, None)
            return hbox(8, param_title(title), sw)
        if p.type == "choice":
            choices = p.choices or []
            dd = Gtk.DropDown.new_from_strings(choices)
            dd.add_css_class("mb-dd")
            dd.connect("notify::selected", lambda d, _p, name=name, ch=choices: (not self._ui_guard) and self.send("param.set", name=name, value=ch[d.get_selected()]))
            self._param_widgets[name] = ("choice", dd, choices)
            return vbox(4, param_title(title), dd)
        # colour
        cb = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        cb.connect("notify::rgba", lambda b, _p, name=name: (not self._ui_guard) and self.send(
            "param.set", name=name, value=[round(b.get_rgba().red, 4), round(b.get_rgba().green, 4), round(b.get_rgba().blue, 4)]))
        self._param_widgets[name] = ("color", cb, None)
        return hbox(8, param_title(title), cb)

    def _update_param_values(self, values: dict[str, Any]) -> None:
        for name, (kind, w, extra) in self._param_widgets.items():
            v = values.get(name)
            if v is None:
                continue
            if kind == "scale":
                if abs(w.get_value() - float(v)) > 1e-6 and not w.has_focus():
                    w.set_value(float(v))
                extra(w)
            elif kind == "switch":
                w.set_active(bool(v))
            elif kind == "choice" and v in extra:
                w.set_selected(extra.index(v))
            elif kind == "color":
                rgba = Gdk.RGBA()
                if isinstance(v, str):
                    rgba.parse(v)
                elif isinstance(v, (list, tuple)) and len(v) == 3:
                    rgba.red, rgba.green, rgba.blue, rgba.alpha = float(v[0]), float(v[1]), float(v[2]), 1.0
                w.set_rgba(rgba)

    def _refresh_master(self, st: dict[str, Any] | None) -> None:
        self._refresh_accent()
        if st is None:
            return
        pct = st["master"] * 100
        if not self.brightness.has_focus() and self._brightness_timer == 0 and abs(self.brightness.get_value() - pct) > 0.5:
            self.brightness.set_value(pct)
        self.brightness_label.set_text(f"{self.brightness.get_value():.0f} %")
        if st["master"] > 0.0:
            self._master_before_blackout = max(0.05, st["master"])
        self.blackout_btn.set_active(st["master"] <= 0.0)
        pct = st.get("trail_s", 0.0) * 100
        if not self.trail.has_focus() and self._trail_timer == 0 and abs(self.trail.get_value() - pct) > 0.5:
            self._ui_guard = True
            try:
                self.trail.set_value(pct)
            finally:
                self._ui_guard = False

    def _refresh_banners(self, st: dict[str, Any] | None) -> None:
        items = vm.banners(self.connected, st)
        key = tuple((b.kind, b.text) for b in items)
        if key == self._banner_key:
            return
        self._banner_key = key
        while (c := self.banner_box.get_first_child()) is not None:
            self.banner_box.remove(c)
        for b in items:
            row = hbox(10, icon("dialog-warning-symbolic", 18), label(b.text if b.kind != "scene_error" else f"{b.title} · {b.text}",
                                                                      "banner-title" if b.kind == "calibrating" else "",
                                                                      hexpand=True, wrap=True), valign=Gtk.Align.CENTER)
            row.get_first_child().add_css_class("banner-icon")
            if b.action:
                btn = push_button(b.action)
                btn.connect("clicked", lambda _b, b=b: self.act_banner(b))
                row.append(btn)
            row.add_css_class("banner")
            self.banner_box.append(row)

    # --- per-frame beat display ----------------------------------------------------------

    def _on_tick(self, _w: Gtk.Widget, _clock: Any) -> bool:
        st = self.state if self.connected else None
        if st is None:
            self.beat_tile.set_intensity(0.0)
            self._set_pip(-1)
        else:
            idx, phase = vm.beat_in_bar(st["clock"], time.monotonic() - self.state_time)
            self.beat_tile.set_intensity(tokens.beat_intensity(phase))
            self._set_pip(idx)
        now = time.monotonic()
        if self.midi_picker.get_visible():
            self.midi_picker.tick(now)
        self.audio_hover.tick(now)
        self._update_countdown(st)
        if self.starting and time.monotonic() > self._start_deadline and not self.connected:
            self.refresh()
        return True  # keep ticking

    def _preview_look(self) -> Look:
        scale = max(1, self.get_scale_factor())
        ball = self.cfg.ball
        return Look(
            width=round(THUMB_W * scale * OVERSAMPLE), height=round(THUMB_H * scale * OVERSAMPLE), accent=tokens.hex_to_rgb(self.accent_hex()),
            tile=ball.row_pitch_deg or DEFAULT_TILE_DEG, row_offset=ball.row_offset_deg if ball.row_pitch_deg else None,
            beats_per_bar=self.cfg.clock.beats_per_bar, bpm=self.cfg.clock.bpm,
        )

    def _preview_beat(self) -> float:
        """The beat a hovered preview plays at: the show's, or the configured tempo with no renderer."""
        st = self.state if self.connected else None
        if st:
            return vm.live_beat(st["clock"], time.monotonic() - self.state_time)
        return time.monotonic() * self.cfg.clock.bpm / 60.0

    def _update_countdown(self, st: dict[str, Any] | None) -> None:
        self.countdown.set_fraction(vm.change_progress(st, time.monotonic() - self.state_time) if st else None)

    def _set_pip(self, idx: int) -> None:
        if idx == self._pip_current:
            return
        self._pip_current = idx
        for i, p in enumerate(self._pips):
            set_classes(p, {"current": i == idx})

    def set_dark(self, dark: bool) -> None:
        self.countdown.dark = dark
        self.countdown.queue_draw()
        self.beat_tile.dark = dark
        self.beat_tile.queue_draw()
        self.midi_picker.set_dark(dark)
        self.audio_picker.set_dark(dark)
        self.audio_hover.set_dark(dark)

    # --- dialogs -------------------------------------------------------------------------

    def show_about(self) -> None:
        d = Adw.AboutDialog(application_name="Floorshow", version="0.1.0", developer_name="Frank Ploss",
                            comments="Beat-driven projection for a mirror ball.", license_type=Gtk.License.GPL_3_0)
        d.present(self)

    def show_shortcuts(self) -> None:
        # The calibration keys are not listed: that window shows every one of them on its own buttons.
        rows = [
            ("Main window", [("Start / Stop", ["Ctrl", "Space"]), ("Tap tempo", ["T"]), ("Downbeat", ["D"]),
                             ("Blackout", ["Ctrl", "B"]), ("Blackout in a panic", ["Esc"] * vm.PANIC_ESCAPES),
                             ("Internal / MIDI / Audio clock", ["C"]),
                             ("Scene 1–10", ["1", "…", "9", "0"]), ("Previous / next scene", ["[", "]"]),
                             ("Manual / Random / Sequence", ["M", "R", "S"]), ("Brightness", ["Ctrl", "↑↓"]),
                             ("Haze simulation", ["H"]),
                             ("Preferences", ["Ctrl", ","]), ("Shortcuts", ["Ctrl", "?"]),
                             ("Quit Floorshow", ["Ctrl", "Q"])]),
        ]
        cols = hbox(32, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        for title, items in rows:
            col = vbox(6, hexpand=True)
            col.append(label(title.upper(), "hint", xalign=0.0))
            for name, keys in items:
                col.append(hbox(8, label(name, hexpand=True), *[kbd(k) for k in keys]))
            cols.append(col)
        dlg = Adw.Dialog(title="Keyboard shortcuts", content_width=420)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(cols)
        dlg.set_child(view)
        dlg.present(self)


def open_in_editor(path: str) -> None:  # pragma: no cover - thin wrapper for the terminal fallback
    subprocess.Popen(["xdg-open", path], start_new_session=True)
