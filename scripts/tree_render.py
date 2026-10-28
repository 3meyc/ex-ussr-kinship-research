"""Отрисовка графа родства: SVG, интерактивная страница HTML, страница печати PDF, Mermaid и DOT.

Отрисовщики читают только Node и Edge из tree_model (сведения уже очищены по режиму) и
раскладку из tree_layout. Любой текст из реестров — данные: он экранируется в каждом формате.
"""

from __future__ import annotations

import base64
import html
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Tuple

from tree_model import STATUSES

if TYPE_CHECKING:
    from tree_layout import Layout
    from tree_model import Edge, Graph, Node

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
_ARROW_NOTE = "Стрелка идёт от person_a к person_b у parent-of, grandparent-of и godparent-of."

# Размер узла меньше шага сетки tree_layout. Центры узлов лежат на целочисленной сетке, поэтому
# все координаты ниже — целые числа (или с одной десятичной у толщины линий).
_NODE_WIDTH = 160
_NODE_HEIGHT = 48
_ARC_RISE = 48  # подъём контрольной точки дуги между лицами одной строки
_LABEL_SIZE = 10
_LABEL_CHAR_WIDTH = 6  # ширина знака подписи с запасом (0,6 em sans-serif)
_LABEL_GAP = 2  # зазор вокруг подписи
# Доли пути, на которых пробуется подпись: от середины к концам, не доходя до узлов и стрелки.
_LABEL_STEPS = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8)

_Box = Tuple[float, float, float, float]  # left, top, right, bottom


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
    """Связи по порядку; подпись каждой — в первой точке пути, свободной от узлов и прежних подписей."""
    node_boxes = [_node_box(layout.positions[node.person_id]) for node in graph.nodes]
    label_boxes: list[_Box] = []
    parts = []
    for edge in graph.edges:
        path, anchors = _edge_geometry(layout.positions[edge.a], layout.positions[edge.b])
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
    half = len(label) * _LABEL_CHAR_WIDTH / 2 + _LABEL_GAP
    return (x - half, baseline - _LABEL_SIZE - _LABEL_GAP, x + half, baseline + _LABEL_SIZE * 0.3 + _LABEL_GAP)


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


def _edge_geometry(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[str, list[tuple[int, int]]]:
    """Путь между границами узлов и места подписи на нём в порядке предпочтения (целые x и базовая
    линия). В одной строке — дуга над узлами, иначе — отрезок от нижней (верхней) кромки одного
    узла к верхней (нижней) кромке другого."""
    (x1, y1), (x2, y2) = start, end
    half = _NODE_HEIGHT / 2
    if y1 == y2:
        top, mid_x = y1 - half, (x1 + x2) / 2
        control = top - _ARC_RISE
        path = f"M{_num(x1)} {_num(top)} Q{_num(mid_x)} {_num(control)} {_num(x2)} {_num(top)}"
        points = [_quadratic((x1, top), (mid_x, control), (x2, top), t) for t in _LABEL_STEPS]
    else:
        sign = 1 if y2 > y1 else -1
        y1, y2 = y1 + sign * half, y2 - sign * half
        path = f"M{_num(x1)} {_num(y1)} L{_num(x2)} {_num(y2)}"
        points = [(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t) for t in _LABEL_STEPS]
    # Базовая линия ниже точки пути на 0,4 кегля: строка встаёт на линию серединой.
    return path, [(round(x), round(y + _LABEL_SIZE * 0.4)) for x, y in points]


def _quadratic(
    start: tuple[float, float], control: tuple[float, float], end: tuple[float, float], t: float
) -> tuple[float, float]:
    """Точка квадратичной кривой Безье при параметре t."""
    weights = ((1 - t) ** 2, 2 * t * (1 - t), t**2)
    return (
        sum(weight * point[0] for weight, point in zip(weights, (start, control, end))),
        sum(weight * point[1] for weight, point in zip(weights, (start, control, end))),
    )


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
        f"{_attr(text)}</text>"
        for text, size, line_y in lines
    )
    return (
        f'<g class="node" data-id="{_attr(node.person_id)}" tabindex="0">'
        f'<rect x="{_num(left)}" y="{_num(top)}" width="{_NODE_WIDTH}" height="{_NODE_HEIGHT}" rx="6" '
        f'fill="{fill}" stroke="{_INK}" stroke-width="1"{dash}/>{texts}</g>'
    )


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _attr(text: str) -> str:
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
    event.preventDefault();
    var point = toSvg(event.clientX, event.clientY);
    var factor = event.deltaY < 0 ? 0.9 : 1 / 0.9;
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
_MERMAID_ENTITIES = {"#": "#35;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "|": "#124;"}


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
