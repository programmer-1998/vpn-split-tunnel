"""Loads and registers the application stylesheet.

The theme lives in ``resources/css/style.css`` in the source tree and is
installed to ``<datadir>/vpn-split-tunnel/style.css``. Loading it is
deliberately defensive: the application must run unchanged on a machine that
does not have the file (or that has no display at all), so every failure path
ends in "no custom style", never in an exception.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk


def _find_stylesheet() -> Path | None:
    """The first existing stylesheet path, from any supported layout."""
    here = Path(__file__).resolve().parents
    candidates: list[Path] = [
        # Source checkout: <project>/resources/css/style.css.  Checked first
        # so a development run picks up the tree's stylesheet instead of a
        # stale installed copy.
        here[3] / "resources" / "css" / "style.css",
    ]
    # Installed under a prefix that owns this module:
    # <prefix>/share/vpn-split-tunnel/style.css.  Walk up past
    # <prefix>/lib/python3.*/site-packages/vpn_split_tunnel/ui/.
    for depth in (3, 4, 5):
        candidates.append(here[depth] / "share" / "vpn-split-tunnel" / "style.css")
    # OS-installed (or admin-set) prefix.
    candidates.append(Path("/usr/share/vpn-split-tunnel/style.css"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_app_css() -> bool:
    """Register the stylesheet with the default display.

    Returns ``False`` (without raising) when there is no display, the file is
    missing, or it cannot be parsed, so callers can ignore the result.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return False
    path = _find_stylesheet()
    if path is None:
        return False
    try:
        provider = Gtk.CssProvider()
        provider.load_from_path(str(path))
    except Exception:
        return False
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    return True