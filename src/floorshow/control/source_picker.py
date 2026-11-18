"""The source dropdowns beside Downbeat (audio sources with level meters, MIDI ports with beat previews) and the
popover over the Audio option. Only drawing and widgets; what they show comes from sources_model.

Meters and beat tiles move every frame, the lists themselves are rebuilt only when the sources change, so a level
change never relayouts the window. Colours keep to the design's rules: the accent for levels, amber near
clipping, red only for a beat.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import sources_model as sm, tokens  # noqa: E402
from .widgets import hbox, label  # noqa: E402

METER_FLOOR_DB = -60.0
CLIP_DB = -1.0
REPAINT_S = 1 / 30
HOVER_DELAY_MS = 180


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

    def __init__(self, channels: int = 2, width: int | None = None) -> None:
        """``width``: fixed, the columns share it (so meters of different sources line up); None: 4 px a channel."""
        super().__init__()
        self.add_css_class("level-meter")
        self.set_valign(Gtk.Align.CENTER)
        self.dark = True
        self.fixed_width = width
        self.levels: tuple[float, ...] = (METER_FLOOR_DB,) * channels
        self.peaks: tuple[float, ...] = self.levels
        self._painted = 0.0
        self.set_content_width(width or max(1, channels) * 4)
        self.set_draw_func(self._draw)

    def set_levels(self, levels: tuple[float, ...], peaks: tuple[float, ...]) -> None:
        if levels == self.levels and peaks == self.peaks:
            return
        now = time.monotonic()
        if now - self._painted < REPAINT_S:
            return
        self._painted = now
        if len(levels) != len(self.levels) and self.fixed_width is None:
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


class SourceDropdown(Gtk.DropDown):
    """The sources of one clock as a dropdown beside Downbeat: an indicator and the name in every entry and on the
    button, nothing else. Its width never changes, so neither does the strip's layout. Subclasses say what the
    indicator is (a level meter, a beat tile).

    ``on_pick(id)`` when a source is chosen from the list. An entry with an empty id stands for "nothing chosen" or a
    chosen source that is not there; it is shown but cannot be picked."""

    NAME_CHARS = 18
    CSS_CLASS = "source-dropdown"

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__()
        self.add_css_class("source-dropdown")
        self.add_css_class(self.CSS_CLASS)
        self.set_valign(Gtk.Align.CENTER)
        self.on_pick = on_pick
        self.ids: list[str] = []
        self._items: dict[str, sm.Item] = {}
        self._labels: list[str] = []
        self._indicators: dict[str, list[Any]] = {}  # id -> indicators on screen for it (list rows and the button)
        self._key: Any = None
        self._guard = False
        self.dark = True
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        factory.connect("unbind", self._unbind)
        self.set_factory(factory)
        self.connect("notify::selected", self._on_selected)

    # --- the indicator (subclasses) ----------------------------------------------------------

    def _new_indicator(self) -> Gtk.Widget:
        raise NotImplementedError

    def _show(self, indicator: Any, item: sm.Item, now: float) -> None:
        """Bring an indicator up to date with its item; ``now`` for anything that moves with time."""

    # --- rows ------------------------------------------------------------------------------

    def _setup(self, _f: Any, li: Any) -> None:
        name = label("")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_width_chars(self.NAME_CHARS)  # fixed: a longer or shorter name never resizes the button
        name.set_max_width_chars(self.NAME_CHARS)
        li.set_child(hbox(8, self._new_indicator(), name))

    @staticmethod
    def _parts(li: Any) -> tuple[Gtk.Box, Any, Gtk.Label]:
        # (found through the item's child: Python attributes on a ListItem do not reliably survive between signals)
        box = li.get_child()
        return box, box.get_first_child(), box.get_last_child()

    def _bind(self, _f: Any, li: Any) -> None:
        box, ind, name = self._parts(li)
        pos = li.get_position()
        sid = self.ids[pos] if 0 <= pos < len(self.ids) else ""
        name.set_text(self._labels[pos] if 0 <= pos < len(self._labels) else "")
        item = self._items.get(sid)
        ind.set_visible(item is not None)
        ind.dark = self.dark
        if item is not None:
            ind._painted = 0.0
            self._show(ind, item, time.monotonic())
            self._indicators.setdefault(sid, []).append(ind)
        (box.add_css_class if item is not None and item.dim else box.remove_css_class)("dim")

    def _unbind(self, _f: Any, li: Any) -> None:
        _box, ind, _name = self._parts(li)
        for inds in self._indicators.values():
            if ind in inds:
                inds.remove(ind)

    # --- content ---------------------------------------------------------------------------

    def set_items(self, items: list[sm.Item], selected: str | None, placeholder: str) -> None:
        """``placeholder``: the text of the extra entry shown while ``selected`` is not among ``items`` (nothing
        chosen yet, or the chosen source is unplugged)."""
        self._items = {i.id: i for i in items}
        ids, labels = [i.id for i in items], [i.name for i in items]
        if selected not in self._items:
            ids.insert(0, "")
            labels.insert(0, placeholder)
        key = (tuple(ids), tuple(labels))
        if key != self._key:
            self._key = key
            self.ids, self._labels = ids, labels
            self._indicators = {}
            self._guard = True
            try:
                self.set_model(Gtk.StringList.new(labels))
            finally:
                self._guard = False
        else:
            now = time.monotonic()
            for it in items:
                for ind in self._indicators.get(it.id, []):
                    self._show(ind, it, now)
        index = ids.index(selected) if selected in ids else 0
        if self.get_selected() != index:
            self._guard = True
            try:
                self.set_selected(index)
            finally:
                self._guard = False

    def selected_id(self) -> str:
        pos = self.get_selected()
        return self.ids[pos] if 0 <= pos < len(self.ids) else ""

    def _on_selected(self, *_a: Any) -> None:
        if not self._guard and self.selected_id():
            self.on_pick(self.selected_id())

    def tick(self, now: float) -> None:
        """Every frame while shown: indicators that move with time follow it."""

    def set_dark(self, dark: bool) -> None:
        self.dark = dark
        for inds in self._indicators.values():
            for ind in inds:
                ind.dark = dark
                ind.queue_draw()


class AudioSourceDropdown(SourceDropdown):
    """The audio sources: full names (an output's monitor says so), a level meter per channel."""

    CSS_CLASS = "audio-source"
    METER_PX = 16  # every entry's meter this wide, whatever its channel count: the names line up

    def _new_indicator(self) -> Gtk.Widget:
        return LevelMeter(2, width=self.METER_PX)

    def _show(self, indicator: LevelMeter, item: sm.Item, now: float) -> None:
        indicator.set_levels(item.levels, item.peaks)


class MidiPortDropdown(SourceDropdown):
    """The MIDI input ports: short names (the full one is the tooltip), a beat tile at each port's tempo."""

    CSS_CLASS = "midi-port"

    def _new_indicator(self) -> Gtk.Widget:
        return Pulse()

    def _show(self, indicator: Pulse, item: sm.Item, now: float) -> None:
        indicator.set_phase(sm.beat_phase(item.bpm, item.beat_time, now))

    def _bind(self, f: Any, li: Any) -> None:
        super()._bind(f, li)
        box = li.get_child()
        pos = li.get_position()
        item = self._items.get(self.ids[pos] if 0 <= pos < len(self.ids) else "")
        box.set_tooltip_text(item.id if item is not None and item.id != item.name else None)  # the full port name

    def tick(self, now: float) -> None:
        for sid, pulses in self._indicators.items():
            item = self._items.get(sid)
            if item is not None:
                for p in pulses:
                    self._show(p, item, now)


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
