"""Подписи графа родства на русском, украинском и английском и выбор языка сборки.

Тексты лежат в assets/i18n/tree.json: ключ → {ru, uk, en}. Данные реестров не переводятся.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

LANGUAGES = ("ru", "uk", "en")
DEFAULT_LANGUAGE = "ru"
VOCABULARY_PATH = Path(__file__).resolve().parent.parent / "assets" / "i18n" / "tree.json"


@dataclass(frozen=True)
class Vocabulary:
    """Тексты по ключу и языку; у каждого ключа есть непустой текст на всех LANGUAGES."""

    entries: Mapping[str, Mapping[str, str]]

    def text(self, key: str, lang: str) -> str:
        return self.entries[key][lang]

    def texts(self, key: str) -> dict[str, str]:
        """Текст ключа на каждом языке."""
        return {lang: self.entries[key][lang] for lang in LANGUAGES}


def load_vocabulary(path: Path = VOCABULARY_PATH) -> Vocabulary:
    """Словарь из файла; ключ без текста на одном из языков — ValueError с перечнем таких ключей."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    incomplete = [key for key, texts in entries.items() if not _complete(texts)]
    if incomplete:
        raise ValueError(f"{path}: нет текста на {', '.join(LANGUAGES)} у ключей: {', '.join(incomplete)}")
    return Vocabulary(entries)


def _complete(texts: object) -> bool:
    return (
        isinstance(texts, dict)
        and set(texts) == set(LANGUAGES)
        and all(isinstance(text, str) and text.strip() for text in texts.values())
    )


class LanguageChoice(NamedTuple):
    lang: str
    ignored: str | None  # значение requested_output_language, которое не подошло


def choose_language(explicit: str | None, requested: str | None) -> LanguageChoice:
    """--lang, иначе requested_output_language из анкеты дела, если это ru, uk или en, иначе ru.
    Значение анкеты сравнивается без учёта регистра и региона (uk-UA → uk)."""
    if explicit is not None:
        return LanguageChoice(explicit, None)
    value = (requested or "").strip()
    primary = value.lower().replace("_", "-").split("-")[0]
    if primary in LANGUAGES:
        return LanguageChoice(primary, None)
    return LanguageChoice(DEFAULT_LANGUAGE, value or None)
