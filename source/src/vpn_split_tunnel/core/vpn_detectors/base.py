"""VPN Detection - Base classes and types."""

from __future__ import annotations

import abc
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum

@dataclass
class InterfaceFacts:
    """Everything we can learn about an interface without touching it."""

    is_up: bool = False
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    cidrs: list[str] = field(default_factory=list)
    gateway: str | None = None
    peer: str | None = None
    routes: list[str] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)
    mtu: int | None = None
    # Set by a detector that learned the address from the client itself (e.g.
    # Tailscale's own status JSON) rather than from the kernel.
    has_address_override: bool = False

    @property
    def all_addresses(self) -> list[str]:
        return [*self.ipv4, *self.ipv6]

    @property
    def has_address(self) -> bool:
        return bool(self.ipv4) or self.has_address_override


class UnsafeCommandError(RuntimeError):
    """Raised when a detector tries to run a command that could change state."""


# Interface name prefixes that mean "looks like a tunnel". This is a shape
# heuristic, not a list of installed things: `wg` matches any WireGuard
# device, `tun` any tun, and so on. Kept in one place because the detectors,
# the manual-entry picker and the docs have to agree.
TUNNEL_IFACE_PREFIXES = (
    "tun",
    "tap",
    "wg",
    "utun",
    "ppp",
    "nordlynx",
    "wsc",
    "tailscale",
    "xray",
    "sing",
    "clash",
    "meta",
    "proxy",
)


# Sub-commands that mutate state. Listed for a clear error message.
_MUTATING_SUBCOMMANDS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "reload-or-restart",
        "enable",
        "disable",
        "mask",
        "unmask",
        "connect",
        "disconnect",
        "up",
        "down",
        "kill",
        "set",
        "add",
        "del",
        "delete",
        "flush",
        "replace",
        "install",
        "reboot",
        "poweroff",
        "suspend",
        "hibernate",
        "edit",
        "modify",
        "activate",
        "deactivate",
    }
)

# Per-binary allowlist. A command is accepted when it satisfies the binary's
# own rule. Anything not listed here is refused outright, because we cannot
# know what an unvetted client binary does when executed. A value of None means
# "flag-only invocations are fine" (e.g. `ss -tulpn`).
_READ_ONLY_BINARIES: dict[str, frozenset[str] | None] = {
    "ip": frozenset({"link", "addr", "route", "rule", "neigh", "netns", "-j"}),
    "systemctl": frozenset(
        {
            "is-active",
            "is-enabled",
            "is-failed",
            "status",
            "show",
            "list-units",
            "list-unit-files",
            "cat",
            "get-default",
        }
    ),
    "ps": frozenset({"aux", "-ef", "-e", "-o", "-p"}),
    "wg": frozenset({"show"}),
    "nmcli": frozenset({"show", "status"}),  # `nmcli con up` is NOT allowed
    "tailscale": frozenset({"status", "version", "netcheck"}),
    "resolvectl": frozenset({"status", "query", "statistics"}),
    "ss": None,
    "loginctl": frozenset({"show-user", "user-status", "list-users"}),
}


def _assert_read_only(cmd: list[str]) -> None:
    """Reject any command that is not a known read-only query."""
    if not cmd:
        raise UnsafeCommandError("empty command")

    argv0 = os.path.basename(cmd[0])

    if argv0 not in _READ_ONLY_BINARIES:
        raise UnsafeCommandError(
            f"detectors may not execute {argv0!r}. Only read-only queries are "
            f"allowed: {sorted(_READ_ONLY_BINARIES)}. Running a VPN client binary "
            f"here can restart its daemon and tunnel and drop the user's live "
            f"connection - interrogate /proc, systemd and the interfaces instead."
        )

    args = cmd[1:]
    if any(arg in _MUTATING_SUBCOMMANDS for arg in args):
        bad = next(arg for arg in args if arg in _MUTATING_SUBCOMMANDS)
        raise UnsafeCommandError(
            f"{argv0} {bad!r} changes system state; detectors must not run it"
        )

    allowed = _READ_ONLY_BINARIES[argv0]
    if allowed:
        if not any(arg in allowed for arg in args):
            raise UnsafeCommandError(
                f"{argv0} {' '.join(args)}: sub-command is not a recognised "
                f"read-only query for {argv0} (expected one of {sorted(allowed)})"
            )
        return

    # Flag-only binary (ss): accept when every argument is a flag.
    if args and all(arg.startswith("-") for arg in args):
        return
    raise UnsafeCommandError(
        f"{argv0} {' '.join(args)}: only flag-only queries are allowed for {argv0}"
    )


def _command_name(ps_line: str) -> str:
    """Executable name from a `ps aux` line.

    `ps aux` columns are USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND,
    so the command is field 11 onwards; the first token of that is the
    executable, which is what we want to show.
    """
    parts = ps_line.split(None, 10)
    if len(parts) < 11:
        return ""
    command = parts[10].strip()
    exe = command.split()[0] if command else ""
    return exe.rsplit("/", 1)[-1]


