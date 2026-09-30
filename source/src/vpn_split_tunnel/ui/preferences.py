"""Preferences Dialog."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from vpn_split_tunnel.utils.config import get_config
from vpn_split_tunnel.utils.i18n import _, get_i18n
from vpn_split_tunnel.ui.text import plain


class PreferencesDialog(Adw.PreferencesDialog):
    """Preferences dialog."""

    __gtype_name__ = "PreferencesDialog"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._config = get_config()
        self._i18n = get_i18n()
        self.set_title(_("Preferences"))
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        """Build UI programmatically."""
        # General page
        general_page = Adw.PreferencesPage.new()
        general_page.set_title(plain(_("General")))
        general_page.set_icon_name("preferences-system-symbolic")

        behavior_group = Adw.PreferencesGroup.new()
        behavior_group.set_title(plain(_("Behavior")))
        general_page.add(behavior_group)

        self.auto_detect_switch = Gtk.Switch.new()
        self.auto_detect_switch.set_valign(Gtk.Align.CENTER)
        self.auto_detect_switch.set_active(True)
        auto_detect_row = Adw.ActionRow.new()
        auto_detect_row.set_title(plain(_("Auto-detect VPN")))
        auto_detect_row.set_subtitle(plain(_("Automatically detect active VPN connections on startup")))
        auto_detect_row.add_suffix(self.auto_detect_switch)
        behavior_group.add(auto_detect_row)

        self.apply_startup_switch = Gtk.Switch.new()
        self.apply_startup_switch.set_valign(Gtk.Align.CENTER)
        apply_startup_row = Adw.ActionRow.new()
        apply_startup_row.set_title(plain(_("Apply rules on startup")))
        apply_startup_row.set_subtitle(plain(_("Automatically apply last used split tunneling rules on application start")))
        apply_startup_row.add_suffix(self.apply_startup_switch)
        behavior_group.add(apply_startup_row)

        self.notifications_switch = Gtk.Switch.new()
        self.notifications_switch.set_valign(Gtk.Align.CENTER)
        self.notifications_switch.set_active(True)
        notifications_row = Adw.ActionRow.new()
        notifications_row.set_title(plain(_("Show notifications")))
        notifications_row.set_subtitle(plain(_("Display desktop notifications when rules are applied or removed")))
        notifications_row.add_suffix(self.notifications_switch)
        behavior_group.add(notifications_row)

        # Appearance page
        appearance_page = Adw.PreferencesPage.new()
        appearance_page.set_title(plain(_("Appearance")))
        appearance_page.set_icon_name("preferences-desktop-theme-symbolic")

        theme_group = Adw.PreferencesGroup.new()
        theme_group.set_title(plain(_("Theme")))
        appearance_page.add(theme_group)

        self.theme_combo = Gtk.ComboBoxText.new()
        self.theme_combo.set_valign(Gtk.Align.CENTER)
        self.theme_combo.append("system", _("System"))
        self.theme_combo.append("light", _("Light"))
        self.theme_combo.append("dark", _("Dark"))
        theme_row = Adw.ActionRow.new()
        theme_row.set_title(plain(_("Theme")))
        theme_row.set_subtitle(plain(_("Choose application theme")))
        theme_row.add_suffix(self.theme_combo)
        theme_group.add(theme_row)

        self.language_combo = Gtk.ComboBoxText.new()
        self.language_combo.set_valign(Gtk.Align.CENTER)
        self.language_combo.append("system", _("System"))
        for lang_code, lang_name in self._i18n.get_available_languages():
            self.language_combo.append(lang_code, lang_name)
        language_row = Adw.ActionRow.new()
        language_row.set_title(plain(_("Language")))
        language_row.set_subtitle(plain(_("Choose application language (requires restart)")))
        language_row.add_suffix(self.language_combo)
        theme_group.add(language_row)

        # Network page
        network_page = Adw.PreferencesPage.new()
        network_page.set_title(plain(_("Network")))
        network_page.set_icon_name("preferences-system-network-symbolic")

        routing_group = Adw.PreferencesGroup.new()
        routing_group.set_title(plain(_("Routing Configuration")))
        network_page.add(routing_group)

        self.tun_device_entry = Gtk.Entry.new()
        self.tun_device_entry.set_valign(Gtk.Align.CENTER)
        self.tun_device_entry.set_width_chars(15)
        self.tun_device_entry.set_placeholder_text("tun0")
        tun_device_row = Adw.ActionRow.new()
        tun_device_row.set_title(plain(_("VPN Interface")))
        tun_device_row.set_subtitle(plain(_("TUN interface name (e.g., tun0, wg0, xray_tun)")))
        tun_device_row.add_suffix(self.tun_device_entry)
        routing_group.add(tun_device_row)

        adj = Gtk.Adjustment.new(101, 1, 255, 1, 1, 0)
        self.routing_table_spin = Gtk.SpinButton.new(adj, 1, 0)
        self.routing_table_spin.set_valign(Gtk.Align.CENTER)
        routing_table_row = Adw.ActionRow.new()
        routing_table_row.set_title(plain(_("Routing Table ID")))
        routing_table_row.set_subtitle(plain(_("Policy routing table number (1-255)")))
        routing_table_row.add_suffix(self.routing_table_spin)
        routing_group.add(routing_table_row)

        adj = Gtk.Adjustment.new(5555, 1, 2147483647, 1, 1, 0)
        self.fwmark_spin = Gtk.SpinButton.new(adj, 1, 0)
        self.fwmark_spin.set_valign(Gtk.Align.CENTER)
        fwmark_row = Adw.ActionRow.new()
        fwmark_row.set_title(plain(_("Firewall Mark")))
        fwmark_row.set_subtitle(plain(_("fwmark value for policy routing (1-2147483647)")))
        fwmark_row.add_suffix(self.fwmark_spin)
        routing_group.add(fwmark_row)

        # Rule priority. This must sit above the VPN client's own rules or it
        # is never consulted, so the row says what it is for.
        adj = Gtk.Adjustment.new(8500, 1, 32766, 1, 10, 0)
        self.rule_priority_spin = Gtk.SpinButton.new(adj, 1, 0)
        self.rule_priority_spin.set_valign(Gtk.Align.CENTER)
        rule_priority_row = Adw.ActionRow.new()
        rule_priority_row.set_title(plain(_("Rule Priority")))
        rule_priority_row.set_subtitle(
            plain(_(
                "Lower runs first. Must be below your VPN client's own rules, "
                "or they take precedence and these never apply"
            ))
        )
        rule_priority_row.add_suffix(self.rule_priority_spin)
        routing_group.add(rule_priority_row)

        self.cgroup_slice_entry = Gtk.Entry.new()
        self.cgroup_slice_entry.set_valign(Gtk.Align.CENTER)
        self.cgroup_slice_entry.set_width_chars(25)
        self.cgroup_slice_entry.set_placeholder_text("vpn-split-tunnel.slice")
        cgroup_slice_row = Adw.ActionRow.new()
        cgroup_slice_row.set_title(plain(_("Cgroup Slice")))
        cgroup_slice_row.set_subtitle(plain(_("systemd slice name for cgroup v2 (e.g., vpn-split-tunnel.slice)")))
        cgroup_slice_row.add_suffix(self.cgroup_slice_entry)
        routing_group.add(cgroup_slice_row)

        # Add pages
        self.add(general_page)
        self.add(appearance_page)
        self.add(network_page)

        # Connect signals
        self.auto_detect_switch.connect("notify::active", self._on_auto_detect_changed)
        self.apply_startup_switch.connect("notify::active", self._on_apply_startup_changed)
        self.notifications_switch.connect("notify::active", self._on_notifications_changed)
        self.theme_combo.connect("changed", self._on_theme_changed)
        self.language_combo.connect("changed", self._on_language_changed)
        self.tun_device_entry.connect("changed", self._on_tun_device_changed)
        self.routing_table_spin.connect("value-changed", self._on_routing_table_changed)
        self.fwmark_spin.connect("value-changed", self._on_fwmark_changed)
        self.rule_priority_spin.connect("value-changed", self._on_rule_priority_changed)
        self.cgroup_slice_entry.connect("changed", self._on_cgroup_slice_changed)

    def _load_settings(self) -> None:
        """Load settings from config."""
        # General
        self.auto_detect_switch.set_active(self._config.vpn.auto_detect)
        self.apply_startup_switch.set_active(self._config.policy.apply_to_all_users)
        self.notifications_switch.set_active(True)

        # Appearance - Theme
        self.theme_combo.set_active_id(self._config.ui.theme)

        # Appearance - Language
        self.language_combo.set_active_id(self._config.ui.language)

        # Network
        self.tun_device_entry.set_text(self._config.routing.tun_device)
        self.routing_table_spin.set_value(self._config.routing.routing_table)
        self.fwmark_spin.set_value(self._config.routing.fwmark)
        self.rule_priority_spin.set_value(self._config.routing.rule_priority)
        self.cgroup_slice_entry.set_text(self._config.routing.cgroup_slice)

    # Signal handlers
    def _on_auto_detect_changed(self, switch, _) -> None:
        self._config.vpn.auto_detect = switch.get_active()
        self._config.save()

    def _on_apply_startup_changed(self, switch, _) -> None:
        self._config.policy.apply_to_all_users = switch.get_active()
        self._config.save()

    def _on_notifications_changed(self, switch, _) -> None:
        pass

    def _on_theme_changed(self, combo) -> None:
        theme = combo.get_active_id()
        if theme:
            self._config.ui.theme = theme
            self._config.save()
            style_manager = Adw.StyleManager.get_default()
            if theme == "dark":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            elif theme == "light":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            else:
                style_manager.set_color_scheme(Adw.ColorScheme.PREFER_LIGHT)

    def _on_language_changed(self, combo) -> None:
        lang = combo.get_active_id()
        if lang:
            self._config.ui.language = lang
            self._config.save()
            self._show_restart_notice()

    def _on_tun_device_changed(self, entry) -> None:
        self._config.routing.tun_device = entry.get_text()
        self._config.save()

    def _on_routing_table_changed(self, spin) -> None:
        self._config.routing.routing_table = int(spin.get_value())
        self._config.save()

    def _on_fwmark_changed(self, spin) -> None:
        self._config.routing.fwmark = int(spin.get_value())
        self._config.save()

    def _on_rule_priority_changed(self, spin) -> None:
        self._config.routing.rule_priority = int(spin.get_value())
        self._config.save()

    def _on_cgroup_slice_changed(self, entry) -> None:
        self._config.routing.cgroup_slice = entry.get_text()
        self._config.save()

    def _show_restart_notice(self) -> None:
        """Show notice that language change requires restart."""
        pass