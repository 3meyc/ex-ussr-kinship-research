"""Отрисовка графа родства: SVG, интерактивная страница HTML, страница печати PDF, Mermaid и DOT.

Отрисовщики читают только Node и Edge из tree_model (сведения уже очищены по режиму) и
раскладку из tree_layout. Любой текст из реестров — данные: он экранируется в каждом формате.
"""

from __future__ import annotations

import base64
import bisect
import html
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tree_model import DIRECTED_TYPES, STATUSES

if TYPE_CHECKING:
    from tree_layout import Layout
    from tree_model import Edge, Graph, Node

    _Point = tuple[float, float]
    _Box = tuple[float, float, float, float]  # left, top, right, bottom
    _Route = tuple[str, list[_Point]]  # атрибут d пути и точки вдоль него

BANNER_PRIVATE = "Содержит сведения о живых лицах — не для передачи"
BANNER_SHAREABLE = "Живые лица скрыты: показан только person_id"
HIDDEN_NOTE = "сведения скрыты"
EMPTY_NOTE = "В реестре нет лиц"
TITLE = "Граф родства"

_INK = "#333333"


@dataclass(frozen=True)
class _Encoding:
    """Вид связи одного статуса во всех форматах."""

    colour: str
    width: float
    dash: str  # stroke-dasharray SVG; пусто — сплошная
    dot_style: str
    dot_colour: str  # пусто — цвет Graphviz по умолчанию
    mermaid_directed: str
    mermaid_undirected: str
    meaning: str  # подпись в легенде


_ENCODINGS = {
    "strongly-corroborated": _Encoding(_INK, 4.5, "", "bold", "", "==>", "===", "надёжно подтверждена"),
    "corroborated": _Encoding(_INK, 1.5, "", "solid", "", "-->", "---", "подтверждена"),
    "single-source": _Encoding(_INK, 1.5, "8 5", "dashed", "", "-.->", "-.-", "один источник"),
    "hypothesis": _Encoding(_INK, 1.5, "2 5", "dotted", "", "-.->", "-.-", "гипотеза"),
    "unresolved": _Encoding("#888888", 1.5, "", "solid", "grey", "-->", "---", "не решена или статус не указан"),
    "contradicted": _Encoding("#c62828", 1.5, "", "solid", "red", "-->", "---", "противоречит источникам"),
}
_ARROW_NOTE = (
    "Стрелка идёт от person_a к person_b у "
    + " и ".join(filter(None, (", ".join(DIRECTED_TYPES[:-1]), DIRECTED_TYPES[-1])))
    + "."
)

# Размер узла меньше шага сетки tree_layout. Центры узлов лежат на целочисленной сетке, поэтому
# все координаты ниже — целые числа (или с одной десятичной у толщины линий).
_NODE_WIDTH = 160
_NODE_HEIGHT = 48
_NODE_PADDING = 6  # поле слева и справа от текста в узле
# Подъём дуги между лицами одной строки: _ARC_RISE плюс _ARC_SPAN_RISE на каждое удвоение пролёта
# сверх ширины узла (длинная дуга проходит над короткой) плюс _LANE на каждую следующую связь пары.
_ARC_RISE = 24
_ARC_SPAN_RISE = 10
_LANE = 12  # шаг между связями одной пары лиц и между каналами связей в строке
_CLEARANCE = 12  # наименьший зазор между каналом связи и чужим узлом
_CURVE_SAMPLES = 16  # точек на участок кривой при поиске места подписи
_CHAR_EM = 0.6  # ширина знака с запасом, в долях кегля (sans-serif)
_LABEL_SIZE = 10
_LABEL_GAP = 2  # зазор вокруг подписи
# Доли длины пути, на которых пробуется подпись: от середины к концам, не доходя до узлов и стрелки.
_LABEL_STEPS = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8)


def banner(graph: Graph) -> str:
    return BANNER_SHAREABLE if graph.shareable else BANNER_PRIVATE


# --- SVG ---------------------------------------------------------------------------------------


def render_svg(graph: Graph, layout: Layout) -> str:
    """Самодостаточный SVG: стили связей — атрибуты, стрелки — в собственных <defs>, поэтому
    он выглядит так же внутри <img>."""
    width, height = _num(layout.width), _num(layout.height)
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" font-family="sans-serif" role="img" aria-label="{TITLE}">'
        ),
        _svg_markers(),
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<g class="edges">',
        *_svg_edges(graph, layout),
        "</g>",
        '<g class="nodes">',
        *(_svg_node(node, layout) for node in graph.nodes),
        "</g>",
        "</svg>",
    ]
    return "\n".join(parts)


