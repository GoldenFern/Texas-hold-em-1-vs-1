"""Small runtime translation layer for server and engine messages."""

from __future__ import annotations

from typing import Optional

from .catalog import MESSAGES

DEFAULT_LOCALE = "en-US"
SUPPORTED_LOCALES = ("zh-CN", "en-US")


def normalize_locale(raw: object, default: Optional[str] = DEFAULT_LOCALE) -> Optional[str]:
    """Normalize a locale header, query value or config value.

    Accepts values such as ``zh``, ``zh-CN``, ``en``, ``en-US`` and
    ``zh-CN,zh;q=0.9``. Unknown values fall back to ``default``.
    """
    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    for part in text.split(","):
        token = part.split(";")[0].strip().lower()
        if token.startswith("zh"):
            return "zh-CN"
        if token.startswith("en"):
            return "en-US"
    return default


def tr(locale: Optional[str], message_key: str, **params: object) -> str:
    """Translate one runtime message, falling back to English then the key."""
    normalized = normalize_locale(locale)
    catalog = MESSAGES.get(normalized or DEFAULT_LOCALE, MESSAGES[DEFAULT_LOCALE])
    template = (
        catalog.get(message_key)
        or MESSAGES[DEFAULT_LOCALE].get(message_key)
        or message_key
    )
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, ValueError):
        return template


__all__ = [
    "DEFAULT_LOCALE",
    "MESSAGES",
    "SUPPORTED_LOCALES",
    "normalize_locale",
    "tr",
]
