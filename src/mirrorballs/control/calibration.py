"""Calibration window, step 1 (silhouette), built from the design's Cal1 boards.

The window only draws controls and a small diagram. The lines themselves are drawn by the renderer
on the projector: this window sends ``calibration.preview`` on every change, and on Finish it saves the
result to config.yaml and commits. If the window (or the whole controller) goes away, the renderer
leaves calibration mode by itself and the saved values stay untouched.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from dataclasses import replace  # noqa: E402

from ..common.config import CalibrationConfig  # noqa: E402
from ..common.geometry import Calibration, ball_to_image  # noqa: E402
from . import config_model as cm  # noqa: E402
from . import tokens  # noqa: E402
from .calibration_model import (  # noqa: E402
    DEFAULT_PITCH, DEFAULT_SIZE, AxisSession, RowsSession, Session, axis_key_to_action, fmt_px, key_to_action,
    rows_key_to_action,
)
from .widgets import Segmented, Stepper, hbox, icon, kbd, label, push_button, set_classes, vbox  # noqa: E402

BEGIN_TIMEOUT_S = 1.5  # wait this long for the renderer to report its window size before assuming 1920x1080


class Diagram(Gtk.DrawingArea):
    """The output screen in miniature with the four lines and the ellipse they enclose."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(270)
        self.set_content_height(180)
        self.session: Session | None = None
        self.dark = True
        self.set_draw_func(self._draw)

    def _draw(self, _a: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        pal = tokens.palette(self.dark)
        rgb = tokens.hex_to_rgb
        if self.session is None:
            return
        s = self.session
        aspect = s.width / s.height
        sw = min(w - 8, (h - 8) * aspect)
        sh = sw / aspect
        x0, y0 = (w - sw) / 2, (h - sh) / 2
        cr.set_source_rgb(*rgb(pal["well"]))
        cr.rectangle(x0, y0, sw, sh)
        cr.fill()
        cr.set_source_rgb(*rgb(pal["line2"]))
        cr.set_line_width(1)
        cr.rectangle(x0 + 0.5, y0 + 0.5, sw - 1, sh - 1)
        cr.stroke()
        c = s.cal
        cx, cy = (c.left + c.right) / 2, (c.top + c.bottom) / 2
        rx, ry = (c.right - c.left) / 2, (c.bottom - c.top) / 2
        cr.save()
        cr.translate(x0 + cx * sw, y0 + cy * sh)
        cr.scale(max(rx * sw, 0.5), max(ry * sh, 0.5))
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.set_source_rgba(*rgb(pal["acc"]), 0.14)
        cr.fill_preserve()
        cr.set_source_rgb(*rgb(pal["acc_line"]))
        cr.set_line_width(1.5)
        cr.stroke()
        for side in ("left", "right", "top", "bottom"):
            v = getattr(c, side)
            cr.set_source_rgb(*rgb(pal["t1"] if s.active == side else pal["t2"]))
            cr.set_line_width(2 if s.active == side else 1)
            if side in ("left", "right"):
                cr.move_to(x0 + v * sw, y0)
                cr.line_to(x0 + v * sw, y0 + sh)
            else:
                cr.move_to(x0, y0 + v * sh)
                cr.line_to(x0 + sw, y0 + v * sh)
            cr.stroke()


class BallDiagram(Gtk.DrawingArea):
    """The ball with its graticule, for steps 2 and 3: the pole crosshair, or the tile-row stripes."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(270)
        self.set_content_height(230)
        self.roll = self.tilt = 0.0
        self.cross: tuple[float, float] | None = None  # pole marker in units of the ball radius (x right, y up)
        self.rows: tuple[float, float, str] | None = None  # pitch, offset, mode
        self.dark = True
        self.set_draw_func(self._draw)

    def _curve(self, cr: Any, pts: list[tuple[float, float, bool]], cx: float, cy: float, r: float) -> None:
        pen_down = False
        for x, y, visible in pts:
            if not visible:
                pen_down = False
                continue
            (cr.line_to if pen_down else cr.move_to)(cx + x * r, cy - y * r)
            pen_down = True

    def _project(self, lat: float, lon: float) -> tuple[float, float, bool]:
        la, lo = math.radians(lat), math.radians(lon)
        d = (math.cos(la) * math.sin(lo), math.sin(la), math.cos(la) * math.cos(lo))
        x, y, z = ball_to_image(d, self.roll, self.tilt)
        return x, y, z > 0

    def _draw(self, _a: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        pal = tokens.palette(self.dark)
        rgb = tokens.hex_to_rgb
        r = min(w, h) / 2 - 10
        cx, cy = w / 2, h / 2
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb(pal["well"]))
        cr.fill_preserve()
        cr.set_source_rgb(*rgb(pal["line2"]))
        cr.set_line_width(1.5)
        cr.stroke()
        cr.set_line_width(1)
        if self.rows is None:
            cr.set_source_rgba(*rgb(pal["t3"]), 0.6)
            for lat in (-60, -30, 30, 60):
                self._curve(cr, [self._project(lat, lon) for lon in range(-180, 181, 4)], cx, cy, r)
                cr.stroke()
            for lon in range(-150, 180, 30):
                self._curve(cr, [self._project(lat, lon) for lat in range(-90, 91, 4)], cx, cy, r)
                cr.stroke()
        else:
            pitch, offset, mode = self.rows
            k0 = math.ceil((-60 - offset) / pitch)
            k1 = math.floor((60 - offset) / pitch)
            for k in range(k0, k1 + 1):
                lit = (k % 2 == 0) if mode == "zebra" else k in (0, 1)  # single row: the two boundaries of row 0
                cr.set_source_rgba(*rgb(pal["t1"] if lit else pal["t3"]), 0.95 if lit else 0.35)
                cr.set_line_width(1.6 if lit else 0.8)
                self._curve(cr, [self._project(offset + k * pitch, lon) for lon in range(-180, 181, 4)], cx, cy, r)
                cr.stroke()
        # the equator, in the accent colour, with its label
        cr.set_source_rgb(*rgb(pal["acc"]))
        cr.set_line_width(1.6)
        eq = [self._project(0, lon) for lon in range(-180, 181, 4)]
        self._curve(cr, eq, cx, cy, r)
        cr.stroke()
        vis = [(x, y) for x, y, v in eq if v]
        if vis:
            x, y = min(vis)
            cr.select_font_face("sans")
            cr.set_font_size(11)
            cr.move_to(cx + x * r + 6, cy - y * r - 4)
            cr.show_text("equator")
        if self.cross is not None:
            px, py = self.cross
            x, y = cx + px * r, cy - py * r
            cr.new_path()  # show_text leaves a current point; without this arc() would draw a line from the label
            cr.set_source_rgb(*rgb(pal["t1"]))
            cr.set_line_width(1.6)
            cr.arc(x, y, 7, 0, 2 * math.pi)
            cr.stroke()
            for dx, dy in ((-13, 0), (13, 0), (0, -13), (0, 13)):
                cr.move_to(x + dx * 0.4, y + dy * 0.4)
                cr.line_to(x + dx, y + dy)
            cr.stroke()


class CalibrationWindow(Adw.Window):
    def __init__(self, main: Any, start_step: int = 1):
        super().__init__(title="Calibrate output", modal=True, transient_for=main, default_width=720, default_height=560)
        self.add_css_class("mb-dialog")
        self.main = main
        self.session: Session | None = None
        self.axis: AxisSession | None = None
        self.rows: RowsSession | None = None
        self.step_no = 1
        self.start_step = start_step
        self.begun = False
        self._begin_deadline = 0.0
        self._closing = False
        self._build()
        self._install_keys()
        self.connect("close-request", self._on_close_request)
        self._begin_deadline = _now() + BEGIN_TIMEOUT_S
        GLib.timeout_add(150, self._poll)
        self._update()

    # --- build ---------------------------------------------------------------------------

    def _build(self) -> None:
        view = Adw.ToolbarView()
        self.set_content(view)
        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self.edited_chip = label("Edited", "chip", "source")
        self.edited_chip.set_visible(False)
        header.pack_start(self.edited_chip)
        header.set_title_widget(label("Calibrate output", "title-label"))
        view.add_top_bar(header)

        self.steps = hbox(8, halign=Gtk.Align.CENTER, margin_top=12)
        self.step_pills: list[Gtk.Box] = []
        for n, (name, optional) in enumerate((("Silhouette", False), ("Ball axis", True), ("Tile rows", True)), start=1):
            pill = hbox(8, label(str(n), "pip", xalign=0.5), label(name, hexpand=False))
            if optional:
                pill.append(label("optional", "hint"))
            self.step_pills.append(pill)
            self.steps.append(pill)
        view.add_top_bar(self.steps)

        self.banner_box = vbox(0)
        view.add_top_bar(self.banner_box)

        self.instruction = label("Move each line until it just touches the edge of the ball.", "secondary", xalign=0.5, wrap=True)
        self.diagram = Diagram()
        self.blink_btn = Gtk.ToggleButton()
        self.grid_btn = Gtk.ToggleButton()
        for btn, text, key in ((self.blink_btn, "Blink", "B"), (self.grid_btn, "Verification grid", "G")):
            btn.add_css_class("mb-btn")
            btn.set_child(hbox(8, label(text), kbd(key)))
        self.blink_btn.connect("toggled", lambda b: self._act("blink", None, from_button=b))
        self.grid_btn.connect("toggled", lambda b: self._act("grid", None, from_button=b))
        self.read_centre, self.read_size, self.read_aspect = label("", "secondary"), label("", "secondary"), label("", "secondary")
        readouts = vbox(4)
        for name, w in (("Centre", self.read_centre), ("Size", self.read_size), ("Aspect", self.read_aspect)):
            readouts.append(hbox(8, label(name, hexpand=True), w))
        left = vbox(12, self.diagram, hbox(8, self.blink_btn, self.grid_btn), readouts, width_request=270)

        self.line_read: dict[str, Gtk.Label] = {}
        lines = vbox(6)
        for name, side, neg, pos, icons in (
            ("Left", "left", ("A", "go-previous-symbolic"), ("D", "go-next-symbolic"), None),
            ("Right", "right", ("J", "go-previous-symbolic"), ("L", "go-next-symbolic"), None),
            ("Top", "top", ("W", "go-up-symbolic"), ("S", "go-down-symbolic"), None),
            ("Bottom", "bottom", ("I", "go-up-symbolic"), ("K", "go-down-symbolic"), None),
        ):
            readout = label("", "secondary")
            self.line_read[side] = readout
            row = hbox(6, label(name, hexpand=True), readout)
            row.set_margin_end(6)
            for (key, ic), direction in ((neg, -1), (pos, +1)):
                b = Gtk.Button(icon_name=ic)
                b.add_css_class("mb-btn")
                word = ("left", "right") if side in ("left", "right") else ("up", "down")
                b.set_tooltip_text(f"{name} line {word[0 if direction < 0 else 1]}")
                b.connect("clicked", lambda _b, side=side, d=direction: self._act("line", (side, d)))
                row.append(hbox(4, b, kbd(key)))
            lines.append(row)

        dpad = Gtk.Grid(row_spacing=4, column_spacing=4, halign=Gtk.Align.CENTER)
        for (col, row_i), ic, arrow in (((1, 0), "go-up-symbolic", (0, -1)), ((0, 1), "go-previous-symbolic", (-1, 0)),
                                        ((2, 1), "go-next-symbolic", (1, 0)), ((1, 1), "go-down-symbolic", (0, 1))):
            b = Gtk.Button(icon_name=ic)
            b.add_css_class("mb-btn")
            b.connect("clicked", lambda _b, a=arrow: self._act("move", a))
            dpad.attach(b, col, row_i, 1, 1)
        grow_shrink = hbox(6, halign=Gtk.Align.END)
        for ic, sign, key in (("list-remove-symbolic", -1, "−"), ("list-add-symbolic", +1, "+")):
            b = Gtk.Button(icon_name=ic)
            b.add_css_class("mb-btn")
            b.connect("clicked", lambda _b, sg=sign: self._act("grow", sg))
            grow_shrink.append(hbox(4, b, kbd(key)))
        self.step_seg = Segmented([("fine", "Fine"), ("normal", "Normal"), ("coarse", "Coarse")], "normal",
                                  lambda k: self._act("step", k))
        right = vbox(
            12, lines,
            hbox(12, vbox(2, label("Move ball"), label("arrow keys", "hint")), Gtk.Box(hexpand=True), dpad),
            hbox(12, label("Grow / Shrink", hexpand=True), grow_shrink),
            hbox(12, label("Step", hexpand=True), self.step_seg),
            hexpand=True,
        )
        body = hbox(24, left, right, margin_start=24, margin_end=24, margin_top=8, margin_bottom=8, vexpand=True)

        self.reset_btn = push_button("Reset to defaults", "tall")
        self.reset_btn.connect("clicked", lambda *_: self._on_reset())
        self.advanced_btn = push_button("Advanced…", "tall")
        self.advanced_btn.set_tooltip_text("Ball axis and tile rows")
        self.advanced_btn.connect("clicked", lambda *_: self._go(2))
        self.finish_btn = push_button("Finish", "tall", "primary", kbd_text="Enter")
        self.finish_btn.add_css_class("on-accent")
        self.finish_btn.connect("clicked", lambda *_: self.finish())

        self.pages = Gtk.Stack(vexpand=True)
        self.pages.add_named(body, "1")
        self.pages.add_named(self._build_axis_page(), "2")
        self.pages.add_named(self._build_rows_page(), "3")

        def cancel_btn() -> Gtk.Button:
            b = push_button("Cancel", "tall", kbd_text="Esc")
            b.connect("clicked", lambda *_: self.cancel())
            return b

        def back_btn(to: int) -> Gtk.Button:
            b = push_button("Back", "tall")
            b.connect("clicked", lambda *_: self._go(to))
            return b

        self.next_btn = push_button("Next", "tall", "primary", kbd_text="Enter")
        self.next_btn.add_css_class("on-accent")
        self.next_btn.connect("clicked", lambda *_: self._go(3))
        skip_btn = push_button("Skip", "tall")
        skip_btn.connect("clicked", lambda *_: self._go(3, apply_axis=False))
        self.finish3_btn = push_button("Finish", "tall", "primary", kbd_text="Enter")
        self.finish3_btn.add_css_class("on-accent")
        self.finish3_btn.connect("clicked", lambda *_: self.finish())
        self.footers = Gtk.Stack()
        pad = dict(margin_start=24, margin_end=24, margin_top=8, margin_bottom=16)
        self.footers.add_named(hbox(8, self.reset_btn, Gtk.Box(hexpand=True), cancel_btn(), self.advanced_btn, self.finish_btn, **pad), "1")
        self.footers.add_named(hbox(8, back_btn(1), Gtk.Box(hexpand=True), cancel_btn(), skip_btn, self.next_btn, **pad), "2")
        self.footers.add_named(hbox(8, back_btn(2), Gtk.Box(hexpand=True), cancel_btn(), self.finish3_btn, **pad), "3")
        view.set_content(vbox(8, self.instruction, self.pages, self.footers, margin_top=8))

    def _build_axis_page(self) -> Gtk.Widget:
        self.axis_diagram = BallDiagram()
        self.read_tilt, self.read_roll = label("", "secondary"), label("", "secondary")
        readouts = vbox(4, hbox(8, label("Tilt", hexpand=True), self.read_tilt), hbox(8, label("Roll", hexpand=True), self.read_roll))
        left = vbox(12, self.axis_diagram, readouts, width_request=270)

        dpad = Gtk.Grid(row_spacing=4, column_spacing=4, halign=Gtk.Align.CENTER)
        for (col, row_i), ic, arrow, key in (((1, 0), "go-up-symbolic", (0, -1), "↑"), ((0, 1), "go-previous-symbolic", (-1, 0), "←"),
                                             ((2, 1), "go-next-symbolic", (1, 0), "→"), ((1, 1), "go-down-symbolic", (0, 1), "↓")):
            b = Gtk.Button(icon_name=ic)
            b.add_css_class("mb-btn")
            b.set_tooltip_text(f"Move crosshair ({key})")
            b.connect("clicked", lambda _b, a=arrow: self._axis_act("cross", a))
            dpad.attach(b, col, row_i, 1, 1)
        self.axis_step = Segmented([("fine", "Fine"), ("normal", "Normal"), ("coarse", "Coarse")], "normal", lambda k: self._axis_act("step", k))
        self.above_seg = Segmented([("above", "Above the equator"), ("below", "Below the equator")], "above",
                                   lambda k: self._axis_set_above(k == "above"))
        self.height_step = Stepper(0, 0, 300, 1, lambda v: f"<b>{v:.0f}</b> cm", lambda v: self._axis_numbers(height=v), width=150)
        self.distance_step = Stepper(2.4, 0.5, 15, 0.1, lambda v: f"<b>{v:.2f}</b> m", lambda v: self._axis_numbers(distance=v), width=150)
        right = vbox(
            12,
            hbox(12, vbox(2, label("Crosshair"), label("arrow keys", "hint")), Gtk.Box(hexpand=True), dpad),
            hbox(12, label("Step", hexpand=True), self.axis_step),
            vbox(6, label("Projector position"), self.above_seg),
            vbox(6, vbox(0, label("Numeric fallback"), label("if the crosshair is hard to judge", "hint", wrap=True)),
                 hbox(12, label("Height above equator", hexpand=True), self.height_step),
                 hbox(12, label("Distance to ball", hexpand=True), self.distance_step)),
            hexpand=True,
        )
        return hbox(24, left, right, margin_start=24, margin_end=24, margin_top=8, margin_bottom=8, vexpand=True)

    def _build_rows_page(self) -> Gtk.Widget:
        self.rows_diagram = BallDiagram()
        self.read_rows = label("", "secondary")
        left = vbox(12, self.rows_diagram, hbox(8, label("Result", hexpand=True), self.read_rows),
                    label("Read only. Calculated from offset and pitch.", "hint", wrap=True), width_request=270)

        def nudge(name: str, sub: str, neg: tuple[str, str, Any], pos: tuple[str, str, Any]) -> Gtk.Widget:
            row = hbox(6, vbox(2, label(name), label(sub, "hint")), Gtk.Box(hexpand=True))
            for key, ic, act in (neg, pos):
                b = Gtk.Button(icon_name=ic)
                b.add_css_class("mb-btn")
                b.connect("clicked", lambda _b, a=act: self._rows_act(*a))
                row.append(hbox(4, b, kbd(key)))
            return row

        self.rows_step = Segmented([("fine", "Fine"), ("normal", "Normal"), ("coarse", "Coarse")], "normal", lambda k: self._rows_act("step", k))
        self.pattern_seg = Segmented([("zebra", "Zebra"), ("single", "Single row")], "zebra", lambda k: self._rows_set_pattern(k))
        right = vbox(
            12,
            nudge("Offset", "up / down", ("↑", "go-up-symbolic", ("offset", +1)), ("↓", "go-down-symbolic", ("offset", -1))),
            nudge("Pitch", "narrower / wider", ("←", "go-previous-symbolic", ("pitch", -1)), ("→", "go-next-symbolic", ("pitch", +1))),
            hbox(12, label("Step", hexpand=True), self.rows_step),
            vbox(6, vbox(0, label("Pattern"), label("shown on the ball", "hint")), self.pattern_seg),
            hexpand=True,
        )
        return hbox(24, left, right, margin_start=24, margin_end=24, margin_top=8, margin_bottom=8, vexpand=True)

    def _install_keys(self) -> None:
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

    def _on_key(self, _c: Any, keyval: int, _code: int, mods: Gdk.ModifierType) -> bool:
        if mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        name = Gdk.keyval_name(keyval) or ""
        mapper = {1: key_to_action, 2: axis_key_to_action, 3: rows_key_to_action}[self.step_no]
        hit = mapper(name)
        if hit is None:
            return False
        action, arg = hit
        if action == "cancel":
            self.cancel()
        elif action == "finish":
            self.finish()
        elif action == "next":
            self._go(3)
        elif action == "back":
            self._go(self.step_no - 1) if self.step_no > 1 else None
        elif self.step_no == 1:
            self._act(action, arg)
        elif self.step_no == 2:
            self._axis_act(action, arg)
        else:
            self._rows_act(action, arg)
        return True

    # --- session -------------------------------------------------------------------------

    def _window_size(self) -> tuple[int, int] | None:
        st = self.main.state if self.main.connected else None
        w = st.get("window") if st else None
        return (w["width"], w["height"]) if w else None

    def _default_calibration(self) -> Calibration:
        w, h = self._window_size() or DEFAULT_SIZE
        return Calibration.default(w / h)

    def _begin(self) -> None:
        """Enter calibration mode on the renderer, starting from the saved values."""
        size = self._window_size() or DEFAULT_SIZE
        saved = self.main.store.load().calibration
        initial = saved.to_calibration() if saved else Calibration.default(size[0] / size[1])
        self.session = Session(initial, *size)
        self.diagram.session = self.session
        self.begun = True
        self.main.send("calibration.begin")
        self._send_preview()
        if self.start_step != 1:  # opened from "Advanced calibration..."
            self._go(self.start_step)
            self.start_step = 1

    def _send_preview(self) -> None:
        if not self.session:
            return
        payload = self.session.preview()
        if self.step_no == 2 and self.axis:
            cal = self.axis.calibration()
            payload.update(roll_deg=cal.roll_deg, tilt_deg=cal.tilt_deg, lines=False, grid=True, blink=False,
                           crosshair=list(self.axis.crosshair_norm()))
        elif self.step_no == 3 and self.rows:
            payload.update(lines=False, grid=False, blink=False, rows=self.rows.preview())
        self.main.send("calibration.preview", **payload)

    # --- steps 2 and 3 -------------------------------------------------------------------

    INSTRUCTIONS = {
        1: "Move each line until it just touches the edge of the ball.",
        2: "Move the crosshair onto the hanger eyelet at the top of the ball.",
        3: "Nudge the rows until the projected stripes line up with the mirror tiles.",
    }

    def _go(self, step: int, apply_axis: bool = True) -> None:
        """Switch step. Leaving step 2 applies the crosshair's roll and tilt to the calibration (unless skipped)."""
        s = self.session
        if s is None or step == self.step_no:
            return
        if self.step_no == 2 and self.axis and apply_axis:
            s.cal = self.axis.calibration()
        if step == 2:  # rebuilt on every entry: the lines may have changed since the last visit
            self.axis = AxisSession(s.cal, s.width, s.height, step=s.step)
            self.axis_step.select(s.step)
            self.above_seg.select("above" if self.axis.above else "below")
        if step == 3 and self.rows is None:
            self.rows = RowsSession(*self._initial_rows())
        self.step_no = step
        self.pages.set_visible_child_name(str(step))
        self.footers.set_visible_child_name(str(step))
        self.instruction.set_text(self.INSTRUCTIONS[step])
        self._send_preview()
        self._update()

    def _initial_rows(self) -> tuple[float, float]:
        b = self.main.store.load().ball
        if b.row_pitch_deg:
            return b.row_pitch_deg, b.row_offset_deg
        if b.diameter_cm and b.tile_mm:
            return cm.pitch_from_rows(cm.rows_from_tile(b.diameter_cm, b.tile_mm)), 0.0
        return DEFAULT_PITCH, 0.0

    def _axis_act(self, action: str, arg: Any) -> None:
        if not self.axis:
            return
        if action == "cross":
            self.axis.move(*arg)
        elif action == "step":
            self.axis.step = arg
        self._send_preview()
        self._update()

    def _axis_set_above(self, above: bool) -> None:
        if self.axis and self.axis.above != above:
            self.axis.set_above(above)
            self._send_preview()
            self._update()

    def _axis_numbers(self, height: float | None = None, distance: float | None = None) -> None:
        if self.axis and not getattr(self, "_syncing", False):
            self.axis.set_numbers(height, distance)
            self._send_preview()
            self._update()

    def _rows_act(self, action: str, arg: Any) -> None:
        if not self.rows:
            return
        self.rows.apply(action, arg)
        if action == "step":
            self.rows_step.select(arg)
        self._send_preview()
        self._update()

    def _rows_set_pattern(self, mode: str) -> None:
        if self.rows and self.rows.mode != mode:
            self.rows.mode = mode
            self._send_preview()
            self._update()

    def _act(self, action: str, arg: Any, from_button: Gtk.ToggleButton | None = None) -> None:
        if self.session is None:
            return
        s = self.session
        if action in ("blink", "grid") and from_button is not None and from_button.get_active() == getattr(s, action):
            return  # the button just mirrors the state we set ourselves
        s.apply(action, arg)
        self._send_preview()
        self._update()

    def on_state(self, state: dict[str, Any]) -> None:
        """Called by the main window for every renderer state."""
        if self._closing:
            return
        if not self.begun:
            if self._window_size() is not None or _now() >= self._begin_deadline:
                self._begin()
        elif self.session and (size := self._window_size()) and size != (self.session.width, self.session.height):
            self.session.width, self.session.height = size  # the output window was resized: keep the normalised lines
        self._update()

    def on_connection(self, up: bool) -> None:
        if not up and self.begun and not self._closing:
            self.begun = False
            self.session = None
            self.diagram.session = None
        self._update()

    def _poll(self) -> bool:
        if self._closing:
            return False
        if not self.begun and self.main.connected and _now() >= self._begin_deadline:
            self._begin()
            self._update()
        return True

    # --- view ----------------------------------------------------------------------------

    def _banner(self, text: str, busy: bool = False) -> Gtk.Widget:
        row = hbox(10, Gtk.Spinner(spinning=True) if busy else icon("dialog-warning-symbolic", 18),
                   label(text, hexpand=True, wrap=True), valign=Gtk.Align.CENTER)
        row.add_css_class("banner")
        return row

    def _update(self) -> None:
        s = self.session
        banners: list[tuple[str, bool]] = []
        st = self.main.state if self.main.connected else None
        if not self.main.connected:
            banners.append(("Starting the renderer. The lines appear on the projector in a moment.", True))
        elif st and st.get("window") and not st["window"]["fullscreen"]:
            banners.append(("The output window is not fullscreen yet. Move it to the projector and press F11.", False))
        if s and self.step_no == 1 and s.aspect_warning:
            banners.append((s.aspect_message(), False))
        key = tuple(banners)
        if key != getattr(self, "_banner_key", None):
            self._banner_key = key
            while (c := self.banner_box.get_first_child()) is not None:
                self.banner_box.remove(c)
            for text, busy in banners:
                self.banner_box.append(self._banner(text, busy))

        for i, pill in enumerate(self.step_pills, start=1):
            set_classes(pill.get_first_child(), {"current": i == self.step_no})
        active = s is not None and self.main.connected
        for w in (self.finish_btn, self.finish3_btn, self.next_btn, self.advanced_btn, self.reset_btn, self.blink_btn,
                  self.grid_btn, self.step_seg):
            w.set_sensitive(active)
        dirty = bool(s and (s.dirty or (self.axis and self.axis.dirty) or (self.rows and self.rows.dirty)))
        self.edited_chip.set_visible(dirty)
        dark = Adw.StyleManager.get_default().get_dark()
        self.diagram.dark = self.axis_diagram.dark = self.rows_diagram.dark = dark
        if s is None:
            for w in (self.read_centre, self.read_size, self.read_aspect, self.read_tilt, self.read_roll, self.read_rows,
                      *self.line_read.values()):
                w.set_text("—")
            self.diagram.queue_draw()
            return
        cx, cy = s.centre_px()
        sw, sh = s.size_px()
        self.read_centre.set_text(f"{fmt_px(cx)}, {fmt_px(cy)} px")
        self.read_size.set_text(f"{fmt_px(sw)} × {fmt_px(sh)} px")
        self.read_aspect.set_text(f"{s.aspect:.3f}")
        set_classes(self.read_aspect, {"amber-text": s.aspect_warning})
        for side, lb in self.line_read.items():
            px, pct = s.line_px(side)
            lb.set_text(f"{fmt_px(px)} px · {pct:.1f} %")
        self.diagram.queue_draw()
        if self.axis:
            tilt_text, roll_text = self.axis.texts()
            self.read_tilt.set_text(tilt_text)
            self.read_roll.set_text(roll_text)
            self._syncing = True
            try:
                self.height_step.set_value(self.axis.height_cm)
                self.distance_step.set_value(self.axis.distance_m)
            finally:
                self._syncing = False
            self.axis_diagram.roll = self.axis.calibration().roll_deg
            self.axis_diagram.tilt = self.axis.calibration().tilt_deg
            self.axis_diagram.cross = (self.axis.px, self.axis.py)
            self.axis_diagram.queue_draw()
        if self.rows:
            self.read_rows.set_text(self.rows.text())
            self.rows_diagram.roll, self.rows_diagram.tilt = s.cal.roll_deg, s.cal.tilt_deg
            self.rows_diagram.rows = (self.rows.pitch, self.rows.offset, self.rows.mode)
            self.rows_diagram.queue_draw()

    def _on_reset(self) -> None:
        if not self.session:
            return
        if not self.session.dirty:
            self._do_reset()
            return
        dlg = Adw.AlertDialog(heading="Reset to defaults?", body="The lines go back to a centred, round ball at 70 % of the screen height.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset to defaults")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "reset" and self._do_reset())
        dlg.present(self)

    def _do_reset(self) -> None:
        if self.session:
            self.session.reset(self._default_calibration())
            self._send_preview()
            self._update()

    def finish(self) -> None:
        if not self.session or not self.main.connected:
            return
        if self.step_no == 2 and self.axis:  # finishing straight from the axis step keeps the crosshair's angles
            self.session.cal = self.axis.calibration()
        c = self.session.cal
        if not c.is_valid():
            self.main.toast("The lines are too close together.")
            return
        data = CalibrationConfig.from_calibration(c).model_dump()
        data["calibrated_at"] = datetime.now().isoformat(timespec="minutes")
        updates: dict[str, Any] = {"calibration": data}
        if self.rows and self.rows.dirty:  # untouched rows are not saved: an arbitrary default pitch would switch snapping on
            updates["ball"] = {"row_pitch_deg": round(self.rows.pitch, 4), "row_offset_deg": round(self.rows.offset, 4)}
        try:
            self.main.store.update(updates)
        except Exception as e:  # noqa: BLE001
            self.main.toast(f"Could not save the calibration: {e}")
            return
        self.main.send("calibration.end", commit=True)
        self.main.send("reload")
        self.main.toast("Calibration saved.")
        self._close()

    def cancel(self) -> None:
        if self.begun and self.main.connected:
            self.main.send("calibration.end", commit=False)
        self._close()

    def _close(self) -> None:
        self._closing = True
        self.main.calibration_window = None
        self.main.refresh()
        self.destroy()

    def _on_close_request(self, *_a: Any) -> bool:
        if not self._closing:
            self.cancel()
        return True


def _now() -> float:
    import time

    return time.monotonic()
