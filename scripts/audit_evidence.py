#!/usr/bin/env python3
"""Audit cross-references and corroboration claims without assigning truth."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from collections.abc import Container, Iterable
from pathlib import Path
from typing import NamedTuple

REQUIRED_FILES = {
    "artifact-register.csv": {
        "artifact_id",
        "independence_group",
        "directness",
        "originality",
        "related_claim_ids",
        "status",
    },
    "claims-register.csv": {
        "claim_id",
        "artifact_ids",
        "supporting_independence_groups",
        "contradicting_artifact_ids",
        "status",
        "rationale",
    },
    "relationship-register.csv": {
        "relationship_id",
        "person_a_id",
        "relationship_type",
        "person_b_id",
        "claim_ids",
        "supporting_artifact_ids",
        "contradicting_artifact_ids",
        "independence_groups",
        "status",
        "rationale",
    },
    "person-register.csv": {"person_id"},
}
# Журнал поиска есть не в каждом деле: проверяется, только если файл существует.
OPTIONAL_FILES = {
    "search-log.csv": {"search_id", "target_person_ids", "result_status", "artifact_ids"},
}

CORROBORATED = {"corroborated", "strongly-corroborated"}
VALID_CLAIM_STATUSES = {
    "",
    "hypothesis",
    "single-source",
    "corroborated",
    "strongly-corroborated",
    "contradicted",
    "unresolved",
}

# Контролируемые значения (references/evidence-protocol.md). Пустое значение здесь не проверяется:
# пустые living_status и biological_legal_household допустимы, пустой relationship_type — отдельная ошибка.
CONTROLLED_VALUES = {
    "person-register.csv": {
        "living_status": {"living", "probably-living", "probably-deceased", "deceased", "unknown"},
    },
    "relationship-register.csv": {
        "relationship_type": {
            "parent-of",
            "spouse-of",
            "sibling-of",
            "half-sibling-of",
            "grandparent-of",
            "godparent-of",
            "other",
        },
        "biological_legal_household": {
            "biological",
            "legal",
            "adoptive",
            "step",
            "foster",
            "household",
            "unknown",
        },
    },
}


class Register(NamedTuple):
    rows: list[dict[str, str]]
    lines: list[int]  # номер строки файла для каждой записи rows
    errors: list[str]


EMPTY_REGISTER = Register([], [], [])


def split_ids(value: str) -> list[str]:
    if not value:
        return []
    normalized = value.replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def read_csv(path: Path, required: set[str]) -> Register:
    """Строки реестра с номерами строк файла и ошибки чтения."""
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return Register([], [], [f"{path.name}: отсутствует строка заголовка"])
            if len(reader.fieldnames) == 1 and ";" in reader.fieldnames[0]:
                return Register([], [], [f"{path.name}: разделитель «;» вместо запятой; сохраните файл как CSV с запятыми"])
            missing = sorted(required - set(reader.fieldnames))
            if missing:
                errors.append(f"{path.name}: отсутствуют колонки: {', '.join(missing)}")
            rows, lines = [], []
            for row in reader:
                if None in row:
                    # csv.DictReader складывает лишние поля в ключ None: обычно незакавыченная запятая.
                    errors.append(f"{path.name}: строка {reader.line_num} содержит больше полей, чем заголовок (незакавыченная запятая?)")
                    row.pop(None)
                if any(value is None for value in row.values()):
                    # Недостающие поля DictReader заполняет None: значения сдвинулись относительно заголовка.
                    present = sum(value is not None for value in row.values())
                    errors.append(
                        f"{path.name}: строка {reader.line_num} содержит меньше полей, чем заголовок "
                        f"({present} из {len(reader.fieldnames)}): значения могли сдвинуться в чужие колонки"
                    )
                rows.append({k: (v or "").strip() for k, v in row.items()})
                lines.append(reader.line_num)
            return Register(rows, lines, errors)
    except UnicodeDecodeError:
        return Register([], [], [f"{path.name}: файл не в UTF-8 (вероятно, cp1251 из Excel); сохраните его как «CSV UTF-8»"])
    except OSError as exc:
        return Register([], [], [f"{path.name}: ошибка чтения: {exc}"])


def load_registers(case_dir: Path) -> tuple[dict[str, Register], list[str]]:
    """Обязательные и имеющиеся необязательные реестры дела; отсутствующий необязательный — пустой."""
    registers: dict[str, Register] = {}
    errors: list[str] = []
    for filename, required in {**REQUIRED_FILES, **OPTIONAL_FILES}.items():
        path = case_dir / filename
        if not path.is_file():
            if filename in REQUIRED_FILES:
                errors.append(f"Отсутствует обязательный файл: {filename}")
            registers[filename] = EMPTY_REGISTER
            continue
        registers[filename] = read_csv(path, required)
        errors.extend(registers[filename].errors)
    return registers, errors


def row_label(row: dict[str, str], id_field: str, filename: str, line: int) -> str:
    """ID записи, а без ID — файл и строка, чтобы сообщение указывало на место."""
    return row.get(id_field, "") or f"{filename}, строка {line}"


def unknown_refs(label: str, field: str, value: str, known: Container[str]) -> list[str]:
    """Ошибки для ID из поля-списка, которых нет среди известных. field — имя ID в единственном числе."""
    return [f"{label}: неизвестный {field} {ref}" for ref in split_ids(value) if ref not in known]


def controlled_value_warnings(label: str, row: dict[str, str], allowed_by_field: dict[str, set[str]]) -> list[str]:
    warnings = []
    for field, allowed in allowed_by_field.items():
        value = row.get(field, "")
        if value and value not in allowed:
            warnings.append(f"{label}: неизвестное значение {field} {value!r}")
    return warnings


def relationship_shape(row: dict[str, str], label: str) -> tuple[list[str], list[str]]:
    """Связь — два разных лица и тип: ошибки и предупреждения по форме записи."""
    errors = [f"{label}: пустой {field}" for field in ("person_a_id", "relationship_type", "person_b_id")
              if not row.get(field, "")]
    person_a = row.get("person_a_id", "")
    if person_a and person_a == row.get("person_b_id", ""):
        errors.append(f"{label}: person_a_id и person_b_id совпадают: {person_a}")
    warnings = controlled_value_warnings(label, row, CONTROLLED_VALUES["relationship-register.csv"])
    if row.get("relationship_type", "") == "other" and not row.get("rationale", ""):
        warnings.append(f"{label}: relationship_type other без rationale")
    return errors, warnings


def duplicate_ids(rows: Iterable[dict[str, str]], field: str) -> list[str]:
    ids = [row.get(field, "") for row in rows if row.get(field, "")]
    return sorted(key for key, count in Counter(ids).items() if count > 1)


def artifact_groups(artifact_ids: Iterable[str], artifact_by_id: dict[str, dict[str, str]]) -> set[str]:
    return {
        artifact_by_id[artifact_id]["independence_group"].strip().casefold()
        for artifact_id in artifact_ids
        if artifact_id in artifact_by_id
        and artifact_by_id[artifact_id].get("independence_group", "").strip()
    }


def has_direct_record(artifact_ids: Iterable[str], artifact_by_id: dict[str, dict[str, str]]) -> bool:
    """Одна и та же запись должна быть прямой и иметь описанное происхождение.

    Непустое описание и локатор — проверка заполнения, не доказательство
    подлинности документа или истинности заключения.
    """
    for artifact_id in artifact_ids:
        row = artifact_by_id.get(artifact_id, {})
        provenance = row.get("provenance_level", "").strip().casefold()
        if (
            row.get("directness", "").casefold() == "direct"
            and row.get("originality", "").casefold()
            in {"original", "image-of-original", "official-extract"}
            and provenance not in {"", "unknown", "неизвестно", "не установлено"}
            and (row.get("source_url_or_path", "").strip() or row.get("archival_reference", "").strip())
        ):
            return True
    return False


def id_prefix(value: str) -> str:
    """Префикс ID без номера: 'A-0001' -> 'A-'."""
    return value.rstrip("0123456789")


REPORT_MARKERS = ("[неразборчиво]", "[предположительно")
HEADING_RE = re.compile(r"^#{2,6}\s")


def audit_report(
    text: str,
    known_ids: dict[str, set[str]],
    statuses: dict[str, str],
) -> tuple[list[str], dict[str, int]]:
    """Сверить ID и статусы итогового отчёта с реестрами.

    known_ids: префикс -> ID этого префикса из реестров. Стандартные префиксы
    проверяются и при пустых реестрах; плейсхолдеры (`A-....`) не содержат номера.
    statuses: ID утверждения или связи -> статус в реестре.
    """
    errors: list[str] = []
    for prefix, ids in sorted(known_ids.items()):
        pattern = re.compile(rf"(?<![\w-]){re.escape(prefix)}\d+(?!\w)")
        for cited in sorted(set(pattern.findall(text)) - ids):
            errors.append(f"final-report.md: ссылка на {cited}, которого нет в реестрах")

    valid = VALID_CLAIM_STATUSES - {""}
    section_id = ""
    section_checked = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if HEADING_RE.match(stripped):
            words = re.findall(r"[\w-]+", stripped)
            section_id = next((word for word in words if word in statuses), "")
            section_checked = False
            continue
        if stripped.startswith("|"):
            # Строка таблицы с ID в первой ячейке и статусом в одной из ячеек.
            cells = [cell.strip().strip("`") for cell in stripped.strip("|").split("|")]
            row_id, row_statuses = cells[0], [cell for cell in cells[1:] if cell in valid]
            if row_id in statuses and row_statuses and statuses[row_id] not in row_statuses:
                errors.append(
                    f"final-report.md:{number}: {row_id} — в отчёте {row_statuses[0]}, в реестре {statuses[row_id] or 'пусто'}"
                )
            continue
        # Первое поле вида **Статус:** value в секции связи/утверждения.
        match = re.match(r"^\*\*[^*]+:\*\*\s*`?([\w-]+)`?", stripped)
        if section_id and not section_checked and match and match.group(1) in valid:
            section_checked = True
            if match.group(1) != statuses[section_id]:
                errors.append(
                    f"final-report.md:{number}: {section_id} — в отчёте {match.group(1)}, в реестре {statuses[section_id] or 'пусто'}"
                )

    markers = {marker.strip("[]"): text.count(marker) for marker in REPORT_MARKERS}
    return errors, markers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Проверяет целостность реестров и минимальные условия кроссподтверждения."
    )
    parser.add_argument("--case-dir", required=True, help="Директория дела")
    parser.add_argument("--json", action="store_true", help="Вывести JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Возвращать ненулевой код также при предупреждениях",
    )
    return parser.parse_args()


def audit_case(case_dir: Path) -> dict:
    """Audit a resolved case directory; the result is what `--json` prints."""
    return audit_registers(case_dir, *load_registers(case_dir))


def audit_registers(case_dir: Path, registers: dict[str, Register], load_errors: list[str]) -> dict:
    """Audit registers already read by `load_registers`, so a caller can reuse the same rows."""
    errors: list[str] = list(load_errors)
    warnings: list[str] = []

    artifacts = registers["artifact-register.csv"].rows
    claims = registers["claims-register.csv"].rows
    relationships = registers["relationship-register.csv"].rows
    persons = registers["person-register.csv"].rows

    for filename, field, label in [
        ("artifact-register.csv", "artifact_id", "артефакта"),
        ("claims-register.csv", "claim_id", "утверждения"),
        ("relationship-register.csv", "relationship_id", "связи"),
        ("person-register.csv", "person_id", "лица"),
    ]:
        rows, lines = registers[filename].rows, registers[filename].lines
        duplicates = duplicate_ids(rows, field)
        if duplicates:
            errors.append(f"Дублирующиеся ID {label}: {', '.join(duplicates)}")
        for line, row in zip(lines, rows):
            if not row.get(field, ""):
                warnings.append(f"{field}: пустой ID в строке {line}")

    artifact_by_id = {row["artifact_id"]: row for row in artifacts if row.get("artifact_id")}
    claim_by_id = {row["claim_id"]: row for row in claims if row.get("claim_id")}
    person_ids = {row["person_id"] for row in persons if row.get("person_id")}

    for line, row in zip(registers["person-register.csv"].lines, persons):
        label = row_label(row, "person_id", "person-register.csv", line)
        errors.extend(unknown_refs(label, "source_artifact_id", row.get("source_artifact_ids", ""), artifact_by_id))
        warnings.extend(controlled_value_warnings(label, row, CONTROLLED_VALUES["person-register.csv"]))

    search_log = registers["search-log.csv"]
    for line, row in zip(search_log.lines, search_log.rows):
        label = row_label(row, "search_id", "search-log.csv", line)
        # Журнал поиска необязателен: его ссылки — предупреждения, не ошибки.
        warnings.extend(unknown_refs(label, "target_person_id", row.get("target_person_ids", ""), person_ids))
        warnings.extend(unknown_refs(label, "artifact_id", row.get("artifact_ids", ""), artifact_by_id))

    for artifact_id, row in artifact_by_id.items():
        if not row.get("independence_group", "").strip():
            warnings.append(f"{artifact_id}: не указан independence_group")
        errors.extend(unknown_refs(artifact_id, "person_id", row.get("person_ids", ""), person_ids))
        errors.extend(unknown_refs(artifact_id, "related claim_id", row.get("related_claim_ids", ""), claim_by_id))

    for claim_id, row in claim_by_id.items():
        status = row.get("status", "")
        if status not in VALID_CLAIM_STATUSES:
            warnings.append(f"{claim_id}: неизвестный статус {status!r}")
        errors.extend(unknown_refs(claim_id, "subject_person_id", row.get("subject_person_ids", ""), person_ids))
        referenced = split_ids(row.get("artifact_ids", ""))
        contradictory = split_ids(row.get("contradicting_artifact_ids", ""))
        for field in ("artifact_ids", "contradicting_artifact_ids"):
            errors.extend(unknown_refs(claim_id, "artifact_id", row.get(field, ""), artifact_by_id))

        groups = artifact_groups(referenced, artifact_by_id)
        for group in sorted(set(split_ids(row.get("supporting_independence_groups", "")))):
            if group.casefold() not in groups:
                errors.append(
                    f"{claim_id}: independence_group {group} заявлен, но ни один из artifact_ids к нему не относится"
                )

        if status in CORROBORATED and len(groups) < 2:
            warnings.append(
                f"{claim_id}: статус {status}, но найдено независимых групп: {len(groups)}"
            )
        if status in CORROBORATED and not row.get("rationale"):
            warnings.append(f"{claim_id}: для {status} отсутствует rationale")
        if status in CORROBORATED and contradictory:
            warnings.append(
                f"{claim_id}: статус {status}, но указаны противоречащие артефакты; "
                "проверьте и объясните, разрешён ли существенный конфликт"
            )
        if status == "single-source" and len(groups) > 1:
            warnings.append(
                f"{claim_id}: несколько независимых групп, но статус всё ещё single-source; проверьте конфликт/полноту"
            )
        if status == "strongly-corroborated" and not has_direct_record(referenced, artifact_by_id):
            warnings.append(
                f"{claim_id}: strongly-corroborated без единой прямой записи "
                "с указанным происхождением и локатором оригинала, образа или официальной выписки"
            )

    for line, row in zip(registers["relationship-register.csv"].lines, relationships):
        relation_id = row_label(row, "relationship_id", "relationship-register.csv", line)
        status = row.get("status", "")
        if status not in VALID_CLAIM_STATUSES:
            warnings.append(f"{relation_id}: неизвестный статус {status!r}")
        shape_errors, shape_warnings = relationship_shape(row, relation_id)
        errors.extend(shape_errors)
        warnings.extend(shape_warnings)
        for person_field in ("person_a_id", "person_b_id"):
            person_id = row.get(person_field, "")
            if person_id and person_id not in person_ids:
                errors.append(f"{relation_id}: неизвестный {person_field} {person_id}")
        errors.extend(unknown_refs(relation_id, "claim_id", row.get("claim_ids", ""), claim_by_id))
        for field in ("supporting_artifact_ids", "contradicting_artifact_ids"):
            errors.extend(unknown_refs(relation_id, "artifact_id", row.get(field, ""), artifact_by_id))
        # Группа засчитывается, только если за ней стоит артефакт: прямой или через claim_ids.
        backing_ids = split_ids(row.get("supporting_artifact_ids", ""))
        contradictory = set(split_ids(row.get("contradicting_artifact_ids", "")))
        for claim_id in split_ids(row.get("claim_ids", "")):
            if claim_id in claim_by_id:
                backing_ids += split_ids(claim_by_id[claim_id].get("artifact_ids", ""))
                contradictory.update(split_ids(claim_by_id[claim_id].get("contradicting_artifact_ids", "")))
        groups = artifact_groups(backing_ids, artifact_by_id)
        for group in sorted(set(split_ids(row.get("independence_groups", "")))):
            if group.casefold() not in groups:
                errors.append(
                    f"{relation_id}: independence_group {group} заявлен, но ни один из артефактов связи к нему не относится"
                )
        if status in CORROBORATED:
            if len(groups) < 2:
                warnings.append(
                    f"{relation_id}: статус {row.get('status')}, но указано независимых групп: {len(groups)}"
                )
            if not row.get("rationale"):
                warnings.append(f"{relation_id}: отсутствует rationale")
            if contradictory:
                warnings.append(
                    f"{relation_id}: статус {status}, но указаны противоречащие артефакты; "
                    "проверьте и объясните, разрешён ли существенный конфликт"
                )
        if status == "strongly-corroborated" and not has_direct_record(backing_ids, artifact_by_id):
            warnings.append(
                f"{relation_id}: strongly-corroborated без единой прямой записи "
                "с указанным происхождением и локатором оригинала, образа или официальной выписки"
            )

    # Итоговый отчёт: ссылки и статусы должны совпадать с реестрами.
    markers: dict[str, int] = {}
    report = case_dir / "final-report.md"
    if report.is_file():
        known_ids: dict[str, set[str]] = {
            prefix: set() for prefix in ("A-", "C-", "R-", "P-")
        }
        for rows, field in [
            (artifacts, "artifact_id"),
            (claims, "claim_id"),
            (relationships, "relationship_id"),
            (persons, "person_id"),
        ]:
            for row in rows:
                if row.get(field, "") and id_prefix(row[field]) != row[field]:
                    known_ids.setdefault(id_prefix(row[field]), set()).add(row[field])
        statuses = {claim_id: row.get("status", "") for claim_id, row in claim_by_id.items()}
        statuses.update(
            (row["relationship_id"], row.get("status", ""))
            for row in relationships
            if row.get("relationship_id")
        )
        report_text = report.read_text(encoding="utf-8-sig")
        report_errors, markers = audit_report(report_text, known_ids, statuses)
        errors.extend(report_errors)

    # Находка без артефакта — предупреждение.
    search_rows = registers["search-log.csv"].rows
    for row in search_rows:
        if row.get("result_status", "").strip().casefold() == "found" and not row.get("artifact_ids", "").strip():
            warnings.append(
                f"{row.get('search_id') or '<без ID>'}: result_status=found, но artifact_ids пуст — "
                "находка не зарегистрирована как артефакт"
            )

    # Сводка готовности: что ещё не снято. Не ошибки — для решения, выдавать ли отчёт.
    summary = {
        "claims_by_status": dict(Counter(row.get("status", "") or "пусто" for row in claims)),
        "relationships_by_status": dict(
            Counter(row.get("status", "") or "пусто" for row in relationships)
        ),
        "persons_by_living_status": dict(
            Counter(row.get("living_status", "") or "пусто" for row in persons)
        ),
        "report_markers": markers,
    }

    return {
        "case_dir": str(case_dir),
        "counts": {
            "persons": len(persons),
            "artifacts": len(artifacts),
            "claims": len(claims),
            "relationships": len(relationships),
            "searches": len(search_rows),
        },
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    args = parse_args()
    case_dir = Path(args.case_dir).expanduser().resolve()

    if not case_dir.is_dir():
        print(f"Ошибка: директория дела не найдена: {case_dir}", file=sys.stderr)
        return 2

    result = audit_case(case_dir)
    counts, summary = result["counts"], result["summary"]
    markers = summary["report_markers"]
    errors, warnings = result["errors"], result["warnings"]

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Проверено дело: {case_dir}")
        print(
            "Записей: "
            f"лица={counts['persons']}, артефакты={counts['artifacts']}, "
            f"утверждения={counts['claims']}, связи={counts['relationships']}, поиски={counts['searches']}"
        )
        open_statuses = ("hypothesis", "single-source", "unresolved", "contradicted", "пусто")
        print("\nСВОДКА:")
        for label, key in [("утверждения", "claims_by_status"), ("связи", "relationships_by_status")]:
            by_status = summary[key]
            line = ", ".join(f"{status}={by_status[status]}" for status in sorted(by_status))
            pending = sum(by_status.get(status, 0) for status in open_statuses)
            print(f"- {label}: {line or 'нет'}; без кроссподтверждения: {pending}")
        living = summary["persons_by_living_status"]
        print("- лица по living_status: " + (", ".join(f"{key}={living[key]}" for key in sorted(living)) or "нет"))
        if markers:
            print("- в отчёте: " + ", ".join(f"[{name}]={count}" for name, count in markers.items()))
        if errors:
            print("\nОШИБКИ:")
            for item in errors:
                print(f"- {item}")
        if warnings:
            print("\nПРЕДУПРЕЖДЕНИЯ:")
            for item in warnings:
                print(f"- {item}")
        if not errors and not warnings:
            print("Ошибок и предупреждений не найдено.")

    if errors:
        return 1
    if warnings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research_common import configure_utf8_stdio

    configure_utf8_stdio()
    raise SystemExit(main())
