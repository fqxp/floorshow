"""The source picker below the clock strip (audio sources with level meters, MIDI ports with beat previews) and the
popover over the Audio option. Only drawing and widgets; what they show comes from sources_model.

Meters and beat tiles move every frame, the chips themselves are rebuilt only when the list of sources changes, so a
level change never relayouts the window. Colours keep to the design's rules: the accent for levels, amber near
clipping, red only for a beat.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import sources_model as sm, tokens  # noqa: E402
from .widgets import hbox, label  # noqa: E402

METER_FLOOR_DB = -60.0
CLIP_DB = -1.0
REPAINT_S = 1 / 30
HOVER_DELAY_MS = 180
MAX_ROWS_PX = 64  # two lines of chips (2 x 29 px + 6 px between); more scroll


def _rounded(cr: Any, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class LevelMeter(Gtk.DrawingArea):
    """One thin column per channel: RMS as the bar, the peak as a tick. Its height is in the stylesheet (em)."""

    def __init__(self, channels: int = 2) -> None:
        super().__init__()
        self.add_css_class("level-meter")
        self.set_valign(Gtk.Align.CENTER)
        self.dark = True
        self.levels: tuple[float, ...] = (METER_FLOOR_DB,) * channels
        self.peaks: tuple[float, ...] = self.levels
        self._painted = 0.0
        self.set_content_width(max(1, channels) * 4)
        self.set_draw_func(self._draw)

    def set_levels(self, levels: tuple[float, ...], peaks: tuple[float, ...]) -> None:
        if levels == self.levels and peaks == self.peaks:
            return
        now = time.monotonic()
        if now - self._painted < REPAINT_S:
            return
        self._painted = now
        if len(levels) != len(self.levels):
            self.set_content_width(max(1, len(levels)) * 4)
        self.levels, self.peaks = levels, peaks
        self.queue_draw()

    def _draw(self, _a: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        pal = tokens.palette(self.dark)
        n = max(1, len(self.levels))
        col = w / n
        for i in range(n):
            x = i * col
            cr.set_source_rgb(*tokens.hex_to_rgb(pal["well"]))
            _rounded(cr, x, 0, col - 1, h, 1)
            cr.fill()
            lv = self.levels[i] if i < len(self.levels) else METER_FLOOR_DB
            frac = max(0.0, min(1.0, (lv - METER_FLOOR_DB) / -METER_FLOOR_DB))
            if frac > 0:
                cr.set_source_rgb(*tokens.hex_to_rgb(pal["amber"] if lv > CLIP_DB else pal["acc"]))
                cr.rectangle(x, h * (1 - frac), col - 1, h * frac)
                cr.fill()
            pk = self.peaks[i] if i < len(self.peaks) else METER_FLOOR_DB
            pf = max(0.0, min(1.0, (pk - METER_FLOOR_DB) / -METER_FLOOR_DB))
            if pf > 0:
                cr.set_source_rgb(*tokens.hex_to_rgb(pal["t2"]))
                cr.rectangle(x, max(0.0, h * (1 - pf) - 1), col - 1, 1.5)
                cr.fill()


class Pulse(Gtk.DrawingArea):
    """A small beat tile: red on the beat, fading into its ground. Plain ground when there is no beat."""

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("pulse")
        self.set_valign(Gtk.Align.CENTER)
        self.dark = True
        self.intensity = 0.0
        self._painted = 0.0
        self.set_draw_func(self._draw)

    def set_phase(self, phase: float | None) -> None:
        value = 0.0 if phase is None else tokens.beat_intensity(phase)
        if abs(value - self.intensity) < 0.02:
            return
        now = time.monotonic()
        if now - self._painted < REPAINT_S:
            return
        self._painted = now
        self.intensity = value
        self.queue_draw()

    def _draw(self, _a: Gtk.DrawingArea, cr: Any, w: int, h: int) -> None:
        pal = tokens.palette(self.dark)
        cr.set_source_rgb(*tokens.mix(pal["well"], pal["beat"], self.intensity))
        _rounded(cr, 0, 0, w, h, min(w, h) / 4)
        cr.fill()


class SourceChip(Gtk.ToggleButton):
    def __init__(self, item: sm.Item, audio: bool) -> None:
        super().__init__()
        self.add_css_class("chip-toggle")
        self.add_css_class("small")
        self.add_css_class("source-chip")
        self.item_id = item.id
        self.meter = LevelMeter(len(item.levels) or 2) if audio else None
        self.pulse = None if audio else Pulse()
        # An output's monitor and an input are told apart by an icon, which leaves the chip room for the name
        shown = item.name.removeprefix("Monitor of ") if item.kind == "monitor" else item.name
        kind_icon = None
        if audio:
            kind_icon = Gtk.Image.new_from_icon_name(
                "audio-speakers-symbolic" if item.kind == "monitor" else "audio-input-microphone-symbolic")
            kind_icon.add_css_class("chip-kind")
        self.name = label(shown)
        self.name.set_ellipsize(Pango.EllipsizeMode.END)
        # Never squeezed below a readable start of the name (the row scrolls sideways instead), never wider than
        # a long port name needs to be told apart
        self.name.set_width_chars(min(len(shown), 12))
        self.name.set_max_width_chars(20)
        self.status = label("", "chip-status")
        box = hbox(6, *[w for w in (self.meter or self.pulse, kind_icon, self.name, self.status) if w is not None])
        self.set_child(box)
        self.set_tooltip_text(item.name if item.id == item.name else f"{item.name}\n{item.id}")
        self.item = item
        self.update(item)

    def update(self, item: sm.Item) -> None:
        self.item = item
        if self.status.get_text() != item.status:
            self.status.set_text(item.status)
            self.status.set_visible(bool(item.status))
        if self.meter is not None:
            self.meter.set_levels(item.levels, item.peaks)
        if item.dim != self.has_css_class("dim"):
            (self.add_css_class if item.dim else self.remove_css_class)("dim")

    def tick(self, now: float) -> None:
        if self.pulse is not None:
            self.pulse.set_phase(sm.beat_phase(self.item.bpm, self.item.beat_time, now))

    def set_dark(self, dark: bool) -> None:
        for w in (self.meter, self.pulse):
            if w is not None:
                w.dark = dark
                w.queue_draw()


class SourcePicker(Gtk.Box):
    """A row of chips, one per audio source or MIDI port; the chosen one is checked. ``on_pick(id)`` on a click."""

    def __init__(self, title: str, audio: bool, on_pick: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)  # the title above: the chips get the width
        self.add_css_class("source-picker")
        self.audio = audio
        self.on_pick = on_pick
        self.title = label(title.upper(), "hint")
        # Every source in view at once: the chips wrap like words (each as wide as its name), two lines before the
        # rest scroll. (A FlowBox would lay them out in equal columns as wide as the widest chip.)
        self.row = Adw.WrapBox(child_spacing=6, line_spacing=6, valign=Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, hexpand=True)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(MAX_ROWS_PX)
        scroller.set_child(self.row)
        self.empty = label("", "secondary")
        self.append(self.title)
        self.append(scroller)
        self.append(self.empty)
        self.chips: dict[str, SourceChip] = {}
        self._key: str | None = None
        self._guard = False
        self._selected: str | None = None
        self.dark = True

    def set_items(self, items: list[sm.Item], selected: str | None, empty_text: str) -> None:
        key = sm.items_key(items)
        if key != self._key:
            self._key = key
            while (c := self.row.get_first_child()) is not None:
                self.row.remove(c)
            self.chips = {}
            first: SourceChip | None = None
            for it in items:
                chip = SourceChip(it, self.audio)
                chip.set_dark(self.dark)
                if first is None:
                    first = chip
                else:
                    chip.set_group(first)
                chip.connect("toggled", self._toggled)
                self.row.append(chip)
                self.chips[it.id] = chip
        else:
            for it in items:
                self.chips[it.id].update(it)
        self.empty.set_text(empty_text)
        self.empty.set_visible(not items)
        self._select(selected)

    def _select(self, sid: str | None) -> None:
        if sid == self._selected and (sid is None or (sid in self.chips and self.chips[sid].get_active())):
            return
        self._selected = sid
        self._guard = True
        try:
            if sid in self.chips:
                self.chips[sid].set_active(True)
            else:  # nothing chosen (or it is not there): no chip is checked
                for c in self.chips.values():
                    c.set_active(False)
        finally:
            self._guard = False

    def _toggled(self, chip: SourceChip) -> None:
        if chip.get_active() and not self._guard:
            self._selected = chip.item_id
            self.on_pick(chip.item_id)

    def tick(self, now: float) -> None:
        for c in self.chips.values():
            c.tick(now)

    def set_dark(self, dark: bool) -> None:
        self.dark = dark
        for c in self.chips.values():
            c.set_dark(dark)


class HoverPreview:
    """A popover that slides in over a widget while the pointer rests on it: the chosen audio source and its beat."""

    def __init__(self, anchor: Gtk.Widget, text_fn: Callable[[], tuple[str, dict[str, Any] | None]]) -> None:
        self.anchor = anchor
        self.text_fn = text_fn
        self.pop = Gtk.Popover(autohide=False, has_arrow=True, position=Gtk.PositionType.BOTTOM)
        self.pop.add_css_class("hover-preview")
        self.pop.set_can_focus(False)
        self.pulse = Pulse()
        self.text = label("")
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=160)
        self.revealer.set_child(hbox(8, self.pulse, self.text, margin_top=2, margin_bottom=2))
        self.pop.set_child(self.revealer)
        self.pop.set_parent(anchor)
        self.tracking: dict[str, Any] | None = None
        self._timer = 0
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._schedule())
        motion.connect("leave", lambda *_a: self.hide())
        anchor.add_controller(motion)
        anchor.connect("destroy", lambda *_a: self.pop.unparent())

    @property
    def visible(self) -> bool:
        return self.pop.get_visible()

    def _schedule(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(HOVER_DELAY_MS, self._show)

    def _show(self) -> bool:
        self._timer = 0
        self.refresh()
        self.pop.popup()
        self.revealer.set_reveal_child(True)
        return False

    def show_now(self) -> None:
        self._show()

    def hide(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.revealer.set_reveal_child(False)
        self.pop.popdown()

    def refresh(self) -> None:
        text, self.tracking = self.text_fn()
        if self.text.get_text() != text:
            self.text.set_text(text)

    def tick(self, now: float) -> None:
        if self.visible:
            self.pulse.set_phase(sm.tracking_phase(self.tracking, now))

    def set_dark(self, dark: bool) -> None:
        self.pulse.dark = dark
        self.pulse.queue_draw()
