"""Геометрия связей графа родства: порты на сторонах узлов, каналы через строки, высоты переходов в
промежутках между строками и места подписей.

Чистые вычисления на значениях: на входе граф и сетка tree_layout, на выходе Routes — пути из
отрезков и скруглённых углов в целых координатах, места подписей, центры узлов и размер холста.
Все связи одной пары лиц идут пучком: один путь, от которого дорожки отстоят на равный шаг, поэтому
они не сходятся и не пересекаются. Каждый путь ортогонален: отвесно от порта на стороне узла,
по горизонтали в промежутке между строками, отвесно через свободный канал строки, которую
пересекает. Отвесные участки разных путей стоят на разных x, горизонтали с общим отрезком x — на
разных высотах; где промежутку не хватает высоты, строки ниже сдвигаются, где строке не хватает
каналов, холст расширяется вправо.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_layout import Layout
    from tree_model import Edge, Graph

    Point = tuple[int, int]
    Box = tuple[float, float, float, float]  # left, top, right, bottom
    Command = tuple[str, tuple[int, ...]]

NODE_WIDTH = 160
NODE_HEIGHT = 48
LABEL_SIZE = 10
CHAR_EM = 0.6  # ширина знака с запасом, в долях кегля (sans-serif)
# Заглавные и эти строчные в sans-serif шире CHAR_EM: строка из них не помещается при той же длине.
_WIDE_EM = 0.75
_WIDE_LOWERCASE = frozenset("жмшщыюmw")

_HALF_HEIGHT = NODE_HEIGHT // 2
_LANE = 12  # шаг портов на стороне узла и наименьший зазор между параллельными участками путей
# Отвесные участки ближе этого по x считаются одной линией. Порты нижних сторон узлов стоят на x,
# кратных _LANE, верхних — со сдвигом на половину шага, поэтому отвесные участки от узлов строки
# выше и к узлам строки ниже в одном промежутке не совпадают.
_NEAR = _LANE // 2
_PHASE = {"bottom": 0, "top": _LANE // 2}
_PORT_REACH = 66  # порты не дальше этого от середины стороны, вне скруглённых углов рамки
_CLEARANCE = 12  # наименьший зазор между каналом и чужим узлом или краем холста
_ARC_RISE = 24  # наименьший подъём дуги между лицами одной строки над их узлами
_CORNER = 6  # радиус скругления угла пути
_LABEL_GAP = 2  # зазор вокруг подписи
# Доли длины пути, на которых пробуется подпись: от середины к концам, не доходя до узлов и стрелки.
_LABEL_STEPS = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8)


@dataclass(frozen=True)
class EdgeRoute:
    commands: tuple[Command, ...]  # ("M" | "L", (x, y)) или ("Q", (x управляющей, y управляющей, x, y))
    label_anchor: Point  # середина подписи по x и её базовая линия


@dataclass(frozen=True)
class Routes:
    edges: tuple[EdgeRoute, ...]  # в порядке graph.edges
    positions: Mapping[str, Point]  # person_id → центр узла после раздвигания строк
    width: int
    height: int


def char_em(char: str) -> float:
    """Оценка ширины знака в долях кегля: заглавные и широкие строчные шире прочих."""
    return _WIDE_EM if char.isupper() or char in _WIDE_LOWERCASE else CHAR_EM


def node_box(centre: Point) -> Box:
    x, y = centre
    return (x - NODE_WIDTH / 2, y - NODE_HEIGHT / 2, x + NODE_WIDTH / 2, y + NODE_HEIGHT / 2)


def route(graph: Graph, layout: Layout) -> Routes:
    """Пути всех связей графа по раскладке layout."""
    grid = _grid(layout)
    bundles = _bundles(graph.edges, grid)
    side_spacing = _side_spacing(bundles)
    spacing = [min(side_spacing[side] for side in bundle.sides) for bundle in bundles]
    half = [(bundle.count - 1) * step // 2 for bundle, step in zip(bundles, spacing)]
    channels, width = _channels(bundles, grid, half, round(layout.width))
    ports = _ports(bundles, grid, channels, side_spacing, spacing, half)
    stations = [(ports[index, 0], *channels.get(index, ()), ports[index, 1]) for index in range(len(bundles))]
    tracks, row_ys = _tracks(bundles, grid, stations, spacing, half)
    growth = row_ys[-1] - grid.row_ys[-1] if row_ys else 0
    height = round(layout.height) + growth

    lines: list[list[Point]] = [[] for _ in graph.edges]
    for index, bundle in enumerate(bundles):
        centre = _centre_line(bundle, index, stations[index], tracks, row_ys)
        for lane, edge_index in enumerate(bundle.edges):
            line = _offset(centre, lane * spacing[index] - half[index])
            lines[edge_index] = line if graph.edges[edge_index].a == bundle.first else line[::-1]

    positions = {person_id: (x, row_ys[grid.row_of[person_id]]) for person_id, x in grid.xs.items()}
    node_boxes = [node_box(positions[node.person_id]) for node in graph.nodes]
    label_boxes: list[Box] = []
    edges = []
    for edge, line in zip(graph.edges, lines):
        anchors = _on_canvas(edge.label, _label_anchors(line), width, height)
        anchor = _label_anchor(edge.label, anchors, node_boxes, label_boxes)
        label_boxes.append(_label_box(edge.label, anchor))
        edges.append(EdgeRoute(_commands(line), anchor))
    return Routes(tuple(edges), MappingProxyType(positions), width, height)


# --- Сетка и пучки -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Grid:
    xs: Mapping[str, int]  # person_id → x центра узла
    row_of: Mapping[str, int]  # person_id → номер строки сверху
    row_ys: tuple[int, ...]  # y центра каждой строки до раздвигания
    blocks: tuple[tuple[tuple[int, int], ...], ...]  # по строкам: отрезки x, закрытые для каналов


def _grid(layout: Layout) -> _Grid:
    row_ys = tuple(sorted({round(y) for _, y in layout.positions.values()}))
    row_index = {y: index for index, y in enumerate(row_ys)}
    xs = {person_id: round(x) for person_id, (x, _) in layout.positions.items()}
    row_of = {person_id: row_index[round(y)] for person_id, (_, y) in layout.positions.items()}
    reach = NODE_WIDTH // 2 + _CLEARANCE
    blocks = [[] for _ in row_ys]
    for person_id, x in xs.items():
        blocks[row_of[person_id]].append((x - reach, x + reach))
    return _Grid(xs, row_of, row_ys, tuple(tuple(sorted(row)) for row in blocks))


@dataclass(frozen=True)
class _Bundle:
    """Связи одной пары лиц. first — верхнее лицо, в одной строке — левое."""

    first: str
    last: str
    first_row: int
    last_row: int
    edges: tuple[int, ...]  # номера связей в graph.edges по порядку

    @property
    def count(self) -> int:
        return len(self.edges)

    @property
    def is_arc(self) -> bool:
        return self.first_row == self.last_row

    @property
    def sides(self) -> tuple[tuple[str, str], tuple[str, str]]:
        """(лицо, сторона узла) у first и у last: дуга выходит из верхних сторон, связь между
        строками — из нижней стороны верхнего узла в верхнюю сторону нижнего."""
        return ((self.first, "top" if self.is_arc else "bottom"), (self.last, "top"))


def _bundles(edges: Sequence[Edge], grid: _Grid) -> list[_Bundle]:
    pairs: dict[frozenset[str], list[int]] = {}
    for index, edge in enumerate(edges):
        pairs.setdefault(frozenset((edge.a, edge.b)), []).append(index)
    bundles = []
    for indexes in pairs.values():
        edge = edges[indexes[0]]
        first, last = sorted((edge.a, edge.b), key=lambda person_id: (grid.row_of[person_id], grid.xs[person_id]))
        bundles.append(_Bundle(first, last, grid.row_of[first], grid.row_of[last], tuple(indexes)))
    return bundles


def _side_spacing(bundles: Sequence[_Bundle]) -> dict[tuple[str, str], int]:
    """Шаг портов на каждой стороне узла: _LANE, а если столько дорожек не помещается в
    2 × _PORT_REACH, — наибольший меньший шаг, при котором помещаются (см. _spacing)."""
    lanes: dict[tuple[str, str], int] = {}
    for bundle in bundles:
        for side in bundle.sides:
            lanes[side] = lanes.get(side, 0) + bundle.count
    return {side: _spacing(count) for side, count in lanes.items()}


def _spacing(lanes: int) -> int:
    """Шаг, при котором lanes дорожек помещаются в 2 × _PORT_REACH: чётный, пока он не меньше 2
    (дорожки пучка симметричны), затем 1, а больше чем 2 × _PORT_REACH + 1 дорожек сходятся в
    середине стороны (шаг 0): в целых координатах их не развести, но ни одна не уходит со стороны."""
    if lanes < 2 or (lanes - 1) * _LANE <= 2 * _PORT_REACH:
        return _LANE
    step = 2 * _PORT_REACH // (lanes - 1)
    return step // 2 * 2 if step >= 2 else step


def _lanes(centre: int, count: int, step: int) -> tuple[int, ...]:
    """x дорожек пучка вокруг centre."""
    half = (count - 1) * step // 2
    return tuple(centre + lane * step - half for lane in range(count))


# --- Каналы и порты -----------------------------------------------------------------------------


def _channels(
    bundles: Sequence[_Bundle], grid: _Grid, half: Sequence[int], width: int
) -> tuple[dict[int, tuple[int, ...]], int]:
    """x канала пучка в каждой строке между его концами (сверху вниз) и ширина холста, которая их
    вмещает. Канал — ближайший к прямой между центрами концов x, где весь пучок отстоит от узлов на
    _CLEARANCE, от прежних каналов строки на _LANE и от чужих каналов соседних строк на _NEAR (их
    отвесные участки встречаются в общем промежутке); если такого нет, канал встаёт правее всего в
    строке, а холст расширяется."""
    taken: dict[int, list[tuple[int, int, int]]] = {}  # строка → (x, половина ширины, пучок)
    channels = {}
    for index, bundle in enumerate(bundles):
        if bundle.is_arc:
            continue
        x1, x2 = grid.xs[bundle.first], grid.xs[bundle.last]
        y1, y2 = grid.row_ys[bundle.first_row], grid.row_ys[bundle.last_row]
        own = half[index]
        xs = []
        for row in range(bundle.first_row + 1, bundle.last_row):
            blocks = [(left - own, right + own) for left, right in grid.blocks[row]]
            blocks += [(x - other - _LANE - own, x + other + _LANE + own) for x, other, _ in taken.get(row, ())]
            blocks += [
                (x - other - _NEAR - own, x + other + _NEAR + own)
                for near_row in (row - 1, row + 1)
                for x, other, owner in taken.get(near_row, ())
                if owner != index
            ]
            target = x1 + (x2 - x1) * (grid.row_ys[row] - y1) / (y2 - y1)
            x, width = _take_channel(blocks, target, own, width)
            taken.setdefault(row, []).append((x, own, index))
            xs.append(x)
        channels[index] = tuple(xs)
    return channels, width


def _take_channel(blocks: Sequence[tuple[int, int]], target: float, half: int, width: int) -> tuple[int, int]:
    low, high = _CLEARANCE + half, width - _CLEARANCE - half
    ends = (end for block in blocks for end in block)
    free = [x for x in (round(target), low, high, *ends) if low <= x <= high and not _inside(x, blocks)]
    if free:
        return min(free, key=lambda x: (abs(x - target), x)), width
    x = max((low, *(right for _, right in blocks)))
    return x, x + half + _CLEARANCE


def _inside(x: float, blocks: Sequence[tuple[float, float]]) -> bool:
    return any(left < x < right for left, right in blocks)


def _ports(
    bundles: Sequence[_Bundle],
    grid: _Grid,
    channels: Mapping[int, Sequence[int]],
    side_spacing: Mapping[tuple[str, str], int],
    spacing: Sequence[int],
    half: Sequence[int],
) -> dict[tuple[int, int], int]:
    """x середины пучка на стороне узла для каждого конца: (номер пучка, 0 у first или 1 у last).

    Порядок на верхней стороне слева направо: дуги влево (от ближней к дальней), связи вверх (по x,
    к которому идут), дуги вправо (от дальней к ближней); на нижней — связи вниз по x, к которому
    идут. Так дальняя дуга, которая выше, обходит ближнюю снаружи, а связи узла не пересекаются у
    него. Порты не встают ближе _NEAR к каналам соседней строки, чьи отвесные участки идут в том же
    промежутке."""
    sides: dict[tuple[str, str], list[tuple[tuple[int, int], int, int]]] = {}
    for index, bundle in enumerate(bundles):
        for end, side in enumerate(bundle.sides):
            other = bundle.last if end == 0 else bundle.first
            if bundle.is_arc:  # first — левое лицо, его дуга уходит вправо
                key = (2 if end == 0 else 0, -grid.xs[other])
            else:
                path = (grid.xs[bundle.first], *channels[index], grid.xs[bundle.last])
                key = (1, path[1] if end == 0 else path[-2])
            sides.setdefault(side, []).append((key, index, end))
    row_channels: dict[int, list[tuple[int, int]]] = {}
    for index, xs in channels.items():
        for step, x in enumerate(xs):
            row_channels.setdefault(bundles[index].first_row + 1 + step, []).append((x, half[index]))
    ports = {}
    for (person_id, side), entries in sides.items():
        entries.sort()
        gap = side_spacing[person_id, side]
        counts = [bundles[index].count for _, index, _ in entries]
        steps = [spacing[index] for _, index, _ in entries]
        centre = grid.xs[person_id]
        starts = None
        if gap == _LANE and all(step == _LANE or count == 1 for count, step in zip(counts, steps)):
            row = grid.row_of[person_id] + (1 if side == "bottom" else -1)
            starts = _phase_starts(centre, counts, _PHASE[side], row_channels.get(row, ()))
        if starts is None:
            starts = _packed_starts(centre, counts, steps, gap)
        for (_, index, end), start in zip(entries, starts):
            ports[index, end] = start + half[index]
    return ports


def _packed_starts(centre: int, counts: Sequence[int], steps: Sequence[int], gap: int) -> list[int]:
    """x первой дорожки каждого пучка стороны: пучки подряд через gap, по центру стороны."""
    widths = [(count - 1) * step for count, step in zip(counts, steps)]
    start = centre - (sum(widths) + gap * (len(widths) - 1)) // 2
    starts = []
    for width in widths:
        starts.append(start)
        start += width + gap
    return starts


def _phase_starts(
    centre: int, counts: Sequence[int], phase: int, channels: Sequence[tuple[int, int]]
) -> list[int] | None:
    """x первой дорожки каждого пучка стороны, где все дорожки стоят на сетке _LANE со сдвигом phase,
    подряд и не ближе _NEAR к каналам channels ((x, половина ширины)); из таких раскладок — та, что
    ближе всего к середине стороны. Свой канал пучка тоже отодвигает его порты: иначе порт встаёт в
    1–5 px от канала и путь делает едва заметный излом. None — если лучшая раскладка выходит за
    _PORT_REACH от середины стороны (каналы теснят порты к краю узла)."""
    total = sum(counts)
    base = _snap(centre - (total - 1) * _LANE // 2, phase)

    def blocked(slot: int) -> bool:
        x = base + slot * _LANE
        return any(abs(x - channel) < _NEAR + width for channel, width in channels)

    def pack(first: int) -> list[int]:
        slots, slot = [], first
        for count in counts:
            while any(blocked(slot + lane) for lane in range(count)):
                slot += 1
            slots.append(slot)
            slot += count
        return slots

    def error(slots: list[int]) -> int:
        return abs(base + (slots[0] + slots[-1] + counts[-1] - 1) * _LANE // 2 - centre)

    best = min((pack(first) for first in sorted(range(-total, total + 1), key=abs)), key=error)
    xs = [base + slot * _LANE for slot in best]
    if xs[0] < centre - _PORT_REACH or xs[-1] + (counts[-1] - 1) * _LANE > centre + _PORT_REACH:
        return None
    return xs


def _snap(x: int, phase: int) -> int:
    """Ближайший к x на сетке _LANE со сдвигом phase (при равенстве — меньший)."""
    below = x - (x - phase) % _LANE
    return below if x - below <= _LANE // 2 else below + _LANE


# --- Высоты в промежутках между строками --------------------------------------------------------


@dataclass(frozen=True)
class _Piece:
    """Участок пучка в одном промежутке: отвесно от верхних x, по горизонтали, отвесно к нижним x.
    У дуги верхних x нет: обе её ноги стоят на верхних сторонах узлов строки под промежутком."""

    bundle: int
    index: int  # номер промежутка на пути пучка
    top: tuple[int, ...]  # x дорожек у верха промежутка
    bottom: tuple[int, ...]  # x дорожек у низа промежутка
    half: int  # половина ширины пучка
    span: int  # длина горизонтали по середине пучка

    @property
    def is_arc(self) -> bool:
        return not self.top

    @cached_property
    def left(self) -> int:
        return min(self.top + self.bottom)

    @cached_property
    def right(self) -> int:
        return max(self.top + self.bottom)


def _tracks(
    bundles: Sequence[_Bundle],
    grid: _Grid,
    stations: Sequence[Sequence[int]],
    spacing: Sequence[int],
    half: Sequence[int],
) -> tuple[dict[tuple[int, int], int], list[int]]:
    """y горизонтали каждого участка (пучок, номер участка) и y строк после раздвигания. Промежуток
    номер r лежит над строкой r; над верхней строкой — поле холста."""
    gaps: list[list[_Piece]] = [[] for _ in grid.row_ys]
    for index, bundle in enumerate(bundles):
        lanes = [_lanes(x, bundle.count, spacing[index]) for x in stations[index]]
        if bundle.is_arc:
            span = abs(stations[index][1] - stations[index][0])
            gaps[bundle.first_row].append(_Piece(index, 0, (), lanes[0] + lanes[1], half[index], span))
            continue
        for step, (top, bottom) in enumerate(zip(stations[index], stations[index][1:])):
            if top != bottom:
                piece = _Piece(index, step, lanes[step], lanes[step + 1], half[index], abs(bottom - top))
                gaps[bundle.first_row + 1 + step].append(piece)
    tracks = {}
    row_ys: list[int] = []
    shift = 0
    for row, (y, pieces) in enumerate(zip(grid.row_ys, gaps)):
        gap_top = 0 if row == 0 else row_ys[-1] + _HALF_HEIGHT
        original = y - _HALF_HEIGHT - (0 if row == 0 else grid.row_ys[row - 1] + _HALF_HEIGHT)
        offsets, height = _stack(pieces, original)
        for piece, offset in zip(pieces, offsets):
            tracks[piece.bundle, piece.index] = gap_top + offset
        shift += height - original
        row_ys.append(y + shift)
    return tracks, row_ys


def _stack(pieces: Sequence[_Piece], height: int) -> tuple[list[int], int]:
    """Высоты горизонталей участков одного промежутка (от его верха) и высота промежутка, не меньше
    height, в которую они помещаются. Связи между строками ложатся сверху вниз, дальние выше; дуги —
    снизу вверх, длинные выше. Горизонтали с общим отрезком x стоят не ближе _LANE, связь — выше дуги,
    под которой проходит."""
    wires = [piece for piece in pieces if not piece.is_arc]
    arcs = [piece for piece in pieces if piece.is_arc]
    wire_ys = _stack_wires(wires)
    rises = _stack_arcs(arcs)
    needed = [height]
    needed += [y + wire.half + _LANE for wire, y in zip(wires, wire_ys)]
    needed += [rise + arc.half + _LANE for arc, rise in zip(arcs, rises)]
    needed += [
        y + wire.half + _LANE + arc.half + rise
        for wire, y in zip(wires, wire_ys)
        for arc, rise in zip(arcs, rises)
        if _overlap(wire, arc) or _near(wire.top, arc.bottom)
    ]
    height = max(needed)
    ys = iter(wire_ys)
    arc_ys = iter(height - rise for rise in rises)
    return [next(arc_ys) if piece.is_arc else next(ys) for piece in pieces], height


def _stack_wires(wires: Sequence[_Piece]) -> list[int]:
    """Высота каждой связи от верха промежутка: самая верхняя свободная. Связь, чья верхняя нога
    стоит на x нижней ноги другой, кладётся раньше и выше неё."""
    # above[i] — связи, чья верхняя нога стоит на x нижней ноги связи i (сама i не входит).
    above = [
        {other for other, wire in enumerate(wires) if other != index and _near(wire.top, below.bottom)}
        for index, below in enumerate(wires)
    ]
    ys: dict[int, int] = {}
    pending = set(range(len(wires)))
    while pending:
        ready = [index for index in pending if above[index].isdisjoint(pending)] or list(pending)
        index = min(ready, key=lambda index: (-wires[index].span, wires[index].bundle))
        pending.remove(index)
        ys[index] = _free_height(wires, index, ys, above)
    return [ys[index] for index in range(len(wires))]


def _free_height(wires: Sequence[_Piece], index: int, ys: Mapping[int, int], above: Sequence[set[int]]) -> int:
    wire = wires[index]
    candidates = sorted({_LANE + wire.half, *(y + wires[other].half + _LANE + wire.half for other, y in ys.items())})
    # Для каждой уложенной связи: её высота, наименьший зазор, общий ли отрезок x, стоит ли её нога над
    # ногой этой связи и наоборот.
    placed = [
        (y, _LANE + wire.half + wires[other].half, _overlap(wire, wires[other]), other in above[index], index in above[other])
        for other, y in ys.items()
    ]

    def fits(y: int, *, keep_order: bool) -> bool:
        return not any(
            (overlap and abs(y - other_y) < room)
            or (over and y - other_y < room)
            or (keep_order and under and other_y - y < room)
            for other_y, room, overlap, over, under in placed
        )

    in_order = next((y for y in candidates if fits(y, keep_order=True)), None)
    if in_order is not None:
        return in_order
    # Порядок нарушается, только если две связи меняются местами (нога каждой стоит под ногой другой);
    # самая нижняя высота свободна всегда.
    return next(y for y in candidates if fits(y, keep_order=False))


def _stack_arcs(arcs: Sequence[_Piece]) -> list[int]:
    """Подъём каждой дуги над низом промежутка: короткие ниже, длинные над ними."""
    rises: dict[int, int] = {}
    for index in sorted(range(len(arcs)), key=lambda index: (arcs[index].span, arcs[index].bundle)):
        arc = arcs[index]
        candidates = sorted({_ARC_RISE + arc.half, *(rise + arcs[other].half + _LANE + arc.half for other, rise in rises.items())})
        crossing = [(rise, _LANE + arc.half + arcs[other].half) for other, rise in rises.items() if _overlap(arc, arcs[other])]
        rises[index] = next(
            rise for rise in candidates if not any(abs(rise - other_rise) < room for other_rise, room in crossing)
        )
    return [rises[index] for index in range(len(arcs))]


def _overlap(first: _Piece, second: _Piece) -> bool:
    """Горизонтали двух участков проходят над общим отрезком x (с зазором _NEAR)."""
    return first.left < second.right + _NEAR and second.left < first.right + _NEAR


def _near(xs: Sequence[int], others: Sequence[int]) -> bool:
    return any(abs(x - other) < _NEAR for x in xs for other in others)


# --- Пути ---------------------------------------------------------------------------------------


def _centre_line(
    bundle: _Bundle, index: int, stations: Sequence[int], tracks: Mapping[tuple[int, int], int], row_ys: Sequence[int]
) -> list[Point]:
    """Углы пути по середине пучка от first к last."""
    if bundle.is_arc:
        y, track = row_ys[bundle.first_row] - _HALF_HEIGHT, tracks[index, 0]
        return [(stations[0], y), (stations[0], track), (stations[1], track), (stations[1], y)]
    points = [(stations[0], row_ys[bundle.first_row] + _HALF_HEIGHT)]
    for step, (top, bottom) in enumerate(zip(stations, stations[1:])):
        if top != bottom:
            track = tracks[index, step]
            points += [(top, track), (bottom, track)]
    points.append((stations[-1], row_ys[bundle.last_row] - _HALF_HEIGHT))
    corners = [points[0]]
    for point, after in zip(points[1:], points[2:]):
        before = corners[-1]
        if not (before[0] == point[0] == after[0] or before[1] == point[1] == after[1]):
            corners.append(point)
    return [*corners, points[-1]]


def _offset(points: Sequence[Point], distance: int) -> list[Point]:
    """Ортогональная ломаная, параллельная points на distance (по одну сторону хода): дорожки пучка
    с разными distance не сходятся и не пересекаются."""
    normals = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        normals.append((-_sign(y2 - y1), _sign(x2 - x1)))
    shifted = []
    for index, (x, y) in enumerate(points):
        around = normals[max(index - 1, 0) : index + 1]
        shift_x = sum(normal[0] for normal in around)
        shift_y = sum(normal[1] for normal in around)
        shifted.append((x + distance * shift_x, y + distance * shift_y))
    return shifted


def _commands(points: Sequence[Point]) -> tuple[Command, ...]:
    """Ломаная со скруглёнными углами: в каждом угле — квадратичная кривая радиуса до _CORNER."""
    commands: list[Command] = [("M", points[0])]
    for before, corner, after in zip(points, points[1:], points[2:]):
        radius = min(_CORNER, _length(before, corner) // 2, _length(corner, after) // 2)
        commands.append(("L", _toward(corner, before, radius)))
        commands.append(("Q", (*corner, *_toward(corner, after, radius))))
    commands.append(("L", points[-1]))
    return tuple(commands)


def _length(first: Point, second: Point) -> int:
    return abs(second[0] - first[0]) + abs(second[1] - first[1])


def _toward(start: Point, end: Point, distance: int) -> Point:
    """Точка отрезка от start к end (отвесного или горизонтального) на distance от start."""
    return start[0] + distance * _sign(end[0] - start[0]), start[1] + distance * _sign(end[1] - start[1])


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


# --- Подписи ------------------------------------------------------------------------------------


def _label_anchors(points: Sequence[Point]) -> list[Point]:
    """Места подписи в порядке предпочтения: точки на долях _LABEL_STEPS длины пути (целые x и
    базовая линия; базовая линия ниже точки на 0,4 кегля, так строка встаёт на путь серединой)."""
    lengths = [0.0]
    for before, after in zip(points, points[1:]):
        lengths.append(lengths[-1] + _length(before, after))
    anchors = []
    for fraction in _LABEL_STEPS:
        distance = fraction * lengths[-1]
        index = max(1, min(bisect.bisect_left(lengths, distance), len(points) - 1))
        span = lengths[index] - lengths[index - 1]
        t = (distance - lengths[index - 1]) / span if span else 0.0
        (x1, y1), (x2, y2) = points[index - 1], points[index]
        anchors.append((round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t + LABEL_SIZE * 0.4)))
    return anchors


def _label_anchor(label: str, anchors: Sequence[Point], node_boxes: Sequence[Box], label_boxes: Sequence[Box]) -> Point:
    """Первая точка с наименьшим числом задетых подписью узлов, затем прежних подписей."""

    def hits(anchor: Point) -> tuple[int, int]:
        box = _label_box(label, anchor)
        return (
            sum(_boxes_overlap(box, node) for node in node_boxes),
            sum(_boxes_overlap(box, other) for other in label_boxes),
        )

    return min(anchors, key=hits)


def _label_box(label: str, anchor: Point) -> Box:
    """Рамка подписи по центру anchor (x, базовая линия) с зазором."""
    x, baseline = anchor
    half = sum(map(char_em, label)) * LABEL_SIZE / 2 + _LABEL_GAP
    return (x - half, baseline - LABEL_SIZE - _LABEL_GAP, x + half, baseline + LABEL_SIZE * 0.3 + _LABEL_GAP)


def _on_canvas(label: str, anchors: Sequence[Point], width: int, height: int) -> list[Point]:
    """anchors, сдвинутые так, чтобы рамка подписи (_label_box) лежала на холсте; подпись шире
    холста стоит по его середине."""
    left, top, right, bottom = _label_box(label, (0, 0))
    x_low, x_high = math.ceil(-left), math.floor(width - right)
    y_low, y_high = math.ceil(-top), math.floor(height - bottom)

    def clamp(value: int, low: int, high: int, size: int) -> int:
        return min(max(value, low), high) if low <= high else round(size / 2)

    return [(clamp(x, x_low, x_high, width), clamp(y, y_low, y_high, height)) for x, y in anchors]


def _boxes_overlap(first: Box, second: Box) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]
