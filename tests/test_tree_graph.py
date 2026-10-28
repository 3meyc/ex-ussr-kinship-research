"""Приёмочные тесты tree_graph.py: CLI на временном деле, созданном init_case.py.

Запуск: python3 -m unittest tests.test_tree_graph
"""

from __future__ import annotations

import base64
import csv
import html
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
ASSETS = ROOT / "assets"
sys.path.insert(0, str(SCRIPTS))

from build_pdf import find_chrome, inspect_html
from tree_layout import layout
from tree_model import build_graph
from tree_render import render_print_page, render_svg

CHROME = find_chrome(None)

# Paths relative to <case>/tree/: each mode writes to its own folder.
PRIVATE = ("private/tree.private.html", "private/tree.private.mmd", "private/tree.private.dot")
SHARED = ("share/tree.html", "share/tree.mmd", "share/tree.dot")
PRIVATE_PDF = "private/tree.private.pdf"
SHARED_PDF = "share/tree.pdf"
BANNER_PRIVATE = "Содержит сведения о живых лицах — не для передачи"
BANNER_SHARED = "Живые лица скрыты: показан только person_id"


def person(person_id: str, label: str, living: str, birth: str, death: str, **extra: str) -> dict:
    row = {"person_id": person_id, "preferred_label": label, "living_status": living,
           "birth_estimate": birth, "death_estimate": death, "places": "с. Липовка",
           "source_artifact_ids": "A-0001", "candidate_status": "confirmed"}
    row.update(extra)
    return row


def relation(rid: str, a: str, kind: str, b: str, status: str, rationale: str, **extra: str) -> dict:
    row = {"relationship_id": rid, "person_a_id": a, "relationship_type": kind, "person_b_id": b,
           "biological_legal_household": "biological", "status": status, "confidence": "moderate",
           "rationale": rationale}
    row.update(extra)
    return row


PERSONS = (
    person("P-0001", "Иван Петров", "deceased", "1850", "1920",
           relatives_as_reported="сосед-XYZ", notes="заметка-XYZ"),
    person("P-0002", "Мария Петрова", "probably-deceased", "1855", ""),
    person("P-0003", "Пётр Петров", "", "1880", "1941"),
    person("P-0004", "Анна Петрова", "living", "1950", ""),
    person("P-0005", "Ольга Петрова", "unknown", "ок. 1905–1912", ""),
    person("P-0006", "Сидор Кумов", "deceased", "1840", "1900"),
)
RELATIONSHIPS = (
    relation("R-0001", "P-0001", "parent-of", "P-0003", "strongly-corroborated", "метрика и ревизская сказка",
             claim_ids="C-0001", supporting_artifact_ids="A-0001;A-0002", independence_groups="G1;G2"),
    relation("R-0002", "P-0001", "spouse-of", "P-0002", "corroborated", "метрика и сказка",
             supporting_artifact_ids="A-0001;A-0002", independence_groups="G1;G2"),
    relation("R-0003", "P-0002", "parent-of", "P-0003", "single-source", "метрика",
             supporting_artifact_ids="A-0001", independence_groups="G1"),
    relation("R-0004", "P-0003", "parent-of", "P-0004", "hypothesis", "со слов Анны Петровой"),
    relation("R-0005", "P-0006", "godparent-of", "P-0003", "unresolved", "восприемник в метрике",
             supporting_artifact_ids="A-0001", independence_groups="G1"),
    relation("R-0006", "P-0005", "sibling-of", "P-0003", "contradicted", "семейное предание"),
    relation("R-0007", "P-0001", "grandparent-of", "P-0004", "", ""),
)
ARTIFACTS = (
    {"artifact_id": "A-0001", "source_title": "Метрическая книга 1880", "person_ids": "P-0001;P-0002;P-0003;P-0006",
     "directness": "direct", "originality": "image-of-original", "independence_group": "G1",
     "related_claim_ids": "C-0001", "status": "reviewed"},
    {"artifact_id": "A-0002", "source_title": "Ревизская сказка 1890", "person_ids": "P-0001;P-0003",
     "directness": "direct", "originality": "image-of-original", "independence_group": "G2",
     "related_claim_ids": "C-0001", "status": "reviewed"},
)
CLAIMS = (
    {"claim_id": "C-0001", "claim_text": "Иван Петров — отец Петра", "subject_person_ids": "P-0001;P-0003",
     "claim_type": "parentage", "artifact_ids": "A-0001;A-0002", "supporting_independence_groups": "G1;G2",
     "status": "corroborated", "confidence": "high", "rationale": "метрика и ревизская сказка"},
)

NAMES = {row["person_id"]: row["preferred_label"] for row in PERSONS}
ENDS = {row["relationship_id"]: (row["person_a_id"], row["person_b_id"]) for row in RELATIONSHIPS}
EDGE_LABELS = {row["relationship_id"]: f"{row['relationship_type']} · {row['relationship_id']}" for row in RELATIONSHIPS}
EDGE_STATUS = {"R-0001": "strongly-corroborated", "R-0002": "corroborated", "R-0003": "single-source",
               "R-0004": "hypothesis", "R-0005": "unresolved", "R-0006": "contradicted", "R-0007": "unresolved"}
DIRECTED = {"R-0001", "R-0003", "R-0004", "R-0005", "R-0007"}
GREY = "#888888"
RED = "#c62828"

