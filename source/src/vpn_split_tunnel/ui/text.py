"""Text that goes into a widget, kept literal.

libadwaita hands the string you pass to ``Adw.ActionRow.set_title`` (and the
other row/group/page setters) to a ``Gtk.Label`` that has ``use-markup`` set to
TRUE.  That was measured, not assumed:

    row = Adw.ActionRow.new(); row.set_title("A & B")
    # -> Gtk-WARNING: Failed to set text 'A & B' from markup ...
    # -> the label's text is the empty string, so the row renders blank

The label does *not* fall back to plain text, it goes blank.  Every one of
these parses markup and blanks on a bare ``&`` or ``<``:

    Adw.ActionRow         set_title, set_subtitle
    Adw.ExpanderRow       set_title, set_subtitle
    Adw.EntryRow          set_title
    Adw.PreferencesGroup  set_title, set_description
    Adw.PreferencesPage   set_title, set_description
    Adw.Toast             set_title

And these take the string literally, so escaping them would *introduce* a bug
by showing ``&amp;`` to the user:

    Adw.ApplicationWindow / Adw.PreferencesDialog / Gtk.FileDialog  set_title
    Adw.WindowTitle, Adw.Banner, Adw.MessageDialog, Gtk.Label

So: escape with :func:`plain` when the text goes into a row, group, page, entry
or toast, and pass it straight through when it goes into a window title.

This matters because the text is not always ours.  Application names and
desktop-file ids come from ``.desktop`` files on disk, VPN client names come
from systemd units, and domain/IP lists come from the user.  "Software &
Updates" and "Tour (Greeter & Tour)" are installed on this machine and both
render as an empty row without this.  It also matters for our own strings:
a translation that happens to contain an ampersand would blank the whole row,
and translators have no way of knowing markup is involved.
"""

from __future__ import annotations

from gi.repository import GLib

__all__ = ["plain"]


def plain(value: str) -> str:
    """Return `value` escaped so a markup-parsing widget shows it verbatim."""
    return GLib.markup_escape_text(value, -1)