def _point_to_point_peer(cidrs: list[str], local: list[str]) -> str | None:
    """The other host on a /30 or /31 link, computed from the prefix.

    A tun link has no ARP, so the kernel's neighbour table cannot tell us the
    far end. On a point-to-point prefix the other address is determined by the
    prefix length alone, so this is arithmetic rather than a guess. Returns
    None for ordinary network prefixes, where a peer would be a guess.
    """
    import ipaddress

    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        # /30 and /31 are the point-to-point sizes for IPv4; a /32 is a single
        # host and anything wider is an ordinary network.
        if net.version != 4 or net.prefixlen < 30:
            continue
        for ours in local:
            try:
                mine = ipaddress.ip_address(ours)
            except ValueError:
                continue
            if mine not in net:
                continue
            for host in net.hosts():
                if host != mine:
                    return str(host)
    return None


class VPNType(Enum):
    """Known VPN types."""
    WIREGUARD = "wireguard"
    OPENVPN = "openvpn"
    TAILSCALE = "tailscale"
    XRAY = "xray"  # VLESS/VMESS/Trojan/etc via Xray/Sing-box/Clash.Meta
    WINDSCRIBE = "windscribe"
    HAPP = "happ"
    NETWORKMANAGER = "networkmanager"
    SYSTEMD_NETWORKD = "systemd-networkd"
    UNKNOWN = "unknown"


@dataclass
class VPNConnection:
    """A VPN that is currently up, as observed without modifying anything."""

    interface: str
    vpn_type: VPNType
    name: str
    is_active: bool = True
    config_path: str | None = None
    gateway_ip: str | None = None
    dns_servers: list[str] | None = None
    all_addresses: list[str] | None = None
    cidrs: list[str] | None = None
    peer_ip: str | None = None
    mtu: int | None = None
    processes: list[str] | None = None
    # Other clients that also claim this interface, so the UI can say
    # "seen as HAPP and OpenVPN" instead of silently dropping one.
    also_seen_as: list[str] | None = None

    def __post_init__(self) -> None:
        if self.dns_servers is None:
            self.dns_servers = []
        if self.all_addresses is None:
            self.all_addresses = []
        if self.cidrs is None:
            self.cidrs = []
        if self.processes is None:
            self.processes = []

    @classmethod
    def from_facts(
        cls,
        *,
        interface: str,
        vpn_type: VPNType,
        name: str,
        facts: InterfaceFacts,
        processes: list[str] | None = None,
        config_path: str | None = None,
    ) -> "VPNConnection | None":
        """Build a connection from interface facts, or None if not a tunnel."""
        if not facts.is_up or not facts.has_address:
            return None
        return cls(
            interface=interface,
            vpn_type=vpn_type,
            name=name,
            is_active=True,
            config_path=config_path,
            gateway_ip=facts.gateway,
            dns_servers=list(facts.dns),
            all_addresses=facts.all_addresses,
            cidrs=list(facts.cidrs),
            peer_ip=facts.peer,
            mtu=facts.mtu,
            processes=list(processes or []),
        )

    def get_display_name(self) -> str:
        """Client name, without the interface suffix."""
        return TYPE_LABELS.get(self.vpn_type, "Unknown")

    @property
    def client_name(self) -> str:
        return TYPE_LABELS.get(self.vpn_type, "Unknown")

    def short_process_names(self) -> list[str]:
        """Process names without their full path, for display."""
        out = []
        for cmd in self.processes or []:
            name = cmd.split()[0].rsplit("/", 1)[-1] if cmd else ""
            if name and name not in out:
                out.append(name)
        return out


TYPE_LABELS = {
    VPNType.WIREGUARD: "WireGuard",
    VPNType.OPENVPN: "OpenVPN",
    VPNType.TAILSCALE: "Tailscale",
    VPNType.XRAY: "Xray / Sing-box",
    VPNType.WINDSCRIBE: "Windscribe",
    VPNType.HAPP: "HAPP",
    VPNType.NETWORKMANAGER: "NetworkManager",
    VPNType.SYSTEMD_NETWORKD: "systemd-networkd",
    VPNType.UNKNOWN: "Unknown VPN",
}


