"""Main control window, built from the "Mirrorballs Control UI" design (Main boards).

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
from . import tokens, viewmodel as vm  # noqa: E402
from .client import RendererClient  # noqa: E402
from .library import Library  # noqa: E402
from .store import ConfigStore  # noqa: E402
from .widgets import hbox, icon, kbd, label, push_button, set_classes, vbox  # noqa: E402

START_TIMEOUT_S = 12.0
TAP_SETTLE_S = 2.5  # save the tapped tempo this long after the last tap
BPM_HOLD_S = 0.6  # likewise for a tempo changed with the scroll wheel
EVERY_HOLD_S = 1.0  # after a click on the every-N-bars stepper, ignore the streamed value until the renderer has applied it
RIGHT_COLUMN_W = 272
CONTROLS_MIN_H = 160
COUNTDOWN_W = 120
LIBRARY_CHECK_S = 1.0
RESTART_DELAY_MS = 600  # let the old renderer release its socket before starting the new one


def view_key(state: dict[str, Any]) -> str:
    """Everything in a state that the window shows, minus what moves every sample (the beat position, which
    the per-frame tick extrapolates itself). Equal keys mean a refresh would change nothing."""
    c = state["clock"]
    volatile = {"clock", "fps", "output"}
    rest = {k: v for k, v in state.items() if k not in volatile}
    clock = (c["source"], c.get("port"), round(c["bpm"], 1), c["beats_per_bar"], c["locked"], c.get("status"))
    return json.dumps([rest, clock, round(state.get("fps", 0)), state.get("output")], sort_keys=True, default=str)


def param_title(text: str) -> Gtk.Label:
    lb = label(text, hexpand=True)
    lb.set_ellipsize(Pango.EllipsizeMode.END)  # a long declared label must not widen the column
    lb.set_tooltip_text(text)
    return lb


class BeatTile(Gtk.DrawingArea):
    """68x68 tile that is red on the beat and fades into its ground until the next one. Red is used
    for nothing else in the app."""

    MAX_FPS = 30  # repaints are costly (the whole window is re-rendered) and 30 per second fades smoothly enough

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(68)
        self.set_content_height(68)
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
        rad = 14
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(r, g, b)
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
    ):
        super().__init__(application=app, title="Mirrorballs", default_width=960, default_height=700)
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
        self._params_key: tuple | None = None
        self._banner_key: tuple | None = None
        self._param_widgets: dict[str, Any] = {}
        self._master_before_blackout = 0.85
        self._brightness_timer = 0
        self._pending_scene: str | None = None
        self._every_hold = 0.0
        self._bpm_hold = 0.0
        self._view_key: str | None = None
        self._library_check = 0.0
        self._restart_pending = False
        self._bpm_target = 0.0
        self._scroll_acc = 0.0
        self._tap_timer = 0
        self.config_dialog: Any = None
        self.calibration_dialog: Any = None
        self.calibration_window: Any = None

        cfg = store.load()
        self.cfg = cfg
        self.trans_idx = max(0, next((i for i, (k, _) in enumerate(vm.TRANSITIONS) if k == cfg.director.transition.type), 0))
        self.len_idx = vm.nearest_length(cfg.director.transition.beats, cfg.clock.beats_per_bar)

        self._build()
        self._install_keys()
        self.add_tick_callback(self._on_tick)
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
        header.set_title_widget(label("Mirrorballs", "title-label"))
        menu = Gio.Menu()
        menu.append("Calibration…", "win.calibration")
        menu.append("Configuration…", "win.config")
        menu.append("Quit renderer", "win.quit-renderer")
        section = Gio.Menu()
        section.append("Keyboard shortcuts", "win.shortcuts")
        section.append("About Mirrorballs", "win.about")
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
        self.run_icon = icon("media-playback-start-symbolic", 26)
        self.run_spinner = Gtk.Spinner(visible=False)
        self.run_spinner.set_size_request(26, 26)
        self.run_label = label("Start", "big")
        self.run_kbd = kbd("Space")
        col = vbox(6, hbox(10, self.run_icon, self.run_spinner, self.run_label, halign=Gtk.Align.CENTER), self.run_kbd,
                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.run_btn.set_child(col)
        self.run_btn.connect("clicked", lambda *_: self.act_toggle_run())

        self.beat_tile = BeatTile()
        self.source_chip = label("", "chip", "source")
        self.source_chip.set_halign(Gtk.Align.START)
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
        self.source_chip.add_controller(scroll)
        self.indicator_sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.cfg.ui.beat_indicator)
        self.indicator_sw.add_css_class("mb-switch")
        self.indicator_sw.set_tooltip_text("Show the beat and bar indicator")
        self.indicator_sw.connect("notify::active", self._on_indicator_toggled)
        self.indicator_row = hbox(8, label("Indicator", "secondary"), self.indicator_sw)
        self._apply_indicator()
        strip_right = vbox(10, hbox(12, self.source_chip, Gtk.Box(hexpand=True), self.indicator_row),
                           hbox(8, self.pips_box, Gtk.Box(hexpand=True), self.downbeat_btn, self.tap_btn), hexpand=True, valign=Gtk.Align.CENTER)
        self.clock_panel = hbox(16, self.beat_tile, strip_right, hexpand=True)
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

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                                max_children_per_line=4, column_spacing=8, row_spacing=8, valign=Gtk.Align.START)
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
            [("Add scene…", lambda: self.open_config("Scenes")), ("Reveal folder", self.act_reveal_folder)],
        )
        self.scene_stack = Gtk.Stack(vexpand=True)
        self.scene_stack.add_named(scroller, "grid")
        self.scene_stack.add_named(self.empty_scenes, "empty")
        panel = vbox(10, head, options, self.scene_stack, vexpand=True)
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
        self.brightness_label = label("85 %", "secondary")
        self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness.set_draw_value(False)
        self.brightness.add_css_class("mb-scale")
        self.brightness.set_value(85)
        self.brightness.connect("value-changed", self._on_brightness_changed)
        self.blackout_btn = Gtk.ToggleButton()
        self.blackout_btn.add_css_class("mb-btn")
        self.blackout_btn.add_css_class("blackout")
        self.blackout_btn.set_child(hbox(8, label("Blackout"), kbd("B"), halign=Gtk.Align.CENTER))
        self.blackout_btn.connect("toggled", self._on_blackout_toggled)
        panel = vbox(10, label("Master", "panel-title"),
                     vbox(4, hbox(0, label("Brightness", hexpand=True), self.brightness_label), self.brightness),
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
            self.act_tap()
        elif action == "downbeat":
            self.act_downbeat()
        elif action == "blackout":
            self.blackout_btn.set_active(not self.blackout_btn.get_active())
        elif action == "mode":
            (self.random_btn if arg == "random" else self.manual_btn).set_active(True)
        elif action == "step":
            self.act_step(arg)
        elif action == "scene-key":
            sid = vm.scene_for_key(arg, self.library.ids(), self.cfg.bindings.keys)
            if sid:
                self.act_scene(sid)
        elif action == "brightness":
            self.brightness.set_value(min(100, max(0, self.brightness.get_value() + arg)))
        elif action == "config":
            self.win_action("config")
        elif action == "shortcuts":
            self.win_action("shortcuts")

    def _register_actions(self) -> None:
        for name, fn in (
            ("config", lambda *_: self.open_config()),
            ("calibration", lambda *_: self.open_calibration_dialog()),
            ("quit-renderer", lambda *_: self.act_quit_renderer()),
            ("shortcuts", lambda *_: self.show_shortcuts()),
            ("about", lambda *_: self.show_about()),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", fn)
            self.add_action(act)

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

    def _on_indicator_toggled(self, sw: Gtk.Switch, *_a: Any) -> None:
        self._apply_indicator()
        if self._ui_guard:
            return
        try:
            self.store.update({"ui": {"beat_indicator": sw.get_active()}})
            self.cfg = self.store.load()
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the setting: {e}")

    def _apply_indicator(self) -> None:
        # Invisible, not removed: the panel must keep its size.
        on = self.indicator_sw.get_active()
        self.beat_tile.set_opacity(1.0 if on else 0.0)
        self.pips_box.set_opacity(1.0 if on else 0.0)

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
        ids = self.library.ids()
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

    def open_config(self, tab: str | None = None) -> None:
        from .config_dialog import ConfigDialog

        if self.config_dialog is None:
            self.config_dialog = ConfigDialog(self)
        if tab:
            self.config_dialog.show_tab(tab)
        self.config_dialog.present()

    def open_calibration_dialog(self) -> None:
        from .calibration_dialog import CalibrationDialog

        if self.calibration_dialog is None:
            self.calibration_dialog = CalibrationDialog(self)
        self.calibration_dialog.present()

    def open_calibration(self, step: int = 1) -> None:
        """Open the calibration window, optionally on step 2 (ball axis) or 3 (tile rows)."""
        from .calibration import CalibrationWindow

        if self.calibration_window is not None:
            self.calibration_window.present()
            return
        if not self.connected and not self.starting:
            self.start_renderer()  # the window shows "Starting the renderer..." until it connects
        self.calibration_window = CalibrationWindow(self, start_step=step)
        self.calibration_window.present()
        if self.state:
            self.calibration_window.on_state(self.state)

    def act_reveal_folder(self) -> None:
        d = self.library.dirs[-1]
        d.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(d))).launch(self, None, None)

    def act_banner(self, banner: vm.Banner) -> None:
        if banner.kind == "midi_lost":
            self.store.update({"clock": {"source": "internal"}})
            self.send("reload")
            self.toast("Switched to the internal clock.")
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

    def _sequence_configured(self) -> bool:
        """A configured sequence sets its own bars per scene, so the global stepper does not apply."""
        ids = set(self.library.ids())
        return any(i.scene in ids for i in self.cfg.director.sequence)

    def _on_transition_changed(self, *_a: Any) -> None:
        if self._ui_guard:
            return
        self.trans_idx, self.len_idx = self.trans_dd.get_selected(), self.len_dd.get_selected()
        try:
            self.store.update({"director": {"transition": self.transition_payload()}})
            self.send("reload")
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not save the transition: {e}")

    def _on_brightness_changed(self, scale: Gtk.Scale) -> None:
        v = scale.get_value()
        self.brightness_label.set_text(f"{v:.0f} %")
        if self._ui_guard:
            return
        if self._brightness_timer:
            GLib.source_remove(self._brightness_timer)
        self._brightness_timer = GLib.timeout_add(40, self._send_brightness, v)

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
        if up:
            self.starting = False
        else:
            self.state = None
            if self._restart_pending:
                self._restart_pending = False
                GLib.timeout_add(RESTART_DELAY_MS, lambda: self.start_renderer() or False)
        self.refresh()
        for w in (self.config_dialog, self.calibration_dialog, self.calibration_window):
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
        key = view_key(state)
        if changed or key != self._view_key:
            self._view_key = key
            self.refresh()
        for w in (self.config_dialog, self.calibration_dialog, self.calibration_window):
            if w:
                w.on_state(state)

    def apply_event(self, ev: dict[str, Any]) -> None:
        if self.config_dialog:
            self.config_dialog.on_event(ev)

    # --- rendering -----------------------------------------------------------------------

    def refresh(self) -> None:
        if self.starting and not self.connected and time.monotonic() > self._start_deadline:
            self.starting = False
            self.toast("The renderer did not start. See ~/.local/state/mirrorballs/render.log")
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
            fixed_bars = self.sequence_btn.get_active() and self._sequence_configured()
            stepper = self.every_label.get_parent()
            stepper.set_sensitive(live and not fixed_bars)
            stepper.set_tooltip_text("Each scene of the sequence has its own length (Configuration › Director)" if fixed_bars else None)
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
        text, warn = vm.clock_text(st["clock"] if st else None, self.cfg.clock.source, self.cfg.clock.midi_port,
                                   self.cfg.clock.bpm,
                                   st.get("midi") if st else None)
        self.source_chip.set_text(text)
        internal = (st["clock"]["source"] == "internal") if st else self.cfg.clock.source == "internal"
        self.source_chip.set_tooltip_text("Scroll to change the tempo" if internal else "The tempo follows the MIDI clock")
        set_classes(self.source_chip, {"warn": warn})
        set_classes(self.pips_box, {"dim": st is None})
        if st:
            self.cfg_bpb = st["clock"]["beats_per_bar"]
            for i, p in enumerate(self._pips):
                p.set_visible(i < st["clock"]["beats_per_bar"])

    def _refresh_scenes(self, st: dict[str, Any] | None) -> None:
        ids = self.library.ids()
        current = st.get("scene") if st else None
        mode = st.get("mode") if st else "manual"
        key = (tuple(ids), current, self.connected)
        if key != self._cards_key:
            self._cards_key = key
            self._rebuild_cards(ids, current)
        self.scene_stack.set_visible_child_name("grid" if ids else "empty")
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
        keys = vm.hotkeys(ids, self.cfg.bindings.keys)
        for sid in ids:
            sc = self.library.scenes[sid]
            live = sid == current
            thumb = Gtk.Box(vexpand=True, hexpand=True)
            thumb.add_css_class("thumb")
            if live:
                chip = label("Live", "live-chip", xalign=0.5)
                chip.set_halign(Gtk.Align.START)
                chip.set_valign(Gtk.Align.START)
                thumb.append(chip)
            name = label(sc.name or vm.display_name(sid), "name", hexpand=True)
            name.set_ellipsize(3)  # Pango.EllipsizeMode.END
            name.set_max_width_chars(9)  # natural width, so four cards fit in a row at 960 px
            top = hbox(6, name, *([kbd(keys[sid])] if sid in keys else []))
            tags = Adw.WrapBox(child_spacing=4, line_spacing=4)  # wraps, so tags never force the card wider
            for t in sc.tags[:3]:
                tags.append(label(t, "tag"))
            btn = Gtk.Button()
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
        if st is None:
            return
        pct = st["master"] * 100
        if not self.brightness.has_focus() and self._brightness_timer == 0 and abs(self.brightness.get_value() - pct) > 0.5:
            self.brightness.set_value(pct)
        self.brightness_label.set_text(f"{self.brightness.get_value():.0f} %")
        if st["master"] > 0.0:
            self._master_before_blackout = max(0.05, st["master"])
        self.blackout_btn.set_active(st["master"] <= 0.0)

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
        if st is None or not self.indicator_sw.get_active():
            self.beat_tile.set_intensity(0.0)
            self._set_pip(-1)
        else:
            idx, phase = vm.beat_in_bar(st["clock"], time.monotonic() - self.state_time)
            self.beat_tile.set_intensity(tokens.beat_intensity(phase))
            self._set_pip(idx)
        self._update_countdown(st)
        if self.starting and time.monotonic() > self._start_deadline and not self.connected:
            self.refresh()
        return True  # keep ticking

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

    # --- dialogs -------------------------------------------------------------------------

    def show_about(self) -> None:
        d = Adw.AboutDialog(application_name="Mirrorballs", version="0.1.0", developer_name="Frank Ploss",
                            comments="Beat-driven projection for a mirror ball.", license_type=Gtk.License.GPL_3_0)
        d.present(self)

    def show_shortcuts(self) -> None:
        rows = [
            ("Main window", [("Start / Stop", ["Space"]), ("Tap tempo", ["T"]), ("Downbeat", ["D"]), ("Blackout", ["B"]),
                             ("Scene 1–8", ["1", "…", "8"]), ("Previous / next scene", ["[", "]"]),
                             ("Manual / Random", ["M", "R"]), ("Brightness", ["Ctrl", "↑↓"]),
                             ("Configuration", ["Ctrl", ","]), ("Shortcuts", ["Ctrl", "?"])]),
            ("Calibration", [("Left line", ["A", "D"]), ("Right line", ["J", "L"]), ("Top line", ["W", "S"]),
                             ("Bottom line", ["I", "K"]), ("Move ball", ["←", "↑", "↓", "→"]), ("Grow / shrink", ["+", "−"]),
                             ("Step", ["1", "2", "3"]), ("Blink / grid", ["B", "G"]), ("Cancel", ["Esc"]),
                             ("Next step", ["Enter"]), ("Back a step", ["⌫"])]),
        ]
        cols = hbox(32, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        for title, items in rows:
            col = vbox(6, hexpand=True)
            col.append(label(title.upper(), "hint", xalign=0.0))
            for name, keys in items:
                col.append(hbox(8, label(name, hexpand=True), *[kbd(k) for k in keys]))
            cols.append(col)
        dlg = Adw.Dialog(title="Keyboard shortcuts", content_width=640)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(cols)
        dlg.set_child(view)
        dlg.present(self)


def open_in_editor(path: str) -> None:  # pragma: no cover - thin wrapper for the terminal fallback
    subprocess.Popen(["xdg-open", path], start_new_session=True)