def _svg_markers() -> str:
    markers = (
        f'<marker id="arrow-{status}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="10" '
        f'markerHeight="10" markerUnits="userSpaceOnUse" orient="auto">'
        f'<path d="M0 0 L10 5 L0 10 z" fill="{encoding.colour}"/></marker>'
        for status, encoding in _ENCODINGS.items()
    )
    return "<defs>" + "".join(markers) + "</defs>"


def _svg_edges(graph: Graph, layout: Layout) -> list[str]:
    """Связи по порядку; подпись каждой — в первой точке пути, свободной от узлов и прежних подписей,
    сдвинутой внутрь холста."""
    node_boxes = [_node_box(layout.positions[node.person_id]) for node in graph.nodes]
    router = _Router(layout)
    label_boxes: list[_Box] = []
    parts = []
    for edge, lane in zip(graph.edges, _pair_lanes(graph.edges)):
        path, points = router.route(edge, lane)
        anchors = [_on_canvas(edge.label, anchor, layout) for anchor in _label_anchors(points)]
        anchor = _label_anchor(edge.label, anchors, node_boxes, label_boxes)
        label_boxes.append(_label_box(edge.label, anchor))
        parts.append(_svg_edge(edge, path, anchor))
    return parts


def _label_anchor(
    label: str, anchors: Sequence[tuple[int, int]], node_boxes: Sequence[_Box], label_boxes: Sequence[_Box]
) -> tuple[int, int]:
    """Первая точка, где подпись не задевает узлы, а среди них — прежние подписи; если такой нет —
    с наименьшим числом задетых узлов, затем подписей."""

    def hits(anchor: tuple[int, int]) -> tuple[int, int]:
        box = _label_box(label, anchor)
        return (
            sum(_overlap(box, node) for node in node_boxes),
            sum(_overlap(box, other) for other in label_boxes),
        )

    for anchor in anchors:
        if hits(anchor) == (0, 0):
            return anchor
    return min(anchors, key=hits)


def _label_box(label: str, anchor: tuple[int, int]) -> _Box:
    """Рамка подписи по центру anchor (x, базовая линия) с зазором."""
    x, baseline = anchor
    half = len(label) * _LABEL_SIZE * _CHAR_EM / 2 + _LABEL_GAP
    return (x - half, baseline - _LABEL_SIZE - _LABEL_GAP, x + half, baseline + _LABEL_SIZE * 0.3 + _LABEL_GAP)


def _on_canvas(label: str, anchor: tuple[int, int], layout: Layout) -> tuple[int, int]:
    """anchor, сдвинутый так, чтобы рамка подписи (_label_box) лежала на холсте; подпись шире
    холста стоит по его середине."""
    left, top, right, bottom = _label_box(label, (0, 0))
    x_low, x_high = math.ceil(-left), math.floor(layout.width - right)
    y_low, y_high = math.ceil(-top), math.floor(layout.height - bottom)
    x = min(max(anchor[0], x_low), x_high) if x_low <= x_high else round(layout.width / 2)
    y = min(max(anchor[1], y_low), y_high) if y_low <= y_high else round(layout.height / 2)
    return x, y


def _overlap(first: _Box, second: _Box) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def _svg_edge(edge: Edge, path: str, anchor: tuple[int, int]) -> str:
    label_x, label_y = anchor
    encoding = _ENCODINGS[edge.status]
    style = _stroke_attrs(encoding)
    if encoding.dash:
        style += ' stroke-linecap="round"'
    if edge.directed:
        style += f' marker-end="url(#arrow-{edge.status})"'
    # Первый <path> — видимая линия со стилем; второй — широкая прозрачная зона для щелчка.
    return (
        f'<g class="edge" data-id="{_attr(edge.relationship_id)}" data-status="{edge.status}" tabindex="0">'
        f'<path d="{path}" fill="none"{style}/>'
        f'<path d="{path}" fill="none" stroke="transparent" stroke-width="14"/>'
        f'<text x="{label_x}" y="{label_y}" text-anchor="middle" font-size="{_LABEL_SIZE}" fill="{_INK}" '
        f'stroke="#ffffff" stroke-width="3" paint-order="stroke">{_attr(edge.label)}</text></g>'
    )


def _stroke_attrs(encoding: _Encoding) -> str:
    """Цвет, толщина и штрих линии статуса — атрибуты SVG (их сохраняет и PDF)."""
    style = f' stroke="{encoding.colour}" stroke-width="{_num(encoding.width)}"'
    if encoding.dash:
        style += f' stroke-dasharray="{encoding.dash}"'
    return style


