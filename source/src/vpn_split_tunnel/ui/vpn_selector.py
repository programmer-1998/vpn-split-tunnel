"""VPN page - every VPN that is currently up, with the details for each.

The page lists *all* detected connections rather than showing one and hiding the
rest behind a chooser, because the question this page answers is "which VPNs do
I have right now, and what is each one?". Marking a connection as the target only
says which tunnel the split-tunneling rules will be applied to.

Nothing on this page executes a VPN client. It renders what the passive
detectors already learned.
"""

from __future__ import annotations

import ipaddress
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk

from vpn_split_tunnel.core.vpn_detectors.base import (
    TUNNEL_IFACE_PREFIXES,
    VPNConnection,
    VPNType,
)
from vpn_split_tunnel.core.vpn_detectors.unknown import UnknownTunnelDetector
from vpn_split_tunnel.utils.i18n import _, ngettext
from vpn_split_tunnel.ui.text import plain

# Interface name prefixes a manual entry may plausibly refer to. Defined once
# in the detectors' base module so the manual picker and the automatic scan
# agree about what looks like a tunnel.
_TUNNEL_PREFIXES = TUNNEL_IFACE_PREFIXES


class VPNSelector(Adw.PreferencesPage):
    """Lists every active VPN connection with its details."""

    __gtype_name__ = "VPNSelector"

    __gsignals__ = {
        "vpn-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "refresh-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connections: list[VPNConnection] = []
        self._selected: VPNConnection | None = None
        # Keyed by interface name, so selection state survives a rescan.
        self._use_buttons: dict[str, Gtk.Button] = {}
        self._badges: dict[str, Gtk.Image] = {}
        self._dynamic_groups: list[Adw.PreferencesGroup] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.set_title(plain(_("VPN Connection")))
        self.set_icon_name("network-vpn-symbolic")

        self.header_group = Adw.PreferencesGroup.new()
        self.header_group.set_title(plain(_("Detected VPNs")))
        self.add(self.header_group)

        self.summary_row = Adw.ActionRow.new()
        self.summary_row.set_title(plain(_("Scanning...")))
        self.summary_row.set_subtitle(plain(_("Looking for active VPN connections")))
        self.summary_row.set_activatable(False)
        self.header_group.add(self.summary_row)

        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text(_("Scan again"))
        self.refresh_button.add_css_class("flat")
        self.refresh_button.connect("clicked", lambda *_: self.emit("refresh-requested"))
        self.summary_row.add_suffix(self.refresh_button)

    def _rebuild(self) -> None:
        """Rebuild the connection and empty-state groups.

        Groups are discarded and recreated rather than diffed, so a row for a
        VPN that is no longer connected cannot survive a rescan. Adw exposes
        PreferencesPage.remove(), which is the supported way to drop a group.
        """
        for group in self._dynamic_groups:
            self.remove(group)
        self._dynamic_groups.clear()
        self._use_buttons.clear()
        self._badges.clear()

        if not self._connections:
            self.summary_row.set_title(plain(_("Not connected to any VPN")))
            self.summary_row.set_subtitle(plain(_("No active VPN tunnel was found on this system")))
            self._dynamic_groups.append(self._build_empty_state())
        else:
            self.summary_row.set_title(
                plain(ngettext(
                    "%d connection detected", "%d connections detected", len(self._connections)
                ))
            )
            self.summary_row.set_subtitle(
                plain(_("Every tunnel that is up right now, with its addresses"))
            )
            self._dynamic_groups.append(self._build_connections_group())
            # The manual path is not only for an empty list: on a machine with
            # an unrecognised client the auto-scan can miss exactly one tunnel
            # while finding the rest, and the fix is the same either way.
            self._dynamic_groups.append(self._build_manual_group())

        for group in self._dynamic_groups:
            self.add(group)

    def _build_manual_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup.new()
        group.set_title(plain(_("Manual entry")))
        group.set_description(plain(
            _("A tunnel the detectors did not recognise can be added here"))
        )
        row = Adw.ActionRow.new()
        row.set_title(plain(_("Add a VPN manually")))
        row.set_subtitle(plain(_("Pick a tunnel interface, or type its name")))
        button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        button.set_tooltip_text(_("Register a tunnel the detectors missed"))
        button.add_css_class("flat")
        button.connect("clicked", self._on_manual_clicked)
        row.add_suffix(button)
        group.add(row)
        return group

    def _build_connections_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup.new()
        group.set_title(plain(_("Connections")))
        group.set_description(plain(_("Select the connection the rules should apply to")))
        for conn in self._connections:
            group.add(self._build_connection_row(conn))
        return group

    def _build_empty_state(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup.new()

        icon = Gtk.Image.new_from_icon_name("network-vpn-symbolic")
        icon.set_pixel_size(96)
        icon.set_opacity(0.3)
        icon.add_css_class("dim-label")
        group.set_header_suffix(icon)

        row = Adw.ActionRow.new()
        row.set_title(plain(_("Not connected to any VPN")))
        row.set_subtitle(plain(_("Connect a VPN, then scan again. Nothing is started by this app.")))
        row.set_activatable(False)
        row.add_css_class("dim-label")
        group.add(row)

        button = Gtk.Button.new_with_label(_("Add a VPN manually"))
        button.set_icon_name("list-add-symbolic")
        button.set_halign(Gtk.Align.CENTER)
        button.set_margin_top(6)
        button.add_css_class("pill")
        button.add_css_class("flat")
        button.set_tooltip_text(_("Register a tunnel the detectors missed"))
        button.connect("clicked", self._on_manual_clicked)
        group.add(button)
        return group

    def _build_connection_row(self, conn: VPNConnection) -> Adw.ExpanderRow:
        """One expander per connection, holding that connection's details."""
        expander = Adw.ExpanderRow.new()
        # For a named client the label is the client's name; for an unknown one
        # it is the client name plus the interface, because two unrecognised
        # tunnels would otherwise share the same headline.
        expander.set_title(plain(
            conn.name if conn.vpn_type == VPNType.UNKNOWN else conn.client_name
        ))
        expander.set_subtitle(plain(_summarise(conn)))

        badge = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        badge.set_tooltip_text(_("The rules target this connection"))
        badge.set_visible(False)
        expander.add_suffix(badge)
        self._badges[conn.interface] = badge

        button = Gtk.Button.new_with_label(_("Use this VPN"))
        button.set_halign(Gtk.Align.CENTER)
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_tooltip_text(_("Send the split tunnel through this VPN"))
        button.connect("clicked", self._on_use_clicked, conn)
        expander.add_suffix(button)
        self._use_buttons[conn.interface] = button

        for title, value in _detail_rows(conn):
            row = Adw.ActionRow.new()
            row.set_title(plain(title))
            row.set_subtitle(plain(value))
            row.set_activatable(False)
            # Addresses and file paths are worth being able to copy.
            if _is_address(value) or value.startswith(("/", "~")):
                row.set_subtitle_selectable(True)
            expander.add_row(row)

        return expander

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set_connections(self, connections: list[VPNConnection]) -> None:
        """Show the given connections, one expander per connection."""
        self._connections = list(connections)
        self._rebuild()

    def set_loading(self, loading: bool) -> None:
        """Show that a scan is in flight."""
        self.refresh_button.set_sensitive(not loading)
        if loading:
            self.summary_row.set_title(plain(_("Scanning...")))
            self.summary_row.set_subtitle(plain(_("Looking for active VPN connections")))

    def set_selected(self, connection: VPNConnection | None) -> None:
        """Mark which connection the rules target."""
        self._selected = connection
        self._sync_selection()

    def get_selected(self) -> VPNConnection | None:
        return self._selected

    def _sync_selection(self) -> None:
        """Reflect the current target in every connection row."""
        target = self._selected.interface if self._selected else None
        for interface, button in self._use_buttons.items():
            is_target = interface == target
            button.set_sensitive(not is_target)
            button.set_label(
                _("Rules target this VPN") if is_target else _("Use this VPN")
            )
            if is_target:
                badge = self._badges.get(interface)
                if badge is not None:
                    badge.set_visible(True)
        for interface, badge in self._badges.items():
            badge.set_visible(interface == target)

    def _on_use_clicked(self, button, conn: VPNConnection) -> None:
        self._selected = conn
        self._sync_selection()
        self.emit("vpn-selected", conn)

    # ------------------------------------------------------------------
    # Manual entry, for tunnels the detectors cannot recognise
    # ------------------------------------------------------------------
    def _on_manual_clicked(self, button) -> None:
        candidates = _tunnel_interfaces()
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Add a VPN manually"),
            _("Pick a tunnel interface, or type its name.")
            if candidates
            else _("Type the VPN interface name, for example tun0 or wg0."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Add"))
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry.new()
        entry.set_placeholder_text("tun0")
        if candidates:
            entry.set_text(candidates[0])
        dialog.set_extra_child(entry)

        def on_response(dlg, response: str) -> None:
            if response == "ok":
                name = entry.get_text().strip()
                if name:
                    self._add_manual(name)

        dialog.connect("response", on_response)
        dialog.present()

    def _add_manual(self, interface: str) -> None:
        """Register an interface the detectors did not recognise.

        The interface gets the same full facts a scanned tunnel would - the
        addresses, tunnel peer, DNS and MTU come from `interface_facts`, not
        from an empty placeholder - so a manually added connection is just as
        informative as a detected one. Only a device that is down or not
        addressed falls back to a record with no details, because there is
        nothing to learn from a device that has no address.
        """
        if any(conn.interface == interface for conn in self._connections):
            return
        conn = UnknownTunnelDetector().connection_for(interface)
        if conn is None:
            conn = VPNConnection(
                interface=interface,
                vpn_type=VPNType.UNKNOWN,
                name=f"{interface} (manual)",
            )
        else:
            conn.name = f"{interface} (manual)"
        self._connections.append(conn)
        self._rebuild()
        self._selected = conn
        self._sync_selection()
        self.emit("vpn-selected", conn)


# ----------------------------------------------------------------------
def _summarise(conn: VPNConnection) -> str:
    """A one-line summary for a connection's row subtitle."""
    bits = [conn.interface]
    addresses = conn.cidrs or conn.all_addresses
    if addresses:
        bits.append(", ".join(addresses))
    if conn.gateway_ip and conn.gateway_ip not in bits:
        bits.append(_("via %s") % conn.gateway_ip)
    return "  •  ".join(bits)


def _detail_rows(conn: VPNConnection) -> list[tuple[str, str]]:
    """(label, value) pairs for a connection, skipping values we do not have."""
    rows: list[tuple[str, str]] = [(_("Interface"), conn.interface)]

    if conn.vpn_type != VPNType.UNKNOWN:
        rows.append((_("Protocol"), conn.client_name))

    addresses = conn.cidrs or conn.all_addresses
    if addresses:
        rows.append((_("IP Addresses"), ", ".join(addresses)))

    if conn.peer_ip:
        rows.append((_("Tunnel Peer"), conn.peer_ip))
    if conn.gateway_ip and conn.gateway_ip != conn.peer_ip:
        rows.append((_("Gateway"), conn.gateway_ip))
    if conn.dns_servers:
        rows.append((_("DNS Servers"), ", ".join(conn.dns_servers)))
    if conn.mtu:
        rows.append((_("MTU"), str(conn.mtu)))
    if conn.processes:
        rows.append((_("Processes"), ", ".join(conn.processes)))
    for note in conn.also_seen_as or []:
        rows.append((_("Also seen as"), note))
    if conn.config_path:
        rows.append((_("Config File"), conn.config_path))

    return rows


def _tunnel_interfaces() -> list[str]:
    """Interfaces whose names look like a tunnel, for the manual picker.

    Read-only, and the same `ip link show` the detectors use.
    """
    try:
        result = subprocess.run(
            ["ip", "-o", "link", "show"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return []

    names: list[str] = []
    for line in (result.stdout or "").splitlines():
        if ":" not in line:
            continue
        name = line.split(":", 1)[1].strip().split("@")[0]
        if name and name.startswith(_TUNNEL_PREFIXES) and name not in names:
            names.append(name)
    return names


def _is_address(value: str) -> bool:
    """True when every comma-separated token parses as an IP or network."""
    tokens = [t for t in value.replace(",", " ").split() if t]
    if not tokens:
        return False
    for token in tokens:
        try:
            ipaddress.ip_network(token, strict=False)
        except ValueError:
            return False
    return True


GObject.type_register(VPNSelector)
