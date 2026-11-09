"""Kontrakty atrapy planującego: dodanie ticketu zachowuje kartki wcześniej zatwierdzonych ticketów."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from . import flowfakes


class LocalFailureFakeTest(unittest.TestCase):
    def test_local_failure_waits_until_the_test_has_disconnected_the_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / "out"
            fake = flowfakes.install(str(root / "bin"))["claude"]
            env = {**os.environ, "HOME": folder, "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Test",
                   "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_NAME": "Test",
                   "GIT_COMMITTER_EMAIL": "test@example.invalid"}
            subprocess.run(["git", "init", "-q", folder], env=env, check=True)
            proc = subprocess.Popen([sys.executable, fake, "--session-id", "local-failure"],
                                    cwd=root, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            try:
                proc.stdin.write(f"ATRAPA: blad-local\nPlik wyniku: {out / 'result.json'}\n")
                proc.stdin.close()
                deadline = time.monotonic() + 5
                while not (out / "fake-call.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue((out / "fake-call.json").exists(), "Atrapa nie opublikowała startu")
                # Uruchomienie chronionego Git może trwać dłużej niż dawne 0,5 s.
                # Sam start procesu nie uprawnia atrapy do zgłoszenia awarii.
                with self.assertRaises(subprocess.TimeoutExpired):
                    proc.wait(timeout=1)
                self.assertFalse((out / "result.json").exists())
                (out / "release-agent").touch()
                self.assertEqual(proc.wait(timeout=5), 0)
                result = json.loads((out / "result.json").read_text())
                self.assertEqual((result["result"], result["reason"]), ("error", "awaria testowa"))
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                proc.stderr.close()


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
