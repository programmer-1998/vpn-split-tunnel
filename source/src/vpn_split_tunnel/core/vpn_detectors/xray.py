"""Xray / Sing-box / Clash.Meta detector for VLESS/VMESS/Trojan - passive.

Runs no client binaries. Proxy TUN interfaces are found by looking at the
interfaces that exist and the systemd units / processes that are already
running, then attributing each tun device to whichever proxy owns it.
"""

from __future__ import annotations

import json
import re

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType, VPNDetector

# Units that commonly front a proxy TUN. Read with `systemctl is-active`, never
# started.
PROXY_SERVICES = (
    "xray",
    "sing-box",
    "clash-meta",
    "mihomo",
    "v2ray",
    "xvpn",
    "clash",
    "nekoray",
)

# Process names that indicate a proxy core is running.
_PROXY_PROCESS_NAMES = (
    "xray",
    "sing-box",
    "singbox",
    "clash",
    "mihomo",
    "v2ray",
    "xvpn",
)

_TUN_NAME_RE = re.compile(r"^(tun|tap|xray|sing|clash|meta|proxy)\d*$")


class XrayDetector(VPNDetector):
    """Detect Xray/Sing-box/Clash.Meta TUN interfaces without running them."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.XRAY

    def is_available(self) -> bool:
        """Is any proxy core present? Filesystem and process checks only."""
        if self._running_services():
            return True
        for line in self._proxy_process_lines():
            return True
        for path in ("/usr/bin/xray", "/usr/bin/sing-box", "/usr/bin/mihomo"):
            if _exists(path):
                return True
        return False

    def detect(self) -> list[VPNConnection]:
        if not self.is_available():
            return []

        active = self._running_services()
        process_ifaces = self._interfaces_from_processes()

        out: list[VPNConnection] = []
        claimed: set[str] = set()

        # 1. Interfaces explicitly named on a running proxy process
        for name in process_ifaces:
            if name in claimed:
                continue
            conn = self._build_connection(name, self._owning_service(active, name))
            if conn:
                out.append(conn)
                claimed.add(name)

        # 2. For each running proxy service, find the tun it owns
        for svc in active:
            for name in self._interfaces_for_service(svc):
                if name in claimed:
                    continue
                conn = self._build_connection(name, svc)
                if conn:
                    out.append(conn)
                    claimed.add(name)

        return out

    # ------------------------------------------------------------------
    def _running_services(self) -> list[str]:
        return self.running_services(PROXY_SERVICES)

    def _proxy_process_lines(self) -> list[str]:
        return self.client_processes(_PROXY_PROCESS_NAMES)

    def _interfaces_from_processes(self) -> list[str]:
        out: list[str] = []
        for line in self._proxy_process_lines():
            for token in line.split():
                if _TUN_NAME_RE.match(token) and token not in out:
                    out.append(token)
        return out

    def _owning_service(self, active: list[str], interface: str) -> str | None:
        """Which active service mentions this interface?"""
        for svc in active:
            if interface in self._unit_text(svc):
                return svc
        # Attribute by process name if only one proxy is running
        if len(active) == 1:
            return active[0]
        return None

    def _unit_text(self, service: str) -> str:
        result = self._run_command(["systemctl", "status", service, "--no-pager"])
        if result.returncode != 0:
            return ""
        return result.stdout or ""

    def _interfaces_for_service(self, service: str) -> list[str]:
        """Interface names a service's unit or journal output mentions."""
        text = self._unit_text(service)
        out: list[str] = []
        for name in re.findall(r"\b(?:tun|tap|xray|sing|clash|meta|proxy)\d*\b", text):
            if name not in out and self._is_up_and_addressed(name):
                out.append(name)
        return out

    def _is_up_and_addressed(self, interface: str) -> bool:
        result = self._run_command(["ip", "-j", "addr", "show", "dev", interface])
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

    def _build_connection(self, interface: str, service: str | None) -> VPNConnection | None:
        facts = self.interface_facts(interface)
        label = service or "Proxy"
        return VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.XRAY,
            name=f"{label} ({interface})",
            facts=facts,
            processes=[l.strip() for l in self._proxy_process_lines()],
        )


def _exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).exists()
