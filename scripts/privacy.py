"""Правило приватности: кого из реестра лиц считать живым при передаче сведений из дела.

Источник правила — references/evidence-protocol.md, раздел «Контролируемые значения».
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Значения living_status (после strip и casefold), при которых лицо не считается живым;
# любое другое значение, включая пустое и вне контролируемого списка, читается как «жив».
_DECEASED_STATUSES = frozenset({"deceased", "probably-deceased"})
# Явный статус живого: год рождения тогда не снимает признак живого.
_LIVING_STATUSES = frozenset({"living", "probably-living"})
# Лицо, родившееся не позже текущего года минус этот возраст, не считается живым.
_LIVING_AGE_LIMIT = 110
# Четырёхзначный год 1000–2999, не часть более длинного числа.
_YEAR = re.compile(r"(?<!\d)[12]\d{3}(?!\d)")


def _latest_year(text: str) -> int | None:
    """Самый поздний год в тексте оценки даты или None, если года нет."""
    years = [int(match) for match in _YEAR.findall(text)]
    return max(years) if years else None


def counts_as_living(row: Mapping[str, str], current_year: int) -> bool:
    """Считается ли лицо из строки person-register живым; отсутствующие поля читаются пустыми.

    Правило закрыто по умолчанию и читает только living_status и birth_estimate: текст о смерти
    не снимает признак живого, лицо с известной смертью отмечается статусом deceased.
    """
    status = (row.get("living_status") or "").strip().casefold()
    if status in _DECEASED_STATUSES:
        return False
    if status in _LIVING_STATUSES:
        return True
    birth_year = _latest_year(row.get("birth_estimate") or "")
    return birth_year is None or birth_year > current_year - _LIVING_AGE_LIMIT
