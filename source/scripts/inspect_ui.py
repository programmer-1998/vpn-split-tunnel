#!/usr/bin/env python3
"""Walk the live MainWindow widget tree and print it, so the layout can be
verified structurally instead of guessing from a screenshot.

Usage: PYTHONPATH=src python scripts/inspect_ui.py [nav-page]
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from vpn_split_tunnel.app import VPNSplitTunnelApp

MAX_DEPTH = 30


def _txt(w):
    """Best-effort human label for a widget."""
    for getter in (
        "get_title",
        "get_label",
        "get_placeholder_text",
        "get_text",
        "get_icon_name",
    ):
        try:
            v = getattr(w, getter)()
            if v:
                return str(v)
        except Exception:
            pass
    return ""


def describe(w) -> str:
    t = type(w).__name__
    extra = []
    txt = _txt(w)
    if txt:
        extra.append(repr(txt[:60]))
    try:
        extra.append(f"vis={int(w.get_visible())}")
        extra.append(f"sens={int(w.get_sensitive())}")
    except Exception:
        pass
    # Row/subtitle for Adw rows
    try:
        st = w.get_subtitle()
        if st:
            extra.append(f"sub={str(st)[:50]!r}")
    except Exception:
        pass
    # Toggle state
    for g in ("get_active", "get_selected"):
        try:
            v = getattr(w, g)()
            extra.append(f"{g[4:]}={int(bool(v))}")
            break
        except Exception:
            pass
    return f"{t} " + " ".join(extra)


def _call(w, name):
    """Call a getter, returning None on any failure."""
    try:
        return getattr(w, name)()
    except Exception:
        return None


def _single_child(w, *getters):
    for g in getters:
        c = _call(w, g)
        if isinstance(c, Gtk.Widget):
            return [c]
    return []


def _sibling_chain(w):
    out = []
    c = _call(w, "get_first_child")
    while isinstance(c, Gtk.Widget):
        out.append(c)
        c = _call(c, "get_next_sibling")
    return out


def children_of(w):
    """Yield direct children, driven by capability probing rather than by
    isinstance chains, so unknown widget versions cannot crash the dump."""
    out: list[Gtk.Widget] = []

    # Paned exposes two slots
    start = _call(w, "get_start_child")
    end = _call(w, "get_end_child")
    if isinstance(start, Gtk.Widget) or isinstance(end, Gtk.Widget):
        for c in (start, end):
            if isinstance(c, Gtk.Widget):
                out.append(c)
        return out

    # Navigation stack
    stack = _call(w, "get_navigation_stack")
    if stack is not None:
        for page in stack:
            if isinstance(page, Gtk.Widget):
                out.append(page)
        return out

    # Adw rows carry prefix/suffix widgets
    pre = _call(w, "get_prefix_widget")
    suf = _call(w, "get_suffix_widget")
    if isinstance(pre, Gtk.Widget) or isinstance(suf, Gtk.Widget):
        for c in (pre, suf):
            if isinstance(c, Gtk.Widget):
                out.append(c)
        return out

    # Stack: follow the visible child only
    if isinstance(w, Gtk.Stack):
        c = _call(w, "get_visible_child")
        if isinstance(c, Gtk.Widget):
            out.append(c)
        return out

    # Single-child containers
    for g in ("get_child", "get_content"):
        c = _call(w, g)
        if isinstance(c, Gtk.Widget):
            out.append(c)
            return out

    # Multi-child containers
    out.extend(_sibling_chain(w))
    return out


def walk(w, depth=0):
    if w is None or depth > MAX_DEPTH:
        return
    print("  " * depth + describe(w), flush=True)
    for c in children_of(w):
        walk(c, depth + 1)


class InspectApp(VPNSplitTunnelApp):
    def do_activate(self) -> None:
        super().do_activate()
        win = self.props.active_window
        GLib.timeout_add(5000, self.dump, win)

    def dump(self, win) -> bool:
        nav = sys.argv[1] if len(sys.argv) > 1 else None
        if nav:
            row = getattr(win, "nav_rows", {}).get(nav)
            if row is not None:
                win.nav_list.select_row(row)
                GLib.timeout_add(1200, self.dump2, win, nav)
                return False
        self.dump2(win, nav)
        return False

    def dump2(self, win, nav) -> bool:
        print(f"\n===== TREE (page={nav or 'current'}) =====", flush=True)
        walk(win)

        print("\n===== stack children =====", flush=True)
        for child in win.content_stack.observe_children():
            pass
        cc = _stack_names(win)
        for n in cc:
            print("  -", n, flush=True)

        print("\n===== sidebar rows =====", flush=True)
        c = win.nav_list.get_first_child()
        while c:
            print("  -", c.get_title(), "| page_id =", getattr(c, "page_id", "?"), flush=True)
            c = c.get_next_sibling()
        self.quit()
        return False


def _stack_names(win):
    names = []
    s = win.content_stack
    cur = s.get_visible_child()
    n = 0
    while cur and n < 20:
        n += 1
        try:
            name = s.child_name_for_property if False else None
        except Exception:
            pass
        names.append(type(cur).__name__)
        break
    return names


if __name__ == "__main__":
    raise SystemExit(InspectApp().run([]))
