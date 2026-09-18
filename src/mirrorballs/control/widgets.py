"""Small GTK building blocks shared by the main window, the configuration dialog and the calibration
window. Everything is styled by style.css (design tokens)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

def label(text: str = "", *classes: str, xalign: float = 0.0, wrap: bool = False, hexpand: bool = False) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=xalign, wrap=wrap, hexpand=hexpand)
    for c in classes:
        if c:
            lb.add_css_class(c)
    return lb


def kbd(text: str) -> Gtk.Label:
    lb = label(text, "kbd", xalign=0.5)
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def icon(name: str, size: int = 16) -> Gtk.Image:
    im = Gtk.Image.new_from_icon_name(name)
    im.set_pixel_size(size)
    return im


def hbox(spacing: int = 8, *children: Gtk.Widget, **kw: Any) -> Gtk.Box:
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing, **kw)
    for c in children:
        b.append(c)
    return b


def vbox(spacing: int = 8, *children: Gtk.Widget, **kw: Any) -> Gtk.Box:
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)
    for c in children:
        b.append(c)
    return b


def set_classes(widget: Gtk.Widget, on: dict[str, bool]) -> None:
    for name, enabled in on.items():
        if enabled:
            widget.add_css_class(name)
        else:
            widget.remove_css_class(name)


def push_button(text: str, *classes: str, kbd_text: str | None = None, icon_name: str | None = None) -> Gtk.Button:
    b = Gtk.Button()
    b.add_css_class("mb-btn")
    for c in classes:
        b.add_css_class(c)
    row = hbox(8, halign=Gtk.Align.CENTER)
    if icon_name:
        row.append(icon(icon_name))
    if text:
        row.append(label(text))
    if kbd_text:
        row.append(kbd(kbd_text))
    b.set_child(row)
    return b




class Stepper(Gtk.Box):
    """[-] value [+] control (design: 132 px wide). ``fmt`` renders the value; ``on_change`` gets the new one."""

    def __init__(self, value: float, lo: float, hi: float, step: float = 1, fmt: Callable[[float], str] | None = None,
                 on_change: Callable[[float], None] | None = None, width: int = 132):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.lo, self.hi, self.step = lo, hi, step
        self.fmt = fmt or (lambda v: f"{v:g}")
        self.on_change = on_change
        self.value = value
        self.add_css_class("stepper")
        self.set_size_request(width, 36)
        self.minus = Gtk.Button(icon_name="list-remove-symbolic")
        self.plus = Gtk.Button(icon_name="list-add-symbolic")
        self.minus.update_property([Gtk.AccessibleProperty.LABEL], ["Decrease"])
        self.plus.update_property([Gtk.AccessibleProperty.LABEL], ["Increase"])
        self.text = label("", xalign=0.5, hexpand=True)
        for w in (self.minus, self.text, self.plus):
            self.append(w)
        self.minus.connect("clicked", lambda *_: self.set_value(self.value - self.step, notify=True))
        self.plus.connect("clicked", lambda *_: self.set_value(self.value + self.step, notify=True))
        self._show()

    def set_value(self, v: float, notify: bool = False) -> None:
        v = min(self.hi, max(self.lo, round(v, 6)))
        changed = v != self.value
        self.value = v
        self._show()
        if notify and changed and self.on_change:
            self.on_change(v)

    def _show(self) -> None:
        self.text.set_markup(self.fmt(self.value))
        self.minus.set_sensitive(self.value > self.lo)
        self.plus.set_sensitive(self.value < self.hi)


class Segmented(Gtk.Box):
    """Two or more mutually exclusive buttons (the design's segmented control)."""

    def __init__(self, options: list[tuple[str, str]], selected: str, on_change: Callable[[str], None] | None = None,
                 fill: bool = False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.add_css_class("segmented")
        self.on_change = on_change
        self._guard = False
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        first: Gtk.ToggleButton | None = None
        for key, text in options:
            b = Gtk.ToggleButton(label=text, hexpand=fill)
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.connect("toggled", self._toggled, key)
            self.buttons[key] = b
            self.append(b)
        self.select(selected)

    def select(self, key: str) -> None:
        self._guard = True
        try:
            self.buttons[key].set_active(True)
        finally:
            self._guard = False

    def selected(self) -> str:
        return next(k for k, b in self.buttons.items() if b.get_active())

    def _toggled(self, btn: Gtk.ToggleButton, key: str) -> None:
        if btn.get_active() and not self._guard and self.on_change:
            self.on_change(key)


def status_chip(text: str, kind: str = "ok") -> Gtk.Box:
    """Small status pill: kind is "ok" (accent), "warn" (amber) or "" (neutral)."""
    box = hbox(6, valign=Gtk.Align.CENTER)
    box.add_css_class("tag")
    if kind:
        box.add_css_class(f"tag-{kind}")
        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.add_css_class("dot-small")
        box.append(dot)
    box.append(label(text))
    return box


def setting_row(title: str, subtitle: str | None = None, *controls: Gtk.Widget) -> Gtk.Box:
    """One row of a settings group: title and hint on the left, controls on the right."""
    text = vbox(1, label(title), hexpand=True, valign=Gtk.Align.CENTER)
    if subtitle:
        text.append(label(subtitle, "row-hint", wrap=True))
    row = hbox(12, text, hbox(8, *controls, valign=Gtk.Align.CENTER, halign=Gtk.Align.END))
    row.add_css_class("setting-row")
    return row


class SettingsGroup(Gtk.Box):
    """Section heading (optional) and a rounded card of rows separated by hairlines."""

    def __init__(self, title: str | None = None, subtitle: str | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if title:
            head = vbox(2, label(title, "group-title"))
            if subtitle:
                head.append(label(subtitle, "secondary"))
            self.append(head)
        self.card = vbox(0)
        self.card.add_css_class("group-card")
        self.append(self.card)
        self._n = 0

    def add(self, row: Gtk.Widget) -> Gtk.Widget:
        if self._n:
            row.add_css_class("row-sep")
        self.card.append(row)
        self._n += 1
        return row

    def clear(self) -> None:
        while (c := self.card.get_first_child()) is not None:
            self.card.remove(c)
        self._n = 0
