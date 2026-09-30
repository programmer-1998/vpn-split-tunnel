"""Compile a gettext .po file into a GNU .mo file, without the gettext tools.

The GNU gettext utilities (`msgfmt`, ...) are not installed on every machine
this project gets built on, and the .mo that results is consumed here only by
Python's `gettext` module (the GUI) plus glibc's gettext for anyone reading the
installed catalogue, so the build does not need to depend on them. This module
is that missing tool.

The output is the standard GNU MO binary format, verified against real .mo
files on disk: each string is stored NUL-terminated in the data area, but the
length words in the offset tables exclude the terminating NUL. The hash table
is deliberately omitted (HashSize = 0); the original-string table is written
sorted, which is what the format specifies for that case, and Python's gettext
ignores the hash table entirely and builds its dictionary from the tables.

CLI:  python tools/msgfmt.py <input.po> <output.mo>
"""

from __future__ import annotations

import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ----------------------------------------------------------------------
# .po parsing
# ----------------------------------------------------------------------
_TOKEN = re.compile(
    r"^(msgctxt|msgid(?:_plural)?|msgstr(?:\[[0-9]+\])?)\s+(.*)$"
)
_STRING_LINE = re.compile(r'^"(.*)"\s*$', re.DOTALL)


class _ParseError(ValueError):
    pass


def _decode_c_string(raw: str) -> str:
    """Convert the C escapes inside one .po quoted string into text."""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            raise _ParseError("trailing backslash in string")
        esc = raw[i]
        i += 1
        if esc == "n":
            out.append("\n")
        elif esc == "t":
            out.append("\t")
        elif esc == "r":
            out.append("\r")
        elif esc == "a":
            out.append("\a")
        elif esc == "b":
            out.append("\b")
        elif esc == "f":
            out.append("\f")
        elif esc == "v":
            out.append("\v")
        elif esc == "\\":
            out.append("\\")
        elif esc == '"':
            out.append('"')
        elif esc == "x":
            hexd = raw[i : i + 2]
            if not re.fullmatch(r"[0-9A-Fa-f]{2}", hexd):
                raise _ParseError(f"bad \\x escape: {hexd!r}")
            out.append(chr(int(hexd, 16)))
            i += 2
        elif esc in "01234567":
            octd = esc + raw[i : i + 2]
            octd = octd[:3]
            if not re.fullmatch(r"[0-7]{1,3}", octd):
                raise _ParseError(f"bad octal escape: {octd!r}")
            out.append(chr(int(octd, 8)))
            i += len(octd) - 1
        elif esc == "u":
            hexd = raw[i : i + 4]
            if not re.fullmatch(r"[0-9A-Fa-f]{4}", hexd):
                raise _ParseError(f"bad \\u escape: {hexd!r}")
            out.append(chr(int(hexd, 16)))
            i += 4
        elif esc == "U":
            hexd = raw[i : i + 8]
            if not re.fullmatch(r"[0-9A-Fa-f]{8}", hexd):
                raise _ParseError(f"bad \\U escape: {hexd!r}")
            out.append(chr(int(hexd, 16)))
            i += 8
        else:
            # Unknown escapes are kept literally, as gettext does.
            out.append(esc)
    return "".join(out)


@dataclass
class Message:
    """One logical msgid/msgstr group from a .po file."""

    msgid: str = ""
    msgid_plural: str | None = None
    context: str | None = None
    msgstr: str | None = None
    plural_msgstr: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """The lookup key inside the .mo file (context \x04 msgid, plurals fused)."""
        base = self.msgid
        if self.msgid_plural:
            base = base + "\x00" + self.msgid_plural
        if self.context:
            base = self.context + "\x04" + base
        return base

    @property
    def translated(self) -> str:
        """The translated string as stored in the .mo file."""
        if self.msgid_plural:
            parts = [
                self.plural_msgstr[0] if len(self.plural_msgstr) > 0 else "",
                self.plural_msgstr[1] if len(self.plural_msgstr) > 1 else "",
            ]
            while len(parts) < 2:
                parts.append("")
            return "\x00".join(parts)
        return self.msgstr or ""


