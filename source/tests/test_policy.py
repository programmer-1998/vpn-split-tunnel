"""Tests for split tunneling policy."""

from __future__ import annotations

import pytest

from vpn_split_tunnel.core.policy import PolicyMode, RoutingRule, SplitTunnelPolicy


class TestRoutingRule:
    """Test RoutingRule."""

    def test_matches_app(self) -> None:
        """Test app matching."""
        rule = RoutingRule(app_ids=["org.mozilla.firefox", "org.gnome.Terminal"])
        assert rule.matches_app("org.mozilla.firefox")
        assert not rule.matches_app("org.gnome.Nautilus")

    def test_matches_domain_exact(self) -> None:
        """Test exact domain matching."""
        rule = RoutingRule(domains=["example.com", "google.com"])
        assert rule.matches_domain("example.com")
        assert rule.matches_domain("google.com")
        assert not rule.matches_domain("sub.example.com")

    def test_matches_domain_suffix(self) -> None:
        """Test suffix domain matching (.example.com)."""
        rule = RoutingRule(domains=[".example.com", ".google.com"])
        assert rule.matches_domain("example.com")
        assert rule.matches_domain("sub.example.com")
        assert rule.matches_domain("api.sub.example.com")
        assert not rule.matches_domain("notexample.com")

    def test_matches_ip_cidr(self) -> None:
        """Test IP/CIDR matching."""
        rule = RoutingRule(ip_cidrs=["192.168.1.0/24", "10.0.0.1/32"])
        assert rule.matches_ip("192.168.1.50")
        assert rule.matches_ip("192.168.1.255")
        assert rule.matches_ip("10.0.0.1")
        assert not rule.matches_ip("192.168.2.1")
        assert not rule.matches_ip("10.0.0.2")

    def test_matches_ip_invalid(self) -> None:
        """Test invalid IP handling."""
        rule = RoutingRule(ip_cidrs=["192.168.1.0/24"])
        assert not rule.matches_ip("not-an-ip")


class TestSplitTunnelPolicy:
    """Test SplitTunnelPolicy."""

    def test_include_mode(self) -> None:
        """Test include mode logic."""
        policy = SplitTunnelPolicy(mode=PolicyMode.INCLUDE)
        rule = RoutingRule(
            app_ids=["org.mozilla.firefox"],
            domains=["example.com"],
            ip_cidrs=["192.168.1.0/24"]
        )
        policy.rules = [rule]

        # Matched traffic goes through VPN
        assert policy.should_route_through_vpn("org.mozilla.firefox", None, None)
        assert policy.should_route_through_vpn(None, "example.com", None)
        assert policy.should_route_through_vpn(None, None, "192.168.1.50")

        # Unmatched traffic bypasses VPN
        assert not policy.should_route_through_vpn("org.gnome.Nautilus", None, None)
        assert not policy.should_route_through_vpn(None, "google.com", None)
        assert not policy.should_route_through_vpn(None, None, "8.8.8.8")

    def test_exclude_mode(self) -> None:
        """Test exclude mode logic."""
        policy = SplitTunnelPolicy(mode=PolicyMode.EXCLUDE)
        rule = RoutingRule(
            app_ids=["org.mozilla.firefox"],
            domains=["example.com"],
            ip_cidrs=["192.168.1.0/24"]
        )
        policy.rules = [rule]

        # Matched traffic bypasses VPN
        assert not policy.should_route_through_vpn("org.mozilla.firefox", None, None)
        assert not policy.should_route_through_vpn(None, "example.com", None)
        assert not policy.should_route_through_vpn(None, None, "192.168.1.50")

        # Unmatched traffic goes through VPN
        assert policy.should_route_through_vpn("org.gnome.Nautilus", None, None)
        assert policy.should_route_through_vpn(None, "google.com", None)
        assert policy.should_route_through_vpn(None, None, "8.8.8.8")

    def test_multiple_rules(self) -> None:
        """Test multiple rules."""
        policy = SplitTunnelPolicy(mode=PolicyMode.INCLUDE)
        policy.rules = [
            RoutingRule(app_ids=["app1"]),
            RoutingRule(domains=["domain1.com"]),
            RoutingRule(ip_cidrs=["10.0.0.0/8"]),
        ]

        assert policy.should_route_through_vpn("app1", None, None)
        assert policy.should_route_through_vpn(None, "domain1.com", None)
        assert policy.should_route_through_vpn(None, None, "10.1.2.3")
        assert not policy.should_route_through_vpn("app2", "domain2.com", "192.168.1.1")

    def test_no_rules(self) -> None:
        """Test policy with no rules."""
        policy = SplitTunnelPolicy(mode=PolicyMode.INCLUDE)
        # No rules means nothing matches
        assert not policy.should_route_through_vpn("any-app", "any-domain", "1.2.3.4")

        policy = SplitTunnelPolicy(mode=PolicyMode.EXCLUDE)
        # No rules means everything matches (nothing excluded) -> all goes through VPN
        assert policy.should_route_through_vpn("any-app", "any-domain", "1.2.3.4")