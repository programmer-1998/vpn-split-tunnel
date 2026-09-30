"""About Dialog - Custom with author info and social links."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from vpn_split_tunnel import __version__
from vpn_split_tunnel.utils.i18n import _


def create_about_dialog(parent=None, **kwargs) -> Adw.AboutDialog:
    """Create and return an AboutDialog instance with custom info."""
    dialog = Adw.AboutDialog.new()
    dialog.set_application_name("VPN Split Tunnel")
    dialog.set_application_icon("com.github.sina.vpn-split-tunnel")
    dialog.set_version(__version__)
    dialog.set_developer_name("Sina Khanzadeh")
    dialog.set_license_type(Adw.License.GPL_3_0)
    dialog.set_website("https://sina-khanzadeh.ir")
    dialog.set_issue_url("https://github.com/sina/vpn-split-tunnel/issues")
    dialog.set_comments(_("Universal VPN Split Tunneling Manager for Linux.\n\nDeveloped by Sina Khanzadeh\n\nContact:\nTelegram: @programmer_1998\nDiscord: @programmer_1998\nInstagram: @programmer_1998\nWebsite: sina-khanzadeh.ir\nEmail: khanzadeh.1377@gmail.com"))

    if parent:
        dialog.set_transient_for(parent)

    return dialog