@dataclass(frozen=True)
class _Lane:
    """Место связи среди связей той же пары лиц (по порядку relationship_id)."""

    index: int
    count: int

    @property
    def offset(self) -> int:
        """Сдвиг концов по x: дорожки пары расходятся на _LANE симметрично середине узла."""
        return round((self.index - (self.count - 1) / 2) * _LANE)


def _pair_lanes(edges: Sequence[Edge]) -> list[_Lane]:
    """Дорожка каждой связи; у единственной связи пары — _Lane(0, 1)."""
    pairs: dict[frozenset[str], list[int]] = {}
    for index, edge in enumerate(edges):
        pairs.setdefault(frozenset((edge.a, edge.b)), []).append(index)
    lanes = [_Lane(0, 1)] * len(edges)
    for indexes in pairs.values():
        for lane, index in enumerate(indexes):
            lanes[index] = _Lane(lane, len(indexes))
    return lanes


class _Router:
    """Пути связей в порядке вызова. Связь через несколько строк проходит каждую промежуточную
    строку по вертикальному каналу, свободному от узлов (с зазором _CLEARANCE) и от каналов прежних
    связей (с шагом _LANE), а между строками — кривой. Поэтому длинная связь не ложится ни под узел,
    ни на другую связь; связи соседних строк и одной строки проходят только промежутки между строками.
    Несколько связей одной пары лиц идут дорожками (_lane_points) через общие каналы, по каналу на
    строку для всей пары."""

    def __init__(self, layout: Layout) -> None:
        self._positions = layout.positions
        self._low, self._high = _CLEARANCE, layout.width - _CLEARANCE
        self._nodes: dict[float, list[tuple[float, float]]] = {}  # y строки → запретные отрезки x
        for x, y in layout.positions.values():
            reach = _NODE_WIDTH / 2 + _CLEARANCE
            self._nodes.setdefault(y, []).append((x - reach, x + reach))
        self._channels: dict[float, list[tuple[float, float]]] = {}  # y строки → занятые каналы
        self._bundles: dict[frozenset[str], tuple[float, ...]] = {}  # пара лиц → x её каналов сверху вниз

    def route(self, edge: Edge, lane: _Lane) -> _Route:
        (x1, y1), (x2, y2) = self._positions[edge.a], self._positions[edge.b]
        if y1 == y2:
            return _arc((x1, y1 - _NODE_HEIGHT / 2), (x2, y2 - _NODE_HEIGHT / 2), lane)
        crossed = sorted(y for y in self._nodes if min(y1, y2) < y < max(y1, y2))
        if crossed and lane.count == 1:
            return self._single((x1, y1), (x2, y2), crossed[::-1] if y2 < y1 else crossed)
        upper, lower = ((x1, y1), (x2, y2)) if y1 < y2 else ((x2, y2), (x1, y1))
        channels = self._bundle(frozenset((edge.a, edge.b)), upper, lower, crossed, lane)
        half = _NODE_HEIGHT / 2
        stops = [
            (upper[0], upper[1] + half),
            *((x, y + side) for x, y in zip(channels, crossed) for side in (-half, half)),
            (lower[0], lower[1] - half),
        ]
        # Остановки идут парами через промежутки между строками; между парами — отвесный канал.
        points = [point for gap in range(0, len(stops), 2) for point in _lane_points(*stops[gap : gap + 2], lane)]
        if y2 < y1:
            points.reverse()
        return " ".join(f"{'L' if index else 'M'}{_num(x)} {_num(y)}" for index, (x, y) in enumerate(points)), points

    def _single(self, start: _Point, end: _Point, crossed: Sequence[float]) -> _Route:
        """Единственная связь пары через строки crossed (в порядке от start к end)."""
        (x1, y1), (x2, y2) = start, end
        half = _NODE_HEIGHT / 2 if y2 > y1 else -_NODE_HEIGHT / 2
        points = [(x1, y1 + half)]
        for row_y in crossed:
            x = self._take_channel(row_y, x1 + (x2 - x1) * (row_y - y1) / (y2 - y1), half_width=0)
            points += [(x, row_y - half), (x, row_y + half)]
        return _channel_path([*points, (x2, y2 - half)])

    def _bundle(
        self, pair: frozenset[str], upper: _Point, lower: _Point, crossed: Sequence[float], lane: _Lane
    ) -> tuple[float, ...]:
        """Каналы пары в строках crossed (сверху вниз): занимает их первая дорожка пары, сразу на
        ширину всех дорожек."""
        if pair not in self._bundles:
            (x1, y1), (x2, y2) = upper, lower
            half_width = (lane.count - 1) * _LANE / 2
            self._bundles[pair] = tuple(
                self._take_channel(row_y, x1 + (x2 - x1) * (row_y - y1) / (y2 - y1), half_width=half_width)
                for row_y in crossed
            )
        return self._bundles[pair]

    def _take_channel(self, row_y: float, target: float, *, half_width: float) -> float:
        """Ближайший к target x строки, у которого дорожки на half_width в обе стороны лежат вне узлов,
        затем вне занятых каналов (при равенстве — меньший); выбранный канал занимается."""
        nodes = [(left - half_width, right + half_width) for left, right in self._nodes[row_y]]
        taken = self._channels.setdefault(row_y, [])
        channels = [(left - half_width, right + half_width) for left, right in taken]
        low, high = self._low + half_width, self._high - half_width
        target = min(max(round(target), low), high)
        bounds = (end for block in (*nodes, *channels) for end in block)
        candidates = [x for x in (target, low, high, *bounds) if low <= x <= high]

        def cost(x: float) -> tuple[bool, bool, float, float]:
            return (_inside(x, nodes), _inside(x, channels), abs(x - target), x)

        channel = min(candidates, key=cost)
        taken.append((channel - half_width - _LANE, channel + half_width + _LANE))
        return channel


