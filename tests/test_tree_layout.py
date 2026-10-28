"""Generation layout of the kinship graph on plain register rows."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tree_layout import layout
from tree_model import build_graph


def person(person_id, label="", living_status="deceased", birth="", death=""):
    return {
        "person_id": person_id,
        "preferred_label": label,
        "living_status": living_status,
        "birth_estimate": birth,
        "death_estimate": death,
    }


def relationship(relationship_id, a, relationship_type, b, status="corroborated"):
    return {
        "relationship_id": relationship_id,
        "person_a_id": a,
        "relationship_type": relationship_type,
        "person_b_id": b,
        "status": status,
    }


FIXTURE_PERSONS = [
    person("P-0001", "Иван Петров", "deceased", "1850", "1920"),
    person("P-0002", "Мария Петрова", "probably-deceased", "1855"),
    person("P-0003", "Пётр Петров", "", "1880", "1941"),
    person("P-0004", "Анна Петрова", "living", "1950"),
    person("P-0005", "Ольга Петрова", "unknown", "ок. 1905–1912"),
    person("P-0006", "Сидор Кумов", "deceased", "1840", "1900"),
]
FIXTURE_RELATIONSHIPS = [
    relationship("R-0001", "P-0001", "parent-of", "P-0003", "strongly-corroborated"),
    relationship("R-0002", "P-0001", "spouse-of", "P-0002", "corroborated"),
    relationship("R-0003", "P-0002", "parent-of", "P-0003", "single-source"),
    relationship("R-0004", "P-0003", "parent-of", "P-0004", "hypothesis"),
    relationship("R-0005", "P-0006", "godparent-of", "P-0003", "unresolved"),
    relationship("R-0006", "P-0005", "sibling-of", "P-0003", "contradicted"),
    relationship("R-0007", "P-0001", "grandparent-of", "P-0004", ""),
]


def lay_out(persons, relationships):
    return layout(build_graph(persons, relationships, shareable=False, current_year=2026))


def placed_ids(result):
    """Every id the layout placed, with repeats, rows first and the unranked row last."""
    return [person_id for row in result.rows for person_id in row] + list(result.unranked)


class GenerationLayoutTest(unittest.TestCase):
    def test_parents_sit_one_row_above_children(self):
        result = lay_out(FIXTURE_PERSONS, FIXTURE_RELATIONSHIPS)

        self.assertEqual(
            [set(row) for row in result.rows],
            [{"P-0001", "P-0002"}, {"P-0003", "P-0005"}, {"P-0004"}],
        )
        row_y = [{result.positions[person_id][1] for person_id in row} for row in result.rows]
        self.assertTrue(all(len(ys) == 1 for ys in row_y), row_y)
        tops = [ys.pop() for ys in row_y]
        self.assertEqual(tops, sorted(set(tops)))

    def test_person_without_generation_edge_goes_to_bottom_row(self):
        result = lay_out(FIXTURE_PERSONS, FIXTURE_RELATIONSHIPS)

        self.assertEqual(result.unranked, ("P-0006",))
        self.assertNotIn("P-0006", [person_id for row in result.rows for person_id in row])
        lowest_ranked_y = max(result.positions[person_id][1] for row in result.rows for person_id in row)
        self.assertGreater(result.positions["P-0006"][1], lowest_ranked_y)

    def test_parent_cycle_places_every_person_once(self):
        persons = [person("P-0001"), person("P-0003")]
        relationships = [
            relationship("R-0001", "P-0001", "parent-of", "P-0003"),
            relationship("R-0002", "P-0003", "parent-of", "P-0001"),
        ]

        result = lay_out(persons, relationships)

        self.assertEqual(sorted(placed_ids(result)), ["P-0001", "P-0003"])
        self.assertEqual(set(result.positions), {"P-0001", "P-0003"})
        self.assertNotEqual(result.positions["P-0001"], result.positions["P-0003"])

    def test_separate_families_start_at_top_row_without_overlap(self):
        persons = [person(f"P-000{number}") for number in range(1, 6)]
        relationships = [
            relationship("R-0001", "P-0001", "parent-of", "P-0002"),
            relationship("R-0002", "P-0005", "parent-of", "P-0003"),
            relationship("R-0003", "P-0003", "parent-of", "P-0004"),
        ]

        result = lay_out(persons, relationships)

        self.assertEqual(
            [set(row) for row in result.rows],
            [{"P-0001", "P-0005"}, {"P-0002", "P-0003"}, {"P-0004"}],
        )
        self.assertEqual(sorted(placed_ids(result)), [p["person_id"] for p in persons])
        self.assertEqual(len(set(result.positions.values())), len(persons))
        for x, y in result.positions.values():
            self.assertTrue(0 < x < result.width and 0 < y < result.height, (x, y))


if __name__ == "__main__":
    unittest.main()
