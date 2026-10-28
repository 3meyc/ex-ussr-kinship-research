"""Отрисовка графа родства: SVG, интерактивная страница HTML, страница печати PDF, Mermaid и DOT.

Отрисовщики читают только Node и Edge из tree_model (сведения уже очищены по режиму), а пути
связей, места подписей и размер холста берут у tree_geometry. Любой текст из реестров — данные: он
экранируется в каждом формате.
"""

from __future__ import annotations

import base64
import html
import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import accumulate
from typing import TYPE_CHECKING

from tree_geometry import (
    LABEL_SIZE,
    NODE_HEIGHT,
    NODE_RADIUS,
    NODE_WIDTH,
    char_em,
    node_box,
    route,
)
from tree_model import DIRECTED_TYPES, STATUSES

if TYPE_CHECKING:
    from tree_geometry import Command, Point
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
_ARROW_NOTE = f"Стрелка идёт от person_a к person_b у {', '.join(DIRECTED_TYPES[:-1])} и {DIRECTED_TYPES[-1]}."

_NODE_PADDING = 6  # поле слева и справа от текста в узле


def banner(graph: Graph) -> str:
    return BANNER_SHAREABLE if graph.shareable else BANNER_PRIVATE


# --- SVG ---------------------------------------------------------------------------------------


def render_svg(graph: Graph, layout: Layout) -> str:
    """Самодостаточный SVG: стили связей — атрибуты, стрелки — в собственных <defs>, поэтому
    он выглядит так же внутри <img>."""
    routes = route(graph, layout)
    width, height = _num(routes.width), _num(routes.height)
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" font-family="sans-serif" role="img" aria-label="{TITLE}">'
        ),
        _svg_markers(),
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<g class="edges">',
        *(
            _svg_edge(edge, _path_data(edge_route.commands), edge_route.label_anchor)
            for edge, edge_route in zip(graph.edges, routes.edges)
        ),
        "</g>",
        '<g class="nodes">',
        *(_svg_node(node, routes.positions[node.person_id]) for node in graph.nodes),
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


def _path_data(commands: Sequence[Command]) -> str:
    """Атрибут d пути из команд tree_geometry."""
    return " ".join(letter + " ".join(_num(value) for value in values) for letter, values in commands)


def _svg_edge(edge: Edge, path: str, anchor: Point) -> str:
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
        f'<text x="{label_x}" y="{label_y}" text-anchor="middle" font-size="{LABEL_SIZE}" fill="{_INK}" '
        f'stroke="#ffffff" stroke-width="3" paint-order="stroke">{_attr(edge.label)}</text></g>'
    )


def _stroke_attrs(encoding: _Encoding) -> str:
    """Цвет, толщина и штрих линии статуса — атрибуты SVG (их сохраняет и PDF)."""
    style = f' stroke="{encoding.colour}" stroke-width="{_num(encoding.width)}"'
    if encoding.dash:
        style += f' stroke-dasharray="{encoding.dash}"'
    return style


def _svg_node(node: Node, centre: Point) -> str:
    x, y = centre
    left, top, _, _ = node_box(centre)
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
        f'<rect x="{_num(left)}" y="{_num(top)}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}" rx="{NODE_RADIUS}" '
        f'fill="{fill}" stroke="{_INK}" stroke-width="1"{dash}/>{texts}</g>'
    )


def _fit(text: str, size: int) -> str:
    """Строка узла; не помещается между полями — обрезана и кончается «…» (ширина — char_em)."""
    room = (NODE_WIDTH - 2 * _NODE_PADDING) / size  # в долях кегля
    ends = list(accumulate(char_em(char) for char in text))
    if not ends or ends[-1] <= room:
        return text
    kept = sum(1 for end in ends if end <= room - char_em("…"))
    return text[:kept] + "…"


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
