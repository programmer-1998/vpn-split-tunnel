"""systemd-networkd VPN detector."""

from __future__ import annotations

from pathlib import Path

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector


class SystemdNetworkdDetector(VPNDetector):
    """Detect systemd-networkd managed VPN connections."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.SYSTEMD_NETWORKD

    def is_available(self) -> bool:
        result = self._run_command(["systemctl", "is-active", "systemd-networkd"])
        return result.returncode == 0

    def detect(self) -> list[VPNConnection]:
        connections: list[VPNConnection] = []

        for netdev_dir in self._netdev_dirs():
            for netdev_file in sorted(netdev_dir.glob("*.netdev")):
                vpn_info = self._parse_netdev_file(netdev_file)
                if not vpn_info:
                    continue
                interface = vpn_info.get("Name")
                if not interface:
                    continue
                conn = VPNConnection.from_facts(
                    interface=interface,
                    vpn_type=VPNType.SYSTEMD_NETWORKD,
                    name=vpn_info.get("Description", interface),
                    facts=self.interface_facts(interface),
                    processes=self.client_processes(("systemd-networkd",)),
                    config_path=str(netdev_file),
                )
                if conn:
                    connections.append(conn)

        return connections

    def _netdev_dirs(self) -> list[Path]:
        return [
            d
            for d in (
                Path("/etc/systemd/network"),
                Path("/usr/lib/systemd/network"),
                Path("/run/systemd/network"),
            )
            if d.is_dir()
        ]

    def _parse_netdev_file(self, path: Path) -> dict | None:
        """Parse systemd .netdev file for VPN configuration."""
        try:
            content = path.read_text()
            # Simple parsing for [NetDev] section
            in_netdev = False
            info = {}
            for line in content.split("\n"):
                line = line.strip()
                if line == "[NetDev]":
                    in_netdev = True
                    continue
                if line.startswith("[") and line != "[NetDev]":
                    in_netdev = False
                    continue
                if in_netdev and "=" in line:
                    key, value = line.split("=", 1)
                    info[key.strip()] = value.strip()

            # Check if it's a VPN type
            kind = info.get("Kind", "").lower()
            if kind in ("wireguard", "openvpn", "tun", "tap", "vxlan", "gre", "ipip", "sit", "vti"):
                return info
        except OSError:
            return None
        return None