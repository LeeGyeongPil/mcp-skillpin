"""UI language selection and message lookup (English / Korean).

Order: ``--lang`` > ``MCP_SKILLPIN_LANG`` > ``LC_ALL`` > ``LC_MESSAGES`` > ``LANG``.
Anything that does not start with ``ko`` becomes English.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

LANGUAGES = ("en", "ko")
ENV_VAR = "MCP_SKILLPIN_LANG"
_DIR = Path(__file__).parent / "messages"

_cache: dict[str, dict[str, str]] = {}
_current = "en"


def load_messages(lang: str) -> dict[str, str]:
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; use one of {LANGUAGES}")
    if lang not in _cache:
        _cache[lang] = json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return _cache[lang]


def resolve_language(cli_value: str | None, environ: Mapping[str, str]) -> str:
    """Return ``"en"`` or ``"ko"`` following the documented precedence."""
    candidates = [cli_value, environ.get(ENV_VAR)]
    candidates += [environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    for value in candidates:
        if value:
            return "ko" if value.strip().lower().startswith("ko") else "en"
    return "en"


def set_language(lang: str) -> None:
    global _current
    load_messages(lang)
    _current = lang


def current_language() -> str:
    return _current


class _Lenient(dict):  # type: ignore[type-arg]
    def __missing__(self, key: str) -> str:
        return "?"


def t(key: str, /, **params: object) -> str:
    """Look up ``key`` in the current language (falls back to English, then the key)."""
    table = load_messages(_current)
    text = table.get(key)
    if text is None:
        text = load_messages("en").get(key, key)
    return text.format_map(_Lenient(params)) if params else text
