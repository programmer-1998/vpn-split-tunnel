"""Application Selector - Get installed applications from .desktop files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

if TYPE_CHECKING:
    from vpn_split_tunnel.utils.i18n import _


@dataclass
class Application:
    """Represents an installed application."""
    desktop_id: str          # e.g., "org.mozilla.firefox"
    name: str                # Display name
    generic_name: str | None # Generic name
    comment: str | None      # Description
    icon_name: str | None    # Icon name
    executable: str | None   # Executable name
    categories: list[str]    # Categories
    no_display: bool         # Hidden from menus
    terminal: bool           # Runs in terminal

    def get_display_name(self) -> str:
        """Get best display name."""
        if self.generic_name and self.name != self.generic_name:
            return f"{self.name} ({self.generic_name})"
        return self.name

    def matches_search(self, query: str) -> bool:
        """Check if app matches search query."""
        query = query.lower()
        return (
            query in self.name.lower() or
            query in self.desktop_id.lower() or
            (self.generic_name and query in self.generic_name.lower()) or
            (self.comment and query in self.comment.lower()) or
            (self.executable and query in self.executable.lower())
        )


class ApplicationManager:
    """The installed applications, read from the desktop entries on disk.

    One shared instance, because scanning every desktop file on the system is
    slow enough that doing it per caller would be noticeable, and because the
    list does not change during a single operation.

    It is a singleton but not a one-shot: `reload()` re-reads the disk, which
    is what a long-running window needs when the user installs something while
    the application is open. Without it the list is frozen at first use and
    the newly installed application never appears at all.
    """

    _instance: ApplicationManager | None = None

    def __new__(cls) -> ApplicationManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._apps: list[Application] = []
        self._skipped: list[str] = []
        self._load_applications()

    @classmethod
    def reset_singleton(cls) -> None:
        """Drop the shared instance.

        For tests, and for anything that must start from a clean state.
        Production code calls `reload` instead, which keeps the identity of
        the object that other code is holding a reference to.
        """
        cls._instance = None

    def reload(self) -> int:
        """Re-read the desktop entries, returning the number of applications.

        This is a filesystem walk over every XDG data directory, so a window
        should call it from a worker thread rather than the main loop.
        """
        self._load_applications()
        return len(self._apps)

    def reload_apps(self) -> list[Application]:
        """Re-read the desktop entries and return the new list.

        Same work as `reload`, but hands back the list instead of a count,
        because the caller's next step is always to show it. Returns a copy:
        the shared instance's own list is replaced on the next reload, and a
        caller holding a reference across that would see it change underneath.
        """
        self._load_applications()
        return list(self._apps)

    def _load_applications(self) -> None:
        """Load all applications from .desktop files.

        Every getter used here is a real GIO method. That matters more than it
        looks: this used to be wrapped in a bare `except Exception: pass` on
        the theory that malformed desktop files are common, and in practice
        one wrong method name raised for every file and the application list
        came out empty. The list being empty is indistinguishable from a system
        with no applications, so the failure was invisible. Malformed files are
        still skipped, but only when GIO says they are unusable, and anything
        else is recorded so it can be reported rather than lost.
        """
        self._apps = []
        self._skipped: list[str] = []

        data_dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
        seen_ids: set[str] = set()

        for data_dir in data_dirs:
            apps_dir = Path(data_dir) / "applications"
            if not apps_dir.is_dir():
                continue

            for desktop_file in sorted(apps_dir.glob("*.desktop")):
                app = self._read_desktop_file(desktop_file, seen_ids)
                if app is not None:
                    self._apps.append(app)

        self._apps.sort(key=lambda a: a.name.lower())

    def _read_desktop_file(
        self, desktop_file: Path, seen_ids: set[str]
    ) -> Application | None:
        """One desktop entry, or None if it should not be listed.

        Separated from the loop so a single bad file cannot take the rest of
        the scan with it, and so the reasons for skipping are collected.
        """
        # A later data directory must not override an earlier one, so the first
        # directory to provide an id wins. That is the XDG search order, and it
        # is what lets a user's own copy of an entry override the system's.
        try:
            app_info = Gio.DesktopAppInfo.new_from_filename(str(desktop_file))
        except Exception as exc:  # noqa: BLE001 - a bad file must not stop the scan
            self._skipped.append(f"{desktop_file.name}: {exc}")
            return None

        if app_info is None:
            self._skipped.append(f"{desktop_file.name}: GIO could not read it")
            return None

        desktop_id = app_info.get_id()
        if not desktop_id or desktop_id in seen_ids:
            return None
        seen_ids.add(desktop_id)

        # `get_nodisplay`, not `get_no_display`: the desktop entry key is
        # NoDisplay and GIO exposes it without the underscore. Calling the
        # non-existent spelling raises AttributeError, which is what emptied
        # the list before.
        if app_info.get_nodisplay():
            return None

        icon = app_info.get_icon()
        return Application(
            desktop_id=desktop_id,
            name=app_info.get_name() or desktop_id,
            generic_name=app_info.get_generic_name(),
            # GIO calls the Comment key's value the description.
            comment=app_info.get_description(),
            icon_name=icon.to_string() if icon is not None else None,
            executable=app_info.get_executable(),
            # `get_categories` returns the raw key value, which is a
            # semicolon-terminated string such as "Network;WebBrowser;".
            # Wrapping that in list() produced a list of single characters, so
            # every category lookup silently matched nothing. `get_string_list`
            # is the accessor that splits it.
            categories=list(app_info.get_string_list("Categories") or []),
            no_display=app_info.get_nodisplay(),
            # GIO has no accessor for the Terminal key, so it is read from the
            # file. It is only used to label the entry in the list.
            terminal=self._reads_terminal_key(desktop_file),
        )

    @staticmethod
    def _reads_terminal_key(desktop_file: Path) -> bool:
        """Whether a desktop entry declares `Terminal=true`.

        Read from the file rather than through GIO, which does not expose it.
        A failure to read the key is not worth reporting: the value only
        affects a subtitle.
        """
        try:
            text = desktop_file.read_text(errors="replace")
        except OSError:
            return False
        for line in text.splitlines():
            if line.strip().lower() == "terminal=true":
                return True
        return False

    def skipped_files(self) -> list[str]:
        """Entries that could not be read, for the user to be told about.

        Exposed because a silently shorter application list is a support
        question, and the reason belongs in the answer.
        """
        return list(self._skipped)

    def get_all_applications(self) -> list[Application]:
        """Get all applications."""
        return self._apps.copy()

    def get_applications_by_category(self, category: str) -> list[Application]:
        """Get applications in a specific category."""
        return [app for app in self._apps if category in app.categories]

    def search_applications(self, query: str) -> list[Application]:
        """Search applications by query."""
        if not query:
            return self._apps.copy()
        return [app for app in self._apps if app.matches_search(query)]

    def get_application(self, desktop_id: str) -> Application | None:
        """The application with this desktop id, or None.

        Accepts the id with or without the `.desktop` suffix. GIO reports ids
        with it, and that is the form the list and the saved config use, but the
        id without the suffix is the form a person would type and the form an
        older saved config may hold. Matching only one of them would mean a
        stale id silently matches nothing -- and an unmatched id looks exactly
        like an application that cannot be matched, so the user would be told
        the wrong thing.
        """
        wanted = desktop_id if desktop_id.endswith(".desktop") else f"{desktop_id}.desktop"
        for app in self._apps:
            if app.desktop_id == wanted:
                return app
        return None

    def refresh(self) -> None:
        """Reload applications."""
        self._load_applications()


def get_application_manager() -> ApplicationManager:
    """Get global application manager instance."""
    return ApplicationManager()