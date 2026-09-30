"""Main Application Class."""

from __future__ import annotations

import logging
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, GLib, Gtk

from vpn_split_tunnel import __version__
from vpn_split_tunnel.ui.main_window import MainWindow
from vpn_split_tunnel.ui.style import load_app_css
from vpn_split_tunnel.utils.config import get_config
from vpn_split_tunnel.utils.i18n import setup_i18n, get_i18n
from vpn_split_tunnel.utils.logger import install_crash_hook, setup_logging

logger = logging.getLogger(__name__)


class VPNSplitTunnelApp(Adw.Application):
    """Main application class."""

    __gtype_name__ = "VPNSplitTunnelApp"

    def __init__(self, **kwargs):
        super().__init__(
            application_id="com.github.sina.vpn-split-tunnel",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN,
            **kwargs
        )
        # Declare --version as a real option so GLib delivers it (and so
        # --help lists it) instead of leaving it as an ignored argument.
        self.add_main_option(
            "version",
            ord("v"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Show version and exit",
            None,
        )
        # Declare --debug as a real option too. Because HANDLES_COMMAND_LINE
        # is set, GLib would reject an unregistered flag before it ever
        # reaches do_command_line, so logging has to be wired through the
        # option table to be reachable at all.
        self.add_main_option(
            "debug",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Enable debug logging to the terminal",
            None,
        )
        self._window: MainWindow | None = None
        self._config = get_config()

    def do_startup(self) -> None:
        """Application startup."""
        # Chain up to parent
        Adw.Application.do_startup(self)
        # An uncaught exception must always find its way to the terminal,
        # not get swallowed by the main loop.
        install_crash_hook()

        # Load config and setup i18n
        self._config.load()
        setup_i18n(self._config.ui.language)
        logger.debug(
            "Startup: config=%s language=%s theme=%s",
            getattr(self._config, "_config_path", "?"),
            get_i18n().current_language,
            self._config.ui.theme,
        )

        # Apply theme
        self._apply_theme()

        # Apply the application stylesheet (best effort: a missing file or a
        # headless run simply means no custom styling).
        load_app_css()

        # Setup actions
        self._setup_actions()

    def _apply_theme(self) -> None:
        """Apply theme from config."""
        style_manager = Adw.StyleManager.get_default()
        theme = self._config.ui.theme
        if theme == "dark":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif theme == "light":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def _setup_actions(self) -> None:
        """Setup application actions."""
        # Preferences action
        prefs_action = Gio.SimpleAction.new("preferences", None)
        prefs_action.connect("activate", self._on_preferences)
        self.add_action(prefs_action)

        # About action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)

    def do_activate(self) -> None:
        """Application activation."""
        if not self._window:
            self._window = MainWindow(application=self)
            logger.debug("Main window created")
        self._window.present()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Handle command line arguments."""
        options = command_line.get_options_dict()

        # Handle --debug first: it must stay visible for the rest of the
        # session (--debug --version should only print the version, but the
        # flag has to be accepted; the option table entry above does that).
        if options.contains("debug"):
            setup_logging(True)

        # Handle --version (--help is handled by GLib's option parser).
        if options.contains("version"):
            print(f"VPN Split Tunnel {__version__}")
            return 0

        # Default: activate
        self.activate()
        return 0

    def _on_preferences(self, *_) -> None:
        """Show preferences dialog."""
        if self._window:
            from vpn_split_tunnel.ui.preferences import PreferencesDialog
            dialog = PreferencesDialog(parent=self._window)
            dialog.present()

    def _on_about(self, *_) -> None:
        """Show about dialog."""
        if self._window:
            from vpn_split_tunnel.ui.about import create_about_dialog
            dialog = create_about_dialog(parent=self._window)
            dialog.present()

    def _on_quit(self, *_) -> None:
        """Quit application."""
        self.quit()


def main() -> int:
    """Main entry point."""
    # Configure logging before the application object exists, so that even
    # setup-time messages reach the terminal under --debug.
    setup_logging("--debug" in sys.argv)
    app = VPNSplitTunnelApp()
    # Pass the real argv: Gio.Application.run(None) hands do_command_line an
    # empty argument list, so --version/--help would silently fail.
    return app.run(sys.argv)


if __name__ == "__main__":
    import sys
    sys.exit(main())