"""Tests for launching applications into a cgroup.

The kernel can tell one application's traffic from another's only by the
cgroup its sockets were created in, and a process can only be put in a cgroup
when it starts. These tests cover the part that can be checked without root:
turning a desktop id into a command, turning that into a unit name, and
reporting an unusable situation instead of launching something wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vpn_split_tunnel.core.routing import launcher  # noqa: E402


class TestUnitNames:
    def test_desktop_suffix_is_dropped(self):
        assert launcher.unit_name_for("firefox.desktop") == "app-firefox"

    def test_dots_become_dashes(self):
        # systemd unit names may not contain '.', so an id that keeps them
        # would produce a unit systemd refuses to create.
        assert launcher.unit_name_for("org.gnome.Nautilus.desktop") == "app-org-gnome-nautilus"

    def test_case_is_normalised(self):
        assert launcher.unit_name_for("My_App.desktop") == "app-my-app"

    def test_the_same_app_always_gets_the_same_scope(self):
        # A relaunch has to reuse the scope, or the nftables rule written for
        # the previous run would point at a cgroup that no longer exists.
        assert launcher.unit_name_for("firefox") == launcher.unit_name_for("firefox.desktop")

    def test_names_never_start_with_a_dash(self):
        for app_id in ("-weird.desktop", ".hidden.desktop", "---.desktop"):
            assert not launcher.unit_name_for(app_id).startswith("-")


class TestExecParsing:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("firefox %u", "firefox"),
            ("nautilus --new-window %U", "nautilus"),
            ("/opt/a/b/c --flag", "/opt/a/b/c"),
            ('sh -c "x y"', "sh"),
            ("gnome-calculator --private", "gnome-calculator"),
            # Alternatives: the desktop spec says the first is the default.
            ("weird | other", "weird"),
        ],
    )
    def test_the_program_is_the_first_token(self, line, expected):
        assert launcher._first_exec_token(line) == expected

    def test_a_leading_environment_assignment_is_skipped(self):
        # `env FOO=bar /usr/bin/thing` must start `/usr/bin/thing`. Starting
        # `env` instead exits successfully having done nothing at all, so the
        # application silently never opens and the launch looks successful.
        assert launcher._exec_tokens("env FOO=bar /usr/bin/thing %U") == [
            "/usr/bin/thing"
        ]
        assert launcher._exec_tokens("FOO=bar /usr/bin/thing") == ["/usr/bin/thing"]

    def test_a_leading_option_is_refused(self):
        # Nothing here names a program, so guessing would be arbitrary.
        assert launcher._first_exec_token("--leading-dash") is None
        assert launcher._first_exec_token("") is None
        assert launcher._first_exec_token("   ") is None

    def test_quoted_arguments_do_not_split_the_program(self):
        assert launcher._first_exec_token("'my app' --go") == "my app"

    def test_unbalanced_quotes_are_declined_rather_than_guessed(self):
        # One of the two readings is a different program. Picking either would
        # run something the user did not ask for, so nothing is run.
        assert launcher._exec_tokens("'unclosed --go") == []

    def test_the_first_alternative_is_used(self):
        # The desktop entry specification says to use the first alternative.
        assert launcher._exec_tokens("/usr/bin/real %U | /usr/bin/fallback") == [
            "/usr/bin/real"
        ]


class TestCommandLookup:
    def test_the_whole_command_is_returned_not_just_the_program(self):
        """A Flatpak's program is `flatpak`, which is useless on its own.

        Starting `/usr/bin/flatpak` with no arguments opens the Flatpak manager
        rather than the application the user picked, and the per-application
        rule would then be attached to the wrong process.
        """
        argv = launcher.command_for("org.gnome.Calculator")
        if not argv:
            pytest.skip("no desktop entry available to probe the Exec grammar with")

        # A program, and not a fragment of a flag string.
        assert Path(argv[0]).name
        assert not Path(argv[0]).name.startswith("-")
        # No field codes survive, because none is supplied.
        assert not any(arg.startswith("%") for arg in argv)

    def test_a_flatpak_entry_keeps_its_run_arguments(self, tmp_path, monkeypatch):
        # The shape of a Flatpak entry, which is the case that made the
        # program-only lookup wrong.
        apps = tmp_path / "applications"
        apps.mkdir()
        (apps / "org.example.App.desktop").write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=App\n"
            "Exec=/usr/bin/flatpak run --branch=stable --command=app org.example.App %U\n"
        )
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))

        assert launcher.command_for("org.example.App") == [
            "/usr/bin/flatpak",
            "run",
            "--branch=stable",
            "--command=app",
            "org.example.App",
        ]


class TestExecutableLookup:
    def test_a_real_desktop_entry_resolves(self):
        # alacarte.desktop ships with the system and its Exec= is `alacarte`.
        # If this fails, the lookup path is wrong rather than the entry.
        assert launcher.executable_for("alacarte") == "alacarte"

    def test_an_unknown_id_resolves_to_nothing(self):
        # Returning None here is what makes the UI say "no desktop entry"
        # instead of attempting to run a command that does not exist.
        assert launcher.executable_for("definitely-not-an-app-12345") is None

    def test_the_desktop_id_is_not_the_command(self, tmp_path, monkeypatch):
        # The whole reason for reading Exec= : the id and the command differ.
        apps = tmp_path / "applications"
        apps.mkdir()
        (apps / "com.example.thing.desktop").write_text(
            "[Desktop Entry]\nName=Thing\nExec=/opt/thing/bin/real-name %U\n"
        )
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))
        assert launcher.executable_for("com.example.thing") == "/opt/thing/bin/real-name"

    def test_the_applications_subdirectory_is_searched(self, tmp_path, monkeypatch):
        # Desktop entries live in <data dir>/applications, not in the data dir
        # itself. Getting this wrong finds nothing and looks like a system
        # with no applications installed.
        apps = tmp_path / "applications"
        apps.mkdir()
        (apps / "flat.desktop").write_text("[Desktop Entry]\nName=F\nExec=flat-bin\n")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))
        assert launcher.executable_for("flat") == "flat-bin"


class TestLaunchRefusals:
    def test_no_desktop_entry_is_an_actionable_error(self, monkeypatch):
        monkeypatch.setattr(launcher, "command_for", lambda app_id: None)
        with pytest.raises(launcher.LaunchError) as excinfo:
            launcher.launch("nothing-here", "vpn-split-tunnel.slice")
        assert "No desktop entry" in str(excinfo.value)

    def test_missing_systemd_run_is_an_actionable_error(self, monkeypatch):
        # The message says what still works, because address and domain rules
        # do not depend on systemd.
        monkeypatch.setattr(launcher, "command_for", lambda app_id: ["firefox"])
        monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
        with pytest.raises(launcher.LaunchError) as excinfo:
            launcher.launch("firefox", "vpn-split-tunnel.slice")
        assert "systemd-run" in str(excinfo.value)
        assert "still work" in str(excinfo.value)

    def test_a_failing_systemd_run_reports_its_own_message(self, monkeypatch):
        monkeypatch.setattr(launcher, "command_for", lambda app_id: ["firefox"])
        monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda args, **kwargs: subprocess.CompletedProcess(
                args, 1, "", "Failed to create slice: Unit already exists"
            ),
        )
        with pytest.raises(launcher.LaunchError) as excinfo:
            launcher.launch("firefox", "vpn-split-tunnel.slice")
        assert "Unit already exists" in str(excinfo.value)

    def test_an_unknown_cgroup_is_reported_rather_than_returned(self, monkeypatch):
        # Returning a path that does not exist is the one failure that must
        # never happen: an nftables rule naming a missing cgroup makes the
        # whole table fail to load, taking the address rules with it.
        monkeypatch.setattr(launcher, "command_for", lambda app_id: ["firefox"])
        monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", "")
        )
        monkeypatch.setattr(launcher, "cgroup_path_for_unit", lambda unit: "/user.slice/gone.scope")
        monkeypatch.setattr(Path, "is_dir", lambda self: False)
        with pytest.raises(launcher.LaunchError) as excinfo:
            launcher.launch("firefox", "vpn-split-tunnel.slice")
        assert "does not exist" in str(excinfo.value)


class TestCgroupPathLookup:
    def test_the_path_comes_from_systemd(self, monkeypatch):
        # The shape depends on the session, so it is read back rather than
        # constructed.
        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda args, **kwargs: subprocess.CompletedProcess(
                args, 0, "/user.slice/user-1000.slice/app.slice/app-firefox.scope\n", ""
            ),
        )
        assert (
            launcher.cgroup_path_for_unit("app-firefox.scope")
            == "/user.slice/user-1000.slice/app.slice/app-firefox.scope"
        )

    def test_the_root_path_means_no_cgroup(self, monkeypatch):
        # systemd reports "/" for a unit that is not in the hierarchy. Treating
        # that as a path would produce a rule matching the whole machine.
        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "/\n", ""),
        )
        assert launcher.cgroup_path_for_unit("app-firefox.scope") is None

    def test_a_timeout_does_not_raise(self, monkeypatch):
        # A hung systemd must not take the UI down with it.
        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemctl")

        def explode(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 10)

        monkeypatch.setattr(subprocess, "run", explode)
        assert launcher.cgroup_path_for_unit("app-firefox.scope") is None


class TestNftablesUsesOnlyRealCgroups:
    """The generated ruleset must load, and a stale path would stop it."""

    def test_a_generated_ruleset_names_only_reported_cgroups(self):
        from vpn_split_tunnel.core.policy import PolicyMode, RoutingRule, SplitTunnelPolicy
        from vpn_split_tunnel.core.routing.manager import RoutingManager

        manager = RoutingManager()
        plan = manager.plan(
            SplitTunnelPolicy(
                mode=PolicyMode.EXCLUDE,
                rules=[RoutingRule(app_ids=["firefox"], domains=["github.com"])],
            ),
            "tun0",
            "172.18.0.2",
        )
        # Nothing is running, so nothing is named, and the ruleset is still
        # valid: the address rules are unaffected by the missing app.
        ruleset = manager._build_nftables(plan)
        assert "socket cgroupv2" not in ruleset
        assert "ip daddr @vpn_addrs" in ruleset

    def test_a_cgroup_rule_carries_the_level_its_path_needs(self, monkeypatch):
        """The emitted rule must be one nftables will actually match.

        `level` has to equal the depth of the cgroup path. The code used to
        write a fixed `level 2`, which nftables accepts without complaint and
        which never matches a packet, because a systemd user cgroup path is far
        deeper than two components. That failure is invisible from the
        generated text alone -- the rule looks right -- so it is pinned here
        against a path of the real shape.
        """
        from vpn_split_tunnel.core.policy import PolicyMode, RoutingRule, SplitTunnelPolicy
        from vpn_split_tunnel.core.routing.manager import RoutingManager

        real_path = (
            "/user.slice/user-1000.slice/user@1000.service/vpn.slice/"
            "vpn-split.slice/vpn-split-tunnel.slice/app-firefox.service"
        )
        monkeypatch.setattr(
            "vpn_split_tunnel.core.routing.manager.Path.is_dir",
            lambda self: True,
            raising=False,
        )
        monkeypatch.setattr(
            "vpn_split_tunnel.core.routing.launcher.cgroup_path_for_unit",
            lambda unit: real_path,
        )
        monkeypatch.setattr(
            "vpn_split_tunnel.core.routing.launcher.cgroup_has_processes",
            lambda path, root="/sys/fs/cgroup": True,
        )

        manager = RoutingManager()
        plan = manager.plan(
            SplitTunnelPolicy(
                mode=PolicyMode.EXCLUDE, rules=[RoutingRule(app_ids=["firefox"])]
            ),
            "tun0",
            "172.18.0.2",
        )
        ruleset = manager._build_nftables(plan)

        assert 'socket cgroupv2 level 7 "firefox-path"' not in ruleset
        expected = f'socket cgroupv2 level 7 "{real_path}"'
        assert expected in ruleset, ruleset
        assert "level 2 " not in ruleset, ruleset

    def test_the_kill_switch_is_include_only(self):
        from vpn_split_tunnel.core.policy import PolicyMode, RoutingRule, SplitTunnelPolicy
        from vpn_split_tunnel.core.routing.manager import RoutingManager

        manager = RoutingManager()
        policy = SplitTunnelPolicy(
            mode=PolicyMode.EXCLUDE, rules=[RoutingRule(ip_cidrs=["10.0.0.0/8"])]
        )
        plan = manager.plan(policy, "tun0", "172.18.0.2")
        # In Exclude mode marked traffic is meant to leave by the normal route,
        # so dropping anything not leaving via the tunnel would drop exactly
        # the traffic the user asked to keep online.
        assert "kill_postrouting" not in manager._build_nftables(plan)

        plan = manager.plan(
            SplitTunnelPolicy(
                mode=PolicyMode.INCLUDE, rules=[RoutingRule(ip_cidrs=["10.0.0.0/8"])]
            ),
            "tun0",
            "172.18.0.2",
        )
        assert "kill_postrouting" in manager._build_nftables(plan)


class TestLaunchRefusesWhenItWouldNotAchieveAnything:
    """A start that cannot put the application in the slice must not claim it did.

    A single-instance application that is already running accepts the start
    request, spawns a process inside the slice, and that process exits a moment
    later having handed the request to the running copy. The scope is created
    and stays registered as `active (running)` with nothing in it.

    Reporting success there made the application list show it as running where
    the rules can see it, made the plan count it, and emitted an nftables rule
    naming an empty cgroup -- so the user was told their application was
    split-tunneled while none of its connections were marked.
    """

    def _prepare(self, monkeypatch, *, procs_after_start: list[list[str]]):
        """Fake systemd and cgroupfs so the start path can be driven end to end."""
        import vpn_split_tunnel.core.routing.launcher as launcher

        fake_path = "/user.slice/u.slice/vpn-split-tunnel.slice/app-x.service"

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemd-run")
        monkeypatch.setattr(launcher, "command_for", lambda app_id: ["/usr/bin/x"])
        monkeypatch.setattr(launcher, "running_pids_for", lambda exe: [])
        monkeypatch.setattr(launcher, "cgroup_path_for_unit", lambda unit: fake_path)
        monkeypatch.setattr(launcher, "_unit_main_pid", lambda unit: 4242)
        # systemd-run itself: the command must not be resolved for real, and a
        # real failure here would be indistinguishable from the case under test.
        monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Result())

        calls = {"n": 0}

        def fake_has_processes(path, root=None):
            # Simulates the real handoff: a process is briefly there, then not.
            seq = procs_after_start[min(calls["n"], len(procs_after_start) - 1)]
            calls["n"] += 1
            return bool(seq)

        monkeypatch.setattr(launcher, "cgroup_has_processes", fake_has_processes)
        monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(launcher.time, "monotonic", lambda: _clock(calls))
        return fake_path

    def test_a_handed_off_start_is_reported_as_a_failure(self, monkeypatch, tmp_path):
        import vpn_split_tunnel.core.routing.launcher as launcher

        # Present for the first few polls, then empty: the single-instance case.
        self._prepare(monkeypatch, procs_after_start=[["1"], [], [], [], []])
        monkeypatch.setattr(launcher.Path, "is_dir", lambda self: True, raising=False)

        with pytest.raises(launcher.LaunchError) as err:
            launcher.launch("x.desktop", "vpn-split-tunnel.slice")
        assert "already open somewhere else" in str(err.value)

    def test_a_start_that_stays_is_reported_as_a_success(self, monkeypatch):
        import vpn_split_tunnel.core.routing.launcher as launcher

        path = self._prepare(monkeypatch, procs_after_start=[["1"], ["1"], ["1"], ["1"]])
        monkeypatch.setattr(launcher.Path, "is_dir", lambda self: True, raising=False)

        result = launcher.launch("x.desktop", "vpn-split-tunnel.slice")
        assert result.cgroup_path == path
        assert result.pid == 4242

    def test_an_application_already_in_the_slice_is_not_started_again(self, monkeypatch):
        """It is already where it needs to be; a second copy would only hand off."""
        import vpn_split_tunnel.core.routing.launcher as launcher

        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemd-run")
        monkeypatch.setattr(launcher, "command_for", lambda app_id: ["/usr/bin/x"])
        monkeypatch.setattr(launcher, "running_pids_for", lambda exe: [11, 12])
        monkeypatch.setattr(launcher, "pids_outside_slice", lambda pids, slice_name: [])

        def must_not_run(args, **kwargs):
            raise AssertionError(f"systemd must not be asked to start it again: {args}")

        monkeypatch.setattr(launcher.subprocess, "run", must_not_run)

        with pytest.raises(launcher.LaunchError) as err:
            launcher.launch("x.desktop", "vpn-split-tunnel.slice")
        assert "already running in the split group" in str(err.value)

    def test_an_application_running_outside_is_refused_before_systemd_is_touched(
        self, monkeypatch
    ):
        """Refused before anything is started, not cleaned up afterwards.

        Starting it first is what leaves processes behind in the slice.
        """
        import vpn_split_tunnel.core.routing.launcher as launcher

        monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemd-run")
        monkeypatch.setattr(launcher, "command_for", lambda app_id: ["/usr/bin/x"])
        monkeypatch.setattr(launcher, "running_pids_for", lambda exe: [11, 12, 13])
        monkeypatch.setattr(launcher, "pids_outside_slice", lambda pids, slice_name: [11, 12])

        def must_not_run(args, **kwargs):
            raise AssertionError(f"systemd must not be asked to start it: {args}")

        monkeypatch.setattr(launcher.subprocess, "run", must_not_run)

        with pytest.raises(launcher.LaunchError) as err:
            launcher.launch("x.desktop", "vpn-split-tunnel.slice")
        assert "already running" in str(err.value)
        assert "splitting" in str(err.value)


def _clock(calls) -> float:
    """A monotonic clock that advances with each poll.

    The settling check depends on elapsed time, and a real sleep would make this
    test take seconds for no benefit.
    """
    return calls["n"] * 0.1
