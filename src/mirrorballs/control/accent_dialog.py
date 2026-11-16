"""Accent colours dialog: the named colours the main window offers as the accent.

The list lives in config.yaml (``accent.colors``). Every edit is saved shortly after it is made, so
there is no Save button; picking one of them as the accent happens on the main window.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import viewmodel as vm  # noqa: E402
from .widgets import SettingsGroup, hbox, label, push_button, vbox  # noqa: E402

SAVE_DELAY_MS = 400  # typing a name writes the file once, not once per key


class AccentDialog(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Accent colors", modal=True, transient_for=main, default_width=520, default_height=480)
        self.add_css_class("mb-dialog")
        self.main = main
        self.colors: list[dict[str, str]] = [{"name": c.name, "color": c.color.lower()} for c in main.store.load().accent.colors]
        self._save_timer = 0

        view = Adw.ToolbarView()
        self.set_content(view)
        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        header.set_title_widget(label("Accent colors", "title-label"))
        view.add_top_bar(header)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        page = vbox(18, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        scroller.set_child(page)
        view.set_content(scroller)

        self.add_btn = push_button("Add color", "tall", "primary", icon_name="list-add-symbolic")
        self.add_btn.add_css_class("on-accent")
        self.add_btn.set_halign(Gtk.Align.START)
        self.add_btn.connect("clicked", lambda *_: self.add_color())
        page.append(self.add_btn)
        self.group = SettingsGroup("Named colors", "Pick one as the accent in the Main box. Scenes read it as accent.")
        page.append(self.group)
        self._rebuild()

        self.connect("close-request", self._on_close)
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", lambda _c, k, *_a: (self.close() or True) if k == Gdk.KEY_Escape else False)
        self.add_controller(ctrl)

    def _on_close(self, *_a: Any) -> bool:
        if self._save_timer:  # a name still being typed
            self._save_now()
        self.main.accent_dialog = None
        return False

    def _rebuild(self) -> None:
        g = self.group
        g.clear()
        if not self.colors:
            row = hbox(0, label("No accent colors yet.", "secondary"))
            row.add_css_class("setting-row")
            g.add(row)
        for entry in self.colors:
            g.add(self._row(entry))

    def _row(self, entry: dict[str, str]) -> Gtk.Widget:
        rgba = Gdk.RGBA()
        rgba.parse(entry["color"])
        btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba, valign=Gtk.Align.CENTER)
        btn.add_css_class("accent-swatch")
        btn.connect("notify::rgba", lambda b, _p: self._recolor(entry, b.get_rgba()))
        name = Gtk.Entry(text=entry["name"], hexpand=True, valign=Gtk.Align.CENTER, placeholder_text="Name")
        name.add_css_class("mb-entry")
        name.connect("changed", lambda e: self._rename(entry, e.get_text()))
        delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        delete.add_css_class("flat-icon")
        delete.set_tooltip_text("Delete this color")
        delete.connect("clicked", lambda *_: self.delete_color(entry))
        row = hbox(12, btn, name, delete)
        row.add_css_class("setting-row")
        return row

    # --- edits ---------------------------------------------------------------------------

    def add_color(self) -> None:
        """A new entry with the current accent, so "keep this one" is a single click."""
        taken = {c["name"] for c in self.colors}
        n = len(self.colors) + 1
        while f"Color {n}" in taken:
            n += 1
        self.colors.append({"name": f"Color {n}", "color": self.main.accent_hex()})
        self._rebuild()
        self._save_now()

    def delete_color(self, entry: dict[str, str]) -> None:
        self.colors = [c for c in self.colors if c is not entry]
        self._rebuild()
        self._save_now()

    def _rename(self, entry: dict[str, str], text: str) -> None:
        entry["name"] = text.strip()
        self._save_later()

    def _recolor(self, entry: dict[str, str], rgba: Gdk.RGBA) -> None:
        old, entry["color"] = entry["color"], vm.rgb_to_hex((rgba.red, rgba.green, rgba.blue))
        if old == entry["color"]:
            return
        self._save_now()
        if self.main.accent_hex() == old:  # the colour on the ball is this one: it changes with it
            self.main.set_accent(entry["color"])

    # --- saving --------------------------------------------------------------------------

    def _save_later(self) -> None:
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timer)

    def _on_save_timer(self) -> bool:
        self._save_timer = 0
        self._save_now()
        return False

    def _save_now(self) -> None:
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
        try:
            self.main.store.update({"accent": {"colors": [dict(c) for c in self.colors]}})
        except Exception as e:  # noqa: BLE001
            self.main.toast(f"Could not save the accent colors: {e}")
            return
        self.main.accent_colors_changed()
