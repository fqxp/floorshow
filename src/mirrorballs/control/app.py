"""mirrorballs: the GTK4 / libadwaita controller. A thin remote for the renderer; it can be closed,
killed and restarted without touching the show."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from ..common import paths  # noqa: E402
from . import tokens  # noqa: E402
from .client import RendererClient  # noqa: E402
from .launcher import start_renderer  # noqa: E402
from .library import Library  # noqa: E402
from .main_window import MainWindow  # noqa: E402
from .store import ConfigStore  # noqa: E402

APP_ID = "de.fqxp.Mirrorballs"


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.window: MainWindow | None = None
        self.client: RendererClient | None = None
        self._provider = Gtk.CssProvider()

    def do_activate(self) -> None:
        if self.window:
            self.window.present()
            return
        style = Adw.StyleManager.get_default()
        style.set_color_scheme(Adw.ColorScheme.PREFER_DARK)  # used in dark rooms; the design's primary theme
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        style.connect("notify::dark", lambda *_: self._apply_theme())
        self._apply_theme()

        win = MainWindow(self, None, Library(), ConfigStore(), launch_renderer=start_renderer)
        self.client = RendererClient(paths.socket_path(), win.apply_state, win.set_connection, win.toast, win.apply_event)
        win.client = self.client
        self.window = win
        win.present()
        self.client.start()

    def _apply_theme(self) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        self._provider.load_from_string(tokens.css(dark))
        if self.window:
            self.window.set_dark(dark)

    def do_shutdown(self) -> None:
        if self.client:
            self.client.close()
        Adw.Application.do_shutdown(self)


def main(argv: list[str] | None = None) -> int:
    return Application().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
