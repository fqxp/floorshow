"""Scenes dialog: the scene files, their validity and the operations on them.

The files themselves are the scene library; this dialog only creates, copies, renames and deletes
them, and shows why one does not load. Saving a file reloads its scene while the show runs, so the
dialog never has to be open for a scene to take effect.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..common import paths  # noqa: E402
from . import scene_files as sf  # noqa: E402
from . import viewmodel as vm  # noqa: E402
from .widgets import SettingsGroup, hbox, icon, kbd, label, push_button, set_classes, status_chip, vbox  # noqa: E402

REFRESH_MS = 1000  # a stat() per scene file, so the list notices edits made outside the app


class ScenesDialog(Adw.Window):
    def __init__(self, main: Any):
        super().__init__(title="Scenes", modal=True, transient_for=main, default_width=720, default_height=600)
        self.add_css_class("mb-dialog")
        self.main = main
        self.library = main.library
        self.cfg = main.store.load()
        self.selected_scene: str | None = None

        view = Adw.ToolbarView()
        self.set_content(view)
        header = Adw.HeaderBar()
        header.add_css_class("mb-header")
        header.set_show_start_title_buttons(False)
        header.set_title_widget(label("Scenes", "title-label"))
        view.add_top_bar(header)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        page = vbox(18, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        scroller.set_child(page)
        self._build(page)
        view.set_content(scroller)

        self.connect("close-request", self._on_close)
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", lambda _c, k, *_a: (self.close() or True) if k == Gdk.KEY_Escape else False)
        self.add_controller(ctrl)
        GLib.timeout_add(REFRESH_MS, self._tick)

    def _on_close(self, *_a: Any) -> bool:
        self.main.scenes_dialog = None
        self.main.refresh()
        return False

    def _tick(self) -> bool:
        if not self.get_visible():
            return False
        if self.library.refresh():
            self._rebuild_scenes()
        return True

    def _build(self, page: Gtk.Box) -> None:
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