def _inside(x: float, blocks: Sequence[tuple[float, float]]) -> bool:
    return any(left < x < right for left, right in blocks)


def _lane_points(upper: _Point, lower: _Point, lane: _Lane) -> list[_Point]:
    """Дорожка через промежуток между строками от upper (выше) к lower. Единственная связь пары —
    отрезок. Дорожки пары сдвинуты по x на lane.offset и вложены друг в друга: каждая идёт от upper
    отвесно, затем наклонным отрезком, параллельным соседним, затем отвесно к lower. Дорожка, которая
    отходит дальше в сторону наклона, поворачивает раньше; так соседние дорожки идут не ближе _LANE и
    на отвесных участках, и поперёк наклонных."""
    shift = lane.offset
    lean = (lower[0] > upper[0]) - (lower[0] < upper[0])  # куда идёт связь сверху вниз: -1, 0, 1
    spread = (lane.count - 1) * _LANE  # сумма двух отвесных участков каждой дорожки
    drop = spread / 2 - lean * shift  # отвесный участок у upper
    top, bottom = (upper[0] + shift, upper[1]), (lower[0] + shift, lower[1])
    corners = [(top[0], top[1] + drop), (bottom[0], bottom[1] - (spread - drop))]
    return [top, *(corner for corner in corners if corner not in (top, bottom)), bottom]


def _arc(start: _Point, end: _Point, lane: _Lane) -> _Route:
    """Дуга над узлами между верхними кромками двух лиц одной строки: от узла вверх четвертью
    окружности, по прямой, четвертью окружности вниз к узлу; радиус равен подъёму. Дорожки пары —
    концентрические дуги: следующая шире на _LANE с каждой стороны и выше на _LANE, поэтому соседние
    дорожки везде идут ровно в _LANE друг от друга."""
    toward = 1 if end[0] >= start[0] else -1
    span = abs(end[0] - start[0])
    radius = round(_ARC_RISE + _ARC_SPAN_RISE * math.log2(max(span, _NODE_WIDTH) / _NODE_WIDTH)) + lane.index * _LANE
    y, top = start[1], start[1] - radius
    first, last = start[0] - toward * lane.offset, end[0] + toward * lane.offset
    rise_x, fall_x = first + toward * radius, last - toward * radius  # концы прямой над узлами
    turn = f"A{radius} {radius} 0 0 {1 if toward > 0 else 0}"
    path = f"M{_num(first)} {_num(y)} {turn} {_num(rise_x)} {_num(top)} L{_num(fall_x)} {_num(top)} {turn} {_num(last)} {_num(y)}"
    quarter = [
        (math.cos(angle), math.sin(angle))
        for angle in (math.pi / 2 * step / _CURVE_SAMPLES for step in range(_CURVE_SAMPLES + 1))
    ]
    points = [(rise_x - toward * radius * cos, y - radius * sin) for cos, sin in quarter]
    points += [(fall_x + toward * radius * cos, y - radius * sin) for cos, sin in reversed(quarter)]
    return path, points


