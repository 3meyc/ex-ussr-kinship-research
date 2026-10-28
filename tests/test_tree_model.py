"""Graph model of the kinship tree on plain register rows."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tree_model import IdConflict, IdConflictError, build_graph


class RegisterText(unittest.TestCase):
    def test_register_text_is_normalised_to_nfc(self):
        decomposed = "Пе\u0308тр Ро\u0301щин"  # е + combining diaeresis, о + combining acute
        person = {"person_id": "P-0001", "preferred_label": decomposed, "living_status": "deceased",
                  "places": decomposed}
        graph = build_graph([person], [], shareable=False, current_year=2026)
        composed = "П\u0451тр Ро\u0301щин"  # ё is one code point; о + acute has no composed form
        node = graph.nodes[0]
        self.assertEqual(node.label, composed)
        self.assertEqual(dict(node.fields)["places"], composed)


class IdsAfterCleaning(unittest.TestCase):
    def test_relationship_ids_equal_after_cleaning_refuse_the_graph(self):
        persons = [{"person_id": "P-0001", "living_status": "deceased"},
                   {"person_id": "P-0002", "living_status": "deceased"}]
        relationship = {"person_a_id": "P-0001", "relationship_type": "spouse-of", "person_b_id": "P-0002"}
        relationships = [{**relationship, "relationship_id": "R-0001"},
                         {**relationship, "relationship_id": "R-0001\x00"}]
        with self.assertRaises(IdConflictError) as raised:
            build_graph(persons, relationships, shareable=False, current_year=2026)
        self.assertEqual(raised.exception.conflicts,
                         (IdConflict("relationship_id", "R-0001", ("R-0001", "R-0001\x00")),))


class LivingStatusAfterCleaning(unittest.TestCase):
    def test_status_changed_by_cleaning_keeps_the_person_living(self):
        person = {"person_id": "P-0001", "preferred_label": "Анна", "living_status": "\x01deceased",
                  "birth_estimate": "1990"}
        graph = build_graph([person], [], shareable=True, current_year=2026)
        self.assertEqual((graph.nodes[0].hidden, graph.nodes[0].label, graph.hidden_count), (True, "P-0001", 1))


if __name__ == "__main__":
    unittest.main()
