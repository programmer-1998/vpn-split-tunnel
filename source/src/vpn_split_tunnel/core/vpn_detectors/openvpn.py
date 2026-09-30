"""OpenVPN detector - passive, and strict about who owns a tun device.

The generic `openvpn.service` is frequently *active* on machines where the real
tunnel belongs to a different client (on this system it is active while tun0
belongs to HAPP), so service activity alone must never be allowed to claim an
arbitrary interface. An interface is attributed to OpenVPN only when a running
openvpn process names that very device on its command line.
"""

from __future__ import annotations

import json
import re
import shutil

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector

# `--dev tun0`, `--dev=tun1`, `--dev-name`, and `dev tun0` config references.
_DEV_RE = re.compile(r"--dev(?:ice|-name)?[= ]+([A-Za-z0-9_.:-]+)")
_CONFIG_RE = re.compile(r"([\w./-]+\.conf)\b")


class OpenVPNDetector(VPNDetector):
    """Detect active OpenVPN connections without touching the daemon."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.OPENVPN

    def is_available(self) -> bool:
        return shutil.which("openvpn") is not None

    def detect(self) -> list[VPNConnection]:
        lines = self._openvpn_process_lines()
        if not lines:
            return []

        processes = self.client_processes(("openvpn",))
        out: list[VPNConnection] = []
        claimed: set[str] = set()

        for interface in sorted(self._named_devices(lines)):
            if interface in claimed or not self._is_up(interface):
                continue
            config = self._config_for(lines, interface)
            conn = VPNConnection.from_facts(
                interface=interface,
                vpn_type=VPNType.OPENVPN,
                name=f"OpenVPN {config} ({interface})" if config else f"OpenVPN ({interface})",
                facts=self.interface_facts(interface),
                processes=processes,
            )
            if conn:
                out.append(conn)
                claimed.add(interface)

        return out

    # ------------------------------------------------------------------
    def _openvpn_process_lines(self) -> list[str]:
        result = self._run_command(["ps", "aux"])
        if result.returncode != 0:
            return []
        return [
            line
            for line in (result.stdout or "").splitlines()
            if "openvpn" in line.lower()
            and "vpn_split_tunnel" not in line
            and "vpn-split-tunnel" not in line
        ]

    def _named_devices(self, lines: list[str]) -> set[str]:
        """Every interface an openvpn command line claims as its own device."""
        devices: set[str] = set()
        for line in lines:
            for device in _DEV_RE.findall(line):
                # `dev-node`/`dev-type` style values are keywords, not names.
                if device not in ("tun", "tap", "null", "tun", "sit", "tap"):
                    devices.add(device)
        return devices

    def _config_for(self, lines: list[str], interface: str) -> str | None:
        """The .conf an openvpn command line is using, for display."""
        for line in lines:
            if interface not in line:
                continue
            match = _CONFIG_RE.search(line)
            if match:
                return match.group(1)
        return None

    def _is_up(self, interface: str) -> bool:
        result = self._run_command(["ip", "-j", "link", "show", interface])
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout or "[]")
            except json.JSONDecodeError:
                return False
            if data and "UP" in data[0].get("flags", []):
                return True
        return self._run_command(["ip", "-j", "addr", "show", "dev", interface]).returncode == 0
