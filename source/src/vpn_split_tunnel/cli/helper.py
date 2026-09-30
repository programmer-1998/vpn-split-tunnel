"""Privileged helper.

Invoked through polkit as `/usr/libexec/vpn-split-tunnel-helper`, this is the
only code in the project that runs as root. Its job is to run one allowlisted
network tool and nothing else.

Two shapes of privileged helper are possible, and the difference matters:

1. A helper that takes a *policy document* and works out what to do. Convenient,
   and it means the elevated process has to be able to interpret input it was
   handed. Anything that can influence that input is an escalation path.

2. A helper that takes a program name from a fixed allowlist plus arguments,
   and executes it. It contains no policy logic, so there is nothing for a
   caller to reinterpret. This is what is here.

The allowlist is duplicated from `vpn_split_tunnel.core.routing.helper` on
purpose. That module is the one that decides what to ask for; if it shared the
list, editing the list would mean editing the security boundary. Here the list
is a short literal that can be read on its own.

Deliberately absent: any code that configures a VPN interface. Adding an
address to, or changing the state of, a tunnel owned by another program is how
live connections get dropped.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys

# The only programs this helper will execute. Nothing here can reconfigure a
# network interface, and nothing here can start another program.
ALLOWED_PROGRAMS = frozenset({"ip", "nft"})

# Programs that live in sbin on some distributions and bin on others; the
# caller passes a bare name and the absolute path is resolved here, so the
# caller's string is never used to build a path.
_SEARCH_DIRS = ("/usr/sbin", "/usr/bin", "/sbin", "/bin")


def _resolve(program: str) -> str | None:
    """Absolute path of an allowlisted program, or None if it is not allowed."""
    if program not in ALLOWED_PROGRAMS:
        return None
    for directory in _SEARCH_DIRS:
        candidate = os.path.join(directory, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run(argv: list[str], stdin: str | None) -> int:
    """Execute an allowlisted program and relay its result to the caller.

    stdout and stderr are passed through unchanged so the GUI sees the tool's
    own diagnostics, and the exit status is the tool's, so `check=True`
    semantics survive the trip through polkit.
    """
    if not argv:
        print("no command given", file=sys.stderr)
        return 2

    path = _resolve(argv[0])
    if path is None:
        print(
            f"refusing to run {argv[0]!r}: allowed programs are "
            f"{', '.join(sorted(ALLOWED_PROGRAMS))}",
            file=sys.stderr,
        )
        return 2

    try:
        result = subprocess.run(
            [path, *argv[1:]],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(f"could not run {path}: {exc}", file=sys.stderr)
        return 1

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    The first argument selects the polkit action: `apply` and `remove` are
    the two action ids the installed policy file defines, and keeping them
    distinct means a user can be asked for permission to install rules without
    also being asked to authorise removing them later.
    """
    parser = argparse.ArgumentParser(
        prog="vpn-split-tunnel-helper",
        description="Run an allowlisted network tool with elevated privileges.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    for name, help_text in (
        ("apply", "install split tunneling rules"),
        ("remove", "remove split tunneling rules"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument(
            "program",
            choices=sorted(ALLOWED_PROGRAMS),
            help="the network tool to run",
        )
        sub.add_argument(
            "args",
            nargs=argparse.REMAINDER,
            help="arguments passed to the tool; a leading -- is ignored",
        )

    args = parser.parse_args(argv)

    forwarded = list(args.args)
    if forwarded and forwarded[0] == "--":
        forwarded.pop(0)

    return _run([args.program, *forwarded], stdin=_read_piped_stdin())


def _read_piped_stdin() -> str | None:
    """The piped ruleset, or None when stdin is not a pipe.

    Reading stdin here rather than in the parent means the caller can stream a
    ruleset in without putting it on a command line, where it would be visible
    in the process list.

    The pipe check is not cosmetic. Under polkit the helper inherits the
    session's stdin, which is a terminal, and reading a terminal to EOF blocks
    until the user presses a key; the check has to be "is something actually
    piped" rather than "is it a tty", because a redirected file is equally not
    something to read from.
    """
    try:
        if not stat.S_ISFIFO(os.fstat(sys.stdin.fileno()).st_mode):
            return None
    except (OSError, ValueError):
        return None
    return sys.stdin.read()


if __name__ == "__main__":
    sys.exit(main())
