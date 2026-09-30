"""Every interactive button in the UI explains itself on hover.

The per-row edit/remove buttons are already pinned by test_domain_editor.py.
This suite extends that guarantee to the whole UI: a control whose purpose
cannot be read off its label -- an icon-only button, or the small check/radio
that selects a mode or a row -- is only guessable otherwise. So no Gtk.Button or
Gtk.CheckButton may exist without a tooltip, in any page, including the buttons
that only appear once the page has data (a connection row's "Use this VPN",
or an application row's start button).

MessageDialog responses (Cancel/OK/...) are deliberately not covered: they are
text buttons GTK renders without a tooltip property, and their labels say what
they do.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("gi")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402


def _buttons(root: Gtk.Widget) -> list[Gtk.Widget]:
    """Flatten the widget tree under ``root`` collecting buttons/checks."""
    found: list[Gtk.Widget] = []

    def walk(widget: Gtk.Widget) -> None:
        if isinstance(widget, (Gtk.Button, Gtk.CheckButton)):
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(root)
    return found


def _assert_all_have_tooltips(root: Gtk.Widget, label: str) -> None:
    buttons = _buttons(root)
    assert buttons, f"{label}: walk found no buttons at all"
    missing = [b for b in buttons if not b.get_tooltip_text()]
    assert not missing, (
        f"{label}: {len(missing)} button(s) without a tooltip:\n"
        + "\n".join(
            f"  {type(b).__name__}: label={b.get_label()!r} icon={b.get_icon_name()!r}"
            for b in missing
        )
    )


def _application() -> Application:
    from vpn_split_tunnel.core.app_selector import Application

    return Application(
        desktop_id="org.example.tooltip-test",
        name="Tooltip Test",
        generic_name=None,
        comment=None,
        icon_name=None,
        executable="tooltip-test",
        categories=[],
        no_display=False,
        terminal=False,
    )


def _connection() -> VPNConnection:
    from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType

    return VPNConnection(
        interface="test0",
        vpn_type=VPNType.HAPP,
        name="Test VPN",
        gateway_ip="10.0.0.1",
    )


class TestEveryButtonHasATooltip:
    def test_standalone_pages(self) -> None:
        from vpn_split_tunnel.utils.i18n import setup_i18n

        setup_i18n("en")

        from vpn_split_tunnel.ui.action_bar import ActionBar
        from vpn_split_tunnel.ui.app_selector import AppSelector
        from vpn_split_tunnel.ui.domain_editor import DomainEditor
        from vpn_split_tunnel.ui.mode_selector import ModeSelector
        from vpn_split_tunnel.ui.vpn_selector import VPNSelector

        pages: list[tuple[str, Gtk.Widget]] = [
            ("ActionBar", ActionBar()),
            ("ModeSelector", ModeSelector()),
            ("DomainEditor", DomainEditor()),
            ("VPNSelector(empty)", VPNSelector()),
            ("AppSelector(empty)", AppSelector()),
        ]
        for name, page in pages:
            _assert_all_have_tooltips(page, name)

    def test_buttons_that_only_appear_with_data(self) -> None:
        """Rows built only after data arrives still carry tooltips.

        The empty-state "Add a VPN manually" button, a connection row's
        "Use this VPN" button, an application row's selection check and its
        start button are all constructed per-row/per-connection, so the
        construction-time walk above cannot see them.
        """
        from vpn_split_tunnel.utils.i18n import setup_i18n

        setup_i18n("en")

        from vpn_split_tunnel.ui.app_selector import AppSelector
        from vpn_split_tunnel.ui.vpn_selector import VPNSelector

        # Empty state of the VPN page: the manual-registration button appears.
        vpn = VPNSelector()
        vpn.set_connections([])
        _assert_all_have_tooltips(vpn, "VPNSelector(manual-add)")

        # A detected connection: the "Use this VPN" button appears.
        vpn = VPNSelector()
        vpn.set_connections([_connection()])
        _assert_all_have_tooltips(vpn, "VPNSelector(connection row)")

        # An application row: selection check + start button appear.
        apps = AppSelector()
        apps.set_applications([_application()])
        _assert_all_have_tooltips(apps, "AppSelector(app row)")

    def test_main_window(self) -> None:
        """The whole window's pages, including the reset button, pass the walk.

        The root walked is the content stack, not the window: the header bar's
        back button and the window controls (minimize/maximize/close) are built
        inside libadwaita, not by this app, and the app has no way to give them
        tooltips.
        """
        from vpn_split_tunnel.utils.i18n import setup_i18n

        setup_i18n("en")

        config = SimpleNamespace(
            ui=SimpleNamespace(language="en"),
            policy=SimpleNamespace(
                mode="include",
                selected_apps=[],
                selected_domains=[],
                selected_ips=[],
            ),
        )
        app = Adw.Application.new(
            "org.example.tooltip-test", Gio.ApplicationFlags.FLAGS_NONE
        )

        with (
            patch("vpn_split_tunnel.ui.main_window.get_config", return_value=config),
            patch(
                "vpn_split_tunnel.ui.main_window.get_vpn_detector_manager",
                return_value=SimpleNamespace(detect_all=lambda: []),
            ),
            patch(
                "vpn_split_tunnel.ui.main_window.get_application_manager",
                return_value=SimpleNamespace(
                    get_all_applications=lambda: [],
                    skipped_files=lambda: [],
                ),
            ),
            patch(
                "vpn_split_tunnel.ui.main_window.get_routing_manager",
                return_value=SimpleNamespace(
                    get_state=lambda: SimpleNamespace(
                        is_active=False, vpn_interface=None, error=None
                    )
                ),
            ),
            patch("vpn_split_tunnel.ui.main_window.GLib.Thread.new"),
        ):
            from vpn_split_tunnel.ui.main_window import MainWindow

            window = MainWindow(application=app)
        _assert_all_have_tooltips(window.content_stack, "MainWindow(pages)")