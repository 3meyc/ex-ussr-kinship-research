"""Правило приватности: кого из реестра лиц считать живым при передаче сведений из дела.

Источник правила — references/evidence-protocol.md, раздел «Контролируемые значения».
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping

from register_text import clean

# Значения living_status, при которых лицо не считается живым; как статус читается — см. counts_as_living.
_DECEASED_STATUSES = frozenset({"deceased", "probably-deceased"})
# Явный статус живого: год рождения тогда не снимает признак живого.
_LIVING_STATUSES = frozenset({"living", "probably-living"})
# Лицо, родившееся не позже текущего года минус этот возраст, не считается живым.
_LIVING_AGE_LIMIT = 110
# Четырёхзначный год 1000–2999, не часть более длинного числа.
_YEAR = re.compile(r"(?<!\d)[12]\d{3}(?!\d)")
# Строчные только для A–Z: casefold свёл бы к списку и иные знаки (ſ → s, знак кельвина → k).
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _latest_year(text: str) -> int | None:
    """Самый поздний год в тексте оценки даты или None, если года нет."""
    years = [int(match) for match in _YEAR.findall(text)]
    return max(years) if years else None


def counts_as_living(row: Mapping[str, str], current_year: int) -> bool:
    """Считается ли лицо из строки person-register (как она записана) живым; отсутствующие поля
    читаются пустыми.

    Правило закрыто по умолчанию и читает только living_status и birth_estimate: текст о смерти
    не снимает признак живого, лицо с известной смертью отмечается статусом deceased. Статус
    берётся без пробелов по краям, строчными только для латиницы A–Z, и сравнивается и как записан,
    и после очистки текста реестра (register_text.clean), которую видит граф: статус, который
    очистка изменила, не снимает признак живого, а живой статус в любой из двух форм его ставит.
    Статус вне списков, включая пустой, признак живого не снимает; его снимает только год рождения.
    """
    written = row.get("living_status") or ""
    status = written.strip().translate(_ASCII_LOWER)
    cleaned = clean(written).translate(_ASCII_LOWER)
    if status in _LIVING_STATUSES or cleaned in _LIVING_STATUSES:
        return True
    if status == cleaned and status in _DECEASED_STATUSES:
        return False
    birth_year = _latest_year(row.get("birth_estimate") or "")
    return birth_year is None or birth_year > current_year - _LIVING_AGE_LIMIT
