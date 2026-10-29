"""Геометрия связей графа родства: порты на сторонах узлов, каналы через строки, высоты переходов в
промежутках между строками и места подписей.

Чистые вычисления на значениях: на входе граф и сетка tree_layout, на выходе Routes — пути из
отрезков и скруглённых углов в целых координатах, места подписей, центры узлов и размер холста.
Все связи одной пары лиц идут пучком, но каждая дорожка пучка — свой путь от своего порта до своего:
на стороне узла дорожки стоят через шаг этой стороны, в каналах и на горизонталях — через _LANE, и
друг друга не пересекают. Каждый путь ортогонален: отвесно от порта на стороне узла, по горизонтали
в промежутке между строками, отвесно через свободный канал строки, которую пересекает. Порты одной
стороны ставит один перебор раскладок по возрастанию стоимости (_side_starts): дорожки подальше от
чужих каналов, на сетке стороны, ровно на своём отвесном участке, ближе к середине стороны.
Отвесные участки разных путей стоят на разных x, горизонтали с общим отрезком x — на разных высотах;
где промежутку не хватает высоты, строки ниже сдвигаются, где строке не хватает каналов, холст
расширяется вправо.
"""

from __future__ import annotations

import bisect
import itertools
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
NODE_RADIUS = 6  # радиус скругления углов рамки узла
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
# Ни один конец пути не дальше этого от середины стороны: на прямом участке рамки, до скругления.
_END_REACH = NODE_WIDTH // 2 - NODE_RADIUS
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
    channels, row_channels, width = _channels(bundles, grid, round(layout.width))
    stations = _stations(bundles, grid, channels, row_channels, _side_spacing(bundles))
    tracks, row_ys = _tracks(bundles, grid, stations)
    growth = row_ys[-1] - grid.row_ys[-1] if row_ys else 0
    height = round(layout.height) + growth

    lines: list[list[Point]] = [[] for _ in graph.edges]
    for index, bundle in enumerate(bundles):
        for edge_index, line in zip(bundle.edges, _lane_lines(bundle, index, stations[index], tracks, row_ys)):
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
    def half(self) -> int:
        """Половина ширины пучка с дорожками через _LANE: в каналах и на горизонталях."""
        return (self.count - 1) * _LANE // 2

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
    """Шаг портов на каждой стороне узла по числу её дорожек (см. _spacing)."""
    lanes: dict[tuple[str, str], int] = {}
    for bundle in bundles:
        for side in bundle.sides:
            lanes[side] = lanes.get(side, 0) + bundle.count
    return {side: _spacing(count) for side, count in lanes.items()}


def _spacing(lanes: int) -> int:
    """Шаг, при котором lanes дорожек помещаются в 2 × _END_REACH: чётный, пока он не меньше 2
    (дорожки пучка симметричны), затем 1, а больше чем 2 × _END_REACH + 1 дорожек сходятся в
    середине стороны (шаг 0): в целых координатах их не развести, но ни одна не уходит со стороны."""
    if lanes < 2 or (lanes - 1) * _LANE <= 2 * _END_REACH:
        return _LANE
    step = 2 * _END_REACH // (lanes - 1)
    return step // 2 * 2 if step >= 2 else step


# --- Каналы и порты -----------------------------------------------------------------------------


def _lanes(start: int, count: int, step: int) -> tuple[int, ...]:
    """x дорожек пучка слева направо от start."""
    return tuple(start + lane * step for lane in range(count))


def _jog(x: int, other: int | None) -> bool:
    """Излом в 1–5 px: x не совпадает с other, но ближе _NEAR к нему (other None — не с чем сравнить)."""
    return other is not None and 0 < abs(x - other) < _NEAR


