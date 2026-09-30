"""Regenerate the translation template (.pot) and sync the .po catalogues.

gettext's own xgettext/msgmerge binaries are not guaranteed to be installed on
the machines this project is built on (they are not on the development
machine), and the committed .po files had drifted away from the code, so this
tool does the two jobs with a small AST scan instead:

* extraction of translatable strings -- `_("...")` and `ngettext("...", "...", n)`
  calls whose arguments are string literals;
* merging them into the template and each catalogue, keeping every existing
  translation and marking newly discovered strings as untranslated, while
  dropping msgids that no longer exist anywhere in the code.

It is idempotent: running it twice changes nothing.

Usage:  python tools/sync_pot.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import msgfmt  # noqa: E402  (same-directory sibling import)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
LOCALE_DIR = PROJECT_ROOT / "resources" / "locale"
POT_FILE = LOCALE_DIR / "vpn-split-tunnel.pot"

_POT_HEADER = """# SOME DESCRIPTIVE TITLE.
# Copyright (C) YEAR THE PACKAGE'S COPYRIGHT HOLDER
# This file is distributed under the same license as the PACKAGE package.
#
#: src/vpn_split_tunnel
msgid ""
msgstr ""
"Project-Id-Version: vpn-split-tunnel 0.1.0\\n"
"Report-Msgid-Bugs-To: https://github.com/sina/vpn-split-tunnel/issues\\n"
"POT-Creation-Date: {date}\\n"
"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\\n"
"Last-Translator: FULL NAME <EMAIL@ADDRESS>\\n"
"Language-Team: LANGUAGE <LL@li.org>\\n"
"Language: \\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=INTEGER; plural=EXPRESSION;\\n"

"""


@dataclass
class Extracted:
    """One translatable string found in the source, with its first reference."""

    msgid: str
    msgid_plural: str | None = None
    ref: str = ""

    @property
    def key(self) -> str:
        base = self.msgid
        if self.msgid_plural:
            base = self.msgid + "\x00" + self.msgid_plural
        return base


def _is_literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract_strings() -> list[Extracted]:
    """Scan the source tree for _() / ngettext() calls with literal arguments."""
    found: dict[str, Extracted] = {}

    for py in sorted(SRC_DIR.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            call = node
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Name) and func.id == "_":
                if len(call.args) != 1:
                    continue
                msgid = _is_literal_string(call.args[0])
                if msgid is None:
                    continue
                extracted = Extracted(msgid=msgid)
            elif isinstance(func, ast.Name) and func.id == "ngettext":
                if len(call.args) < 2:
                    continue
                singular = _is_literal_string(call.args[0])
                plural = _is_literal_string(call.args[1])
                if singular is None or plural is None:
                    continue
                extracted = Extracted(msgid=singular, msgid_plural=plural)
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == "ngettext"
                and isinstance(func.value, ast.Name)
                and func.value.id in ("gettext", "i18n")
            ):
                if len(call.args) < 2:
                    continue
                singular = _is_literal_string(call.args[0])
                plural = _is_literal_string(call.args[1])
                if singular is None or plural is None:
                    continue
                extracted = Extracted(msgid=singular, msgid_plural=plural)
            else:
                continue

            key = extracted.key
            if key not in found:
                rel = py.relative_to(PROJECT_ROOT).as_posix()
                extracted.ref = f"{rel}:{node.lineno}"
                found[key] = extracted

    return sorted(found.values(), key=lambda e: (e.msgid, e.msgid_plural or ""))


# ----------------------------------------------------------------------
# .po text generation
# ----------------------------------------------------------------------
def _escape_po(value: str) -> str:
    """Escape a string for a `msgid "..."` line."""
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    return "".join(out)


def _entry_text(extracted: Extracted, msg: msgfmt.Message | None) -> list[str]:
    lines: list[str] = []
    if extracted.ref:
        lines.append(f"#: {extracted.ref}")
    msgstr = msg.translated if msg is not None else ""
    if extracted.msgid_plural is None:
        lines.append(f'msgid "{_escape_po(extracted.msgid)}"')
        if msg is not None and msg.msgstr:
            lines.append(f'msgstr "{_escape_po(msg.msgstr)}"')
        else:
            lines.append('msgstr ""')
    else:
        lines.append(f'msgid "{_escape_po(extracted.msgid)}"')
        lines.append(f'msgid_plural "{_escape_po(extracted.msgid_plural)}"')
        parts = msg.plural_msgstr if msg is not None else []
        for idx in range(2):
            value = parts[idx] if idx < len(parts) else ""
            lines.append(f'msgstr[{idx}] "{_escape_po(value)}"')
    lines.append("")
    return lines


def render_pot(extracted: list[Extracted], date: str) -> str:
    body: list[str] = []
    for entry in extracted:
        body.extend(_entry_text(entry, None))
    return _POT_HEADER.format(date=date) + "\n".join(body)


# ----------------------------------------------------------------------
# catalogue merging
# ----------------------------------------------------------------------
def split_po_prefix(text: str) -> tuple[str, str]:
    """Split .po text into (prefix through header msgstr, rest).

    The prefix keeps all file-level comments and the header entry, untouched.
    The first real entry's ``#:`` reference comments are *not* file-level
    comments: they belong to that entry and are regenerated from the source
    scan, so they live in ``rest`` -- otherwise every re-sync would append one
    more copy of them to the "prefix" and the file would drift on each run.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = msgfmt._TOKEN.match(line.strip())
        if m and m.group(1) == "msgid" and m.group(2).strip() != '""':
            # First real entry starts at line i. Everything before it is the
            # header entry followed by the first entry's reference comments.
            # Drop both the trailing blank separators and those "#:" comments
            # (they are regenerated from the source scan): keeping them would
            # make every re-sync append one more copy and the file drift.
            prefix_lines = lines[:i]
            while prefix_lines and prefix_lines[-1].strip() == "":
                prefix_lines.pop()
            while prefix_lines and prefix_lines[-1].strip().startswith("#:"):
                prefix_lines.pop()
            while prefix_lines and prefix_lines[-1].strip() == "":
                prefix_lines.pop()
            # Keep a single blank line between the header and the body.
            return "\n".join(prefix_lines) + "\n\n", "\n".join(lines[i:])
    return text, ""


def render_po(prefix: str, extracted: list[Extracted], existing: dict[str, msgfmt.Message]) -> str:
    body: list[str] = []
    for entry in extracted:
        body.extend(_entry_text(entry, existing.get(entry.key)))
    return prefix + "\n".join(body) + "\n"


def sync_catalogue(po_path: Path, extracted: list[Extracted]) -> None:
    raw = po_path.read_text(encoding="utf-8")
    prefix, rest = split_po_prefix(raw)
    existing = {m.key: m for m in msgfmt._parse_po(rest)}
    po_path.write_text(render_po(prefix, extracted, existing), encoding="utf-8")
    print(f"synced {po_path.relative_to(PROJECT_ROOT)} ({len(existing)} kept translations)")


def main() -> int:
    extracted = extract_strings()
    pot = render_pot(extracted, "2026-01-01 00:00+0000")
    POT_FILE.write_text(pot, encoding="utf-8")
    print(f"wrote {POT_FILE.relative_to(PROJECT_ROOT)} with {len(extracted)} msgids")

    for po in sorted(LOCALE_DIR.glob("*/LC_MESSAGES/*.po")):
        sync_catalogue(po, extracted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())