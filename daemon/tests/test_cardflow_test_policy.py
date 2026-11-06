"""PT-243: kartki rozdzielają testy ticketu od sprawdzenia całej karty."""
import os
import subprocess
import sys
import tempfile
import unittest

from tide_daemon.cardflow import handoff
from tide_daemon.cardflow.state import FlowState, ticket_of


class TestPolicyCards(unittest.TestCase):
    base = "a" * 40

    def test_ticket_and_fixes_override_old_plan_requirement(self):
        common = dict(card_id="K-62", title="Testy", description="Pełny `make test` przed oddaniem ticketu.",
                      ticket="PT-243", branch="ticket/pt-243-tests", worktree="/w", out_dir="/o",
                      test_base=self.base)
        cards = [handoff.ticket_card(**common, project="p", card_branch="card/k-62"),
                 handoff.fix_card(**common, round_no=1, review="MAJOR: popraw test")]
        for text in cards:
            with self.subTest(kind=text.splitlines()[0]):
                self.assertIn(f"make test-changed BASE={self.base}", text)
                self.assertIn("Pełny `make test` nie jest kryterium oddania ticketu ani jego review", text)
                self.assertIn("Ta zasada zastępuje wymóg pełnego `make test`", text)
                self.assertIn("K-37, K-38 i K-55", text)
                self.assertIn("Zapisz polecenie i wynik w notce", text)

    def test_reviewer_checks_changed_tests_without_demanding_full_run(self):
        text = handoff.review_card(card_id="K-62", title="Testy", description="d", ticket="PT-243",
                                   round_no=1, card_branch="card/k-62", ticket_branch="ticket/pt-243",
                                   worktree="/w", out_dir="/o", diff="d", note="n",
                                   rules="Pełny `make test` przed oddaniem ticketu.", test_base=self.base)
        self.assertIn(f"make test-changed BASE={self.base}", text)
        self.assertIn("sprawdź w notce, że `make test-changed`", text)
        self.assertIn("nie żądaj osobnego pełnego `make test`", text)

    def test_close_requires_full_run_before_summary_for_both_check_modes(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                text = handoff.close_card(card_id="K-62", title="Testy", description="d", worktree="/w",
                                          out_dir="/o", tickets=[], manual=manual)
                self.assertIn("Przed napisaniem podsumowania uruchom raz pełny `make test`", text)
                self.assertIn("w worktree gałęzi karty, po scaleniu ticketów", text)
                self.assertIn("Jeśli testy nie przejdą, nie zgłaszaj done", text)
                self.assertIn("zapisz wynik error", text)
                self.assertIn("denied albo outside", text)
                self.assertIn("Nie scalaj do gałęzi głównej", text)
                self.assertEqual("Dodaj sekcję „Co przetestować ręcznie i jak”" in text, manual)

    def test_ticket_base_comes_from_its_branch_creation_not_the_card(self):
        st = FlowState()
        st.results = {
            ("card-branch", "card/k-62"): dict(ok=True, branch="card/k-62", worktree="/c", base="b" * 40),
            ("ticket-branch", "PT-242"): dict(ok=True, ticket="PT-242", branch="ticket/pt-242",
                                             worktree="/other", base="c" * 40),
            ("ticket-branch", "PT-243"): dict(ok=True, ticket="PT-243", branch="ticket/pt-243",
                                             worktree="/w", base=self.base),
        }
        self.assertEqual(ticket_of(st, "PT-243")["base"], self.base)
        self.assertEqual(ticket_of(st, "PT-242")["base"], "c" * 40)


if __name__ == "__main__":
    unittest.main()


class OldCardBranchTest(unittest.TestCase):
    """06.11: gałęzie kart sprzed `make test-changed` (K-37, K-38) dostają dotychczasową zasadę pełnego `make test`."""

    def tree(self, makefile):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.mkdir(os.path.join(tmp.name, "mk"))
        with open(os.path.join(tmp.name, "mk", "tests.mk"), "w", encoding="utf-8") as fh:
            fh.write(makefile)
        return tmp.name

    def cards(self, worktree):
        common = dict(card_id="K-38", title="Pudełko", description="d", ticket="PT-189", worktree=worktree,
                      out_dir="/o", test_base="b" * 40)
        return [handoff.ticket_card(**common, project="p", branch="t", card_branch="card/k-38"),
                handoff.fix_card(**common, branch="t", round_no=1, review="MAJOR"),
                handoff.review_card(**common, round_no=1, card_branch="card/k-38", ticket_branch="t",
                                    diff="d", note="n", rules="")]

    def test_branch_without_target_keeps_full_run_and_forbids_porting(self):
        for text in self.cards(self.tree("test:\n\t@true\ntest-daemon:\n\t@true\n")):
            with self.subTest(kind=text.splitlines()[0]):
                self.assertNotIn("make test-changed BASE=", text)
                self.assertIn("pełny `make test`", text)
                self.assertIn("Nie przenoś infrastruktury testów z master", text)

    def test_branch_with_target_uses_changed_tests(self):
        for text in self.cards(self.tree("test:\n\t@true\ntest-changed:\n\t@true\n")):
            with self.subTest(kind=text.splitlines()[0]):
                self.assertIn("make test-changed BASE=" + "b" * 40, text)


class OldRunnerJobsTest(unittest.TestCase):
    """06.11: gałęzie sprzed wspólnej puli testów brały po 14 procesów — polecenie poza piaskownicą dostaje JOBS=4."""

    def tree(self, runner):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if runner is not None:
            os.mkdir(os.path.join(tmp.name, "tools"))
            with open(os.path.join(tmp.name, "tools", "test_runner.py"), "w", encoding="utf-8") as fh:
                fh.write(runner)
        return tmp.name

    def test_pool_detection(self):
        from tide_daemon.cardflow.engine import test_pool_available
        self.assertFalse(test_pool_available(self.tree("def execute(): pass\n")))
        self.assertTrue(test_pool_available(self.tree("class MachineSlots:\n    pass\n")))
        self.assertTrue(test_pool_available(self.tree(None)), "projekt bez uruchamiacza Tide — bez zmian")


class RerunFailedTest(unittest.TestCase):
    """06.11 (etap A): ponowienie bez zmian kodu uruchamia tylko padnięte moduły — w każdej gałęzi karty."""

    def test_every_card_kind_explains_rerun_of_failed_modules_only(self):
        for makefile in ("test:\n\t@true\ntest-changed:\n\t@true\n", "test:\n\t@true\n"):
            for text in OldCardBranchTest.cards(None, OldCardBranchTest.tree(self, makefile)):
                with self.subTest(makefile=makefile, kind=text.splitlines()[0]):
                    self.assertIn("nie powtarzaj całego zestawu", text)
                    self.assertIn("ten sam HEAD, czysty `git status`", text)

    def test_runner_repository_gets_exact_command_other_technologies_the_rule(self):
        own = self.tree_with_runner()
        self.assertIn("/usr/bin/python3 -m unittest discover -t . -s <katalog modułu> -p <plik modułu>",
                      handoff.rerun_failed(own))
        other = OldCardBranchTest.tree(self, "test:\n\t@dotnet test\n")
        text = handoff.rerun_failed(other)
        self.assertNotIn("unittest", text)
        self.assertIn("narzędziem testów projektu z filtrem", text)

    def tree_with_runner(self):
        root = OldCardBranchTest.tree(self, "test:\n\t@true\ntest-changed:\n\t@true\n")
        os.mkdir(os.path.join(root, "tools"))
        open(os.path.join(root, "tools", "test_runner.py"), "w").close()
        return root

    def test_reviewer_accepts_rerun_only_on_the_same_head(self):
        review = OldCardBranchTest.cards(None, OldCardBranchTest.tree(self, "test:\n\t@true\ntest-changed:\n\t@true\n"))[2]
        self.assertIn("ponowienie samych padniętych modułów uznaj tylko przy tym samym HEAD", review)

    def test_documented_command_runs_a_nested_test_module(self):
        # Wszystkie katalogi testów repozytorium są pakietami (test_every_test_directory_is_a_package).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "apps", "x", "tests"))
        for part in ("apps", "apps/x", "apps/x/tests"):
            open(os.path.join(tmp.name, part, "__init__.py"), "w").close()
        with open(os.path.join(tmp.name, "apps", "x", "tests", "test_probe.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-t", ".", "-s", "apps/x/tests",
                              "-p", "test_probe.py"], cwd=tmp.name, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Ran 1 test", run.stderr)

    def test_every_test_directory_is_a_package(self):
        """Bez `__init__.py` polecenie `unittest discover -t . -s <katalog>` nie zaimportuje modułu."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0, os.path.join(root, "tools"))
        self.addCleanup(sys.path.remove, os.path.join(root, "tools"))
        import test_runner
        dirs = sorted({d for pairs in test_runner.SUITES.values() for d, _ in pairs})
        self.assertEqual([d for d in dirs if not os.path.isfile(os.path.join(root, d, "__init__.py"))], [])
