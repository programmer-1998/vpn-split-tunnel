#!/bin/bash
# Build dist/vpn-split-tunnel_<version>_all.deb from the meson build.
#
# The .deb ships the same files meson installs with --prefix=/usr: the Python
# package under /usr/lib/python3/dist-packages, the launcher, the privileged
# helper, the desktop entry, icon, metainfo, polkit action, bash completion,
# stylesheet and the Persian catalogue. `meson install --destdir` produces
# exactly that layout, so this script only adds the DEBIAN/control file and
# repacks the tree.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VERSION="$(sed -n "s/^.*version: '\([0-9][^']*\)'.*/\1/p" meson.build | head -1)"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: could not read version from meson.build" >&2
    exit 1
fi

PKG="vpn-split-tunnel_${VERSION}_all"
DIST_DIR="$PROJECT_ROOT/dist"
ROOT="$PROJECT_ROOT/deb-root"

log() { echo "[make-deb] $*"; }

# 1. Build (the source tree's changes must land in builddir first).
log "Setting up / compiling with meson (prefix=/usr) ..."
if [[ -d builddir && -f builddir/build.ninja ]]; then
    meson setup --reconfigure builddir --prefix=/usr >/dev/null
else
    meson setup builddir --prefix=/usr >/dev/null
fi
meson compile -C builddir >/dev/null

# 2. Install into a staging root.
log "Installing into staging root ..."
rm -rf "$ROOT"
DESTDIR="$ROOT" meson install -C builddir >/dev/null

# meson's pycompile step writes __pycache__/*.pyc into the tree; the deb
# ships the sources only.
find "$ROOT" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# 3. Control file.
log "Writing DEBIAN/control ..."
mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<EOF
Package: vpn-split-tunnel
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.10),
         python3-gi,
         gir1.2-gtk-4.0 (>= 4.10),
         gir1.2-adw-1 (>= 1.4),
         gir1.2-glib-2.0 (>= 2.76),
         gir1.2-gio-2.0 (>= 2.76),
         pkexec,
         policykit-1
Recommends: bash-completion
Maintainer: Sina Khanzadeh <khanzadeh.1377@gmail.com>
Homepage: https://sina-khanzadeh.ir
Description: Universal VPN split tunneling manager (GTK4 / libadwaita)
 Route chosen applications, domains and IP ranges through a VPN while the
 rest of the system uses the direct connection (or the inverse). Detects
 live VPN tunnels passively (WireGuard, OpenVPN, HAPP, Tailscale,
 Windscribe, NetworkManager, systemd-networkd, Xray, unknown tunnels),
 builds the nftables/ip rules for you and removes them on demand. The
 application never starts or modifies a VPN itself: it only sees what is
 already up and manages its own rules.
EOF

# 4. Repack.
log "Building $DIST_DIR/$PKG.deb ..."
mkdir -p "$DIST_DIR"
dpkg-deb --build --root-owner-group "$ROOT" "$DIST_DIR/$PKG.deb" >/dev/null

# 5. Clean the staging root and report.
rm -rf "$ROOT"
sha256sum "$DIST_DIR/$PKG.deb"
log "Done: $DIST_DIR/$PKG.deb"