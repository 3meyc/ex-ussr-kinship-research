"""Тесты init_case.py: сброс шаблонов через --force не теряет данные дела.

Запуск: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ASSETS = SCRIPTS.parent / "assets"
PERSON_ROW = "P-0001,Иван Петрович Коваленко,deceased,,,,,,,,,,,,,\n"


def init(case: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "init_case.py"), "--case-dir", str(case),
         "--research-question", "q", *extra],
        capture_output=True, text=True, encoding="utf-8", check=False,   # tests assert the exit code
    )


class ForceKeepsCaseData(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name) / "case"
        self.assertEqual(init(self.case).returncode, 0)

    def test_force_backs_up_a_filled_register_before_resetting_it(self):
        register = self.case / "person-register.csv"
        with register.open("a", encoding="utf-8") as handle:
            handle.write(PERSON_ROW)
        filled = register.read_text(encoding="utf-8")

        proc = init(self.case, "--force")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(register.read_bytes(), (ASSETS / "person-register.csv").read_bytes())
        backups = sorted(self.case.glob("person-register.csv.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), filled)
        self.assertIn(backups[0].name, proc.stdout)

    def test_force_makes_no_backup_of_files_still_equal_to_their_template(self):
        proc = init(self.case, "--force")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        backed_up = sorted(p.name.split(".")[0] for p in self.case.glob("*.bak"))
        self.assertEqual(backed_up, ["case-intake"])          # the only file init itself filled in

    def test_without_force_an_existing_case_is_refused_and_left_as_is(self):
        register = self.case / "person-register.csv"
        with register.open("a", encoding="utf-8") as handle:
            handle.write(PERSON_ROW)
        before = register.read_bytes()

        proc = init(self.case)

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(register.read_bytes(), before)
        self.assertEqual(list(self.case.glob("*.bak")), [])


if __name__ == "__main__":
    unittest.main()
