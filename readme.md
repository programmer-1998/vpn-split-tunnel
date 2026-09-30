# VPN Split Tunnel

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL%20v3%2B-blue.svg)](https://www.gnu.org/licenses/gpl-3.0.html)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![GTK4](https://img.shields.io/badge/GTK-4.10+-green.svg)](https://gtk.org/)
[![libadwaita](https://img.shields.io/badge/libadwaita-1.4+-orange.svg)](https://gnome.pages.gitlab.gnome.org/libadwaita/)

**Universal VPN Split Tunneling Manager for Linux** — a GTK4 / libadwaita
graphical application that routes *selected* applications, domains and IP
ranges through your VPN while everything else stays on the direct connection
(or the exact opposite), across every major VPN client — without ever starting,
stopping or reconfiguring your VPN itself.

The application is fully localized in **English** and **Persian (RTL)**.

> 📖 **فارسی:** [نسخهٔ فارسی این مستند را اینجا بخوانید](readme.fa.md)

---

## Table of Contents

- [The Problem](#the-problem)
- [The Solution](#the-solution)
- [Features](#features)
- [UI Reference — every menu, section, field, input and button](#ui-reference)
  - [The main window](#the-main-window)
  - [1. VPN Connection page](#1-vpn-connection-page)
  - [2. Split Tunneling Mode page](#2-split-tunneling-mode-page)
  - [3. Applications page](#3-applications-page)
  - [4. Domains and IP Addresses page](#4-domains-and-ip-addresses-page)
  - [5. Status and Actions page](#5-status-and-actions-page)
  - [Preferences dialog](#preferences-dialog)
  - [About dialog](#about-dialog)
- [How It Works](#how-it-works)
- [Privacy & Safety Guarantees](#privacy--safety-guarantees)
- [Installation](#installation)
- [Dependencies](#dependencies)
- [Configuration File](#configuration-file)
- [Command Line](#command-line)
- [Testing & Development](#testing--development)
- [Contributing](#contributing)
- [Author](#author)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## The Problem

Linux VPN clients are mostly **all-or-nothing**. Almost every client can push
all of your traffic through the tunnel, or none of it — but very few let you
tunnel *part* of it:

- You want your **browser and terminal on the VPN**, while a game, a download
  or a local service uses your **direct** connection — or exactly the reverse.
- You want **bank and government sites** (`.ir`, `gov.uk`, `ch` …) to bypass
  the VPN while the rest of your traffic goes through it.
- You want a streaming or banking site to **never see your VPN IP address**.
- Clients that do offer "split tunneling" only support their **own** application,
  only apps they happen to know about, or only work from a CLI with hand-written
  firewall rules.

Fixing this by hand means writing `nftables` rules, `ip rule` policy routing
and — because the Linux kernel can only tell one process's sockets from another
by **cgroup** — manual `cgroup v2` setup. One small mistake silently leaks
traffic the wrong way: the whole point of split tunneling is that the two sides
never mix, and a single bad rule mixes them.

## The Solution

**VPN Split Tunnel** is a graphical manager (GTK4 + libadwaita) that does the
hard part for you:

1. **Finds** every VPN tunnel that is *up right now* on your machine by reading
   the live system — never from a hardcoded vendor list.
2. **Lets you choose** a target tunnel, a mode, the applications, the domains
   and the IP/CIDR ranges in plain, clickable controls.
3. **Shows you what will happen before anything changes** — a preview computed
   from your actual routing table, with no password prompt and no system change
   until you ask for it.
4. **Builds and installs** the `nftables` + policy-routing rules with one admin
   prompt (polkit).
5. **Removes exactly what it installed** (and nothing else) when you press
   *Remove Rules*.

The app is **strictly read-only about your VPN**: it never connects,
disconnects, restarts or reconfigures a tunnel — that stays the job of your own
VPN client. It only sees what is already running and manages its own rules on
top.

## Features

### 🔐 Multi-VPN support (live detection)
Every tunnel actually up is offered — detected from the system, not a list:
**WireGuard, OpenVPN, Tailscale, Xray / Sing-box / Clash.Meta (VLESS/VMESS/
Trojan), Windscribe, HAP, NetworkManager VPNs, systemd-networkd, Proton-style
tunnels**, and anything else tunnel-shaped — which can also be added **manually**
by interface name if the detectors miss it.

### 🎯 Two split-tunneling modes
- **Include Mode (Split-Include):** only the selected applications, domains and
  IP ranges go through the VPN; everything else uses the direct connection.
- **Exclude Mode (Inverse / Reverse Split):** everything goes through the VPN
  **except** the selected applications, domains and IP ranges.

### 🗂️ All your installed applications in one place — and you pick
The application list comes from **your system**, not from a hardcoded set: every
installed program (from your `.desktop` files) appears in one searchable list,
and **you** choose which ones the tunnel applies to. When a VPN client offers
split tunneling at all, it usually covers only its own app or a few programs it
happens to know about. Letting you pick **any** of your installed apps yourself
is a capability very few desktop programs — VPN or otherwise — offer.

### 🖥️ Per-application routing (cgroup v2)
Traffic is told apart per application by the **cgroup** the process runs in. The
app starts applications inside its own systemd slice, so the kernel — and the
rules — can match them exactly.

### 🌐 Domains, IPs and CIDR ranges
Plain-text list editor with per-entry edit/remove, validation, file import and
export.

### 🔒 Security by design
PolicyKit escalation through a **dedicated helper** that can only run `ip` and
`nft`; the rules carry counters so the app always knows exactly which ones it
owns.

### 🎨 UI/UX
GTK4 + libadwaita (looks native on GNOME, works on KDE/XFCE and any desktop),
light/dark/system theme, English and Persian (RTL), search, tooltips on every
control.

---

## UI Reference

### The main window

A **sidebar** on the left (260 px) with the app icon, title and subtitle; a
**navigation list** with the five sections; and at the bottom the **version**
(`v0.1.0`), *"Developed by Sina Khanzadeh"* and the developer's social links
(Telegram, Discord, Instagram, Website, Email — selectable text).

The **header bar** shows the current section's title/subtitle (it follows your
navigation). The **window menu** (hamburger icon) offers:

- **Preferences** — opens the Preferences dialog (see below).
- **About** — opens the About dialog (see below).
- **Quit** — closes the application.

---

### 1. VPN Connection page

*"Detected VPN status and details"* — shows every tunnel that is up right now.

- **Detected VPNs group:**
  - **Summary row** — *"Scanning…"* while a scan runs, *"N connections
    detected"* when tunnels are found, or *"Not connected to any VPN"*.
  - **Refresh button** (↻ icon, tooltip *"Scan again"*) — re-scans the system
    for VPNs. It also reloads the installed-application list, so a newly
    installed app appears too.
- **Connections group** — one expander row per detected connection:
  - **Row title** — the VPN client name (e.g. *"HAPP"*, *"Tailscale"*); for an
    unrecognised tunnel it is *"Unknown VPN (tun0)"* style.
  - **Row subtitle** — a one-line summary: `interface • addresses • via gateway`.
  - **"Use this VPN"** button (green, suggested action, tooltip *"Send the split
    tunnel through this VPN"*) — marks this tunnel as the target your rules will
    apply to. Once targeted, the button disables, its label becomes *"Rules
    target this VPN"*, and a small **(✓) badge** appears on the row.
  - **Detail rows** (click the expander arrow): *Interface, Protocol, IP
    Addresses, Tunnel Peer, Gateway, DNS Servers, MTU, Processes, Also seen as,
    Config File*. Addresses and file paths are **selectable** so you can copy
    them.
- **Manual entry group** — for tunnels the detectors did not recognise:
  - **"Add a VPN manually"** row with a **+ button** (tooltip *"Register a
    tunnel the detectors missed"*). It opens a dialog with a **text field**
    (pre-filled with the first candidate tunnel interface found in `ip link`)
    and **Cancel / Add** buttons. The interface you type (e.g. `tun0`, `wg0`)
    gets the same full details a scanned tunnel would.
- **Empty state** (no VPN detected): a large faded icon, *"Not connected to any
  VPN"* and a pill **"Add a VPN manually"** button.

> Nothing on this page starts a VPN client. It renders what the passive
> detectors already learned from the system.

---

### 2. Split Tunneling Mode page

*"Choose how split tunneling should work"* — one group, two choices.

- **Include Mode (Split-Include)** radio — *"Only selected applications and
  domains go through VPN. Everything else uses direct connection."*
- **Exclude Mode (Inverse Split/Reverse)** radio — *"Everything goes through
  VPN EXCEPT selected applications and domains. They use direct connection."*

Clicking either row selects its radio button. This is the default page the
preview on the Status page explains in plain language.

---

### 3. Applications page

*"Select applications to include or exclude from the VPN tunnel."*

- **Installed Applications group:**
  - **"All System (apply to all traffic)"** check button — checked **by
    default**; applies the split tunnel to all system traffic. While it is
    checked, the individual app list and search are disabled.
  - **Search entry** (*"Search applications…"*) — filters the list live by
    application name or desktop id.
  - **Application list** (scrollable) — one row per installed application:
    - **App icon + display name + desktop id** subtitle.
    - **Selection check** (tooltip *"Select this application for the split
      tunnel"*) — adds/removes the app from the policy.
    - **Start button** (▶ play icon, tooltip *"Start this application so its
      traffic can be told apart"*) — starts the application **inside** the
      app's own systemd slice (`vpn-split-tunnel.slice`), which is the only way
      the kernel can match its traffic later. A helper banner explains if the
      app is already running elsewhere: close it first, then start it from here.
    - Once a selected app is running in the slice, its start button becomes a
      disabled **✓ emblem**, the row is highlighted, and the tooltip becomes
      *"This application is running where the split tunnel can see it"*.
  - **Selected count row** — *"All system traffic (default)"*, or *"N
    applications selected"* followed by *"· M started"* or *"· none started yet,
    so none can be matched"* — the second number is the one that decides whether
    the policy can actually tell that app apart.
- **Skipped entries group** (only when it happens) — desktop files that could
  not be read are listed here with one reason per row, instead of silently
  shrinking the list.
- **Notice group** (only when it happens) — a persistent warning (not a
  disappearing toast) when a launched application did not land where the rules
  can match it.

---

### 4. Domains and IP Addresses page

*"Manage domains and IP ranges."* — *"Enter domains or IP addresses (one per
line). Supports CIDR notation for IP ranges. Empty by default."*

- **Add Domain row:**
  - **Text entry** (placeholder `example.com`) — type a domain and press
    **Enter**.
  - **"Add" button** (tooltip *"Add the domain above to the list"*).
  - Duplicates are ignored.
- **Add IP Address / CIDR row:**
  - **Text entry** (placeholder `192.168.1.0/24 or 10.0.0.1`) — validated; an
    invalid entry opens an *"Error"* dialog (*"Invalid IP address or CIDR
    notation"*).
  - **"Add" button** (tooltip *"Add the IP address or CIDR above to the list"*).
- **Actions row:**
  - **"Clear All"** button (destructive style, tooltip *"Remove every domain and
    IP from the list"*) — asks for confirmation first.
  - **"Import from file"** button (tooltip *"Load domains and IPs from a text
    file"*) — opens a file picker; each non-empty, non-`#` line is added and
    auto-classified as IP/CIDR or domain.
  - **"Export to file"** button (tooltip *"Save the current domains and IPs to
    a text file"*) — writes a commented text file with `# Domains` and `# IP
    Addresses / CIDR` sections.
- **Current List group:**
  - One row per entry with a **type badge** — **Domain** / **IP** / **CIDR**.
  - **Edit button** (✎ icon, tooltip *"Edit this entry"*) — dialog with a text
    field and **Cancel / Save**.
  - **Remove button** (✕/− icon, tooltip *"Remove this entry"*).
  - Empty state label when nothing is added.

---

### 5. Status and Actions page

*"Apply or remove split tunneling rules."* — the page that tells you the truth
before you commit, and the page you act from.

- **Current Rules card** — *"What will be applied when you press Apply Rules"*:
  - **Target VPN** — the selected tunnel as *"client (interface)"* or *"None
    selected"*.
  - **Mode** — spelled out: *"Only the selected apps and addresses go through
    the VPN"* (Include) or *"Everything goes through the VPN except the selected
    apps and addresses"* (Exclude).
  - **Applications** — *"All system traffic"* or *"N applications"*.
  - **Domains and IP Addresses** — *"None"* or *"N domains, N IP ranges"*.
- **What This Will Do card** — *"Calculated from your current system routing
  table, before anything is changed"*:
  - **Result** — computed by the routing engine with **no privileged command and
    no password prompt**: what marked traffic will use (the tunnel or your
    normal route), how many selected applications are running where the rules
    can match them, and any domains that could not be resolved.
  - **Things To Know** (a separate group, visible only when it matters) — one
    *"Note"* row per warning, e.g.: your VPN client's terminal routing rules run
    before ours (lower the *Rule Priority* in Preferences), the client already
    tunnels everything so Include mode cannot pull the rest out, or a selected
    app is not running in a matchable group yet.
- **Actions card:**
  - **Status line** — *"Status: Ready / Applying… / Active / Error"* plus a
    detail line such as *"Ready to apply to wg0"*, *"Applied to wg0"*, *"No VPN
    selected"*, or the error text.
  - **Remove Rules** (destructive red, tooltip *"Undo the rules this app
    applied"*) — deletes everything the app installed (nftables table, the `ip
    rule`, the route table); asks for the admin password. Enabled whenever the
    rules are active.
  - **Apply Rules** (green suggested, tooltip *"Install the split-tunnel rules
    into the live network (asks for admin permission)"*) — builds and installs
    the rules; asks for the admin password. Enabled only when a VPN is selected
    **and** something is selected (apps, domains, IPs).
  - **Reset to Defaults** (pill button, tooltip *"Clear the form and restore the
    initial settings"*) — confirmation dialog, then: mode back to **Include**,
    **All System** selected, domains/IPs cleared, and any active rules removed.

> Desktop notifications confirm each Apply / Remove (toggle in Preferences).

---

### Preferences dialog

Opened from the window menu. Three pages:

**General → Behavior**
- **Auto-detect VPN** switch (on by default) — scan for active VPNs on start.
- **Apply rules on startup** switch — re-apply the last used rules when the app
  starts.
- **Show notifications** switch (on by default) — desktop notifications when
  rules are applied or removed.

**Appearance → Theme**
- **Theme** dropdown — **System / Light / Dark** (applies immediately).
- **Language** dropdown — **System / English / Persian** (needs a restart; the
  whole UI flips to RTL in Persian).

**Network → Routing Configuration** (advanced values the rules are built from)
- **VPN Interface** — text field, placeholder `tun0` (e.g. `tun0`, `wg0`,
  `xray_tun`).
- **Routing Table ID** — spin box, 1–255, default **101** (policy routing table).
- **Firewall Mark** — spin box, 1–2147483647, default **5555** (the `fwmark`
  value the rules set).
- **Rule Priority** — spin box, 1–32766, default **8500**. ⚠ *Lower runs first;
  it must sit **below** your VPN client's own rules or they take precedence and
  these never apply.*
- **Cgroup Slice** — text field, default **`vpn-split-tunnel.slice`** (the
  systemd slice used for cgroup v2 per-app matching).

### About dialog

Application name & icon, **version**, developer *"Sina Khanzadeh"*, license
**GPL-3.0-or-later**, website, issue tracker, and the developer's contact
information.

---

## How It Works

```
                 ┌─────────────────────────────────────────────┐
                 │          VPN Split Tunnel (GUI)             │
                 │  live VPN scan   policy form   plan/preview  │
                 └───────────────┬─────────────────────────────┘
                                 │  apply / remove (admin prompt)
                    ┌────────────▼─────────────┐
                    │  polkit                  │
                    │  com.github.sina.         │
                    │   vpn-split-tunnel.       │
                    │   {apply,remove}-policy   │
                    └────────────┬─────────────┘
                                 ▼
                 /usr/libexec/vpn-split-tunnel-helper
                 (root; may execute ONLY:  ip,  nft)
```

1. **Detection (passive).** Detectors read `/sys/class/net`, `ip -o link show`,
   interface addressing and routes, and `systemctl` — for WireGuard, OpenVPN,
   Tailscale, Xray/Sing-box, Windscribe, HAP, NetworkManager, systemd-networkd
   and a catch-all for "any tunnel-shaped interface". Nothing is ever started.

2. **Plan without touching anything.** `plan()` reads `ip rule show`
   (unprivileged), computes the exact rule set and the warnings (e.g. your
   client's `nop` rules shadowing ours), and renders the preview — so you see
   reality before any password prompt.

3. **Apply (via polkit + the helper).** The helper runs only allowlisted
   programs (`ip`, `nft`) and checks them again itself.
   - **nftables** table `inet vpn_split_tunnel`:
     - `mark_output` chain (route hook, priority mangle) marks traffic selected
       by policy. It skips loopback and the tunnel's own interface; applications
       are matched by `socket cgroupv2 level N "path"`, IP/CIDRs by `ip daddr`,
       and domains through named address sets `vpn_addrs` / `vpn_addrs6`
       (resolved at apply time). Every rule carries a counter.
     - In **Include** mode a `kill_postrouting` chain drops marked traffic that
       would leave the machine *outside* the tunnel (leak protection). Exclude
       mode needs no kill switch.
   - **Policy routing.** Marked traffic is sent by one `ip rule` at the
     configured priority: `fwmark <mark>/0xFF0000 lookup <our-table>` (Include)
     or `lookup main` (Exclude, i.e. the direct route). The mark is masked with
     `0xFF0000` so other software's marks (Tailscale `0x80000`, WireGuard
     `0xca6c`) never collide with ours. IPv4 and IPv6 both get rules; Include
     mode adds a table whose default route points **via the tunnel's real peer**,
     read from the kernel — never from a configured value.

4. **Per-application launch.** The play button runs
   `systemd-run --user --unit=app-<name> --slice=vpn-split-tunnel.slice <cmd>`.
   The resulting cgroup path is read back from systemd; nftables rules are only
   emitted for cgroups that exist **and** hold live processes — an nftables rule
   naming a missing cgroup would reject the whole table.

5. **Remove.** Deletes the named nftables table and, by exact
   priority/mark/target, the `ip` rules, then flushes the route table (v4 + v6).
   Every artefact is addressed precisely, so nothing belonging to the VPN client
   or another tool can be removed by accident.

## Privacy & Safety Guarantees

- **Read-only about your VPN.** The app never adds addresses to your tunnel
  interface, never sets it up/down, and never replaces the VPN client's routes.
- **Least privilege.** polkit grants are scoped to this application's helper
  (`org.freedesktop.policykit.exec` annotated for
  `/usr/libexec/vpn-split-tunnel-helper`); apply and remove are two separate
  actions. The helper's allowlist is only `ip` and `nft`.
- **Clean removal.** *Remove Rules* restores the system to exactly the state
  before *Apply Rules*.
- **Nothing runs in the background** and nothing runs at login.
- Works on **any desktop** (GNOME, KDE, XFCE, …); on KDE the admin prompt is
  shown by the KDE polkit agent.

## Installation

### Option A — Debian package (`.deb`) — Debian / Ubuntu / Zorin / Mint …

A ready-made package accompanies this repository — `vpn-split-tunnel_0.1.0_all.deb`
(no build step needed):

```bash
sudo apt install ./vpn-split-tunnel_0.1.0_all.deb    # recommended – resolves dependencies
# or: sudo dpkg -i vpn-split-tunnel_0.1.0_all.deb
```

### Option B — From source (meson)

```bash
git clone https://github.com/programmer-1998/vpn-split-tunnel.git
cd vpn-split-tunnel
meson setup builddir
sudo meson install -C builddir      # root privileges
```

Installs: the Python package (`/usr/lib/python3/dist-packages/vpn_split_tunnel`),
the launcher (`/usr/bin/vpn-split-tunnel`), the privileged helper
(`/usr/libexec/vpn-split-tunnel-helper`), the `.desktop` entry, app icon,
metainfo, the polkit policy, bash completion, the stylesheet and the Persian
catalogue (`/usr/share/locale/fa/LC_MESSAGES/vpn-split-tunnel.mo`).

### Launching

- From the application menu: **VPN Split Tunnel** (shown as its Persian name in
  Persian sessions).
- From a terminal: `vpn-split-tunnel` (supports `--version` and `--help`).

No gettext tools are needed anywhere in the build — translation files are
compiled by bundled scripts (`tools/msgfmt.py`, `tools/sync_pot.py`).

## Dependencies

**Runtime:** Python 3.10+ · GTK 4.10+ (`gir1.2-gtk-4.0`) · libadwaita 1.4+
(`gir1.2-adw-1`) · `python3-gi` + `gir1.2-glib-2.0`/`gir1.2-gio-2.0` ·
`nftables` · `iproute2` · systemd (cgroup v2 + `systemd-run`) · polkit +
`pkexec`. All are in the standard repositories of every major distribution.

**Build:** meson 1.2+ and Python 3.10+ (the `.po`→`.mo` compiler is a bundled
Python script).

## Configuration File

Settings are saved to `~/.config/vpn-split-tunnel/config.ini` (created
automatically, XDG-compliant):

```ini
[routing]
tun_device = tun0                        # last used tunnel interface
fwmark = 5555                            # firewall mark
routing_table = 101                      # policy routing table id
rule_priority = 8500                     # MUST be lower than your VPN client's rules
cgroup_slice = vpn-split-tunnel.slice    # systemd slice for cgroup v2

[policy]
mode = include                           # include | exclude

[ui]
theme = system                           # system | light | dark
language = system                        # system | en | fa
```

The one key that matters most: **`rule_priority`** — policy rules with a lower
number run first. If your VPN client installs rules below this value, they take
precedence and these never apply; the app detects this and warns you.

## Command Line

```
vpn-split-tunnel [--version] [--help]
```

- `--version` — prints the version and exits.
- `--help` — GLib's standard option help.

## Testing & Development

```bash
cd vpn-split-tunnel
python3 -m venv venv && source venv/bin/activate
pip install pytest pygobject
bash build.sh test          # or: PYTHONPATH=src python3 -m pytest tests/ -q
```

Run without installing:

```bash
PYTHONPATH=src python3 -m vpn_split_tunnel
```

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Make your changes.
4. Run tests: `bash build.sh test` (or `PYTHONPATH=src python3 -m pytest tests/ -q`).
5. Run the linter: `ruff check .`
6. Open a pull request.

**Translations** live in `resources/locale/<lang>/LC_MESSAGES/`. Sync the
catalogue with the code (no gettext needed):
`python3 tools/sync_pot.py`, then edit the `.po` file and compile it with
`python3 tools/msgfmt.py <lang>.po <lang>.mo`.

## Author

**VPN Split Tunnel** is developed by **Sina Khanzadeh** — the same information
you will find in the app's About dialog and sidebar:

| Contact | Handle |
|---|---|
| Telegram | `@programmer_1998` |
| Discord | `@programmer_1998` |
| Instagram | `@programmer_1998` |
| Website | [sina-khanzadeh.ir](https://sina-khanzadeh.ir) |
| Email | [khanzadeh.1377@gmail.com](mailto:khanzadeh.1377@gmail.com) |
| Issues | [github.com/programmer-1998/vpn-split-tunnel/issues](https://github.com/programmer-1998/vpn-split-tunnel/issues) |

## License

**GPL-3.0-or-later** — see [LICENSE](LICENSE).

## Acknowledgments

- [netslice](https://github.com/occasion-2/netslice) — the cgroups v2 +
  nftables approach this app is inspired by.
- [Mullvad VPN — advanced split tunneling for Linux](https://mullvad.net/en/help/split-tunneling-with-linux-advanced)
  — documentation of the mechanics.
- [libadwaita](https://gnome.pages.gitlab.gnome.org/libadwaita/) — modern GTK4
  widgets.
- [PyGObject](https://pygobject.gnome.org/) — Python bindings for GTK.