"""Application selector: which applications, and how they get started.

Picking an application is only half of per-application split tunneling. The
other half is that the application has to be *running inside a cgroup this app
controls*, because that cgroup is the only thing the kernel lets nftables match
a packet's originating socket by, and a process can only enter a cgroup when it
starts.

So each row carries a start button, and the row's state distinguishes three
things that look identical otherwise:

* selected, not started  - the policy cannot match this application yet
* selected, started      - its traffic is matchable
* not selected           - it is not part of the policy at all

Without that distinction the UI would let a user select an application, apply,
and be told it succeeded while nothing about that application's traffic had
changed.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from vpn_split_tunnel.core.app_selector import Application, get_application_manager
from vpn_split_tunnel.core.routing.launcher import LaunchError, launch, unit_name_for
from vpn_split_tunnel.utils.config import get_config
from vpn_split_tunnel.utils.i18n import _
from vpn_split_tunnel.ui.text import plain


class AppSelector(Adw.PreferencesPage):
    """Widget for selecting applications."""

    __gtype_name__ = "AppSelector"

    __gsignals__ = {
        # Emitted with the current list of desktop ids. The window rebuilds
        # the routing plan from it, so a change here changes the preview.
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Emitted after a launch attempt, with a (ok, message) pair, so the
        # window can show a toast. A launch is not a selection change, but it
        # does change whether the policy can match anything.
        "app-launched": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._all_apps: list[Application] = []
        self._filtered_apps: list[Application] = []
        self._selected_apps: set[str] = set()
        self._all_system_selected = True  # Default to True (All System selected)
        # desktop ids currently running inside our slice, discovered rather
        # than remembered: an application the user closed and reopened from
        # their own launcher is no longer in the slice, and a remembered set
        # would claim otherwise.
        self._running: set[str] = set()
        self._build_ui()
        self.refresh_running()

    def _build_ui(self) -> None:
        """Build UI programmatically."""
        # A notice that stays until it is no longer true, rather than a toast
        # that disappears. It sits above everything else so it cannot be
        # scrolled past: it says that a launch did not achieve what the button
        # implied, and a user who missed that would apply a policy expecting
        # per-application control and not get it.
        self._notice = ""
        self._notice_group = Adw.PreferencesGroup.new()
        self._notice_group.set_visible(False)
        self._notice_row = Adw.ActionRow.new()
        self._notice_row.set_activatable(False)
        self._notice_row.set_title_selectable(False)
        self._notice_row.set_subtitle_lines(0)
        self._notice_row.set_subtitle_selectable(True)
        self._notice_row.add_prefix(
            Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        )
        self._notice_group.add(self._notice_row)
        self.add(self._notice_group)

        # Unreadable desktop entries. The scan silently skips files it cannot
        # parse (a broken ~/.local/share/applications/foo.desktop hides foo from
        # the list), and a silently shorter list is a support question, so the
        # reasons are surfaced here instead of being hidden.
        self._skipped: list[str] = []
        self._skipped_rows: list[Gtk.Widget] = []
        self._skipped_group = Adw.PreferencesGroup.new()
        self._skipped_group.set_visible(False)
        self._skipped_row = Adw.ActionRow.new()
        self._skipped_row.set_activatable(False)
        self._skipped_row.add_prefix(
            Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        )
        self.add(self._skipped_group)

        group = Adw.PreferencesGroup.new()
        group.set_title(plain(_("Installed Applications")))
        group.set_description(
            plain(_(
                "Traffic can only be told apart per application for applications "
                "running in this app's own group. Use the play button to start "
                "one that way; an application started any other way cannot be "
                "separated from the rest of the system."
            ))
        )
        self.add(group)

        # All System row - checked by default
        self.all_system_row = Adw.ActionRow.new()
        self.all_system_row.set_title(plain(_("All System (apply to all traffic)")))
        self.all_system_row.set_subtitle(plain(_("Apply split tunneling to all system traffic")))
        self.all_system_row.set_activatable(True)
        group.add(self.all_system_row)

        self.all_system_check = Gtk.CheckButton.new()
        self.all_system_check.set_active(True)  # Default checked
        self.all_system_check.set_valign(Gtk.Align.CENTER)
        self.all_system_check.set_tooltip_text(
            _("Apply the split tunnel to all system traffic")
        )
        self.all_system_row.add_prefix(self.all_system_check)
        self.all_system_check.connect("toggled", self._on_all_system_toggled)

        # Search row
        search_row = Adw.ActionRow.new()
        search_row.set_title(plain(_("Search")))
        group.add(search_row)

        self.search_entry = Gtk.SearchEntry.new()
        self.search_entry.set_placeholder_text(_("Search applications..."))
        self.search_entry.set_hexpand(True)
        self.search_entry.set_width_chars(30)
        search_row.add_suffix(self.search_entry)
        self.search_entry.connect("search-changed", self._on_search_changed)

        # Scrolled window for apps list
        self.apps_scrolled = Gtk.ScrolledWindow.new()
        self.apps_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.apps_scrolled.set_min_content_height(300)
        self.apps_scrolled.set_max_content_height(400)
        group.add(self.apps_scrolled)

        self.apps_listbox = Gtk.ListBox.new()
        self.apps_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.apps_scrolled.set_child(self.apps_listbox)

        # Selected count row
        self.selected_count_row = Adw.ActionRow.new()
        self.selected_count_row.set_title(plain(_("Selected")))
        self.selected_count_row.set_subtitle(plain(_("All system traffic (default)")))
        self.selected_count_row.set_activatable(False)
        group.add(self.selected_count_row)

    def set_applications(self, apps: list[Application]) -> None:
        """Set the list of applications."""
        self._all_apps = apps
        self._filtered_apps = apps.copy()
        self._populate_list()

    def set_skipped(self, skipped: list[str]) -> None:
        """Show which desktop entries could not be read, if any.

        The scan collects one reason string per unreadable entry; they are
        shown one per row so the list stays readable, and the whole group is
        hidden when there is nothing to explain. Rows are tracked explicitly:
        AdwPreferencesGroup wraps its rows in an internal box, so walking
        ``get_first_child()`` would reach widgets that are not children.
        """
        for row in self._skipped_rows:
            self._skipped_group.remove(row)
        self._skipped_rows.clear()

        self._skipped = list(skipped)
        if not self._skipped:
            self._skipped_group.set_visible(False)
            return

        count = len(self._skipped)
        self._skipped_row.set_title(
            plain(_("{count} application entry could not be read")
                  .format(count=count))
            if count == 1
            else plain(_("{count} application entries could not be read")
                       .format(count=count))
        )
        self._skipped_row.set_subtitle(
            plain(_("These files are not listed below, so they cannot be "
                    "selected for split tunneling"))
        )
        self._skipped_group.add(self._skipped_row)
        self._skipped_rows.append(self._skipped_row)

        for reason in self._skipped:
            row = Adw.ActionRow.new()
            row.set_activatable(False)
            row.set_title(plain(reason))
            row.set_subtitle(plain(_("Unreadable entry")))
            self._skipped_group.add(row)
            self._skipped_rows.append(row)

        self._skipped_group.set_visible(True)

    def set_selected_apps(self, app_ids: list[str]) -> None:
        """Set selected applications, and tell the window.

        The signal is emitted here rather than left to the caller. This setter
        changes the selection, and everything downstream -- the policy, the
        routing plan, the outcome card, the saved config -- is derived from it,
        so a caller that set the selection without an event would leave all of
        them describing the previous selection. That is not a visible error: the
        rows would show the new check marks while the preview kept promising
        the old set would be applied.
        """
        self._selected_apps = set(app_ids)
        self._all_system_selected = "__all_system__" in self._selected_apps
        self.all_system_check.set_active(self._all_system_selected)
        # The running set is about the selection, so changing the selection has
        # to recompute it. It did not, which left the count row saying "none
        # started yet" while the status page on the other tab said the same
        # application was running where the rules can see it -- both correct
        # about different moments, both on screen at once.
        self.refresh_running()
        self.emit("selection-changed", self.get_selected_apps())

    def get_selected_apps(self) -> list[str]:
        """Get selected application IDs."""
        if self._all_system_selected:
            return ["__all_system__"]
        return list(self._selected_apps)

    def refresh_running(self) -> None:
        """Re-read which selected applications are running in our slice.

        Called after a rescan and after a launch. The answer comes from cgroupfs
        rather than from what this widget did, so closing an application behind
        our back is reflected.

        This asks the same question the routing plan asks, in the same way, and
        that is the point. The two used to disagree -- the list compared unit
        names while the plan looked for live processes, and the name comparison
        was matching `app-alacarte` against `app-alacarte.service`, so it never
        matched anything. The plan was right and the list was empty, on the same
        screen, at the same moment.
        """
        from vpn_split_tunnel.core.routing.launcher import (
            cgroup_has_processes,
            cgroup_path_for_unit,
        )

        self._running = {
            app_id
            for app_id in self._selected_apps
            if self._is_running_in_slice(app_id, cgroup_path_for_unit, cgroup_has_processes)
        }
        # The count text is derived from `_running`, so it has to be rebuilt in
        # the same breath. It was not, so the row said "none started yet" for an
        # application that was running, while the status page said the opposite.
        self._update_selection_ui()
        self._populate_list()

    @staticmethod
    def _is_running_in_slice(
        app_id: str, path_for_unit, has_processes
    ) -> bool:
        """Whether this application has live processes where our rules can see them."""
        path = path_for_unit(unit_name_for(app_id))
        return bool(path) and has_processes(path)

    def _populate_list(self) -> None:
        """Populate the listbox with applications."""
        # Clear existing
        child = self.apps_listbox.get_first_child()
        while child:
            self.apps_listbox.remove(child)
            child = self.apps_listbox.get_first_child()

        # Add apps
        for app in self._filtered_apps:
            row = self._create_app_row(app)
            self.apps_listbox.append(row)

    def _create_app_row(self, app: Application) -> Adw.ActionRow:
        """Create a list row for an application."""
        row = Adw.ActionRow.new()
        row.set_title(plain(app.get_display_name()))
        row.set_subtitle(plain(app.desktop_id))
        row.set_activatable(True)

        # Icon
        if app.icon_name:
            icon = Gtk.Image.new_from_icon_name(app.icon_name)
            icon.set_pixel_size(24)
            icon.add_css_class("app-icon")
            row.add_prefix(icon)

        # Selection check
        check = Gtk.CheckButton.new()
        check.set_valign(Gtk.Align.CENTER)
        check.set_tooltip_text(_("Select this application for the split tunnel"))
        is_selected = app.desktop_id in self._selected_apps
        check.set_active(is_selected)
        check.connect("toggled", self._on_app_toggled, app.desktop_id)
        row.add_suffix(check)

        # Start button. Its state is the whole point of this row: a policy
        # entry that names an application which is not running matches
        # nothing, and the user cannot tell that from a row that looks
        # selected.
        is_running = app.desktop_id in self._running
        button = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        button.set_valign(Gtk.Align.CENTER)
        button.add_css_class("flat")
        if is_running:
            button.set_sensitive(False)
            button.set_icon_name("emblem-ok-symbolic")
            button.set_tooltip_text(
                _("This application is running where the split tunnel can see it")
            )
            row.add_css_class("app-running")
        else:
            button.set_tooltip_text(
                _("Start this application so its traffic can be told apart")
            )
            button.connect("clicked", self._on_start_clicked, app.desktop_id)
        row.add_suffix(button)

        # GObject data access is unsupported from PyGObject, so association
        # with the app is a plain Python attribute.
        row.app_id = app.desktop_id

        return row

    def _on_all_system_toggled(self, check) -> None:
        """Handle 'All System' toggle."""
        self._all_system_selected = check.get_active()
        if self._all_system_selected:
            # Disable individual selections
            self.apps_listbox.set_sensitive(False)
            self.search_entry.set_sensitive(False)
            self._selected_apps.clear()
            self._running.clear()
        else:
            self.apps_listbox.set_sensitive(True)
            self.search_entry.set_sensitive(True)
        self._update_selection_ui()
        self._populate_list()
        self.emit("selection-changed", self.get_selected_apps())

    def _on_app_toggled(self, check, app_id: str) -> None:
        """Handle individual app toggle."""
        if check.get_active():
            self._selected_apps.add(app_id)
        else:
            self._selected_apps.discard(app_id)
        self._update_selection_ui()
        self._populate_list()
        self.emit("selection-changed", self.get_selected_apps())

    def _on_start_clicked(self, button, app_id: str) -> None:
        """Start an application inside the slice so its traffic is matchable."""
        # The previous notice described a different application, and leaving it
        # up would attach this launch's outcome to the wrong row.
        self.clear_notice()
        app = get_application_manager().get_application(app_id)
        name = app.get_display_name() if app else app_id
        slice_name = get_config().routing.cgroup_slice

        # The launch shells out to systemd-run and can take a moment, so it
        # runs off the main loop. Doing it inline would freeze the window and
        # the button would look dead.
        def work() -> None:
            try:
                launched = launch(app_id, slice_name)
            except LaunchError as exc:
                GLib.idle_add(
                    self.emit, "app-launched", False, f"{name}: {exc}"
                )
                return
            GLib.idle_add(
                self.emit,
                "app-launched",
                True,
                _("{name} is running where the split tunnel can see it.").format(
                    name=name
                ),
            )
            GLib.idle_add(self.clear_notice)
            # Refresh from systemd rather than assuming success, so a unit
            # that started but did not land in the slice is not claimed.
            GLib.idle_add(self._after_launch, launched.cgroup_path, app_id)

        GLib.idle_add(self._set_busy, button, True)
        import threading

        threading.Thread(target=work, daemon=True).start()

    def _set_busy(self, button, busy: bool) -> bool:
        button.set_sensitive(not busy)
        if busy:
            button.add_css_class("busy")
        else:
            button.remove_css_class("busy")
        return GLib.SOURCE_REMOVE

    def _after_launch(self, cgroup_path: str, app_id: str) -> bool:
        from vpn_split_tunnel.core.routing.launcher import cgroup_path_for_unit

        # The set is re-derived from systemd rather than updated with this one
        # application. Accumulating it here is how a launch of an application
        # that is not even part of the policy ended up counted as "started",
        # which contradicted the preview on the status page in the same window.
        # Whatever systemd reports is the truth about where processes are, and
        # `refresh_running` already intersects that with the selection.
        landed = cgroup_path_for_unit(unit_name_for(app_id)) == cgroup_path
        if not landed:
            # systemd started something that is not in the slice, so it cannot
            # be matched. Said plainly rather than shown as success.
            self._set_notice(
                _("{name} started, but it is not in the split group, so its "
                  "traffic cannot be told apart from the rest of the system.").format(
                    name=self._display_name(app_id)
                )
            )
        self.refresh_running()
        # The policy's matchability changed, so the preview has to be rebuilt.
        self.emit("selection-changed", self.get_selected_apps())
        return GLib.SOURCE_REMOVE

    def _display_name(self, app_id: str) -> str:
        app = get_application_manager().get_application(app_id)
        return app.get_display_name() if app is not None else app_id

    def _set_notice(self, message: str) -> None:
        """Report something the user must act on.

        A notice rather than a toast: it changes what the policy can do, and it
        stays true after the message has gone.
        """
        self._notice = message
        self._notice_row.set_subtitle(plain(message))
        self._notice_group.set_visible(bool(message))

    def clear_notice(self) -> None:
        self._set_notice("")

    def _on_search_changed(self, entry) -> None:
        """Handle search text change."""
        query = entry.get_text().strip()
        if not query:
            self._filtered_apps = self._all_apps.copy()
        else:
            self._filtered_apps = [app for app in self._all_apps if app.matches_search(query)]
        self._populate_list()

    def _update_selection_ui(self) -> None:
        """Update selection count display."""
        if self._all_system_selected:
            self.selected_count_row.set_subtitle(plain(_("All system traffic (default)")))
            return

        selected = len(self._selected_apps)
        if selected == 0:
            self.selected_count_row.set_subtitle(plain(_("No applications selected")))
            return

        # Both numbers are shown, because the gap between them is the thing
        # that decides whether the policy does anything: six applications
        # selected and zero started means six entries that match no traffic.
        started = len(self._running)
        count_text = (
            _("1 application selected")
            if selected == 1
            else _("{count} applications selected").format(count=selected)
        )
        if started == 0:
            self.selected_count_row.set_subtitle(
                plain(_("{selection} · none started yet, so none can be matched").format(
                    selection=count_text
                ))
            )
        else:
            self.selected_count_row.set_subtitle(
                plain(_("{selection} · {started} started").format(
                    selection=count_text, started=started
                ))
            )


GObject.type_register(AppSelector)
