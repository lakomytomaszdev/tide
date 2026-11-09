"""PT-284 (K-69 #7): kartka konfliktu i linia planowania z efektywnego kontraktu testów.

Testy jednostkowe publicznych funkcji kartek (`conflict_card`, `current_tests`, `plan_tests_line`,
`with_plan_tests`) — bez usługi i D-Bus. Decyzję `effective_tests` sprawdza test_cardflow_effective_tests.py.
"""
import re
import shlex
import unittest

from tide_daemon.cardflow import handoff
from tide_daemon.cardflow.testpolicy import effective_tests

from .test_cardflow_effective_tests import BASE, CHANGED, DETECTED_LINE, DOTNET, LEGACY, NO_MAKEFILE, tree

# Polecenie `make` w kartce (w backtickach); słowo „make” w opisie źródła zasady ogólnej nie jest poleceniem.
MAKE_COMMAND = re.compile(r"`make\b")


def conflict(policy, worktree="/w") -> str:
    return handoff.conflict_card(card_id="K-69", title="Kontrakt testów", description="Opis karty.",
                                 ticket="PT-279", card_branch="card/k-69", ticket_branch="ticket/pt-279",
                                 worktree=worktree, out_dir="/o", error="konflikt w handoff.py", policy=policy)


class ConflictCardTest(unittest.TestCase):
    def policy(self, files, contract, approved, base=BASE):
        worktree = tree(self, files)
        return worktree, effective_tests(contract, worktree, approved, base)

    def test_approved_dotnet_contract_gives_changed_with_ticket_base_and_no_make(self):
        worktree, policy = self.policy(CHANGED, DOTNET, True)
        text = conflict(policy, worktree)
        command = "dotnet test Foo.Tests --filter Changed=" + shlex.quote(BASE)
        self.assertIn(f"uruchom w worktree karty `{command}`", text)
        self.assertIn("Baza w poleceniu to przypięty punkt startu scalanego ticketu", text)
        self.assertIn("Źródło polecenia testów ticketu: zatwierdzony kontrakt testów projektu", text)
        self.assertNotRegex(text, MAKE_COMMAND)
        self.assertNotIn("make test", text)
        self.assertIn(f"Wymagany `{command}` zgłoś od razu jako polecenie poza piaskownicą", text)
        self.assertIn(handoff.RERUN_GENERIC, text)

    def test_project_without_contract_and_makefile_gives_general_rule_without_make(self):
        worktree, policy = self.policy(NO_MAKEFILE, None, False)
        text = conflict(policy, worktree)
        self.assertIn("projekt nie podaje polecenia testów zmian", text)
        self.assertIn("według zasad projektu (CLAUDE.md/AGENTS.md) i zapisz polecenie oraz wynik w notce", text)
        self.assertIn(f"Wymagany {handoff.RULES_SANDBOX} zgłoś od razu", text)
        self.assertNotRegex(text, MAKE_COMMAND)
        self.assertNotIn(handoff.UNAPPROVED_LINE, text)

    def test_plasma_tide_without_contract_gives_detected_test_changed_with_base(self):
        worktree, policy = self.policy(CHANGED, None, False)
        text = conflict(policy, worktree)
        self.assertIn(f"`make test-changed BASE={BASE}`", text)
        self.assertIn("BASE to przypięty punkt startu scalanego ticketu na gałęzi karty", text)
        self.assertIn(DETECTED_LINE, text)
        self.assertIn("`make test-cardflow`", text)

    def test_legacy_branch_requires_full_make_test_and_result(self):
        worktree, policy = self.policy(LEGACY, None, False)
        self.assertTrue(policy.legacy)
        text = conflict(policy, worktree)
        self.assertIn("ta gałąź karty nie ma celu `make test-changed`", text)
        self.assertIn("pełny `make test` w worktree karty i zapisz polecenie oraz wynik w notce", text)
        self.assertNotIn("make test-changed BASE", text)

    def test_unapproved_contract_uses_detection_or_general_rule_and_says_so(self):
        for files, changed in ((CHANGED, f"`make test-changed BASE={BASE}`"),
                               (NO_MAKEFILE, "projekt nie podaje polecenia testów zmian")):
            worktree, policy = self.policy(files, DOTNET, False)
            text = conflict(policy, worktree)
            with self.subTest(files=sorted(files)):
                self.assertEqual(text.count(handoff.UNAPPROVED_LINE), 1)
                self.assertIn(changed, text)
                self.assertNotIn("dotnet", text)

    def test_unknown_base_stays_unexpanded_and_is_described(self):
        worktree, policy = self.policy(CHANGED, None, False, base="")
        text = conflict(policy, worktree)
        self.assertIn("`make test-changed BASE={base}`", text)
        self.assertIn("`{base}` to przypięty punkt startu scalanego ticketu", text)
        self.assertIn("podstaw jego SHA przed uruchomieniem", text)

    def test_result_contract_and_merge_instructions_stay(self):
        text = conflict(self.policy(CHANGED, None, False)[1])
        self.assertTrue(text.startswith("Rozwiąż konflikt scalenia ticketu PT-279 do karty K-69."))
        self.assertIn("Zatwierdzony ticket jest na gałęzi ticket/pt-279; przyczyna: konflikt w handoff.py.", text)
        self.assertIn("zapisz commit scalenia z dwoma rodzicami", text)
        self.assertIn("Plik wyniku: /o/result.json", text)
        self.assertNotIn("uruchom potrzebne testy", text)

    def test_resume_section_uses_conflict_text(self):
        worktree, policy = self.policy(CHANGED, DOTNET, True)
        section = handoff.current_tests(policy, kind="conflict", worktree=worktree)
        self.assertIn(handoff.CURRENT_TESTS_HEAD, section)
        self.assertIn("Testy po scaleniu:", section)
        self.assertNotIn("Testy ticketu (także po poprawkach)", section)
        self.assertEqual(handoff.strip_current_tests(conflict(policy, worktree) + section),
                         conflict(policy, worktree))


