"""Tests for internationalization."""

from __future__ import annotations

import pytest

from vpn_split_tunnel.utils.i18n import I18nManager, _, ngettext, get_i18n, setup_i18n


class TestI18n:
    """Test I18nManager."""

    def test_singleton(self) -> None:
        """Test I18nManager is singleton."""
        mgr1 = I18nManager()
        mgr2 = I18nManager()
        assert mgr1 is mgr2

    def test_get_available_languages(self) -> None:
        """Test available languages."""
        mgr = get_i18n()
        langs = mgr.get_available_languages()
        assert ("en", "English") in langs
        assert ("fa", "فارسی") in langs

    def test_set_language_en(self) -> None:
        """Test setting English language."""
        mgr = get_i18n()
        mgr.set_language("en")
        assert mgr.current_language == "en"
        assert not mgr.is_rtl

    def test_set_language_fa(self) -> None:
        """Test setting Persian language."""
        mgr = get_i18n()
        mgr.set_language("fa")
        assert mgr.current_language == "fa"
        assert mgr.is_rtl

    def test_set_language_invalid_fallbacks_to_en(self) -> None:
        """Test invalid language falls back to English."""
        mgr = get_i18n()
        mgr.set_language("invalid")
        assert mgr.current_language == "en"

    def test_gettext_english(self) -> None:
        """Test gettext with English."""
        mgr = get_i18n()
        mgr.set_language("en")
        # English uses msgid directly
        assert _("VPN Split Tunnel") == "VPN Split Tunnel"

    def test_ngettext(self) -> None:
        """Test plural gettext."""
        mgr = get_i18n()
        mgr.set_language("en")
        assert ngettext("1 item", "%d items", 1) == "1 item"
        assert ngettext("1 item", "%d items", 5) == "5 items"

    def test_ngettext_substitutes_the_count_in_every_language(self) -> None:
        """The count must be substituted for Persian too, not just English.

        GNUTranslations selects the plural form but leaves the %d in place, so
        callers must not have to apply the substitution themselves.
        """
        mgr = get_i18n()
        try:
            for language in ("en", "fa"):
                mgr.set_language(language)
                one = ngettext("%d connection detected", "%d connections detected", 1)
                many = ngettext("%d connection detected", "%d connections detected", 4)
                assert "%d" not in one, f"{language} singular left a placeholder: {one!r}"
                assert "%d" not in many, f"{language} plural left a placeholder: {many!r}"
                assert "1" in one
                assert "4" in many
        finally:
            mgr.set_language("en")

    def test_ngettext_persian_is_actually_persian(self) -> None:
        """Persian ngettext must return a Persian string, not an English form.

        Regression: the translator type test used
        ``isinstance(translator, gettext.NullTranslations)`` to decide there
        was no catalogue. GNUTranslations subclasses NullTranslations, so the
        check was always true and every language received the English
        singular/plural pair. The result was correct-looking ("1 connection
        detected") and the placeholder test above passed anyway, which is how
        this survived.
        """
        mgr = get_i18n()
        try:
            mgr.set_language("fa")
            assert ngettext("%d domain", "%d domains", 1) == "1 دامنه"
            assert ngettext("%d domain", "%d domains", 5) == "5 دامنه"
            found = ngettext("%d connection detected", "%d connections detected", 4)
            assert "connection" not in found
            assert "اتصال" in found
        finally:
            mgr.set_language("en")

    def test_setup_i18n(self) -> None:
        """Test setup_i18n function."""
        mgr = setup_i18n("fa")
        assert mgr.current_language == "fa"
        assert mgr.is_rtl