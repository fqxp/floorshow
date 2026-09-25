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

import cairo

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
    DEFAULT_PITCH, DEFAULT_SIZE, ROW_TINTS, STEP_AXIS, STEP_BALL, STEP_DONE, STEP_ROWS, STEP_SILHOUETTE, AxisSession,
    RowsSession, Session, axis_key_to_action, ball_key_to_action, done_key_to_action, fmt_px, key_to_action,
    parity_offset, rows_key_to_action,
)
from .widgets import (  # noqa: E402
    Segmented, SettingsGroup, SizeChoice, Stepper, hbox, icon, kbd, label, push_button, set_classes, setting_row, vbox,
)

BEGIN_TIMEOUT_S = 1.5  # wait this long for the renderer to report its window size before assuming 1920x1080
SPIN_DPS, SPIN_TICK_MS = 24.0, 50  # the confirmation globe turns at the renderer's rate (core.CAL_SPIN_DPS)
DEFAULT_DIAMETER_CM, DEFAULT_TILE_MM, DEFAULT_RPM = 30.0, 10.0, 6.0  # what step 1 offers before anything is measured
DIAMETERS_CM = [20.0, 25.0, 30.0, 40.0, 50.0]  # the sizes actually sold; anything else goes in under "Other…"
TILES_MM = [5.0, 10.0, 15.0, 20.0, 25.0]


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
        self.spin = 0.0  # degrees the graticule is turned about the pole axis (step 4)
        self.cross: tuple[float, float] | None = None  # pole marker in units of the ball radius (x right, y up)
        self.rows: tuple[float, float, str] | None = None  # pitch, offset, mode
        self.rows_limit = 60.0  # matches gl.ROWS_LIMIT_DEG
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

    def _band(self, cr: Any, lat0: float, lat1: float, cx: float, cy: float, r: float) -> bool:
        """Path around the visible part of one tile row, so it can be filled like the renderer fills it."""
        lons = range(-180, 181, 3)
        top = [(x, y) for x, y, v in (self._project(lat0, lon) for lon in lons) if v]
        bottom = [(x, y) for x, y, v in (self._project(lat1, lon) for lon in lons) if v]
        if len(top) < 2 or len(bottom) < 2:
            return False
        cr.new_path()
        for i, (x, y) in enumerate(top + bottom[::-1]):
            (cr.line_to if i else cr.move_to)(cx + x * r, cy - y * r)
        cr.close_path()
        return True

    def _project(self, lat: float, lon: float) -> tuple[float, float, bool]:
        la, lo = math.radians(lat), math.radians(lon + self.spin)
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
        else:
            pitch, offset, mode = self.rows
            equator_band = math.floor(-offset / pitch)
            k0 = math.ceil((-self.rows_limit - offset) / pitch)
            k1 = math.floor((self.rows_limit - offset) / pitch) - 1
            for k in range(k0, k1 + 1):
                is_eq = k == equator_band
                if mode == "equator" and not is_eq:
                    continue
                tint = ROW_TINTS["equator"] if is_eq else ROW_TINTS["a" if k % 2 == 0 else "b"]
                if self._band(cr, offset + k * pitch, offset + (k + 1) * pitch, cx, cy, r):
                    cr.set_source_rgba(*tint, 0.9)
                    cr.fill()
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


def _facet(ix: int, iy: int) -> float:
    """A fixed pseudo-random number per tile, so the artwork looks scattered but never changes."""
    return (((ix * 73856093) ^ (iy * 19349663)) % 997) / 996.0


