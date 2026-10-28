"""Очистка текста реестров дела перед выводом и сравнением: одно правило для графа и правила о живых."""

from __future__ import annotations

import re
import unicodedata

# Управляющие знаки C0 и знаки, недопустимые в XML (суррогаты, U+FFFE, U+FFFF): в тексте реестров
# они становятся пробелом, поэтому ни один вывод (SVG, HTML, PDF, Mermaid, DOT) их не получает.
_UNPRINTABLE = re.compile(r"[\x00-\x1f\ud800-\udfff\ufffe\uffff]")


def clean(value: str) -> str:
    """Текст реестра для вывода: непечатаемые знаки — пробелы, форма NFC, без пробелов по краям."""
    return unicodedata.normalize("NFC", _UNPRINTABLE.sub(" ", value)).strip()
