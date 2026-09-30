"""Tests for application selector."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from vpn_split_tunnel.core.app_selector import Application, ApplicationManager


class TestApplication:
    """Test Application dataclass."""

    def test_get_display_name(self) -> None:
        """Test display name generation."""
        app = Application(
            desktop_id="test.app",
            name="Test App",
            generic_name="Generic",
            comment=None,
            icon_name=None,
            executable=None,
            categories=[],
            no_display=False,
            terminal=False,
        )
        assert app.get_display_name() == "Test App (Generic)"

    def test_get_display_name_no_generic(self) -> None:
        """Test display name without generic name."""
        app = Application(
            desktop_id="test.app",
            name="Test App",
            generic_name=None,
            comment=None,
            icon_name=None,
            executable=None,
            categories=[],
            no_display=False,
            terminal=False,
        )
        assert app.get_display_name() == "Test App"

    def test_matches_search(self) -> None:
        """Test search matching."""
        app = Application(
            desktop_id="org.mozilla.firefox",
            name="Firefox",
            generic_name="Web Browser",
            comment="Browse the web",
            icon_name="firefox",
            executable="firefox",
            categories=["Network", "WebBrowser"],
            no_display=False,
            terminal=False,
        )
        assert app.matches_search("firefox")
        assert app.matches_search("browser")
        assert app.matches_search("web")
        assert app.matches_search("mozilla")
        assert not app.matches_search("chrome")


@pytest.fixture
def clean_manager():
    """Give each test a fresh ApplicationManager.

    The manager is a singleton that scans the disk once. That is right for the
    application and wrong for tests: the second test in a session would read
    the first one's cached scan and pass or fail depending on what ran before
    it. Resetting around each test is the only way these can be trusted to run
    in any order.
    """
    ApplicationManager.reset_singleton()
    yield
    ApplicationManager.reset_singleton()


class TestApplicationManager:
    """Test ApplicationManager."""

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_load_applications(self, mock_glib, tmp_path, clean_manager) -> None:
        """A desktop entry on disk becomes a listed application.

        Deliberately not mocked at the GIO level. A `Mock` answers any method
        name, so a mocked test cannot detect a call to a method that does not
        exist -- which is exactly the bug that made this list come out empty.
        A real desktop file, read by real GIO, is the only version of this
        test that can fail for the reason that matters.
        """
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "firefox.desktop").write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Firefox\n"
            "GenericName=Web Browser\n"
            "Comment=Browse the web\n"
            "Exec=/bin/true %u\n"
            "Icon=firefox\n"
            "Categories=Network;WebBrowser;\n"
            "Terminal=false\n"
        )

        manager = ApplicationManager()
        apps = manager.get_all_applications()

        assert manager.skipped_files() == []
        assert len(apps) == 1
        app = apps[0]
        assert app.desktop_id == "firefox.desktop"
        assert app.name == "Firefox"
        assert app.generic_name == "Web Browser"
        assert app.comment == "Browse the web"
        assert app.executable == "/bin/true"
        assert app.icon_name == "firefox"
        assert app.categories == ["Network", "WebBrowser"]
        assert app.no_display is False
        assert app.terminal is False

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_nodisplay_entries_are_not_listed(self, mock_glib, tmp_path, clean_manager) -> None:
        """`NoDisplay=true` keeps an entry out of the list, as the spec says."""
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "hidden.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Hidden\nExec=/bin/true\nNoDisplay=true\n"
        )
        (apps_dir / "shown.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Shown\nExec=/bin/true\n"
        )

        manager = ApplicationManager()
        ids = [a.desktop_id for a in manager.get_all_applications()]

        assert "hidden.desktop" not in ids
        assert "shown.desktop" in ids

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_terminal_key_is_read_from_the_file(self, mock_glib, tmp_path, clean_manager) -> None:
        """GIO exposes no Terminal accessor, so the key is read directly."""
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "term.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Term\nExec=/bin/true\nTerminal=true\n"
        )

        manager = ApplicationManager()
        assert [a.terminal for a in manager.get_all_applications()] == [True]

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_a_user_entry_overrides_the_system_one(self, mock_glib, tmp_path, clean_manager) -> None:
        """The first data directory to provide an id wins, in XDG order."""
        user_dir = tmp_path / "user"
        system_dir = tmp_path / "system"
        (user_dir / "applications").mkdir(parents=True)
        (system_dir / "applications").mkdir(parents=True)
        (user_dir / "applications" / "dup.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=User Version\nExec=/bin/true\n"
        )
        (system_dir / "applications" / "dup.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=System Version\nExec=/bin/true\n"
        )

        mock_glib.get_user_data_dir.return_value = str(user_dir)
        mock_glib.get_system_data_dirs.return_value = [str(system_dir)]

        manager = ApplicationManager()
        apps = [a for a in manager.get_all_applications() if a.desktop_id == "dup.desktop"]

        # Not two entries, and not the system's: the user's copy is the one a
        # launcher would start.
        assert len(apps) == 1
        assert apps[0].name == "User Version"

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_an_entry_whose_program_is_gone_is_reported_not_shown(
        self, mock_glib, tmp_path, clean_manager
    ) -> None:
        """GIO hides an entry whose `Exec=` program is not installed.

        Surprising, and load-bearing: `new_from_filename` resolves `Exec=`
        against `PATH` and returns NULL when nothing is there, raising
        `TypeError` in PyGObject. So a desktop file left behind by a program
        the user has since removed does not appear in the list at all.

        That is the right outcome -- there is no traffic to split-tunnel for a
        program that is not installed -- but a silently shorter list is
        indistinguishable from a bug, which is exactly the failure that emptied
        the list before. The reason has to be recorded.
        """
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "stale.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Stale\n"
            "Exec=/nonexistent/program-name %U\n"
        )
        (apps_dir / "present.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Present\nExec=/bin/true\n"
        )

        manager = ApplicationManager()
        ids = [a.desktop_id for a in manager.get_all_applications()]

        assert ids == ["present.desktop"]
        assert any("stale.desktop" in reason for reason in manager.skipped_files())

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_an_unreadable_entry_does_not_empty_the_list(
        self, mock_glib, tmp_path, clean_manager
    ) -> None:
        """One bad file must not take every other application with it.

        This is the failure that motivated the rewrite: a single wrong method
        name raised for every file, was swallowed, and left the user looking at
        an empty list with no way to tell it apart from having no applications.
        """
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        # Not a desktop entry at all, so GIO cannot construct one from it.
        (apps_dir / "broken.desktop").write_text("this is not a desktop entry")
        (apps_dir / "good.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Good\nExec=/bin/true\n"
        )

        manager = ApplicationManager()
        ids = [a.desktop_id for a in manager.get_all_applications()]

        assert ids == ["good.desktop"]
        # And the reason is recorded rather than lost.
        assert any("broken.desktop" in reason for reason in manager.skipped_files())

    def test_every_gio_method_the_loader_calls_exists(self, clean_manager) -> None:
        """Guard the whole class of bug: calling a GIO method that is not there.

        `ApplicationManager` calls into GIO by name. Those names are not
        checked by the type system, and a wrong one raises `AttributeError`
        at runtime. The obvious test -- one that mocks `DesktopAppInfo` -- is
        incapable of catching it, because a mock has an attribute for any name
        whatsoever. This test asks a real `DesktopAppInfo` instead.
        """
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        sample = Path("/usr/share/applications/alacarte.desktop")
        if not sample.is_file():
            pytest.skip("no desktop entry available to probe the GIO API with")

        info = Gio.DesktopAppInfo.new_from_filename(str(sample))
        assert info is not None, "GIO could not read its own desktop entry"

        # Every accessor the loader relies on. If GIO renames one, this fails
        # here rather than as an empty application list at runtime.
        for method in (
            "get_id",
            "get_name",
            "get_generic_name",
            "get_description",
            "get_icon",
            "get_executable",
            "get_categories",
            "get_nodisplay",
        ):
            assert hasattr(info, method), (
                f"Gio.DesktopAppInfo has no {method}; the application loader "
                f"calls it, so the application list would fail to build"
            )

        # Names that were used and do not exist. If any of these ever appears
        # to work, something else has changed and the list needs re-checking.
        for method in ("get_no_display", "get_comment", "get_is_terminal"):
            assert not hasattr(info, method), (
                f"getattr(Gio.DesktopAppInfo, {method}) now exists; the loader "
                f"uses a different name and this assertion needs reviewing"
            )

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_reload_picks_up_a_newly_installed_application(
        self, mock_glib, tmp_path, clean_manager
    ) -> None:
        """A long-running window must be able to see a new application.

        The manager is a singleton, so it scans once. Without `reload` the list
        is frozen for the lifetime of the process, and a user who installs
        something while the window is open never finds it. That reads as "this
        application does not work", not as "restart the app".
        """
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "first.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=First\nExec=/bin/true\n"
        )

        manager = ApplicationManager()
        assert [a.desktop_id for a in manager.get_all_applications()] == [
            "first.desktop"
        ]

        # Something gets installed.
        (apps_dir / "second.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Second\nExec=/bin/true\n"
        )

        # Still one, because nothing asked it to look again.
        assert len(manager.get_all_applications()) == 1

        assert manager.reload() == 2
        assert sorted(a.desktop_id for a in manager.get_all_applications()) == [
            "first.desktop",
            "second.desktop",
        ]

    @patch("vpn_split_tunnel.core.app_selector.GLib")
    def test_reload_forgets_an_application_that_was_uninstalled(
        self, mock_glib, tmp_path, clean_manager
    ) -> None:
        """The other direction matters too, and is the more annoying one.

        A rule naming an application the user has just uninstalled cannot match
        anything, and if the list still offers it the user will select an
        application that does not exist and be told it was applied.
        """
        mock_glib.get_user_data_dir.return_value = str(tmp_path)
        mock_glib.get_system_data_dirs.return_value = []

        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        entry = apps_dir / "goner.desktop"
        entry.write_text(
            "[Desktop Entry]\nType=Application\nName=Goner\nExec=/bin/true\n"
        )

        manager = ApplicationManager()
        assert len(manager.get_all_applications()) == 1

        entry.unlink()
        manager.reload()

        assert manager.get_all_applications() == []

    def test_search_applications(self, clean_manager) -> None:
        """Test searching applications."""
        manager = ApplicationManager()
        manager._apps = [
            Application("app1", "Firefox", None, None, None, None, [], False, False),
            Application("app2", "Chrome", None, None, None, None, [], False, False),
            Application("app3", "Terminal", None, None, None, None, [], False, False),
        ]

        results = manager.search_applications("fire")
        assert len(results) == 1
        assert results[0].name == "Firefox"

        results = manager.search_applications("")
        assert len(results) == 3

    def test_get_application(self, clean_manager) -> None:
        """Look up by desktop id, with or without the suffix.

        The ids GIO reports end in `.desktop`, and that is what the list and the
        saved config hold. The bare form is what a person types and what an
        older saved config may contain, so both have to resolve to the same
        application.
        """
        manager = ApplicationManager()
        manager._apps = [
            Application(
                "firefox.desktop", "Firefox", None, None, None, None, [], False, False
            ),
            Application(
                "chrome.desktop", "Chrome", None, None, None, None, [], False, False
            ),
        ]

        for spelling in ("firefox.desktop", "firefox"):
            app = manager.get_application(spelling)
            assert app is not None, f"{spelling!r} did not resolve"
            assert app.name == "Firefox"

        # A genuinely unknown id still returns nothing rather than a guess.
        app = manager.get_application("nonexistent")
        assert app is None