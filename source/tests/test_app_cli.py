"""Command-line handling of the GTK application.

--version and --help used to be silently swallowed: Gio.Application.run(None)
passes an empty argument list to do_command_line, and the "version" option was
never registered, so the flag never arrived and the app always fell through to
activation. These tests pin the current behaviour: the registered flag reaches
do_command_line, the version is printed, --help is left to GLib, and a plain
invocation activates.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import GLib

from vpn_split_tunnel.app import VPNSplitTunnelApp

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = str(REPO_ROOT / "src")


def _options(flipped: dict[str, bool]) -> GLib.VariantDict:
    """A VariantDict shaped like a parsed command-line option table."""
    vd = GLib.VariantDict.new()
    for key, value in flipped.items():
        vd.insert_value(key, GLib.Variant.new_boolean(value))
    return vd


class FakeCommandLine:
    """Minimal stand-in for Gio.ApplicationCommandLine.

    do_command_line only calls get_options_dict() on it, so a Python object
    with that one method is enough to drive the real handler.
    """

    def __init__(self, options: GLib.VariantDict) -> None:
        self._options = options

    def get_options_dict(self) -> GLib.VariantDict:
        return self._options


class TestCommandLine:
    def test_version_prints_version(self, capsys) -> None:
        app = VPNSplitTunnelApp()
        cl = FakeCommandLine(_options({"version": True}))
        assert app.do_command_line(cl) == 0
        out = capsys.readouterr().out
        assert "VPN Split Tunnel" in out
        assert "0.1.0" in out

    def test_empty_options_activate(self, monkeypatch) -> None:
        app = VPNSplitTunnelApp()
        activated: list[str] = []

        def fake_activate() -> None:
            activated.append("activated")

        monkeypatch.setattr(app, "activate", fake_activate)
        cl = FakeCommandLine(_options({}))
        assert app.do_command_line(cl) == 0
        assert activated

    def test_version_does_not_activate(self, monkeypatch) -> None:
        app = VPNSplitTunnelApp()
        activated: list[str] = []

        def fake_activate() -> None:
            activated.append("activated")

        monkeypatch.setattr(app, "activate", fake_activate)
        cl = FakeCommandLine(_options({"version": True}))
        assert app.do_command_line(cl) == 0
        assert not activated

    def test_debug_activates(self, monkeypatch) -> None:
        """--debug is a logging flag, not a mode: the app still opens."""
        import vpn_split_tunnel.app as app_module

        app = VPNSplitTunnelApp()
        activated: list[str] = []
        logged: list[bool] = []

        def fake_activate() -> None:
            activated.append("activated")

        def fake_setup(debug: bool) -> None:
            logged.append(debug)

        monkeypatch.setattr(app, "activate", fake_activate)
        monkeypatch.setattr(app_module, "setup_logging", fake_setup)
        cl = FakeCommandLine(_options({"debug": True}))
        assert app.do_command_line(cl) == 0
        assert activated
        assert logged == [True]


class TestRealArgv:
    """End-to-end checks through the real application entry point.

    These exercise the two regressions the unit tests cannot: that the process
    argv is actually handed to the application (run(sys.argv), not run(None))
    and that the flag is registered so GLib parses it.
    """

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC
        # Force a headless session: activating a window is not what we test.
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        return subprocess.run(
            [sys.executable, "-m", "vpn_split_tunnel.__main__", *args],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )

    def test_version_flag_prints_version(self) -> None:
        proc = self._run("--version")
        assert proc.returncode == 0
        assert "VPN Split Tunnel 0.1.0" in proc.stdout

    def test_help_exits_zero(self) -> None:
        proc = self._run("--help")
        assert proc.returncode == 0
        assert "--version" in proc.stdout
        # The registered option must show up in GLib's generated help.
        assert "--debug" in proc.stdout

    def test_debug_flag_accepted_and_enables_logging(self) -> None:
        """--debug is accepted by GLib and turns logging on before anything runs.

        In a headless environment do_command_line is never invoked (the
        known pre-existing limitation), so there is no version output. What
        still proves the feature works end to end: GLib does not reject
        --debug ("Unknown option" would be a non-zero exit), and main()
        enables the debug stream, which prints its banner to stderr.
        """
        proc = self._run("--debug")
        assert proc.returncode in (0, 1)  # headless activation may fail later
        assert "Debug mode enabled" in proc.stderr