def _channels(
    bundles: Sequence[_Bundle], grid: _Grid, width: int
) -> tuple[dict[int, tuple[int, ...]], dict[int, list[tuple[int, int]]], int]:
    """x середины канала пучка в каждой строке между его концами (сверху вниз), каналы каждой строки
    ((x середины, половина ширины пучка)) и ширина холста, которая их вмещает. Канал — ближайший к прямой между центрами концов x, где весь пучок (дорожки
    через _LANE) отстоит от узлов на _CLEARANCE, от прежних каналов строки на _LANE и от чужих каналов
    соседних строк на _NEAR (их отвесные участки встречаются в общем промежутке), и который не встаёт
    в 1–5 px от канала того же пучка строкой выше; если такого нет, канал встаёт правее всего в строке,
    а холст расширяется."""
    taken: dict[int, list[tuple[int, int, int]]] = {}  # строка → (x, половина ширины, пучок)
    channels = {}
    for index, bundle in enumerate(bundles):
        if bundle.is_arc:
            continue
        x1, x2 = grid.xs[bundle.first], grid.xs[bundle.last]
        y1, y2 = grid.row_ys[bundle.first_row], grid.row_ys[bundle.last_row]
        own = bundle.half
        xs: list[int] = []
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
            x, width = _take_channel(blocks, target, own, width, xs[-1] if xs else None)
            taken.setdefault(row, []).append((x, own, index))
            xs.append(x)
        channels[index] = tuple(xs)
    rows = {row: [(x, half) for x, half, _ in entries] for row, entries in taken.items()}
    return channels, rows, width


def _take_channel(
    blocks: Sequence[tuple[int, int]], target: float, half: int, width: int, previous: int | None
) -> tuple[int, int]:
    low, high = _CLEARANCE + half, width - _CLEARANCE - half
    ends = (end for block in blocks for end in block)
    around = () if previous is None else (previous - _NEAR, previous, previous + _NEAR)
    candidates = (round(target), low, high, *around, *ends)
    free = [x for x in candidates if low <= x <= high and not _inside(x, blocks)]
    if free:
        return min(free, key=lambda x: (_jog(x, previous), abs(x - target), x)), width
    x = max((low, *(right for _, right in blocks)))
    return x, x + half + _CLEARANCE


def _inside(x: float, blocks: Sequence[tuple[float, float]]) -> bool:
    return any(left < x < right for left, right in blocks)


def _stations(
    bundles: Sequence[_Bundle],
    grid: _Grid,
    channels: Mapping[int, Sequence[int]],
    row_channels: Mapping[int, Sequence[tuple[int, int]]],
    side_spacing: Mapping[tuple[str, str], int],
) -> list[list[tuple[int, ...]]]:
    """x дорожек каждого пучка слева направо на каждом отвесном участке от first к last: порт на
    стороне узла first, каналы, порт на стороне узла last. На стороне узла дорожки стоят через её шаг
    (_spacing), в канале — через _LANE; порт, ещё не размеченный, — None."""
    paths: list[list[tuple[int, ...] | None]] = [
        [None, *(_lanes(x - bundle.half, bundle.count, _LANE) for x in channels.get(index, ())), None]
        for index, bundle in enumerate(bundles)
    ]
    for (person_id, side), entries in _side_order(bundles, grid, channels):
        step = side_spacing[person_id, side]
        counts = [bundles[index].count for index, _ in entries]
        row = grid.row_of[person_id] + (1 if side == "bottom" else -1)
        own = [_own_lanes(bundles[index], end, paths[index]) for index, end in entries]
        starts = _side_starts(grid.xs[person_id], counts, step, _PHASE[side], row_channels.get(row, ()), own)
        for (index, end), count, start in zip(entries, counts, starts):
            paths[index][0 if end == 0 else -1] = _lanes(start, count, step)
    return paths  # type: ignore[return-value]  # все порты размечены


def _side_order(
    bundles: Sequence[_Bundle], grid: _Grid, channels: Mapping[int, Sequence[int]]
) -> list[tuple[tuple[str, str], list[tuple[int, int]]]]:
    """Стороны узлов в порядке разметки и концы (пучок, 0 у first или 1 у last) на каждой слева направо.

    Порядок пучков на верхней стороне слева направо: дуги влево (от ближней к дальней), связи вверх (по
    x, к которому идут), дуги вправо (от дальней к ближней); на нижней — связи вниз по x, к которому
    идут. Так дальняя дуга, которая выше, обходит ближнюю снаружи, а связи узла не пересекаются у него.
    Стороны размечаются по строкам сверху вниз, в строке сначала верхние: так у порта нижнего конца уже
    известен соседний отвесный участок пучка (канал или порт верхнего конца), и он может встать ровно на
    него (_side_starts)."""
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
    order = sorted(sides, key=lambda side: (grid.row_of[side[0]], side[1] == "bottom", side[0]))
    return [(side, [(index, end) for _, index, end in sorted(sides[side])]) for side in order]


