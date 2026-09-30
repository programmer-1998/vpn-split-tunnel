"""The bundled .po -> .mo compiler must produce catalogues gettext can read.

This project cannot rely on the gettext toolchain (msgfmt/msgmerge/xgettext
are not installed on the machines it is built on), so ``tools/msgfmt.py``
does the compilation and ``tools/sync_pot.py`` does the extraction. These
tests pin the two things that actually broke while writing it:

* a Python ``gettext.translation`` round-trip against the compiled catalogue
  (this failed with ``too many values to unpack`` when a key was wrong);
* per-entry string lengths. The first working version leaked the length of
  the *last* entry into every table row because ``id_bytes`` was a loop
  variable read after the loop, so entry zero's key length was reported as
  16 instead of 0.
"""

from __future__ import annotations

import gettext
import pathlib
import struct
import sys
from tempfile import TemporaryDirectory

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import msgfmt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FA_PO = ROOT / "resources" / "locale" / "fa" / "LC_MESSAGES" / "vpn-split-tunnel.po"
_MAGIC_LE = 0x950412DE


@pytest.fixture(scope="module")
def fa_mo() -> bytes:
    return msgfmt.compile_po(FA_PO.read_text(encoding="utf-8"))


def _catalog(mo: bytes, language: str):
    with TemporaryDirectory() as d:
        p = pathlib.Path(d) / language / "LC_MESSAGES"
        p.mkdir(parents=True)
        (p / "vpn-split-tunnel.mo").write_bytes(mo)
        return gettext.translation("vpn-split-tunnel", localedir=d, languages=[language])


class TestRoundTrip:
    def test_magic_and_version(self, fa_mo: bytes) -> None:
        magic, version = struct.unpack_from("<II", fa_mo, 0)
        assert magic == _MAGIC_LE
        assert version == 0

    def test_translated_strings_come_back(self, fa_mo: bytes) -> None:
        t = _catalog(fa_mo, "fa")
        assert t.gettext("VPN Split Tunnel") == "مدیریت تونل جدا شده VPN"

    def test_untranslated_strings_fall_back_to_msgid(self, fa_mo: bytes) -> None:
        t = _catalog(fa_mo, "fa")
        assert t.gettext("Connect") == "Connect"

    def test_plural_catalogue_is_loadable(self, fa_mo: bytes) -> None:
        # A plural entry's key is msgid + \x00 + msgid_plural; gettext must
        # select a form. Python's gettext leaves the %d in the translated
        # template -- substituting the count is the i18n wrapper's job (see
        # tests/test_i18n.py::test_ngettext_persian_is_actually_persian).
        t = _catalog(fa_mo, "fa")
        one = t.ngettext("%d domain", "%d domains", 1)
        many = t.ngettext("%d domain", "%d domains", 5)
        assert one == "%d دامنه"
        assert many == "%d دامنه"  # Persian keeps the singular after a number


class TestSkipUntranslated:
    def test_empty_translations_are_omitted_and_fall_back(self) -> None:
        """A .po entry with an empty translation must vanish from the .mo.

        If it is left in with an empty value, gettext returns ``""`` for it and
        the Persian UI renders a blank label. Skipping it makes the lookup miss
        and gettext falls back to the msgid, exactly like GNU msgfmt.
        """
        text = (
            'msgid ""\n'
            'msgstr ""\n'
            '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            'msgid "Translated"\n'
            'msgstr "ترجمه"\n\n'
            'msgid "Untranslated"\n'
            'msgstr ""\n\n'
            'msgid "Plural thing"\n'
            'msgid_plural "Plural things"\n'
            'msgstr[0] ""\n'
            'msgstr[1] ""\n'
        )
        t = _catalog(msgfmt.compile_po(text), "fa")
        assert t.gettext("Translated") == "ترجمه"
        assert t.gettext("Untranslated") == "Untranslated"
        assert t.ngettext("Plural thing", "Plural things", 1) == "Plural thing"
        assert t.ngettext("Plural thing", "Plural things", 2) == "Plural things"

    def test_header_entry_is_always_kept(self) -> None:
        text = (
            'msgid ""\n'
            'msgstr ""\n'
            '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            'msgid "Only translated key"\n'
            'msgstr "چون"\n'
        )
        mo = msgfmt.compile_po(text)
        assert len(mo) >= 28
        raw = msgfmt._parse_po(text)
        kept = [m for m in raw if msgfmt._is_translated(m)]
        assert any(m.msgid == "" for m in kept)


class TestPerEntryLengths:
    def test_no_key_contains_two_nul_bytes(self, fa_mo: bytes) -> None:
        """The loader that broke this: msg.split(b'\\x00') needs <= 2 pieces."""
        raw = msgfmt._parse_po(FA_PO.read_text(encoding="utf-8"))
        for message in raw:
            assert message.key.count("\x00") <= 1, message.key

    def test_every_length_field_matches_its_own_entry(self, fa_mo: bytes) -> None:
        """Walk the tables the way gettext does and compare each slice.

        The leaked-length regression made entry 0's id length 16; if that
        happens again, this slice comparison fails.
        """
        buf = fa_mo
        assert struct.unpack_from("<I", buf, 0)[0] == _MAGIC_LE
        _ver, msgcount, masteridx, transidx = struct.unpack_from("<4I", buf, 4)

        parsed = msgfmt._parse_po(FA_PO.read_text(encoding="utf-8"))
        expected = [
            msgfmt.encode_po_string(m.key)
            for m in sorted((m for m in parsed if msgfmt._is_translated(m)), key=lambda m: m.key)
        ]
        assert len(expected) == msgcount, "header + translated entries only"

        for i in range(msgcount):
            mlen, moff = struct.unpack_from("<II", buf, masteridx + 8 * i)
            tlen, toff = struct.unpack_from("<II", buf, transidx + 8 * i)
            raw = buf[moff : moff + mlen]
            # The stored length excludes the terminating NUL: raw is the key
            # without it, and exactly this entry's key.
            assert raw == expected[i][:-1], f"entry {i}: key mismatch"
            assert toff + tlen <= len(buf)

    def test_parse_po_handles_plural_index_lines(self) -> None:
        """msgstr[N] lines must parse (regression: 'msgstr[0]' raised)."""
        text = (
            'msgid ""\n'
            'msgstr ""\n'
            '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
            'msgid "%d item"\n'
            'msgid_plural "%d items"\n'
            'msgstr[0] "یک مورد"\n'
            'msgstr[1] "%d مورد"\n'
        )
        messages = msgfmt._parse_po(text)
        plural = [m for m in messages if m.msgid_plural]
        assert len(plural) == 1
        assert plural[0].plural_msgstr == ["یک مورد", "%d مورد"]

    def test_sync_catalogue_is_idempotent_via_gettext(self, fa_mo: bytes) -> None:
        """Recompiling the committed .po reproduces a loadable catalogue."""
        t = _catalog(msgfmt.compile_po(FA_PO.read_text(encoding="utf-8")), "fa")
        assert t.gettext("Not connected to any VPN") == "به هیچ VPNای متصل نیستید"