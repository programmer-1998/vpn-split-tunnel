"""apply_policy() must complete, and must not deadlock on its own lock.

apply_policy() holds RoutingManager._lock and then calls _cleanup_rules(), which
calls remove_policy(), which takes the lock again. With a plain threading.Lock
that second acquire never returns, so Apply hung before it reached the password
prompt: no rules installed, no error shown, the button simply stopped working.

Two rules shape these tests:

* Every call into the manager runs on a watchdog thread with a timeout. A
  regression here is a *hang*, and a test that hangs instead of failing takes
  the whole suite down with it -- the first version of this file did exactly
  that, since only the first test was guarded.
* Only the privileged call is replaced. It is the one that would need root and
  a password to run for real; the lock and the call order are the subjects, so
  replacing anything else would test the replacement rather than the code.
"""

from __future__ import annotations

import subprocess
import threading

import pytest

from vpn_split_tunnel.core.policy import PolicyMode, RoutingRule, SplitTunnelPolicy
from vpn_split_tunnel.core.routing.manager import RoutingManager

TIMEOUT = 15.0


class Hangs(Exception):
    """The call did not return in time."""


def call_guarded(func, *args, **kwargs):
    """Run `func` on a thread that is abandoned if it blocks.

    A daemon thread is left behind on timeout rather than joined, because joining
    is what would hang the suite. The assertion is made by the caller, not here,
    so the failure message can say which operation was stuck.
    """
    box: dict[str, object] = {}

    def run():
        try:
            box["result"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - handed to the assertion
            box["error"] = exc
        finally:
            box["done"] = True

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=TIMEOUT)
    if not box.get("done"):
        raise Hangs(f"{getattr(func, '__name__', func)} did not return in {TIMEOUT}s")
    if "error" in box:
        raise AssertionError(f"{getattr(func, '__name__', func)} raised: {box['error']}")
    return box.get("result")


def _policy() -> SplitTunnelPolicy:
    policy = SplitTunnelPolicy()
    policy.mode = PolicyMode.INCLUDE
    policy.rules = [RoutingRule(app_ids=["example.desktop"], ip_cidrs=["192.0.2.1/32"])]
    return policy


class Recorder:
    """Stands in for the privileged call, recording every command."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str, bool]] = []
        self._lock = threading.Lock()

    def __call__(self, args, *, action="apply", check=True, input_data=None):
        with self._lock:
            self.calls.append((tuple(args), action, check))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    def verbs(self) -> list[tuple[str, ...]]:
        with self._lock:
            return [c[0] for c in self.calls]


@pytest.fixture
def manager(monkeypatch):
    mgr = RoutingManager()
    rec = Recorder()
    monkeypatch.setattr(RoutingManager, "_run", lambda self, args, **kw: rec(args, **kw))
    mgr.recorder = rec
    return mgr


def test_apply_completes_instead_of_deadlocking(manager):
    """The regression: this used to block forever, before any password prompt."""
    state = call_guarded(manager.apply_policy, _policy(), "tun0", "172.18.0.2")
    assert state.is_active is True
    assert state.error is None


def test_apply_records_what_it_installed(manager):
    """Every keyword apply_policy() passes has to be a real field.

    It passed `vpn_peer` and `rule_priority` to a dataclass that had neither, so
    apply ended in TypeError before installing a single rule. Recording the
    tunnel address is what makes the outcome explainable afterwards.
    """
    state = call_guarded(manager.apply_policy, _policy(), "tun0", "172.18.0.2")
    assert state.vpn_interface == "tun0"
    assert state.vpn_peer == "172.18.0.2"
    assert state.routing_table == 101
    assert state.rule_priority == 8500
    assert state.fwmark == 0x15B3
    assert state.marked_count == 2  # one application, one address


def test_cleanup_happens_before_anything_is_installed(manager):
    call_guarded(manager.apply_policy, _policy(), "tun0", "172.18.0.2")
    verbs = manager.recorder.verbs()

    cleanup = [i for i, v in enumerate(verbs) if v[:2] == ("nft", "delete")]
    installs = [i for i, v in enumerate(verbs) if v[:1] == ("nft",) and "-f" in v]

    assert cleanup, "no cleanup of a previous policy was attempted"
    assert installs, "the nftables ruleset was never installed"
    assert max(cleanup) < min(installs), (
        "rules were installed before the previous policy was cleared: "
        f"cleanup at {cleanup}, install at {installs}"
    )


def test_apply_then_remove_from_sequential_calls(manager):
    call_guarded(manager.apply_policy, _policy(), "tun0", "172.18.0.2")
    old = call_guarded(manager.remove_policy)
    assert old.is_active is True, "remove should report the state it tore down"
    assert manager.get_state().is_active is False


def test_failed_install_leaves_nothing_behind(manager, monkeypatch):
    """A rule that cannot be installed must not leave a half-applied policy.

    The replacement raises exactly as `_run` does when the command fails, rather
    than returning a non-zero status: `_run` is the layer that turns a failed
    command into an exception, and returning a status instead would have this
    test pass without ever reaching the failure path.
    """
    rec = manager.recorder

    def fail_on_ruleset(self, args, **kwargs):
        if args[:1] == ["nft"] and "-f" in args:
            rec(args, **kwargs)
            raise subprocess.CalledProcessError(1, list(args), "", "nft: syntax error")
        return rec(args, **kwargs)

    monkeypatch.setattr(RoutingManager, "_run", fail_on_ruleset)

    with pytest.raises(Exception, match="Failed to apply policy"):
        call_guarded(manager.apply_policy, _policy(), "tun0", "172.18.0.2")

    assert manager.get_state().is_active is False
    verbs = rec.verbs()
    assert verbs, "nothing was recorded"
    # Cleanup must have run after the failure, not only before the install: the
    # ruleset is loaded before the route and the ip rule, so a failure there
    # has already put packets' fate in play.
    deletes = [i for i, v in enumerate(verbs) if v[:2] == ("nft", "delete")]
    installs = [i for i, v in enumerate(verbs) if v[:1] == ("nft",) and "-f" in v]
    assert deletes and installs
    assert max(deletes) > min(installs), (
        "the ruleset was never torn down after a failed install"
    )


def test_lock_is_reentrant_for_the_owning_thread_only():
    """Re-entrancy is for the thread that holds the lock, and only that one.

    Re-entrancy is needed because the nesting above is the same thread taking
    the lock twice. It must not weaken exclusion: a second thread still waits.
    """
    mgr = RoutingManager()
    mgr._lock.acquire()
    try:
        mgr._lock.acquire()  # same thread: must not block
        mgr._lock.release()
    finally:
        mgr._lock.release()

    entered = threading.Event()

    def from_another_thread():
        mgr._lock.acquire()
        entered.set()
        mgr._lock.release()

    mgr._lock.acquire()
    try:
        thread = threading.Thread(target=from_another_thread, daemon=True)
        thread.start()
        thread.join(timeout=3)
        assert not entered.is_set(), "the lock no longer excludes other threads"
    finally:
        mgr._lock.release()
    thread.join(timeout=TIMEOUT)
    assert entered.is_set(), "the lock was never released"
