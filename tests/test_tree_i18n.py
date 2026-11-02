"""Vocabulary of the kinship tree and the choice of the build language."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tree_i18n import LanguageChoice, choose_language, load_vocabulary


class VocabularyFile(unittest.TestCase):
    def test_shipped_vocabulary_has_every_language(self):
        vocabulary = load_vocabulary()  # raises if a key misses ru, uk or en
        self.assertEqual(vocabulary.texts("legend"), {"ru": "Легенда", "uk": "Легенда", "en": "Legend"})

    def test_key_without_a_language_or_with_empty_text_is_refused(self):
        entries = {
            "ok": {"ru": "а", "uk": "б", "en": "c"},
            "no_en": {"ru": "а", "uk": "б"},
            "blank_uk": {"ru": "а", "uk": "  ", "en": "c"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tree.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_vocabulary(path)
        message = str(caught.exception)
        self.assertIn("no_en", message)
        self.assertIn("blank_uk", message)
        self.assertNotIn("ok,", message)


class BuildLanguage(unittest.TestCase):
    def test_choice_order(self):
        cases = {
            "--lang wins over the intake": (("uk", "en"), LanguageChoice("uk", None)),
            "intake language": ((None, "en"), LanguageChoice("en", None)),
            "intake with region and case": ((None, "UK-ua"), LanguageChoice("uk", None)),
            "no intake value": ((None, None), LanguageChoice("ru", None)),
            "empty intake value": ((None, " "), LanguageChoice("ru", None)),
            "auto falls back to ru": ((None, "auto"), LanguageChoice("ru", "auto")),
            "unsupported falls back to ru": ((None, "pl"), LanguageChoice("ru", "pl")),
        }
        for name, (args, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(choose_language(*args), expected)


if __name__ == "__main__":
    unittest.main()
