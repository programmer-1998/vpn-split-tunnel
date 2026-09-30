"""The Application selector surfaces unreadable desktop entries.

The scan skips .desktop files it cannot parse; those reasons are collected by
``ApplicationManager.skipped_files()`` and must be shown in the UI instead of
silently shrinking the list. The widget is built headlessly (Adw widgets
construct without a display).
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

from vpn_split_tunnel.core.app_selector import Application, get_application_manager  # noqa: E402
from vpn_split_tunnel.ui.app_selector import AppSelector  # noqa: E402


@pytest.fixture()
def selector() -> AppSelector:
    return AppSelector()


class TestSkippedGroup:
    def test_hidden_by_default(self, selector: AppSelector) -> None:
        assert selector._skipped_group.get_visible() is False
        assert selector._skipped_rows == []

    def test_populates_one_row_per_reason(self, selector: AppSelector) -> None:
        selector.set_skipped([
            "broken.desktop: Permission denied",
            "old.desktop: malformed file",
        ])
        assert selector._skipped_group.get_visible() is True
        assert len(selector._skipped_rows) == 3  # summary + two entries
        titles = [row.get_title() for row in selector._skipped_rows]
        assert titles[0].startswith("2 application entries")
        assert "broken.desktop" in titles[1]
        assert "old.desktop" in titles[2]

    def test_clear_hides_and_resets(self, selector: AppSelector) -> None:
        selector.set_skipped(["one.desktop: boom"])
        assert selector._skipped_group.get_visible() is True
        selector.set_skipped([])
        assert selector._skipped_group.get_visible() is False
        assert selector._skipped_rows == []

    def test_resetting_replaces_rows(self, selector: AppSelector) -> None:
        selector.set_skipped(["a.desktop: x", "b.desktop: y"])
        first_pass = selector._skipped_rows
        # The summary row is one persistent widget re-titled for the new
        # count; the per-entry rows are fresh widgets every time.
        old_entries = first_pass[1:]
        selector.set_skipped(["c.desktop: z"])
        second_pass = selector._skipped_rows
        assert len(second_pass) == 2  # summary + one entry
        assert "c.desktop" in second_pass[1].get_title()
        # The old per-entry rows were removed from the group.
        for gone in old_entries:
            assert gone.get_parent() is None
        # The summary row is reused, not rebuilt.
        assert first_pass[0] is second_pass[0]

    def test_row_titles_are_plain_text(self, selector: AppSelector) -> None:
        """Reasons come from disk and may contain markup-ish characters."""
        selector.set_skipped(["weird <b>name</b> & Co.desktop: nope"])
        entry = selector._skipped_rows[1]
        assert entry.get_title() == "weird &lt;b&gt;name&lt;/b&gt; &amp; Co.desktop: nope"


class TestManagerIntegration:
    def test_skipped_files_collected_by_manager(self) -> None:
        """In this environment no desktop entry should be unreadable; the
        plumbing (set_skipped <- manager) must not raise with the real list."""
        manager = get_application_manager()
        apps = manager.get_all_applications()
        skipped = manager.skipped_files()
        assert isinstance(apps, list)
        assert isinstance(skipped, list)
        assert all(isinstance(s, str) for s in skipped)