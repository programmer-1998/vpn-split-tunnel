"""sync_pot.py's catalogue merge must be idempotent.

A pre-existing bug made split_po_prefix treat the *first* entry's ``#:``
reference comments as part of the file header: they were kept in the preserved
prefix while render_po also emitted a fresh reference for the same entry, so
every re-sync appended another copy (of the reference, then of a blank line)
and the .po drifted run after run. These tests pin the fixed boundary -- the
header is preserved, the first entry's references live in the body, and
applying the merge twice yields identical bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import msgfmt  # noqa: E402
import sync_pot as sp  # noqa: E402

SAMPLE = """# Persian translation for vpn-split-tunnel
#
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#: src/vpn_split_tunnel/ui/a.py:10
#: src/vpn_split_tunnel/ui/b.py:20
msgid "Hello"
msgstr "سلام"

#: src/vpn_split_tunnel/ui/c.py:30
msgid "World"
msgstr ""
"""


def _extracted() -> list[sp.Extracted]:
    return [
        sp.Extracted(msgid="Hello", ref="src/vpn_split_tunnel/ui/a.py:10"),
        sp.Extracted(msgid="World", ref="src/vpn_split_tunnel/ui/c.py:30"),
    ]


class TestSplitPoPrefix:
    def test_first_entry_refs_are_not_header_material(self) -> None:
        prefix, _ = sp.split_po_prefix(SAMPLE)
        assert "#: src/vpn_split_tunnel/ui/a.py:10" not in prefix
        assert "#: src/vpn_split_tunnel/ui/b.py:20" not in prefix
        # The header (heading comment + msgid/msgstr pair) is the whole prefix.
        assert prefix.endswith("\n\n")
        assert "Content-Type" in prefix

    def test_refs_of_first_entry_are_regenerated_from_scan(self) -> None:
        # The first entry's references are not preserved text the way later
        # entries' are: the header boundary drops them and render_po re-emits
        # the source-scan reference. What must never happen is the entry being
        # lost or duplicated.
        prefix, rest = sp.split_po_prefix(SAMPLE)
        assert "#: src/vpn_split_tunnel/ui/a.py:10" not in prefix
        existing = {m.key: m for m in msgfmt._parse_po(rest)}
        out = sp.render_po(prefix, _extracted(), existing)
        assert "#: src/vpn_split_tunnel/ui/a.py:10" in out

    def test_existing_translations_are_kept(self) -> None:
        _, rest = sp.split_po_prefix(SAMPLE)
        existing = {m.key: m for m in msgfmt._parse_po(rest)}
        assert existing["Hello"].msgstr == "سلام"


class TestIdempotentMerge:
    """Splitting and re-rendering a catalogue must be a fixed point."""

    @staticmethod
    def _merge(po_text: str) -> str:
        prefix, rest = sp.split_po_prefix(po_text)
        existing = {m.key: m for m in msgfmt._parse_po(rest)}
        return sp.render_po(prefix, _extracted(), existing)

    def test_merge_is_a_fixed_point(self) -> None:
        once = self._merge(SAMPLE)
        twice = self._merge(once)
        assert once == twice

    def test_does_not_accumulate_first_entry_refs(self) -> None:
        once = self._merge(SAMPLE)
        # The first entry has exactly one reference, never more.
        body = once.split("\n\n", 1)[1]
        assert body.count("#: src/vpn_split_tunnel/ui/a.py:10") == 1