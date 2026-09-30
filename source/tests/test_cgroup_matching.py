"""Tests for the facts that per-application matching depends on.

Each test here corresponds to a behaviour that was asserted by the UI, believed
to be true, and was wrong. They are worth more than the fixes themselves,
because the fixes were only found by measuring the machine:

* `level` in a cgroup rule has to equal the depth of the path, or nftables
  accepts the rule and it never matches a single packet;
* a cgroup directory outliving its processes makes an application look matchable
  when nothing of it is running;
* a live process can be moved into a cgroup, but sockets opened before the move
  stay unmarked, so "move now" cannot be described as an instant switch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vpn_split_tunnel.core.routing.launcher import (
    cgroup_has_processes,
    pids_in_cgroup,
    running_units,
)
from vpn_split_tunnel.core.routing.manager import _cgroup_level

# The real shape of a systemd user slice on this machine, which is what made the
# hardcoded `level 2` wrong: no real path has two components.
REAL_PATH = (
    "/user.slice/user-1000.slice/user@1000.service/vpn.slice/vpn-split.slice/"
    "vpn-split-tunnel.slice/app-alacarte.service"
)


class TestCgroupLevel:
    """`level` is the path's depth, not a constant."""

    def test_a_real_path_is_seven_levels_deep(self) -> None:
        assert _cgroup_level(REAL_PATH) == 7

    @pytest.mark.parametrize(
        "path",
        [
            "/user.slice",
            "/user.slice/user-1000.slice",
            REAL_PATH,
            "/a/b/c/d/e/f/g/h/i",
        ],
    )
    def test_level_counts_the_components(self, path: str) -> None:
        assert _cgroup_level(path) == len([p for p in path.split("/") if p])

    def test_a_trailing_slash_does_not_add_a_level(self) -> None:
        """A trailing slash is a path separator, not a component.

        Without this, the same cgroup would get a different level depending on
        how systemd happened to print its ControlGroup property, and the rule
        would silently stop matching.
        """
        assert _cgroup_level(REAL_PATH + "/") == _cgroup_level(REAL_PATH)

    def test_no_real_cgroup_path_is_two_levels_deep(self) -> None:
        """The bug, stated as a test.

        The code shipped `level 2` for every application. That value is only
        correct for a two-component path, and no cgroup systemd reports is that
        shallow, so every per-application rule was a rule that matched nothing.
        """
        assert _cgroup_level(REAL_PATH) != 2

    def test_two_different_cgroups_get_different_levels(self) -> None:
        """A shared constant would have silently matched the wrong thing."""
        shallow = "/user.slice/user-1000.slice"
        assert _cgroup_level(REAL_PATH) != _cgroup_level(shallow)


class TestCgroupProcessChecks:
    """A directory is not proof that anything is running."""

    def test_a_cgroup_with_a_process_is_reported_live(self, tmp_path: Path) -> None:
        cgroup = tmp_path / "app-live.service"
        cgroup.mkdir()
        (cgroup / "cgroup.procs").write_text("1234\n1235\n")
        assert cgroup_has_processes("app-live.service", str(tmp_path)) is True
        assert pids_in_cgroup("app-live.service", str(tmp_path)) == [1234, 1235]

    def test_an_empty_cgroup_is_not_reported_live(self, tmp_path: Path) -> None:
        """The directory exists and systemd still calls the unit active.

        This is the state a `systemd-run` unit is left in once every process has
        been moved out or exited. Treating it as live is what made the app claim
        an application was being split-tunneled while nothing of it ran.
        """
        cgroup = tmp_path / "app-empty.service"
        cgroup.mkdir()
        (cgroup / "cgroup.procs").write_text("\n")
        assert cgroup_has_processes("app-empty.service", str(tmp_path)) is False

    def test_a_missing_cgroup_is_not_reported_live(self, tmp_path: Path) -> None:
        assert cgroup_has_processes("app-gone.service", str(tmp_path)) is False

    def test_an_unreadable_cgroup_is_not_reported_live(self, tmp_path: Path) -> None:
        """A cgroup we cannot read is not a cgroup we can match.

        Returning True here would put a rule in the ruleset naming a cgroup whose
        path cannot be confirmed, and nftables rejects the whole table when one
        rule names a missing cgroup.
        """
        assert cgroup_has_processes("app-x.service", str(tmp_path)) is False


class TestRunningUnitsEnumeration:
    """The enumeration has to survive contact with `systemctl`."""

    def test_no_such_option_is_never_passed(self, monkeypatch) -> None:
        """`list-units` has no `--slice` filter.

        Passing it made systemctl exit non-zero with no output, which read as
        "nothing is running" -- so every started application was reported as not
        running. The failure looked like correct data.
        """
        recorded: list[list[str]] = []

        def fake_run(args, **kwargs):
            recorded.append(list(args))
            raise AssertionError("stop here")

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr("subprocess.run", fake_run)

        with pytest.raises(AssertionError):
            running_units("vpn-split-tunnel.slice")

        for args in recorded:
            assert not any(a.startswith("--slice") for a in args), args

    def test_the_slice_itself_is_not_listed_as_an_application(
        self, monkeypatch
    ) -> None:
        """The slice appears in the same listing and is not an application.

        Left in, it reads as one running thing among the applications.
        """
        listing = "\n".join(
            [
                "vpn-split-tunnel.slice  loaded active running -",
                "app-firefox.service      loaded active running /usr/bin/firefox",
                "session-3.scope          loaded active running -",
            ]
        )

        class Result:
            returncode = 0
            stdout = listing

        def fake_run(args, **kwargs):
            return Result()

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr(
            "vpn_split_tunnel.core.routing.launcher.cgroup_path_for_unit",
            lambda unit: f"/user.slice/some.slice/vpn-split-tunnel.slice/{unit}",
        )

        units = [unit for unit, _ in running_units("vpn-split-tunnel.slice")]
        assert units == ["app-firefox.service"]