def _own_lanes(bundle: _Bundle, end: int, path: Sequence[tuple[int, ...] | None]) -> tuple[int, ...] | None:
    """x дорожек соседнего с концом end отвесного участка того же пучка по ту сторону промежутка (path —
    участки пучка от first к last): канала в строке рядом с концом или, у нижнего конца связи между
    соседними строками, порта верхнего конца. None у дуги и у верхнего конца связи между соседними
    строками: его нижний порт ещё не размечен (_side_order)."""
    return None if bundle.is_arc else path[1 if end == 0 else -2]


def _side_starts(
    centre: int,
    counts: Sequence[int],
    step: int,
    phase: int,
    channels: Sequence[tuple[int, int]],
    own: Sequence[tuple[int, ...] | None],
) -> list[int]:
    """x первой дорожки каждого пучка стороны (в их порядке): дорожки пучка через step, пучки подряд не
    ближе step (на тесной стороне, где step меньше _LANE, — ровно через step), все в пределах
    ±_END_REACH от середины стороны centre (_spacing держит их ширину в 2 × _END_REACH). Из всех таких
    раскладок — с наименьшей суммой _port_cost по пучкам, сравниваемой по порядку: дорожки у каналов,
    дорожки вне сетки, дорожки не на своём участке, отступ от раскладки по середине стороны; при
    равенстве — левее.

    Динамика по пучкам слева направо: у пучка i допустимы x от lows[i] на size шагов в 1 px, и при x на
    offset от lows[i] пучок i − 1 стоит не правее x на offset от lows[i − 1] (ровно там на тесной
    стороне)."""
    widths = [(count - 1) * step for count in counts]
    spread = step == _LANE
    span = sum(widths) + step * (len(counts) - 1)
    size = 2 * _END_REACH - span + 1
    shift = _END_REACH - span // 2  # отступ раскладки по середине стороны от самой левой
    lows = []
    before = 0
    for width in widths:
        lows.append(centre - _END_REACH + before)
        before += width + step
    # Все дорожки стороны лежат в centre ± _END_REACH: близость к каналам считается один раз на x.
    near = {x: _near_channel(x, channels) for x in range(centre - _END_REACH, centre + _END_REACH + 1)}
    # best[i][offset] — наименьшая сумма стоимостей пучков 0..i при первой дорожке пучка i на lows[i] + offset
    # и x пучка i − 1 в этой сумме.
    best: list[list[tuple[tuple[int, ...], int]]] = []
    for item, (count, low) in enumerate(zip(counts, lows)):
        costs = [_port_cost(low + offset, count, step, phase, near, own[item], low + shift) for offset in range(size)]
        if not item:
            best.append([(cost, low + offset) for offset, cost in enumerate(costs)])
            continue
        previous = [(cost, lows[item - 1] + offset) for offset, (cost, _) in enumerate(best[-1])]
        if spread:  # лучшая сумма с пучком i − 1 не правее
            previous = list(itertools.accumulate(previous, min))
        best.append([(tuple(map(sum, zip(cost, before))), back) for cost, (before, back) in zip(costs, previous)])
    offset = min(range(size), key=lambda offset: best[-1][offset][0])  # при равенстве — левее
    starts = [lows[-1] + offset]
    for item in range(len(counts) - 1, 0, -1):
        starts.append(best[item][starts[-1] - lows[item]][1])
    return starts[::-1]


def _port_cost(
    x: int,
    count: int,
    step: int,
    phase: int,
    near: Mapping[int, bool],
    own: tuple[int, ...] | None,
    ideal: int,
) -> tuple[int, int, int, int]:
    """Стоимость пучка из count дорожек через step с первой на x: (сколько дорожек у каналов (near: x →
    ближе ли он _NEAR к краю канала, _near_channel) или в 1–5 px от своего участка own, сколько не на сетке
    _LANE со сдвигом phase, сколько не на своём участке own, отступ x от ideal). Дорожка ровно на своём
    участке идёт к нему отвесно: она не теснит каналы и не нуждается в фазе."""
    close = off_phase = off_own = 0
    for lane in range(count):
        lane_x = x + lane * step
        target = None if own is None else own[lane]
        if lane_x == target:
            continue
        close += _jog(lane_x, target) or near[lane_x]
        off_phase += (lane_x - phase) % _LANE != 0
        off_own += target is not None
    return close, off_phase, off_own, abs(x - ideal)


