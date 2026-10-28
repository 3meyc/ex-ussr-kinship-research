"""Graph model of the kinship tree on plain register rows."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tree_model import build_graph


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


if __name__ == "__main__":
    unittest.main()