def _channel_path(points: Sequence[_Point]) -> _Route:
    """Путь от узла через каналы к узлу: участки одного x — отрезки, переходы — кубические кривые в
    промежутке между строками. В канал кривая входит и выходит вертикально, а от узла уходит сразу к
    повороту, чтобы не идти вдоль другой связи из той же точки узла."""
    kept = [
        point
        for index, point in enumerate(points)
        if index in (0, len(points) - 1) or not points[index - 1][0] == point[0] == points[index + 1][0]
    ]
    path, samples = [f"M{_num(kept[0][0])} {_num(kept[0][1])}"], [kept[0]]
    last = len(kept) - 1
    for index, ((x1, y1), (x2, y2)) in enumerate(zip(kept, kept[1:])):
        if x1 == x2:
            path.append(f"L{_num(x2)} {_num(y2)}")
            samples.append((x2, y2))
            continue
        middle = (y1 + y2) / 2
        first = (x1, y1) if index == 0 else (x1, middle)
        second = (x2, y2) if index + 1 == last else (x2, middle)
        path.append(f"C{_num(first[0])} {_num(first[1])} {_num(second[0])} {_num(second[1])} {_num(x2)} {_num(y2)}")
        samples += _curve_points(((x1, y1), first, second, (x2, y2)))
    return " ".join(path), samples


def _curve_points(controls: Sequence[_Point]) -> list[_Point]:
    """Точки кривой Безье (по алгоритму де Кастельжо) после начальной, конечная включена."""
    points = []
    for step in range(1, _CURVE_SAMPLES + 1):
        t, level = step / _CURVE_SAMPLES, list(controls)
        while len(level) > 1:
            level = [((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in zip(level, level[1:])]
        points.append(level[0])
    return points


def _label_anchors(points: Sequence[_Point]) -> list[tuple[int, int]]:
    """Места подписи в порядке предпочтения: точки на долях _LABEL_STEPS длины пути (целые x и
    базовая линия; базовая линия ниже точки на 0,4 кегля, так строка встаёт на путь серединой)."""
    lengths = [0.0]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.hypot(x2 - x1, y2 - y1))
    anchors = []
    for fraction in _LABEL_STEPS:
        distance = fraction * lengths[-1]
        index = max(1, min(bisect.bisect_left(lengths, distance), len(points) - 1))
        span = lengths[index] - lengths[index - 1]
        t = (distance - lengths[index - 1]) / span if span else 0.0
        (x1, y1), (x2, y2) = points[index - 1], points[index]
        anchors.append((round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t + _LABEL_SIZE * 0.4)))
    return anchors


def _node_box(centre: tuple[float, float]) -> _Box:
    x, y = centre
    return (x - _NODE_WIDTH / 2, y - _NODE_HEIGHT / 2, x + _NODE_WIDTH / 2, y + _NODE_HEIGHT / 2)


def _svg_node(node: Node, layout: Layout) -> str:
    x, y = layout.positions[node.person_id]
    left, top, _, _ = _node_box((x, y))
    fill, dash = ("#eeeeee", ' stroke-dasharray="4 3"') if node.hidden else ("#ffffff", "")
    lines = [(node.label, 13, y - 4), (node.years, 11, y + 14)] if node.years else [(node.label, 13, y + 5)]
    texts = "".join(
        f'<text x="{_num(x)}" y="{_num(line_y)}" text-anchor="middle" font-size="{size}" fill="{_INK}">'
        f"{_attr(_fit(text, size))}</text>"
        for text, size, line_y in lines
    )
    # <title> — полный текст узла (подсказка при наведении), даже если строка в узле обрезана.
    full = " ".join(text for text in (node.label, node.years) if text)
    return (
        f'<g class="node" data-id="{_attr(node.person_id)}" tabindex="0"><title>{_attr(full)}</title>'
        f'<rect x="{_num(left)}" y="{_num(top)}" width="{_NODE_WIDTH}" height="{_NODE_HEIGHT}" rx="6" '
        f'fill="{fill}" stroke="{_INK}" stroke-width="1"{dash}/>{texts}</g>'
    )


def _fit(text: str, size: int) -> str:
    """Строка узла, оценённая по _CHAR_EM; не помещается между полями — обрезана и кончается «…»."""
    room = int((_NODE_WIDTH - 2 * _NODE_PADDING) / (size * _CHAR_EM))
    return text if len(text) <= room else text[: room - 1] + "…"


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _attr(text: str) -> str:
    """Текст или значение атрибута разметки со спецсимволами, экранированными для HTML и XML."""
    return html.escape(text, quote=True)


