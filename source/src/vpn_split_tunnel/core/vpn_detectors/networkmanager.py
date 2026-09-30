"""NetworkManager VPN detector."""

from __future__ import annotations

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector


class NetworkManagerDetector(VPNDetector):
    """Detect NetworkManager VPN connections."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.NETWORKMANAGER

    def is_available(self) -> bool:
        import shutil
        return shutil.which("nmcli") is not None

    def detect(self) -> list[VPNConnection]:
        connections = []

        # Use nmcli to get active VPN connections
        result = self._run_command(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "con", "show", "--active"])
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 4:
                    name, vpn_type, device, state = parts[0], parts[1], parts[2], parts[3]
                    if "vpn" in vpn_type.lower() and state == "activated" and device:
                        conn = self._get_connection_details(device, name)
                        if conn:
                            connections.append(conn)

        return connections

    def _get_connection_details(self, interface: str, name: str) -> VPNConnection | None:
        vpn_subtype = self._get_vpn_subtype(name)
        return VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.NETWORKMANAGER,
            name=f"{name} ({vpn_subtype})",
            facts=self.interface_facts(interface),
            processes=self.client_processes(("NetworkManager",)),
        )

    def _get_vpn_subtype(self, connection_name: str) -> str:
        """Get VPN subtype (wireguard, openvpn, etc.) from NM."""
        result = self._run_command(["nmcli", "-t", "-f", "vpn.service-type", "con", "show", connection_name])
        if result.returncode == 0:
            output = result.stdout.strip()
            if output:
                return output.split(":")[-1] if ":" in output else output
        return "VPN"