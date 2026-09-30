"""Main Application Window - Redesigned per user specifications."""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, GLib, Gtk

from vpn_split_tunnel.ui.vpn_selector import VPNSelector
from vpn_split_tunnel.ui.mode_selector import ModeSelector
from vpn_split_tunnel.ui.app_selector import AppSelector
from vpn_split_tunnel.ui.domain_editor import DomainEditor
from vpn_split_tunnel.ui.action_bar import ActionBar
from vpn_split_tunnel.ui.preferences import PreferencesDialog
from vpn_split_tunnel.ui.about import create_about_dialog
from vpn_split_tunnel.core.vpn_detectors.manager import get_vpn_detector_manager
from vpn_split_tunnel.core.app_selector import get_application_manager
from vpn_split_tunnel.core.policy import PolicyMode, SplitTunnelPolicy, RoutingRule
from vpn_split_tunnel.core.routing.manager import get_routing_manager
from vpn_split_tunnel.utils.config import get_config
from vpn_split_tunnel.utils.i18n import _, get_i18n, ngettext, setup_i18n
from vpn_split_tunnel import __version__
from vpn_split_tunnel.ui.text import plain


class MainWindow(Adw.ApplicationWindow):
    """Main application window with sidebar navigation."""

    # page_id, title, subtitle. Wrapped in lambdas so the strings are
    # translated lazily, after i18n is set up, not at import time.
    PAGE_TITLES = [
        ("vpn", lambda: _("VPN Connection"), lambda: _("Detected VPN status and details")),
        ("mode", lambda: _("Split Tunneling Mode"), lambda: _("Choose how split tunneling should work")),
        ("apps", lambda: _("Applications"), lambda: _("Select applications to include or exclude from the VPN tunnel")),
        ("domains", lambda: _("Domains and IP Addresses"), lambda: _("Manage domains and IP ranges")),
        ("status", lambda: _("Status and Actions"), lambda: _("Apply or remove split tunneling rules")),
    ]

    def __init__(self, *, application: Adw.Application, **kwargs):
        super().__init__(application=application, **kwargs)

        self.set_title(_("VPN Split Tunnel"))
        self.set_default_size(1100, 700)
        self.set_icon_name("com.github.sina.vpn-split-tunnel")

        # Initialize i18n
        config = get_config()
        setup_i18n(config.ui.language)

        # Persian is right-to-left. Gtk inherits the direction set on a window
        # down to every child, so one call re-lays-out the whole UI.
        self.set_direction(
            Gtk.TextDirection.RTL if get_i18n().is_rtl else Gtk.TextDirection.LTR
        )

        # Core components
        self._vpn_manager = get_vpn_detector_manager()
        self._app_manager = get_application_manager()
        self._routing_manager = get_routing_manager()
        self._config = config

        # State
        self._selected_vpn = None
        self._policy = SplitTunnelPolicy()
        self._is_applying = False

        # Build UI
        self._build_ui()
        self._connect_signals()
        self._load_initial_data()

    def _build_ui(self) -> None:
        """Build the main UI: a sidebar pane beside a switching content stack."""
        # Content stack holds one page per section
        self.content_stack = Gtk.Stack.new()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.content_stack.set_transition_duration(200)
        self._create_section_pages()

        # Each section renders its own header bar so the title follows the
        # selected sidebar row. The window controls live on this header.
        self.content_header = Adw.HeaderBar.new()
        self.content_header.set_title_widget(
            Adw.WindowTitle.new(_("VPN Connection"), _("Detected VPN status and details"))
        )
        self.content_header.set_show_start_title_buttons(True)
        self.content_header.set_show_end_title_buttons(True)

        content_toolbar = Adw.ToolbarView.new()
        content_toolbar.add_top_bar(self.content_header)
        content_toolbar.set_content(self.content_stack)

        # Sidebar
        self.sidebar_box = self._create_sidebar_page()

        # Sidebar scrolls independently when the nav list is long
        sidebar_scroll = Gtk.ScrolledWindow.new()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_child(self.sidebar_box)
        sidebar_scroll.set_size_request(260, -1)

        # Divider between sidebar and content: drawn by the CSS rule
        # `.main-panes > separator` on the paned handle, which follows the
        # pane flip in RTL (see resources/css/style.css).
        panes = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        panes.set_start_child(sidebar_scroll)
        panes.set_end_child(content_toolbar)
        panes.set_position(260)
        panes.set_resize_start_child(False)
        panes.set_shrink_start_child(False)
        panes.set_vexpand(True)
        panes.add_css_class("main-panes")

        # A plain Gtk.Window has no Adw header bar of its own, so wrap the
        # panes in a ToolbarView to get one. The content header is the visible
        # one; this only exists so the window has a surface to draw into.
        outer = Adw.ToolbarView.new()

        # Toasts are placed here rather than inside a page so that a message
        # about a launch is visible from whichever page the user is on: the
        # action that causes it starts on the applications page, but the thing
        # it affects is the policy preview on the status page.
        self._toast_overlay = Adw.ToastOverlay.new()
        self._toast_overlay.set_child(panes)
        outer.set_content(self._toast_overlay)

        self.set_content(outer)
        self._create_menu_actions()

    def _create_sidebar_page(self) -> Gtk.Widget:
        """Create the sidebar with navigation list."""
        # Sidebar list box
        sidebar_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        sidebar_box.add_css_class("sidebar")
        sidebar_box.set_size_request(260, -1)

        # App title in sidebar
        title_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        title_box.set_margin_top(16)
        title_box.set_margin_bottom(16)
        title_box.set_margin_start(16)
        title_box.set_margin_end(16)

        icon = Gtk.Image.new_from_icon_name("com.github.sina.vpn-split-tunnel")
        icon.set_pixel_size(48)
        icon.set_valign(Gtk.Align.CENTER)
        title_box.append(icon)

        title_label = Gtk.Label.new(_("VPN Split Tunnel"))
        title_label.add_css_class("title-2")
        title_label.set_xalign(0)
        title_box.append(title_label)

        subtitle_label = Gtk.Label.new(_("Universal VPN Split Tunneling"))
        subtitle_label.add_css_class("dim-label")
        subtitle_label.set_xalign(0)
        subtitle_label.set_wrap(True)
        title_box.append(subtitle_label)

        sidebar_box.append(title_box)

        # Separator
        separator = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
        sidebar_box.append(separator)

        # Navigation list
        self.nav_list = Gtk.ListBox.new()
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.connect("row-selected", self._on_nav_row_selected)
        sidebar_box.append(self.nav_list)

        # Add navigation items
        nav_items = [
            ("vpn", _("VPN Connection"), "network-vpn-symbolic"),
            ("mode", _("Split Tunneling Mode"), "preferences-system-network-symbolic"),
            ("apps", _("Applications"), "applications-system-symbolic"),
            ("domains", _("Domains and IP Addresses"), "network-server-symbolic"),
            ("status", _("Status and Actions"), "system-run-symbolic"),
        ]

        self.nav_rows = {}
        for idx, (page_id, title, icon_name) in enumerate(nav_items):
            row = Adw.ActionRow.new()
            row.set_title(plain(title))
            row.page_id = page_id
            
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(20)
            icon.set_valign(Gtk.Align.CENTER)
            icon.add_css_class("nav-icon")
            row.add_prefix(icon)
            
            row.set_activatable(True)
            self.nav_list.append(row)
            self.nav_rows[page_id] = row

        # Select first item by default
        self.nav_list.select_row(self.nav_rows["vpn"])

        # Bottom spacer
        spacer = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        spacer.set_vexpand(True)
        sidebar_box.append(spacer)

        # About section at bottom
        about_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        about_box.set_margin_top(16)
        about_box.set_margin_bottom(16)
        about_box.set_margin_start(16)
        about_box.set_margin_end(16)

        # Version
        version_label = Gtk.Label.new(f"v{__version__}")
        version_label.add_css_class("dim-label")
        version_label.add_css_class("caption")
        version_label.set_xalign(0)
        about_box.append(version_label)

        # Author
        author_label = Gtk.Label.new(_("Developed by Sina Khanzadeh"))
        author_label.add_css_class("dim-label")
        author_label.add_css_class("caption")
        author_label.set_xalign(0)
        about_box.append(author_label)

        # Social links
        social_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        social_box.set_margin_top(8)

        links = [
            ("Telegram", "@programmer_1998"),
            ("Discord", "@programmer_1998"),
            ("Instagram", "@programmer_1998"),
            ("Website", "sina-khanzadeh.ir"),
            ("Email", "khanzadeh.1377@gmail.com"),
        ]

        for platform, handle in links:
            link_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
            link_label = Gtk.Label.new(f"{platform}:")
            link_label.add_css_class("dim-label")
            link_label.add_css_class("caption")
            link_label.set_size_request(80, -1)
            link_box.append(link_label)
            
            handle_label = Gtk.Label.new(handle)
            handle_label.add_css_class("caption")
            handle_label.set_selectable(True)
            handle_label.set_xalign(0)
            link_box.append(handle_label)
            
            social_box.append(link_box)

        about_box.append(social_box)
        sidebar_box.append(about_box)

        return sidebar_box

    def _create_section_pages(self) -> None:
        """Create all section pages for the content stack.

        Each page is a plain scrollable box. Titles are shown by the single
        shared header bar in _build_ui, which _on_nav_row_selected updates.
        """
        # VPN Section
        self.vpn_selector = VPNSelector()
        self.content_stack.add_titled(self._wrap(self.vpn_selector), "vpn", _("VPN Connection"))

        # Mode Section
        self.mode_selector = ModeSelector()
        self.content_stack.add_titled(self._wrap(self.mode_selector), "mode", _("Split Tunneling Mode"))

        # Apps Section
        self.app_selector = AppSelector()
        self.content_stack.add_titled(self._wrap(self.app_selector), "apps", _("Applications"))

        # Domains Section
        self.domain_editor = DomainEditor()
        self.content_stack.add_titled(self._wrap(self.domain_editor), "domains", _("Domains and IP Addresses"))

        # Status & Actions Section
        status_content = Gtk.Box.new(Gtk.Orientation.VERTICAL, 24)
        status_content.set_margin_top(24)
        status_content.set_margin_start(24)
        status_content.set_margin_end(24)
        status_content.set_margin_bottom(24)
        status_content.set_valign(Gtk.Align.CENTER)
        status_content.set_halign(Gtk.Align.CENTER)
        self.content_stack.add_titled(
            self._wrap(status_content, scroll=False), "status", _("Status and Actions")
        )

        # Rules card. The status line itself lives in the ActionBar below, so
        # this card only answers "what rules exist right now".
        rules_card = Adw.PreferencesGroup.new()
        rules_card.set_title(plain(_("Current Rules")))
        rules_card.set_description(
            plain(_("What will be applied when you press Apply Rules"))
        )
        status_content.append(rules_card)

        self.target_row = Adw.ActionRow.new()
        self.target_row.set_title(plain(_("Target VPN")))
        self.target_row.set_activatable(False)
        rules_card.add(self.target_row)

        self.mode_summary_row = Adw.ActionRow.new()
        self.mode_summary_row.set_title(plain(_("Mode")))
        self.mode_summary_row.set_activatable(False)
        rules_card.add(self.mode_summary_row)

        self.applications_row = Adw.ActionRow.new()
        self.applications_row.set_title(plain(_("Applications")))
        self.applications_row.set_activatable(False)
        rules_card.add(self.applications_row)

        self.domains_row = Adw.ActionRow.new()
        self.domains_row.set_title(plain(_("Domains and IP Addresses")))
        self.domains_row.set_activatable(False)
        rules_card.add(self.domains_row)

        # Outcome card. This is the part that tells the user the truth before
        # they commit: what the rules will do, and anything that will stop them
        # working. It is built by the routing manager's `plan`, which performs
        # no privileged operation, so nothing here has been applied yet and
        # nothing here has cost a password prompt.
        self.outcome_card = Adw.PreferencesGroup.new()
        self.outcome_card.set_title(plain(_("What This Will Do")))
        self.outcome_card.set_description(
            plain(_("Calculated from your current system routing table, before anything is changed"))
        )
        status_content.append(self.outcome_card)

        self.outcome_row = Adw.ActionRow.new()
        self.outcome_row.set_title(plain(_("Result")))
        self.outcome_row.set_activatable(False)
        self.outcome_row.set_subtitle_selectable(True)
        self.outcome_card.add(self.outcome_row)

        # One row per problem, because a warning that is a single run-on line
        # gets skimmed and then forgotten, and these are the sentences that
        # decide whether the policy does what the user expects.
        self.warning_rows: list[Adw.ActionRow] = []
        self.warning_group = Adw.PreferencesGroup.new()
        self.warning_group.set_title(plain(_("Things To Know")))
        self.warning_group.set_description(
            plain(_("These do not stop the rules from being installed, but they decide what the rules can actually do"))
        )
        self.warning_group.set_visible(False)
        status_content.append(self.warning_group)

        # Actions - Apply, Remove and Reset. Remove undoes live system state
        # and is styled destructive; Reset only clears the form, so it is not.
        actions_card = Adw.PreferencesGroup.new()
        actions_card.set_title(plain(_("Actions")))
        status_content.append(actions_card)

        self.action_bar = ActionBar()
        actions_card.add(self.action_bar)

        # Reset to defaults button
        self.reset_button = Gtk.Button.new_with_label(_("Reset to Defaults"))
        self.reset_button.set_icon_name("edit-undo-symbolic")
        self.reset_button.set_halign(Gtk.Align.CENTER)
        self.reset_button.add_css_class("pill")
        self.reset_button.set_tooltip_text(_("Clear the form and restore the initial settings"))
        self.reset_button.connect("clicked", self._on_reset_defaults)
        actions_card.add(self.reset_button)

    def _wrap(self, child: Gtk.Widget, *, scroll: bool = True) -> Gtk.Widget:
        """Put a section's content in a scrolled window inside a clamped box."""
        if scroll:
            scroller = Gtk.ScrolledWindow.new()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            scroller.set_child(child)
            inner = scroller
        else:
            inner = child
            inner.set_vexpand(True)

        clamp = Adw.Clamp.new()
        clamp.set_maximum_size(720)
        clamp.set_tightening_threshold(560)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        clamp.set_child(inner)
        return clamp

    def _on_nav_row_selected(self, listbox, row) -> None:
        """Handle sidebar navigation selection."""
        if row is None:
            return
        page_id = getattr(row, 'page_id', None)
        if not page_id:
            return
        self.content_stack.set_visible_child_name(page_id)
        self._sync_header_title(page_id)
        self._update_status_page()

    def _sync_header_title(self, page_id: str) -> None:
        """Point the shared header bar at the section that is now visible."""
        for nav_id, title, subtitle in self.PAGE_TITLES:
            if nav_id == page_id:
                self.content_header.set_title_widget(
                    Adw.WindowTitle.new(title(), subtitle())
                )
                return

    def _create_menu_actions(self) -> None:
        """Create menu actions."""
        app = self.get_application()

        prefs_action = Gio.SimpleAction.new("preferences", None)
        prefs_action.connect("activate", self._on_preferences)
        app.add_action(prefs_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        app.add_action(about_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: app.quit())
        app.add_action(quit_action)

    def _connect_signals(self) -> None:
        """Connect signals."""
        self.vpn_selector.connect("vpn-selected", self._on_vpn_selected)
        self.vpn_selector.connect("refresh-requested", self._on_refresh_vpn)
        self.mode_selector.connect("mode-changed", self._on_mode_changed)
        self.app_selector.connect("selection-changed", self._on_app_selection_changed)
        self.app_selector.connect("app-launched", self._on_app_launched)
        self.domain_editor.connect("domains-changed", self._on_domains_changed)
        self.action_bar.connect("apply-requested", self._on_apply_rules)
        self.action_bar.connect("remove-requested", self._on_remove_rules)

    def _load_initial_data(self) -> None:
        """Load initial data."""
        apps = self._app_manager.get_all_applications()
        self.app_selector.set_applications(apps)
        self.app_selector.set_skipped(self._app_manager.skipped_files())

        self._restore_policy()
        self._update_ui_state()

        # Detect VPNs. Runs in a worker thread so the window paints immediately;
        # stale cached results are never shown - a VPN that just disconnected must
        # disappear right away.
        self._refresh_vpn_list()

    def _refresh_vpn_list(self) -> None:
        """Refresh VPN detection on a worker thread (detection shells out to ip/systemctl)."""
        self.vpn_selector.set_loading(True)
        GLib.Thread.new("vpn-detect", self._do_vpn_detection, None)

    def _do_vpn_detection(self, _data=None) -> bool:
        """Perform VPN detection. Runs on a worker thread (scheduled by _refresh_vpn_list)."""
        connections = self._vpn_manager.detect_all()
        GLib.idle_add(self._on_vpn_detection_done, connections)
        return False

    def _on_vpn_detection_done(self, connections) -> bool:
        """Apply detection results on the main thread."""
        # A VPN that vanished must not stay selected. Compare by interface, not
        # by object: the same tunnel can be reported by different detectors
        # across scans, and dataclass equality would then call it "different".
        if self._selected_vpn is not None and not any(
            c.interface == self._selected_vpn.interface for c in connections
        ):
            self._selected_vpn = None

        self.vpn_selector.set_connections(connections)
        self.vpn_selector.set_loading(False)

        # With exactly one tunnel up there is no choice to make, so target it.
        if len(connections) == 1 and self._selected_vpn is None:
            self._select_vpn(connections[0])

        self.vpn_selector.set_selected(self._selected_vpn)
        self._update_ui_state()
        return False

    def _restore_policy(self) -> None:
        """Restore policy from config."""
        config = self._config.policy
        mode = PolicyMode.INCLUDE if config.mode == "include" else PolicyMode.EXCLUDE
        self.mode_selector.set_mode(mode)

        if config.selected_apps:
            self.app_selector.set_selected_apps(config.selected_apps)

        if config.selected_domains:
            self.domain_editor.set_domains(config.selected_domains)
        if config.selected_ips:
            self.domain_editor.set_ips(config.selected_ips)

        self._update_policy_from_ui()

    def _update_policy_from_ui(self) -> None:
        """Update internal policy from UI state."""
        self._policy.mode = self.mode_selector.get_mode()
        self._policy.rules = []

        rule = RoutingRule(
            app_ids=self.app_selector.get_selected_apps(),
            domains=self.domain_editor.get_domains(),
            ip_cidrs=self.domain_editor.get_ips(),
        )
        if rule.app_ids or rule.domains or rule.ip_cidrs:
            self._policy.rules.append(rule)

    def _update_ui_state(self) -> None:
        """Update UI based on current state."""
        has_vpn = self._selected_vpn is not None
        has_selection = bool(self._policy.rules and (
            self._policy.rules[0].app_ids or
            self._policy.rules[0].domains or
            self._policy.rules[0].ip_cidrs
        ))

        self.action_bar.set_apply_sensitive(has_vpn and has_selection and not self._is_applying)
        self.action_bar.set_remove_sensitive(self._routing_manager.get_state().is_active)

        # Update status
        state = self._routing_manager.get_state()
        if state.is_active:
            self.action_bar.set_status("active", _("Status: Active"))
            self.action_bar.set_details(f"Applied to {state.vpn_interface}")
        elif self._is_applying:
            self.action_bar.set_status("applying", _("Status: Applying..."))
        elif state.error:
            self.action_bar.set_status("error", _("Status: Error"))
            self.action_bar.set_details(state.error)
        else:
            self.action_bar.set_status("ready", _("Status: Ready"))
            if self._selected_vpn:
                self.action_bar.set_details(f"Ready to apply to {self._selected_vpn.interface}")
            else:
                self.action_bar.set_details(_("No VPN selected"))

        self._update_status_page()

    def _update_status_page(self) -> None:
        """Fill in the 'Current Rules' card.

        The status line itself is the ActionBar's job, done in _update_ui_state,
        so this only describes what the current selection would apply.
        """
        rule = self._policy.rules[0] if self._policy.rules else None

        # Which VPN the rules will target.
        if self._selected_vpn is not None:
            self.target_row.set_subtitle(
                plain(f"{self._selected_vpn.client_name} ({self._selected_vpn.interface})")
            )
        else:
            self.target_row.set_subtitle(plain(_("None selected")))
        self.target_row.set_subtitle_selectable(True)

        # Mode, spelled out so the difference is unambiguous.
        if self._policy.mode == PolicyMode.INCLUDE:
            self.mode_summary_row.set_subtitle(
                plain(_("Only the selected apps and addresses go through the VPN"))
            )
        else:
            self.mode_summary_row.set_subtitle(
                plain(_("Everything goes through the VPN except the selected apps and addresses"))
            )

        # What is selected, as a count, so the row fits at any selection size.
        if rule is None or not rule.app_ids:
            self.applications_row.set_subtitle(plain(_("All system traffic")))
        elif "__all_system__" in rule.app_ids:
            self.applications_row.set_subtitle(plain(_("All system traffic")))
        else:
            self.applications_row.set_subtitle(
                plain(ngettext(
                    "%d application", "%d applications", len(rule.app_ids)
                ))
            )
            self.applications_row.set_subtitle_selectable(True)

        if rule is None or (not rule.domains and not rule.ip_cidrs):
            self.domains_row.set_subtitle(plain(_("None")))
        else:
            bits: list[str] = []
            if rule.domains:
                bits.append(
                    ngettext("%d domain", "%d domains", len(rule.domains))
                )
            if rule.ip_cidrs:
                bits.append(
                    ngettext("%d IP range", "%d IP ranges", len(rule.ip_cidrs))
                )
            self.domains_row.set_subtitle(plain(", ".join(bits)))
            self.domains_row.set_subtitle_selectable(True)

        self._update_outcome()

    def _update_outcome(self) -> None:
        """Show what the current selection will actually do.

        The plan is computed by the routing manager without touching anything
        and without asking for a password, so this is a real answer rather
        than a restatement of the form. When there is no VPN to target there is
        nothing to plan against, and that is said instead of guessing.
        """
        if self._selected_vpn is None:
            self.outcome_row.set_subtitle(
                plain(_("No VPN selected, so there is nothing to calculate yet."))
            )
            self._set_warning_rows([])
            return

        conn = self._selected_vpn
        plan = self._routing_manager.plan(
            self._policy,
            conn.interface,
            conn.peer_ip or conn.gateway_ip,
        )
        self.outcome_row.set_subtitle(plain(plan.describe()))

        # How many of the selected applications are actually reachable by a
        # rule. This is the number that decides whether the per-app half of the
        # policy exists, and it is worth saying out loud because it can be
        # zero while everything else in the policy looks correct.
        summary = plan.describe()
        if plan.app_ids:
            summary += " " + _(
                "{total} of those are applications, and {running} of them are "
                "running where the rules can tell their traffic apart."
            ).format(total=len(plan.app_ids), running=len(plan.matchable_apps))
        self.outcome_row.set_subtitle(plain(summary))
        self._set_warning_rows(list(plan.warnings))

    def _set_warning_rows(self, warnings: list[str]) -> None:
        """Rebuild the warning list.

        Rebuilt rather than updated in place so a warning that has gone away
        disappears, which matters because these are read once and then acted
        on: a stale warning is worse than none.
        """
        for row in self.warning_rows:
            self.warning_group.remove(row)
        self.warning_rows = []

        if not warnings:
            self.warning_group.set_visible(False)
            return

        for text in warnings:
            row = Adw.ActionRow.new()
            row.set_title(plain(_("Note")))
            row.set_subtitle(plain(text))
            row.set_subtitle_selectable(True)
            row.set_subtitle_lines(0)
            row.set_activatable(False)
            row.add_prefix(
                Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            )
            self.warning_group.add(row)
            self.warning_rows.append(row)
        self.warning_group.set_visible(True)

    def _set_error(self, message: str) -> None:
        """Show a message that the user must act on, as a warning row.

        A toast disappears; this does not. Anything that leaves the policy
        unable to affect what the user asked for belongs here.
        """
        self._set_warning_rows([message])

    # Signal handlers
    def _on_vpn_selected(self, selector, connection) -> None:
        """The user picked a connection on the VPN page."""
        self._select_vpn(connection)

    def _select_vpn(self, connection) -> None:
        """Record the connection the rules will target."""
        self._selected_vpn = connection
        self.vpn_selector.set_selected(connection)
        self._update_ui_state()

    def _on_refresh_vpn(self, selector) -> None:
        # The refresh button says "rescan", and a user who just installed an
        # application is reaching for exactly that button. The application list
        # is a singleton scan, so without a reload here a newly installed
        # application would never appear no matter how many times the user
        # pressed it -- which reads as the application being broken.
        self._reload_applications()
        self._refresh_vpn_list()

    def _reload_applications(self) -> None:
        """Re-read the installed applications and rebuild the list.

        On a worker thread, because it is a filesystem walk over every XDG data
        directory. A selection that no longer exists is dropped rather than
        silently kept: a rule naming an application the user has uninstalled
        cannot match anything, and leaving it selected would let the user apply
        a policy that does less than it says.
        """
        GLib.Thread.new("apps-reload", self._do_reload_applications, None)

    def _do_reload_applications(self, _data=None) -> bool:
        apps = self._app_manager.reload_apps()
        GLib.idle_add(self._on_applications_reloaded, apps)
        return False

    def _on_applications_reloaded(self, apps) -> bool:
        known = {a.desktop_id for a in apps}
        still_valid = [i for i in self.app_selector.get_selected_apps() if i in known]

        self.app_selector.set_applications(apps)
        self.app_selector.set_skipped(self._app_manager.skipped_files())
        if still_valid != self.app_selector.get_selected_apps():
            self.app_selector.set_selected_apps(still_valid)
            self._update_policy_from_ui()
            self._config.policy.selected_apps = still_valid
            self._config.save()

        # The plan depends on which applications exist and are running, so the
        # preview has to be recomputed rather than left describing the old list.
        self._update_ui_state()
        return False

    def _on_mode_changed(self, selector, mode: PolicyMode) -> None:
        self._policy.mode = mode
        self._update_ui_state()

    def _on_app_selection_changed(self, selector, app_ids: list[str]) -> None:
        self._update_policy_from_ui()
        self._config.policy.selected_apps = app_ids
        self._config.save()
        self._update_ui_state()

    def _on_app_launched(self, selector, ok: bool, message: str) -> None:
        """Report the outcome of starting an application inside the slice."""
        if ok:
            # A Toast title is a markup label too, and this message carries the
            # application's name from its .desktop file.
            toast = Adw.Toast.new(plain(message))
            toast.set_timeout(4)
            self._toast_overlay.add_toast(toast)
            # Matchability changed, so what the policy can actually affect
            # changed, and the preview has to say so.
            self._update_ui_state()
        else:
            # A launch failure is not cosmetic: the application will not be in
            # the policy's reach, so it is reported as an error banner rather
            # than a passing toast that disappears.
            self._set_error(message)
            self._update_ui_state()

    def _on_domains_changed(self, editor, domains: list[str], ips: list[str]) -> None:
        self._update_policy_from_ui()
        self._config.policy.selected_domains = domains
        self._config.policy.selected_ips = ips
        self._config.save()
        self._update_ui_state()

    def _on_apply_rules(self, bar) -> None:
        """Apply split tunneling rules."""
        if not self._selected_vpn or self._is_applying:
            return

        self._is_applying = True
        self._update_ui_state()

        GLib.Thread.new("apply-rules", self._do_apply_rules, None)

    def _do_apply_rules(self, _) -> None:
        """Apply rules in background thread."""
        try:
            self._update_policy_from_ui()
            self._config.policy.mode = self._policy.mode.value
            self._config.save()

            # The tunnel's far end comes from what the detector read out of
            # the kernel, never from a configured value, so the route points
            # at the address the VPN client actually set up.
            peer = self._selected_vpn.peer_ip or self._selected_vpn.gateway_ip
            state = self._routing_manager.apply_policy(
                self._policy, self._selected_vpn.interface, peer
            )
            GLib.idle_add(self._on_apply_complete, state)
        except Exception as e:
            GLib.idle_add(self._on_apply_error, str(e))

    def _on_apply_complete(self, state) -> None:
        """Report the outcome, including anything that will not work."""
        self._is_applying = False
        self._update_ui_state()

        if state.warnings:
            # A policy that is installed but shadowed by the VPN client's own
            # rules is not a success, and saying so is the whole point of
            # collecting the warnings.
            self._show_notification(
                _("Rules applied with warnings"),
                "\n\n".join(state.warnings),
            )
        else:
            self._show_notification(_("Rules applied successfully"), "")

    def _on_apply_error(self, error: str) -> None:
        self._is_applying = False
        self._update_ui_state()
        self._show_notification(_("Failed to apply rules"), error)

    def _on_remove_rules(self, bar) -> None:
        """Remove split tunneling rules."""
        self._is_applying = True
        self._update_ui_state()

        GLib.Thread.new("remove-rules", self._do_remove_rules, None)

    def _do_remove_rules(self, _) -> None:
        try:
            state = self._routing_manager.remove_policy()
            GLib.idle_add(self._on_remove_complete, state)
        except Exception as e:
            GLib.idle_add(self._on_apply_error, str(e))

    def _on_remove_complete(self, state) -> None:
        self._is_applying = False
        self._update_ui_state()
        self._show_notification(_("Rules removed successfully"), "")

    def _on_reset_defaults(self, button) -> None:
        """Reset all settings to defaults."""
        dialog = Adw.MessageDialog.new(
            self,
            _("Reset to Defaults"),
            _("Are you sure you want to reset all settings to defaults? This will clear all selected apps, domains, IPs, and mode.")
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Reset"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_confirmed)
        dialog.present()

    def _on_reset_confirmed(self, dialog, response: str) -> None:
        if response == "ok":
            # Reset config
            self._config.policy.mode = "include"
            self._config.policy.selected_apps = ["__all_system__"]
            self._config.policy.selected_domains = []
            self._config.policy.selected_ips = []
            self._config.save()

            # Reset UI
            self.mode_selector.set_mode(PolicyMode.INCLUDE)
            self.app_selector.set_selected_apps(["__all_system__"])
            self.domain_editor.set_domains([])
            self.domain_editor.set_ips([])
            
            # Remove any active rules
            if self._routing_manager.get_state().is_active:
                self._routing_manager.remove_policy()
            
            self._update_ui_state()
            self._show_notification(_("Settings reset to defaults"), "")

    def _show_notification(self, title: str, body: str) -> None:
        """Show desktop notification."""
        if self._config.policy.apply_to_all_users:
            from gi.repository import Notify
            if not Notify.is_initted():
                Notify.init("VPN Split Tunnel")
            notification = Notify.Notification.new(title, body, "com.github.sina.vpn-split-tunnel")
            notification.show()

    def _on_preferences(self, *_) -> None:
        dialog = PreferencesDialog(parent=self)
        dialog.present()

    def _on_about(self, *_) -> None:
        dialog = create_about_dialog(parent=self)
        dialog.present()