"""Every marking rule must carry a counter.

A marking rule that never fires looks exactly like one that is working: the UI
shows the policy as applied either way, and the usual reason a rule matches
nothing -- the application was never started inside the slice, so no rule names
its cgroup -- is invisible. The counter is what turns "applied" into "applied
and matching", and it is the first thing anyone checks when a split tunnel
appears not to work.
"""

from __future__ import annotations

from vpn_split_tunnel.core.policy import PolicyMode, RoutingRule, SplitTunnelPolicy
from vpn_split_tunnel.core.routing.manager import RoutingManager


def _render(mode, apps=(), domains=(), cidrs=(), cgroups=()):
    policy = SplitTunnelPolicy()
    policy.mode = mode
    policy.rules = [RoutingRule(app_ids=list(apps), domains=list(domains), ip_cidrs=list(cidrs))]
    plan = RoutingManager().plan(policy, "tun0", "172.18.0.2")
    if cgroups:
        plan = type(plan)(**{**plan.__dict__, "cgroups": cgroups})
    return RoutingManager()._build_nftables(plan), plan


def _marking_lines(ruleset: str) -> list[str]:
    return [
        line.strip()
        for line in ruleset.splitlines()
        if "meta mark set" in line and "mark_output" not in line
    ]


def test_address_rules_have_counters():
    ruleset, _ = _render(PolicyMode.INCLUDE, cidrs=["192.0.2.1/32", "198.51.100.0/24"])
    marking = _marking_lines(ruleset)
    assert len(marking) == 2, marking
    for line in marking:
        assert " counter " in f" {line} ", f"no counter on: {line}"


def test_application_rules_have_counters():
    ruleset, _ = _render(
        PolicyMode.INCLUDE,
        apps=["firefox.desktop"],
        cgroups=[("firefox.desktop", "/user.slice/user-1000.slice/app.slice/firefox-1.scope")],
    )
    marking = _marking_lines(ruleset)
    assert len(marking) == 1, marking
    assert "socket cgroupv2" in marking[0]
    assert " counter " in f" {marking[0]} ", marking[0]


def test_domain_rules_have_counters():
    ruleset, _ = _render(PolicyMode.INCLUDE, domains=["example.com"])
    marking = _marking_lines(ruleset)
    assert len(marking) == 2, marking  # one for each address family
    for line in marking:
        assert " counter " in f" {line} ", f"no counter on: {line}"
    assert any("@vpn_addrs6" in line for line in marking)


def test_counters_survive_in_exclude_mode_too():
    """Exclude mode is exactly when a counter is worth the most: the whole
    question is whether the application's traffic is being kept out, and that
    is answered by whether the marking rule fires."""
    ruleset, _ = _render(PolicyMode.EXCLUDE, cidrs=["192.0.2.1/32"])
    for line in _marking_lines(ruleset):
        assert " counter " in f" {line} ", f"no counter on: {line}"


def test_counter_precedes_the_comment_so_nft_accepts_it():
    """nftables parses these as a sequence, and the comment has to come last."""
    ruleset, _ = _render(PolicyMode.INCLUDE, cidrs=["192.0.2.1/32"])
    line = _marking_lines(ruleset)[0]
    assert line.index("counter") < line.index("comment"), line


def test_no_marking_rules_still_yields_a_valid_table():
    """An empty policy produces a table with no marking rules, and that must
    still be loadable rather than a syntax error."""
    ruleset, _ = _render(PolicyMode.INCLUDE)
    assert _marking_lines(ruleset) == []
    assert "chain mark_output {" in ruleset
    assert "type route hook output priority mangle; policy accept;" in ruleset
