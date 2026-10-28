"""Приёмочные тесты tree_graph.py: CLI на временном деле, созданном init_case.py.

Запуск: python3 -m unittest tests.test_tree_graph
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from scripts.build_pdf import find_chrome

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
ASSETS = ROOT / "assets"
CHROME = find_chrome(None)

PRIVATE = ("tree.private.html", "tree.private.mmd", "tree.private.dot")
SHARED = ("tree.html", "tree.mmd", "tree.dot")
PRIVATE_PDF = "tree.private.pdf"
SHARED_PDF = "tree.pdf"
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
    """Collects edge groups (attributes of the group merged with its first path) and the panel data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.edges: dict = {}
        self.node_ids: list = []
        self.tree_data = None
        self._groups: list = []
        self._with_path: set = set()
        self._in_tree_data = False

    def handle_starttag(self, tag, attrs):
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "").split()
        if tag == "script" and values.get("id") == "tree-data" and values.get("type") == "application/json":
            self._in_tree_data = True
            self.tree_data = ""
        elif tag == "g":
            if "node" in classes:
                self.node_ids.append(values.get("data-id"))
            edge_id = values.get("data-id") if "edge" in classes else None
            if edge_id:
                self.edges[edge_id] = values
            self._groups.append(edge_id)
        elif tag == "path":
            edge_id = next((group for group in reversed(self._groups) if group), None)
            if edge_id and edge_id not in self._with_path:
                self._with_path.add(edge_id)
                self.edges[edge_id] = {**self.edges[edge_id], **values}

    def handle_endtag(self, tag):
        if tag == "g" and self._groups:
            self._groups.pop()
        elif tag == "script":
            self._in_tree_data = False

    def handle_data(self, data):
        if self._in_tree_data:
            self.tree_data += data


def parse_page(source: str) -> SvgEdges:
    parser = SvgEdges()
    parser.feed(source)
    parser.close()
    return parser


def page_text(source: str) -> str:
    """Page markup with entities decoded plus the panel data with JSON escapes decoded."""
    data = json.loads(parse_page(source).tree_data)
    return html.unescape(source) + "\n" + json.dumps(data, ensure_ascii=False)


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

    def write_fixture(self, persons: dict | None = None, relationships: dict | None = None) -> None:
        """Write the registers; `persons`/`relationships` map a row id to field overrides."""
        self.write_register("person-register.csv", PERSONS, "person_id", persons or {})
        self.write_register("relationship-register.csv", RELATIONSHIPS, "relationship_id", relationships or {})
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
        return {path.name: path.read_bytes() for path in self.tree.iterdir()} if self.tree.exists() else {}

    def read(self, name: str) -> str:
        return (self.tree / name).read_text(encoding="utf-8")

    def assert_counts(self, stdout: str, persons: int, relations: int, hidden: int) -> None:
        for name, number in (("лица", persons), ("связи", relations), ("скрыто живых", hidden)):
            self.assertRegex(stdout, rf"{name}\W*{number}(?!\d)")


class OutputsPerMode(CaseTestBase):
    def test_no_pdf_writes_three_files(self):
        self.write_fixture()
        proc = self.build()
        self.assertEqual(set(self.snapshot()), set(PRIVATE))
        for name in PRIVATE:
            self.assertIn(name, proc.stdout)
        self.assert_counts(proc.stdout, 6, 7, 0)

        proc = self.build("--exclude-living")
        self.assertEqual(set(self.snapshot()), set(PRIVATE) | set(SHARED))
        for name in SHARED:
            self.assertIn(name, proc.stdout)
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
        self.assertIn("В реестре нет лиц", html.unescape(self.read("tree.private.html")))


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
        source = self.read("tree.private.html")
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
        self.assertIn("со слов Анны Петровой", texts["tree.private.html"])

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
        self.assertIn("сведения скрыты", texts["tree.html"])
        self.assertLessEqual({"R-0004", "R-0007"}, set(parse_page(self.read("tree.html")).edges))

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
        edges = parse_page(self.read("tree.private.html")).edges
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

    def test_mermaid_and_dot_have_every_node_and_edge(self):
        mmd, dot = self.read("tree.private.mmd"), self.read("tree.private.dot")
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


class InteractivePage(CaseTestBase):
    def test_page_has_no_network_references(self):
        self.write_fixture()
        self.build()
        source = self.read("tree.private.html")
        for pattern in (r"<link\b", r"\b(?:src|href)\s*=\s*[\"']?\s*https?:",
                        r"url\(\s*[\"']?\s*https?:", r"@import[^;]*https?:"):
            with self.subTest(pattern):
                self.assertIsNone(re.search(pattern, source, re.IGNORECASE))

    def test_register_text_cannot_inject_markup(self):
        payload = 'Иван "</script><script>alert(1)</script>'
        self.write_fixture(persons={"P-0001": {"preferred_label": payload}})
        self.build()
        source = self.read("tree.private.html")
        self.assertEqual(len(re.findall(r"<script\b", source, re.IGNORECASE)), 2)
        self.assertIn(payload, page_text(source))
        mmd, dot = self.read("tree.private.mmd"), self.read("tree.private.dot")
        self.assertIn("Иван #quot;", mmd)
        self.assertNotIn('Иван "', mmd)
        self.assertEqual(len(MERMAID_NODE.findall(mmd)), 6)
        self.assertIn('Иван \\"', dot)
        self.assertEqual(len(dot_nodes(dot)), 6)
        self.assertEqual(set(dot_edges(dot)), set(EDGE_LABELS))


class MissingBrowser(CaseTestBase):
    def test_missing_browser_exit_3_keeps_other_files(self):
        self.write_fixture()
        self.tree.mkdir()
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
        self.assertTrue((self.tree / PRIVATE_PDF).read_bytes().startswith(b"%PDF-"))
        self.assert_counts(proc.stdout, 6, 7, 0)

    def test_shareable_build_keeps_private_files(self):
        self.write_fixture()
        self.build_with_pdf()
        private = self.snapshot()
        proc = self.build_with_pdf("--exclude-living")
        after = self.snapshot()
        self.assertEqual(set(after), set(private) | set(SHARED) | {SHARED_PDF})
        self.assertEqual({name: after[name] for name in private}, private)
        self.assertTrue(after[SHARED_PDF].startswith(b"%PDF-"))
        self.assert_counts(proc.stdout, 6, 7, 1)


if __name__ == "__main__":
    unittest.main()
