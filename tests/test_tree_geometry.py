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


def corners(edge_route):
    """Corners of the orthogonal path before rounding: its ends and each curve's control point."""
    points = coordinates(edge_route)
    controls = [args[:2] for kind, args in edge_route.commands if kind == "Q"]
    return [points[0], *controls, points[-1]]


def relationships_from(rows):
    """Relationship rows from "R-0001 P-0001 parent-of P-0002" lines."""
    return [relationship(*row.split()) for row in rows]


def persons_of(relationships):
    return [
        person(person_id)
        for person_id in sorted({row[key] for row in relationships for key in ("person_a_id", "person_b_id")})
    ]


def paths_by_id(routes, relationships):
    """relationship_id → path points; routes.edges follow relationship_id order."""
    return dict(zip(sorted(row["relationship_id"] for row in relationships), map(coordinates, routes.edges)))


def ends_and_sides(routes, relationships):
    """(relationship_id, person_id, end, middle of the end's box side) for both ends of every path."""
    paths = paths_by_id(routes, relationships)
    result = []
    for data in relationships:
        points = paths[data["relationship_id"]]
        people = data["person_a_id"], data["person_b_id"]
        lower_row = max(routes.positions[person_id][1] for person_id in people)
        for end, person_id in zip((points[0], points[-1]), people):
            x, y = routes.positions[person_id]
            # Same row: both ends on the top side; otherwise the upper box's bottom
            # side meets the lower box's top side.
            side = 24 if y < lower_row else -24
            result.append((data["relationship_id"], person_id, end, (x, y + side)))
    return result


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

                self.assert_ends_on_their_sides(routes, relationships)

    def assert_ends_on_their_sides(self, routes, relationships):
        for relationship_id, person_id, (end_x, end_y), (x, side_y) in ends_and_sides(routes, relationships):
            self.assertEqual(end_y, side_y, (relationship_id, person_id))
            # 80 is half the box width; ends stay off its 6 px rounded corners.
            self.assertLessEqual(abs(end_x - x), 74, (relationship_id, person_id, end_x, x))

    def test_end_takes_the_nearest_placement_on_either_side_of_the_middle(self):
        relationships = relationships_from(
            [
                "R-0001 P-0008 grandparent-of P-0000",
                "R-0006 P-0000 parent-of P-0006",
                "R-0007 P-0000 spouse-of P-0001",
                "R-0011 P-0008 sibling-of P-0005",
                "R-0014 P-0006 half-sibling-of P-0001",
                "R-0016 P-0000 grandparent-of P-0003",
                "R-0019 P-0004 sibling-of P-0005",
                "R-0021 P-0008 parent-of P-0003",
                "R-0022 P-0008 sibling-of P-0003",
                "R-0024 P-0008 other P-0003",
            ]
        )

        _, routes = routed(persons_of(relationships), relationships)

        x, y = routes.positions["P-0001"]
        sides = {
            (relationship_id, person_id): end
            for relationship_id, person_id, end, _ in ends_and_sides(routes, relationships)
        }
        self.assertEqual(sides[("R-0014", "P-0001")], (x - 20, y + 24))
        self.assert_ends_on_their_sides(routes, relationships)

    def test_every_drawn_lane_starts_at_its_own_end_on_a_crowded_side(self):
        # 60 parent-of and 30 godparent-of edges: 90 ends on one side, a step of 1 px.
        relationships = [
            relationship(f"R-{child:04d}", "P-0000", "parent-of", f"P-{child:04d}") for child in range(1, 61)
        ] + [
            relationship(f"R-{60 + index:04d}", "P-0000", "godparent-of", f"P-{child:04d}")
            for index, child in enumerate(range(1, 61, 2), start=1)
        ]

        _, routes = routed(persons_of(relationships), relationships)

        x, y = routes.positions["P-0000"]
        starts = [points[0] for points in paths_by_id(routes, relationships).values()]
        self.assertEqual(len(starts), 90)
        self.assertEqual({start_y for _, start_y in starts}, {y + 24})
        xs = {start_x for start_x, _ in starts}
        self.assertEqual(len(xs), 90, sorted(xs))
        self.assertTrue(all(abs(start_x - x) <= 74 for start_x in xs), sorted(xs))

    def test_each_end_of_a_pair_spaces_its_lanes_by_its_own_side_step(self):
        # 15 ends below P-0000 (a 10 px step); P-0001's top side holds only the pair's 2 ends.
        relationships = [
            relationship(f"R-{child:04d}", "P-0000", "parent-of", f"P-{child:04d}") for child in range(1, 15)
        ] + [relationship("R-0015", "P-0000", "godparent-of", "P-0001")]

        _, routes = routed(persons_of(relationships), relationships)

        paths = paths_by_id(routes, relationships)
        pair = [paths["R-0001"], paths["R-0015"]]
        upper = [points[0] for points in pair]
        lower = [points[-1] for points in pair]
        self.assertEqual({end_y for _, end_y in upper}, {routes.positions["P-0000"][1] + 24})
        self.assertEqual({end_y for _, end_y in lower}, {routes.positions["P-0001"][1] - 24})
        self.assertEqual(abs(lower[0][0] - lower[1][0]), 12, lower)
        self.assertEqual(abs(upper[0][0] - upper[1][0]), 10, upper)

    def test_no_path_has_a_horizontal_jog_shorter_than_a_lane(self):
        relationships = relationships_from(
            [
                "R-0004 P-0000 spouse-of P-0002",
                "R-0008 P-0006 sibling-of P-0000",
                "R-0010 P-0000 parent-of P-0003",
                "R-0011 P-0000 spouse-of P-0008",
                "R-0012 P-0000 grandparent-of P-0009",
                "R-0015 P-0000 other P-0001",
                "R-0017 P-0000 parent-of P-0005",
                "R-0019 P-0007 parent-of P-0004",
                "R-0021 P-0007 half-sibling-of P-0005",
            ]
        )

        _, routes = routed(persons_of(relationships), relationships)

        # A horizontal segment joins two consecutive corners at one y.
        paths = dict(zip(sorted(row["relationship_id"] for row in relationships), map(corners, routes.edges)))
        jogs = [
            (relationship_id, left, right)
            for relationship_id, points in paths.items()
            for left, right in zip(points, points[1:])
            if left[1] == right[1] and 0 < abs(right[0] - left[0]) < 6
        ]
        self.assertEqual(jogs, [])

    def test_edge_runs_straight_onto_its_own_channel_off_the_grid(self):
        relationships = relationships_from(
            [
                "R-0008 P-0009 parent-of P-0000",
                "R-0016 P-0009 grandparent-of P-0003",
                "R-0017 P-0000 half-sibling-of P-0004",
            ]
        )

        _, routes = routed(persons_of(relationships), relationships)

        points = paths_by_id(routes, relationships)["R-0016"]
        self.assertEqual(len({x for x, _ in points}), 1, points)
        self.assertEqual(points[0][1], routes.positions["P-0009"][1] + 24)
        self.assertEqual(points[-1][1], routes.positions["P-0003"][1] - 24)


if __name__ == "__main__":
    unittest.main()
