"""Tailscale VPN detector - passive."""

from __future__ import annotations

import json
import shutil

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector


class TailscaleDetector(VPNDetector):
    """Detect active Tailscale connections.

    `tailscale status` is a pure query and is on the read-only allowlist.
    `tailscale up` would reconnect, and is rejected by the allowlist.
    """

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.TAILSCALE

    def is_available(self) -> bool:
        return shutil.which("tailscale") is not None

    def detect(self) -> list[VPNConnection]:
        status = self._status()
        if not status:
            return []
        # Only "Running" means an active tunnel. "Stopped"/"NeedsLogin" do not.
        if status.get("BackendState") != "Running":
            return []

        interface = self._get_tailscale_interface()
        if interface is None:
            return []

        facts = self.interface_facts(interface)
        tailscale_ips = self._self_ips(status)
        if tailscale_ips and not facts.ipv4:
            # Tailscale reports the address even if the interface is odd
            facts.ipv4 = list(tailscale_ips)
            facts.has_address_override = True

        conn = VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.TAILSCALE,
            name=f"Tailscale ({interface})",
            facts=facts,
            processes=self._daemon_lines(),
        )
        if conn and status.get("ExitNodeStatus"):
            conn.also_seen_as = [
                f"exit node: {status['ExitNodeStatus'].get('TailscaleIPs', ['?'])[0]}"
            ]
        return [conn] if conn else []

    def _status(self) -> dict:
        result = self._run_command(["tailscale", "status", "--json"])
        if result.returncode != 0 or not (result.stdout or "").strip():
            return {}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _get_tailscale_interface(self) -> str | None:
        result = self._run_command(["ip", "-j", "link", "show"])
        if result.returncode != 0:
            return None
        try:
            interfaces = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return None
        for iface in interfaces:
            name = iface.get("ifname", "")
            if name.startswith("tailscale") and "UP" in iface.get("flags", []):
                return name
        return None

    def _self_ips(self, status: dict) -> list[str]:
        self_info = status.get("Self") or {}
        return [ip for ip in self_info.get("TailscaleIPs", []) if ip]

    def _daemon_lines(self) -> list[str]:
        return self.client_processes(("tailscaled",))
