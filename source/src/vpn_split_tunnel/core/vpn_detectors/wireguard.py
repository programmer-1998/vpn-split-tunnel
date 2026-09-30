"""WireGuard detector - passive.

`wg show <iface> dump` is a pure query of the kernel's WireGuard tables, so it
tells us both that a tunnel exists and which peers it has without touching it.
"""

from __future__ import annotations

import json
import shutil

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector


class WireGuardDetector(VPNDetector):
    """Detect active WireGuard connections."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.WIREGUARD

    def is_available(self) -> bool:
        return shutil.which("wg") is not None

    def detect(self) -> list[VPNConnection]:
        interfaces = self._interfaces_from_wg() or self._interfaces_from_kernel()
        out: list[VPNConnection] = []
        for interface in interfaces:
            conn = self._build_connection(interface)
            if conn:
                out.append(conn)
        return out

    # ------------------------------------------------------------------
    def _interfaces_from_wg(self) -> list[str]:
        """Interfaces the kernel's WireGuard tables know about."""
        result = self._run_command(["wg", "show", "all", "dump"])
        if result.returncode != 0:
            return []
        names = []
        for line in (result.stdout or "").splitlines():
            first = line.split("\t")[0].strip()
            if first and first != "interface" and first not in names:
                names.append(first)
        return names

    def _interfaces_from_kernel(self) -> list[str]:
        result = self._run_command(["ip", "-j", "link", "show", "type", "wireguard"])
        if result.returncode != 0:
            return []
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        return [entry["ifname"] for entry in data if entry.get("ifname")]

    def _peers(self, interface: str) -> list[dict[str, str]]:
        """Peers from `wg show dump`: public key, endpoint, allowed IPs."""
        result = self._run_command(["wg", "show", interface, "dump"])
        if result.returncode != 0:
            return []
        peers = []
        for line in (result.stdout or "").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 4:
                peers.append(
                    {
                        "public_key": parts[1],
                        "endpoint": parts[2],
                        "allowed_ips": parts[3],
                    }
                )
        return peers

    def _build_connection(self, interface: str) -> VPNConnection | None:
        facts = self.interface_facts(interface)
        conn = VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.WIREGUARD,
            name=f"WireGuard {interface}",
            facts=facts,
            processes=self.client_processes(("wg-quick", "wireguard")),
        )
        if conn is None:
            return None

        unit = f"wg-quick@{interface}"
        result = self._run_command(["systemctl", "is-active", unit])
        if result.returncode == 0 and result.stdout.strip() == "active":
            conn.config_path = f"/etc/wireguard/{interface}.conf"

        peers = self._peers(interface)
        if peers:
            endpoints = [p["endpoint"] for p in peers if p["endpoint"] and p["endpoint"] != "(none)"]
            if endpoints:
                # A WireGuard "gateway" is the peer's endpoint host, not the
                # tunnel address, so report it as the peer rather than inventing
                # a gateway that the routing table does not contain.
                conn.peer_ip = endpoints[0].rsplit(":", 1)[0] if ":" in endpoints[0] else endpoints[0]
                conn.also_seen_as = [
                    f"{len(peers)} peer(s): " + ", ".join(p["allowed_ips"] for p in peers)
                ]
        return conn
