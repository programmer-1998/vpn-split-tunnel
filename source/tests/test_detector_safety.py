"""Detection must never change the state of the machine.

Regression tests for the bug where the app launched the user's VPNs: the HAPP
detector ran `happ status`, but /usr/bin/happ is HAPP's GUI binary, and starting
it boots `happd` plus a sing-box TUN. That rebuilt the tunnel and dropped the
live connection every time the app opened.

The fix is structural - detectors can only issue allowlisted read-only queries -
so these tests check the allowlist itself, not the detectors' behaviour.
"""

from __future__ import annotations

import pytest

from vpn_split_tunnel.core.vpn_detectors.base import (
    _assert_read_only,
    _point_to_point_peer,
    InterfaceFacts,
    UnsafeCommandError,
    VPNConnection,
    VPNType,
)

# Client invocations that connect, reconnect, or boot a daemon.
DANGEROUS = [
    ["happ", "status"],  # the exact bug: GUI binary that starts happd + sing-box
    ["happd"],
    ["Happ"],
    ["/opt/happ/bin/Happ"],
    ["systemctl", "start", "happd"],
    ["systemctl", "stop", "happd"],
    ["systemctl", "restart", "happd"],
    ["openvpn", "--config", "/etc/openvpn/home.conf"],
    ["wg-quick", "up", "wg0"],
    ["wg-quick", "down", "wg0"],
    ["tailscale", "up"],
    ["tailscale", "down"],
    ["nmcli", "con", "up", "id", "Home"],
    ["nmcli", "connection", "up", "Home"],
    ["nmcli", "general", "reload", "off"],
    ["windscribe", "connect"],
    ["sing-box", "run"],
    ["ip", "rule", "add", "fwmark", "5555", "lookup", "101"],
    ["ip", "link", "set", "tun0", "down"],
]


@pytest.mark.parametrize("cmd", DANGEROUS, ids=lambda c: " ".join(c))
def test_state_changing_commands_are_rejected(cmd: list[str]) -> None:
    with pytest.raises(UnsafeCommandError):
        _assert_read_only(cmd)


# Queries the detectors legitimately need.
SAFE = [
    ["ip", "-j", "link", "show"],
    ["ip", "-j", "addr", "show", "dev", "tun0"],
    ["ip", "-j", "route", "show", "dev", "tun0"],
    ["ip", "-j", "link", "show", "type", "wireguard"],
    ["systemctl", "is-active", "happd"],
    ["systemctl", "status", "happd", "--no-pager"],
    ["systemctl", "list-units", "--type=service", "--state=active", "openvpn@*"],
    ["ps", "aux"],
    ["wg", "show", "all", "dump"],
    ["tailscale", "status", "--json"],
    ["resolvectl", "status", "tun0"],
    ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "con", "show", "--active"],
    ["ss", "-tulpn"],
]


@pytest.mark.parametrize("cmd", SAFE, ids=lambda c: " ".join(c))
def test_read_only_queries_are_allowed(cmd: list[str]) -> None:
    _assert_read_only(cmd)  # must not raise


def test_no_detector_executes_a_client_binary() -> None:
    """No detector source may name a VPN client binary in a command list.

    This is the check that would have caught the original bug at review time,
    even if the allowlist were later loosened by accident.
    """
    from pathlib import Path

    import vpn_split_tunnel.core.vpn_detectors as pkg

    clients = {
        "happ",
        "happd",
        "openvpn",
        "wg-quick",
        "windscribe",
        "sing-box",
        "xray",
        "psiphon",
        "amnezia",
        "xvpn",
    }
    offenders: list[str] = []
    for path in Path(pkg.__path__[0]).glob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("[") or '"' not in stripped:
                continue
            first = stripped.lstrip("[").strip().strip("\"',").split("/")[-1]
            if first in clients:
                offenders.append(f"{path.name}:{lineno}: {stripped}")

    assert not offenders, "detectors must not build commands from client binaries:\n" + "\n".join(
        offenders
    )


def test_empty_command_is_rejected() -> None:
    with pytest.raises(UnsafeCommandError):
        _assert_read_only([])


def test_point_to_point_peer_is_computed_not_guessed() -> None:
    # The real shape of tun0 on this machine: a /30 with the far end at .2.
    assert _point_to_point_peer(["172.18.0.1/30"], ["172.18.0.1"]) == "172.18.0.2"
    # A /31 has exactly one other address.
    assert _point_to_point_peer(["10.0.0.5/31"], ["10.0.0.5"]) == "10.0.0.4"
    # Ordinary network prefixes have no determined peer, so we report none
    # rather than inventing one.
    assert _point_to_point_peer(["192.168.1.55/24"], ["192.168.1.55"]) is None
    assert _point_to_point_peer(["100.111.220.35/32"], ["100.111.220.35"]) is None
    assert _point_to_point_peer([], ["172.18.0.1"]) is None


def test_from_facts_requires_an_up_interface_with_an_address() -> None:
    down = InterfaceFacts(is_up=False, ipv4=["172.18.0.1"])
    assert VPNConnection.from_facts(
        interface="tun0", vpn_type=VPNType.HAPP, name="x", facts=down
    ) is None

    no_address = InterfaceFacts(is_up=True)
    assert VPNConnection.from_facts(
        interface="tun0", vpn_type=VPNType.HAPP, name="x", facts=no_address
    ) is None

    good = InterfaceFacts(is_up=True, ipv4=["172.18.0.1"], cidrs=["172.18.0.1/30"], peer="172.18.0.2")
    conn = VPNConnection.from_facts(
        interface="tun0", vpn_type=VPNType.HAPP, name="HAPP (tun0)", facts=good
    )
    assert conn is not None
    assert conn.all_addresses == ["172.18.0.1"]
    assert conn.peer_ip == "172.18.0.2"
    assert conn.client_name == "HAPP"


def test_facts_from_another_client_count_as_an_address() -> None:
    """Tailscale reports its own address, which may not be in the kernel yet."""
    facts = InterfaceFacts(is_up=True, has_address_override=True, ipv4=["100.111.220.35"])
    conn = VPNConnection.from_facts(
        interface="tailscale0",
        vpn_type=VPNType.TAILSCALE,
        name="Tailscale",
        facts=facts,
    )
    assert conn is not None
    assert conn.all_addresses == ["100.111.220.35"]
