"""Раскладка графа родства по строкам поколений: предки выше потомков, лица без связи, задающей
поколение, — в нижней строке «вне поколений»."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_model import Graph

# Шаг сетки в единицах SVG; позиции — центры узлов.
COLUMN_WIDTH = 200.0
ROW_HEIGHT = 120.0
MARGIN = 40.0
_COMPONENT_GAP = COLUMN_WIDTH / 2
# Сдвиг поколения от person_a к person_b. godparent-of, other и неизвестные типы поколение не задают.
_GENERATION_STEP = {
    "parent-of": 1,
    "grandparent-of": 2,
    "spouse-of": 0,
    "sibling-of": 0,
    "half-sibling-of": 0,
}


@dataclass(frozen=True)
class Layout:
    rows: tuple[tuple[str, ...], ...]  # строки поколений сверху вниз, person_id слева направо
    unranked: tuple[str, ...]  # нижняя строка «вне поколений»
    positions: Mapping[str, tuple[float, float]]  # person_id → (x, y) центра узла
    width: float
    height: float


def layout(graph: Graph) -> Layout:
    """Строки поколений по каждой компоненте связей (компоненты рядом, все с верхней строки),
    порядок в строке — по барицентрам соседей, затем по person_id."""
    steps = _generation_steps(graph)
    neighbours = _neighbours(graph)
    families = [_ordered_rows(ranks, neighbours) for ranks in _ranked_components(graph, steps)]
    unranked = tuple(node.person_id for node in graph.nodes if node.person_id not in steps)
    return _place(families, unranked)


def _generation_steps(graph: Graph) -> dict[str, list[tuple[str, int]]]:
    """Для каждого лица со связью-рангом: (сосед, сдвиг поколения к нему) в порядке relationship_id."""
    steps: dict[str, list[tuple[str, int]]] = {}
    for edge in graph.edges:
        step = _GENERATION_STEP.get(edge.type)
        if step is None:
            continue
        steps.setdefault(edge.a, []).append((edge.b, step))
        steps.setdefault(edge.b, []).append((edge.a, -step))
    return steps


def _neighbours(graph: Graph) -> dict[str, list[str]]:
    neighbours: dict[str, list[str]] = {}
    for edge in graph.edges:
        neighbours.setdefault(edge.a, []).append(edge.b)
        neighbours.setdefault(edge.b, []).append(edge.a)
    return neighbours


def _ranked_components(graph: Graph, steps: Mapping[str, Sequence[tuple[str, int]]]) -> list[dict[str, int]]:
    """Поколение каждого лица по компонентам: обход в ширину от наименьшего person_id компоненты;
    при противоречии (цикл, несогласованные связи) остаётся первое назначение. Верхняя строка — 0."""
    placed: set[str] = set()
    components = []
    for node in graph.nodes:
        start = node.person_id
        if start not in steps or start in placed:
            continue
        ranks = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbour, step in steps[current]:
                if neighbour not in ranks:
                    ranks[neighbour] = ranks[current] + step
                    queue.append(neighbour)
        top = min(ranks.values())
        components.append({person_id: rank - top for person_id, rank in ranks.items()})
        placed.update(ranks)
    return components


def _ordered_rows(ranks: Mapping[str, int], neighbours: Mapping[str, Sequence[str]]) -> list[list[str]]:
    """Строки одной компоненты: проход сверху вниз и снизу вверх по барицентрам соседних строк."""
    rows: list[list[str]] = [[] for _ in range(max(ranks.values()) + 1)]
    for person_id in sorted(ranks):
        rows[ranks[person_id]].append(person_id)
    for index in range(1, len(rows)):
        rows[index] = _by_barycentre(rows[index], rows[index - 1], neighbours)
    for index in range(len(rows) - 2, -1, -1):
        rows[index] = _by_barycentre(rows[index], rows[index + 1], neighbours)
    return rows


def _by_barycentre(row: Sequence[str], fixed: Sequence[str], neighbours: Mapping[str, Sequence[str]]) -> list[str]:
    """Строка по среднему месту соседей в строке fixed; лицо без таких соседей держит своё место."""
    column = {person_id: index for index, person_id in enumerate(fixed)}

    def key(item: tuple[int, str]) -> tuple[float, str]:
        place, person_id = item
        linked = [column[other] for other in neighbours.get(person_id, ()) if other in column]
        return (sum(linked) / len(linked) if linked else float(place), person_id)

    return [person_id for _, person_id in sorted(enumerate(row), key=key)]


def _place(families: Sequence[Sequence[Sequence[str]]], unranked: Sequence[str]) -> Layout:
    """Координаты: компоненты слева направо, каждая строка по центру своей компоненты."""
    positions: dict[str, tuple[float, float]] = {}
    left = MARGIN
    for rows in families:
        columns = max(len(row) for row in rows)
        for rank, row in enumerate(rows):
            row_left = left + (columns - len(row)) * COLUMN_WIDTH / 2
            for index, person_id in enumerate(row):
                positions[person_id] = _point(row_left + (index + 0.5) * COLUMN_WIDTH, rank)
        left += columns * COLUMN_WIDTH + _COMPONENT_GAP
    row_count = max((len(rows) for rows in families), default=0)
    for index, person_id in enumerate(unranked):
        positions[person_id] = _point(MARGIN + (index + 0.5) * COLUMN_WIDTH, row_count)
    content_width = max(left - _COMPONENT_GAP - MARGIN if families else 0.0, len(unranked) * COLUMN_WIDTH)
    rows_drawn = row_count + (1 if unranked else 0)
    return Layout(
        rows=tuple(
            tuple(person_id for rows in families if rank < len(rows) for person_id in rows[rank])
            for rank in range(row_count)
        ),
        unranked=tuple(unranked),
        positions=MappingProxyType(positions),
        width=round(content_width + 2 * MARGIN, 1),
        height=round(rows_drawn * ROW_HEIGHT + 2 * MARGIN, 1),
    )


def _point(x: float, rank: int) -> tuple[float, float]:
    return (round(x, 1), round(MARGIN + (rank + 0.5) * ROW_HEIGHT, 1))
