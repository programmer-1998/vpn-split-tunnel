"""Tests for the split-tunnel routing plan.

The plan is the part of the app that decides what will happen to the user's
traffic. It is tested against the routing tables a real VPN client leaves
behind, because the failure mode is not a crash: it is a policy that installs
cleanly and never takes effect, which is indistinguishable from success unless
it is checked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vpn_split_tunnel.core.policy import (  # noqa: E402
    PolicyMode,
    RoutingRule,
    SplitTunnelPolicy,
)
from vpn_split_tunnel.core.routing.manager import (  # noqa: E402
    ALL_SYSTEM,
    RoutingManager,
    _fwmark_can_match,
    _restricts_traffic,
)

# Exactly what HAPP leaves on this machine while connected: a catch-all into
# its own table at 9001, a terminal nop at 9010 that stops anything leaking
# around the tunnel, and Tailscale's mark-based rules in between.
HAPP_RULES = """\
0:	from all lookup local
5210:	from all fwmark 0x80000/0xff0000 lookup main
5230:	from all fwmark 0x80000/0xff0000 lookup default
5250:	from all fwmark 0x80000/0xff0000 unreachable
5270:	from all lookup 52
9000:	from all to 172.18.0.0/30 lookup 2022
9001:	from all lookup 2022 suppress_prefixlength 0
9002:	not from all dport 53 lookup main suppress_prefixlength 0
9003:	not from all iif lo lookup 2022
9010:	from all nop
32766:	from all lookup main
32767:	from all lookup default
"""


@pytest.fixture
def manager(monkeypatch, tmp_path):
    """A manager whose privileged side never runs and whose ip output is fixed.

    `plan` is documented as non-privileged, so the tests assert that too: if a
    code path reached the polkit helper, `pkexec` would block on a password
    prompt in CI. The fixture makes any attempt to escalate an error.
    """

    def fake_run(args, check=True, input_data=None):
        raise AssertionError(f"plan() must not run privileged commands, got {args!r}")

    monkeypatch.setattr(RoutingManager, "_run", fake_run)

    def fake_ip(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, HAPP_RULES, "")

    monkeypatch.setattr(subprocess, "run", fake_ip)
    return RoutingManager()


def _policy(mode: PolicyMode, **rule_kwargs) -> SplitTunnelPolicy:
    return SplitTunnelPolicy(mode=mode, rules=[RoutingRule(**rule_kwargs)])


# ----------------------------------------------------------------------
# fwmark reasoning
# ----------------------------------------------------------------------
class TestFwmarkMatching:
    def test_tailscales_mark_does_not_conflict_with_ours(self):
        # Tailscale marks 0x80000 with mask 0xff0000; we mark 0x15b3, which has
        # no bits in that byte, so Tailscale's terminal rule at 5250 is not in
        # our way. Reporting it as a conflict would be a false alarm.
        assert not _fwmark_can_match(
            ["from", "all", "fwmark", "0x80000/0xff0000", "unreachable"], 0x15B3
        )

    def test_unmarked_rule_is_assumed_to_match(self):
        # `from all nop` has no mark selector, so it applies to us.
        assert _fwmark_can_match(["from", "all", "nop"], 0x15B3)

    def test_our_own_mark_matches_our_own_rule(self):
        assert _fwmark_can_match(
            ["from", "all", "fwmark", "0x15b3/0xff0000", "unreachable"], 0x15B3
        )

    def test_bare_mark_is_a_full_width_comparison(self):
        assert _fwmark_can_match(["from", "all", "fwmark", "0x15b3", "unreachable"], 0x15B3)
        assert not _fwmark_can_match(["from", "all", "fwmark", "0x20", "unreachable"], 0x15B3)


class TestTrafficRestriction:
    def test_from_all_is_not_a_restriction(self):
        # `from all` is how ip prints "no restriction". Reading it as one would
        # hide HAPP's catch-all rule and make Include mode look viable.
        assert not _restricts_traffic(["from", "all", "lookup", "2022"])

    def test_destination_is_a_restriction(self):
        assert _restricts_traffic(["from", "all", "to", "172.18.0.0/30", "lookup", "2022"])

    def test_negated_port_rule_is_a_restriction(self):
        assert _restricts_traffic(["not", "from", "all", "dport", "53", "lookup", "main"])

    def test_ingress_interface_is_a_restriction(self):
        assert _restricts_traffic(["not", "from", "all", "iif", "lo", "lookup", "2022"])


# ----------------------------------------------------------------------
# The plan
# ----------------------------------------------------------------------
class TestPlan:
    def test_exclude_sends_marked_traffic_to_the_normal_route(self, manager):
        plan = manager.plan(
            _policy(PolicyMode.EXCLUDE, app_ids=["firefox.desktop"]), "tun0", "172.18.0.2"
        )
        assert "outside the tunnel" in plan.describe()

    def test_include_sends_marked_traffic_into_the_tunnel(self, manager):
        plan = manager.plan(
            _policy(PolicyMode.INCLUDE, app_ids=["firefox.desktop"]), "tun0", "172.18.0.2"
        )
        assert "tunnel on tun0" in plan.describe()

    def test_all_system_is_not_counted_as_a_selectable_target(self, manager):
        # "All System" is the absence of a per-app selection, not a target in
        # its own right, and it has no cgroup to match.
        plan = manager.plan(_policy(PolicyMode.INCLUDE, app_ids=[ALL_SYSTEM]), "tun0", "172.18.0.2")
        assert plan.app_ids == ()
        assert plan.selector_count == 0

    def test_duplicate_selectors_are_collapsed(self, manager):
        policy = SplitTunnelPolicy(
            mode=PolicyMode.EXCLUDE,
            rules=[
                RoutingRule(app_ids=["firefox.desktop"], domains=["github.com"]),
                RoutingRule(app_ids=["firefox.desktop"], domains=["github.com"]),
            ],
        )
        plan = manager.plan(policy, "tun0", "172.18.0.2")
        assert plan.app_ids == ("firefox.desktop",)
        assert plan.domains == ("github.com",)

    def test_no_selection_means_nothing_is_marked(self, manager):
        plan = manager.plan(_policy(PolicyMode.EXCLUDE), "tun0", "172.18.0.2")
        assert "No applications or addresses selected" in plan.describe()

    def test_rule_priority_sits_below_the_clients_catch_all(self, manager):
        # Left to itself `ip rule add` picks 32765, which is after HAPP's
        # terminal rule at 9010 and therefore never consulted.
        plan = manager.plan(_policy(PolicyMode.EXCLUDE, app_ids=["a.desktop"]), "tun0", "1.2.3.4")
        assert plan.rule_priority < 9010

    def test_tailscales_rules_are_not_reported_as_shadowing(self, manager):
        plan = manager.plan(
            _policy(PolicyMode.EXCLUDE, app_ids=["a.desktop"]), "tun0", "172.18.0.2"
        )
        assert not any("terminal routing rules" in w for w in plan.warnings)


class TestPlanWarnings:
    def test_include_warns_when_the_client_already_captures_everything(self, manager):
        # HAPP sends all unmarked traffic into table 2022 at priority 9001, so
        # nothing of ours placed after it can send the remainder the other way.
        # Include mode's promise of "only these" cannot be kept here, and the
        # user has to hear that rather than get a policy that does nothing.
        plan = manager.plan(
            _policy(PolicyMode.INCLUDE, app_ids=["firefox.desktop"]), "tun0", "172.18.0.2"
        )
        joined = " ".join(plan.warnings)
        assert "already sends all unmarked traffic" in joined
        assert "9001" in joined

    def test_exclude_does_not_warn_about_the_catch_all(self, manager):
        # Exclude mode is the mode that works on a capturing client, so
        # warning about it would be noise.
        plan = manager.plan(
            _policy(PolicyMode.EXCLUDE, app_ids=["firefox.desktop"]), "tun0", "172.18.0.2"
        )
        assert not any("already sends all unmarked traffic" in w for w in plan.warnings)

    def test_include_warns_when_the_tunnel_address_is_unknown(self, manager):
        plan = manager.plan(
            _policy(PolicyMode.INCLUDE, app_ids=["firefox.desktop"]), "tun0", None
        )
        assert any("could not be read" in w for w in plan.warnings)

    def test_exclude_does_not_need_the_tunnel_address(self, manager):
        # The bypass target is the ordinary system route, which already exists,
        # so a missing peer is irrelevant in this mode.
        plan = manager.plan(
            _policy(PolicyMode.EXCLUDE, app_ids=["firefox.desktop"]), "tun0", None
        )
        assert not any("could not be read" in w for w in plan.warnings)

    def test_a_selected_app_that_is_not_running_is_reported(self, manager, monkeypatch):
        # Per-app rules match by cgroup, and a process can only enter one when
        # it starts, so an app the user selected but never launched matches
        # nothing. The warning has to name it and say what to do about it.
        monkeypatch.setattr(Path, "is_dir", lambda self: False)
        plan = manager.plan(
            _policy(PolicyMode.EXCLUDE, app_ids=["firefox.desktop"]), "tun0", "172.18.0.2"
        )
        assert plan.unmatched_apps == ("firefox.desktop",)
        assert any("not running" in w for w in plan.warnings)

    def test_address_only_policy_needs_no_cgroup(self, manager, monkeypatch):
        monkeypatch.setattr(Path, "is_dir", lambda self: False)
        plan = manager.plan(
            _policy(PolicyMode.EXCLUDE, ip_cidrs=["192.168.1.0/24"]), "tun0", "172.18.0.2"
        )
        assert not plan.unmatched_apps
        assert not any("not running" in w for w in plan.warnings)


class TestPlanIsSideEffectFree:
    def test_planning_never_escalates(self, manager):
        # The fixture raises if _run is called. Reaching it would mean the user
        # gets a password prompt before being told what will happen.
        manager.plan(
            _policy(PolicyMode.INCLUDE, app_ids=["firefox.desktop"]), "tun0", "172.18.0.2"
        )

    def test_plan_is_a_value(self, manager):
        plan = manager.plan(_policy(PolicyMode.EXCLUDE, domains=["github.com"]), "tun0", "1.2.3.4")
        with pytest.raises(Exception):
            plan.interface = "tun9"  # frozen
