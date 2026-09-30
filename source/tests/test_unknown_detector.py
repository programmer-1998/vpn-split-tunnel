"""The unknown-tunnel fallback must show any real, unattributed tunnel.

The named detectors attribute tun devices to clients they recognise by process,
unit or config. A client this codebase has never heard of would otherwise be
invisible on an unknown machine: its tunnel is up and addressed and the page
still shows nothing. These tests pin the fallback that closes that hole:

* tunnel-shaped devices (by ARPHRD type, or by name when /sys is silent)
  that are up and addressed are reported with full interface facts;
* physical NICs, loopback, down and unaddressed devices are not;
* the manager registers the fallback last and keeps a named detector's
  attribution when both claim the same interface.

The kernel is faked: every command goes through `_run_command`, and the ARPHRD
read is monkeypatched, so no test touches a real interface.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from vpn_split_tunnel.core.vpn_detectors.base import VPNConnection, VPNType
from vpn_split_tunnel.core.vpn_detectors.manager import VPNDetectorManager
from vpn_split_tunnel.core.vpn_detectors.unknown import UnknownTunnelDetector


# ----------------------------------------------------------------------
# A fake kernel: canned `ip -j addr show` output and per-device lookups.
# ----------------------------------------------------------------------
def _iface(name, flags, addrs, mtu=1500):
    return {
        "ifname": name,
        "flags": flags,
        "mtu": mtu,
        "addr_info": addrs,
    }


V4 = lambda ip, plen, scope="global": {"family": "inet", "local": ip, "prefixlen": plen, "scope": scope}
V6 = lambda ip, plen: {"family": "inet6", "local": ip, "prefixlen": plen, "scope": "global"}

TUN7 = _iface("tun7", ["UP", "POINTOPOINT"], [V4("10.9.0.2", 30)])
ETH0 = _iface("eth0", ["UP", "BROADCAST"], [V4("192.168.1.5", 24)])
WLAN = _iface("wlan0", ["UP"], [V4("10.0.0.5", 24)])
LO = _iface("lo", ["UP", "LOOPBACK"], [V4("127.0.0.1", 8), V6("::1", 128)])
TUN9_DOWN = _iface("tun9", ["NO-CARRIER"], [V4("10.9.0.2", 24)])
TUNNOADDR = _iface("tun10", ["UP"], [])
VPN0 = _iface("vpn0", ["UP", "POINTOPOINT"], [V4("100.100.0.2", 32)])  # proprietary client
CUSTOM_TUN = _iface("custom-tun", ["UP"], [V4("10.1.1.2", 30)])


class FakeKernel:
    """Answers `_run_command` the way `ip`/`resolvectl` on a kernel would."""

    def __init__(self, interfaces):
        self.by_name = {i["ifname"]: i for i in interfaces}
        self.arphrd: dict[str, int | None] = {}

    def __call__(self, args, **kwargs):
        cmd = list(args)
        if cmd[:4] == ["ip", "-j", "addr", "show"]:
            if len(cmd) == 6 and cmd[4] == "dev":
                name = cmd[5]
                return self._ok([self.by_name[name]] if name in self.by_name else [])
            return self._ok(list(self.by_name.values()))
        if cmd[:5] == ["ip", "-j", "route", "show", "dev"]:
            return self._ok([])
        if cmd[:4] == ["ip", "-j", "link", "show"]:
            name = cmd[4] if len(cmd) > 4 else None
            entry = self.by_name.get(name, {})
            return self._ok([{k: v for k, v in entry.items() if k != "addr_info"}] if name else [])
        if cmd[0] == "resolvectl":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        raise AssertionError(f"unexpected command in fake kernel: {cmd}")

    @staticmethod
    def _ok(entries):
        return subprocess.CompletedProcess(
            ["ip"], 0, json.dumps(entries), ""
        )


def _patch(monkeypatch, kernel):
    monkeypatch.setattr(UnknownTunnelDetector, "_run_command", lambda self, args, **kw: kernel(args, **kw))
    monkeypatch.setattr(UnknownTunnelDetector, "_arphrd_type", lambda self, name: kernel.arphrd.get(name))
    return kernel


def test_unattributed_tun_is_reported_with_full_facts(monkeypatch):
    kernel = _patch(monkeypatch, FakeKernel([TUN7, ETH0, LO]))
    kernel.arphrd.update({"tun7": 65534, "eth0": 1, "lo": 772})

    connections = UnknownTunnelDetector().detect()

    assert [c.interface for c in connections] == ["tun7"]
    conn = connections[0]
    assert conn.vpn_type is VPNType.UNKNOWN
    assert conn.is_active is True
    assert conn.all_addresses == ["10.9.0.2"]
    assert conn.cidrs == ["10.9.0.2/30"]
    assert conn.peer_ip == "10.9.0.1"  # computed from the /30, not guessed
    assert conn.mtu == 1500


def test_physical_loopback_down_and_unaddressed_are_skipped(monkeypatch):
    kernel = _patch(monkeypatch, FakeKernel([ETH0, WLAN, LO, TUN9_DOWN, TUNNOADDR]))
    kernel.arphrd.update(
        {"eth0": 1, "wlan0": 1, "lo": 772, "tun9": 65534, "tun10": 65534}
    )
    assert UnknownTunnelDetector().detect() == []


def test_arphrd_type_is_authoritative_over_the_name(monkeypatch):
    """`vpn0` under a proprietary client is still a tunnel by kernel type."""
    kernel = _patch(monkeypatch, FakeKernel([VPN0]))

    kernel.arphrd = {"vpn0": 65534}
    out = UnknownTunnelDetector().detect()
    assert [c.interface for c in out] == ["vpn0"]

    # A device with a tunnel-ish *name* is caught when /sys is silent.
    kernel2 = _patch(monkeypatch, FakeKernel([TUN7]))
    kernel2.arphrd = {"tun7": None}
    assert [c.interface for c in UnknownTunnelDetector().detect()] == ["tun7"]


def test_unreadable_sys_without_a_tunnel_name_is_not_reported(monkeypatch):
    kernel = _patch(monkeypatch, FakeKernel([CUSTOM_TUN]))
    kernel.arphrd = {"custom-tun": None}  # /sys silent and no prefix match
    assert UnknownTunnelDetector().detect() == []


def test_detect_is_empty_when_no_tunnel_exists(monkeypatch):
    kernel = _patch(monkeypatch, FakeKernel([ETH0, LO]))
    kernel.arphrd = {"eth0": 1, "lo": 772}
    assert UnknownTunnelDetector().detect() == []


def test_connection_for_returns_none_for_down_device(monkeypatch):
    kernel = _patch(monkeypatch, FakeKernel([TUN9_DOWN]))
    kernel.arphrd = {"tun9": 65534}
    assert UnknownTunnelDetector().connection_for("tun9") is None


def test_manager_registers_the_fallback_last(monkeypatch):
    """Every detector class pretends to be installed so registration is real."""
    from vpn_split_tunnel.core.vpn_detectors import (
        hap,
        networkmanager,
        openvpn,
        systemd_networkd,
        tailscale,
        wireguard,
        windscribe,
        xray,
    )

    for module in (
        hap, networkmanager, openvpn, systemd_networkd,
        tailscale, wireguard, windscribe, xray,
    ):
        for cls in vars(module).values():
            if isinstance(cls, type) and hasattr(cls, "is_available"):
                monkeypatch.setattr(cls, "is_available", lambda self: True)

    prev = VPNDetectorManager._instance
    VPNDetectorManager._instance = None
    try:
        manager = VPNDetectorManager()
    finally:
        VPNDetectorManager._instance = prev

    assert any(isinstance(d, UnknownTunnelDetector) for d in manager._detectors)
    assert isinstance(manager._detectors[-1], UnknownTunnelDetector)


def test_manager_deduplicates_by_interface_in_registration_order(monkeypatch):
    """A named detector wins when both it and the fallback see the device."""
    named = type("Named", (), {})()
    named.detect = lambda: [
        VPNConnection(interface="tun7", vpn_type=VPNType.WIREGUARD, name="WireGuard tun7")
    ]
    fallback = type("Fallback", (), {})()
    fallback.detect = lambda: [
        VPNConnection(interface="tun7", vpn_type=VPNType.UNKNOWN, name="Unknown VPN (tun7)")
    ]

    manager = VPNDetectorManager()
    manager._detectors = [named, fallback]
    connections = manager.detect_all()

    assert len(connections) == 1
    assert connections[0].vpn_type is VPNType.WIREGUARD
    assert connections[0].name == "WireGuard tun7"