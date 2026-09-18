"""Configuration dialog, built from the design's Config boards: seven tabs of setting rows.

Every change is saved to config.yaml (debounced) and the renderer is told to reload, so the
show reacts immediately. The dialog holds no state the files do not have.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..common import paths  # noqa: E402
from . import config_model as cm  # noqa: E402
from . import scene_files as sf  # noqa: E402
from . import viewmodel as vm  # noqa: E402
from .midi_ports import list_input_ports  # noqa: E402
from .widgets import (  # noqa: E402
    Segmented, SettingsGroup, Stepper, hbox, icon, label, push_button, set_classes, setting_row, status_chip, vbox,
)

TABS = ["Scenes", "Clock", "Output", "MIDI", "Director", "Calibration", "Ball"]
SAVE_DELAY_MS = 250


def dropdown(options: list[str], selected: int = 0, width: int = 210, on_change: Any = None) -> Gtk.DropDown:
    dd = Gtk.DropDown.new_from_strings(options or [""])
    dd.add_css_class("mb-dd")
    dd.set_size_request(width, 36)
    dd.set_selected(max(0, selected))
    if on_change:
        dd.connect("notify::selected", lambda d, _p: on_change(d.get_selected()))
    return dd


class ConfigDialog(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Configuration", modal=True, transient_for=main, default_width=720, default_height=600)
        self.add_css_class("mb-dialog")
        self.main = main
        self.store, self.library = main.store, main.library
        self._pending: dict[str, Any] = {}
        self._save_timer = 0
        self._guard = False
        self.selected_scene: str | None = None
        self.learn_row: int | None = None
        self.midi_map: list[dict[str, Any]] = []
        self._ports: list[str] = []
        self.cfg = self.store.load()

        view = Adw.ToolbarView()
        self.set_content(view)
        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        header.set_title_widget(label("Configuration", "title-label"))
        view.add_top_bar(header)

        self.stack = Gtk.Stack(vexpand=True)
        tabs = hbox(0)
        tabs.add_css_class("tabs")
        first: Gtk.ToggleButton | None = None
        self.tab_buttons: dict[str, Gtk.ToggleButton] = {}
        builders = [self._page_scenes, self._page_clock, self._page_output, self._page_midi, self._page_director,
                    self._page_calibration, self._page_ball]
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
        self.tab_buttons["Scenes"].set_active(True)

        self.connect("close-request", self._on_close)
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", lambda _c, k, *_a: (self.close() or True) if k == Gdk.KEY_Escape else False)
        self.add_controller(ctrl)
        GLib.timeout_add(1000, self._tick)
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
        if self.learn_row is not None:
            self.main.send("midi.learn", on=False)
        self.main.config_dialog = None
        self.main.refresh()
        return False

    def _tick(self) -> bool:
        if not self.get_visible():
            return False
        if self.library.refresh():
            self._rebuild_scenes()
            self._rebuild_director_choices()
        return True

    # --- Scenes --------------------------------------------------------------------------

    def _page_scenes(self, page: Gtk.Box) -> None:
        add = push_button("Add", "tall", "primary", icon_name="list-add-symbolic")
        add.add_css_class("on-accent")
        self.dup_btn = push_button("Duplicate", "tall", icon_name="edit-copy-symbolic")
        self.rename_btn = push_button("Rename", "tall", icon_name="document-edit-symbolic")
        self.delete_btn = push_button("Delete", "tall", icon_name="user-trash-symbolic")
        edit = push_button("Open in editor", "tall", icon_name="document-open-symbolic")
        reveal = push_button("Reveal folder", "tall", icon_name="folder-symbolic")
        add.connect("clicked", lambda *_: self._ask_name("Add scene", "New scene", "Add", self._do_add))
        self.dup_btn.connect("clicked", lambda *_: self._dup())
        self.rename_btn.connect("clicked", lambda *_: self._rename())
        self.delete_btn.connect("clicked", lambda *_: self._delete())
        edit.connect("clicked", lambda *_: self._open_selected())
        reveal.connect("clicked", lambda *_: self.main.act_reveal_folder())
        bar = Adw.WrapBox(child_spacing=8, line_spacing=8)
        for b in (add, self.dup_btn, self.rename_btn, self.delete_btn, edit, reveal):
            bar.append(b)
        page.append(bar)
        self.scene_group = SettingsGroup("Scene files", "Saving a file reloads its scene while the show runs.")
        page.append(self.scene_group)
        self._rebuild_scenes()

    def _scene_path(self, sid: str | None):
        return self.library.path_of(sid) if sid else None

    def _is_user(self, sid: str | None) -> bool:
        p = self._scene_path(sid)
        return bool(p and p.parent.resolve() == paths.user_scenes_dir().resolve())

    def _rebuild_scenes(self) -> None:
        g = self.scene_group
        g.clear()
        ids = self.library.ids()
        keys = vm.hotkeys(ids, self.cfg.bindings.keys)
        rows: list[tuple[str, bool]] = [(sid, True) for sid in ids] + [(sid, False) for sid in sorted(self.library.errors) if sid not in ids]
        rows.sort(key=lambda r: r[0])
        if self.selected_scene not in {r[0] for r in rows}:
            self.selected_scene = rows[0][0] if rows else None
        for sid, ok in rows:
            sc = self.library.scenes.get(sid)
            status = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            status.add_css_class("row-status")
            status.set_size_request(24, 24)
            status.append(icon("object-select-symbolic" if ok else "dialog-warning-symbolic", 14))
            set_classes(status, {"bad": not ok})
            name = sc.name if sc else vm.display_name(sid)
            text = vbox(1, label(name), label(f"{sid}.yaml", "row-hint", "mono"), hexpand=True)
            controls = [status_chip("Valid" if ok else "Error", "ok" if ok else "warn")]
            if sid in keys:
                from .widgets import kbd

                controls.append(kbd(keys[sid]))
            row = hbox(12, status, text, hbox(8, *controls, valign=Gtk.Align.CENTER))
            row.add_css_class("setting-row")
            set_classes(row, {"selected": sid == self.selected_scene})
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, sid=sid: self._select(sid))
            row.add_controller(click)
            g.add(row)
            if not ok:
                g.add(self._error_detail(sid))
        self._update_scene_buttons()

    def _error_detail(self, sid: str) -> Gtk.Widget:
        err = self.library.errors[sid]
        msg = f"{sid}.yaml\n" + "\n".join(err.problems[:4])
        text = label(msg, "mono", wrap=True)
        copy = push_button("Copy message", icon_name="edit-copy-symbolic")
        copy.connect("clicked", lambda *_: (Gdk.Display.get_default().get_clipboard().set(msg), self.main.toast("Copied.")))
        edit = push_button("Open in editor", icon_name="document-edit-symbolic")
        edit.connect("clicked", lambda *_: self._open(sid))
        box = vbox(8, text, hbox(8, edit, copy))
        box.add_css_class("error-detail")
        return box

    def _select(self, sid: str) -> None:
        self.selected_scene = sid
        self._rebuild_scenes()

    def _update_scene_buttons(self) -> None:
        sid = self.selected_scene
        self.dup_btn.set_sensitive(sid is not None and sid in self.library.scenes)
        self.rename_btn.set_sensitive(self._is_user(sid) and sid in self.library.scenes)
        self.delete_btn.set_sensitive(self._is_user(sid))
        note = None if self._is_user(sid) or sid is None else "Bundled example: duplicate it to edit or rename a copy."
        self.rename_btn.set_tooltip_text(note)
        self.delete_btn.set_tooltip_text(note)

    def _open(self, sid: str | None) -> None:
        p = self._scene_path(sid)
        if p:
            Gtk.FileLauncher.new(Gio.File.new_for_path(str(p))).launch(self, None, None)

    def _open_selected(self) -> None:
        self._open(self.selected_scene)

    def _ask_name(self, heading: str, initial: str, verb: str, then: Any) -> None:
        entry = Gtk.Entry(text=initial, activates_default=True)
        entry.add_css_class("mb-entry")
        dlg = Adw.AlertDialog(heading=heading)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.connect("response", lambda _d, r: r == "ok" and entry.get_text().strip() and then(entry.get_text().strip()))
        dlg.present(self)
        entry.grab_focus()

    def _after_file_change(self, path: Any) -> None:
        self.library.refresh()
        self.selected_scene = path.stem if path else self.selected_scene
        self._rebuild_scenes()
        self.main.send("reload")

    def _do_add(self, name: str) -> None:
        self._after_file_change(sf.create_scene(paths.user_scenes_dir(), name, set(self.library.scenes) | set(self.library.errors)))

    def _dup(self) -> None:
        sid = self.selected_scene
        src = self._scene_path(sid)
        if not src or sid not in self.library.scenes:
            return
        base = self.library.scenes[sid].name

        def go(name: str) -> None:
            self._after_file_change(sf.duplicate_scene(src, paths.user_scenes_dir(), name, set(self.library.scenes) | set(self.library.errors)))

        self._ask_name("Duplicate scene", f"{base} copy", "Duplicate", go)

    def _rename(self) -> None:
        sid = self.selected_scene
        p = self._scene_path(sid)
        if not p or not self._is_user(sid) or sid not in self.library.scenes:
            return

        def go(name: str) -> None:
            sf.rename_scene(p, name)
            self._after_file_change(p)

        self._ask_name("Rename scene", self.library.scenes[sid].name, "Rename", go)

    def _delete(self) -> None:
        sid = self.selected_scene
        p = self._scene_path(sid)
        if not p or not self._is_user(sid):
            return
        dlg = Adw.AlertDialog(heading=f"Delete “{sid}”?", body="The file is removed from disk. This cannot be undone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def go(_d: Any, r: str) -> None:
            if r == "delete":
                sf.delete_scene(p)
                self.selected_scene = None
                self._after_file_change(None)

        dlg.connect("response", go)
        dlg.present(self)

    # --- Clock ---------------------------------------------------------------------------

    def _page_clock(self, page: Gtk.Box) -> None:
        c = self.cfg.clock
        self.clock_seg = Segmented([("midi", "MIDI clock"), ("internal", "Internal BPM")], c.source, self._on_clock_source, fill=True)
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
        self.clock_stack.add_named(g, "midi")
        # internal
        entry = Gtk.Entry(text=f"{c.bpm:g}", width_chars=6, xalign=1.0, input_purpose=Gtk.InputPurpose.NUMBER)
        entry.add_css_class("mb-entry")
        entry.add_css_class("bpm-entry")
        entry.connect("changed", lambda e: self._on_bpm(e))
        self.bpm_entry = entry
        gi_ = SettingsGroup("Internal tempo")
        gi_.add(setting_row("Tempo", "Default 120 BPM. Tap tempo on the main window sets this value.", entry, label("BPM", "secondary")))
        self.clock_stack.add_named(gi_, "internal")
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

    def _ports_for_dropdown(self) -> list[str]:
        saved = self.cfg.clock.midi_port
        ports = list(self._ports)
        if saved and not any(saved.lower() in p.lower() for p in ports):
            ports.insert(0, f"{saved} (not connected)")
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

    # --- Output --------------------------------------------------------------------------

    def _page_output(self, page: Gtk.Box) -> None:
        self.test_btn = push_button("Show test pattern", "tall")
        self.test_btn.connect("clicked", lambda *_: self._toggle_test_pattern())
        g = SettingsGroup("Test pattern")
        g.add(setting_row("Test pattern", "Shows a grid and a circle on the projector so you can position the output window.", self.test_btn))
        page.append(g)
        self.window_sub = label("The renderer is not running.", "row-hint")
        self.window_chip_box = hbox(0)
        g2 = SettingsGroup("Output window")
        row = hbox(12, vbox(1, label("Window state"), self.window_sub, hexpand=True), self.window_chip_box)
        row.add_css_class("setting-row")
        g2.add(row)
        page.append(g2)
        note = hbox(12, icon("dialog-information-symbolic", 18), label(
            "The output window is moved to the projector and made fullscreen with GNOME: Super + Shift + ← → changes monitor, F11 toggles fullscreen.",
            "secondary", wrap=True, hexpand=True))
        note.add_css_class("note-box")
        page.append(note)

    def _toggle_test_pattern(self) -> None:
        on = not (self.main.state or {}).get("test_pattern", False)
        if self.main.connected:
            self.main.send("testpattern.set", on=on)
        else:
            self.main.toast("Start the renderer first.")

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
        if ev.get("event") == "midi_learned" and self.learn_row is not None and self.learn_row < len(self.midi_map):
            self.midi_map[self.learn_row]["on"] = cm.trigger_from_learned(ev["on"])
            self.learn_row = None
            self.learn_bar.set_visible(False)
            self._save_map()
            self._rebuild_mappings()

    # --- Director ------------------------------------------------------------------------

    def _page_director(self, page: Gtk.Box) -> None:
        d = self.cfg.director
        every = d.every.in_beats(self.cfg.clock.beats_per_bar) / max(1, self.cfg.clock.beats_per_bar)
        g = SettingsGroup("Automatic scene changes", "Used by Random mode on the main window.")
        self.every = Stepper(round(every), 1, 64, 1, lambda v: f"<b>{v:.0f}</b> bars",
                             lambda v: self.save("director", {"every": {"bars": int(v), "beats": None}}))
        self.no_repeat = Gtk.Switch(active=d.no_repeat, valign=Gtk.Align.CENTER)
        self.no_repeat.add_css_class("mb-switch")
        self.no_repeat.connect("notify::active", lambda s, _p: self.save("director", {"no_repeat": s.get_active()}))
        g.add(setting_row("Change scene every", None, self.every))
        g.add(setting_row("Avoid repeating the last scene", None, self.no_repeat))
        self.tag_row = Adw.WrapBox(child_spacing=6, line_spacing=6)
        tag_box = vbox(6, label("Only use scenes tagged"), self.tag_row)
        tag_box.add_css_class("setting-row")
        g.add(tag_box)
        page.append(g)
        self.seq_group = SettingsGroup("Sequence", "Plays in order in Sequence mode.")
        page.append(self.seq_group)
        add = push_button("Add scene", "tall", icon_name="list-add-symbolic")
        add.connect("clicked", lambda *_: self._seq_add())
        self.loop_sw = Gtk.Switch(active=d.loop, valign=Gtk.Align.CENTER)
        self.loop_sw.add_css_class("mb-switch")
        self.loop_sw.connect("notify::active", lambda s, _p: self.save("director", {"loop": s.get_active()}))
        page.append(hbox(12, add, Gtk.Box(hexpand=True), label("Loop sequence"), self.loop_sw))
        self.sequence = [(i.scene, i.bars) for i in d.sequence]
        self._rebuild_director_choices()

    def _all_tags(self) -> list[str]:
        return sorted({t for sc in self.library.scenes.values() for t in sc.tags})

    def _rebuild_director_choices(self) -> None:
        if not hasattr(self, "tag_row"):
            return
        while (c := self.tag_row.get_first_child()) is not None:
            self.tag_row.remove(c)
        active = set(self.cfg.director.pool.tags)
        for t in self._all_tags():
            b = Gtk.ToggleButton(label=t, active=t in active)
            b.add_css_class("chip-toggle")
            b.connect("toggled", lambda _b: self._save_tags())
            self.tag_row.append(b)
        self._rebuild_sequence()

    def _save_tags(self) -> None:
        tags, c = [], self.tag_row.get_first_child()
        while c is not None:
            if c.get_active():
                tags.append(c.get_label())
            c = c.get_next_sibling()
        self.save("director", {"pool": {"tags": tags}})

    def _rebuild_sequence(self) -> None:
        g = self.seq_group
        g.clear()
        ids = self.library.ids()
        names = [self.library.scenes[s].name for s in ids]
        if not self.sequence:
            g.add(setting_row("Empty", "Without a sequence, Sequence mode walks all scenes in alphabetical order."))
        for i, (scene, bars) in enumerate(self.sequence):
            dd = dropdown(names, ids.index(scene) if scene in ids else 0, 210,
                          lambda idx, i=i, ids=ids: self._seq_set(i, scene=ids[idx]))
            st = Stepper(bars, 1, 64, 1, lambda v: f"<b>{v:.0f}</b> bars", lambda v, i=i: self._seq_set(i, bars=int(v)))
            rm = Gtk.Button(icon_name="edit-delete-symbolic")
            rm.add_css_class("mb-btn")
            rm.connect("clicked", lambda *_a, i=i: self._seq_remove(i))
            g.add(hbox(12, label(str(i + 1), "kbd"), dd, Gtk.Box(hexpand=True), st, rm, margin_top=8, margin_bottom=8,
                       margin_start=14, margin_end=14))

    def _seq_save(self) -> None:
        self.save("director", {"sequence": [{"scene": s, "bars": b} for s, b in self.sequence]})

    def _seq_set(self, i: int, scene: str | None = None, bars: int | None = None) -> None:
        if self._guard or not (0 <= i < len(self.sequence)):
            return
        s, b = self.sequence[i]
        self.sequence[i] = (scene or s, bars or b)
        self._seq_save()

    def _seq_add(self) -> None:
        ids = self.library.ids()
        if ids:
            self.sequence.append((ids[0], 8))
            self._seq_save()
            self._rebuild_sequence()

    def _seq_remove(self, i: int) -> None:
        if 0 <= i < len(self.sequence):
            del self.sequence[i]
            self._seq_save()
            self._rebuild_sequence()

    # --- Calibration ---------------------------------------------------------------------

    def _page_calibration(self, page: Gtk.Box) -> None:
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
        self._update_calibration()

    def _update_calibration(self) -> None:
        if not hasattr(self, "cal_centre"):
            return
        w = (self.main.state or {}).get("window") if self.main.connected else None
        width, height = (w["width"], w["height"]) if w else (1920, 1080)
        cal = self.cfg.calibration
        centre, size = cm.calibration_readout(cal.model_dump() if cal else None, width, height)
        self.cal_centre.set_text(centre)
        self.cal_size.set_text(size)
        axis = bool(cal and (cal.roll_deg or cal.tilt_deg)) or self.cfg.ball.row_pitch_deg is not None
        self.cal_axis.set_text("Set" if axis else "Not set")
        self.cal_when.set_text(cm.describe_time(cal.calibrated_at if cal else None))

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
                self._update_calibration()
                self.main.toast("Calibration reset.")

        dlg.connect("response", go)
        dlg.present(self)

    # --- Ball ----------------------------------------------------------------------------

    def _page_ball(self, page: Gtk.Box) -> None:
        b = self.cfg.ball
        page.append(label("Starting values for calibration. Calibration refines them.", "secondary"))
        d_cm = b.diameter_cm or 30.0
        rows = 180.0 / b.row_pitch_deg if b.row_pitch_deg else cm.rows_from_tile(d_cm, b.tile_mm or 10.0)
        self._ball = {"d": d_cm, "tile": b.tile_mm or cm.tile_from_rows(d_cm, rows), "rows": rows}
        self.d_step = Stepper(d_cm, 5, 200, 1, lambda v: f"<b>{v:.0f}</b> cm", lambda v: self._ball_changed("d", v))
        self.tile_step = Stepper(round(self._ball["tile"]), 2, 50, 1, lambda v: f"<b>{v:.0f}</b> mm", lambda v: self._ball_changed("tile", v))
        self.rows_step = Stepper(round(rows), 8, 300, 1, lambda v: f"<b>{v:.0f}</b>", lambda v: self._ball_changed("rows", v))
        g = SettingsGroup()
        g.add(setting_row("Ball diameter", None, self.d_step))
        g.add(setting_row("Tile size", None, self.tile_step))
        g.add(setting_row("Number of tile rows", "Alternative to tile size.", self.rows_step))
        page.append(g)
        motor = Gtk.Switch(valign=Gtk.Align.CENTER, sensitive=False)
        motor.add_css_class("mb-switch")
        g2 = SettingsGroup("Motor")
        g2.add(setting_row("Rotation motor", "Not connected.", status_chip("Coming later", ""), motor))
        page.append(g2)
        g3 = SettingsGroup("Advanced")
        adv = push_button("Advanced calibration…")
        adv.set_sensitive(False)
        adv.set_tooltip_text("Ball axis and tile rows: coming later")
        g3.add(setting_row("Advanced calibration…", "Ball axis and tile rows.", adv))
        page.append(g3)

    def _ball_changed(self, which: str, v: float) -> None:
        b = self._ball
        b[which] = v
        self._guard = True
        try:
            if which in ("d", "tile"):
                b["rows"] = cm.rows_from_tile(b["d"], b["tile"])
                self.rows_step.set_value(round(b["rows"]))
            else:
                b["tile"] = cm.tile_from_rows(b["d"], b["rows"])
                self.tile_step.set_value(round(b["tile"]))
        finally:
            self._guard = False
        self.save("ball", {"diameter_cm": b["d"], "tile_mm": round(b["tile"], 2), "row_pitch_deg": round(cm.pitch_from_rows(b["rows"]), 4)})

    # --- live updates from the renderer --------------------------------------------------

    def on_state(self, state: dict[str, Any]) -> None:
        clock = state["clock"]
        while (c := self.midi_status.get_first_child()) is not None:
            self.midi_status.remove(c)
        if clock["source"] == "midi":
            self.midi_status.append(status_chip(f"Receiving · {clock['bpm']:.1f} BPM", "ok") if clock["locked"]
                                    else status_chip("Waiting for clock", "warn"))
        else:
            self.midi_status.append(status_chip("Not used (internal clock)", ""))
        tp = state.get("test_pattern", False)
        self.test_btn.get_child().get_last_child().set_text("Hide test pattern" if tp else "Show test pattern")
        w = state.get("window")
        while (c := self.window_chip_box.get_first_child()) is not None:
            self.window_chip_box.remove(c)
        if w:
            where = f"{w['monitor']} · " if w.get("monitor") else ""
            self.window_sub.set_text(f"{where}{w['width']} × {w['height']}")
            self.window_chip_box.append(status_chip("Fullscreen", "ok") if w["fullscreen"] else status_chip("Windowed", "warn"))
        else:
            self.window_sub.set_text("Waiting for the renderer.")
        self._update_calibration()
        self._rebuild_mappings_if_connection_changed()

    def on_connection(self, up: bool) -> None:
        if not up:
            self.window_sub.set_text("The renderer is not running.")
            while (c := self.window_chip_box.get_first_child()) is not None:
                self.window_chip_box.remove(c)
            self.window_chip_box.append(status_chip("Not running", ""))
        self._rebuild_mappings_if_connection_changed()

    def _rebuild_mappings_if_connection_changed(self) -> None:
        key = self.main.connected
        if key != getattr(self, "_last_conn", None):
            self._last_conn = key
            self._rebuild_mappings()
