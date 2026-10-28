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
# Связь читается «person_a <тип> person_b»; у этих типов стрелка a→b, у остальных её нет.
_DIRECTED_TYPES = frozenset({"parent-of", "grandparent-of", "godparent-of"})
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
    label: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Graph:
    nodes: tuple[Node, ...]  # по person_id
    edges: tuple[Edge, ...]  # по relationship_id
    shareable: bool
    hidden_count: int


def build_graph(
    persons: Iterable[Mapping[str, str]],
    relationships: Iterable[Mapping[str, str]],
    *,
    shareable: bool,
    current_year: int,
) -> Graph:
    """Граф из строк person-register и relationship-register. В передаваемом режиме (shareable)
    живые по правилу privacy.counts_as_living лица показаны только person_id, а у связей нет
    обоснования."""
    nodes = sorted(
        (_node(row, hidden=shareable and counts_as_living(row, current_year)) for row in persons),
        key=lambda node: node.person_id,
    )
    edges = sorted(
        (_edge(row, shareable=shareable) for row in relationships),
        key=lambda edge: edge.relationship_id,
    )
    return Graph(tuple(nodes), tuple(edges), shareable, sum(node.hidden for node in nodes))


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


def _edge(row: Mapping[str, str], *, shareable: bool) -> Edge:
    relationship_id = _value(row, "relationship_id")
    relationship_type = _value(row, "relationship_type")
    status = _value(row, "status")
    return Edge(
        relationship_id=relationship_id,
        a=_value(row, "person_a_id"),
        b=_value(row, "person_b_id"),
        type=relationship_type,
        status=status if status in STATUSES else _UNRESOLVED,
        directed=relationship_type in _DIRECTED_TYPES,
        label=f"{relationship_type} · {relationship_id}",
        fields=_pick(row, _SHAREABLE_RELATIONSHIP_FIELDS if shareable else _RELATIONSHIP_FIELDS),
    )


def _pick(row: Mapping[str, str], names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((name, _value(row, name)) for name in names)


def _value(row: Mapping[str, str], name: str) -> str:
    """Значение колонки без пробелов по краям; отсутствующая колонка читается пустой."""
    return (row.get(name) or "").strip()
