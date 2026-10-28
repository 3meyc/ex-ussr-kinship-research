#!/usr/bin/env python3
"""Граф родства дела: интерактивный HTML, PDF, Mermaid и Graphviz DOT.

По умолчанию показаны все сведения (рабочий вид, <дело>/tree/private/tree.private.*); с
--exclude-living живые лица скрыты до person_id (вид для передачи, <дело>/tree/share/tree.*).
Пока аудит реестров находит ошибки, граф не строится. PDF печатает установленный Chrome,
Chromium или Edge (build_pdf.print_pdf).
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from audit_evidence import Register, audit_registers, load_registers
from build_pdf import find_chrome, print_pdf
from research_common import atomic_bytes
from tree_layout import layout
from tree_model import Graph, IdConflictError, build_graph
from tree_render import (
    render_dot,
    render_html,
    render_mermaid,
    render_print_page,
    render_svg,
)

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

    registers, load_errors = load_registers(case_dir)
    audit = audit_registers(case_dir, registers, load_errors)
    if audit["errors"]:
        _report_audit_errors(audit["errors"], case_dir)
        return EXIT_AUDIT_ERRORS

    try:
        graph = _build_graph(registers, shareable=args.exclude_living)
    except IdConflictError as error:
        _report_id_conflicts(error)
        return EXIT_AUDIT_ERRORS
    # Режимы пишут в разные папки: tree/share/ можно отправить целиком, не выбирая файлы.
    if graph.shareable:
        out_dir, stem = case_dir / "tree" / "share", "tree"
    else:
        out_dir, stem = case_dir / "tree" / "private", "tree.private"
    pdf_path = out_dir / f"{stem}.pdf"
    # Прежний передаваемый PDF не совпадает с новым графом; его не должны отправить по ошибке, даже
    # если сборка прервётся, поэтому он удаляется до записи новых файлов.
    stale_pdf_removed = graph.shareable and _remove(pdf_path)
    svg = render_svg(graph, layout(graph))
    written = _write_outputs(out_dir, stem, _render(graph, svg))
    pdf_built = not args.no_pdf and _build_pdf(args.chrome, render_print_page(graph, svg), pdf_path)
    if pdf_built:
        written.append(pdf_path)
    elif stale_pdf_removed:
        print(f"Удалён устаревший PDF: {pdf_path}", file=sys.stdout if args.no_pdf else sys.stderr)
    _report_build(written, graph, warnings=len(audit["warnings"]))
    return 0 if pdf_built or args.no_pdf else EXIT_NO_PDF


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Строит граф родства дела по реестрам лиц и связей.")
    parser.add_argument("--case-dir", required=True, help="Папка дела")
    parser.add_argument(
        "--exclude-living",
        action="store_true",
        help="Вид для передачи: живые лица показаны только person_id (папка tree/share/)",
    )
    parser.add_argument("--no-pdf", action="store_true", help="Не строить PDF")
    parser.add_argument(
        "--chrome",
        help="Файл браузера для PDF; иной путь — браузера нет. Без него: CHROME_PATH, затем поиск",
    )
    return parser.parse_args(argv)


def _report_audit_errors(errors: Sequence[str], case_dir: Path) -> None:
    print(f"Граф не построен: аудит нашёл ошибок: {len(errors)}.", file=sys.stderr)
    for error in errors[:_ERRORS_SHOWN]:
        print(f"- {error}", file=sys.stderr)
    command = f"python3 scripts/audit_evidence.py --case-dir {shlex.quote(str(case_dir))}"
    print(f"Все ошибки: {command}", file=sys.stderr)


def _report_id_conflicts(error: IdConflictError) -> None:
    """Заголовок с числом id из ошибки, затем первые _ERRORS_SHOWN групп id, слитых или опустошённых
    очисткой, — по одной строке, и сколько групп не показано; id в записи Python (repr), чтобы
    управляющие знаки было видно в терминале."""
    print(f"Граф не построен: {error}.", file=sys.stderr)
    for conflict in error.conflicts[:_ERRORS_SHOWN]:
        raw_ids = ", ".join(f"{raw_id!r}" for raw_id in conflict.raw_ids)
        result = f"{conflict.cleaned!r}" if conflict.cleaned else "пусто"
        print(f"- {conflict.column} {raw_ids}: после очистки {result}", file=sys.stderr)
    more = len(error.conflicts) - _ERRORS_SHOWN
    if more > 0:
        print(f"- ещё {more} {_groups_ru(more)}", file=sys.stderr)
    print(
        "Такие id различаются только управляющими знаками, пробелами по краям или формой Юникода; "
        "исправьте их в реестрах.",
        file=sys.stderr,
    )


def _groups_ru(count: int) -> str:
    """Слово «группа» в форме для числа count."""
    if count % 10 == 1 and count % 100 != 11:
        return "группа"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "группы"
    return "групп"


def _build_graph(registers: Mapping[str, Register], *, shareable: bool) -> Graph:
    """Граф из тех же строк реестров, что прошли аудит."""
    return build_graph(
        registers["person-register.csv"].rows,
        registers["relationship-register.csv"].rows,
        shareable=shareable,
        current_year=date.today().year,
    )


def _render(graph: Graph, svg: str) -> dict[str, str]:
    """Тексты выходных файлов по расширению."""
    return {".html": render_html(graph, svg), ".mmd": render_mermaid(graph), ".dot": render_dot(graph)}


def _remove(path: Path) -> bool:
    """Удаляет файл, если он есть; True — файл был."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _write_outputs(out_dir: Path, stem: str, texts: Mapping[str, str]) -> list[Path]:
    """Каждый файл заменяется атомарно (временный файл в той же папке и os.replace)."""
    written = []
    for suffix, text in texts.items():
        path = out_dir / f"{stem}{suffix}"
        atomic_bytes(path, text.encode("utf-8"))
        written.append(path)
    return written