DOT_ID = r'(?:"(?:[^"\\]|\\.)*"|[\w.]+)'
DOT_EDGE = re.compile(rf"^\s*({DOT_ID})\s*->\s*({DOT_ID})\s*\[(.*)\]\s*;?\s*$", re.MULTILINE)
DOT_NODE = re.compile(rf"^\s*({DOT_ID})\s*\[(.*)\]\s*;?\s*$", re.MULTILINE)
DOT_ATTR = re.compile(r'([\w-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^,;\s\]]+)')
UNKNOWN_PERSON = {"R-0004": {"person_b_id": "P-0099"}}
MERMAID_NODE = re.compile(r'\b(n\d+)\s*[\[({]+\s*"([^"]*)"')


class SvgEdges(HTMLParser):
    """Collects edge groups (attributes of the group merged with its first path), node boxes, edge
    label texts and the panel data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.edges: dict = {}
        self.node_ids: list = []
        self.node_boxes: list = []  # (left, top, right, bottom) of each node <rect>
        self.box_of: dict = {}  # person_id -> the box of that node's first <rect>
        self.labels: list = []  # [attributes, text] of each edge <text>
        self.tree_data = None
        self._groups: list = []
        self._node_groups: list = []
        self._with_path: set = set()
        self._in_tree_data = False
        self._label = None
        self._node_id = None

    def handle_starttag(self, tag, attrs):
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "").split()
        if tag == "script" and values.get("id") == "tree-data" and values.get("type") == "application/json":
            self._in_tree_data = True
            self.tree_data = ""
        elif tag == "g":
            if "node" in classes:
                self.node_ids.append(values.get("data-id"))
                self._node_id = values.get("data-id")
            edge_id = values.get("data-id") if "edge" in classes else None
            if edge_id:
                self.edges[edge_id] = values
            self._groups.append(edge_id)
            self._node_groups.append("node" in classes)
        elif tag == "rect" and self._node_groups and self._node_groups[-1]:
            left, top = float(values["x"]), float(values["y"])
            self.node_boxes.append((left, top, left + float(values["width"]), top + float(values["height"])))
            self.box_of.setdefault(self._node_id, self.node_boxes[-1])
        elif tag == "text" and self._groups and self._groups[-1]:
            self._label = [values, ""]
            self.labels.append(self._label)
        elif tag == "path":
            edge_id = next((group for group in reversed(self._groups) if group), None)
            if edge_id and edge_id not in self._with_path:
                self._with_path.add(edge_id)
                self.edges[edge_id] = {**self.edges[edge_id], **values}

    def handle_endtag(self, tag):
        if tag == "g" and self._groups:
            self._groups.pop()
            self._node_groups.pop()
        elif tag == "script":
            self._in_tree_data = False
        elif tag == "text":
            self._label = None

    def handle_data(self, data):
        if self._in_tree_data:
            self.tree_data += data
        elif self._label is not None:
            self._label[1] += data


def parse_page(source: str) -> SvgEdges:
    parser = SvgEdges()
    parser.feed(source)
    parser.close()
    return parser


def page_text(source: str) -> str:
    """Page markup with entities decoded plus the panel data with JSON escapes decoded."""
    data = json.loads(parse_page(source).tree_data)
    return html.unescape(source) + "\n" + json.dumps(data, ensure_ascii=False)


def label_box(attrs: dict, text: str) -> tuple:
    """Generous box of a centred label: 0.6 em per character, from one em above the baseline to
    0.3 em below it."""
    x, y, size = float(attrs["x"]), float(attrs["y"]), float(attrs["font-size"])
    half = len(text) * 0.6 * size / 2
    return (x - half, y - size, x + half, y + 0.3 * size)


def boxes_overlap(first: tuple, second: tuple) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def has_year(text: str, year: str) -> bool:
    return re.search(rf"(?<!\d){year}(?!\d)", text) is not None


def unquote(value: str) -> str:
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def dot_attrs(text: str) -> dict:
    return {name: unquote(value) for name, value in DOT_ATTR.findall(text)}


def dot_nodes(dot: str) -> dict:
    nodes = {}
    for match in DOT_NODE.finditer(dot):
        node_id, attrs = unquote(match.group(1)), match.group(2)
        if "->" not in match.group(0) and node_id not in ("node", "edge", "graph"):
            nodes[node_id] = dot_attrs(attrs).get("label", "")
    return nodes


def dot_edges(dot: str) -> dict:
    edges = {}
    for match in DOT_EDGE.finditer(dot):
        attrs = dot_attrs(match.group(3))
        rid = re.search(r"R-\d{4}", attrs.get("label", ""))
        if rid:
            edges.setdefault(rid.group(), []).append((unquote(match.group(1)), unquote(match.group(2)), attrs))
    return edges


def mermaid_edges(mmd: str) -> dict:
    """Relationship id -> list of (first node, last node, has arrow, line) for each link line."""
    edges = {}
    for line in mmd.splitlines():
        rid = re.search(r"·\s*(R-\d{4})", line)
        if not rid or line.lstrip().startswith("%%"):
            continue
        bare = re.sub(r'"[^"]*"', "", line)
        ends = re.findall(r"\bn\d+\b", bare)
        edges.setdefault(rid.group(1), []).append((ends[0], ends[-1], ">" in bare, line))
    return edges


def mermaid_text(label: str) -> str:
    """Text Mermaid shows for a quoted label: entity codes (`#quot;`, `#35;`) become HTML entities,
    then the label is rendered as HTML."""
    label = re.sub(r"#(\d+);", r"&#\1;", label)
    return html.unescape(re.sub(r"#([A-Za-z]\w*);", r"&\1;", label))


def panel_entries(data, item_id: str) -> list:
    """Smallest parts of the panel data that belong to `item_id`: the value under a key equal to the
    id, else the innermost objects holding the id as a value."""
    found: list = []

    def visit(value) -> bool:
        if isinstance(value, str):
            return value == item_id
        if isinstance(value, dict) and item_id in value:
            found.append(value[item_id])
            return True
        if not isinstance(value, (dict, list)):
            return False
        before = len(found)
        contains = False
        for child in value.values() if isinstance(value, dict) else value:
            contains = visit(child) or contains  # visit every child, not only up to the first hit
        if isinstance(value, dict) and contains and len(found) == before:
            found.append(value)
        return contains

    visit(data)
    return found


PATH_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
PATH_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "Q": 4, "C": 6, "A": 7, "Z": 0}


def bezier(points: tuple, t: float) -> tuple:
    while len(points) > 1:
        points = tuple(((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in zip(points, points[1:]))
    return points[0]


def arc(start: tuple, args: list, end: tuple, steps: int) -> list:
    """Points of an SVG elliptical arc (endpoint to centre conversion, SVG 1.1 F.6.5)."""
    rx, ry, phi, large, sweep = abs(args[0]), abs(args[1]), math.radians(args[2]), args[3] != 0, args[4] != 0
    if rx == 0 or ry == 0:
        return [bezier((start, end), k / steps) for k in range(1, steps + 1)]
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (start[0] - end[0]) / 2, (start[1] - end[1]) / 2
    x1, y1 = cos * dx + sin * dy, -sin * dx + cos * dy
    scale = max(1.0, math.sqrt(x1 ** 2 / rx ** 2 + y1 ** 2 / ry ** 2))
    rx, ry = rx * scale, ry * scale
    numerator = rx ** 2 * ry ** 2 - rx ** 2 * y1 ** 2 - ry ** 2 * x1 ** 2
    coef = math.sqrt(max(0.0, numerator / (rx ** 2 * y1 ** 2 + ry ** 2 * x1 ** 2)))
    coef = -coef if large == sweep else coef
    cx1, cy1 = coef * rx * y1 / ry, -coef * ry * x1 / rx
    cx = cos * cx1 - sin * cy1 + (start[0] + end[0]) / 2
    cy = sin * cx1 + cos * cy1 + (start[1] + end[1]) / 2
    first = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    sweep_angle = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx) - first
    if sweep and sweep_angle < 0:
        sweep_angle += 2 * math.pi
    elif not sweep and sweep_angle > 0:
        sweep_angle -= 2 * math.pi
    points = []
    for k in range(1, steps + 1):
        angle = first + sweep_angle * k / steps
        points.append((cx + rx * math.cos(angle) * cos - ry * math.sin(angle) * sin,
                       cy + rx * math.cos(angle) * sin + ry * math.sin(angle) * cos))
    return points


def path_points(d: str, steps: int = 64) -> list:
    """Points along an SVG path `d` (M, L, H, V, Q, C, A, Z, absolute and relative)."""
    tokens = PATH_TOKEN.findall(d)
    points: list = []
    current = start = (0.0, 0.0)
    command, index = "M", 0
    while index < len(tokens):
        if tokens[index].isalpha():
            command, index = tokens[index], index + 1
        kind, relative = command.upper(), command.islower()
        if kind not in PATH_ARITY:
            raise ValueError(f"unsupported path command {command!r} in {d!r}")
        args = [float(value) for value in tokens[index:index + PATH_ARITY[kind]]]
        index += PATH_ARITY[kind]
        base = current if relative else (0.0, 0.0)
        pairs = [(args[i] + base[0], args[i + 1] + base[1]) for i in range(0, len(args) - 1, 2)]
        if kind == "Z":
            end, controls = start, ()
        elif kind == "H":
            end, controls = (args[0] + base[0], current[1]), ()
        elif kind == "V":
            end, controls = (current[0], args[0] + base[1]), ()
        elif kind == "A":
            end = (args[5] + base[0], args[6] + base[1])
            points.extend(arc(current, args, end, steps))
            current = end
            continue
        else:
            end, controls = pairs[-1], tuple(pairs[:-1])
        if kind == "M":
            points.append(end)
            current = start = end
            command = "l" if relative else "L"  # further pairs after a moveto are linetos
            continue
        points.extend(bezier((current, *controls, end), k / steps) for k in range(1, steps + 1))
        current = end
    return points


def distance_to_polyline(point: tuple, polyline: list) -> float:
    """Shortest distance from `point` to the segments joining the samples of a path."""
    best = math.inf
    for (ax, ay), (bx, by) in zip(polyline, polyline[1:] or polyline):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length))
        best = min(best, math.hypot(point[0] - ax - t * dx, point[1] - ay - t * dy))
    return best


def svg_canvas(svg: ET.Element) -> tuple:
    """(left, top, right, bottom) of the SVG canvas in user units: the viewBox, else width and height."""
    view_box = svg.get("viewBox")
    if view_box:
        left, top, width, height = (float(value) for value in re.split(r"[\s,]+", view_box.strip()))
    else:
        left, top = 0.0, 0.0
        width, height = (float(re.match(r"[\d.]+", svg.get(name)).group()) for name in ("width", "height"))
    return (left, top, left + width, top + height)


# C0 control characters other than the tab and line breaks of file structure, and U+FFFF.
FORBIDDEN_CHAR = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￿]")


def forbidden_characters(text: str, *, mermaid: bool = False) -> list:
    """Forbidden characters in `text`, written raw or as numeric references (`&#1;`, `&#xFFFF;`, and
    Mermaid's `#1;` when `mermaid`)."""
    found = FORBIDDEN_CHAR.findall(text)
    references = [(match.group(1), 16) for match in re.finditer(r"&#[xX]([0-9a-fA-F]+);", text)]
    references += [(match.group(1), 10) for match in re.finditer(r"&#(\d+);", text)]
    if mermaid:
        references += [(match.group(1), 10) for match in re.finditer(r"(?<![&\w])#(\d+);", text)]
    for digits, base in references:
        code = int(digits, base)
        if code <= 0x10FFFF and FORBIDDEN_CHAR.match(chr(code)):
            found.append(chr(code))
    return found


def ids_by_person(labels: dict) -> dict:
    """Map person_id -> graph node id by the preferred_label inside each node label."""
    return {pid: node_id for node_id, label in labels.items() for pid, name in NAMES.items() if name in label}


class CaseTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name) / "case"
        self.tree = self.case / "tree"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "init_case.py"), "--case-dir", str(self.case),
             "--research-question", "q"],
            check=True, capture_output=True,
        )

    def write_fixture(self, persons: dict | None = None, relationships: dict | None = None, *,
                      extra_persons: tuple = (), extra_relationships: tuple = ()) -> None:
        """Write the registers; `persons`/`relationships` map a row id to field overrides, the extra
        rows follow the fixture's."""
        self.write_register("person-register.csv", PERSONS + extra_persons, "person_id", persons or {})
        self.write_register("relationship-register.csv", RELATIONSHIPS + extra_relationships, "relationship_id",
                            relationships or {})
        self.write_register("artifact-register.csv", ARTIFACTS, "artifact_id", {})
        self.write_register("claims-register.csv", CLAIMS, "claim_id", {})

    def write_register(self, name: str, rows: tuple, key: str, changes: dict) -> None:
        with (ASSETS / name).open(encoding="utf-8", newline="") as handle:
            fields = next(csv.reader(handle))
        with (self.case / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({**row, **changes.get(row[key], {})} for row in rows)

    def run_tree(self, *args: str, env: dict | None = None, case: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "tree_graph.py"), "--case-dir", str(case or self.case), *args],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=180,
            check=False,  # tests assert the exit code
        )

    def assert_exit(self, proc: subprocess.CompletedProcess, code: int) -> None:
        self.assertEqual(proc.returncode, code, msg=f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    def build(self, *args: str) -> subprocess.CompletedProcess:
        proc = self.run_tree("--no-pdf", *args)
        self.assert_exit(proc, 0)
        return proc

    def snapshot(self) -> dict:
        """Every file under tree/, keyed by its path relative to tree/."""
        return {path.relative_to(self.tree).as_posix(): path.read_bytes()
                for path in sorted(self.tree.rglob("*")) if path.is_file()}

    def folders(self) -> set:
        return {path.relative_to(self.tree).as_posix() for path in self.tree.rglob("*") if path.is_dir()}

    def read(self, name: str) -> str:
        path = self.tree / name
        self.assertTrue(path.is_file(), f"tree/{name} not written")
        return path.read_text(encoding="utf-8")

    def assert_counts(self, stdout: str, persons: int, relations: int, hidden: int) -> None:
        for name, number in (("лица", persons), ("связи", relations), ("скрыто живых", hidden)):
            self.assertRegex(stdout, rf"{name}\W*{number}(?!\d)")


class OutputsPerMode(CaseTestBase):
    def test_no_pdf_writes_three_files(self):
        self.write_fixture()
        proc = self.build()
        self.assertEqual(set(self.snapshot()), set(PRIVATE))
        for name in PRIVATE:
            self.assertIn(str(Path("tree", name)), proc.stdout)
        self.assert_counts(proc.stdout, 6, 7, 0)

        proc = self.build("--exclude-living")
        self.assertEqual(set(self.snapshot()), set(PRIVATE) | set(SHARED))
        for name in SHARED:
            self.assertIn(str(Path("tree", name)), proc.stdout)
        self.assert_counts(proc.stdout, 6, 7, 1)

    def test_output_is_deterministic(self):
        self.write_fixture()
        self.build()
        first = self.snapshot()
        self.build()
        self.assertEqual(self.snapshot(), first)

    def test_empty_registers_build(self):
        self.build()
        self.assertEqual(set(self.snapshot()), set(PRIVATE))
        self.assertIn("В реестре нет лиц", html.unescape(self.read("private/tree.private.html")))

    def test_rows_without_id_are_left_out(self):
        unnamed = ("Безымянный Первый", "Безымянная Вторая")
        self.write_fixture(extra_persons=(person("", unnamed[0], "deceased", "1801", "1870"),
                                          person("", unnamed[1], "deceased", "1802", "1871")))
        proc = self.build()
        self.assertRegex(proc.stdout.replace(str(self.case), ""), r"(?<![\w.])2(?![\w.])")
        page = self.read("private/tree.private.html")
        self.assertEqual(sorted(parse_page(page).node_ids), sorted(NAMES))
        mmd, dot = self.read("private/tree.private.mmd"), self.read("private/tree.private.dot")
        self.assertEqual(len(MERMAID_NODE.findall(mmd)), 6)
        self.assertEqual(len(dot_nodes(dot)), 6)
        for name, text in (("html", page_text(page)), ("mmd", mmd), ("dot", dot)):
            for label in unnamed:
                with self.subTest(name, label=label):
                    self.assertNotIn(label, text)


class AuditGate(CaseTestBase):
    def test_audit_error_writes_nothing(self):
        with self.subTest("no earlier build"):
            self.write_fixture(relationships=UNKNOWN_PERSON)
            proc = self.run_tree("--no-pdf")
            self.assert_exit(proc, 1)
            self.assertIn("P-0099", proc.stderr)
            self.assertIn("audit_evidence.py", proc.stderr)
            self.assertFalse(self.tree.exists())

        with self.subTest("earlier build kept"):
            self.write_fixture()
            self.build()
            self.build("--exclude-living")
            earlier = self.snapshot()
            self.write_fixture(relationships=UNKNOWN_PERSON)
            for args in ((), ("--exclude-living",)):
                self.assert_exit(self.run_tree("--no-pdf", *args), 1)
            self.assertEqual(self.snapshot(), earlier)

    def test_audit_warning_builds_with_unknown_type_undirected(self):
        self.write_fixture(relationships={"R-0006": {"relationship_type": "cousin-of"}})
        audit = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit_evidence.py"), "--case-dir", str(self.case), "--json"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        report = json.loads(audit.stdout)
        self.assertEqual(report["errors"], [])
        warnings = len(report["warnings"])
        self.assertGreaterEqual(warnings, 1)

        proc = self.build()
        self.assertRegex(proc.stdout, rf"(?<![\w.]){warnings}(?![\w.])")
        source = self.read("private/tree.private.html")
        self.assertIn("cousin-of · R-0006", html.unescape(source))
        edge = parse_page(source).edges["R-0006"]
        self.assertIn(edge.get("marker-end", "none").strip(), ("", "none"))

    def test_missing_case_dir_exit_2(self):
        missing = Path(self.tmp.name) / "no-such-case"
        with self.subTest("does not exist"):
            proc = self.run_tree(case=missing)
            self.assert_exit(proc, 2)
            self.assertIn(str(missing), proc.stderr)
            self.assertFalse(missing.exists())
        not_folder = Path(self.tmp.name) / "case.txt"
        not_folder.write_text("x", encoding="utf-8")
        with self.subTest("not a folder"):
            proc = self.run_tree(case=not_folder)
            self.assert_exit(proc, 2)
            self.assertIn(str(not_folder), proc.stderr)
            self.assertEqual(not_folder.read_text(encoding="utf-8"), "x")


class Privacy(CaseTestBase):
    def outputs(self, names: tuple) -> dict:
        page, mmd, dot = names
        return {page: page_text(self.read(page)), mmd: self.read(mmd), dot: self.read(dot)}

    def test_default_shows_living(self):
        self.write_fixture()
        self.build()
        texts = self.outputs(PRIVATE)
        for name, text in texts.items():
            with self.subTest(name):
                self.assertIn("Анна Петрова", text)
                self.assertTrue(has_year(text, "1950"))
                self.assertIn(BANNER_PRIVATE, text)
        self.assertIn("со слов Анны Петровой", texts["private/tree.private.html"])

    def test_shareable_hides_living_everywhere(self):
        self.write_fixture()
        self.build("--exclude-living")
        texts = self.outputs(SHARED)
        for name, text in texts.items():
            with self.subTest(name):
                self.assertNotIn("Анна", text)
                self.assertFalse(has_year(text, "1950"))
                self.assertNotIn("со слов", text)
                self.assertIn("P-0004", text)
                self.assertIn("Иван Петров", text)
                self.assertTrue(has_year(text, "1850"))
                self.assertIn(BANNER_SHARED, text)
                self.assertNotIn(BANNER_PRIVATE, text)
                self.assertIn(EDGE_LABELS["R-0004"], text)
                self.assertIn(EDGE_LABELS["R-0007"], text)
        self.assertIn("сведения скрыты", texts["share/tree.html"])
        self.assertLessEqual({"R-0004", "R-0007"}, set(parse_page(self.read("share/tree.html")).edges))

    def test_hidden_edges_drop_relationship_details(self):
        self.write_fixture(relationships={"R-0004": {"biological_legal_household": "adoptive", "confidence": "low"}})
        self.build("--exclude-living")
        data = json.loads(parse_page(self.read("share/tree.html")).tree_data)
        hidden = json.dumps(panel_entries(data, "R-0004"), ensure_ascii=False)
        shown = json.dumps(panel_entries(data, "R-0001"), ensure_ascii=False)
        self.assertIn("R-0004", hidden)
        for value in ("adoptive", "low"):
            self.assertIsNone(re.search(rf"\b{value}\b", hidden), value)
        for value in ("biological", "moderate"):
            self.assertRegex(shown, rf"\b{value}\b")

    def test_unlisted_fields_never_appear(self):
        self.write_fixture()
        for args, names in (((), PRIVATE), (("--exclude-living",), SHARED)):
            self.build(*args)
            for name, text in self.outputs(names).items():
                with self.subTest(name):
                    for secret in ("заметка-XYZ", "сосед-XYZ", "Метрическая книга 1880"):
                        self.assertNotIn(secret, text)


class EdgeEncoding(CaseTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.write_fixture()
        self.build()

    def test_edge_status_and_direction_in_svg(self):
        edges = parse_page(self.read("private/tree.private.html")).edges
        self.assertEqual(set(edges), set(EDGE_STATUS))
        for rid, status in EDGE_STATUS.items():
            with self.subTest(rid):
                edge = edges[rid]
                self.assertEqual(edge.get("data-status"), status)
                has_marker = edge.get("marker-end", "none").strip() not in ("", "none")
                self.assertEqual(has_marker, rid in DIRECTED)
                dash = edge.get("stroke-dasharray", "none").strip()
                if status in ("single-source", "hypothesis"):
                    self.assertNotIn(dash, ("", "none"))
                else:
                    self.assertIn(dash, ("", "none"))
                stroke = edge.get("stroke", "").lower()
                expected_stroke = {"unresolved": GREY, "contradicted": RED}.get(status)
                if expected_stroke:
                    self.assertEqual(stroke, expected_stroke)
                else:
                    self.assertNotIn(stroke, ("", GREY, RED))

        def width(rid: str) -> float:
            return float(re.match(r"[\d.]+", edges[rid]["stroke-width"]).group())

        self.assertAlmostEqual(width("R-0001"), 3 * width("R-0002"))
        self.assertNotEqual(edges["R-0003"]["stroke-dasharray"], edges["R-0004"]["stroke-dasharray"])

        page = parse_page(self.read("private/tree.private.html"))
        self.assertEqual(sorted(text for _, text in page.labels), sorted(EDGE_LABELS.values()))
        self.assertEqual(len(page.node_boxes), 6)
        covered = [(text, node) for attrs, text in page.labels for node in page.node_boxes
                   if boxes_overlap(label_box(attrs, text), node)]
        self.assertEqual(covered, [], "edge labels under node boxes")

    def test_mermaid_and_dot_have_every_node_and_edge(self):
        mmd, dot = self.read("private/tree.private.mmd"), self.read("private/tree.private.dot")
        mermaid_labels = dict(MERMAID_NODE.findall(mmd))
        self.assertEqual(len(MERMAID_NODE.findall(mmd)), 6)
        dot_labels = dot_nodes(dot)
        self.assertEqual(len(dot_labels), 6)
        node_of = {"mmd": ids_by_person(mermaid_labels), "dot": ids_by_person(dot_labels)}
        for fmt, mapping in node_of.items():
            self.assertEqual(set(mapping), set(NAMES), msg=fmt)

        links, statements = mermaid_edges(mmd), dot_edges(dot)
        self.assertEqual(set(links), set(EDGE_LABELS))
        self.assertEqual(set(statements), set(EDGE_LABELS))
        dot_style = {"strongly-corroborated": ("bold", None), "corroborated": ("solid", None),
                     "single-source": ("dashed", None), "hypothesis": ("dotted", None),
                     "unresolved": ("solid", "grey"), "contradicted": ("solid", "red")}
        for rid, label in EDGE_LABELS.items():
            a, b = ENDS[rid]
            with self.subTest(rid, fmt="mmd"):
                self.assertEqual(len(links[rid]), 1)
                first, last, arrow, line = links[rid][0]
                self.assertIn(label, line)
                self.assertEqual(arrow, rid in DIRECTED)
                if arrow:
                    self.assertEqual((first, last), (node_of["mmd"][a], node_of["mmd"][b]))
            with self.subTest(rid, fmt="dot"):
                self.assertEqual(len(statements[rid]), 1)
                source, target, attrs = statements[rid][0]
                self.assertEqual(attrs["label"], label)
                style, color = dot_style[EDGE_STATUS[rid]]
                self.assertEqual(attrs.get("style", "solid"), style)
                if color:
                    self.assertEqual(attrs.get("color"), color)
                else:
                    self.assertNotIn(attrs.get("color"), ("grey", "red"))
                if rid in DIRECTED:
                    self.assertNotEqual(attrs.get("dir"), "none")
                    self.assertEqual((source, target), (node_of["dot"][a], node_of["dot"][b]))
                else:
                    self.assertEqual(attrs.get("dir"), "none")

    def test_overlapping_edges_are_drawn_apart(self):
        self.write_fixture(extra_relationships=(
            relation("R-0008", "P-0006", "godparent-of", "P-0003", "contradicted", "другая метрика"),))
        self.build()
        edges = parse_page(self.read("private/tree.private.html")).edges
        with self.subTest("parallel edges"):
            self.assert_lanes_apart(edges, "R-0008", "R-0005")
        with self.subTest("long edge over a node"):
            left, top, right, bottom = parse_page(self.read("private/tree.private.html")).box_of["P-0003"]
            points = path_points(edges["R-0007"]["d"])
            self.assertTrue(points)
            inside = [point for point in points if left < point[0] < right and top < point[1] < bottom]
            self.assertEqual(inside[:3], [], f"R-0007 ({edges['R-0007']['d']}) crosses P-0003's box")

    def assert_lanes_apart(self, edges: dict, rid: str, other: str) -> None:
        """Every sample of `rid` beyond 10 px of its ends lies at least 3 px from the path of `other`."""
        first, second = path_points(edges[rid]["d"]), path_points(edges[other]["d"])
        self.assertTrue(first)
        self.assertTrue(second)
        ends = (first[0], first[-1])
        middle = [point for point in first if all(math.dist(point, end) > 10 for end in ends)]
        self.assertTrue(middle, f"{rid} ({edges[rid]['d']}) has no part beyond 10 px of its ends")
        closest = min(distance_to_polyline(point, second) for point in middle)
        self.assertGreaterEqual(closest, 3, f"{rid} ({edges[rid]['d']}) runs {closest:.2f} px from "
                                            f"{other} ({edges[other]['d']})")

    def test_lanes_stay_apart(self):
        self.write_fixture(extra_relationships=(
            relation("R-0008", "P-0001", "other", "P-0002", "hypothesis", "свидетели на свадьбе"),
            relation("R-0009", "P-0002", "godparent-of", "P-0003", "hypothesis", "восприемница в метрике")))
        self.build()
        page = parse_page(self.read("private/tree.private.html"))
        boxes = page.box_of
        with self.subTest("layout"):
            self.assertEqual(boxes["P-0001"][1], boxes["P-0002"][1], "P-0001 and P-0002 are not in one row")
            self.assertGreater(boxes["P-0003"][1], boxes["P-0002"][3], "P-0003 is not in a row below P-0002")
            self.assertNotEqual(boxes["P-0002"][0], boxes["P-0003"][0], "P-0002 and P-0003 share an x")
        for rid, other in (("R-0008", "R-0002"), ("R-0009", "R-0003")):
            with self.subTest(f"{rid} beside {other}"):
                self.assert_lanes_apart(page.edges, rid, other)

    def test_edge_labels_stay_on_canvas(self):
        column = tuple(row for row in PERSONS if row["person_id"] in ("P-0001", "P-0002", "P-0003"))
        chain = (relation("R-0001", "P-0001", "parent-of", "P-0002", "corroborated", "метрика"),
                 relation("R-0002", "P-0002", "parent-of", "P-0003", "corroborated", "метрика"),
                 relation("R-0003", "P-0001", "grandparent-of", "P-0003", "corroborated", "метрика"))
        self.write_register("person-register.csv", column, "person_id", {})
        self.write_register("relationship-register.csv", chain, "relationship_id", {})
        self.write_register("artifact-register.csv", ARTIFACTS, "artifact_id",
                            {"A-0001": {"person_ids": "P-0001;P-0002;P-0003"}})
        self.build()
        source = self.read("private/tree.private.html")
        graph = next(svg for svg in re.findall(r"<svg\b.*?</svg>", source, re.DOTALL | re.IGNORECASE)
                     if 'data-id="R-0003"' in svg)
        left, top, right, bottom = svg_canvas(ET.fromstring(graph))
        labels = parse_page(graph).labels
        self.assertEqual(sorted(text for _, text in labels),
                         ["grandparent-of · R-0003", "parent-of · R-0001", "parent-of · R-0002"])
        for attrs, text in labels:
            with self.subTest(text):
                box = label_box(attrs, text)
                self.assertTrue(left <= box[0] and top <= box[1] and box[2] <= right and box[3] <= bottom,
                                f"label box {box} outside canvas {(left, top, right, bottom)}")


class InteractivePage(CaseTestBase):
    def test_page_has_no_network_references(self):
        self.write_fixture()
        self.build()
        source = self.read("private/tree.private.html")
        for pattern in (r"<link\b", r"\b(?:src|href)\s*=\s*[\"']?\s*https?:",
                        r"url\(\s*[\"']?\s*https?:", r"@import[^;]*https?:"):
            with self.subTest(pattern):
                self.assertIsNone(re.search(pattern, source, re.IGNORECASE))

    def test_register_text_cannot_inject_markup(self):
        payload = 'Иван "</script><script>alert(1)</script>'
        self.write_fixture(persons={"P-0001": {"preferred_label": payload}})
        self.build()
        source = self.read("private/tree.private.html")
        self.assertEqual(len(re.findall(r"<script\b", source, re.IGNORECASE)), 2)
        self.assertIn(payload, page_text(source))
        mmd, dot = self.read("private/tree.private.mmd"), self.read("private/tree.private.dot")
        self.assertIn("Иван #quot;", mmd)
        self.assertNotIn('Иван "', mmd)
        self.assertEqual(len(MERMAID_NODE.findall(mmd)), 6)
        self.assertIn('Иван \\"', dot)
        self.assertEqual(len(dot_nodes(dot)), 6)
        self.assertEqual(set(dot_edges(dot)), set(EDGE_LABELS))

    def test_control_characters_keep_svg_well_formed(self):
        self.write_fixture(persons={"P-0001": {"preferred_label": "Иван\x0bПетров"},
                                    "P-0002": {"preferred_label": "Мария &lt; Петрова"},
                                    "P-0003": {"preferred_label": "Пётр￿Петров"},
                                    "P-0005": {"preferred_label": "Ольга\x01Петрова"}})
        self.build()
        source = self.read("private/tree.private.html")
        data = json.dumps(json.loads(parse_page(source).tree_data), ensure_ascii=False)
        outputs = {"html": forbidden_characters(source + "\n" + data),
                   "mmd": forbidden_characters(self.read("private/tree.private.mmd"), mermaid=True),
                   "dot": forbidden_characters(self.read("private/tree.private.dot"))}
        for name, found in outputs.items():
            with self.subTest(name):
                self.assertEqual(found, [], f"control characters or U+FFFF in {name}")
        svgs = re.findall(r"<svg\b.*?</svg>", source, re.DOTALL | re.IGNORECASE)
        self.assertTrue(svgs)
        for index, svg in enumerate(svgs):
            with self.subTest(svg=index):
                try:
                    ET.fromstring(svg)
                except ET.ParseError as error:
                    self.fail(f"SVG is not well-formed XML: {error}")
        labels = [mermaid_text(label) for _, label in MERMAID_NODE.findall(self.read("private/tree.private.mmd"))]
        self.assertTrue(any("Мария &lt; Петрова" in label for label in labels), labels)


class PrintPage(unittest.TestCase):
    def test_print_page_passes_preflight_and_hides_living(self):
        graph = build_graph(PERSONS, RELATIONSHIPS, shareable=True, current_year=2026)
        page = render_print_page(graph, render_svg(graph, layout(graph)))
        self.assertEqual(inspect_html(page, Path("tree.html")), ([], []))
        images = [base64.b64decode(data).decode("utf-8")
                  for data in re.findall(r'src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)"', page)]
        graph_svg = next(image for image in images if "P-0004" in image)
        text = html.unescape(page) + "\n" + "\n".join(html.unescape(image) for image in images)
        self.assertNotIn("Анна", text)
        self.assertFalse(has_year(text, "1950"))
        self.assertNotIn("со слов", text)
        for rid in ("R-0004", "R-0007"):
            self.assertIn(EDGE_LABELS[rid], html.unescape(graph_svg))
        self.assertIn(BANNER_SHARED, text)
        for status in set(EDGE_STATUS.values()):  # legend: all six statuses, outside the graph image
            self.assertIn(status, html.unescape(page))


class MissingBrowser(CaseTestBase):
    def test_missing_browser_exit_3_keeps_other_files(self):
        self.write_fixture()
        (self.tree / "private").mkdir(parents=True)
        earlier = b"%PDF-1.4 earlier build\n"
        (self.tree / PRIVATE_PDF).write_bytes(earlier)
        env = {key: value for key, value in os.environ.items() if key != "CHROME_PATH"}
        if CHROME:
            env["CHROME_PATH"] = CHROME  # a named path that does not exist must win over a working one
        proc = self.run_tree("--chrome", str(Path(self.tmp.name) / "no-such-browser"), env=env)
        self.assert_exit(proc, 3)
        self.assertEqual(set(self.snapshot()), set(PRIVATE) | {PRIVATE_PDF})
        self.assertEqual((self.tree / PRIVATE_PDF).read_bytes(), earlier)
        self.assertIn("--chrome", proc.stderr)
        self.assertIn("CHROME_PATH", proc.stderr)

    def test_shareable_missing_browser_removes_stale_pdf(self):
        self.write_fixture()
        stale = self.tree / SHARED_PDF
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"%PDF-1.4 earlier shareable build\n")
        env = {key: value for key, value in os.environ.items() if key != "CHROME_PATH"}
        if CHROME:
            env["CHROME_PATH"] = CHROME  # a named path that does not exist must win over a working one
        proc = self.run_tree("--exclude-living", "--chrome", str(Path(self.tmp.name) / "no-such-browser"), env=env)
        self.assert_exit(proc, 3)
        self.assertFalse(stale.exists())
        self.assertEqual(set(self.snapshot()), set(SHARED))

    def test_shareable_no_pdf_removes_stale_pdf(self):
        self.write_fixture()
        self.build()
        (self.tree / "private").mkdir(parents=True, exist_ok=True)
        (self.tree / PRIVATE_PDF).write_bytes(b"%PDF-1.4 earlier private build\n")
        private = self.snapshot()
        stale = self.tree / SHARED_PDF
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"%PDF-1.4 earlier shareable build\n")
        proc = self.build("--exclude-living")
        self.assertFalse(stale.exists())
        self.assertIn(str(Path("tree", SHARED_PDF)), proc.stdout)
        after = self.snapshot()
        self.assertEqual(set(after), set(private) | set(SHARED))
        self.assertEqual({name: after[name] for name in private}, private)