def _near_channel(x: int, channels: Sequence[tuple[int, int]]) -> bool:
    """Ближе ли x к одному из каналов ((x, половина ширины пучка)), чем _NEAR от края пучка."""
    return any(abs(x - channel) < _NEAR + width for channel, width in channels)


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
    bundles: Sequence[_Bundle], grid: _Grid, stations: Sequence[Sequence[tuple[int, ...]]]
) -> tuple[dict[tuple[int, int], int], list[int]]:
    """y середины горизонтали каждого участка (пучок, номер участка) и y строк после раздвигания.
    Промежуток номер r лежит над строкой r; над верхней строкой — поле холста."""
    gaps: list[list[_Piece]] = [[] for _ in grid.row_ys]
    for index, bundle in enumerate(bundles):
        lanes = stations[index]
        if bundle.is_arc:
            span = abs(_middle(lanes[1]) - _middle(lanes[0]))
            gaps[bundle.first_row].append(_Piece(index, 0, (), lanes[0] + lanes[1], bundle.half, span))
            continue
        for step, (top, bottom) in enumerate(zip(lanes, lanes[1:])):
            if top != bottom:
                piece = _Piece(index, step, top, bottom, bundle.half, abs(_middle(bottom) - _middle(top)))
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


def _middle(lanes: Sequence[int]) -> int:
    return (lanes[0] + lanes[-1]) // 2


def _lane_lines(
    bundle: _Bundle,
    index: int,
    stations: Sequence[tuple[int, ...]],
    tracks: Mapping[tuple[int, int], int],
    row_ys: Sequence[int],
) -> list[list[Point]]:
    """Углы пути каждой дорожки пучка от first к last, в порядке bundle.edges. Дорожка 0 у связи между
    строками — правая на всех отвесных участках, у дуги — левая у first, правая у last и верхняя."""
    count, half = bundle.count, bundle.half
    if bundle.is_arc:
        y = row_ys[bundle.first_row] - _HALF_HEIGHT
        left, right = stations
        lines = []
        for lane in range(count):
            track = tracks[index, 0] - half + lane * _LANE
            right_x = right[count - 1 - lane]
            lines.append([(left[lane], y), (left[lane], track), (right_x, track), (right_x, y)])
        return lines
    top_y = row_ys[bundle.first_row] + _HALF_HEIGHT
    bottom_y = row_ys[bundle.last_row] - _HALF_HEIGHT
    # Горизонталь есть только там, где отвесные участки пучка сверху и снизу промежутка не совпадают.
    ranks = {
        step: _lane_ranks(top, bottom)
        for step, (top, bottom) in enumerate(zip(stations, stations[1:]))
        if top != bottom
    }
    lines = []
    for position in reversed(range(count)):
        points = [(stations[0][position], top_y)]
        for step, rank in ranks.items():
            y = tracks[index, step] - half + rank[position] * _LANE
            points += [(stations[step][position], y), (stations[step + 1][position], y)]
        points.append((stations[-1][position], bottom_y))
        lines.append(_corners(points))
    return lines


def _lane_ranks(top: Sequence[int], bottom: Sequence[int]) -> list[int]:
    """Ряд горизонтали каждой дорожки (по порядку слева направо) в участке от x top к x bottom, 0 —
    верхний. Пучок, идущий вправо, кладёт правые дорожки выше, идущий влево — левые: так дорожки
    вкладываются друг в друга. Если шаги дорожек сверху и снизу разные, дорожка может обогнать соседнюю:
    левая, чей низ правее верха правой, идёт ниже неё, правая, чей низ левее верха левой, — ниже левой;
    иначе их горизонтали пересекли бы отвесные участки друг друга."""
    count = len(top)
    pending = sorted(range(count), reverse=sum(bottom) >= sum(top))
    above: list[set[int]] = [set() for _ in range(count)]  # above[p] — дорожки, чья горизонталь выше p
    for left, right in itertools.combinations(range(count), 2):
        if bottom[left] >= top[right]:
            above[left].add(right)
        elif bottom[right] <= top[left]:
            above[right].add(left)
    ranks = [0] * count
    for rank in range(count):
        position = next((position for position in pending if above[position].isdisjoint(pending)), pending[0])
        pending.remove(position)
        ranks[position] = rank
    return ranks


def _corners(points: Sequence[Point]) -> list[Point]:
    """Углы ортогональной ломаной: без повторов и точек посреди прямого участка."""
    kept = [point for point, before in zip(points, (None, *points)) if point != before]
    corners = [kept[0]]
    for point, after in zip(kept[1:], kept[2:]):
        before = corners[-1]
        if not (before[0] == point[0] == after[0] or before[1] == point[1] == after[1]):
            corners.append(point)
    return [*corners, kept[-1]]


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