class ConflictMatrixTest(unittest.TestCase):
    """Kartka konfliktu × zatwierdzony, niezatwierdzony, wykryty, `legacy`, brak."""

    CASES = {
        "approved": (CHANGED, DOTNET, True),
        "unapproved": (NO_MAKEFILE, DOTNET, False),
        "detected": (CHANGED, None, False),
        "legacy": (LEGACY, None, False),
        "none": (NO_MAKEFILE, None, False),
    }

    def test_every_variant_requires_tests_after_merge_without_missing_command(self):
        for name, (files, contract, approved) in self.CASES.items():
            worktree = tree(self, files)
            policy = effective_tests(contract, worktree, approved, BASE)
            text = conflict(policy, worktree)
            with self.subTest(case=name):
                self.assertIn("Testy po scaleniu:", text)
                self.assertIn("zapisz polecenie oraz wynik w notce", text)
                self.assertIn(handoff.CONFLICT_DONE, text)
                self.assertIn("zgłoś od razu jako polecenie poza piaskownicą", text)
                self.assertRegex(text, r"Źródło polecenia testów ticketu: ")
                self.assertEqual("zatwierdzony kontrakt testów projektu" in text, name == "approved")
                self.assertEqual(handoff.UNAPPROVED_LINE in text, name == "unapproved")
                self.assertEqual(MAKE_COMMAND.search(text) is not None, name in ("detected", "legacy"))
                if name in ("detected", "approved"):
                    self.assertIn(shlex.quote(BASE), text)


class PlanTestsLineTest(unittest.TestCase):
    """Linia planowania i review planu × `profile`, `detected`, `rules`."""

    def line(self, files, contract, approved):
        policy = effective_tests(contract, tree(self, files), approved, "")
        return policy, handoff.plan_tests_line(policy)

    def test_profile_source(self):
        policy, line = self.line(CHANGED, DOTNET, True)
        self.assertEqual(policy.source, "profile")
        self.assertTrue(line.startswith(handoff.PLAN_TESTS_PREFIX + "zatwierdzony kontrakt w profilu projektu"))
        self.assertIn("testy ticketu `dotnet test Foo.Tests --filter Changed={base}`", line)
        self.assertIn("`{base}` to przypięty punkt startu ticketu", line)
        self.assertIn("pełne testy przy domknięciu karty `dotnet test Foo.sln`", line)
        self.assertNotIn("make", line)

    def test_detected_source(self):
        policy, line = self.line(CHANGED, None, False)
        self.assertEqual(policy.source, "detected")
        self.assertIn("wykryte z Makefile", line)
        self.assertIn("`make test-changed BASE={base}`", line)
        self.assertIn("`make test`", line)

    def test_rules_source(self):
        policy, line = self.line(NO_MAKEFILE, None, False)
        self.assertEqual(policy.source, "rules")
        self.assertIn("nie ma zastosowanego kontraktu testów ani celów make", line)
        self.assertIn("kartki ticketów odeślą do zasad testów projektu (CLAUDE.md/AGENTS.md)", line)
        self.assertNotRegex(line, MAKE_COMMAND)

    def test_unapproved_contract_is_mentioned(self):
        _, line = self.line(NO_MAKEFILE, DOTNET, False)
        self.assertIn("nie jest zatwierdzony i nie jest stosowany", line)
        self.assertNotIn("dotnet", line)

    def test_one_line_replaced_on_resume(self):
        for files, contract, approved in ((CHANGED, DOTNET, True), (CHANGED, None, False), (NO_MAKEFILE, None, False)):
            _, line = self.line(files, contract, approved)
            with self.subTest(line=line):
                self.assertEqual(line.count("\n"), 1)
                self.assertTrue(line.endswith("\n"))
        _, first = self.line(CHANGED, DOTNET, True)
        policy, _ = self.line(NO_MAKEFILE, None, False)
        once = handoff.with_plan_tests("Krok planowania Lekkiego karty K-1.\nTreść.", policy)
        self.assertEqual(once.count(handoff.PLAN_TESTS_PREFIX), 1)
        again = handoff.with_plan_tests("Krok.\n" + first + "Dalej.\n", policy)
        self.assertEqual(again.count(handoff.PLAN_TESTS_PREFIX), 1)
        self.assertNotIn("dotnet", again)
        self.assertIn("Dalej.\n", again)


if __name__ == "__main__":
    unittest.main()
