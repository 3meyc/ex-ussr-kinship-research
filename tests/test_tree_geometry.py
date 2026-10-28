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
        # 13 ends span 12 × 12 = 144 px, the most that fits within 74 px of the side's middle.
        children = 13
        persons = [person(f"P-{number:04d}") for number in range(children + 1)]
        relationships = [
            relationship(f"R-{child:04d}", "P-0000", "parent-of", f"P-{child:04d}") for child in range(1, children + 1)
        ]

        _, routes = routed(persons, relationships)

        x, y = routes.positions["P-0000"]
        starts = sorted(coordinates(edge)[0] for edge in routes.edges)
        self.assertEqual({start_y for _, start_y in starts}, {y + 24})
        xs = [start_x for start_x, _ in starts]
        self.assertEqual(len(xs), children)
        self.assertTrue(all(right - left >= 12 for left, right in zip(xs, xs[1:])), xs)
        self.assertTrue(all(abs(start_x - x) <= 74 for start_x in xs), xs)

    def test_edge_runs_straight_between_its_box_and_its_own_channel(self):
        # Each case routes edges from row 0 past row 1 to row 2; ``end`` indexes the checked end.
        upper_end = (
            [person(f"P-000{number}") for number in range(4)],
            [
                relationship("R-0001", "P-0000", "parent-of", "P-0001"),
                relationship("R-0002", "P-0000", "parent-of", "P-0003"),
                relationship("R-0003", "P-0001", "parent-of", "P-0002"),
                relationship("R-0004", "P-0000", "grandparent-of", "P-0002"),
            ],
            (("P-0000",), ("P-0001", "P-0003"), ("P-0002",)),
            ("R-0004",),
            0,
        )
        lower_end = (
            [person(person_id) for person_id in ("P-0000", "P-0003", "P-0004", "P-0005")],
            [
                relationship("R-0004", "P-0004", "parent-of", "P-0003"),
                relationship("R-0005", "P-0004", "grandparent-of", "P-0005"),
                relationship("R-0006", "P-0004", "parent-of", "P-0000"),
                relationship("R-0009", "P-0004", "parent-of", "P-0005"),
            ],
            (("P-0004",), ("P-0000", "P-0003"), ("P-0005",)),
            ("R-0005", "R-0009"),
            -1,
        )
        # Channels leave the six ends below P-0000 no lane-grid layout near the side's middle.
        crowded_side = (
            [person(f"P-000{number}") for number in range(4)],
            [
                relationship("R-0000", "P-0000", "grandparent-of", "P-0001"),
                relationship("R-0001", "P-0000", "parent-of", "P-0001"),
                relationship("R-0003", "P-0000", "spouse-of", "P-0002"),
                relationship("R-0005", "P-0000", "parent-of", "P-0003"),
                relationship("R-0007", "P-0000", "parent-of", "P-0001"),
                relationship("R-0009", "P-0000", "grandparent-of", "P-0001"),
                relationship("R-0010", "P-0000", "parent-of", "P-0003"),
            ],
            (("P-0000", "P-0002"), ("P-0003",), ("P-0001",)),
            ("R-0000", "R-0001", "R-0007", "R-0009"),
            0,
        )
        for name, (persons, relationships, rows, checked, end) in (
            ("upper end", upper_end),
            ("lower end", lower_end),
            ("upper end on a crowded side", crowded_side),
        ):
            with self.subTest(name):
                grid, routes = routed(persons, relationships)

                self.assertEqual(grid.rows, rows)
                edges = dict(zip(sorted(row["relationship_id"] for row in relationships), routes.edges))
                middle_y = routes.positions[rows[1][0]][1]
                for relationship_id in checked:
                    points = coordinates(edges[relationship_id])
                    end_x, end_y = points[end]
                    # Points between the end's box and row 1 (boxes are 48 px tall).
                    beside_end = {x for x, y in points if abs(y - middle_y) > 24 and (y < middle_y) == (end_y < middle_y)}
                    self.assertEqual(beside_end, {end_x}, (relationship_id, points))

    def test_every_end_stays_on_its_own_box_side(self):
        bundle_through_a_row = (
            [person(f"P-000{number}") for number in range(1, 5)],
            [
                relationship("R-0001", "P-0001", "grandparent-of", "P-0003"),
                relationship("R-0002", "P-0001", "grandparent-of", "P-0003"),
                relationship("R-0003", "P-0001", "half-sibling-of", "P-0003"),
                relationship("R-0004", "P-0001", "other", "P-0003"),
                relationship("R-0005", "P-0001", "parent-of", "P-0002"),
                relationship("R-0006", "P-0001", "spouse-of", "P-0004"),
            ],
        )
        ninety_children = (
            [person(f"P-{number:04d}") for number in range(91)],
            [relationship(f"R-{child:04d}", "P-0000", "parent-of", f"P-{child:04d}") for child in range(1, 91)],
        )
        for name, (persons, relationships) in (
            ("bundle crossing a row", bundle_through_a_row),
            ("90 ends on one side", ninety_children),
        ):
            with self.subTest(name):
                _, routes = routed(persons, relationships)

                for edge, data in zip(routes.edges, sorted(relationships, key=lambda row: row["relationship_id"])):
                    points = coordinates(edge)
                    people = data["person_a_id"], data["person_b_id"]
                    rows = [routes.positions[person_id][1] for person_id in people]
                    for (end_x, end_y), person_id in zip((points[0], points[-1]), people):
                        x, y = routes.positions[person_id]
                        # Same row: both ends on the top side; otherwise the upper box's bottom
                        # side meets the lower box's top side.
                        side = 24 if y < max(rows) else -24
                        self.assertEqual(end_y, y + side, (data["relationship_id"], person_id))
                        # 80 is half the box width; ends stay off its 6 px rounded corners.
                        self.assertLessEqual(abs(end_x - x), 74, (data["relationship_id"], person_id, end_x, x))


if __name__ == "__main__":
    unittest.main()
