"""Split Tunneling Policy Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vpn_split_tunnel.utils.i18n import _


class PolicyMode(Enum):
    """Split tunneling policy mode."""
    INCLUDE = "include"      # Only selected apps/domains go through VPN
    EXCLUDE = "exclude"      # Everything except selected goes through VPN


@dataclass
class RoutingRule:
    """A single routing rule."""
    # App matching (by desktop ID or executable name)
    app_ids: list[str] = field(default_factory=list)
    # Domain matching
    domains: list[str] = field(default_factory=list)
    # IP/CIDR matching
    ip_cidrs: list[str] = field(default_factory=list)
    # Rule priority (higher = more specific)
    priority: int = 100

    def matches_app(self, app_id: str) -> bool:
        return app_id in self.app_ids

    def matches_domain(self, domain: str) -> bool:
        for rule_domain in self.domains:
            if rule_domain.startswith("."):
                # Suffix match: .example.com matches sub.example.com
                if domain.endswith(rule_domain) or domain == rule_domain[1:]:
                    return True
            elif rule_domain == domain:
                return True
        return False

    def matches_ip(self, ip: str) -> bool:
        import ipaddress
        try:
            ip_obj = ipaddress.ip_address(ip)
            for cidr in self.ip_cidrs:
                if ip_obj in ipaddress.ip_network(cidr, strict=False):
                    return True
        except ValueError:
            pass
        return False


@dataclass
class SplitTunnelPolicy:
    """Complete split tunneling policy."""
    mode: PolicyMode = PolicyMode.INCLUDE
    rules: list[RoutingRule] = field(default_factory=list)
    # Global settings
    apply_to_all_users: bool = False
    dns_through_vpn: bool = True

    def get_rules_for_mode(self) -> list[RoutingRule]:
        """Get rules applicable for current mode."""
        return self.rules

    def should_route_through_vpn(self, app_id: str | None, domain: str | None, ip: str | None) -> bool:
        """Determine if traffic should go through VPN based on policy."""
        matched = False
        for rule in self.rules:
            rule_matches = False
            if app_id and rule.matches_app(app_id):
                rule_matches = True
            if domain and rule.matches_domain(domain):
                rule_matches = True
            if ip and rule.matches_ip(ip):
                rule_matches = True

            if rule_matches:
                matched = True
                break

        if self.mode == PolicyMode.INCLUDE:
            # Only matched traffic goes through VPN
            return matched
        else:
            # EXCLUDE mode: matched traffic bypasses VPN, everything else goes through VPN
            return not matched


@dataclass
class PolicyState:
    """Runtime state of applied policy."""
    is_active: bool = False
    applied_policy: SplitTunnelPolicy | None = None
    vpn_interface: str | None = None
    # The tunnel address the route actually points at, read from the detector at
    # apply time. apply_policy() was already passing it here; the field was
    # missing, so every apply ended in TypeError before installing anything.
    vpn_peer: str | None = None
    fwmark: int = 5555
    routing_table: int = 101
    # Where the consuming ip rule landed. Same story as vpn_peer: the manager
    # records it, and the field had to exist for that to work.
    rule_priority: int = 8500
    cgroup_slice: str = "vpn-split-tunnel.slice"
    # How many distinct selectors (apps, domains, IPs) actually got a mark rule.
    marked_count: int = 0
    # True but not fatal, e.g. a rule a VPN client's catch-all will shadow.
    warnings: list[str] = field(default_factory=list)
    error: str | None = None