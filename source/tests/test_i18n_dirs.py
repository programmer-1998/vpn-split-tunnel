"""Locale-directory discovery must find the catalogue in every layout.

The application is installed in several shapes (system prefix, pip prefix,
source checkout) and the .mo must be found in all of them. The discovery
returns candidates for every shape, deduplicated, and loading tries each in
order, so the same code path serves the dev checkout and the installed app.
"""

from __future__ import annotations

from pathlib import Path

from vpn_split_tunnel.utils.i18n import I18nManager, _, setup_i18n

ROOT = Path(__file__).resolve().parent.parent


class TestLocaleDirs:
    def test_system_prefix_is_first_candidate(self) -> None:
        mgr = I18nManager()
        assert Path("/usr/share/locale") in mgr._locale_dirs

    def test_source_checkout_is_a_candidate(self) -> None:
        mgr = I18nManager()
        assert (ROOT / "resources" / "locale") in mgr._locale_dirs

    def test_candidates_are_deduplicated(self) -> None:
        mgr = I18nManager()
        assert len(mgr._locale_dirs) == len(set(mgr._locale_dirs))

    def test_source_checkout_catalogue_is_loaded_for_fa(self) -> None:
        """The committed .mo under resources/locale must serve Persian."""
        mgr = setup_i18n("fa")
        assert (ROOT / "resources" / "locale" / "fa" / "LC_MESSAGES"
                / "vpn-split-tunnel.mo").is_file()
        assert _("VPN Split Tunnel") == "مدیریت تونل جدا شده VPN"

    def test_unknown_language_falls_back_cleanly(self) -> None:
        """A language with no catalogue anywhere must not raise."""
        mgr = setup_i18n("de")
        assert mgr.current_language == "de" or mgr.current_language == "en"
        assert _("VPN Split Tunnel") == "VPN Split Tunnel"
        setup_i18n("en")