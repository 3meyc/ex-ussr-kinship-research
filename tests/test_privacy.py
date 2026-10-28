"""Living-person privacy rule on plain person-register rows."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from privacy import counts_as_living


class CountsAsLivingTest(unittest.TestCase):
    def test_living_rule_table(self):
        # current_year 2026: births after 1916 count as living.
        cases = [
            ("living", "1950", "", True),
            ("living", "1950", "2001", True),
            ("deceased", "1990", "", False),
            ("probably-deceased", "", "", False),
            ("unknown", "1917", "", True),
            ("", "1916", "", False),
            ("probably-living", "", "", True),
            ("unknown", "ок. 1905–1912", "", False),
            ("unknown", "1890-е или 1920", "", True),
            ("", "неизвестно", "", True),
            ("Жив", "1950", "", True),
            ("unknown", "1975", "неизвестно", True),
            (" Deceased ", "", "", False),
            ("living", "1950", "ок. 2001", True),
            ("unknown", "1985", "после 2015", True),
            ("unknown", "1985", "жива на 2024", True),
            ("unknown", "1985", "ок. 2001", False),
            ("living", "1950", "   ", True),
            ("  ", "", "", True),
        ]
        for status, birth, death, expected in cases:
            row = {"living_status": status, "birth_estimate": birth, "death_estimate": death}
            with self.subTest(status=status, birth=birth, death=death):
                self.assertIs(counts_as_living(row, 2026), expected)

    def test_qualified_death_years_are_not_death_years(self):
        # A year after «после», «не ранее», «позже», «жив», «жива» (any case, optional «на»)
        # says the person was alive then; any other year in the text is a death year.
        cases = [
            ("unknown", "Не ранее 2015", True),
            ("", "позже  2010", True),
            ("unknown", "ЖИВ НА 2020", True),
            ("unknown", "после 2015 или 2001", False),
            ("unknown", "ум. 2001", False),
            ("probably-living", "2001", True),
        ]
        for status, death, expected in cases:
            row = {"living_status": status, "birth_estimate": "1985", "death_estimate": death}
            with self.subTest(status=status, death=death):
                self.assertIs(counts_as_living(row, 2026), expected)

    def test_missing_fields_read_as_empty(self):
        self.assertIs(counts_as_living({"person_id": "P-0001"}, 2026), True)


if __name__ == "__main__":
    unittest.main()
