"""Central logging setup.

The GUI normally gives the user no terminal feedback. ``--debug`` changes
that: every message the application logs is printed to stderr, so someone
who hits a crash after installing the app can run
``vpn-split-tunnel --debug`` from a terminal and send the output back.

This module is the single place that decides what reaches the terminal:

* ``setup_logging(debug=False)`` — silent by default: only WARNING and
  above are shown, which keeps a normal launch clean for anyone who does
  run it from a terminal.
* ``setup_logging(debug=True)`` — the full DEBUG stream, with timestamps,
  so a crash can be traced step by step.
* ``install_crash_hook()`` — an ``excepthook`` that turns an uncaught
  exception into a logged CRITICAL with the full traceback, instead of a
  bare Python traceback. Same information, but clearly attributed and
  showing in the same format the rest of the debug output uses.
"""

from __future__ import annotations

import logging
import sys

# One shared format for every line the app prints, so a pasted debug log is
# uniform and greppable.
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_logger = logging.getLogger("vpn_split_tunnel")


def setup_logging(debug: bool = False) -> None:
    """Point the root logger at stderr with a level chosen by ``debug``.

    Idempotent: calling it again (e.g. from ``main()`` and later from
    ``do_command_line``) replaces the stderr handler instead of stacking
    duplicates.
    """
    root = logging.getLogger()

    level = logging.DEBUG if debug else logging.WARNING
    root.setLevel(level)

    # Detach a handler we installed on an earlier call so repeated setup
    # does not double every line.
    for handler in list(root.handlers):
        if getattr(handler, "_vpn_split_tunnel", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    handler._vpn_split_tunnel = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    if debug:
        _logger.info("Debug mode enabled. Include this output in any crash report.")
    else:
        _logger.debug("Logging configured (silent mode).")


def install_crash_hook() -> None:
    """Log uncaught exceptions instead of printing a bare traceback.

    The default ``sys.excepthook`` already writes to stderr; this version
    routes the same information through the logging system so it lands in
    the same format as everything else and can be copied out of a terminal
    with one selection.
    """

    def _hook(exc_type, exc_value, exc_tb):
        _logger.critical(
            "Unhandled exception (include the traceback in your report)",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _hook


def is_debug_enabled() -> bool:
    """Whether the root logger is in debug mode."""
    return logging.getLogger().getEffectiveLevel() <= logging.DEBUG