# --- HTML --------------------------------------------------------------------------------------


def render_html(graph: Graph, svg: str) -> str:
    """Страница без внешних ресурсов: SVG, панель сведений, легенда, данные панели в JSON."""
    canvas = svg if graph.nodes else f'<p class="empty">{EMPTY_NOTE}</p>'
    return _PAGE.format(
        **_header(graph),
        style=_STYLE,
        canvas=canvas,
        legend=_legend(),
        data=_tree_data(graph),
        script=_SCRIPT,
    )


def _header(graph: Graph) -> dict[str, str]:
    """Заголовок, режим и баннер — общие для интерактивной страницы и страницы печати."""
    return {"title": TITLE, "mode": "shareable" if graph.shareable else "private", "banner": _attr(banner(graph))}


def _legend() -> str:
    items = (
        f"<li>{_line_sample(_ENCODINGS[status])} <code>{status}</code> — {_ENCODINGS[status].meaning}</li>"
        for status in STATUSES
    )
    return "<ul>" + "".join(items) + f"</ul><p>{_ARROW_NOTE}</p>"


def _line_sample(encoding: _Encoding) -> str:
    """Образец линии статуса для легенды — самостоятельный SVG (годится и для <img>)."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="12" aria-hidden="true">'
        f'<line x1="2" y1="6" x2="46" y2="6"{_stroke_attrs(encoding)}/></svg>'
    )


def _tree_data(graph: Graph) -> str:
    """JSON для панели; <, > и & записаны JSON-escape, чтобы текст не закрыл <script>."""
    data = {
        "nodes": {node.person_id: _node_data(node) for node in graph.nodes},
        "edges": {edge.relationship_id: {"title": edge.label, "fields": edge.fields} for edge in graph.edges},
    }
    text = json.dumps(data, ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _node_data(node: Node) -> dict:
    data: dict = {"title": node.label, "fields": node.fields}
    if node.hidden:
        data["note"] = HIDDEN_NOTE
    return data


_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<header>
<h1>{title}</h1>
<p class="banner {mode}">{banner}</p>
</header>
<main>
<div id="canvas">{canvas}</div>
<aside id="panel"><p>Нажмите на лицо или связь, чтобы увидеть сведения. Колесо мыши — масштаб,
перетаскивание — сдвиг.</p></aside>
</main>
<section class="legend"><h2>Легенда</h2>{legend}
<button type="button" id="reset">Исходный вид</button></section>
<script type="application/json" id="tree-data">{data}</script>
<script>{script}</script>
</body>
</html>
"""

_BANNER_STYLE = """
.banner { margin: 0 0 12px; padding: 8px 12px; border-radius: 4px; font-weight: bold; }
.banner.private { background: #fdecea; color: #8e0000; }
.banner.shareable { background: #e8f5e9; color: #1b5e20; }
"""

_STYLE = (
    _BANNER_STYLE
    + """
body { margin: 0; padding: 16px; font-family: sans-serif; color: #222222; background: #ffffff; }
h1 { margin: 0 0 8px; font-size: 22px; }
main { display: flex; gap: 12px; align-items: stretch; }
#canvas { flex: 1 1 auto; min-width: 0; height: 72vh; border: 1px solid #cccccc; border-radius: 4px;
  overflow: hidden; touch-action: none; }
#canvas svg { width: 100%; height: 100%; cursor: grab; user-select: none; }
#canvas .empty { padding: 16px; }
g.node, g.edge { cursor: pointer; }
g.node:focus:not(:focus-visible), g.edge:focus:not(:focus-visible) { outline: none; }
g.node.selected rect { stroke: #1565c0; stroke-width: 3; }
g.edge.selected text { font-weight: bold; fill: #1565c0; }
#panel { flex: 0 0 300px; overflow: auto; height: 72vh; padding: 8px 12px; border: 1px solid #cccccc;
  border-radius: 4px; font-size: 14px; }
#panel h2 { font-size: 16px; margin: 4px 0 8px; }
#panel .note { font-style: italic; }
#panel dt { font-family: monospace; color: #555555; margin-top: 6px; }
#panel dd { margin: 0; overflow-wrap: anywhere; }
.legend ul { list-style: none; padding: 0; }
.legend li { margin: 4px 0; }
@media (max-width: 720px) { main { flex-direction: column; } #panel { flex-basis: auto; height: auto; } }
"""
)

