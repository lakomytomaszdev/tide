"""Budowa drzewa mapy gałęzi w oknie Worktrees (K-99, PT-472) na dwóch zestawach wejścia.

- (a) zapisany `branchmap.json` z #21 (prawdziwa odpowiedź usługi, ≥ 40 gałęzi),
- (b) mapa 500 węzłów (limit maksymalny) z projekcji PT-387 (`branch_map(journal, board, slug, limit=500)`) na dzienniku
  z prawdziwych typów zdarzeń — zestaw jawnie syntetyczny, służy wyłącznie wydajności (scripts/k99/measure_branch_tree.py).

Sondę uruchamia skrypt pomiaru w trybie `--smoke` (okno offscreen, prawdziwy `Backend`, odpowiedź mapy podana z pętli
zdarzeń jak odpowiedź D-Bus), a test sprawdza: wywołanie odczytu mapy wraca, zanim drzewo powstanie (0 węzłów, „Czytam
mapę…”); po odpowiedzi drzewo ma tyle węzłów kart i ticketów, ile odpowiedź, a „pokazano N z M” zgadza się z `truncated`;
czas od podania odpowiedzi do zbudowania drzewa jest poniżej LUŹNEGO progu regresji. Próg chroni przed regresją rzędu
wielkości — kryterium „mapa < 1 s” (mediana z ≥ 5 powtórzeń) mierzy polecenie `outside`, nie ten test.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

from . import REPO
from .test_window import QML_PROBLEMS
from scripts.k99 import measure_branch_tree as tree

SCRIPT = os.path.join(REPO, "scripts", "k99", "measure_branch_tree.py")
REGRESSION_SECONDS = {"branchmap": 2.0, "nodes500": 3.0}


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class TreeBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1")
        run = subprocess.run(["/usr/bin/python3", SCRIPT, "--smoke", "--repeats", "3"], env=env, capture_output=True,
                             text=True, timeout=240)
        cls.stderr = run.stderr
        if run.returncode != 0:
            raise AssertionError(f"pomiar --smoke zakończył się kodem {run.returncode}: {run.stderr[-3000:]}"
                                 f"\n{run.stdout[-3000:]}")
        cls.out = json.loads(run.stdout)
        cls.replies = {"branchmap": tree.saved_reply(), "nodes500": tree.synthetic_reply()}
        for name, data in cls.out["datasets"].items():
            print(f"[k99 tree build] {name}: najlepszy {data['seconds']['best']:.3f} s, mediana "
                  f"{data['seconds']['median']:.3f} s, najgorszy {data['seconds']['worst']:.3f} s "
                  f"({data['nodes']} węzłów, {data['mapBranches']} z {data['mapTotal']} gałęzi)")

    def test_no_qml_problems_in_the_log(self):
        self.assertIsNone(re.search(QML_PROBLEMS, self.stderr), self.stderr[-3000:])

    def test_the_read_returns_before_the_tree_exists_and_all_build_checks_pass(self):
        for name in tree.DATASETS:
            with self.subTest(name):
                checks = self.out["datasets"][name]["checks"]
                self.assertEqual(checks, {k: True for k in checks}, checks)
                self.assertTrue(checks["callReturnedBeforeTree"], "wywołanie odczytu mapy wróciło przed drzewem")

    def test_saved_map_builds_one_node_per_card_ticket_and_base(self):
        reply, data = self.replies["branchmap"], self.out["datasets"]["branchmap"]
        self.assertGreaterEqual(tree.branch_count(reply), 40)
        self.assertEqual(data["nodes"], len(reply["nodes"]))
        self.assertEqual(data["kinds"], {k: sum(1 for n in reply["nodes"] if n["kind"] == k)
                                         for k in ("base", "card", "ticket", "unknown-base")})
        self.assertFalse(reply["truncated"])
        self.assertEqual(data["shown"], [], "mapa nieobcięta: bez „pokazano N z M”")

    def test_500_nodes_from_the_projection_say_shown_n_of_m(self):
        reply, data = self.replies["nodes500"], self.out["datasets"]["nodes500"]
        self.assertEqual(tree.branch_count(reply), 500)
        self.assertTrue(reply["truncated"])
        self.assertEqual(data["nodes"], len(reply["nodes"]))
        self.assertEqual(data["kinds"]["card"] + data["kinds"]["ticket"], 500)
        self.assertEqual(data["shown"], [f"pokazano 500 z {reply['total']}"])

    def test_build_time_is_below_the_loose_regression_threshold(self):
        for name, limit in REGRESSION_SECONDS.items():
            with self.subTest(name):
                worst = self.out["datasets"][name]["seconds"]["worst"]
                self.assertLess(worst, limit, f"{name}: najgorszy czas budowy {worst:.3f} s ≥ progu {limit} s")


if __name__ == "__main__":
    unittest.main()