def _parse_po(text: str) -> list[Message]:
    """Parse .po text into Message objects, in file order."""
    messages: list[Message] = []
    current: Message | None = None
    pending: str | None = None  # key of the string currently being accumulated
    accumulator: list[str] = []

    def flush_accumulator() -> None:
        nonlocal pending, accumulator
        if current is None or pending is None:
            return
        value = _decode_c_string("".join(accumulator))
        if pending == "msgid":
            current.msgid = value
        elif pending == "msgid_plural":
            current.msgid_plural = value
        elif pending == "msgstr":
            current.msgstr = value
        else:  # msgstr[N]
            idx = int(pending[pending.index("[") + 1 : pending.index("]")])
            while len(current.plural_msgstr) <= idx:
                current.plural_msgstr.append("")
            current.plural_msgstr[idx] = value
        pending = None
        accumulator = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if line.startswith("#:"):
                if current is not None:
                    current.references.append(line[2:].strip())
            continue

        token = _TOKEN.match(line)
        if token:
            flush_accumulator()
            keyword, quoted = token.group(1), token.group(2)
            if keyword == "msgid" and current is not None and current.msgid == "" \
                    and not current.msgid_plural and current.msgstr is None:
                # Header entry: msgid "" followed by the header string.
                pass
            if keyword == "msgid":
                if current is not None:
                    messages.append(current)
                current = Message()
            elif keyword == "msgctxt":
                current.context = _decode_c_string(quoted.strip('"'))
            pending = keyword
            if pending == "msgctxt":
                pending = None
            else:
                accumulator = []
                if _STRING_LINE.match(quoted):
                    accumulator.append(_STRING_LINE.match(quoted).group(1))
                    # value parsed by flush
            continue

        string_line = _STRING_LINE.match(line)
        if string_line:
            accumulator.append(string_line.group(1))
            continue

        raise _ParseError(f"unrecognised .po line: {line!r}")

    flush_accumulator()
    if current is not None:
        messages.append(current)
    return messages


# ----------------------------------------------------------------------
# .mo writing
# ----------------------------------------------------------------------
_MAGIC_LE = 0x950412DE


def encode_po_string(value: str) -> bytes:
    """Encode one string as it appears in the .mo data area."""
    return value.encode("utf-8") + b"\x00"


def _is_translated(message: Message) -> bool:
    """Whether the entry carries any translation.

    An entry whose translation is entirely empty is untranslated. It must be
    dropped from the .mo: Python's gettext returns ``""`` -- not the msgid --
    for a key that is present with an empty translation, so leaving such
    entries in would render blank labels. Dropping them makes the lookup miss
    and gettext falls back to the msgid, which is exactly what GNU msgfmt
    achieves by not emitting untranslated strings.
    """
    # The header entry has an empty msgid but a real translation.
    if message.msgid == "":
        return True
    if message.msgid_plural:
        return any(part for part in message.plural_msgstr)
    return bool(message.msgstr)


def generate(messages: list[Message]) -> bytes:
    """Render Message objects into the bytes of a GNU .mo file."""
    # Sort by the stored key, the byte order glibc's no-hash-table lookup
    # (binary search over the original string table) expects.
    entries = sorted(
        ((m.key, m.translated) for m in messages if _is_translated(m)),
        key=lambda e: e[0],
    )
    n = len(entries)

    strings: list[tuple[bytes, bytes, int, int]] = []  # (id bytes, str bytes, idoff, stroff)
    data = bytearray()
    o_off = 28                       # header size
    t_off = o_off + 8 * n            # original table
    data_offset = t_off + 8 * n      # start of the string data area

    for msgid, msgstr in entries:
        id_bytes = encode_po_string(msgid)
        str_bytes = encode_po_string(msgstr)
        # The spec recommends 4-byte alignment inside the data area.
        while (data_offset + len(data)) % 4:
            data.append(0)
        id_off = data_offset + len(data)
        data.extend(id_bytes)
        while (data_offset + len(data)) % 4:
            data.append(0)
        str_off = data_offset + len(data)
        data.extend(str_bytes)
        strings.append((id_bytes, str_bytes, id_off, str_off))

    header = struct.pack(
        "<IIIIIII",
        _MAGIC_LE,
        0,                  # version
        n,                  # number of strings
        o_off,              # original string table offset
        t_off,              # translation table offset
        0,                  # hash table offset: none
        0,                  # hash table size: none
    )
    original = b"".join(
        struct.pack("<II", len(id_bytes) - 1, id_off) for id_bytes, _, id_off, _ in strings
    )
    translated = b"".join(
        struct.pack("<II", len(str_bytes) - 1, str_off) for _, str_bytes, _, str_off in strings
    )
    return header + original + translated + bytes(data)


def compile_po(text: str) -> bytes:
    """Compile .po text into .mo bytes."""
    return generate(_parse_po(text))


def compile_po_file(src: Path, dst: Path) -> None:
    """Compile one .po file into .mo, writing to `dst`."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(compile_po(src.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {sys.argv[0]} <input.po> <output.mo>", file=sys.stderr)
        return 2
    compile_po_file(Path(argv[0]), Path(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))