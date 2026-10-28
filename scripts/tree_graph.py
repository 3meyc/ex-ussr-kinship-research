#!/usr/bin/env python3
"""Граф родства дела: интерактивный HTML, Mermaid и Graphviz DOT в <дело>/tree/.

По умолчанию показаны все сведения (рабочий вид, tree.private.*); с --exclude-living живые лица
скрыты до person_id (вид для передачи, tree.*). Пока аудит реестров находит ошибки, граф не
строится.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from audit_evidence import audit_case, load_registers
from build_pdf import find_chrome
from research_common import atomic_bytes
from tree_layout import layout
from tree_model import Graph, build_graph
from tree_render import render_dot, render_html, render_mermaid, render_svg

EXIT_AUDIT_ERRORS = 1
EXIT_NO_CASE = 2
EXIT_NO_PDF = 3
_ERRORS_SHOWN = 5


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    case_dir = Path(args.case_dir).expanduser()
    if not case_dir.is_dir():
        print(f"Ошибка: папка дела не найдена: {args.case_dir}", file=sys.stderr)
        return EXIT_NO_CASE
    case_dir = case_dir.resolve()

    audit = audit_case(case_dir)
    if audit["errors"]:
        _report_audit_errors(audit["errors"], case_dir)
        return EXIT_AUDIT_ERRORS

    graph = _load_graph(case_dir, shareable=args.exclude_living)
    stem = "tree" if graph.shareable else "tree.private"
    written = _write_outputs(case_dir / "tree", stem, _render(graph))
    _report_build(written, graph, warnings=len(audit["warnings"]))
    if args.no_pdf:
        return 0
    return _pdf_step(args.chrome)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Строит граф родства дела по реестрам лиц и связей.")
    parser.add_argument("--case-dir", required=True, help="Папка дела")
    parser.add_argument(
        "--exclude-living",
        action="store_true",
        help="Вид для передачи: живые лица показаны только person_id (файлы tree.*)",
    )
    parser.add_argument("--no-pdf", action="store_true", help="Не строить PDF")
    parser.add_argument(
        "--chrome",
        help="Браузер для PDF; несуществующий путь — браузера нет. Без него: CHROME_PATH, затем поиск",
    )
    return parser.parse_args(argv)


def _report_audit_errors(errors: Sequence[str], case_dir: Path) -> None:
    print(f"Граф не построен: аудит нашёл ошибок: {len(errors)}.", file=sys.stderr)
    for error in errors[:_ERRORS_SHOWN]:
        print(f"- {error}", file=sys.stderr)
    command = f"python3 scripts/audit_evidence.py --case-dir {shlex.quote(str(case_dir))}"
    print(f"Все ошибки: {command}", file=sys.stderr)


def _load_graph(case_dir: Path, *, shareable: bool) -> Graph:
    registers, _ = load_registers(case_dir)  # ошибки чтения уже отсеял аудит
    return build_graph(
        registers["person-register.csv"].rows,
        registers["relationship-register.csv"].rows,
        shareable=shareable,
        current_year=date.today().year,
    )


def _render(graph: Graph) -> dict[str, str]:
    """Тексты выходных файлов по расширению."""
    svg = render_svg(graph, layout(graph))
    return {".html": render_html(graph, svg), ".mmd": render_mermaid(graph), ".dot": render_dot(graph)}


def _write_outputs(tree_dir: Path, stem: str, texts: Mapping[str, str]) -> list[Path]:
    """Каждый файл заменяется атомарно (временный файл в tree/ и os.replace)."""
    written = []
    for suffix, text in texts.items():
        path = tree_dir / f"{stem}{suffix}"
        atomic_bytes(path, text.encode("utf-8"))
        written.append(path)
    return written


def _report_build(written: Sequence[Path], graph: Graph, *, warnings: int) -> None:
    print("Записано:")
    for path in written:
        print(f"  {path}")
    print(f"лица: {len(graph.nodes)}, связи: {len(graph.edges)}, скрыто живых: {graph.hidden_count}")
    print(f"Предупреждений аудита: {warnings}")


def _pdf_step(chrome: str | None) -> int:
    browser = _pick_browser(chrome)
    if browser is None:
        print(
            "PDF не построен: браузер не найден. Укажите --chrome PATH или переменную CHROME_PATH.",
            file=sys.stderr,
        )
    else:
        print("PDF не построен: печать графа в PDF ещё не поддерживается.", file=sys.stderr)
    return EXIT_NO_PDF


def _pick_browser(explicit: str | None) -> str | None:
    """Явно указанный --chrome, если путь существует; без него — CHROME_PATH и автопоиск."""
    if explicit is not None:
        return explicit if Path(explicit).exists() else None
    return find_chrome(None)


if __name__ == "__main__":
    raise SystemExit(main())
