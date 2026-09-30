#!/usr/bin/env python3
"""Render a live MainWindow to PNG so the UI can actually be looked at.

Usage: PYTHONPATH=src python scripts/shoot.py out.png [nav-page]
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from vpn_split_tunnel.app import VPNSplitTunnelApp


def build(out: str, nav: str | None):
    app = ShootApp()
    app._shoot_out = out
    app._shoot_nav = nav
    return app


class ShootApp(VPNSplitTunnelApp):
    def do_activate(self) -> None:
        super().do_activate()
        win = self.props.active_window
        if win is None:
            print("no window", flush=True)
            self.quit()
            return
        # Let the VPN detectors finish so the window shows real data
        GLib.timeout_add(5000, self._capture, win)

    def _capture(self, win) -> bool:
        nav = self._shoot_nav
        if nav:
            row = getattr(win, "nav_rows", {}).get(nav)
            if row is not None:
                win.nav_list.select_row(row)
                GLib.timeout_add(1500, self._snap, win)
                return False
        self._snap(win)
        return False

    def _snap(self, win) -> bool:
        out = self._shoot_out
        w, h = win.get_width(), win.get_height()
        print(f"window size: {w}x{h}", flush=True)
        try:
            paintable = Gtk.WidgetPaintable.new(win)
            snap = Gtk.Snapshot.new()
            paintable.snapshot(snap, w, h)
            node = snap.to_node()
            if node is None:
                print("snapshot produced no node", flush=True)
                self.quit()
                return False
            native = win.get_native()
            if native is None:
                print("no native surface", flush=True)
                self.quit()
                return False
            texture = native.get_renderer().render_texture(node, None)
            texture.save_to_png(out)
            print(f"saved {out}", flush=True)
        except Exception as exc:
            print(f"SNAPSHOT FAILED: {exc!r}", flush=True)
        self.quit()
        return False


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/window.png"
    nav = sys.argv[2] if len(sys.argv) > 2 else None
    return build(out, nav).run([])


if __name__ == "__main__":
    raise SystemExit(main())
