"""Minimal dictionary-based i18n for the WACA path admin UI.

Why not Babel/gettext:
    Either way every hard-coded string in the templates has to be replaced with a
    lookup, so the migration cost is the same. Babel additionally needs a
    ``pybabel compile`` build step and an extra dependency, which works against
    the "clone it and it runs" promise of this repository. JSON catalogues are
    also far easier for translators to edit directly on GitHub than ``.po``.

Contract:
    * ``ja`` is the source language; ``en`` is a translation of it.
    * A key missing from the active catalogue falls back to ``ja``.
    * A key missing from *both* returns the key itself, and is logged once, so a
      typo shows up as ``users.csv.buton`` on screen instead of silently blanking.
    * Values may contain ``{placeholders}`` filled via :func:`translate`.
      Never build a sentence by concatenation - languages order words
      differently, and the count in e.g. "shared with N sessions" must stay
      inside the translated string.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("waca_path_backend.i18n")

DEFAULT_LANG = "ja"
SUPPORTED_LANGS: tuple[str, ...] = ("ja", "en")
LANG_COOKIE = "waca_path_lang"

_CATALOG_DIR = Path(__file__).resolve().parent
_catalogs: dict[str, dict[str, str]] = {}
_warned: set[str] = set()


def _load(lang: str) -> dict[str, str]:
    path = _CATALOG_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def catalog(lang: str) -> dict[str, str]:
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    if lang not in _catalogs:
        _catalogs[lang] = _load(lang)
    return _catalogs[lang]


def normalize_lang(value: str | None) -> str | None:
    """Map an incoming language token onto a supported language, or None."""
    if not value:
        return None
    token = value.strip().lower().replace("_", "-")
    if token in SUPPORTED_LANGS:
        return token
    base = token.split("-", 1)[0]
    return base if base in SUPPORTED_LANGS else None


def negotiate_lang(
    *,
    query: str | None = None,
    cookie: str | None = None,
    accept_language: str | None = None,
    tenant_default: str | None = None,
) -> str:
    """Resolve the UI language.

    Priority: explicit ?lang= > cookie > Accept-Language > tenant config > ja.
    Only the explicit query value is persisted by the caller; a header-derived
    value must not overwrite a deliberate choice.
    """
    for candidate in (query, cookie):
        lang = normalize_lang(candidate)
        if lang:
            return lang
    for part in (accept_language or "").split(","):
        lang = normalize_lang(part.split(";", 1)[0])
        if lang:
            return lang
    return normalize_lang(tenant_default) or DEFAULT_LANG


def translate(key: str, lang: str = DEFAULT_LANG, /, **params: Any) -> str:
    """Look up ``key`` in ``lang``, falling back to ja, then to the key itself."""
    text = catalog(lang).get(key)
    if text is None:
        text = catalog(DEFAULT_LANG).get(key)
        if text is None:
            if key not in _warned:
                _warned.add(key)
                logger.warning("i18n: missing translation key %r", key)
            return key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            logger.warning("i18n: bad placeholders for key %r", key)
            return text
    return text


def make_translator(lang: str):
    """Return a ``t(key, **params)`` bound to ``lang`` for Jinja templates."""

    def _t(key: str, **params: Any) -> str:
        return translate(key, lang, **params)

    return _t


def missing_keys(lang: str) -> list[str]:
    """Keys present in the source language but absent from ``lang``.

    Used by the test-suite so an untranslated string cannot reach a release.
    """
    return sorted(set(catalog(DEFAULT_LANG)) - set(catalog(lang)))
