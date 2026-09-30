"""Per-row edit/remove buttons in the domain editor are icon-only.

With no label, the tooltip is the user's whole affordance for what the button
does, so it must always be set.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from vpn_split_tunnel.ui.domain_editor import DomainEditor  # noqa: E402
from vpn_split_tunnel.utils.i18n import setup_i18n  # noqa: E402


def _buttons(root: Gtk.Widget) -> list[Gtk.Button]:
    """Flatten the widget tree under ``root`` collecting Gtk.Buttons."""
    found: list[Gtk.Button] = []

    def walk(widget: Gtk.Widget) -> None:
        if isinstance(widget, Gtk.Button):
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(root)
    return found


class TestRowButtonTooltips:
    def test_edit_and_remove_buttons_explain_themselves(self) -> None:
        # Deterministic regardless of the machine's locale.
        setup_i18n("en")
        editor = DomainEditor()
        row = editor._create_entry_row("example.com", "domain")
        tooltips = {
            tooltip
            for button in _buttons(row)
            if (tooltip := button.get_tooltip_text())
        }
        assert "Edit this entry" in tooltips
        assert "Remove this entry" in tooltips

    def test_every_icon_button_in_a_row_has_a_tooltip(self) -> None:
        setup_i18n("en")
        editor = DomainEditor()
        row = editor._create_entry_row("10.0.0.1", "ip")
        for button in _buttons(row):
            assert button.get_tooltip_text(), (
                "icon-only button without a tooltip"
            )


class TestAddDomainValidation:
    def _editor(self) -> DomainEditor:
        setup_i18n("en")
        return DomainEditor()

    def test_add_domain_normalizes_urls_and_case(self) -> None:
        editor = self._editor()
        editor.domain_entry.set_text("HTTPS://Sina.IR/foo")
        editor._on_add_domain()
        assert editor.get_domains() == ["sina.ir"]
        # Entry is cleared after a successful add.
        assert editor.domain_entry.get_text() == ""

    def test_add_domain_rejects_garbage_without_adding(self) -> None:
        editor = self._editor()
        editor.domain_entry.set_text("not a domain!!!")
        editor._on_add_domain()
        assert editor.get_domains() == []
        # The invalid text stays so the user can correct it.
        assert editor.domain_entry.get_text() == "not a domain!!!"

    def test_add_domain_rejects_ip_in_domain_field(self) -> None:
        editor = self._editor()
        editor.domain_entry.set_text("10.0.0.1")
        editor._on_add_domain()
        assert editor.get_domains() == []

    def test_add_domain_ignores_empty_input(self) -> None:
        editor = self._editor()
        editor.domain_entry.set_text("   ")
        editor._on_add_domain()
        assert editor.get_domains() == []

    def test_duplicate_normalized_domain_not_added_twice(self) -> None:
        editor = self._editor()
        editor.domain_entry.set_text("sina.ir")
        editor._on_add_domain()
        editor.domain_entry.set_text("SINA.IR")
        editor._on_add_domain()
        assert editor.get_domains() == ["sina.ir"]


class TestImportValidation:
    def test_import_validates_each_line(self) -> None:
        setup_i18n("en")
        editor = DomainEditor()
        content = (
            "# comment line\n"
            "sina.ir\n"
            "10.0.0.1\n"
            "192.168.1.0/24\n"
            "totally invalid!!!\n"
            "https://example.com/path\n"
        )
        skipped = editor._import_text(content)
        assert editor.get_domains() == ["sina.ir", "example.com"]
        assert editor.get_ips() == ["10.0.0.1", "192.168.1.0/24"]
        assert skipped, "invalid line should be reported"
        assert skipped == ["totally invalid!!!"]


class TestEditEntry:
    def test_edit_domain_normalizes(self) -> None:
        setup_i18n("en")
        editor = DomainEditor()
        editor.set_domains(["sina.ir"])
        editor._apply_edit("sina.ir", "domain", "HTTPS://A.Sina.IR/x")
        assert editor.get_domains() == ["a.sina.ir"]

    def test_edit_domain_rejects_garbage(self) -> None:
        setup_i18n("en")
        editor = DomainEditor()
        editor.set_domains(["sina.ir"])
        editor._show_error = lambda _message: None
        editor._apply_edit("sina.ir", "domain", "not a domain!!!")
        assert editor.get_domains() == ["sina.ir"]

    def test_edit_ip_validates(self) -> None:
        setup_i18n("en")
        editor = DomainEditor()
        editor.set_ips(["10.0.0.1"])
        editor._show_error = lambda _message: None
        editor._apply_edit("10.0.0.1", "ip", "10.0.0.2")
        assert editor.get_ips() == ["10.0.0.2"]

    def test_edit_ip_rejects_invalid_cidr(self) -> None:
        setup_i18n("en")
        editor = DomainEditor()
        editor.set_ips(["10.0.0.1"])
        editor._show_error = lambda _message: None
        editor._apply_edit("10.0.0.1", "ip", "10.0.0.1/40")
        assert editor.get_ips() == ["10.0.0.1"]