"""Fallback detector: every tunnel-like interface that no named client claimed.

The named detectors attribute each tun device to a client they recognise by
its process, systemd unit, or config file. A client this codebase has never
heard of -- a proprietary VPN app, a renamed daemon, a container network
pretending to be a tunnel -- would otherwise be *invisible* no matter how real
its tunnel is: the device is up, addressed, and carrying traffic, and the page
still shows nothing. That is exactly the machine this app has to survive: one
where "what is installed" is not known in advance.

So the fallback classifies devices by what the kernel says they are (the
ARPHRD type read from /sys: tun, tap, wg, ppp, gre, ipip, sit...), not by which
client made them, and reports each one as an Unknown VPN with the full set of
interface facts. It is registered last, and the manager keeps only the first
connection per interface, so a device a named detector recognised is still
attributed to that client and never appears twice.

The price of the broad net is a possible false headline: a device that is
kernel-tunnel-shaped but is not a VPN (say, a spare tun created by some other
tool) will be listed. That is deliberate. Listing an idle interface costs
nothing, and the alternative -- silently hiding a real VPN on a machine we
cannot recognise -- is the failure this class exists to prevent.
"""

from __future__ import annotations

import json

from vpn_split_tunnel.core.vpn_detectors.base import (
    TUNNEL_IFACE_PREFIXES,
    VPNConnection,
    VPNDetector,
    VPNType,
)

# ARPHRD types that mean "this is a point-to-point or tunnel device", from
# <uapi/linux/if_arp.h>. 65534 (ARPHRD_NONE) is a tun, a tap, a WireGuard
# interface and an ordinary Linux utun; 512 is ppp; 768/776/778/783/784 are the
# ipip, sit, gre, gretap and ip6gre encapsulations.
_TUNNEL_ARPHRD = frozenset({512, 768, 776, 778, 783, 784, 65534})

# Name prefixes that also mean "tunnel-like", used when /sys says nothing (the
# type file is unreadable on some unusual kernels). See the definition in base.
_TUNNEL_PREFIXES = TUNNEL_IFACE_PREFIXES


class UnknownTunnelDetector(VPNDetector):
    """Report tunnel-shaped interfaces no named detector claimed."""

    @property
    def vpn_type(self) -> VPNType:
        return VPNType.UNKNOWN

    def is_available(self) -> bool:
        # The kernel is always present; this is a scan, not an installation
        # check, so there is nothing to be unavailable when the machine does
        # not have the kernel's neighbours in a special place.
        return True

    def detect(self) -> list[VPNConnection]:
        return [
            conn
            for conn in (
                self.connection_for(interface) for interface in self._candidates()
            )
            if conn is not None
        ]

    # ------------------------------------------------------------------
    # Candidate interfaces
    # ------------------------------------------------------------------
    def _candidates(self) -> list[str]:
        """Every up, addressed interface that is shaped like a tunnel.

        Only the names come back; each candidate is then interrogated through
        the shared `interface_facts`, so whatever the machine happens to call
        its tunnel (even something like `vpn0` under a proprietary client) is
        discovered rather than assumed.
        """
        result = self._run_command(["ip", "-j", "addr", "show"])
        if result.returncode != 0:
            return []
        try:
            entries = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []

        candidates: list[str] = []
        for entry in entries:
            name = entry.get("ifname", "")
            if not name or name == "lo":
                continue
            if "UP" not in entry.get("flags", []):
                continue
            addressed = any(
                addr.get("family") in ("inet", "inet6")
                and addr.get("scope") != "link"
                for addr in entry.get("addr_info", [])
            )
            if not addressed:
                continue
            if self._looks_like_tunnel(name):
                candidates.append(name)
        return candidates

    def _looks_like_tunnel(self, interface: str) -> bool:
        """Whether the kernel classifies this interface as a tunnel.

        The ARPHRD type from /sys is the authoritative answer. When it is not
        readable the name prefix is the fallback, because the underlying
        question -- "is this interface likely the far end of a tunnel?" -- has
        to be answered somehow and a name is the only evidence left.
        """
        arphrd = self._arphrd_type(interface)
        if arphrd is not None:
            return arphrd in _TUNNEL_ARPHRD
        return interface.startswith(_TUNNEL_PREFIXES)

    @staticmethod
    def _arphrd_type(interface: str) -> int | None:
        """The ARPHRD type of an interface, or None when unreadable."""
        try:
            raw = f"/sys/class/net/{interface}/type"
            with open(raw, encoding="ascii") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Connection building
    # ------------------------------------------------------------------
    def connection_for(self, interface: str) -> VPNConnection | None:
        """A full Unknown VPN connection for one interface, or None.

        Shared with the UI's "Add a VPN manually" flow so a manually named
        interface gets exactly the same facts as one found by the scan --
        addresses, tunnel peer, DNS and MTU included. A device that is down or
        not addressed yields None, which from_facts expresses as "not a VPN
        right now".
        """
        facts = self.interface_facts(interface)
        return VPNConnection.from_facts(
            interface=interface,
            vpn_type=VPNType.UNKNOWN,
            name=f"Unknown VPN ({interface})",
            facts=facts,
            processes=[],
        )