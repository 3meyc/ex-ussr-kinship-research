"""Edge geometry of the kinship graph on plain register rows: arc heights, room for edges, ports."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tree_geometry import route
from tree_layout import layout
from tree_model import build_graph


def person(person_id):
    return {"person_id": person_id, "preferred_label": person_id, "living_status": "deceased", "birth_estimate": "1850"}


def relationship(relationship_id, a, relationship_type, b):
    return {
        "relationship_id": relationship_id,
        "person_a_id": a,
        "relationship_type": relationship_type,
        "person_b_id": b,
        "status": "corroborated",
    }


def routed(persons, relationships):
    graph = build_graph(persons, relationships, shareable=False, current_year=2026)
    grid = layout(graph)
    return grid, route(graph, grid)


def coordinates(edge_route):
    """(x, y) of every point a path command names: ends, corners and curve controls."""
    values = [value for _, args in edge_route.commands for value in args]
    return list(zip(values[::2], values[1::2]))


class EdgeGeometryTest(unittest.TestCase):
    def test_overlapping_arcs_in_one_row_are_a_lane_apart(self):
        persons = [person(f"P-000{number}") for number in range(1, 5)]
        relationships = [
            relationship("R-0001", "P-0001", "sibling-of", "P-0002"),
            relationship("R-0002", "P-0001", "sibling-of", "P-0003"),
            relationship("R-0003", "P-0002", "sibling-of", "P-0004"),
        ]

        grid, routes = routed(persons, relationships)

        self.assertEqual(grid.rows, (("P-0001", "P-0002", "P-0003", "P-0004"),))
        first_top = min(y for _, y in coordinates(routes.edges[1]))
        second_top = min(y for _, y in coordinates(routes.edges[2]))
        self.assertGreaterEqual(abs(first_top - second_top), 12)

    def test_row_moves_down_when_arcs_need_more_room_than_the_margin(self):
        persons = [person("P-0001"), person("P-0002")]
        relationships = [relationship("R-0001", "P-0001", "spouse-of", "P-0002")] + [
            relationship(f"R-000{number}", "P-0001", "other", "P-0002") for number in range(2, 9)
        ]

        grid, routes = routed(persons, relationships)

        growth = routes.height - grid.height
        self.assertGreater(growth, 0)
        for person_id in ("P-0001", "P-0002"):
            self.assertEqual(routes.positions[person_id][1], grid.positions[person_id][1] + growth)
        tops = sorted(min(y for _, y in coordinates(edge)) for edge in routes.edges)
        self.assertGreaterEqual(tops[0], 0)
        self.assertTrue(all(lower - upper >= 12 for upper, lower in zip(tops, tops[1:])), tops)

    def test_ends_on_one_side_of_a_box_are_a_lane_apart(self):
        persons = [person(f"P-000{number}") for number in range(5)]
        relationships = [relationship(f"R-000{child}", "P-0000", "parent-of", f"P-000{child}") for child in range(1, 5)]

        _, routes = routed(persons, relationships)

        x, y = routes.positions["P-0000"]
        starts = sorted(coordinates(edge)[0] for edge in routes.edges)
        self.assertEqual({start_y for _, start_y in starts}, {y + 24})
        xs = [start_x for start_x, _ in starts]
        self.assertTrue(all(right - left >= 12 for left, right in zip(xs, xs[1:])), xs)
        self.assertTrue(all(x - 80 < start_x < x + 80 for start_x in xs), xs)


if __name__ == "__main__":
    unittest.main()
