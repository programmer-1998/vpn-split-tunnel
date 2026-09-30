"""Windscribe VPN detector - strictly passive.

Windscribe's CLI is a client: `windscribe connect` / `windscribe up` mutates
the connection, and on some builds invoking the GUI-capable binary at all is
enough to spin the daemon up. So this detector reads only what already exists:
the process table, the interfaces, and the daemon's own config/status files.
"""

from __future__ import annotations

import json
from pathlib import Path

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector

# Read-only status/config files the daemon maintains.
_STATUS_FILES = (
    Path("~/.config/windscribe/windscribe_status"),
    Path("/etc/windscribe/windscribe_status"),
)
_CONFIG_DIRS = (
    Path("~/.config/windscribe"),
    Path("/etc/windscribe"),
)


class WindscribeDetector(VPNDetector):
    """Detect Windscribe VPN connections without running the client."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.WINDSCRIBE

    def is_available(self) -> bool:
        for path in _CONFIG_DIRS:
            if path.expanduser().is_dir():
                return True
        return any(p.expanduser().exists() for p in _STATUS_FILES)

    def detect(self) -> list[VPNConnection]:
        if not self._processes_running():
            return []

        interface = self._find_interface()
        if interface is None:
            return []
        return [c for c in [self._build_connection(interface)] if c]

    # ------------------------------------------------------------------
    def _processes_running(self) -> list[str]:
        """Is a Windscribe daemon or GUI process alive?"""
        return self.client_processes(("windscribe",))

    def _connected_flag(self) -> bool:
        """Whether the daemon reports itself as connected."""
        for path in _STATUS_FILES:
            real = path.expanduser()
            if not real.exists():
                continue
            try:
                raw = real.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if "connected" in raw:
                return True
        return False

    def _find_interface(self) -> str | None:
        """Find the tun device the Windscribe process holds."""
        names = self._interfaces_from_processes()
        for name in names:
            if self._is_up_and_addressed(name):
                return name
        return None

    def _interfaces_from_processes(self) -> list[str]:
        """Interfaces named on a windscribe process line."""
        out: list[str] = []
        for line in self._processes_running():
            for token in line.split():
                if token.startswith(("tun", "tap", "wsc")) and len(token) < 24:
                    if token not in out:
                        out.append(token)
        return out

    def _is_up_and_addressed(self, name: str) -> bool:
        result = self._run_command(["ip", "-j", "addr", "show", "dev", name])
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return False
        for entry in data:
            if "UP" not in entry.get("flags", []):
                continue
            for addr in entry.get("addr_info", []):
                if addr.get("family") == "inet":
                    return True
        return False

    def _build_connection(self, interface: str) -> VPNConnection | None:
        facts = self.interface_facts(interface)
        return VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.WINDSCRIBE,
            name=f"Windscribe ({interface})",
            facts=facts,
            processes=[l.strip() for l in self._processes_running()],
        )
