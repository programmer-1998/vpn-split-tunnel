"""Routing manager - applies per-app and per-address split tunneling.

Design constraints that come from how a Linux host actually routes, not from
what is convenient to write:

1. **Never touch the VPN client's own interface.** The tunnel is configured by
   the client, and reconfiguring it is what drops live connections. We read the
   interface's addressing; we never add addresses to it, never set it up or
   down, and never replace its routes.

2. **A rule placed after the client's catch-all never runs.** Policy routing
   evaluates rules in ascending priority, and a VPN client typically ends its
   block with a terminal rule (`from all nop` on this machine, at priority
   9010) so nothing leaks around the tunnel. Any override we add has to sit
   *below* that priority to be consulted at all.

3. **The two modes are the same mechanism pointed at different tables.**
   Traffic we mark is routed by a single `ip rule` at a fixed priority. In
   Exclude mode marked traffic is sent to `main`, i.e. the normal route, which
   takes it out of the tunnel. In Include mode marked traffic is sent to our
   own table, whose default route is the tunnel.

4. **The kill switch is mode-specific.** Dropping marked traffic that is not
   leaving via the tunnel is only correct for Include mode. Applied in Exclude
   mode it would drop precisely the traffic the user asked to keep online.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from vpn_split_tunnel.core.policy import PolicyMode, PolicyState, SplitTunnelPolicy
from vpn_split_tunnel.utils.config import get_config

logger = logging.getLogger(__name__)

# Marks are masked with this so we can tell our own mark apart from marks
# other software sets (Tailscale uses 0x80000, WireGuard uses 0xca6c).
_OUR_MARK_MASK = 0xFF0000

# cgroup v2 root of the unified hierarchy, where nftables matches from.
_CGROUP_ROOT = "/sys/fs/cgroup"

# Route lookup actions that end the search with no usable route. A marked
# packet that reaches one of these before our rule is dropped, not rerouted.
_TERMINAL_ACTIONS = frozenset({"nop", "unreachable", "blackhole", "prohibit"})


class RoutingError(Exception):
    """Routing operation error."""


class InsufficientPrivilegesError(RoutingError):
    """Need root privileges."""


@dataclass(frozen=True)
class RoutePlan:
    """What the rules will actually do, derived from the policy and the tunnel.

    Building this before touching anything is what lets the UI tell the user
    the truth about a policy, including the cases where the policy cannot work.
    """

    mode: PolicyMode
    interface: str
    peer: str | None
    fwmark: int
    rule_priority: int
    table: int
    app_ids: tuple[str, ...]
    domains: tuple[str, ...]
    ip_cidrs: tuple[str, ...]
    warnings: tuple[str, ...]
    # The cgroup each selected application actually lives in, discovered from
    # systemd rather than assumed. An nftables rule naming a cgroup that does
    # not exist makes the whole table fail to load, so only paths verified to
    # exist end up in the ruleset.
    cgroups: tuple[tuple[str, str], ...] = ()

    @property
    def selector_count(self) -> int:
        return len(self.app_ids) + len(self.domains) + len(self.ip_cidrs)

    @property
    def matchable_apps(self) -> tuple[str, ...]:
        """Selected applications that are actually running in a known cgroup."""
        return tuple(app_id for app_id, _ in self.cgroups)

    @property
    def unmatched_apps(self) -> tuple[str, ...]:
        """Selected applications with no running process to match."""
        matched = {app_id for app_id, _ in self.cgroups}
        return tuple(app_id for app_id in self.app_ids if app_id not in matched)

    def describe(self) -> str:
        """One line describing where marked traffic goes."""
        if self.selector_count == 0:
            return "No applications or addresses selected, so no traffic is marked."

        target = (
            "your normal route, outside the tunnel"
            if self.mode == PolicyMode.EXCLUDE
            else f"the tunnel on {self.interface}"
        )
        if self.selector_count == 1:
            return f"1 selected target is marked, and marked traffic uses {target}."
        return f"{self.selector_count} selected targets are marked, and marked traffic uses {target}."


def _cgroup_level(cgroup_path: str) -> int:
    """The `level` a cgroup rule needs for this path.

    `socket cgroupv2 level N "PATH"` matches a socket only when `N` equals the
    number of path components in `PATH`, and the socket's cgroup is `PATH`
    itself or somewhere below it.

    That is not documented clearly, and the wrong value fails silently: nftables
    accepts the rule, the counters never move, and every application looks like
    it is being matched when nothing is. A hardcoded `level 2` was in this file
    and matched nothing at all, because no real cgroup path has two components.

    Measured on this machine by generating every (path, level) pair for a known
    cgroup and sending known packets: each ancestor matched at its own depth and
    at no other level. `/user.slice` matched at level 1, the seven-component
    application cgroup matched at level 7, and nothing else matched anything.
    """
    return len([part for part in cgroup_path.split("/") if part])


class RoutingManager:
    """Installs and removes the nftables + policy-routing rules."""

    def __init__(self) -> None:
        # Reentrant, and that is load-bearing. apply_policy() holds this lock
        # and then calls _cleanup_rules(), which calls remove_policy(), which
        # takes the lock again. With a plain Lock that second acquire never
        # returns: the Apply button hung before it even reached the password
        # prompt, so no rules were installed and no error was ever shown --
        # it just stopped. The nesting is intentional (an apply must clear the
        # previous policy first, and only the public remove_policy() knows the
        # full set of artefacts), so the lock is the thing that has to allow
        # it.
        self._lock = threading.RLock()
        self._state = PolicyState()

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    def plan(self, policy: SplitTunnelPolicy, interface: str, peer: str | None) -> RoutePlan:
        """Work out the rule set, and anything that will stop it working.

        This performs no privileged operation and changes nothing, so the UI
        can show the outcome before the user commits to a password prompt.
        """
        cfg = get_config().routing
        app_ids: list[str] = []
        domains: list[str] = []
        cidrs: list[str] = []

        for rule in policy.rules:
            for app_id in rule.app_ids:
                if app_id != ALL_SYSTEM and app_id not in app_ids:
                    app_ids.append(app_id)
            for domain in rule.domains:
                if domain not in domains:
                    domains.append(domain)
            for cidr in rule.ip_cidrs:
                if cidr not in cidrs:
                    cidrs.append(cidr)

        warnings: list[str] = []
        plan = RoutePlan(
            mode=policy.mode,
            interface=interface,
            peer=peer,
            fwmark=cfg.fwmark,
            rule_priority=cfg.rule_priority,
            table=cfg.routing_table,
            app_ids=tuple(app_ids),
            domains=tuple(domains),
            ip_cidrs=tuple(cidrs),
            warnings=tuple(warnings),
        )

        if plan.mode == PolicyMode.INCLUDE and peer is None:
            warnings.append(
                f"The tunnel address of {interface} could not be read, so traffic "
                "cannot be routed into it."
            )

        shadowing = self._rules_that_shadow(cfg.rule_priority, cfg.fwmark)
        if shadowing:
            warnings.append(
                "Your VPN client has terminal routing rules at priority "
                + ", ".join(str(p) for p in shadowing)
                + f", which run before priority {cfg.rule_priority}. Lower the "
                "rule priority in Preferences until it is below those."
            )

        if plan.mode == PolicyMode.INCLUDE:
            capturing = self._catch_all_tunnel_rules(cfg.rule_priority, cfg.fwmark)
            if capturing:
                warnings.append(
                    "Your VPN client already sends all unmarked traffic through "
                    "the tunnel (rule "
                    + ", ".join(str(p) for p in capturing)
                    + "). Include mode can only guarantee that the selected apps "
                    "go through the VPN; it cannot pull the other apps out, "
                    "because the client's own rule catches them first. Use "
                    "Exclude mode to keep traffic out of the tunnel."
                )

        if plan.app_ids:
            discovered = self._discover_cgroups(plan.app_ids)
            plan = RoutePlan(**{**plan.__dict__, "cgroups": discovered})
            unmatched = [
                app_id for app_id in plan.app_ids if app_id not in dict(discovered)
            ]
            if unmatched:
                # "a and b" reads as a list; "a, b" reads as a fragment of a
                # longer list, which is how these sentences get skimmed.
                #
                # The forms are carried together because the sentence has to stay
                # grammatical either way. Building the pronoun into the fixed
                # text produced "start they" and "if they is already open",
                # which is worse than no sentence at all. English needs two of
                # them here: "it" for both, but "they" when it is the subject of
                # a clause -- hence object and subject kept apart.
                if len(unmatched) == 1:
                    names = unmatched[0]
                    verb, them, they, aux = "is", "it", "it", "is"
                else:
                    names = f"{', '.join(unmatched[:-1])} and {unmatched[-1]}"
                    verb, them, they, aux = "are", "them", "they", "are"
                # This text used to say a process can only be put in a cgroup
                # when it starts. That was never tested and it is not what the
                # kernel does. A live process can be moved, by writing its pid
                # to cgroup.procs; the app cannot offer that yet, because telling
                # one application's processes from another is not possible
                # without privileges. So the text says what is actually true:
                # the traffic is not in a group the rules can name, and the one
                # action that fixes it is starting it from here.
                warnings.append(
                    f"{names} {verb} selected but not running in a group the rules "
                    f"can name, so {they} cannot be matched. Traffic is told "
                    f"apart by the group a process is in, and {they} {aux} not "
                    f"in one. Use the start button on the Applications page to "
                    f"start {them} inside {cfg.cgroup_slice}, then apply again. "
                    f"If {they} {aux} already open elsewhere, close {them} "
                    f"first: starting {them} again only hands the request to "
                    f"that copy."
                )

        return RoutePlan(**{**plan.__dict__, "warnings": tuple(warnings)})

    def _discover_cgroups(self, app_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        """Ask systemd where each selected application's processes are.

        A path is only returned when it holds at least one live process. Two
        separate checks, both necessary:

        * the directory must exist, because `nft -f` rejects an entire table when
          one rule names a missing cgroup, and a single stale path would take
          the address and domain rules down with it;
        * something must actually be running in it, because a transient unit
          whose processes have all gone leaves its cgroup behind, still
          registered as active. Trusting the directory reports an application
          as matchable when it is not, generates a rule that matches no packet,
          and tells the user their application is being split-tunneled.
        """
        from vpn_split_tunnel.core.routing.launcher import (
            cgroup_has_processes,
            cgroup_path_for_unit,
            unit_name_for,
        )

        found: list[tuple[str, str]] = []
        for app_id in app_ids:
            path = cgroup_path_for_unit(unit_name_for(app_id))
            if not path:
                continue
            if not (Path(_CGROUP_ROOT) / path.lstrip("/")).is_dir():
                continue
            if not cgroup_has_processes(path, _CGROUP_ROOT):
                continue
            found.append((app_id, path))
        return tuple(found)

    def _catch_all_tunnel_rules(self, priority: int, mark: int) -> list[int]:
        """Priorities where the VPN client captures unmarked traffic.

        A rule with no `fwmark`, no destination restriction and no ingress
        interface restriction applies to every packet, including the ones we
        did not mark. When one of those sends traffic to a non-`main` table,
        the client is already tunnelling everything, and no rule of ours placed
        after it can send the unmarked remainder the other way. Include mode's
        promise of "only these" then cannot be kept, and the user has to be
        told rather than left with a policy that silently does nothing.

        Only rules *after* our own priority matter: anything before it is
        bypassed by marked traffic, and our mode is defined by what happens to
        the traffic we mark.
        """
        try:
            result = subprocess.run(
                ["ip", "-4", "rule", "show"], capture_output=True, text=True, check=False
            )
        except OSError:
            return []
        if result.returncode != 0:
            return []

        capturing: list[int] = []
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if not parts or not parts[0].endswith(":"):
                continue
            try:
                rule_priority = int(parts[0].rstrip(":"))
            except ValueError:
                continue
            if rule_priority <= priority:
                continue
            selectors = parts[1:]
            if "lookup" not in selectors:
                continue
            table = selectors[selectors.index("lookup") + 1]
            if table in ("main", "default", "local"):
                continue
            if _restricts_traffic(selectors):
                continue
            capturing.append(rule_priority)
        return sorted(set(capturing))

    def _rules_that_shadow(self, priority: int, mark: int) -> list[int]:
        """Priorities below ours that terminate lookup for *our* marked packets.

        `nop` and `unreachable` end route lookup with no answer, so a terminal
        rule at a lower priority number than ours means our rule is never
        consulted.

        A terminal rule that selects a different `fwmark` is not in our way:
        Tailscale installs `from all fwmark 0x80000/0xff0000 unreachable` at
        5250, which cannot match the mark we set, and treating it as a
        conflict would be a false alarm. Rules with no `fwmark` selector are
        assumed to match, because that is the safe direction to be wrong in.

        `ip rule show` needs no privileges, and plan() must stay unprivileged:
        the user should see what will happen before being asked for a password.
        """
        try:
            result = subprocess.run(
                ["ip", "-4", "rule", "show"], capture_output=True, text=True, check=False
            )
        except OSError:
            return []
        if result.returncode != 0:
            return []

        shadowing: list[int] = []
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if not parts or not parts[0].endswith(":"):
                continue
            try:
                rule_priority = int(parts[0].rstrip(":"))
            except ValueError:
                continue
            if rule_priority >= priority:
                continue
            selectors = parts[1:]
            if not any(t in _TERMINAL_ACTIONS for t in selectors):
                continue
            if not _fwmark_can_match(selectors, mark):
                continue
            shadowing.append(rule_priority)
        return sorted(shadowing)

    # ------------------------------------------------------------------
    # Applying
    # ------------------------------------------------------------------
    def apply_policy(
        self,
        policy: SplitTunnelPolicy,
        interface: str,
        peer: str | None = None,
    ) -> PolicyState:
        """Install the rules for `policy`, targeting `interface`.

        `peer` is the far end of the tunnel as reported by the detector. It is
        used only to build a route; the interface itself is never reconfigured.
        """
        with self._lock:
            self._cleanup_rules()

            cfg = get_config().routing
            plan = self.plan(policy, interface, peer)
            logger.debug(
                "Apply: mode=%s interface=%s peer=%s selectors=%d",
                policy.mode,
                interface,
                peer,
                plan.selector_count,
            )
            state = PolicyState(
                is_active=False,
                applied_policy=policy,
                vpn_interface=interface,
                vpn_peer=peer,
                fwmark=cfg.fwmark,
                routing_table=cfg.routing_table,
                rule_priority=cfg.rule_priority,
                cgroup_slice=cfg.cgroup_slice,
                marked_count=plan.selector_count,
                warnings=list(plan.warnings),
            )

            try:
                self._install_nftables(plan)
                unresolved = self._populate_address_sets(plan)
                if unresolved:
                    state.warnings.append(
                        "These addresses could not be resolved and are not "
                        "matched by address: " + ", ".join(unresolved)
                    )
                self._install_route(plan)
                self._install_rule(plan)
            except Exception as exc:
                logger.exception("Apply failed: %s", exc)
                state.error = str(exc)
                self._state = state
                # Never leave a half-installed policy behind: a marked packet
                # with no rule to consume the mark is an unpredictable state.
                self._cleanup_rules()
                state.is_active = False
                raise RoutingError(f"Failed to apply policy: {exc}") from exc

            state.is_active = True
            self._state = state
            return state

    def _install_route(self, plan: RoutePlan) -> None:
        """Give marked traffic a table to be routed by.

        Include mode gets a table whose default route is the tunnel. Exclude
        mode routes to `main` directly and needs no table at all, because the
        bypass target is the ordinary system route.
        """
        self._run(["ip", "route", "flush", "table", str(plan.table)], check=False)
        if plan.mode == PolicyMode.EXCLUDE:
            return

        args = ["ip", "route", "add", "default", "dev", plan.interface]
        if plan.peer:
            args += ["via", plan.peer]
        args += ["table", str(plan.table)]
        self._run(args)
        self._run(args[:1] + ["-6"] + args[1:], check=False)

    def _install_rule(self, plan: RoutePlan) -> None:
        """Insert the single rule that consumes our mark.

        The priority is explicit. Left to itself `ip rule add` picks 32765,
        which is after every VPN client's block and therefore never consulted.
        """
        target = "main" if plan.mode == PolicyMode.EXCLUDE else str(plan.table)
        for family in (["ip"], ["ip", "-6"]):
            self._run(
                family
                + [
                    "rule",
                    "add",
                    "pref",
                    str(plan.rule_priority),
                    "fwmark",
                    f"{plan.fwmark}/{_OUR_MARK_MASK:#x}",
                    "lookup",
                    target,
                ]
            )

    def _install_nftables(self, plan: RoutePlan) -> None:
        """Load the marking rules.

        Marking happens in the `output` hook, where `socket cgroupv2` still
        knows which process the packet came from. `skuid`/`gid` are also
        matched because they are the one per-app signal available for
        processes that were not started inside our slice.
        """
        self._run(
            ["nft", "-f", "-"],
            input_data=self._build_nftables(plan),
        )

    def _build_nftables(self, plan: RoutePlan) -> str:
        """Render the nftables ruleset for a plan.

        Per-application rules are emitted only for applications that are
        currently running inside a cgroup systemd reported, and with the path
        it reported. A rule built from the desktop id instead would name a
        cgroup that does not exist, and nftables rejects the entire table when
        one rule names a missing cgroup, which would take the address and
        domain rules down along with it.
        """
        mark = plan.fwmark
        tun = plan.interface
        direction = (
            "out of the tunnel" if plan.mode == PolicyMode.EXCLUDE else "into the tunnel"
        )
        lines: list[str] = [
            "# Generated by VPN Split Tunnel. Removed with 'Remove Rules'.",
            "# Traffic the policy selects is marked here; the ip rule then sends",
            f"# marked traffic {direction}.",
            "table inet vpn_split_tunnel {",
            "  chain mark_output {",
            "    type route hook output priority mangle; policy accept;",
            "    # Loopback must not be marked, or local name resolution breaks.",
            "    oifname \"lo\" return",
            "    # Traffic the tunnel itself emits must not be marked again.",
            f"    oifname \"{tun}\" return",
        ]

        # Every marking rule carries a counter. It costs nothing and it is the
        # only way to tell "the policy is applied and this application's
        # traffic is going through the tunnel" apart from "the policy is applied
        # and this application's traffic is being matched by nothing". A rule
        # that silently never fires looks exactly like a working one from the
        # UI, and the usual cause -- the application was not started inside the
        # slice, so no rule names its cgroup -- is invisible without this.
        for app_id, cgroup_path in plan.cgroups:
            lines.append(
                f"    socket cgroupv2 level {_cgroup_level(cgroup_path)} "
                f'"{cgroup_path}" '
                f'meta mark set 0x{mark:08x} counter comment "app {app_id}"'
            )

        for cidr in plan.ip_cidrs:
            lines.append(
                f"    ip daddr {cidr} meta mark set 0x{mark:08x} "
                f'counter comment "address {cidr}"'
            )
        if plan.domains:
            lines.append(
                f"    ip daddr @vpn_addrs meta mark set 0x{mark:08x} "
                f'counter comment "resolved domain"'
            )
            lines.append(
                f"    ip6 daddr @vpn_addrs6 meta mark set 0x{mark:08x} "
                f'counter comment "resolved domain"'
            )

        lines.append("  }")

        # The kill switch only makes sense when marked traffic must be forced
        # through the tunnel. In Exclude mode it would drop the very traffic
        # the user asked to keep online.
        if plan.mode == PolicyMode.INCLUDE:
            lines += [
                "  chain kill_postrouting {",
                "    type filter hook postrouting priority filter; policy accept;",
                f"    meta mark {mark} oifname != \"{tun}\" counter drop",
                "  }",
            ]

        lines += [
            "  set vpn_addrs  { type ipv4_addr; flags interval; }",
            "  set vpn_addrs6 { type ipv6_addr; flags interval; }",
            "}",
        ]
        return "\n".join(lines) + "\n"

    def _populate_address_sets(self, plan: RoutePlan) -> list[str]:
        """Fill the address sets from the selected domains and CIDRs.

        Domains are resolved now, which means a domain that only resolves
        through the tunnel cannot be matched by address alone. That limitation
        is reported rather than hidden.
        """
        v4: list[str] = []
        v6: list[str] = []
        unresolved: list[str] = []

        for domain in plan.domains:
            try:
                infos = socket.getaddrinfo(domain, None)
            except socket.gaierror:
                unresolved.append(domain)
                logger.debug("Domain %r could not be resolved", domain)
                continue
            for info in infos:
                try:
                    addr = ipaddress.ip_address(info[4][0])
                except ValueError:
                    continue
                bucket = v4 if addr.version == 4 else v6
                if str(addr) not in bucket:
                    bucket.append(str(addr))
            logger.debug("Domain %r resolved to %d address(es)", domain, len(infos))

        for cidr in plan.ip_cidrs:
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                logger.debug("Ignoring unparseable CIDR %r", cidr)
                continue
            bucket = v4 if net.version == 4 else v6
            rendered = str(net)
            if rendered not in bucket:
                bucket.append(rendered)

        if v4:
            self._run(
                [
                    "nft",
                    "add",
                    "element",
                    "inet",
                    "vpn_split_tunnel",
                    "vpn_addrs",
                    "{ " + ", ".join(v4) + " }",
                ]
            )
        if v6:
            self._run(
                [
                    "nft",
                    "add",
                    "element",
                    "inet",
                    "vpn_split_tunnel",
                    "vpn_addrs6",
                    "{ " + ", ".join(v6) + " }",
                ],
                check=False,
            )
        return unresolved

    # ------------------------------------------------------------------
    # Removing
    # ------------------------------------------------------------------
    def remove_policy(self) -> PolicyState:
        """Remove everything this app installed, and nothing else.

        Each command names the exact rule, table and priority that apply
        installed, so nothing belonging to the VPN client or to another tool
        can be matched by accident.
        """
        with self._lock:
            cfg = get_config().routing
            logger.debug(
                "Remove: table=%s fwmark=%#x priority=%d", cfg.routing_table, cfg.fwmark, cfg.rule_priority
            )
            self._run(
                ["nft", "delete", "table", "inet", "vpn_split_tunnel"],
                check=False,
                action="remove",
            )
            for target in ("main", str(cfg.routing_table)):
                for family in (["ip"], ["ip", "-6"]):
                    self._run(
                        family
                        + [
                            "rule",
                            "del",
                            "pref",
                            str(cfg.rule_priority),
                            "fwmark",
                            f"{cfg.fwmark}/{_OUR_MARK_MASK:#x}",
                            "lookup",
                            target,
                        ],
                        check=False,
                        action="remove",
                    )
            self._run(
                ["ip", "route", "flush", "table", str(cfg.routing_table)],
                check=False,
                action="remove",
            )
            self._run(
                ["ip", "-6", "route", "flush", "table", str(cfg.routing_table)],
                check=False,
                action="remove",
            )

            old = self._state
            self._state = PolicyState()
            return old

    def _cleanup_rules(self) -> None:
        """Best-effort removal, used before applying and after a failure."""
        try:
            self.remove_policy()
        except Exception:
            # A failed cleanup must not abort the apply that triggered it.
            pass

    # ------------------------------------------------------------------
    def get_state(self) -> PolicyState:
        return self._state

    def _run(
        self,
        args: list[str],
        check: bool = True,
        input_data: str | None = None,
        action: str = "apply",
    ) -> subprocess.CompletedProcess:
        """Run a privileged command via polkit.

        The GUI never runs these itself; they go through the helper so that
        there is exactly one place holding the privilege escalation.
        """
        from vpn_split_tunnel.core.routing.helper import run_privileged

        logger.debug("run_privileged(%s, action=%s)", " ".join(args), action)
        return run_privileged(args, action=action, check=check, input_data=input_data)


# ----------------------------------------------------------------------
# The pseudo app-id the UI uses for "every application on the system".
ALL_SYSTEM = "__all_system__"


def _restricts_traffic(selectors: list[str]) -> bool:
    """Whether a routing rule applies to only part of the traffic.

    `from all` and `to all` are the printed form of "no restriction", so they
    must not be read as one; a rule carrying them still matches every packet.
    Any other selector, an ingress or egress interface, a mark, a port or a
    `not` prefix, does narrow the rule.
    """
    for index, token in enumerate(selectors):
        if token in ("from", "to"):
            following = selectors[index + 1] if index + 1 < len(selectors) else ""
            if following and following != "all":
                return True
            continue
        if token in ("fwmark", "fwmarkmask", "iif", "oif", "dport", "sport", "not", "uidrange", "tos"):
            return True
    return False


def _fwmark_can_match(selectors: list[str], mark: int) -> bool:
    """Whether a routing rule with these selectors could match `mark`.

    `ip rule` prints its mark selector as `fwmark VALUE/MASK`, or just
    `fwmark VALUE` for a full 32-bit comparison. A rule carrying an `fwmark`
    selector only applies to packets whose mark agrees on the masked bits, so
    Tailscale's `0x80000/0xff0000` rules are irrelevant to a mark that only
    sets low bits.

    A rule with no `fwmark` selector is assumed to match. Other selectors
    (`dport`, `iif`, source address) narrow the rule further, so assuming
    they match is the direction that avoids a false all-clear.
    """
    for index, token in enumerate(selectors):
        if token not in ("fwmark", "fwmarkmask"):
            continue
        spec = ""
        if index + 1 < len(selectors) and not selectors[index + 1].startswith("lookup"):
            spec = selectors[index + 1]
        if not spec:
            continue
        if "/" in spec:
            value_text, mask_text = spec.split("/", 1)
        else:
            value_text, mask_text = spec, "0xffffffff"
        try:
            value = int(value_text, 0)
            mask = int(mask_text, 0)
        except ValueError:
            continue
        return (mark & mask) == (value & mask)
    return True


_routing_manager: RoutingManager | None = None


def get_routing_manager() -> RoutingManager:
    """Return the shared routing manager."""
    global _routing_manager
    if _routing_manager is None:
        _routing_manager = RoutingManager()
    return _routing_manager
