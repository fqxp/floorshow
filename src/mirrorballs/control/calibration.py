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

from ..common.config import CalibrationConfig  # noqa: E402
from ..common.geometry import Calibration  # noqa: E402
from . import tokens  # noqa: E402
from .calibration_model import DEFAULT_SIZE, Session, fmt_px, key_to_action  # noqa: E402
from .widgets import Segmented, hbox, icon, kbd, label, push_button, set_classes, vbox  # noqa: E402

BEGIN_TIMEOUT_S = 1.5  # wait this long for the renderer to report its window size before assuming 1920x1080


class Diagram(Gtk.DrawingArea):
    """The output screen in miniature with the four lines and the ellipse they enclose."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(300)
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


class CalibrationWindow(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Calibrate output", modal=True, transient_for=main, default_width=720, default_height=560)
        self.add_css_class("mb-dialog")
        self.main = main
        self.session: Session | None = None
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
        for n, (name, optional) in enumerate((("Silhouette", False), ("Ball axis", True), ("Tile rows", True)), start=1):
            pill = hbox(8, label(str(n), "pip", xalign=0.5), label(name, hexpand=False))
            if optional:
                pill.append(label("optional", "hint"))
                pill.set_sensitive(False)
                pill.set_tooltip_text("Coming later")
            else:
                pill.get_first_child().add_css_class("current")
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
        left = vbox(12, self.diagram, hbox(8, self.blink_btn, self.grid_btn), readouts, width_request=300)

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
        cancel = push_button("Cancel", "tall", kbd_text="Esc")
        cancel.connect("clicked", lambda *_: self.cancel())
        self.advanced_btn = push_button("Advanced…", "tall")
        self.advanced_btn.set_sensitive(False)
        self.advanced_btn.set_tooltip_text("Ball axis and tile rows: coming later")
        self.finish_btn = push_button("Finish", "tall", "primary", kbd_text="Enter")
        self.finish_btn.add_css_class("on-accent")
        self.finish_btn.connect("clicked", lambda *_: self.finish())
        footer = hbox(8, self.reset_btn, Gtk.Box(hexpand=True), cancel, self.advanced_btn, self.finish_btn,
                      margin_start=24, margin_end=24, margin_top=8, margin_bottom=16)
        view.set_content(vbox(8, self.instruction, body, footer, margin_top=8))

    def _install_keys(self) -> None:
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

    def _on_key(self, _c: Any, keyval: int, _code: int, mods: Gdk.ModifierType) -> bool:
        if mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        hit = key_to_action(Gdk.keyval_name(keyval) or "")
        if hit is None:
            return False
        action, arg = hit
        if action == "cancel":
            self.cancel()
        elif action == "finish":
            self.finish()
        else:
            self._act(action, arg)
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
        if self.session:
            self.main.send("calibration.preview", **self.session.preview())

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
        if s and s.aspect_warning:
            banners.append((s.aspect_message(), False))
        key = tuple(banners)
        if key != getattr(self, "_banner_key", None):
            self._banner_key = key
            while (c := self.banner_box.get_first_child()) is not None:
                self.banner_box.remove(c)
            for text, busy in banners:
                self.banner_box.append(self._banner(text, busy))

        active = s is not None and self.main.connected
        for w in (self.finish_btn, self.reset_btn, self.blink_btn, self.grid_btn, self.step_seg):
            w.set_sensitive(active)
        self.edited_chip.set_visible(bool(s and s.dirty))
        if s is None:
            for w in (self.read_centre, self.read_size, self.read_aspect, *self.line_read.values()):
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
        self.diagram.dark = Adw.StyleManager.get_default().get_dark()
        self.diagram.queue_draw()

    # --- finishing -----------------------------------------------------------------------

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
        c = self.session.cal
        if not c.is_valid():
            self.main.toast("The lines are too close together.")
            return
        data = CalibrationConfig.from_calibration(c).model_dump()
        data["calibrated_at"] = datetime.now().isoformat(timespec="minutes")
        try:
            self.main.store.update({"calibration": data})
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
