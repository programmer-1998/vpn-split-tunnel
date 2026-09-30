"""Tests for the privilege boundary.

The app's one security-critical property is that the only code running as root
is the installed helper, and that the helper can only run two network tools.
These tests cover that boundary from both sides: the GUI side must not be able
to ask for anything else, and the helper must refuse anything else even if it
is asked directly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vpn_split_tunnel.cli import helper as helper_cli  # noqa: E402
from vpn_split_tunnel.core.routing import helper as escalation  # noqa: E402


class TestHelperAllowlist:
    def test_only_ip_and_nft_are_allowed(self):
        assert helper_cli.ALLOWED_PROGRAMS == frozenset({"ip", "nft"})

    def test_shell_is_refused(self):
        assert helper_cli._resolve("sh") is None

    def test_interpreter_is_refused(self):
        assert helper_cli._resolve("python3") is None

    def test_a_path_is_not_accepted_as_a_program(self):
        # Accepting a path would let a caller pass a program this project
        # installed, or one the user can write to.
        assert helper_cli._resolve("../../bin/sh") is None

    def test_ip_and_nft_resolve_to_real_binaries(self):
        for program in ("ip", "nft"):
            resolved = helper_cli._resolve(program)
            assert resolved is not None, f"{program} was not found on this system"
            assert Path(resolved).is_absolute()

    def test_refusal_does_not_run_anything(self, monkeypatch, capsys):
        def explode(*args, **kwargs):
            raise AssertionError("a refused command must not be executed")

        monkeypatch.setattr(subprocess, "run", explode)
        assert helper_cli._run(["bash", "-c", "id"], None) == 2
        assert "refusing to run" in capsys.readouterr().err


class TestHelperArguments:
    def test_arguments_are_forwarded_after_the_separator(self, monkeypatch):
        recorded = {}

        def fake_run(args, **kwargs):
            recorded["args"] = args
            recorded["kwargs"] = kwargs
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(helper_cli, "_resolve", lambda p: f"/usr/sbin/{p}")
        monkeypatch.setattr(subprocess, "run", fake_run)

        code = helper_cli.main(
            ["apply", "ip", "--", "rule", "add", "pref", "8500", "fwmark", "0x15b3", "lookup", "main"]
        )
        assert code == 0
        assert recorded["args"][0] == "/usr/sbin/ip"
        assert recorded["args"][1:] == [
            "rule", "add", "pref", "8500", "fwmark", "0x15b3", "lookup", "main",
        ]

    def test_the_program_cannot_be_disguised_as_an_argument(self, monkeypatch):
        # Without argparse's choices, `apply ip -- sh -c id` would be a way to
        # run a shell. The program is a named argument here, so that fails.
        with pytest.raises(SystemExit):
            helper_cli.main(["apply", "sh", "--", "-c", "id"])

    def test_apply_and_remove_are_the_two_actions(self, monkeypatch):
        # They map to the two polkit action ids, so this asserts the helper and
        # the installed policy file cannot drift apart silently.
        monkeypatch.setattr(helper_cli, "_resolve", lambda p: f"/usr/sbin/{p}")
        seen = []
        monkeypatch.setattr(
            helper_cli, "_run", lambda argv, stdin: seen.append(argv) or 0
        )
        for action in ("apply", "remove"):
            helper_cli.main([action, "nft", "--", "list", "ruleset"])
        assert [call[0] for call in seen] == ["nft", "nft"]

    def test_an_unknown_action_is_refused(self):
        with pytest.raises(SystemExit):
            helper_cli.main(["elevate", "ip", "--", "rule", "show"])


class TestEscalation:
    def test_gui_side_refuses_a_program_outside_the_allowlist(self):
        with pytest.raises(escalation.PrivilegedCommandRefused) as excinfo:
            escalation.run_privileged(["bash", "-c", "id"])
        assert "allowlist" in str(excinfo.value)

    def test_gui_side_refuses_a_path_to_another_program(self):
        with pytest.raises(escalation.PrivilegedCommandRefused):
            escalation.run_privileged(["/usr/bin/bash", "-c", "id"])

    def test_gui_side_refuses_an_unknown_polkit_action(self, monkeypatch):
        monkeypatch.setattr(escalation, "helper_is_installed", lambda: True)
        monkeypatch.setattr(escalation.shutil, "which", lambda name: "/usr/bin/pkexec")
        with pytest.raises(escalation.PrivilegedCommandRefused):
            escalation.run_privileged(["ip", "rule", "show"], action="do-whatever")

    def test_a_missing_helper_is_reported_before_a_password_prompt(self, monkeypatch):
        # Otherwise the user types their password and is then told the helper
        # is not installed.
        monkeypatch.setattr(escalation, "helper_is_installed", lambda: False)
        with pytest.raises(escalation.PrivilegedCommandRefused) as excinfo:
            escalation.run_privileged(["ip", "rule", "show"])
        assert "not installed" in str(excinfo.value)

    def test_pkexec_receives_the_helper_and_the_action(self, monkeypatch):
        monkeypatch.setattr(escalation, "helper_is_installed", lambda: True)
        monkeypatch.setattr(escalation.shutil, "which", lambda name: "/usr/bin/pkexec")
        recorded = {}

        def fake_run(args, **kwargs):
            recorded["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        escalation.run_privileged(["nft", "list", "ruleset"], action="remove")

        assert recorded["args"][:2] == ["pkexec", escalation.HELPER_PATH]
        assert recorded["args"][2] == "remove"
        assert recorded["args"][3] == "nft"
        assert recorded["args"][-1] == "ruleset"

    def test_a_failing_command_raises_with_the_tools_own_message(self, monkeypatch):
        monkeypatch.setattr(escalation, "helper_is_installed", lambda: True)
        monkeypatch.setattr(escalation.shutil, "which", lambda name: "/usr/bin/pkexec")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda args, **kwargs: subprocess.CompletedProcess(
                args, 1, "", "RTNETLINK answers: Operation not permitted"
            ),
        )
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            escalation.run_privileged(["ip", "rule", "add", "pref", "8500"])
        assert "Operation not permitted" in excinfo.value.stderr