# Щелчок по лицу или связи — панель из #tree-data; колесо — масштаб, перетаскивание — сдвиг
# (меняется viewBox). Без сети и библиотек.
_SCRIPT = """
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("tree-data").textContent);
  var panel = document.getElementById("panel");
  var svg = document.querySelector("#canvas svg");
  if (!svg) { return; }
  var initial = svg.getAttribute("viewBox");
  var view = initial.split(" ").map(Number);
  var drag = null;
  var dragged = false;
  var selected = null;

  function apply() { svg.setAttribute("viewBox", view.join(" ")); }

  function toSvg(clientX, clientY) {
    var point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }

  function add(parent, tag, text, className) {
    var element = document.createElement(tag);
    element.textContent = text;
    if (className) { element.className = className; }
    parent.appendChild(element);
    return element;
  }

  function show(group) {
    var kind = group.classList.contains("node") ? "nodes" : "edges";
    var item = data[kind][group.getAttribute("data-id")];
    if (!item) { return; }
    if (selected) { selected.classList.remove("selected"); }
    selected = group;
    group.classList.add("selected");
    panel.textContent = "";
    add(panel, "h2", item.title);
    if (item.note) { add(panel, "p", item.note, "note"); }
    var list = add(panel, "dl", "");
    item.fields.forEach(function (pair) {
      add(list, "dt", pair[0]);
      add(list, "dd", pair[1] || "—");
    });
  }

  function pick(target) {
    var group = target.closest ? target.closest("g.node, g.edge") : null;
    if (group) { show(group); }
  }

  svg.addEventListener("pointerdown", function (event) {
    drag = { x: event.clientX, y: event.clientY, view: view.slice(), scale: 1 / svg.getScreenCTM().a };
    dragged = false;
  });
  svg.addEventListener("pointermove", function (event) {
    if (!drag) { return; }
    if (event.buttons === 0) { drag = null; return; }  // кнопку отпустили вне окна
    var dx = event.clientX - drag.x;
    var dy = event.clientY - drag.y;
    if (!dragged && Math.abs(dx) + Math.abs(dy) < 4) { return; }
    if (!dragged) { svg.setPointerCapture(event.pointerId); }
    dragged = true;
    view[0] = drag.view[0] - dx * drag.scale;
    view[1] = drag.view[1] - dy * drag.scale;
    apply();
  });
  function stop() { drag = null; }
  svg.addEventListener("pointerup", stop);
  svg.addEventListener("pointercancel", stop);
  svg.addEventListener("click", function (event) {
    if (dragged) { dragged = false; return; }
    pick(event.target);
  });
  svg.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(event.target); }
  });
  svg.addEventListener("wheel", function (event) {
    if (event.deltaY === 0) { return; }  // горизонтальная прокрутка — не масштаб
    event.preventDefault();
    var point = toSvg(event.clientX, event.clientY);
    // Шаг 0,9 на 100 пикселей прокрутки (строка — 20 пикселей, страница — высота графа на экране),
    // не больше трёх шагов за событие.
    var unit = event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? svg.getBoundingClientRect().height : 1;
    var steps = Math.min(Math.abs(event.deltaY) * unit / 100, 3);
    var factor = Math.pow(0.9, event.deltaY < 0 ? steps : -steps);
    view[0] = point.x - (point.x - view[0]) * factor;
    view[1] = point.y - (point.y - view[1]) * factor;
    view[2] *= factor;
    view[3] *= factor;
    apply();
  }, { passive: false });
  document.getElementById("reset").addEventListener("click", function () {
    view = initial.split(" ").map(Number);
    apply();
  });
})();
"""


# --- Страница печати (PDF) -----------------------------------------------------------------------


def render_print_page(graph: Graph, svg: str) -> str:
    """Статичная страница для build_pdf.print_pdf: заголовок, баннер, граф и легенда на первой
    странице. Только теги из build_pdf.STATIC_TAGS; граф и образцы линий — SVG в <img> с адресом
    data:, поэтому текст реестров не попадает в разметку и не похож на незаполненный <…>."""
    if graph.nodes:
        canvas = f'<img class="graph" src="{_svg_data_uri(svg)}" alt="{TITLE}">'
    else:
        canvas = f"<p>{EMPTY_NOTE}</p>"
    return _PRINT_PAGE.format(
        **_header(graph),
        style=_PRINT_STYLE,
        canvas=canvas,
        legend=_print_legend(),
    )


