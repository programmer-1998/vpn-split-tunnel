"""Starting applications inside a cgroup so they can be told apart.

This is the part that makes per-application split tunneling real, and it exists
because of one fact about the kernel:

    An `ip rule` can select traffic by firewall mark, and `nftables` can set
    that mark by matching a packet's originating socket's cgroup. There is no
    third option. A process's traffic cannot be selected by its executable
    name, its parent, its user, or anything else the kernel records at send
    time. `skuid` is the closest thing and it is the same for every application
    the user runs.

So an application can only be split-tunneled if it is running inside a cgroup
we control.

A running process *can* be moved into another cgroup, by writing its pid to
that cgroup's `cgroup.procs` -- measured here, not assumed, and non-destructive:
the process keeps running. What migration does not do is fix up sockets that
were already open. The kernel associates a socket with a cgroup when the socket
is created, so after a move the process matches for every new connection and
not for the ones already established:

    before the move, socket opened earlier:  not matched
    before the move, socket opened after:    matched

That is why starting an application here is still the cleaner path -- a fresh
process has no inherited sockets -- and why the move, when it is offered, has to
say "from the next connection onwards" rather than implying a clean switch.

How a cgroup is created and entered:

* `systemd-run --user --scope --slice=... --unit=app-<name>` starts a program
  in a transient scope inside the named slice. The unit's `ControlGroup`
  property reports the resulting path, which is what the nftables rules need.
* The path is deep and includes the uid, e.g.
  `/user.slice/user-1000.slice/user@1000.service/app.slice/vpn-split-tunnel.slice/app-firefox.scope`.
  It is discovered from systemd rather than assumed, because its shape depends
  on how the session was started.

One consequence shapes the whole apply flow: **an nftables ruleset that names
a cgroup path which does not exist fails to load entirely.** Not that one rule
is skipped, the whole table is rejected. So the scopes have to exist before the
rules that reference them are written, which is why `launch()` and
`apply_policy()` are separate steps and why the UI reports a per-application
policy as not-yet-matching until the user has launched the app once.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_CGROUP_ROOT = "/sys/fs/cgroup"


class LaunchError(RuntimeError):
    """An application could not be started inside the split-tunnel slice."""


@dataclass(frozen=True)
class LaunchedApp:
    """An application running inside a cgroup we can match."""

    unit: str
    executable: str
    cgroup_path: str
    pid: int | None

    @property
    def cgroup_dir(self) -> Path:
        return Path(_CGROUP_ROOT) / self.cgroup_path.lstrip("/")


def unit_name_for(app_id: str) -> str:
    """The transient scope name for a desktop id.

    systemd unit names cannot contain `.`, so `firefox.desktop` becomes
    `app-firefox`. The name is derived rather than stored, so the same
    application always lands in the same scope and a relaunch reuses it.
    """
    slug = app_id.removesuffix(".desktop")
    slug = "".join(c if c.isalnum() else "-" for c in slug).strip("-").lower()
    return f"app-{slug}"


def executable_for(app_id: str) -> str | None:
    """The command that starts an application, from its desktop entry.

    The id rarely equals the command: `org.gnome.Nautilus.desktop` runs
    `nautilus`, and `firefox_firefox.desktop` does not run `firefox`. The
    desktop file is the authority.

    GIO is asked first, because it implements the desktop entry specification
    including the `Exec=` grammar, localised names and the `Hidden`/try-exec
    conventions. The text parsing below is the fallback for the case where GIO
    is unavailable, and is deliberately conservative: it returns None rather
    than guessing when the line is ambiguous.
    """
    return " ".join(command_for(app_id) or []) or None


def command_for(app_id: str) -> list[str] | None:
    """The full command to start an application, as an argument vector.

    The whole line, not just the program, because the program alone is not
    enough for a Flatpak. A Flatpak entry's `Exec=` is
    `/usr/bin/flatpak run --branch=stable ... org.ardour.Ardour`, and starting
    `/usr/bin/flatpak` with no arguments opens the flatpak manager instead of
    the application.

    GIO's `get_commandline` already performs the desktop-spec expansion,
    including removing field codes such as `%U`, so the arguments are the ones
    that would be used to start the application with no files or URLs.
    """
    desktop_id = app_id if app_id.endswith(".desktop") else f"{app_id}.desktop"

    resolved = _command_via_gio(desktop_id)
    if resolved:
        return resolved

    for directory in _desktop_dirs():
        path = Path(directory) / desktop_id
        if not path.is_file():
            continue
        for line in path.read_text(errors="replace").splitlines():
            if not line.startswith("Exec="):
                continue
            tokens = _exec_tokens(line[len("Exec=") :])
            if tokens:
                return tokens
    return None


def _command_via_gio(desktop_id: str) -> list[str] | None:
    """Ask GIO for an application's command line, if GIO is importable here."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return None

    for directory in _desktop_dirs():
        path = Path(directory) / desktop_id
        if not path.is_file():
            continue
        try:
            info = Gio.DesktopAppInfo.new_from_filename(str(path))
        except Exception:
            # A malformed desktop file is a normal thing to encounter; the
            # text fallback is next.
            continue
        if info is None:
            continue
        line = info.get_commandline()
        if not line:
            continue
        tokens = [tok for tok in shlex.split(line) if not tok.startswith("%")]
        if tokens:
            return tokens
    return None


