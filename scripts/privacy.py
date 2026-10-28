"""Правило приватности: кого из реестра лиц считать живым при передаче сведений из дела.

Источник правила — references/evidence-protocol.md, раздел «Контролируемые значения».
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Пустой статус читается как unknown.
_LIVING_STATUSES = frozenset({"", "living", "probably-living", "unknown"})
# Без сведений о смерти лицо младше этого возраста считается живым.
_LIVING_AGE_LIMIT = 110
# Четырёхзначный год 1000–2999, не часть более длинного числа.
_YEAR = re.compile(r"(?<!\d)[12]\d{3}(?!\d)")


def _birth_year(text: str) -> int | None:
    """Самый поздний год в тексте оценки рождения или None, если года нет."""
    years = [int(match) for match in _YEAR.findall(text)]
    return max(years) if years else None


def counts_as_living(row: Mapping[str, str], current_year: int) -> bool:
    """Считается ли лицо из строки person-register живым; отсутствующие поля читаются пустыми."""
    if (row.get("living_status") or "").strip() not in _LIVING_STATUSES:
        return False
    if (row.get("death_estimate") or "").strip():
        return False
    year = _birth_year(row.get("birth_estimate") or "")
    return year is None or year > current_year - _LIVING_AGE_LIMIT
