"""Privilege escalation, in one place.

Everything that needs root goes through `run_privileged`. There is exactly one
escalation mechanism in this project so that it can be audited as one thing.

The mechanism is polkit, reached through the installed helper at
`/usr/libexec/vpn-split-tunnel-helper`. Two reasons for a fixed helper rather
than `pkexec` on the target program directly:

* The installed policy file names the helper, so the authorisation the user
  grants is scoped to *this application's* helper. Authorising `pkexec ip`
  instead would create a system-wide grant to run `ip` as root, which is a
  much larger privilege than this app needs to hold.
* The helper only executes an allowlisted program, so the escalation cannot be
  steered into running something else even if a caller is compromised.

`plan` in the routing manager is documented as doing no privileged work, and
that is enforced there rather than assumed: it reads `ip rule show` with a
plain subprocess, because the user should learn what will happen to their
traffic before being asked for a password.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

HELPER_PATH = "/usr/libexec/vpn-split-tunnel-helper"

logger = logging.getLogger(__name__)

# Which polkit action to request. Splitting apply from remove means a user can
# grant one without permanently granting the other.
_ACTION_APPLY = "apply"
_ACTION_REMOVE = "remove"


class PrivilegedCommandRefused(RuntimeError):
    """A privileged command was requested that is not on the allowlist."""


def helper_is_installed() -> bool:
    """Whether the privileged helper is present on this system.

    The GUI uses this to explain itself before offering to apply rules, rather
    than letting the user type a password only to be told the helper is absent.
    """
    return shutil.which(HELPER_PATH) is not None or _is_executable_file(HELPER_PATH)


def _is_executable_file(path: str) -> bool:
    import os

    return os.path.isfile(path) and os.access(path, os.X_OK)


def run_privileged(
    args: list[str],
    *,
    action: str = _ACTION_APPLY,
    check: bool = True,
    input_data: str | None = None,
) -> subprocess.CompletedProcess:
    """Run `args[0]` with root privileges, asking the user for permission.

    `args` is `["ip", "route", ...]` or `["nft", ...]`. The program is passed to
    the helper by name; the helper re-checks it against its own allowlist, so
    this function's check is a fast failure, not the security boundary.

    Raises PrivilegedCommandRefused when the program is not allowlisted or the
    helper is not installed, and subprocess.CalledProcessError when `check` is
    set and the command fails.
    """
    if not args:
        raise PrivilegedCommandRefused("no command given")

    program = args[0].rsplit("/", 1)[-1]
    if program not in ("ip", "nft"):
        raise PrivilegedCommandRefused(
            f"{program!r} is not on the privileged allowlist ('ip', 'nft')"
        )

    if action not in (_ACTION_APPLY, _ACTION_REMOVE):
        raise PrivilegedCommandRefused(f"unknown polkit action {action!r}")

    if not helper_is_installed():
        raise PrivilegedCommandRefused(
            f"{HELPER_PATH} is not installed, so there is no way to ask for "
            "permission. Reinstall the application, or run its install step, "
            "and try again."
        )

    if not shutil.which("pkexec"):
        raise PrivilegedCommandRefused(
            "pkexec is not installed, so there is no way to ask for permission"
        )

    result = subprocess.run(
        ["pkexec", HELPER_PATH, action, program, "--", *args[1:]],
        input=input_data,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        logger.error(
            "Privileged command failed (rc=%d): %s",
            result.returncode,
            " ".join(args),
        )
        if result.stdout.strip():
            logger.error("  stdout: %s", result.stdout.strip())
        if result.stderr.strip():
            logger.error("  stderr: %s", result.stderr.strip())
        raise subprocess.CalledProcessError(
            result.returncode,
            args,
            output=result.stdout,
            stderr=result.stderr,
        )
    logger.debug("Privileged command ok (rc=%d): %s", result.returncode, " ".join(args))
    return result
