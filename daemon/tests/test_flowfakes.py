"""Kontrakty atrapy planującego: dodanie ticketu zachowuje kartki wcześniej zatwierdzonych ticketów."""
import json
import os
import subprocess
import tempfile
import unittest

from . import flowfakes


class RevisionFakeTest(unittest.TestCase):
    def test_adding_ticket_keeps_existing_bodies_for_local_and_tracker_ids(self):
        for prefix in ("PT", "LOG"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as folder:
                fake = flowfakes.install(os.path.join(folder, "bin"))["claude"]
                out = os.path.join(folder, "out")
                rows = [{"workId": f"{prefix}-{n}", "title": f"Ticket {n}", "executor": "Sonnet",
                         "reviewer": "Opus", "note": "", "class": "standard", "blockers": [],
                         "body": f"# Ticket {n}\n\n- [ ] Wynik {n}.\n"} for n in (1, 2)]
                prompt = ("Popraw propozycję planu karty K-1\nPoprzednia propozycja: " +
                          json.dumps({"rows": rows}) + "\nDodaj ticket z poprawką\nPlik wyniku: " +
                          os.path.join(out, "result.json") + "\n")
                proc = subprocess.run([fake, "--session-id", "test"], input=prompt, capture_output=True,
                                      text=True, cwd=folder, env={**os.environ, "HOME": folder}, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                for row in rows:
                    with open(os.path.join(out, "tickets", row["workId"] + ".md"), encoding="utf-8") as fh:
                        self.assertEqual(fh.read(), row["body"])
                self.assertTrue(os.path.isfile(os.path.join(out, "tickets", "3.md")))
