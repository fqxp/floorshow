"""Calibration dialog: what is calibrated right now, and the way into the calibration wizard.

Separate from Configuration on purpose. Calibration is redone every time the rig is set up — the
projector never stands in quite the same place twice — while the configuration usually stays as it is.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import config_model as cm  # noqa: E402
from .widgets import SettingsGroup, hbox, label, push_button, setting_row, vbox  # noqa: E402


class CalibrationDialog(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Calibration", modal=True, transient_for=main, default_width=560, default_height=420)
        self.add_css_class("mb-dialog")
        self.main = main
        self.store = main.store
        self.cfg = self.store.load()

        view = Adw.ToolbarView()
        self.set_content(view)
        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        header.set_title_widget(label("Calibration", "title-label"))
        view.add_top_bar(header)

        page = vbox(18, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        self.cal_centre, self.cal_size = label("", "secondary"), label("", "secondary")
        self.cal_axis, self.cal_when = label("Not set", "secondary"), label("", "secondary")
        g = SettingsGroup("Current calibration")
        g.add(setting_row("Centre", None, self.cal_centre))
        g.add(setting_row("Size", None, self.cal_size))
        g.add(setting_row("Ball axis and tile rows", "Optional advanced steps.", self.cal_axis))
        g.add(setting_row("Last calibrated", None, self.cal_when))
        page.append(g)
        cal = push_button("Calibrate…", "tall", "primary")
        cal.add_css_class("on-accent")
        cal.connect("clicked", lambda *_: self.main.open_calibration())
        reset = push_button("Reset to defaults", "tall")
        reset.connect("clicked", lambda *_: self._confirm_reset())
        page.append(hbox(8, cal, reset, label("Reset asks for confirmation.", "hint", hexpand=True), valign=Gtk.Align.CENTER))
        view.set_content(page)

        self.connect("close-request", self._on_close)
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", lambda _c, k, *_a: (self.close() or True) if k == Gdk.KEY_Escape else False)
        self.add_controller(ctrl)
        self._update()

    def _on_close(self, *_a: Any) -> bool:
        self.main.calibration_dialog = None
        self.main.refresh()
        return False

    # --- live updates from the renderer --------------------------------------------------

    def on_state(self, _state: dict[str, Any]) -> None:
        self._update()

    def on_connection(self, _up: bool) -> None:
        self._update()

    def _update(self) -> None:
        w = (self.main.state or {}).get("window") if self.main.connected else None
        width, height = (w["width"], w["height"]) if w else (1920, 1080)
        cal = self.cfg.calibration
        centre, size = cm.calibration_readout(cal.model_dump() if cal else None, width, height)
        self.cal_centre.set_text(centre)
        self.cal_size.set_text(size)
        axis = bool(cal and (cal.roll_deg or cal.tilt_deg)) or self.cfg.ball.row_pitch_deg is not None
        self.cal_axis.set_text("Set" if axis else "Not set")
        self.cal_when.set_text(cm.describe_time(cal.calibrated_at if cal else None))

    def reload(self) -> None:
        """Re-read config.yaml: the wizard writes it while this dialog stays open."""
        self.cfg = self.store.load()
        self._update()

    def _confirm_reset(self) -> None:
        dlg = Adw.AlertDialog(heading="Reset calibration to defaults?",
                              body="Centre, size, ball axis and tile rows go back to their factory values. This cannot be undone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset to defaults")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def go(_d: Any, r: str) -> None:
            if r == "reset":
                self.store.update({"calibration": None, "ball": {"row_pitch_deg": None, "row_offset_deg": 0}})
                self.cfg = self.main.cfg = self.store.load()
                self.main.send("reload")
                self._update()
                self.main.toast("Calibration reset.")

        dlg.connect("response", go)
        dlg.present(self)