def _desktop_dirs() -> list[str]:
    """Where desktop entries are looked for, in the XDG search order.

    Each result is the `applications` subdirectory of a data directory, which
    is where the specification puts them.

    `XDG_DATA_DIRS` is read from the environment when set and falls back to the
    specification's default when it is not. A session started by a display
    manager may export neither variable, and the fallback is the only thing
    that finds the system entries in that case.
    """
    roots = [
        os.environ.get("XDG_DATA_HOME")
        or os.path.join(os.path.expanduser("~"), ".local", "share")
    ]
    roots += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return [os.path.join(root, "applications") for root in roots if root]


def _first_exec_token(exec_line: str) -> str | None:
    """The program in an `Exec=` line, or None if it cannot be determined.

    Kept as a separate name because the question "which program is this" and
    the question "what is the full command" have different failure modes, and
    the first is what a caller usually wants to display.
    """
    tokens = _exec_tokens(exec_line)
    return tokens[0] if tokens else None


def _exec_tokens(exec_line: str) -> list[str]:
    """Split an `Exec=` line into arguments, or empty if it is unusable.

    `shlex` does the splitting because the desktop entry specification uses
    shell-like quoting and the difference matters: `'my app'` is one program,
    and a hand-rolled split on whitespace would try to start `my`.

    Two constructs are declined rather than guessed:

    * alternatives (`a | b`) - the spec says to use the first, so the first is
      taken and the rest dropped.
    * a leading environment assignment (`env FOO=bar prog`) - starting `env`
      with nothing after it succeeds and does nothing, so the assignment is
      removed and the real program is run, but a line that begins with an
      option rather than a program is declined entirely.
    """
    line = exec_line.split("|")[0].strip()
    if not line:
        return []

    try:
        tokens = shlex.split(line, comments=False, posix=True)
    except ValueError:
        # Unbalanced quotes: the entry is malformed, and guessing which side
        # the user meant would be worse than not starting anything.
        return []

    # Field codes such as %U, %F and %i describe a file or URL to open. None
    # is supplied here, so they are dropped rather than passed through, where
    # they would appear as a literal "%U" argument.
    tokens = [tok for tok in tokens if not tok.startswith("%")]

    # A leading VAR=value sets the environment, so it is removed. `env` itself
    # is the one program whose whole purpose is to apply the assignments that
    # follow it and then run something else, so it is removed too: starting
    # `env` with nothing after it exits successfully having done nothing, and
    # the application would silently never open.
    while tokens:
        head = tokens[0]
        if "=" in head and not head.startswith("-"):
            tokens.pop(0)
        elif Path(head).name == "env" and len(tokens) > 1:
            tokens.pop(0)
        else:
            break

    # Nothing naming a program, or a line that is only options.
    if not tokens or tokens[0].startswith("-"):
        return []
    return tokens


# Where cgroup v2 is mounted. A module constant rather than a literal scattered
# through the file so the read-only helpers and the tests agree on one place.
CGROUP_ROOT = "/sys/fs/cgroup"


