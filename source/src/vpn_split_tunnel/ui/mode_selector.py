"""Mode Selector Widget - Built programmatically."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk

from vpn_split_tunnel.core.policy import PolicyMode
from vpn_split_tunnel.utils.i18n import _
from vpn_split_tunnel.ui.text import plain


class ModeSelector(Adw.PreferencesPage):
    """Widget for selecting split tunneling mode."""

    __gsignals__ = {
        "mode-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mode = PolicyMode.INCLUDE
        self._build_ui()

    def _build_ui(self) -> None:
        """Build UI programmatically."""
        group = Adw.PreferencesGroup.new()
        group.set_title(plain(_("Mode Selection")))
        group.set_description(plain(_("Choose how split tunneling should work")))
        self.add(group)

        # Include row
        self.include_row = Adw.ActionRow.new()
        self.include_row.set_title(plain(_("Include Mode (Split-Include)")))
        self.include_row.set_subtitle(plain(_("Only selected applications and domains go through VPN. Everything else uses direct connection.")))
        self.include_row.set_activatable(True)
        group.add(self.include_row)

        self.include_radio = Gtk.CheckButton.new()
        self.include_radio.set_active(True)
        self.include_radio.set_valign(Gtk.Align.CENTER)
        self.include_radio.set_tooltip_text(
            _("Include mode: only the selected apps and domains use the VPN")
        )
        self.include_row.add_prefix(self.include_radio)
        self.include_radio.connect("toggled", self._on_include_toggled)

        # Exclude row
        self.exclude_row = Adw.ActionRow.new()
        self.exclude_row.set_title(plain(_("Exclude Mode (Inverse Split/Reverse)")))
        self.exclude_row.set_subtitle(plain(_("Everything goes through VPN EXCEPT selected applications and domains. They use direct connection.")))
        self.exclude_row.set_activatable(True)
        group.add(self.exclude_row)

        self.exclude_radio = Gtk.CheckButton.new()
        self.exclude_radio.set_group(self.include_radio)
        self.exclude_radio.set_valign(Gtk.Align.CENTER)
        self.exclude_radio.set_tooltip_text(
            _("Exclude mode: everything except the selected apps and domains uses the VPN")
        )
        self.exclude_row.add_prefix(self.exclude_radio)
        self.exclude_radio.connect("toggled", self._on_exclude_toggled)

        # Make rows activatable
        self.include_row.connect("activated", lambda *_: self.include_radio.set_active(True))
        self.exclude_row.connect("activated", lambda *_: self.exclude_radio.set_active(True))

    def set_mode(self, mode: PolicyMode) -> None:
        """Set the current mode."""
        self._mode = mode
        if mode == PolicyMode.INCLUDE:
            self.include_radio.set_active(True)
        else:
            self.exclude_radio.set_active(True)

    def get_mode(self) -> PolicyMode:
        """Get the current mode."""
        return self._mode

    def _on_include_toggled(self, radio) -> None:
        if radio.get_active():
            self._mode = PolicyMode.INCLUDE
            self.emit("mode-changed", self._mode)

    def _on_exclude_toggled(self, radio) -> None:
        if radio.get_active():
            self._mode = PolicyMode.EXCLUDE
            self.emit("mode-changed", self._mode)