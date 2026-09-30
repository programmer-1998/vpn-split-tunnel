"""Row text must reach the screen literally.

libadwaita passes a row's title/subtitle to a `Gtk.Label` with `use-markup`
TRUE, and that label does not fall back to plain text when the markup fails to
parse -- it goes blank. "Software & Updates" is installed on this machine and
its row rendered with no text at all. It also means a name that happens to
contain markup is interpreted rather than shown, so an application called
`<b>x</b>` was rendered bold.

`plain()` is the fix. These tests pin both halves: that `plain()` produces text
a markup label renders verbatim, and that no row text setter is left unescaped.
The second half is structural on purpose -- the failure was not a wrong string,
it was a whole call site nobody had looked at.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from vpn_split_tunnel.ui.text import plain

UI_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "vpn_split_tunnel" / "ui"

# Receivers whose set_title() is a *window* title: the window manager shows
# these literally, so escaping them would put "&amp;" in the title bar. They are
# identified by what they were constructed from, not by line number.
WINDOW_CLASSES = {
    "Adw.ApplicationWindow",
    "Adw.PreferencesDialog",
    "Adw.PreferencesWindow",
    "Adw.Window",
    "Adw.MessageDialog",
    "Adw.AboutDialog",
    "Adw.AlertDialog",
    "Gtk.Window",
    "Gtk.Dialog",
    "Gtk.FileDialog",
}

# Row/group/page setters that parse markup.
MARKUP_SETTERS = {"set_title", "set_subtitle", "set_description"}


class TestPlainEscapes:
    def test_bare_ampersand_is_escaped(self):
        assert plain("A & B") == "A &amp; B"

    def test_angle_brackets_are_escaped(self):
        assert plain("5 < 6 > 2") == "5 &lt; 6 &gt; 2"

    def test_markup_in_a_name_is_neutralised(self):
        assert plain("<b>Bold</b>") == "&lt;b&gt;Bold&lt;/b&gt;"

    def test_real_desktop_names_on_this_machine(self):
        # Both are installed here and both used to render as an empty row.
        assert plain("Software & Updates") == "Software &amp; Updates"
        assert plain("Tour (Greeter & Tour)") == "Tour (Greeter &amp; Tour)"

    def test_ampersand_entity_in_a_name_survives(self):
        # A desktop file can legitimately be named "R&amp;D". Escaping must
        # show the original text, not a half-decoded one.
        assert plain("R&D") == "R&amp;D"

    def test_text_without_special_characters_is_unchanged(self):
        assert plain("Firefox") == "Firefox"


def _constructor_names(tree: ast.Module) -> set[str]:
    """Local names built from a window/dialog class."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Attribute) and func.attr == "new":
            owner = func.value
            if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
                if f"{owner.value.id}.{owner.attr}" in WINDOW_CLASSES:
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
    return names


def _window_class_names(tree: ast.Module) -> set[str]:
    """Classes deriving from a window/dialog class, so `self.set_title` on one
    of them is a window title as well."""
    direct = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if ast.unparse(base) in WINDOW_CLASSES:
                    direct.add(node.name)
    # one more level, so a subclass of a window subclass is still covered
    inherited = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if ast.unparse(base) in direct:
                    inherited.add(node.name)
    return direct | inherited


def _row_text_calls(tree: ast.Module):
    """(lineno, receiver, setter, argument source) for markup-parsing setters."""
    windows = _constructor_names(tree)
    window_classes = _window_class_names(tree)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list = []
            self.found: list = []

        def visit_ClassDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr in MARKUP_SETTERS:
                value = node.func.value
                if node.func.attr == "set_title" and isinstance(value, ast.Name):
                    enclosing = self.stack[-1] if self.stack else None
                    if value.id in windows:
                        pass  # window title, shown literally
                    elif value.id == "self" and enclosing in window_classes:
                        pass  # window title on a window subclass
                    else:
                        self._record(node)
                else:
                    self._record(node)
            self.generic_visit(node)

        def _record(self, node):
            arg = ast.unparse(node.args[0]) if node.args else ""
            self.found.append(
                (node.lineno, ast.unparse(node.func.value), node.func.attr, arg)
            )

    visitor = Visitor()
    visitor.visit(tree)
    return visitor.found


