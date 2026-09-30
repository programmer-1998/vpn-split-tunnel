"""Logging setup: --debug turns on, silent stays silent, no duplicate lines.

The whole point of the --debug flag is that a user who hits a crash after
installing the app can run it from a terminal and copy the output back.
These tests pin the three properties that make that promise reliable:
debug mode prints DEBUG lines, normal mode does not, and re-running the
setup (main() and do_command_line both call it) does not double lines.
"""

from __future__ import annotations

import logging

from vpn_split_tunnel.utils.logger import setup_logging


class TestSetupLogging:
    def test_debug_mode_enables_debug_level(self) -> None:
        setup_logging(True)
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

    def test_default_is_quiet(self) -> None:
        setup_logging(False)
        assert logging.getLogger().getEffectiveLevel() == logging.WARNING

    def test_debug_mode_writes_debug_lines(self, capsys) -> None:
        setup_logging(True)
        logging.getLogger("test").debug("ping")
        err = capsys.readouterr().err
        assert "ping" in err

    def test_default_hides_debug_lines(self, capsys) -> None:
        setup_logging(False)
        logging.getLogger("test").debug("ping")
        err = capsys.readouterr().err
        assert "ping" not in err

    def test_repeat_setup_does_not_duplicate_handlers(self, capsys) -> None:
        setup_logging(True)
        setup_logging(True)
        setup_logging(True)
        marked = [h for h in logging.getLogger().handlers if getattr(h, "_vpn_split_tunnel", False)]
        assert len(marked) == 1
        logging.getLogger("test").info("once")
        err = capsys.readouterr().err
        assert err.count("once") == 1

    def test_switch_from_quiet_to_debug_writes_subsequent_lines(self, capsys) -> None:
        setup_logging(False)
        setup_logging(True)
        logging.getLogger("test").debug("now visible")
        err = capsys.readouterr().err
        assert "now visible" in err
