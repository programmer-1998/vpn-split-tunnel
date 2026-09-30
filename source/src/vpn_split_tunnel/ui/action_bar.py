"""Action bar: the Apply and Remove buttons, with a status line.

Both buttons exist because the rules this app installs are live system state:
until they are removed, the machine keeps routing around the VPN. A "remove"
path that is only reachable by restarting the machine is not a removal path.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk

from vpn_split_tunnel.utils.i18n import _


class ActionBar(Adw.ActionRow):
    """Action bar with the apply/remove buttons and the current status."""

    __gtype_name__ = "ActionBar"

    __gsignals__ = {
        "apply-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "remove-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_activatable(False)
        self._build_ui()

    def _build_ui(self) -> None:
        status_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        status_box.set_valign(Gtk.Align.CENTER)
        status_box.set_hexpand(True)
        self.add_prefix(status_box)

        self.status_label = Gtk.Label.new(_("Status: Ready"))
        self.status_label.set_xalign(0)
        self.status_label.set_wrap(True)
        self.status_label.add_css_class("heading")
        status_box.append(self.status_label)

        self.details_label = Gtk.Label.new(_("No VPN selected"))
        self.details_label.set_xalign(0)
        self.details_label.set_wrap(True)
        self.details_label.add_css_class("dim-label")
        status_box.append(self.details_label)

        actions_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        actions_box.set_valign(Gtk.Align.CENTER)
        self.add_suffix(actions_box)

        self.remove_button = Gtk.Button.new_with_label(_("Remove Rules"))
        self.remove_button.set_icon_name("edit-delete-symbolic")
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.set_sensitive(False)
        self.remove_button.set_tooltip_text(_("Undo the rules this app applied"))
        self.remove_button.connect("clicked", self._on_remove_clicked)
        actions_box.append(self.remove_button)

        self.apply_button = Gtk.Button.new_with_label(_("Apply Rules"))
        self.apply_button.set_icon_name("system-run-symbolic")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.set_sensitive(False)
        self.apply_button.set_tooltip_text(
            _("Install the split-tunnel rules into the live network (asks for admin permission)")
        )
        self.apply_button.connect("clicked", self._on_apply_clicked)
        actions_box.append(self.apply_button)

    # ------------------------------------------------------------------
    def _on_apply_clicked(self, button) -> None:
        self.emit("apply-requested")

    def _on_remove_clicked(self, button) -> None:
        self.emit("remove-requested")

    def set_apply_sensitive(self, sensitive: bool) -> None:
        self.apply_button.set_sensitive(sensitive)

    def set_remove_sensitive(self, sensitive: bool) -> None:
        self.remove_button.set_sensitive(sensitive)

    def set_status(self, status: str, label: str) -> None:
        """Set the status text and its colour class."""
        self.status_label.set_label(label)
        for cls in ("status-ready", "status-applying", "status-active", "status-error"):
            self.status_label.remove_css_class(cls)
        self.status_label.add_css_class(f"status-{status}")

    def set_details(self, details: str) -> None:
        self.details_label.set_label(details)


GObject.type_register(ActionBar)