def _print_legend() -> str:
    rows = (
        f'<tr><td><img src="{_svg_data_uri(_line_sample(_ENCODINGS[status]))}" alt=""></td>'
        f"<td><code>{status}</code></td><td>{_ENCODINGS[status].meaning}</td></tr>"
        for status in STATUSES
    )
    return "<table>" + "".join(rows) + f"</table><p>{_ARROW_NOTE}</p>"


def _svg_data_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


_PRINT_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<h1>{title}</h1>
<p class="banner {mode}">{banner}</p>
{canvas}
<section class="legend"><h2>Легенда</h2>{legend}</section>
</body>
</html>
"""

# Граф ужат так, чтобы заголовок, баннер, граф и легенда уместились на первом листе A4.
_PRINT_STYLE = (
    _BANNER_STYLE
    + """
@page { size: A4; margin: 12mm; }
body { margin: 0; font-family: sans-serif; color: #222222; background: #ffffff;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { margin: 0 0 6px; font-size: 20px; }
h2 { margin: 12px 0 4px; font-size: 15px; }
img.graph { display: block; max-width: 100%; max-height: 175mm; width: auto; height: auto;
  border: 1px solid #cccccc; }
.legend { break-inside: avoid; font-size: 12px; }
.legend table { border-collapse: collapse; }
.legend td { padding: 2px 8px 2px 0; vertical-align: middle; }
.legend p { margin: 4px 0 0; }
"""
)


# --- Mermaid -----------------------------------------------------------------------------------

# Сущности Mermaid (#name;) вместо символов, которые закрыли бы метку или начали сущность.
_MERMAID_ENTITIES = {
    "#": "#35;",
    '"': "#quot;",
    "<": "#lt;",
    ">": "#gt;",
    "|": "#124;",
    "&": "#38;",  # иначе «&lt;» из реестра Mermaid покажет как «<»
    "%": "#37;",  # иначе «%%» начнёт комментарий или директиву Mermaid
}


def render_mermaid(graph: Graph) -> str:
    """flowchart TB: узлы n1… по person_id, по строке на связь, цвет и штрих — linkStyle."""
    node_ids = {node.person_id: f"n{index}" for index, node in enumerate(graph.nodes, start=1)}
    lines = [f"%% {banner(graph)}", "flowchart TB"]
    for node in graph.nodes:
        label = _mermaid_text(node.label)
        if node.years:
            label += "<br/>" + _mermaid_text(node.years)
        lines.append(f'    {node_ids[node.person_id]}["{label}"]')
    link_styles = []
    for index, edge in enumerate(graph.edges):
        encoding = _ENCODINGS[edge.status]
        link = encoding.mermaid_directed if edge.directed else encoding.mermaid_undirected
        lines.append(f'    {node_ids[edge.a]} {link}|"{_mermaid_text(edge.label)}"| {node_ids[edge.b]}')
        style = f"stroke:{encoding.colour},stroke-width:{_num(encoding.width)}px"
        if encoding.dash:
            style += f",stroke-dasharray:{encoding.dash}"
        link_styles.append(f"    linkStyle {index} {style}")
    return "\n".join(lines + link_styles) + "\n"


def _mermaid_text(text: str) -> str:
    return "".join(_MERMAID_ENTITIES.get(char, char) for char in " ".join(text.split()))


# --- DOT ---------------------------------------------------------------------------------------


def render_dot(graph: Graph) -> str:
    """digraph: по строке на лицо и на связь; dir=none у связей без стрелки."""
    lines = [
        f"// {banner(graph)}",
        "digraph tree {",
        '    graph [rankdir=TB, fontname="sans-serif"];',
        '    node [shape=box, style=rounded, fontname="sans-serif"];',
        '    edge [fontname="sans-serif", fontsize=10];',
    ]
    for node in graph.nodes:
        label = node.label + ("\n" + node.years if node.years else "")
        lines.append(f'    "{_dot_text(node.person_id)}" [label="{_dot_text(label)}"];')
    for edge in graph.edges:
        encoding = _ENCODINGS[edge.status]
        attrs = [f'label="{_dot_text(edge.label)}"', f"style={encoding.dot_style}"]
        if encoding.dot_colour:
            attrs.append(f"color={encoding.dot_colour}")
        if not edge.directed:
            attrs.append("dir=none")
        lines.append(f'    "{_dot_text(edge.a)}" -> "{_dot_text(edge.b)}" [{", ".join(attrs)}];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _dot_text(text: str) -> str:
    """Строка DOT в кавычках: \\ и " экранированы, переводы строк — \\n."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return "\\n".join(escaped.splitlines())
