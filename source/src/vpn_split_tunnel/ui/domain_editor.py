"""Domain & IP Editor Widget - Empty by default with add/edit/delete."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from vpn_split_tunnel.utils.i18n import _, ngettext
from vpn_split_tunnel.utils.validation import is_ip_or_cidr, normalize_domain
from vpn_split_tunnel.ui.text import plain


class DomainEditor(Adw.PreferencesPage):
    """Widget for editing domains and IP addresses."""

    __gtype_name__ = "DomainEditor"

    __gsignals__ = {
        "domains-changed": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._domains: list[str] = []
        self._ips: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        """Build UI programmatically."""
        group = Adw.PreferencesGroup.new()
        group.set_title(plain(_("Domains and IP Addresses")))
        group.set_description(plain(_("Enter domains or IP addresses (one per line). Supports CIDR notation for IP ranges. Empty by default.")))
        self.add(group)

        # Input section
        input_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        input_box.set_margin_top(6)
        group.add(input_box)

        # Domain input row
        domain_row = Adw.ActionRow.new()
        domain_row.set_title(plain(_("Add Domain")))
        input_box.append(domain_row)

        domain_input_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        domain_row.add_suffix(domain_input_box)

        self.domain_entry = Gtk.Entry.new()
        self.domain_entry.set_placeholder_text(_("example.com"))
        self.domain_entry.set_hexpand(True)
        self.domain_entry.connect("activate", self._on_add_domain)
        domain_input_box.append(self.domain_entry)

        self.add_domain_button = Gtk.Button.new_with_label(_("Add"))
        self.add_domain_button.set_icon_name("list-add-symbolic")
        self.add_domain_button.set_has_frame(True)
        self.add_domain_button.set_tooltip_text(_("Add the domain above to the list"))
        self.add_domain_button.connect("clicked", self._on_add_domain)
        domain_input_box.append(self.add_domain_button)

        # IP/CIDR input row
        ip_row = Adw.ActionRow.new()
        ip_row.set_title(plain(_("Add IP Address / CIDR")))
        input_box.append(ip_row)

        ip_input_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        ip_row.add_suffix(ip_input_box)

        self.ip_entry = Gtk.Entry.new()
        self.ip_entry.set_placeholder_text(_("192.168.1.0/24 or 10.0.0.1"))
        self.ip_entry.set_hexpand(True)
        self.ip_entry.connect("activate", self._on_add_ip)
        ip_input_box.append(self.ip_entry)

        self.add_ip_button = Gtk.Button.new_with_label(_("Add"))
        self.add_ip_button.set_icon_name("list-add-symbolic")
        self.add_ip_button.set_has_frame(True)
        self.add_ip_button.set_tooltip_text(_("Add the IP address or CIDR above to the list"))
        self.add_ip_button.connect("clicked", self._on_add_ip)
        ip_input_box.append(self.add_ip_button)

        # Buttons row
        buttons_row = Adw.ActionRow.new()
        buttons_row.set_title(plain(_("Actions")))
        input_box.append(buttons_row)

        buttons_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        buttons_row.add_suffix(buttons_box)

        self.clear_all_button = Gtk.Button.new_with_label(_("Clear All"))
        self.clear_all_button.set_icon_name("edit-clear-all-symbolic")
        self.clear_all_button.set_has_frame(True)
        self.clear_all_button.add_css_class("destructive-action")
        self.clear_all_button.set_tooltip_text(_("Remove every domain and IP from the list"))
        self.clear_all_button.connect("clicked", self._on_clear_all)
        buttons_box.append(self.clear_all_button)

        self.import_button = Gtk.Button.new_with_label(_("Import from file"))
        self.import_button.set_icon_name("document-import-symbolic")
        self.import_button.set_has_frame(True)
        self.import_button.set_tooltip_text(_("Load domains and IPs from a text file"))
        self.import_button.connect("clicked", self._on_import)
        buttons_box.append(self.import_button)

        self.export_button = Gtk.Button.new_with_label(_("Export to file"))
        self.export_button.set_icon_name("document-export-symbolic")
        self.export_button.set_has_frame(True)
        self.export_button.set_tooltip_text(_("Save the current domains and IPs to a text file"))
        self.export_button.connect("clicked", self._on_export)
        buttons_box.append(self.export_button)

        # List section
        list_group = Adw.PreferencesGroup.new()
        list_group.set_title(plain(_("Current List")))
        list_group.set_description(plain(_("Domains and IP addresses currently configured")))
        group.add(list_group)

        # Scrolled window for list
        self.list_scrolled = Gtk.ScrolledWindow.new()
        self.list_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scrolled.set_min_content_height(200)
        self.list_scrolled.set_max_content_height(300)
        list_group.add(self.list_scrolled)

        self.list_box = Gtk.ListBox.new()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_scrolled.set_child(self.list_box)

        # Empty state label
        self.empty_label = Gtk.Label.new(_("No domains or IPs added yet. Add some above."))
        self.empty_label.add_css_class("dim-label")
        self.empty_label.set_margin_top(24)
        self.empty_label.set_margin_bottom(24)
        self.list_box.append(self.empty_label)

    def set_domains(self, domains: list[str]) -> None:
        """Set domains."""
        self._domains = domains.copy()
        self._populate_list()

    def set_ips(self, ips: list[str]) -> None:
        """Set IPs."""
        self._ips = ips.copy()
        self._populate_list()

    def get_domains(self) -> list[str]:
        """Get domains."""
        return self._domains.copy()

    def get_ips(self) -> list[str]:
        """Get IPs."""
        return self._ips.copy()

    def _populate_list(self) -> None:
        """Populate the listbox."""
        # Clear existing
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()

        # Show empty state if no items
        if not self._domains and not self._ips:
            self.list_box.append(self.empty_label)
            return

        # Add domains
        for domain in self._domains:
            row = self._create_entry_row(domain, "domain")
            self.list_box.append(row)

        # Add IPs
        for ip in self._ips:
            row = self._create_entry_row(ip, "ip")
            self.list_box.append(row)

    def _create_entry_row(self, value: str, entry_type: str) -> Adw.ActionRow:
        """Create a row for a domain or IP with edit/delete."""
        row = Adw.ActionRow.new()
        row.set_title(plain(value))

        # Type badge
        badge = Gtk.Label.new()
        badge.add_css_class("domain-type-badge")
        if entry_type == "domain":
            badge.set_label(_("Domain"))
            badge.add_css_class("domain-type-domain")
        elif "/" in value:
            badge.set_label(_("CIDR"))
            badge.add_css_class("domain-type-cidr")
        else:
            badge.set_label(_("IP"))
            badge.add_css_class("domain-type-ip")
        badge.set_valign(Gtk.Align.CENTER)
        row.add_suffix(badge)

        # Edit button
        edit_btn = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.add_css_class("flat")
        # Icon-only button: the tooltip is the whole affordance for what it
        # does, there is no label to read it from.
        edit_btn.set_tooltip_text(_("Edit this entry"))
        edit_btn.connect("clicked", self._on_edit_entry, value, entry_type)
        row.add_suffix(edit_btn)

        # Remove button
        remove_btn = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text(_("Remove this entry"))
        remove_btn.connect("clicked", self._on_remove_entry, value, entry_type)
        row.add_suffix(remove_btn)

        # GObject data access is unsupported from PyGObject, so the handlers
        # already receive value/type as signal arguments; these attributes are
        # here for anything that walks the rows later.
        row.entry_value = value
        row.entry_type = entry_type

        return row

    def _on_add_domain(self, *_args) -> None:
        """Add domain from entry."""
        raw = self.domain_entry.get_text()
        if not raw.strip():
            return
        domain = normalize_domain(raw)
        if domain is None:
            self._show_error(_("Please enter a valid domain"))
            return
        if domain not in self._domains:
            self._domains.append(domain)
            self.domain_entry.set_text("")
            self._populate_list()
            self._emit_changed()

    def _on_add_ip(self, *_args) -> None:
        """Add IP/CIDR from entry."""
        ip = self.ip_entry.get_text().strip()
        if ip and ip not in self._ips:
            # Basic validation
            if self._validate_ip_cidr(ip):
                self._ips.append(ip)
                self.ip_entry.set_text("")
                self._populate_list()
                self._emit_changed()
            else:
                self._show_error(_("Invalid IP address or CIDR notation"))

    def _validate_ip_cidr(self, value: str) -> bool:
        """Validate IP address or CIDR."""
        return is_ip_or_cidr(value)

    def _apply_edit(self, value: str, entry_type: str, new_text: str) -> None:
        """Validate ``new_text`` and replace ``value`` in the matching list.

        Pure logic, separated from the dialog so it can be tested headless.
        """
        new_value = new_text.strip()
        if not new_value or new_value == value:
            return
        if entry_type == "domain":
            normalized = normalize_domain(new_value)
            if normalized is None:
                self._show_error(_("Please enter a valid domain"))
                return
            if normalized not in self._domains:
                idx = self._domains.index(value)
                self._domains[idx] = normalized
        else:
            if not self._validate_ip_cidr(new_value):
                self._show_error(_("Invalid IP address or CIDR notation"))
                return
            if new_value not in self._ips:
                idx = self._ips.index(value)
                self._ips[idx] = new_value
        self._populate_list()
        self._emit_changed()

    def _on_edit_entry(self, button, value: str, entry_type: str) -> None:
        """Edit an entry."""
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Edit Entry"),
            _("Modify the domain or IP address:")
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Save"))
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry.new()
        entry.set_text(value)
        entry.set_placeholder_text(_("example.com") if entry_type == "domain" else _("192.168.1.0/24"))
        entry.set_hexpand(True)
        dialog.set_extra_child(entry)

        def on_response(dlg, resp):
            if resp == "ok":
                self._apply_edit(value, entry_type, entry.get_text())

        dialog.connect("response", on_response)
        dialog.present()

    def _on_remove_entry(self, button, value: str, entry_type: str) -> None:
        """Remove an entry."""
        if entry_type == "domain":
            self._domains.remove(value)
        else:
            self._ips.remove(value)
        self._populate_list()
        self._emit_changed()

    def _on_clear_all(self, button) -> None:
        """Clear all entries."""
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Clear All Entries"),
            _("Are you sure you want to remove all domains and IP addresses?")
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Clear All"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_confirmed)
        dialog.present()

    def _on_clear_confirmed(self, dialog, response: str) -> None:
        if response == "ok":
            self._domains.clear()
            self._ips.clear()
            self._populate_list()
            self._emit_changed()

    def _on_import(self, button) -> None:
        """Import from file."""
        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Import Domains/IPs"))
        dialog.set_modal(True)

        filters = Gio.ListStore.new(Gtk.FileFilter)
        text_filter = Gtk.FileFilter.new()
        text_filter.set_name(_("Text files"))
        text_filter.add_mime_type("text/plain")
        filters.append(text_filter)
        all_filter = Gtk.FileFilter.new()
        all_filter.set_name(_("All files"))
        all_filter.add_pattern("*")
        filters.append(all_filter)
        dialog.set_filters(filters)

        def on_open(dlg, result):
            try:
                file = dlg.open_finish(result)
                if file:
                    self._import_file(file)
            except GLib.Error:
                pass

        dialog.open(self.get_root(), None, on_open)

    def _import_text(self, content: str) -> list[str]:
        """Parse ``content`` adding valid entries; return the skipped lines.

        Pure logic, separated from the file dialog so it can be tested
        headless. Blank lines and ``#`` comments are ignored, IPs/CIDRs and
        domains are validated, and anything invalid is returned so the caller
        can report it without silently accepting garbage.
        """
        skipped: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if self._validate_ip_cidr(line):
                if line not in self._ips:
                    self._ips.append(line)
                continue
            domain = normalize_domain(line)
            if domain is None:
                skipped.append(line)
            elif domain not in self._domains:
                self._domains.append(domain)
        self._populate_list()
        self._emit_changed()
        return skipped

    def _import_file(self, file: Gio.File) -> None:
        """Import entries from file."""
        try:
            content = file.load_contents(None)[1].decode("utf-8")
        except Exception as e:
            self._show_error(str(e))
            return

        skipped = self._import_text(content)
        if skipped:
            preview = ", ".join(skipped[:5])
            if len(skipped) > 5:
                preview += f" (+{len(skipped) - 5})"
            message = ngettext(
                "%d invalid line was skipped",
                "%d invalid lines were skipped",
                len(skipped),
            )
            self._show_error(f"{message}: {preview}")

    def _on_export(self, button) -> None:
        """Export to file."""
        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Export Domains/IPs"))
        dialog.set_modal(True)
        dialog.set_initial_name("vpn-split-tunnel-domains.txt")

        def on_save(dlg, result):
            try:
                file = dlg.save_finish(result)
                if file:
                    self._export_file(file)
            except GLib.Error:
                pass

        dialog.save(self.get_root(), None, on_save)

    def _export_file(self, file: Gio.File) -> None:
        """Export entries to file."""
        lines = ["# VPN Split Tunnel - Domains & IPs", "# Generated by VPN Split Tunnel", ""]
        lines.append("# Domains")
        lines.extend(self._domains)
        lines.append("")
        lines.append("# IP Addresses / CIDR")
        lines.extend(self._ips)
        content = "\n".join(lines)

        try:
            file.replace_contents_bytes(
                content.encode("utf-8"),
                None,
                False,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                None,
            )
        except Exception as e:
            self._show_error(str(e))

    def _emit_changed(self) -> None:
        """Emit changed signal."""
        self.emit("domains-changed", self._domains.copy(), self._ips.copy())

    def _show_error(self, message: str) -> None:
        """Show error dialog."""
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Error"),
            message
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()


GObject.type_register(DomainEditor)