def _sources():
    return sorted(p for p in UI_DIR.glob("*.py") if p.name != "text.py")


def _toast_constructions(tree: ast.Module):
    """(lineno, argument source) for every Adw.Toast.new(...).

    A toast title is a markup label, so it needs plain() for the same reason a
    row title does. Adw.Banner is deliberately absent: measured, it holds its
    text literally, so escaping there would show "&amp;" to the user.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "new"):
            continue
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)):
            continue
        if f"{owner.value.id}.{owner.attr}" != "Adw.Toast":
            continue
        if not node.args:
            continue
        found.append((node.lineno, ast.unparse(node.args[0])))
    return found


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_toast_text_is_escaped(path):
    tree = ast.parse(path.read_text())
    offenders = [f"  line {n}: Adw.Toast.new({a[:60]})"
                 for n, a in _toast_constructions(tree)
                 if not a.startswith("plain(")]
    assert not offenders, (
        f"{path.name} builds a Toast from text without plain():\n" + "\n".join(offenders)
    )


def test_the_launch_toast_is_escaped():
    """The launch outcome message carries the application's name from disk."""
    src = (UI_DIR / "main_window.py").read_text()
    assert "Adw.Toast.new(plain(message))" in src
    assert "Adw.Toast.new(message)" not in src


def test_the_ui_directory_was_found():
    files = _sources()
    assert files, f"no UI modules found under {UI_DIR}"
    assert {p.name for p in files} >= {"app_selector.py", "main_window.py", "vpn_selector.py"}


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_row_text_setter_is_left_unescaped(path):
    """Every title/subtitle/description handed to a row goes through plain()."""
    src = path.read_text()
    tree = ast.parse(src)
    offenders = []
    for lineno, recv, setter, arg in _row_text_calls(tree):
        already = arg.startswith("plain(")
        if not already:
            offenders.append(f"  line {lineno}: {recv}.{setter}({arg[:60]})")
    assert not offenders, (
        f"{path.name} passes text to a markup-parsing widget without plain():\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_plain_is_not_double_wrapped(path):
    src = path.read_text()
    assert "plain(plain(" not in src, f"{path.name} escapes text twice"


def test_window_titles_are_left_unescaped():
    """The escape must not reach the title bar, which shows text literally."""
    checks = {
        "main_window.py": 'self.set_title(plain(_("VPN Split Tunnel")))',
        "preferences.py": 'self.set_title(plain(_("Preferences")))',
    }
    for name, forbidden in checks.items():
        src = (UI_DIR / name).read_text()
        assert forbidden not in src, f"{name} escapes a window title: {forbidden}"


class TestAgainstRealWidgets:
    """The measurement this whole module rests on, re-run against real widgets."""

    @staticmethod
    def _label_text(row):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        found, stack = [], [row]
        while stack:
            node = stack.pop()
            child = node.get_first_child() if hasattr(node, "get_first_child") else None
            while child is not None:
                if isinstance(child, Gtk.Label):
                    found.append(child)
                stack.append(child)
                child = child.get_next_sibling()
        return [label.get_text() for label in found]

    def _adw(self):
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        Adw.init()
        return Adw

    def test_escaped_text_is_shown_verbatim(self):
        try:
            Adw = self._adw()
        except Exception as exc:  # pragma: no cover - depends on the session
            pytest.skip(f"libadwaita unavailable: {exc}")

        for raw in ("Software & Updates", "Tour (Greeter & Tour)", "<b>x</b>", "R&D"):
            row = Adw.ActionRow.new()
            row.set_title(plain(raw))
            shown = [t for t in self._label_text(row) if t.strip()]
            assert shown == [raw], f"{raw!r} rendered as {shown!r}"

    def test_unescaped_text_would_be_lost(self):
        """Why plain() exists: the same text, without escaping, renders blank."""
        try:
            Adw = self._adw()
        except Exception as exc:  # pragma: no cover - depends on the session
            pytest.skip(f"libadwaita unavailable: {exc}")

        row = Adw.ActionRow.new()
        row.set_title("Software & Updates")
        assert [t for t in self._label_text(row) if t.strip()] == []
