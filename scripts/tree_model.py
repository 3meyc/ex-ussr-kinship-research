"""Модель графа родства: лица и связи из реестров дела, уже очищенные по режиму вывода.

Сведения о живых лицах скрываются только здесь: отрисовщики читают Node и Edge и не видят
строк реестров.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from privacy import counts_as_living

# Статусы связи в порядке легенды. Пустой или неизвестный статус рисуется как unresolved.
STATUSES = (
    "strongly-corroborated",
    "corroborated",
    "single-source",
    "hypothesis",
    "unresolved",
    "contradicted",
)
_UNRESOLVED = "unresolved"
# Связь читается «person_a <тип> person_b»: тип → (стрелка a→b, сдвиг поколения от a к b; None —
# поколение не задаёт). Типа нет в таблице (other, неизвестный) — без стрелки и без поколения.
_RELATIONSHIP_TYPES = {
    "parent-of": (True, 1),
    "grandparent-of": (True, 2),
    "godparent-of": (True, None),
    "spouse-of": (False, 0),
    "sibling-of": (False, 0),
    "half-sibling-of": (False, 0),
}
_UNKNOWN_TYPE = (False, None)
DIRECTED_TYPES = tuple(name for name, (directed, _) in _RELATIONSHIP_TYPES.items() if directed)
# Только эти колонки реестров попадают в вывод; notes, relatives_as_reported и прочие — никогда.
_PERSON_FIELDS = (
    "person_id",
    "living_status",
    "birth_estimate",
    "death_estimate",
    "places",
    "candidate_status",
    "source_artifact_ids",
)
_RELATIONSHIP_FIELDS = (
    "relationship_id",
    "relationship_type",
    "person_a_id",
    "person_b_id",
    "biological_legal_household",
    "status",
    "confidence",
    "claim_ids",
    "supporting_artifact_ids",
    "contradicting_artifact_ids",
    "rationale",
)
# Обоснование — свободный текст, в нём бывают имена живых; в передаваемый граф не попадает.
_SHAREABLE_RELATIONSHIP_FIELDS = tuple(name for name in _RELATIONSHIP_FIELDS if name != "rationale")
# Вид связи (усыновление, опека) и уверенность говорят о семье живого лица; у его связей их нет.
_HIDDEN_ENDPOINT_FIELDS = tuple(
    name for name in _SHAREABLE_RELATIONSHIP_FIELDS if name not in ("confidence", "biological_legal_household")
)


@dataclass(frozen=True)
class Node:
    person_id: str
    label: str
    years: str
    hidden: bool  # живое лицо в передаваемом графе: показан только person_id
    fields: tuple[tuple[str, str], ...]  # (колонка, значение) для панели сведений


@dataclass(frozen=True)
class Edge:
    relationship_id: str
    a: str
    b: str
    type: str
    status: str  # один из STATUSES
    directed: bool  # стрелка от a к b
    generation_step: int | None  # сдвиг поколения от a к b; None — связь поколение не задаёт
    label: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Graph:
    nodes: tuple[Node, ...]  # по person_id
    edges: tuple[Edge, ...]  # по relationship_id
    shareable: bool
    hidden_count: int
    skipped_rows: int  # строки реестров с пустым person_id или relationship_id, в граф не вошли


def build_graph(
    persons: Iterable[Mapping[str, str]],
    relationships: Iterable[Mapping[str, str]],
    *,
    shareable: bool,
    current_year: int,
) -> Graph:
    """Граф из строк person-register и relationship-register; строки с пустым id пропускаются.
    В передаваемом режиме (shareable) живые по правилу privacy.counts_as_living лица показаны
    только person_id, у связей нет обоснования, а у связей таких лиц — и вида связи с уверенностью."""
    person_rows, skipped_persons = _with_id(persons, "person_id")
    relationship_rows, skipped_relationships = _with_id(relationships, "relationship_id")
    nodes = sorted(
        (_node(row, hidden=shareable and counts_as_living(row, current_year)) for row in person_rows),
        key=lambda node: node.person_id,
    )
    hidden_ids = frozenset(node.person_id for node in nodes if node.hidden)
    edges = sorted(
        (_edge(row, shareable=shareable, hidden_ids=hidden_ids) for row in relationship_rows),
        key=lambda edge: edge.relationship_id,
    )
    return Graph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        shareable=shareable,
        hidden_count=len(hidden_ids),
        skipped_rows=skipped_persons + skipped_relationships,
    )


def _with_id(rows: Iterable[Mapping[str, str]], id_name: str) -> tuple[list[Mapping[str, str]], int]:
    """Строки с непустым id и число пропущенных."""
    every_row = list(rows)
    kept = [row for row in every_row if _value(row, id_name)]
    return kept, len(every_row) - len(kept)


def _node(row: Mapping[str, str], *, hidden: bool) -> Node:
    person_id = _value(row, "person_id")
    if hidden:
        return Node(person_id, person_id, "", True, (("person_id", person_id),))
    return Node(person_id, _label(row), _years(row), False, _pick(row, _PERSON_FIELDS))


def _label(row: Mapping[str, str]) -> str:
    """preferred_label, иначе первое из names_as_written (через «;»), иначе person_id."""
    first_written = _value(row, "names_as_written").split(";")[0].strip()
    return _value(row, "preferred_label") or first_written or _value(row, "person_id")


def _years(row: Mapping[str, str]) -> str:
    parts = (_value(row, "birth_estimate"), _value(row, "death_estimate"))
    return "–".join(part for part in parts if part)


def _edge(row: Mapping[str, str], *, shareable: bool, hidden_ids: frozenset[str]) -> Edge:
    relationship_id = _value(row, "relationship_id")
    relationship_type = _value(row, "relationship_type")
    a, b = _value(row, "person_a_id"), _value(row, "person_b_id")
    status = _value(row, "status")
    directed, generation_step = _RELATIONSHIP_TYPES.get(relationship_type, _UNKNOWN_TYPE)
    if a in hidden_ids or b in hidden_ids:
        field_names = _HIDDEN_ENDPOINT_FIELDS
    else:
        field_names = _SHAREABLE_RELATIONSHIP_FIELDS if shareable else _RELATIONSHIP_FIELDS
    return Edge(
        relationship_id=relationship_id,
        a=a,
        b=b,
        type=relationship_type,
        status=status if status in STATUSES else _UNRESOLVED,
        directed=directed,
        generation_step=generation_step,
        label=f"{relationship_type} · {relationship_id}",
        fields=_pick(row, field_names),
    )


def _pick(row: Mapping[str, str], names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((name, _value(row, name)) for name in names)


def _value(row: Mapping[str, str], name: str) -> str:
    """Значение колонки без пробелов по краям; отсутствующая колонка читается пустой."""
    return (row.get(name) or "").strip()
