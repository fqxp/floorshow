"""Preferences: the settings that stay put between shows. Scenes have their own dialog.

Every change is saved to config.yaml (debounced) and the renderer is told to reload, so the
show reacts immediately. The dialog holds no state the files do not have.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import config_model as cm  # noqa: E402
from . import sources_model as sm, viewmodel as vm  # noqa: E402
from .midi_ports import list_input_ports  # noqa: E402
from .widgets import (  # noqa: E402
    Segmented, SettingsGroup, Stepper, dropdown, hbox, icon, label, push_button, set_classes, setting_row, status_chip,
    vbox,
)

TABS = ["Clock", "MIDI"]
SAVE_DELAY_MS = 250


class PreferencesDialog(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Preferences", modal=True, transient_for=main, default_width=720, default_height=600)
        self.add_css_class("mb-dialog")
        self.main = main
        self.store, self.library = main.store, main.library
        self._pending: dict[str, Any] = {}
        self._save_timer = 0
        self._guard = False
        self.learn_row: int | None = None
        self.detecting = False  # waiting for a beat note to be played (Clock tab)
        self.midi_map: list[dict[str, Any]] = []
        self._ports: list[str] = []
        self.cfg = self.store.load()

        view = Adw.ToolbarView()
        self.set_content(view)
        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        header.set_title_widget(label("Preferences", "title-label"))
        view.add_top_bar(header)

        self.stack = Gtk.Stack(vexpand=True)
        tabs = hbox(0)
        tabs.add_css_class("tabs")
        first: Gtk.ToggleButton | None = None
        self.tab_buttons: dict[str, Gtk.ToggleButton] = {}
        builders = [self._page_clock, self._page_midi]
        for name, build in zip(TABS, builders):
            b = Gtk.ToggleButton(label=name, hexpand=True)
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.connect("toggled", lambda btn, n=name: btn.get_active() and self.stack.set_visible_child_name(n))
            tabs.append(b)
            self.tab_buttons[name] = b
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            page = vbox(18, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
            scroller.set_child(page)
            build(page)
            self.stack.add_named(scroller, name)
        view.add_top_bar(tabs)
        view.set_content(self.stack)
        self.tab_buttons["Clock"].set_active(True)

        self.connect("close-request", self._on_close)
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", lambda _c, k, *_a: (self.close() or True) if k == Gdk.KEY_Escape else False)
        self.add_controller(ctrl)
        if main.state:
            self.on_state(main.state)

    def show_tab(self, name: str) -> None:
        self.tab_buttons[name].set_active(True)

    # --- saving --------------------------------------------------------------------------

    def save(self, section: str, data: dict[str, Any]) -> None:
        """Queue ``data`` for config.yaml section ``section`` (merged), written after a short pause."""
        if self._guard:
            return
        cur = self._pending.setdefault(section, {})
        cur.update(data)
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(SAVE_DELAY_MS, self._flush)

    def _flush(self) -> bool:
        self._save_timer = 0
        if not self._pending:
            return False
        pending, self._pending = self._pending, {}
        try:
            self.store.update(pending)
            self.main.cfg = self.store.load()
            self.cfg = self.main.cfg
            self.main.send("reload")
        except Exception as e:  # noqa: BLE001
            self.main.toast(f"Could not save the settings: {e}")
        return False

    def _on_close(self, *_a: Any) -> bool:
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._flush()
        if self.learn_row is not None or self.detecting:
            self.main.send("midi.learn", on=False)
        self.main.preferences_dialog = None
        self.main.refresh()
        return False

    # --- Clock ---------------------------------------------------------------------------

    def _page_clock(self, page: Gtk.Box) -> None:
        c = self.cfg.clock
        self.clock_seg = Segmented([("midi", "MIDI clock"), ("internal", "Internal BPM"), ("audio", "Audio")], c.source,
                                   self._on_clock_source, fill=True)
        page.append(self.clock_seg)
        self.clock_stack = Gtk.Stack()
        # MIDI
        self._ports = list_input_ports()
        self.port_dd = dropdown([], 0, 210, self._on_port)
        self._fill_ports()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("mb-btn")
        refresh.set_tooltip_text("Refresh ports")
        refresh.connect("clicked", lambda *_: (self._reload_ports(), None))
        self.midi_status = vbox(0, halign=Gtk.Align.END)
        g = SettingsGroup("MIDI clock input")
        g.add(setting_row("Input port", "Ports that are present right now are listed.", self.port_dd, refresh))
        g.add(setting_row("Status", None, self.midi_status))
        self.midi_mode_seg = Segmented([("clock", "MIDI clock"), ("beats", "Beat notes")], c.midi_mode, self._on_midi_mode)
        g.add(setting_row("Beat signal", "MIDI clock is 24 pulses per beat. Some programs, such as Mixxx with the “MIDI for light” "
                          "script, send a note on every beat instead.", self.midi_mode_seg))
        self.beat_group = self._beat_notes_group()
        self.clock_stack.add_named(vbox(16, g, self.beat_group), "midi")
        # internal
        entry = Gtk.Entry(text=f"{c.bpm:g}", width_chars=6, xalign=1.0, input_purpose=Gtk.InputPurpose.NUMBER)
        entry.add_css_class("mb-entry")
        entry.add_css_class("bpm-entry")
        entry.connect("changed", lambda e: self._on_bpm(e))
        self.bpm_entry = entry
        gi_ = SettingsGroup("Internal tempo")
        gi_.add(setting_row("Tempo", "Default 120 BPM. Tap tempo on the main window sets this value.", entry, label("BPM", "secondary")))
        self.clock_stack.add_named(gi_, "internal")
        self.clock_stack.add_named(self._audio_group(), "audio")
        page.append(self.clock_stack)
        self.clock_stack.set_visible_child_name(c.source)
        # timing
        t = SettingsGroup("Timing")
        self.bpb = Stepper(c.beats_per_bar, 1, 16, 1, lambda v: f"{v:.0f}", lambda v: self.save("clock", {"beats_per_bar": int(v)}))
        # positive offset = the beat is shown later (design copy)
        self.offset = Stepper(c.offset_ms, -500, 500, 5, lambda v: f"<b>{v:.0f}</b> ms", lambda v: self.save("clock", {"offset_ms": v}))
        t.add(setting_row("Beats per bar", "Sets the downbeat and the “every N bars” counter.", self.bpb))
        t.add(setting_row("Beat offset", "Shift the beat to cancel projector latency. Positive means later.", self.offset))
        page.append(t)

    def _audio_group(self) -> Gtk.Widget:
        c = self.cfg.clock
        self._audio_sources: list[tuple[str, str]] = []  # (id, name) as the dropdown lists them
        self._audio_key: Any = None
        self.audio_dd = dropdown([], 0, 260, self._on_audio_source)
        self._fill_audio_sources([])
        self.audio_status = label("", "secondary")
        g = SettingsGroup("Audio input")
        g.add(setting_row("Source", "An output (what this computer plays, e.g. a DJ program) or an input. Line level "
                          "works best; a microphone is best effort. The main window lists them with level meters.",
                          self.audio_dd))
        g.add(setting_row("Status", None, self.audio_status))
        bpm = lambda v: f"<b>{v:.0f}</b> BPM"  # noqa: E731
        self.audio_min = Stepper(c.audio_min_bpm, 20, 300, 5, bpm, lambda v: self._save_tempo_range(v, None))
        self.audio_max = Stepper(c.audio_max_bpm, 20, 300, 5, bpm, lambda v: self._save_tempo_range(None, v))
        g.add(setting_row("Slowest tempo", "The beat detection looks between these two. Drum and bass (174) reads as 87 "
                          "unless the slowest is above 87.", self.audio_min))
        g.add(setting_row("Fastest tempo", None, self.audio_max))
        self.audio_offset = Stepper(c.audio_offset_ms, -500, 500, 5, lambda v: f"<b>{v:.0f}</b> ms",
                                    lambda v: self.save("clock", {"audio_offset_ms": v}))
        g.add(setting_row("Audio offset", "Added to the beat offset while the audio clock drives the show: the delay "
                          "of the sound path. Positive means later.", self.audio_offset))
        note_box = hbox(12, icon("dialog-information-symbolic", 18), label(
            "The music does not say which beat starts the bar. Press the downbeat button (D) on the first beat of a bar.",
            "secondary", wrap=True, hexpand=True))
        note_box.add_css_class("note-box")
        return vbox(16, g, note_box)

    def _fill_audio_sources(self, sources: list[dict[str, Any]]) -> None:
        saved = self.cfg.clock.audio_source
        items = [(s["id"], s["name"]) for s in sources]
        if saved and not any(saved == i or saved.lower() in (i + " " + n).lower() for i, n in items):
            items.insert(0, (saved, f"{saved} (not there)"))
        elif not saved:
            items.insert(0, ("", "Select a source…" if sources else "No audio source found"))
        self._audio_sources = items
        self._guard = True
        try:
            self.audio_dd.set_model(Gtk.StringList.new([n for _, n in items]))
            want = (saved or "").lower()
            idx = next((k for k, (i, n) in enumerate(items) if want and (i == saved or want in (i + " " + n).lower())), 0)
            self.audio_dd.set_selected(idx)
        finally:
            self._guard = False

    def _on_audio_source(self, idx: int) -> None:
        if 0 <= idx < len(self._audio_sources) and self._audio_sources[idx][0]:
            sid = self._audio_sources[idx][0]
            if sid != self.cfg.clock.audio_source:
                self.save("clock", {"audio_source": sid})

    def _save_tempo_range(self, lo: float | None, hi: float | None) -> None:
        lo = self.audio_min.value if lo is None else lo
        hi = self.audio_max.value if hi is None else hi
        if hi < 1.5 * lo:  # the config insists on a range that holds a tempo and its half or double
            self.audio_status.set_text("The fastest tempo must be at least 1.5 times the slowest.")
            return
        self.save("clock", {"audio_min_bpm": lo, "audio_max_bpm": hi})

    def _beat_notes_group(self) -> Gtk.Widget:
        c = self.cfg.clock
        names = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
        note = lambda v: f"<b>{v:.0f}</b> ({names[int(v) % 12]})"
        save = lambda key: (lambda v: self.save("clock", {key: int(v)}))
        g = SettingsGroup("Beat notes")
        self.beat_note = Stepper(c.beat_note, 0, 127, 1, note, save("beat_note"))
        self.beat_velocity = Stepper(c.beat_velocity, 0, 127, 1, lambda v: "<b>any</b>" if v == 0 else f"<b>{v:.0f}</b>",
                                     save("beat_velocity"))
        self.midi_channel = Stepper(c.midi_channel, 0, 16, 1, lambda v: "<b>any</b>" if v == 0 else f"<b>{v:.0f}</b>",
                                    save("midi_channel"))
        self.tempo_note = Stepper(c.tempo_note, -1, 127, 1, lambda v: "<b>off</b>" if v < 0 else note(v), save("tempo_note"))
        self.tempo_offset = Stepper(c.tempo_offset, -127, 300, 1, lambda v: f"<b>{v:+.0f}</b>", save("tempo_offset"))
        self.detect_btn = push_button("Detect")
        self.detect_btn.set_tooltip_text("Fills in note, velocity and channel from the next beat that arrives")
        self.detect_btn.connect("clicked", lambda *_: self._toggle_detect())
        g.add(setting_row("Detect beat", "Play something in the other program, then click Detect: the first note that "
                          "arrives on the port above sets the three values below.", self.detect_btn))
        g.add(setting_row("Beat note", "Every note-on with this number is one beat. Mixxx “MIDI for light”: 50 (D).", self.beat_note))
        g.add(setting_row("Beat velocity", "Only notes with this velocity count. Mixxx: 100.", self.beat_velocity))
        g.add(setting_row("MIDI channel", "Mixxx: the channel set in Midi_for_light.js.", self.midi_channel))
        g.add(setting_row("Tempo note", "The value of this note announces the tempo. It is only displayed; the beats "
                          "themselves set the speed. Mixxx: 52 (E).", self.tempo_note))
        g.add(setting_row("Tempo offset", "Tempo = note value + offset. Mixxx: +50.", self.tempo_offset))
        note_box = hbox(12, icon("dialog-information-symbolic", 18), label(
            "A beat note does not say which beat starts the bar. Press the downbeat button (D) on the first beat of a bar to set it.",
            "secondary", wrap=True, hexpand=True))
        note_box.add_css_class("note-box")
        box = vbox(16, g, note_box)
        box.set_visible(c.midi_mode == "beats")
        return box

    def _toggle_detect(self) -> None:
        if not self.main.connected:
            self.main.toast("The renderer is not running.")
            return
        self.detecting = not self.detecting
        self.detect_btn.get_child().get_last_child().set_text("Listening… (cancel)" if self.detecting else "Detect")
        self.main.send("midi.learn", on=self.detecting, role="clock")

    def _beat_detected(self, on: dict[str, int]) -> None:
        self.detecting = False
        self.detect_btn.get_child().get_last_child().set_text("Detect")
        self.beat_note.set_value(on["note"])
        self.beat_velocity.set_value(on.get("velocity", 0))
        self.midi_channel.set_value(on["ch"])
        self.save("clock", {"beat_note": on["note"], "beat_velocity": on.get("velocity", 0), "midi_channel": on["ch"]})
        self.main.toast(f"Beat note {on['note']}, velocity {on.get('velocity', 0)}, channel {on['ch']}")

    def _on_midi_mode(self, key: str) -> None:
        self.beat_group.set_visible(key == "beats")
        self.save("clock", {"midi_mode": key})

    def _ports_for_dropdown(self) -> list[str]:
        saved = self.cfg.clock.midi_port
        ports = list(self._ports)
        if saved and not any(saved.lower() in p.lower() for p in ports):
            ports.insert(0, f"{saved} (not connected)")
        elif not saved and ports:
            ports.insert(0, "Select a port…")  # otherwise the first port looks chosen while nothing is saved
        return ports or ["No MIDI input found"]

    def _fill_ports(self) -> None:
        self._guard = True
        try:
            names = self._ports_for_dropdown()
            self.port_dd.set_model(Gtk.StringList.new(names))
            saved = (self.cfg.clock.midi_port or "").lower()
            self.port_dd.set_selected(next((i for i, n in enumerate(names) if saved and saved in n.lower()), 0))
        finally:
            self._guard = False

    def _reload_ports(self) -> None:
        self._ports = list_input_ports()
        self._fill_ports()
        self._fill_control_ports()

    def _on_clock_source(self, key: str) -> None:
        self.clock_stack.set_visible_child_name(key)
        self.save("clock", {"source": key})

    def sync_clock_source(self) -> None:
        """The same setting can be changed from the main window's clock strip while this dialog is open."""
        self.cfg = self.store.load()
        self._guard = True
        try:
            self.clock_seg.select(self.cfg.clock.source)
            self.clock_stack.set_visible_child_name(self.cfg.clock.source)
        finally:
            self._guard = False

    def _on_port(self, idx: int) -> None:
        names = self._ports_for_dropdown()
        if 0 <= idx < len(names) and names[idx] in self._ports:
            self.save("clock", {"midi_port": names[idx]})

    def _on_bpm(self, entry: Gtk.Entry) -> None:
        try:
            bpm = float(entry.get_text().replace(",", "."))
        except ValueError:
            entry.add_css_class("error")
            return
        if 20 <= bpm <= 300:
            entry.remove_css_class("error")
            self.save("clock", {"bpm": bpm})
        else:
            entry.add_css_class("error")

    # --- MIDI ----------------------------------------------------------------------------

    def _page_midi(self, page: Gtk.Box) -> None:
        self.midi_map = [dict(m) for m in (m.model_dump(exclude_none=True) for m in self.cfg.midi.map)]
        for m in self.midi_map:
            m["do"] = cm.normalise_action(m["do"], self.library.ids())
            m["on"] = {k: v for k, v in m["on"].items() if v is not None}
        self.control_dd = dropdown([], 0, 210, self._on_control_port)
        self._fill_control_ports()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("mb-btn")
        refresh.connect("clicked", lambda *_: self._reload_ports())
        g = SettingsGroup("MIDI control input", "Notes and knobs that control the show.")
        g.add(setting_row("Control port", None, self.control_dd, refresh))
        page.append(g)
        self.learn_bar = hbox(12, label("Listening… press a key or turn a knob", hexpand=True))
        cancel = push_button("Cancel")
        cancel.connect("clicked", lambda *_: self._stop_learning())
        self.learn_bar.append(cancel)
        self.learn_bar.add_css_class("learn-bar")
        self.learn_bar.set_visible(False)
        page.append(self.learn_bar)
        self.map_group = SettingsGroup("Mappings", "Each trigger runs one action.")
        page.append(self.map_group)
        add = push_button("Add mapping", "tall", icon_name="list-add-symbolic")
        add.connect("clicked", lambda *_: self._add_mapping())
        page.append(hbox(0, add))
        self._rebuild_mappings()

    def _control_ports(self) -> list[str]:
        saved = self.cfg.midi.control_port
        ports = list(self._ports or list_input_ports())
        if saved and not any(saved.lower() in p.lower() for p in ports):
            ports.insert(0, f"{saved} (not connected)")
        return ports or ["No MIDI input found"]

    def _fill_control_ports(self) -> None:
        if not hasattr(self, "control_dd"):
            return
        self._guard = True
        try:
            names = self._control_ports()
            self.control_dd.set_model(Gtk.StringList.new(names))
            saved = (self.cfg.midi.control_port or "").lower()
            self.control_dd.set_selected(next((i for i, n in enumerate(names) if saved and saved in n.lower()), 0))
        finally:
            self._guard = False

    def _on_control_port(self, idx: int) -> None:
        names = self._control_ports()
        if 0 <= idx < len(names) and names[idx] in (self._ports or list_input_ports()):
            self.save("midi", {"control_port": names[idx]})

    def _rebuild_mappings(self) -> None:
        g = self.map_group
        g.clear()
        ids = self.library.ids()
        names = {sid: self.library.scenes[sid].name for sid in ids}
        opts = cm.action_options(ids, names)
        can_learn = bool(self.cfg.midi.control_port) and self.main.connected
        if not self.midi_map:
            g.add(setting_row("No mappings yet", "Add one, then press a key or turn a knob on your controller."))
        for i, m in enumerate(self.midi_map):
            trig = label(cm.trigger_label(m["on"]), "mono", hexpand=True)
            dd = dropdown([lb for _, lb in opts], max(0, cm.action_index(m["do"], opts)), 230,
                          lambda idx, i=i, opts=opts: self._set_action(i, opts[idx][0]))
            learn = push_button("Learn")
            learn.set_sensitive(can_learn)
            learn.set_tooltip_text(None if can_learn else "Needs a control port and a running renderer")
            learn.connect("clicked", lambda *_a, i=i: self._start_learning(i))
            rm = Gtk.Button(icon_name="edit-delete-symbolic")
            rm.add_css_class("mb-btn")
            rm.set_tooltip_text("Remove mapping")
            rm.connect("clicked", lambda *_a, i=i: self._remove_mapping(i))
            row = hbox(12, trig, dd, learn, rm)
            row.add_css_class("setting-row")
            set_classes(row, {"selected": i == self.learn_row})
            g.add(row)

    def _save_map(self) -> None:
        self.save("midi", {"map": [{"on": m["on"], "do": m["do"]} for m in self.midi_map if m["on"]]})

    def _set_action(self, i: int, value: Any) -> None:
        if not self._guard and 0 <= i < len(self.midi_map) and self.midi_map[i]["do"] != value:
            self.midi_map[i]["do"] = value
            self._save_map()

    def _add_mapping(self) -> None:
        ids = self.library.ids()
        self.midi_map.append({"on": {}, "do": {"scene": ids[0]} if ids else "next"})
        self._rebuild_mappings()
        if self.cfg.midi.control_port and self.main.connected:
            self._start_learning(len(self.midi_map) - 1)

    def _remove_mapping(self, i: int) -> None:
        if 0 <= i < len(self.midi_map):
            del self.midi_map[i]
            self._stop_learning()
            self._save_map()
            self._rebuild_mappings()

    def _start_learning(self, i: int) -> None:
        self.learn_row = i
        self.learn_bar.set_visible(True)
        self.main.send("midi.learn", on=True)
        self._rebuild_mappings()

    def _stop_learning(self) -> None:
        if self.learn_row is not None:
            self.main.send("midi.learn", on=False)
        self.learn_row = None
        self.learn_bar.set_visible(False)
        self._rebuild_mappings()

    def on_event(self, ev: dict[str, Any]) -> None:
        """Renderer events: a learned MIDI trigger fills the mapping that is listening."""
        if ev.get("event") == "midi_learned" and ev.get("role") == "clock" and self.detecting:
            self._beat_detected(ev["on"])
        elif ev.get("event") == "midi_learned" and self.learn_row is not None and self.learn_row < len(self.midi_map):
            self.midi_map[self.learn_row]["on"] = cm.trigger_from_learned(ev["on"])
            self.learn_row = None
            self.learn_bar.set_visible(False)
            self._save_map()
            self._rebuild_mappings()

    # --- live updates from the renderer --------------------------------------------------

    def on_state(self, state: dict[str, Any]) -> None:
        while (c := self.midi_status.get_first_child()) is not None:
            self.midi_status.remove(c)
        text, kind = vm.midi_input_status(state)
        self.midi_status.append(status_chip(text, kind))
        audio = state.get("audio") or {}
        key = [(s["id"], s["name"]) for s in audio.get("sources") or []]
        if key != self._audio_key:
            self._audio_key = key
            self._fill_audio_sources(audio.get("sources") or [])
        hover, _ = sm.hover_text(audio, self.cfg.clock.audio_source, state["clock"]["source"] == "audio",
                                 self.main._audio_name())
        if self.audio_status.get_text() != hover:
            self.audio_status.set_text(hover)
        self._rebuild_mappings_if_connection_changed()

    def on_connection(self, _up: bool) -> None:
        self._rebuild_mappings_if_connection_changed()

    def _rebuild_mappings_if_connection_changed(self) -> None:
        key = self.main.connected
        if key != getattr(self, "_last_conn", None):
            self._last_conn = key
            self._rebuild_mappings()