class BallArtwork(Gtk.DrawingArea):
    """A mirror ball throwing its beams through haze. Not a diagram and not accurate: it is here to say
    what all this is for. Always drawn in the dark palette, because it is a picture of a dark room."""

    BEAMS = ((-166, 5), (-131, 8), (-97, 4), (-58, 9), (-24, 5), (14, 7), (49, 4), (86, 9), (121, 5), (155, 7))

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(196)
        self.set_content_height(132)
        self.set_valign(Gtk.Align.START)
        self.set_draw_func(self._draw)

    def _draw(self, _a: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        pal, rgb = tokens.DARK, tokens.hex_to_rgb
        acc, far = rgb(pal["acc"]), math.hypot(w, h)
        cx, cy, r = w * 0.5, h * 0.42, min(w, h) * 0.2
        rad = 10.0
        cr.new_path()  # the room, with the artwork's own rounded corners
        for sx, sy, a0 in ((w - rad, h - rad, 0.0), (rad, h - rad, 0.5), (rad, rad, 1.0), (w - rad, rad, 1.5)):
            cr.arc(sx, sy, rad, a0 * math.pi, (a0 + 0.5) * math.pi)
        cr.close_path()
        cr.set_source_rgb(*rgb(pal["well"]))
        cr.fill_preserve()
        cr.clip()

        haze = cairo.RadialGradient(cx, cy, r * 0.5, cx, cy, far * 0.8)
        haze.add_color_stop_rgba(0.0, *acc, 0.20)
        haze.add_color_stop_rgba(1.0, *acc, 0.0)
        cr.set_source(haze)
        cr.paint()

        for i in range(34):  # the spots the ball throws around the room
            x, y = _facet(i, 7) * w, _facet(i, 13) * h
            d = math.hypot(x - cx, y - cy) / far
            cr.set_source_rgba(*rgb(pal["t1"]), 0.10 + 0.35 * max(0.0, 1.0 - d * 2.2))
            cr.rectangle(x, y, 2.5, 2.5)
            cr.fill()

        for angle, spread in self.BEAMS:
            a = math.radians(angle)
            # three wedges inside each other instead of one: a beam in haze has no edge, it just thins out
            for widen, peak in ((1.7, 0.09), (1.0, 0.13), (0.45, 0.17)):
                half = math.radians(spread) * widen
                beam = cairo.LinearGradient(cx, cy, cx + math.cos(a) * far, cy + math.sin(a) * far)
                beam.add_color_stop_rgba(0.0, *tokens.mix(pal["acc"], pal["t1"], 0.55), 0.0)
                beam.add_color_stop_rgba(0.10, *tokens.mix(pal["acc"], pal["t1"], 0.45), peak * 2)  # hot at the ball
                beam.add_color_stop_rgba(0.45, *acc, peak)
                beam.add_color_stop_rgba(1.0, *acc, 0.0)
                cr.move_to(cx + math.cos(a - half) * r, cy + math.sin(a - half) * r)
                cr.line_to(cx + math.cos(a - half) * far, cy + math.sin(a - half) * far)
                cr.line_to(cx + math.cos(a + half) * far, cy + math.sin(a + half) * far)
                cr.line_to(cx + math.cos(a + half) * r, cy + math.sin(a + half) * r)
                cr.close_path()
                cr.set_source(beam)
                cr.fill()

        cr.save()  # the ball: square facets, brightest where the projector hits it
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.clip()
        cell = r * 0.27
        hx, hy = cx - r * 0.4, cy - r * 0.45
        n = int(r / cell) + 2
        for iy in range(-n, n + 1):
            for ix in range(-n, n + 1):
                x, y = cx + ix * cell, cy + iy * cell
                f = _facet(ix, iy)
                lit = max(0.0, 1.0 - math.hypot(x - hx, y - hy) / (2.4 * r)) ** 1.3
                lit *= 0.25 + 0.75 * f + (0.9 if f > 0.93 else 0.0)  # a few facets catch the light head on
                cr.set_source_rgb(*tokens.mix(pal["bg2"], pal["t1"], min(1.0, lit)))
                cr.rectangle(x - cell / 2 + 0.5, y - cell / 2 + 0.5, cell - 1, cell - 1)
                cr.fill()
        cr.restore()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(*rgb(pal["t1"]), 0.35)
        cr.set_line_width(1)
        cr.stroke()
        cr.move_to(cx, cy - r)  # the hanger, which is where step 3 looks for the pole
        cr.line_to(cx, 0)
        cr.set_source_rgba(*rgb(pal["t3"]), 0.8)
        cr.stroke()


class CalibrationWindow(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Calibrate output", modal=True, transient_for=main, default_width=720, default_height=560)
        self.add_css_class("mb-dialog")
        self.main = main
        self.session: Session | None = None
        self.axis: AxisSession | None = None
        self.rows: RowsSession | None = None
        self.step_no = STEP_BALL
        self.rows_cleared = False  # step 3 was reset to defaults: the saved row grid is dropped
        self.begun = False
        self._begin_deadline = 0.0
        self._closing = False
        self._spin_timer = 0
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
        header.set_title_widget(label("Calibrate output", "title-label"))
        view.add_top_bar(header)

        self.steps = hbox(8, halign=Gtk.Align.CENTER, margin_top=12)
        self.step_pills: list[Gtk.Box] = []
        for n, (name, optional) in enumerate((("Ball", False), ("Silhouette", False), ("Ball axis", True),
                                              ("Tile rows", True), ("Confirm", False)), start=1):
            pill = hbox(8, label(str(n), "pip", xalign=0.5), label(name, hexpand=False))
            if optional:
                pill.append(label("optional", "hint"))
            self.step_pills.append(pill)
            self.steps.append(pill)
        view.add_top_bar(self.steps)

        self.banner_box = vbox(0)
        view.add_top_bar(self.banner_box)

        self.instruction = label(self.INSTRUCTIONS[STEP_BALL], "lead", xalign=0.5, wrap=True)
        self.instruction.set_margin_start(32)
        self.instruction.set_margin_end(32)
        self.instruction.set_visible(bool(self.INSTRUCTIONS[STEP_BALL]))
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
        for name, w in (("Center", self.read_centre), ("Size", self.read_size), ("Aspect", self.read_aspect)):
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

        self.pages = Gtk.Stack(vexpand=True)
        self.pages.add_named(self._build_ball_page(), str(STEP_BALL))
        self.pages.add_named(body, str(STEP_SILHOUETTE))
        self.pages.add_named(self._build_axis_page(), str(STEP_AXIS))
        self.pages.add_named(self._build_rows_page(), str(STEP_ROWS))
        self.pages.add_named(self._build_done_page(), str(STEP_DONE))

        def cancel_btn() -> Gtk.Button:
            b = push_button("Cancel", "tall", kbd_text="Esc")
            b.connect("clicked", lambda *_: self.cancel())
            return b

        def back_btn(to: int) -> Gtk.Button:
            b = push_button("Back", "tall")
            b.connect("clicked", lambda *_: self._go(to))
            return b

        def next_btn(to: int) -> Gtk.Button:
            b = push_button("Next", "tall", "primary", kbd_text="Enter")
            b.add_css_class("on-accent")
            b.connect("clicked", lambda *_: self._go(to))
            return b

        def reset_btn(step: int) -> Gtk.Button:
            # No keyboard shortcut, on any page: it undoes work and is too easy to hit by accident.
            b = push_button("Reset to defaults", "tall")
            b.connect("clicked", lambda *_: self._on_reset(step))
            return b

        # Every step but the last ends in Next, so the wizard always runs to the confirmation screen.
        self.next_btns = {step: next_btn(step + 1) for step in (STEP_BALL, STEP_SILHOUETTE, STEP_AXIS, STEP_ROWS)}
        self.reset_btns = {step: reset_btn(step) for step in (STEP_BALL, STEP_SILHOUETTE, STEP_AXIS, STEP_ROWS)}
        self.finish_btn = push_button("Finish", "tall", "primary", kbd_text="Enter")
        self.finish_btn.add_css_class("on-accent")
        self.finish_btn.connect("clicked", lambda *_: self.finish())
        self.footers = Gtk.Stack()
        pad = dict(margin_start=24, margin_end=24, margin_top=8, margin_bottom=16)
        self.footers.add_named(hbox(8, self.reset_btns[STEP_BALL], Gtk.Box(hexpand=True), cancel_btn(),
                                    self.next_btns[STEP_BALL], **pad), str(STEP_BALL))
        for step in (STEP_SILHOUETTE, STEP_AXIS, STEP_ROWS):
            self.footers.add_named(hbox(8, back_btn(step - 1), self.reset_btns[step], Gtk.Box(hexpand=True),
                                        cancel_btn(), self.next_btns[step], **pad), str(step))
        self.footers.add_named(hbox(8, back_btn(STEP_ROWS), Gtk.Box(hexpand=True), cancel_btn(), self.finish_btn,
                                    **pad), str(STEP_DONE))
        view.set_content(vbox(8, self.instruction, self.pages, self.footers, margin_top=8))

    def _build_ball_page(self) -> Gtk.Widget:
        """Step 1: what we know about the rig before the projector comes into it. Nothing here is measured
        by the wizard, and nothing here has to be right: it only gives the later steps somewhere to start."""
        b = self.main.store.load().ball
        self.ball_saved = bool(b.diameter_cm and b.tile_mm)
        self.ball_diameter = b.diameter_cm or DEFAULT_DIAMETER_CM
        self.ball_tile = b.tile_mm or DEFAULT_TILE_MM
        self.ball_rotation_rpm = b.rotation_rpm
        self._ball_initial = (self.ball_diameter, self.ball_tile, self.ball_rotation_rpm)
        self.diameter_step = SizeChoice(DIAMETERS_CM, self.ball_diameter, "cm", 5, 200, 1,
                                        lambda v: self._ball_changed(diameter=v))
        self.tile_step = SizeChoice(TILES_MM, self.ball_tile, "mm", 2, 50, 1, lambda v: self._ball_changed(tile=v))
        self.rotation_seg = Segmented([("still", "Still"), ("right", "Turns right"), ("left", "Turns left")],
                                      self._rotation_key(), lambda k: self._ball_changed(direction=k))
        self.speed_step = Stepper(abs(self.ball_rotation_rpm) or DEFAULT_RPM, 0.1, 20, 0.1,
                                  lambda v: f"<b>{v:.1f}</b> rpm", lambda v: self._ball_changed(rpm=v), width=132)
        self.speed_hint = label("", "secondary")

        hero = hbox(18, BallArtwork(), vbox(6, label("Calibration", "headline"), label(
            "Enter basic specs of the mirror ball here. Exactness is not necessary, you can refine later.",
            "lead", wrap=True), hexpand=True, valign=Gtk.Align.CENTER), valign=Gtk.Align.CENTER)
        g = SettingsGroup()
        g.add(setting_row("Diameter", "Diameter from the ball's specs.", self.diameter_step))
        g.add(setting_row("Tile size", "The little mirrors, often 5 or 10 mm.", self.tile_step))
        g.add(setting_row("Rotation", "Which way it turns, facing the ball. Nothing uses it yet.", self.rotation_seg))
        g.add(setting_row("Rotational speed", "What the motor claims. Close enough is fine.",
                          self.speed_hint, self.speed_step))
        return vbox(16, hero, g, margin_start=24, margin_end=24, margin_top=4, margin_bottom=8, vexpand=True)

    def _rotation_key(self) -> str:
        r = self.ball_rotation_rpm
        return "still" if not r else ("right" if r > 0 else "left")

    def _ball_text(self) -> str:
        """The step's whole result in one line, for the confirmation page."""
        r = self.ball_rotation_rpm
        turning = "not turning" if not r else f"{abs(r):.1f} rpm {'right' if r > 0 else 'left'}"
        return f"{self.ball_diameter:.0f} cm \u00b7 {self.ball_tile:.0f} mm tiles \u00b7 {turning}"

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
        self.height_label = label("Height above equator", hexpand=True)
        self.height_step = Stepper(0, 0, 300, 1, lambda v: f"<b>{v:.0f}</b> cm", lambda v: self._axis_numbers(height=v), width=150)
        self.distance_step = Stepper(2.4, 0.5, 15, 0.1, lambda v: f"<b>{v:.2f}</b> m", lambda v: self._axis_numbers(distance=v), width=150)
        right = vbox(
            12,
            hbox(12, vbox(2, label("Crosshair"), label("arrow keys", "hint")), Gtk.Box(hexpand=True), dpad),
            hbox(12, label("Step", hexpand=True), self.axis_step),
            vbox(6, label("Projector position"), self.above_seg),
            vbox(6, vbox(0, label("Numeric fallback"), label("if the crosshair is hard to judge", "hint", wrap=True)),
                 hbox(12, self.height_label, self.height_step),
                 hbox(12, label("Distance to ball", hexpand=True), self.distance_step)),
            hexpand=True,
        )
        return hbox(24, left, right, margin_start=24, margin_end=24, margin_top=8, margin_bottom=8, vexpand=True)

    def _build_rows_page(self) -> Gtk.Widget:
        self.rows_diagram = BallDiagram()
        self.read_rows = label("", "secondary")
        self.read_shift = label("", "secondary")
        self.rows_from_ball = label("", "secondary")  # what step 1's measurements make the starting pitch
        left = vbox(12, self.rows_diagram,
                    vbox(4, hbox(8, label("Rows", hexpand=True), self.read_rows),
                         hbox(8, label("Shift", hexpand=True), self.read_shift)),
                    label("Read only. Worked out from the pitch and the shift.", "hint", wrap=True), width_request=270)

        def nudge(name: str, sub: str, neg: tuple[str, str, Any], pos: tuple[str, str, Any]) -> Gtk.Widget:
            row = hbox(6, vbox(2, label(name), label(sub, "hint")), Gtk.Box(hexpand=True))
            for key, ic, act in (neg, pos):
                b = Gtk.Button(icon_name=ic)
                b.add_css_class("mb-btn")
                b.connect("clicked", lambda _b, a=act: self._rows_act(*a))
                row.append(hbox(4, b, kbd(key)))
            return row

        self.rows_step = Segmented([("fine", "Fine"), ("normal", "Normal"), ("coarse", "Coarse")], "normal", lambda k: self._rows_act("step", k))
        self.pattern_seg = Segmented([("all", "All rows"), ("equator", "Equator only")], "all",
                                     lambda k: self._rows_set_pattern(k))
        right = vbox(
            12,
            nudge("Shift", "up / down", ("↑", "go-up-symbolic", ("offset", +1)), ("↓", "go-down-symbolic", ("offset", -1))),
            nudge("Pitch", "narrower / wider", ("←", "go-previous-symbolic", ("pitch", -1)), ("→", "go-next-symbolic", ("pitch", +1))),
            hbox(12, label("Step", hexpand=True), self.rows_step),
            vbox(6, vbox(0, label("Pattern"), label("shown on the ball", "hint")), self.pattern_seg),
            vbox(4, vbox(0, label("Starting pitch"), label("From the ball's size, set on step 1", "hint", wrap=True)),
                 self.rows_from_ball),
            hexpand=True,
        )
        return hbox(24, left, right, margin_start=24, margin_end=24, margin_top=8, margin_bottom=8, vexpand=True)

    def _ball_changed(self, diameter: float | None = None, tile: float | None = None, rpm: float | None = None,
                      direction: str | None = None) -> None:
        """The ball's measurements only seed the pitch; the painted rows then decide it. Without a seed within
        about 20 % the pattern is ambiguous, because every second row lining up looks just as convincing."""
        if getattr(self, "_syncing", False):
            return
        if rpm is not None or direction is not None:
            sign = {"still": 0.0, "right": 1.0, "left": -1.0}[direction or self._rotation_key()]
            self.ball_rotation_rpm = sign * (self.speed_step.value if rpm is None else rpm)
        if diameter is not None or tile is not None:
            self.ball_diameter = self.ball_diameter if diameter is None else diameter
            self.ball_tile = self.ball_tile if tile is None else tile
            self.ball_saved = True
            if self.rows:  # the rows step was already visited: re-seed it from the new measurements
                self.rows.seed_pitch(self._seeded_pitch())
                self.rows_cleared = False
                self._send_preview()
        self._update()

    def _seeded_pitch(self) -> float:
        return cm.pitch_from_rows(cm.rows_from_tile(self.ball_diameter, self.ball_tile))

    @property
    def ball_measured(self) -> bool:
        """The ball was measured on step 1 this time round, so the pitch it seeds is a real value."""
        return (self.ball_diameter, self.ball_tile) != self._ball_initial[:2]

    @property
    def rows_will_save(self) -> bool:
        """Whether finish() will write a row grid, and so whether scenes will snap to rows.

        An untouched default pitch is not saved, because that alone would switch snapping on. One the ball's own
        measurements seeded is worth keeping even if the rows were only looked at."""
        return bool(self.rows and not self.rows_cleared and (self.rows.dirty or self.ball_measured))

    def _build_done_page(self) -> Gtk.Widget:
        self.done_diagram = BallDiagram()
        left = vbox(12, self.done_diagram, label("The projector shows the same globe on the ball.", "hint", wrap=True),
                    width_request=270)
        self.done_ball, self.done_silhouette, self.done_axis, self.done_rows = (label("", "secondary") for _ in range(4))
        summary = vbox(4)
        for name, w in (("Ball", self.done_ball), ("Silhouette", self.done_silhouette), ("Ball axis", self.done_axis),
                        ("Tile rows", self.done_rows)):
            summary.append(hbox(8, label(name, hexpand=True), w))
        note = hbox(12, icon("dialog-information-symbolic", 18), label(
            "Finish writes these values to the configuration and reloads the show. Cancel leaves the saved "
            "calibration untouched.", "secondary", wrap=True, hexpand=True), valign=Gtk.Align.CENTER)
        note.add_css_class("note-box")
        right = vbox(12, label("Calibration done!", "headline"), summary, note, hexpand=True)
        return hbox(24, left, right, margin_start=24, margin_end=24, margin_top=8, margin_bottom=8, vexpand=True)

    def _spin_tick(self) -> bool:
        """Turn the globe on both the confirmation page and the projector while step 4 is showing."""
        if self._closing or self.step_no != STEP_DONE:
            self._spin_timer = 0
            return False
        self.done_diagram.spin = (self.done_diagram.spin + SPIN_DPS * SPIN_TICK_MS / 1000) % 360
        self.done_diagram.queue_draw()
        return True

    def _install_keys(self) -> None:
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

    def _on_key(self, _c: Any, keyval: int, _code: int, mods: Gdk.ModifierType) -> bool:
        if mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        name = Gdk.keyval_name(keyval) or ""
        mapper = {STEP_BALL: ball_key_to_action, STEP_SILHOUETTE: key_to_action, STEP_AXIS: axis_key_to_action,
                  STEP_ROWS: rows_key_to_action, STEP_DONE: done_key_to_action}[self.step_no]
        hit = mapper(name)
        if hit is None:
            return False
        action, arg = hit
        if action == "cancel":
            self.cancel()
        elif action == "finish":
            self.finish()
        elif action == "next":
            self._go(self.step_no + 1)
        elif action == "back":
            self._go(self.step_no - 1) if self.step_no > 1 else None
        elif self.step_no == STEP_SILHOUETTE:
            self._act(action, arg)
        elif self.step_no == STEP_AXIS:
            self._axis_act(action, arg)
        elif self.step_no == STEP_ROWS:
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

    def _send_preview(self) -> None:
        if not self.session:
            return
        payload = self.session.preview()
        if self.step_no == STEP_AXIS and self.axis:
            cal = self.axis.calibration()
            payload.update(roll_deg=cal.roll_deg, tilt_deg=cal.tilt_deg, lines=False, grid=True, blink=False,
                           crosshair=list(self.axis.crosshair_norm()))
        elif self.step_no == STEP_ROWS and self.rows:
            payload.update(lines=False, grid=False, blink=False, rows=self.rows.preview())
        elif self.step_no == STEP_DONE:
            payload.update(lines=False, grid=True, blink=False, spin=True)
        self.main.send("calibration.preview", **payload)

    # --- steps 2 and 3 -------------------------------------------------------------------

    INSTRUCTIONS = {
        STEP_BALL: "",  # the page opens with its own headline
        STEP_SILHOUETTE: "Move each line inwards until its blinking centre mark disappears behind the ball, then "
                         "one step back out. The lines then sit just outside the ball's edge.",
        STEP_AXIS: "Move the crosshair onto the hanger eyelet at the top of the ball.",
        STEP_ROWS: "Nudge until the painted rows line up with the real tile rows. Shift moves them all together; "
                   "pitch stretches them, so get the equator right first and then the rest.",
        STEP_DONE: "",
    }

    def _go(self, step: int) -> None:
        """Switch step. Leaving step 2 writes the crosshair's roll and tilt into the calibration."""
        s = self.session
        if s is None or step == self.step_no:
            return
        if self.step_no == STEP_AXIS and self.axis:
            s.cal = self.axis.calibration()
        if step == STEP_AXIS:  # rebuilt on every entry: the lines may have changed since the last visit
            self.axis = AxisSession(s.cal, s.width, s.height, step=s.step)
            self.axis_step.select(s.step)
            self.above_seg.select("above" if self.axis.above else "below")
        if step == STEP_ROWS and self.rows is None:
            self.rows = RowsSession(*self._initial_rows())
        if step == STEP_DONE and not self._spin_timer:
            self._spin_timer = GLib.timeout_add(SPIN_TICK_MS, self._spin_tick)
        self.step_no = step
        self.pages.set_visible_child_name(str(step))
        self.footers.set_visible_child_name(str(step))
        self.instruction.set_text(self.INSTRUCTIONS[step])
        self.instruction.set_visible(bool(self.INSTRUCTIONS[step]))  # steps that carry their own title need no line
        self._send_preview()
        self._update()

    def _initial_rows(self) -> tuple[float, float]:
        b = self.main.store.load().ball
        if b.row_pitch_deg:
            return b.row_pitch_deg, b.row_offset_deg
        if self.ball_saved:  # never calibrated, but the ball has been measured (on file, or on step 1)
            pitch = self._seeded_pitch()
            return pitch, parity_offset(pitch)  # the row count says where the seam falls
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
        if action in ("offset", "pitch"):
            self.rows_cleared = False  # nudged again after a reset: it is a real row grid once more
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
        elif self.session and (size := self._window_size()) and self.session.resize(*size):
            self._send_preview()  # the output window was resized: the ball keeps its size in pixels
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
        if s and self.step_no == STEP_SILHOUETTE and s.aspect_warning:
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
        for w in (self.finish_btn, *self.next_btns.values(), self.blink_btn, self.grid_btn, self.step_seg,
                  *(b for step, b in self.reset_btns.items() if step != STEP_BALL)):
            w.set_sensitive(active)
        self._update_ball()
        dark = Adw.StyleManager.get_default().get_dark()
        self.diagram.dark = self.axis_diagram.dark = self.rows_diagram.dark = self.done_diagram.dark = dark
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
            lb.set_text(f"{fmt_px(s.line_px(side)[0])} px")
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
            self.height_label.set_text(f"Height {'above' if self.axis.above else 'below'} equator")
            self.axis_diagram.roll = self.axis.calibration().roll_deg
            self.axis_diagram.tilt = self.axis.calibration().tilt_deg
            self.axis_diagram.cross = (self.axis.px, self.axis.py)
            self.axis_diagram.queue_draw()
        if self.rows:
            self.read_rows.set_text(self.rows.text())
            self.read_shift.set_text(self.rows.shift_text())
            self.rows_from_ball.set_text(f"{self.ball_diameter:.0f} cm · {self.ball_tile:.0f} mm → {self._seeded_pitch():.1f}°")
            self.rows_diagram.roll, self.rows_diagram.tilt = s.cal.roll_deg, s.cal.tilt_deg
            self.rows_diagram.rows = (self.rows.pitch, self.rows.offset, self.rows.mode)
            self.rows_diagram.queue_draw()
        if self.step_no == STEP_DONE:
            self.done_ball.set_text(self._ball_text())
            self.done_silhouette.set_text(f"{fmt_px(sw)} × {fmt_px(sh)} px at {fmt_px(cx)}, {fmt_px(cy)}")
            self.done_axis.set_text(" · ".join(self.axis.texts()) if self.axis and self.axis.dirty else "Not set")
            self.done_rows.set_text(self.rows.text() if self.rows_will_save else "Not set")
            self.done_diagram.roll, self.done_diagram.tilt = s.cal.roll_deg, s.cal.tilt_deg
            self.done_diagram.queue_draw()

    def _update_ball(self) -> None:
        """Step 1 describes the hardware, so it reads the same with or without a renderer."""
        turning = self.ball_rotation_rpm != 0
        self.rotation_seg.select(self._rotation_key())
        self.speed_step.set_sensitive(turning)
        self.speed_hint.set_text(f"≈ {60 / self.speed_step.value:.0f} s per turn" if turning else "")

    RESET_BODY = {
        STEP_BALL: f"The ball goes back to {DEFAULT_DIAMETER_CM:g} cm with {DEFAULT_TILE_MM:g} mm tiles, and to "
                   "not turning.",
        STEP_SILHOUETTE: "The lines go back to a centred, round ball at 70 % of the screen height.",
        STEP_AXIS: "The ball's axis goes back to upright, with the projector level with the equator.",
        STEP_ROWS: f"The row grid goes back to {DEFAULT_PITCH:g}° per row, and scenes stop snapping to rows.",
    }

    def _step_dirty(self, step: int) -> bool:
        ball = (self.ball_diameter, self.ball_tile, self.ball_rotation_rpm) != self._ball_initial
        return bool({STEP_BALL: ball, STEP_SILHOUETTE: self.session and self.session.dirty,
                     STEP_AXIS: self.axis and self.axis.dirty,
                     STEP_ROWS: self.rows and self.rows.dirty}.get(step))

    def _on_reset(self, step: int) -> None:
        """Each page resets only what it sets. Asked for only when there is something to lose."""
        if not self.session and step != STEP_BALL:
            return
        if not self._step_dirty(step):
            self._do_reset(step)
            return
        dlg = Adw.AlertDialog(heading="Reset to defaults?", body=self.RESET_BODY[step])
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset to defaults")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "reset" and self._do_reset(step))
        dlg.present(self)

    def _do_reset(self, step: int) -> None:
        if step == STEP_BALL:  # the only page that does not need a session: it describes the hardware
            self.ball_diameter, self.ball_tile = DEFAULT_DIAMETER_CM, DEFAULT_TILE_MM
            self.ball_rotation_rpm = 0.0
            self._syncing = True
            try:
                self.diameter_step.set_value(self.ball_diameter)
                self.tile_step.set_value(self.ball_tile)
                self.speed_step.set_value(DEFAULT_RPM)
                self.rotation_seg.select("still")
            finally:
                self._syncing = False
            self.ball_saved = False  # as if never measured: the rows step falls back to its own default
            if self.rows:
                self.rows.seed_pitch(DEFAULT_PITCH)
            self._send_preview()
            self._update()
            return
        s = self.session
        if s is None:
            return
        if step == STEP_SILHOUETTE:
            s.reset(self._default_calibration())
        elif step == STEP_AXIS:
            s.cal = replace(s.cal, roll_deg=0.0, tilt_deg=0.0)
            self.axis = AxisSession(s.cal, s.width, s.height, step=s.step)
            self.above_seg.select("above")
        else:
            # Back to "never calibrated": the default pitch with snapping off, which finish() stores as null.
            self.rows = RowsSession(DEFAULT_PITCH, 0.0, step=self.rows.step if self.rows else "normal",
                                    mode=self.rows.mode if self.rows else "all")
            self.rows_cleared = True
        self._send_preview()
        self._update()

    def finish(self) -> None:
        if not self.session or not self.main.connected:
            return
        if self.step_no == STEP_AXIS and self.axis:  # finishing straight from the axis step keeps the crosshair's angles
            self.session.cal = self.axis.calibration()
        c = self.session.cal
        if not c.is_valid():
            self.main.toast("The lines are too close together.")
            return
        data = CalibrationConfig.from_calibration(c).model_dump()
        data["calibrated_at"] = datetime.now().isoformat(timespec="minutes")
        updates: dict[str, Any] = {"calibration": data}
        ball: dict[str, Any] = {}
        if (self.ball_diameter, self.ball_tile, self.ball_rotation_rpm) != self._ball_initial:  # for the next time
            ball.update(diameter_cm=self.ball_diameter, tile_mm=self.ball_tile,
                        rotation_rpm=round(self.ball_rotation_rpm, 3))
        if self.rows_cleared:  # the rows step was reset: snapping goes off again
            ball.update(row_pitch_deg=None, row_offset_deg=0.0)
        elif self.rows_will_save:
            ball.update(row_pitch_deg=round(self.rows.pitch, 4), row_offset_deg=round(self.rows.offset, 4))
        if ball:
            updates["ball"] = ball
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
        if self._spin_timer:
            GLib.source_remove(self._spin_timer)
            self._spin_timer = 0
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