class VPNDetector(abc.ABC):
    """Abstract base class for VPN detectors."""

    @property
    @abc.abstractmethod
    def vpn_type(self) -> VPNType:
        """Return the VPN type this detector handles."""
        pass

    @abc.abstractmethod
    def detect(self) -> list[VPNConnection]:
        """Detect active VPN connections of this type."""
        pass

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if this VPN type is available on the system."""
        pass

    def _run_command(self, cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
        """Run a *read-only* query command.

        Detection must never change the state of the machine. Some VPN clients
        are shipped as a GUI binary that also bootstraps its own daemon and
        tunnel when executed (HAPP's `happ` binary starts `happd` and a
        sing-box TUN), so "just run the client CLI to ask for status" silently
        tears down and rebuilds the user's live connection.

        Every detector therefore goes through an allowlist of read-only
        operations. Anything else raises, so the mistake surfaces in tests
        instead of on the user's connection.
        """
        _assert_read_only(cmd)
        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=False,
        )

    def _get_interface_info(self, interface: str) -> dict[str, str]:
        """Get detailed info about a network interface."""
        result = self._run_command(["ip", "-j", "link", "show", interface])
        if result.returncode == 0:
            import json
            try:
                data = json.loads(result.stdout)
                if data:
                    return data[0]
            except json.JSONDecodeError:
                pass
        return {}

    # ------------------------------------------------------------------
    # Shared, read-only interface facts
    # ------------------------------------------------------------------
    def interface_facts(self, interface: str) -> InterfaceFacts:
        """Collect addresses, peer, gateway and DNS for an interface.

        Shared by every detector so the UI can show the same set of details
        regardless of which client created the tunnel. A tun device usually has
        no gateway route of its own, so the tunnel peer is reported
        separately rather than being faked into the gateway field.
        """
        import json

        facts = InterfaceFacts()

        addr = self._run_command(["ip", "-j", "addr", "show", "dev", interface])
        if addr.returncode == 0:
            try:
                for entry in json.loads(addr.stdout or "[]"):
                    if "UP" in entry.get("flags", []):
                        facts.is_up = True
                    for a in entry.get("addr_info", []):
                        family = a.get("family")
                        if family == "inet" and a.get("local"):
                            if a.get("local") not in facts.ipv4:
                                facts.ipv4.append(a["local"])
                            if a.get("prefixlen") is not None:
                                facts.cidrs.append(f"{a['local']}/{a['prefixlen']}")
                        elif family == "inet6" and a.get("local"):
                            if a.get("scope") != "link":
                                if a.get("local") not in facts.ipv6:
                                    facts.ipv6.append(a["local"])
            except json.JSONDecodeError:
                pass

        route = self._run_command(["ip", "-j", "route", "show", "dev", interface])
        if route.returncode == 0:
            try:
                for r in json.loads(route.stdout or "[]"):
                    if "linkdown" in r:
                        continue
                    if r.get("gateway") and not facts.gateway:
                        facts.gateway = r["gateway"]
                    if r.get("dst") and not facts.routes:
                        facts.routes.append(r["dst"])
            except json.JSONDecodeError:
                pass

        link = self._run_command(["ip", "-j", "link", "show", interface])
        if link.returncode == 0:
            try:
                data = json.loads(link.stdout or "[]")
                if data and "mtu" in data[0]:
                    facts.mtu = data[0]["mtu"]
            except json.JSONDecodeError:
                pass

        # The other end of a point-to-point tunnel. A tun device has no ARP, so
        # the neighbour table stays empty; for a /30 or /31 link the peer is
        # simply the other host address, which we can compute exactly.
        if not facts.peer:
            facts.peer = _point_to_point_peer(facts.cidrs, facts.ipv4)

        dns = self._run_command(["resolvectl", "status", interface])
        if dns.returncode == 0:
            for line in (dns.stdout or "").splitlines():
                stripped = line.strip()
                if stripped.startswith("DNS Servers:"):
                    for token in stripped.split(":", 1)[1].split():
                        if token:
                            facts.dns.append(token)

        # A tun device with only a link-scope route has no gateway; the peer
        # address is the meaningful "other end" to show the user.
        if not facts.gateway and facts.peer:
            facts.gateway = facts.peer

        return facts

    def client_processes(self, names: tuple[str, ...]) -> list[str]:
        """Executable names for whichever of `names` are currently running.

        Attribution of a tun device to a client cannot be done from an
        unprivileged session: /proc/<pid>/net/tun does not exist on current
        kernels, and a root-owned client's /proc/<pid>/fd is unreadable. What
        does work for any user is the process table, so a client is identified
        by its binary/service name. This never executes the client.
        """
        result = self._run_command(["ps", "aux"])
        if result.returncode != 0:
            return []
        out: list[str] = []
        for line in (result.stdout or "").splitlines():
            low = line.lower()
            if "vpn_split_tunnel" in low or "vpn-split-tunnel" in low:
                continue
            if not any(n.lower() in low for n in names):
                continue
            exe = _command_name(line)
            if exe and exe not in out:
                out.append(exe)
        return out

    def running_services(self, units: tuple[str, ...]) -> list[str]:
        """Which of `units` systemd reports as active."""
        active: list[str] = []
        for unit in units:
            result = self._run_command(["systemctl", "is-active", unit])
            if result.returncode == 0 and result.stdout.strip() == "active":
                active.append(unit)
        return active