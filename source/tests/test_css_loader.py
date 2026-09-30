"""The stylesheet loader is defensive by contract.

The theme must never break the app: a missing file, an unparseable file, or a
headless run all end in "no custom style", never in an exception. These tests
pin both halves -- the path resolution for each install shape and every
failure path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("gi")

import vpn_split_tunnel.ui.style as style  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_CSS = ROOT / "resources" / "css" / "style.css"


class TestFindStylesheet:
    def test_source_checkout_is_found(self) -> None:
        path = style._find_stylesheet()
        assert path is not None
        assert path == REAL_CSS
        assert path.is_file()

    def test_css_has_no_gtk3_underscore_colors(self) -> None:
        """GTK4 named colors are @name_color -- not the GTK3 _bg_color forms."""
        text = REAL_CSS.read_text(encoding="utf-8")
        for bad in ("@success_bg_color", "@error_bg_color",
                    "@warning_bg_color", "@window_fg_color@"):
            assert bad not in text, f"GTK3-era/unknown color {bad!r} used"


class TestLoadAppCss:
    def test_headless_returns_false_without_raising(self) -> None:
        with patch.object(style.Gdk.Display, "get_default", return_value=None):
            assert style.load_app_css() is False

    def test_missing_stylesheet_returns_false(self) -> None:
        with patch.object(style, "_find_stylesheet", return_value=None), \
             patch.object(style.Gdk.Display, "get_default", return_value=object()):
            assert style.load_app_css() is False

    def test_unparseable_css_returns_false(self) -> None:
        with patch.object(style, "_find_stylesheet", return_value=REAL_CSS), \
             patch.object(style.Gdk.Display, "get_default", return_value=object()), \
             patch.object(style.Gtk.CssProvider, "load_from_path",
                          side_effect=Exception("parse failure")):
            assert style.load_app_css() is False

    def test_success_registers_the_provider(self) -> None:
        with patch.object(style, "_find_stylesheet", return_value=REAL_CSS), \
             patch.object(style.Gdk.Display, "get_default", return_value=object()), \
             patch.object(style.Gtk.CssProvider, "load_from_path") as load, \
             patch.object(style.Gtk.StyleContext, "add_provider_for_display") as add:
            assert style.load_app_css() is True
            load.assert_called_once_with(str(REAL_CSS))
            add.assert_called_once()
            provider, priority = add.call_args[0][1], add.call_args[0][2]
            assert priority == style.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            assert provider is not None


class TestStylesheetParsesSilently:
    """The shipped stylesheet must parse without GTK CSS warnings.

    GTK 4.14 rejects logical properties (margin-end, border-start, ...),
    @media at-rules and :dir() selectors, printing a "Theme parser error"
    to stderr on every launch. Parsing the real file must produce nothing
    that mentions it. capfd captures at the fd level, so the C-level
    g_log output is seen here. On a newer GTK that understands these the
    test still passes (no warnings at all).
    """

    def test_real_css_has_no_parsing_warnings(self, capfd) -> None:
        provider = style.Gtk.CssProvider()
        provider.load_from_path(str(REAL_CSS))
        captured = capfd.readouterr()
        assert "style.css" not in captured.err