def _report_build(written: Sequence[Path], graph: Graph, *, warnings: int) -> None:
    print("Записано:")
    for path in written:
        print(f"  {path}")
    print(f"лица: {len(graph.nodes)}, связи: {len(graph.edges)}, скрыто живых: {graph.hidden_count}")
    if graph.skipped_rows:
        print(f"Не показано строк реестров с пустым person_id или relationship_id: {graph.skipped_rows}")
    print(f"Предупреждений аудита: {warnings}")


def _build_pdf(chrome: str | None, page: str, pdf_path: Path) -> bool:
    """Печатает страницу в PDF; при неудаче пишет причину в stderr, прежний PDF остаётся (в
    передаваемом режиме main уже удалил его до записи файлов)."""
    browser = _pick_browser(chrome)
    if browser is None:
        print(
            "PDF не построен: браузер не найден. Укажите --chrome PATH или переменную CHROME_PATH.",
            file=sys.stderr,
        )
        return False
    # Страница и PDF печатаются во временную папку рядом с PDF (папка 700, удаляется и при ошибке);
    # готовый PDF получает права 600, как прочие файлы, и только потом заменяет прежний.
    with tempfile.TemporaryDirectory(prefix=".tree-print-", dir=pdf_path.parent) as staging:
        page_path, printed = Path(staging) / "print.html", Path(staging) / pdf_path.name
        page_path.write_text(page, encoding="utf-8")
        code = print_pdf(browser, page_path, printed)
        if code:
            print(f"PDF не построен: браузер {browser} не смог напечатать граф (код {code}).", file=sys.stderr)
            return False
        printed.chmod(0o600)
        os.replace(printed, pdf_path)
    return True


def _pick_browser(explicit: str | None) -> str | None:
    """Явно указанный --chrome, если это существующий файл; без него — CHROME_PATH и автопоиск."""
    if explicit is not None:
        return explicit if Path(explicit).is_file() else None
    return find_chrome(None)


if __name__ == "__main__":
    raise SystemExit(main())
