"""tools/coordinator/metrics.py: udział czerwonych przebiegów poza piaskownicą i decyzje odstępstwo/ponowienie (K-100, PT-422).

Dzienniki kart i dziennik interwencji są syntetyczne, w katalogu tymczasowym; XDG_STATE_HOME wskazuje na niego,
więc narzędzie nie dotyka prawdziwych dzienników użytkownika.
"""
import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(REPO, "tools", "coordinator", "metrics.py")
HEADER = "czas\tkarta\tkto\trodzaj\tco\tprzyczyna\tpotrzebne\tco_by_usunelo\n"


def outside(step, ts, exit_code, command="make test-changed BASE=abc"):
    return [{"ts": ts, "type": "outside.started", "step": step},
            {"ts": ts[:-2] + "30", "type": "outside.finished", "step": step, "command": command, "exit": exit_code}]


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = os.path.join(self._tmp.name, "state")
        self.board = os.path.join(self.state, "tide", "cardflow", "board")
        os.makedirs(self.board)
        self.log = os.path.join(self._tmp.name, "dziennik.tsv")

    def write_card(self, events):
        with open(os.path.join(self.board, "K-1.jsonl"), "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")

    def write_log(self, rows):
        with open(self.log, "w", encoding="utf-8") as fh:
            fh.write(HEADER)
            for ts, kind, what in rows:
                fh.write(f"{ts}\tK-1\tkoordynator\t{kind}\t{what}\t\t?\t\n")

    def run_tool(self):
        env = dict(os.environ, XDG_STATE_HOME=self.state)
        done = subprocess.run(["/usr/bin/python3", TOOL, "--since", "2026-11-10", "--log", self.log],
                              capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = {}
        for line in done.stdout.splitlines():
            if line[:4] == "2026":
                rows[line[:10]] = [c.strip() for c in line.split("|")]
        return rows, done.stdout

    def test_red_share_all_and_without_intentional_red(self):
        ev = []
        ev += outside("PT-1-1", "2026-11-11T10:00:00", 1)          # padło
        ev += outside("PT-1-2", "2026-11-11T11:00:00", 0)          # przeszło
        # krok kończy się prośbą o przebieg (powód zapisany w dzienniku przed outside.*), potem przebieg
        ev.append({"ts": "2026-11-11T11:59:00", "type": "step.finished", "step": "PT-1-3", "reason": "czerwony test przed poprawką"})
        ev += outside("PT-1-3", "2026-11-11T12:00:00", 1)          # celowo czerwony
        ev += outside("PT-1-4", "2026-11-11T13:00:00", 0, "ls")    # nie HEAVY — nie liczy się
        self.write_card(ev)
        self.write_log([])
        rows, out = self.run_tool()
        row = rows["2026-11-11"]
        self.assertIn("67% (2/3)", row[-3])       # wszystkie: 2 z 3
        self.assertIn("50% (1/2)", row[-2])       # bez celowo czerwonych: 1 z 2
        self.assertIn("<35%", out)
        self.assertIn("<10 decyzji", out)

    def test_day_without_runs_shows_decisions_and_dash(self):
        self.write_card([])
        self.write_log([("2026-11-12T09:00", "decyzja", "przyjęte odstępstwo: test lifecycle pada pod obciążeniem")])
        rows, _ = self.run_tool()
        row = rows["2026-11-12"]
        self.assertTrue(row[-3].startswith("-"))
        self.assertEqual(row[-1], "1")

    def test_decision_rule_counts_only_deviation_decisions(self):
        self.write_card([])
        self.write_log([
            ("2026-11-11T08:00", "decyzja", "odstępstwo od testów: ponowienie 21/21 OK na tym samym HEAD"),   # tak
            ("2026-11-11T08:10", "zgoda", "ponowienie przebiegu test_daemon_lifecycle, osobno 3/3 OK"),      # tak
            ("2026-11-11T08:20", "zgoda", "zgoda „run” na skrypt pomiarowy build/x.sh"),                   # zwykła zgoda
            ("2026-11-11T08:30", "decyzja", "podział karty K-36, nowa K-99"),                               # bez testów
            ("2026-11-11T08:40", "interwencja", "odstępstwo testów: ponowienie przebiegu"),                 # zły rodzaj
            ("2026-11-11T08:50", "decyzja", "plan zatwierdzony, ponowienie planowania po review"),          # ponowienie, ale nie testów
        ])
        rows, _ = self.run_tool()
        self.assertEqual(rows["2026-11-11"][-1], "2")

    def test_compound_kinds_and_plural_unstable_wording(self):
        self.write_card([])
        self.write_log([
            ("2026-11-11T09:00", "zgoda/decyzja", "ponowienie padniętych modułów testów"),                   # tak
            ("2026-11-11T09:10", "błąd+decyzja", "porażka testu, odstępstwo przyjęte"),                      # tak
            ("2026-11-11T09:20", "decyzja", "znane niestabilne testy: upoważnienie z wyprzedzeniem"),        # tak
            ("2026-11-11T09:30", "odpowiedź", "odstępstwo przyjęte: test pada pod obciążeniem"),             # poza regułą
            ("2026-11-11T09:40", "odmowa", "odstępstwo testów odrzucone, ponowienie"),                       # poza regułą
        ])
        rows, _ = self.run_tool()
        self.assertEqual(rows["2026-11-11"][-1], "3")

    def test_missing_log_is_read_only_and_not_fatal(self):
        self.write_card(outside("PT-1-1", "2026-11-11T10:00:00", 0))
        before = os.listdir(self.board)
        rows, _ = self.run_tool()
        self.assertEqual(rows["2026-11-11"][-1], "0")
        self.assertEqual(os.listdir(self.board), before)


if __name__ == "__main__":
    unittest.main()