def cgroup_path_for_unit(unit: str) -> str | None:
    """Ask systemd where a unit's processes actually live.

    The path is read back rather than constructed because its shape varies with
    the session: a systemd user session nests the slice under
    `user@<uid>.service`, and the set of intermediate slices differs between
    distributions and between a graphical and a SSH login.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return None
    try:
        result = subprocess.run(
            [systemctl, "--user", "show", unit, "--property=ControlGroup", "--value"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return path if path and path != "/" else None


def cgroup_has_processes(cgroup_path: str, root: str = CGROUP_ROOT) -> bool:
    """Whether any process is actually inside this cgroup right now.

    The existence of the directory is not enough. A transient unit that has
    exited leaves its cgroup behind, still registered with systemd as
    `active (running)`, and the directory still there -- so a directory check
    reports an application as matchable when nothing of it is running. That is
    the worst direction to be wrong in: the preview would promise the
    application is handled, an nft rule naming that cgroup would be generated,
    and no packet would ever match it.

    Observed live, not theorised: a `systemd-run` unit whose only processes had
    been moved away reported `ActiveState=active SubState=running` with an
    existing directory and an empty `cgroup.procs`.
    """
    procs = Path(root) / cgroup_path.lstrip("/") / "cgroup.procs"
    try:
        return bool(procs.read_text().strip())
    except OSError:
        return False


def pids_in_cgroup(cgroup_path: str, root: str = CGROUP_ROOT) -> list[int]:
    """The pids inside a cgroup, or an empty list if it cannot be read."""
    procs = Path(root) / cgroup_path.lstrip("/") / "cgroup.procs"
    try:
        return [int(line) for line in procs.read_text().split() if line.strip()]
    except (OSError, ValueError):
        return []


def running_pids_for(executable: str) -> list[int]:
    """Pids of processes that look like this executable, for any user.

    A coarse signal, and deliberately not treated as authoritative: it matches on
    the resolved `/proc/<pid>/exe` and on `comm`, either of which can be read for
    the caller's own processes without privileges. It cannot see inside a
    Flatpak sandbox, and a program that renames itself will not match.

    Used only to refuse a launch that cannot work, never to decide that traffic
    is matched. When it finds nothing, nothing is claimed.
    """
    wanted = Path(executable).name
    if not wanted:
        return []
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            continue
        if Path(exe).name == wanted:
            found.append(int(entry.name))
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        if comm == wanted:
            found.append(int(entry.name))
    return found


def pids_outside_slice(pids: list[int], slice_name: str) -> list[int]:
    """Which of these pids are not in the split slice."""
    outside = []
    for pid in pids:
        try:
            cgroup = Path(f"/proc/{pid}/cgroup").read_text()
        except OSError:
            continue
        if f"/{slice_name}/" not in cgroup:
            outside.append(pid)
    return outside


def launch(
    app_id: str,
    slice_name: str,
    *,
    extra_args: list[str] | None = None,
    wait: bool = False,
) -> LaunchedApp:
    """Start an application inside `slice_name` and report its cgroup.

    `wait=True` blocks until the application exits, which is what the CLI
    wants; the GUI leaves it running so the application outlives this call.

    Raises LaunchError with a message meant to be shown to the user, since
    every failure here is something they can act on: no desktop entry, no
    systemd, or a scope that would not start.
    """
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        raise LaunchError(
            "systemd-run was not found, so applications cannot be started in a "
            "way that can be told apart by traffic. Per-application split "
            "tunneling needs systemd; address and domain rules still work."
        )

    argv = command_for(app_id)
    if not argv:
        raise LaunchError(
            f"No desktop entry was found for {app_id!r}, so this app does not "
            "know how to start it."
        )
    program = argv[0]

    # Starting an application that is already running does not put it in the
    # slice; it hands the request to the instance that is already up and exits.
    # Anything the new process spawns in the meantime is left behind in the
    # slice, and then one application's traffic is split across two cgroups.
    #
    # That is not hypothetical. Chrome was already open when a start was
    # requested here; the new invocation handed off and returned, and left 13 of
    # the live browser's processes in the split group while its main process
    # stayed outside. Applying a policy then would have marked part of a browser
    # and not the rest, which looks like the app being broken.
    pretty = Path(program).name or program
    already = running_pids_for(program)
    if already:
        outside = pids_outside_slice(already, slice_name)
        if outside:
            raise LaunchError(
                f"{pretty} is already running ({len(outside)} process"
                f"{'es' if len(outside) != 1 else ''}), started outside the "
                "split group. Starting it again would not move it, and would "
                "leave whatever it spawns behind in the split group, splitting "
                f"one application's traffic across two groups. Close {pretty} "
                "first, then start it from here."
            )
        # Already in the slice: nothing to do, and saying so beats starting a
        # second copy that would hand off to the first and exit.
        raise LaunchError(
            f"{pretty} is already running in the split group, so its traffic "
            "is already being matched. There is no need to start it again."
        )

    unit = unit_name_for(app_id)
    command = [systemd_run, "--user", f"--unit={unit}", f"--slice={slice_name}"]
    if wait:
        command.append("--wait")
    command += [*argv, *(extra_args or [])]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise LaunchError(
            f"Starting {program} took too long and was cancelled. The "
            "application may still be starting; check whether it opened."
        ) from None
    except OSError as exc:
        raise LaunchError(f"Could not run systemd-run: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise LaunchError(
            f"systemd could not start {program}"
            + (f": {detail}" if detail else ".")
        )

    # The unit is reported by systemd rather than assumed. If it cannot be
    # read, the application is running but unmatchable, and the caller is told
    # so rather than being given a path that does not exist: an nftables rule
    # naming a missing cgroup makes the entire table fail to load.
    cgroup_path = cgroup_path_for_unit(unit)
    if cgroup_path is None:
        raise LaunchError(
            f"{program} started, but systemd did not report which cgroup it "
            "is in, so its traffic cannot be matched. Try starting it again."
        )

    if not Path(_CGROUP_ROOT, cgroup_path.lstrip("/")).is_dir():
        raise LaunchError(
            f"{program} started, but the cgroup {cgroup_path} does not "
            "exist. Per-application rules cannot be installed for it."
        )

    # The unit exists and its cgroup exists. That is still not proof the
    # application is in there: a single-instance program that is already running
    # hands the request to the running instance and the new process exits, so
    # the scope is created, sits there empty, and systemd reports it as
    # `active (running)`.
    #
    # Reporting success there is the failure this check exists to stop. The
    # application list would show it as running where the rules can see it, the
    # plan would count it, and an nftables rule would name a cgroup with nothing
    # in it -- so the user would be told their application is split-tunneled
    # while every one of its connections stayed unmarked.
    if not _wait_for_processes(cgroup_path):
        raise LaunchError(
            f"{pretty} did not stay running in the split group. It is most "
            "likely already open somewhere else, so starting it again just "
            f"handed the request to that copy. Close {pretty} first, then "
            "start it from here."
        )

    return LaunchedApp(
        unit=unit,
        executable=program,
        cgroup_path=cgroup_path,
        pid=_unit_main_pid(unit),
    )


def _wait_for_processes(
    cgroup_path: str, settle: float = 1.2, timeout: float = 6.0
) -> bool:
    """Whether anything lands in the cgroup and stays there.

    Presence is not enough. A single-instance application that is already
    running does start a process inside the slice, and that process exits a
    moment later after handing the request to the running copy. Checking only
    that something appeared reports that as a successful launch, and the unit
    is `inactive dead` seconds later.

    So the processes have to still be there after a settling period. Measured on
    the real case: Chrome's hand-off process was present at 0.1s and gone by the
    time the next check ran.
    """
    deadline = time.monotonic() + timeout
    present_since: float | None = None
    while time.monotonic() < deadline:
        if cgroup_has_processes(cgroup_path):
            if present_since is None:
                present_since = time.monotonic()
            elif time.monotonic() - present_since >= settle:
                return True
        else:
            present_since = None
        time.sleep(0.1)
    return False


def _unit_main_pid(unit: str) -> int | None:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return None
    try:
        result = subprocess.run(
            [systemctl, "--user", "show", unit, "--property=MainPID", "--value"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        pid = int(result.stdout.strip() or "0")
    except ValueError:
        return None
    return pid or None


def running_units(slice_name: str) -> list[tuple[str, str]]:
    """Units currently alive in the slice, as (unit, cgroup path) pairs.

    Used so the UI can say "these applications are being split-tunneled right
    now" rather than listing applications the user selected but never started.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return []
    try:
        listing = subprocess.run(
            [
                systemctl,
                "--user",
                "list-units",
                "--all",
                "--no-legend",
                "--plain",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    # `list-units` exits non-zero on an unknown option and prints nothing, which
    # is indistinguishable from "nothing is running". So this is not allowed to
    # pass a filter option at all.
    if listing.returncode != 0:
        return []

    units: list[tuple[str, str]] = []
    for line in listing.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        # `list-units` has no --slice filter, so the slice is determined the way
        # the routing plan determines it: by where the unit's cgroup actually
        # is. An earlier version asked systemd for the slice and got nothing,
        # because the option does not exist -- and the symptom was that an
        # application which really was running in the slice was reported as not
        # running, so the plan and the list disagreed on the same screen.
        # Filtering by cgroup path also means this function and the plan can
        # never disagree: they ask the same question of the same source.
        if not unit.startswith("app-"):
            # The slice itself is in the listing too, and it is not an
            # application. Letting it through would put the slice's own path in
            # the set, which then reads as "one thing is running" in a list of
            # applications.
            continue
        path = cgroup_path_for_unit(unit)
        if path and f"/{slice_name}/" in f"{path}/":
            units.append((unit, path))
    return units
