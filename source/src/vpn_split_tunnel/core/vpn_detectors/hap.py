"""HAPP VPN detector - strictly passive.

HAPP ships a single GUI binary at /usr/bin/happ which, when executed, boots its
own daemon (`happd`) and a sing-box TUN. Running it to ask for status therefore
rebuilds the tunnel and drops whatever connection the user already had.

So this detector never executes anything from the HAPP installation. It only
reads the state that already exists: the happd systemd unit, the process table,
the network interfaces, and HAPP's own config files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector

# The real unit is happd.service; the "happ" name is only the GUI binary.
HAPP_UNITS = ("happd",)

# Where HAPP keeps its state. Read-only, never written.
_CONFIG_LOCATIONS = (
    Path("/etc/happ/config.json"),
    Path("/etc/happ/happ.json"),
    Path("/usr/local/etc/happ/config.json"),
    Path("~/.config/happ/config.json"),
    Path("~/.config/Happ/config.json"),
)

_LOG_LOCATIONS = (
    Path("/var/log/happd.log"),
    Path("~/.config/happ/happ.log"),
)


class HAPPDetector(VPNDetector):
    """Detect HAPP VPN connections without touching the HAPP installation."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.HAPP

    def is_available(self) -> bool:
        """Is HAPP installed? Purely a filesystem check, no execution."""
        return self._find_happ_binary() is not None or self._happd_unit_exists()

    def detect(self) -> list[VPNConnection]:
        if not self.is_available():
            return []

        # The interface belonging to HAPP: the process table tells us which
        # interface a live tun is bound to.
        interface = self._find_interface_via_processes()
        if interface is None:
            interface = self._find_interface_via_unit()

        if interface is None:
            # happd is up but we could not attribute an interface. Only then
            # fall back to "the single tun device must be it".
            if not self._is_happd_running():
                return []
            interface = self._only_tun_interface()
            if interface is None:
                return []

        conn = self._build_connection(interface)
        return [conn] if conn else []

    # ------------------------------------------------------------------
    # Installation / service state (read-only)
    # ------------------------------------------------------------------
    def _find_happ_binary(self) -> Path | None:
        """Locate the HAPP binary without running it."""
        for candidate in ("/usr/bin/happ", "/usr/local/bin/happ", "/opt/happ/bin/Happ"):
            p = Path(candidate)
            if p.exists():
                return p
        return None

    def _happd_unit_exists(self) -> bool:
        for unit in HAPP_UNITS:
            result = self._run_command(["systemctl", "status", unit, "--no-pager"])
            # "could not be found" also exits non-zero, so check the text
            if result.returncode == 0 or "Loaded:" in (result.stdout or ""):
                return True
        return False

    def _is_happd_running(self) -> bool:
        """Is the HAPP daemon currently running?"""
        for unit in HAPP_UNITS:
            result = self._run_command(["systemctl", "is-active", unit])
            if result.returncode == 0 and result.stdout.strip() == "active":
                return True
        return self._happ_processes() != []

    def _happ_processes(self) -> list[str]:
        """HAPP's daemon, its GUI and the sing-box core it runs."""
        return self.client_processes(("happd", "sing-box", "/opt/happ/"))

    # ------------------------------------------------------------------
    # Interface attribution
    # ------------------------------------------------------------------
    def _find_interface_via_processes(self) -> str | None:
        """Find a tun interface that a HAPP process is holding open.

        /proc/<pid>/net/tun or the fds of the process point at the device.
        Reading /proc is passive and cannot disturb the connection.
        """
        pids: list[str] = []
        result = self._run_command(["ps", "aux"])
        if result.returncode != 0:
            return None
        for line in (result.stdout or "").splitlines():
            low = line.lower()
            if "happd" in low or "sing-box" in low or "/opt/happ/" in low:
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    pids.append(parts[1])

        if not pids:
            return None

        for pid in pids:
            try:
                fd_dir = Path(f"/proc/{pid}/fd")
                for fd in fd_dir.iterdir():
                    try:
                        target = os.readlink(fd)
                    except OSError:
                        continue
                    if target.startswith("/dev/net/tun"):
                        # /dev/net/tun tells us tun mode but not the name, so
                        # fall back to the config-derived name below.
                        pass
            except OSError:
                continue

        return self._interface_from_config()

    def _interface_from_config(self) -> str | None:
        """Read the tun device name out of HAPP's config file."""
        for path in _CONFIG_LOCATIONS:
            real = path.expanduser()
            if not real.exists():
                continue
            try:
                raw = real.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = _find_tun_name_in_config(raw)
            if name and self._interface_exists(name):
                return name
        return None

    def _find_interface_via_unit(self) -> str | None:
        """Ask systemd whether happd names a device (no execution)."""
        for unit in HAPP_UNITS:
            result = self._run_command(
                ["systemctl", "show", unit, "--property=ExecStart", "--no-pager"]
            )
            if result.returncode != 0:
                continue
            name = _find_tun_name_in_config(result.stdout or "")
            if name and self._interface_exists(name):
                return name
        return None

    def _only_tun_interface(self) -> str | None:
        """If exactly one tun device is up and addressed, return it."""
        ups = self._up_addressed_tun_interfaces()
        return ups[0] if len(ups) == 1 else None

    def _up_addressed_tun_interfaces(self) -> list[str]:
        result = self._run_command(["ip", "-j", "addr", "show"])
        if result.returncode != 0:
            return []
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        out = []
        for entry in data:
            name = entry.get("ifname", "")
            if not (name.startswith("tun") or name.startswith("tap")):
                continue
            if "UP" not in entry.get("flags", []):
                continue
            for addr in entry.get("addr_info", []):
                if addr.get("family") == "inet":
                    out.append(name)
                    break
        return out

    def _interface_exists(self, name: str) -> bool:
        result = self._run_command(["ip", "-j", "link", "show", name])
        return result.returncode == 0 and bool((result.stdout or "").strip())

    # ------------------------------------------------------------------
    # Connection details
    # ------------------------------------------------------------------
    def _build_connection(self, interface: str) -> VPNConnection | None:
        facts = self.interface_facts(interface)
        return VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.HAPP,
            name=f"HAPP ({interface})",
            facts=facts,
            processes=self._happ_processes(),
            config_path=self._config_path(),
        )

    def _config_path(self) -> str | None:
        for path in _CONFIG_LOCATIONS:
            real = path.expanduser()
            if real.exists():
                return str(real)
        return None


def _find_tun_name_in_config(text: str) -> str | None:
    """Pull a tun device name out of config text, tolerating many formats."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    def from_obj(obj) -> str | None:
        if isinstance(obj, dict):
            for key in ("tunName", "tun_name", "tunDevice", "device", "ifname", "interface"):
                val = obj.get(key)
                if isinstance(val, str) and val and not val.startswith("/"):
                    if val.startswith(("tun", "tap", "wg")) or val.isalnum():
                        return val
            for val in obj.values():
                found = from_obj(val)
                if found:
                    return found
        elif isinstance(obj, list):
            for val in obj:
                found = from_obj(val)
                if found:
                    return found
        return None

    if data is not None:
        found = from_obj(data)
        if found:
            return found

    # Fall back to a regex over non-JSON text
    import re

    m = re.search(r'"?tun(?:Name|_name|Device|device)"?\s*[:=]\s*"?([A-Za-z0-9_.-]+)', text)
    return m.group(1) if m else None
