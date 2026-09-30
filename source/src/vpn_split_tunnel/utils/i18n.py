"""Internationalization support for VPN Split Tunnel."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path
from typing import Callable


class I18nManager:
    """Manages translations for the application."""

    _instance: I18nManager | None = None
    _current_lang: str = "en"
    _translator: gettext.NullTranslations | None = None

    def __new__(cls) -> I18nManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._locale_dirs = self._find_locale_dirs()
        self._setup_system_locale()

    def _find_locale_dirs(self) -> list[Path]:
        """Candidates for the directory that holds <lang>/LC_MESSAGES/*.mo.

        The application is installed in different shapes on different
        machines: the meson install places the catalogues under the chosen
        prefix's share/locale (usually /usr/share/locale), a pip install drops
        the modules outside the prefix, and a source checkout keeps them under
        resources/locale. Every shape is a candidate, in order, and
        ``_load_translations`` uses the first one that actually contains a
        catalogue for the requested language. The set is deduplicated because
        several of the derivations resolve to the same directory.
        """
        here = Path(__file__).resolve().parents
        candidates: list[Path] = [
            # Installed with the OS (or an admin-set prefix).
            Path("/usr/share/locale"),
        ]
        # The prefix that owns this module: walk up past
        # <prefix>/lib/python3.*/site-packages/vpn_split_tunnel/utils/.
        for depth in (3, 4, 5):
            candidates.append(here[depth] / "share" / "locale")
        # Source checkout: <project>/resources/locale.
        candidates.append(here[3] / "resources" / "locale")

        deduped: list[Path] = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def _setup_system_locale(self) -> None:
        """Initialize system locale."""
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            locale.setlocale(locale.LC_ALL, "C.UTF-8")

    def set_language(self, lang: str) -> None:
        """Set application language."""
        if lang == "system":
            lang = self._get_system_language()

        if lang not in ("en", "fa"):
            lang = "en"

        self._current_lang = lang
        self._load_translations(lang)

    def _get_system_language(self) -> str:
        """Detect system language."""
        lang = os.environ.get("LANG", "").split("_")[0].split(".")[0]
        if lang.startswith("fa"):
            return "fa"
        return "en"

    def _load_translations(self, lang: str) -> None:
        """Load the catalogue for ``lang`` from the first usable directory."""
        if lang == "en":
            self._translator = gettext.NullTranslations()
            return

        for localedir in self._locale_dirs:
            catalogue = localedir / lang / "LC_MESSAGES" / "vpn-split-tunnel.mo"
            if not catalogue.is_file():
                continue
            try:
                self._translator = gettext.translation(
                    "vpn-split-tunnel",
                    localedir=str(localedir),
                    languages=[lang],
                    fallback=True,
                )
                return
            except (FileNotFoundError, OSError):
                continue

        # No catalogue anywhere: fall back to untranslated strings.
        self._translator = gettext.NullTranslations()

    def get_translator(self) -> gettext.NullTranslations:
        """Get current translator."""
        if self._translator is None:
            self._load_translations(self._current_lang)
        return self._translator

    def gettext(self, message: str) -> str:
        """Translate a message."""
        return self.get_translator().gettext(message)

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        """Translate a plural message and return a finished string.

        The count is always substituted, for every language. GNUTranslations
        picks the right plural form but leaves the %d in place, so without this
        the same call would return a formatted string in English and a
        template in Persian, and callers would have to know which they got.
        """
        translator = self.get_translator()
        # GNUTranslations subclasses NullTranslations, so testing _for_ the
        # base class would always be true and a loaded catalogue would be
        # thrown away, returning English in every language. Test for the
        # concrete type instead.
        if isinstance(translator, gettext.GNUTranslations):
            message = translator.ngettext(singular, plural, n)
            if not message:
                # An entry that is absent (or dropped as untranslated) makes
                # gettext return an empty string here: fall back to English.
                message = singular if n == 1 else plural
        else:
            # NullTranslations: use our own plural rule. English has two forms.
            message = singular if n == 1 else plural
        return message % n if "%d" in message else message

    @property
    def current_language(self) -> str:
        return self._current_lang

    @property
    def is_rtl(self) -> bool:
        return self._current_lang == "fa"

    def get_available_languages(self) -> list[tuple[str, str]]:
        """Get list of available languages with native names."""
        return [
            ("en", "English"),
            ("fa", "فارسی"),
        ]


# Global instance
_i18n = I18nManager()


def _(message: str) -> str:
    """Translate message (gettext alias)."""
    return _i18n.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate plural message."""
    return _i18n.ngettext(singular, plural, n)


def get_i18n() -> I18nManager:
    """Get global I18nManager instance."""
    return _i18n


def setup_i18n(language: str = "system") -> I18nManager:
    """Initialize i18n with given language."""
    _i18n.set_language(language)
    return _i18n