@unittest.skipUnless(CHROME, "Chrome, Chromium or Edge not found")
class PdfBuild(CaseTestBase):
    def build_with_pdf(self, *args: str) -> subprocess.CompletedProcess:
        proc = self.run_tree(*args)
        self.assert_exit(proc, 0)
        return proc

    def test_default_build_writes_private_files(self):
        self.write_fixture()
        proc = self.build_with_pdf()
        self.assertEqual(set(self.snapshot()), set(PRIVATE) | {PRIVATE_PDF})
        self.assertEqual(self.folders(), {"private"})
        self.assertTrue((self.tree / PRIVATE_PDF).read_bytes().startswith(b"%PDF-"))
        self.assertEqual(stat.S_IMODE((self.tree / PRIVATE_PDF).stat().st_mode), 0o600)
        self.assert_counts(proc.stdout, 6, 7, 0)

    def test_shareable_build_keeps_private_files(self):
        self.write_fixture()
        self.build_with_pdf()
        private = self.snapshot()
        proc = self.build_with_pdf("--exclude-living")
        after = self.snapshot()
        self.assertEqual(set(after), set(private) | set(SHARED) | {SHARED_PDF})
        self.assertEqual(self.folders(), {"private", "share"})
        self.assertEqual({name: after[name] for name in private}, private)
        self.assertTrue(after[SHARED_PDF].startswith(b"%PDF-"))
        self.assert_counts(proc.stdout, 6, 7, 1)


if __name__ == "__main__":
    unittest.main()
