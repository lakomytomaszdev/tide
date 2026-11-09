"""PT-280 (K-69 #3): polecenia zatwierdzonego kontraktu testów jako zgody kroku — reguła jednostkowo.

`testpolicy.contract_grants` z `TestPolicy` daje dokładne polecenia dla listy kroku; tu sprawdzamy decyzję, regułę
Claude (`claude_tools`), `is_allowed` na liście kroku i blok kartki. Przepływ silnika: test_cardflow_step_test_policy.py.
"""
import os
import unittest

from tide_daemon.board import commands, contract_approval as approval, project_tests
from tide_daemon.cardflow import handoff, testpolicy
from tide_daemon.cardflow.testpolicy import effective_tests

from .test_board_step_approval import CONTRACT as STEP_CONTRACT, StepCase
from .test_cardflow_effective_tests import CHANGED, tree

SHA = "0123456789abcdef0123456789abcdef01234567"
PROJECT = ["git status *", "rg *"]


class Case(unittest.TestCase):
    def policy(self, changed="", full="", *, approved=True, base=SHA, files=CHANGED):
        return effective_tests({"changed": changed, "full": full}, tree(self, files), approved, base)


def step_list(grants):
    """Lista kroku tak, jak składa ją silnik: lista projektu + zgody kontraktu."""
    return [*PROJECT, *grants.commands]


class ContractGrantsTest(Case):
    def test_approved_contract_grants_exact_commands_with_pinned_base(self):
        grants = testpolicy.contract_grants(self.policy("make test-changed BASE={base} JOBS=2", "make test"), [])
        self.assertEqual(grants.commands, (f"make test-changed BASE={SHA} JOBS=2", "make test"))
        self.assertEqual((grants.notes, grants.fix), ((), False))
        allowed, _bans = commands.claude_tools(step_list(grants), [])
        self.assertIn(f"Bash(make test-changed BASE={SHA} JOBS=2)", allowed)
        self.assertNotIn(f"Bash(make test-changed BASE={SHA} JOBS=2 *)", allowed)
        self.assertNotIn("Bash(make test-changed *)", allowed)
        self.assertTrue(commands.is_allowed(f"make test-changed BASE={SHA} JOBS=2", step_list(grants)))
        self.assertFalse(commands.is_allowed(f"make test-changed BASE={SHA} JOBS=2 X=1", step_list(grants)))
        self.assertFalse(commands.is_allowed("make test-changed BASE=deadbeef JOBS=2", step_list(grants)))

    def test_grant_text_is_the_policy_text(self):
        tests = self.policy("pytest -q --base {base}", "pytest -q")
        grants = testpolicy.contract_grants(tests, [])
        self.assertEqual(grants.commands, (tests.changed, tests.full))
        self.assertIn(f"`{tests.changed}`", handoff.ticket_tests(tests.base, "", tests))

    def test_only_profile_source_grants(self):
        # Niezatwierdzony kontrakt i wykrycie z Makefile (plasma-tide bez kontraktu) — bez zgód i bez uwag.
        for tests in (self.policy("pytest --base {base}", "pytest", approved=False), self.policy(approved=False),
                      testpolicy.master_policy("", SHA), None):
            with self.subTest(tests=tests):
                self.assertEqual(testpolicy.contract_grants(tests, []), testpolicy.ContractGrants())
        detected = self.policy(approved=False)
        self.assertEqual(detected.changed_source, testpolicy.DETECTED)
        self.assertEqual(handoff.command_policy(PROJECT, [], contract=testpolicy.contract_grants(detected, [])),
                         handoff.command_policy(PROJECT, []))

    def test_legacy_branch_with_approved_changed_grants_only_full(self):
        # Gałąź bez `test-changed`: `changed` zamienia się na wykryte `make test` (źródło detected) — bez zgody.
        tests = self.policy("make test-changed BASE={base}", "pytest", files={"Makefile": "test:\n\ttrue\n"})
        self.assertTrue(tests.legacy)
        self.assertEqual(testpolicy.contract_grants(tests, []).commands, ("pytest",))

    def test_star_gives_no_grant_and_shares_the_project_note(self):
        tests = self.policy("", "pytest *")
        grants = testpolicy.contract_grants(tests, [])
        self.assertEqual(grants.commands, ())
        self.assertEqual(grants.notes, (("full", "pytest *", project_tests.grant_note("pytest *")),))
        listed = step_list(grants)
        self.assertFalse(commands.is_allowed("pytest -x tests/other", listed))
        self.assertFalse(commands.is_allowed("pytest", listed))
        allowed, _ = commands.claude_tools(listed, [])
        self.assertFalse(any("pytest" in rule for rule in allowed))

    def test_star_in_filter_after_base(self):
        command = 'dotnet test --filter "Name~Foo*" {base}'
        grants = testpolicy.contract_grants(self.policy(command, ""), [])
        self.assertEqual(grants.commands, ())
        [(field, shown, note)] = grants.notes
        self.assertEqual((field, note), ("changed", project_tests.grant_note(command)))
        self.assertEqual(note, project_tests.GRANT_NOTE)
        self.assertNotIn("{base}", shown)
        listed = step_list(grants)
        self.assertFalse(commands.is_allowed('dotnet test --filter "Name~FooBar" x', listed))
        allowed, _ = commands.claude_tools(listed, [])
        self.assertFalse(any("dotnet" in rule for rule in allowed))
        block = handoff.contract_commands(grants)
        self.assertIn(project_tests.GRANT_NOTE, block)

    def test_unknown_base_grants_full_only(self):
        grants = testpolicy.contract_grants(self.policy("make test-changed BASE={base}", "make test", base=""), [])
        self.assertEqual(grants.commands, ("make test",))
        self.assertEqual([n[0] for n in grants.notes], ["changed"])
        self.assertFalse(any("{base}" in rule for rule in commands.claude_tools(step_list(grants), [])[0]))

    def test_forbidden_commands_are_not_added_and_card_asks_for_fix(self):
        cases = (([], "make install", "make install"),                       # zakaz Tide zawsze
                 (["pytest --slow *"], "LC_ALL=C pytest --slow", "pytest --slow *"),
                 (["make test"], "LC_ALL=pl_PL.UTF-8 make test", "make test"))
        for denied, full, rule in cases:
            with self.subTest(full=full):
                grants = testpolicy.contract_grants(self.policy("", full), denied)
                self.assertEqual(grants.commands, ())
                self.assertTrue(grants.fix)
                self.assertIn(f"„{rule}”", grants.notes[0][2])
                self.assertIn(handoff.CONTRACT_FIX, handoff.contract_commands(grants))

    def test_compound_commands_never_become_grants(self):
        for full in ("make test; rm -rf x", "make test && true", "make test | tee x", "make test $X",
                     "make test `id`", "env X=1 make test", "PATH=/x make test"):
            with self.subTest(full=full):
                grants = testpolicy.contract_grants(self.policy("", full), [])
                self.assertEqual(grants.commands, ())
                self.assertEqual(grants.notes[0][2], project_tests.GRANT_NOTE)

    def test_same_command_in_both_fields_is_listed_once(self):
        grants = testpolicy.contract_grants(self.policy("pytest --since {base}", f"pytest --since {SHA}"), [])
        self.assertEqual(grants.commands, (f"pytest --since {SHA}",))


class ApprovalToGrantsTest(StepCase):
    """Zgody idą wyłącznie za `approval_for_step` (#9): kolizja ścieżek albo brak/uszkodzenie magazynu → bez zgód."""

    def grants(self, path, record=True):
        if record:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            approval.record(self.root, "app", STEP_CONTRACT, path)
        project = self.project()
        approved, _reason = approval.approval_for_step(project, self.step(), self.root, path)
        tests = effective_tests(project.tests, tree(self, CHANGED), approved, SHA, error=project.tests_error)
        return testpolicy.contract_grants(tests, project.denied_commands)

    def test_grants_follow_the_step_approval(self):
        self.assertEqual(self.grants(self.state).commands, (f"make test-changed BASE={SHA}", "make test"))
        self.assertEqual(self.grants(os.path.join(self.out, "s", approval.FILE_NAME)).commands, ())   # kolizja
        missing = os.path.join(self.tmp, "other-state", "tide", approval.FILE_NAME)
        self.assertEqual(self.grants(missing, record=False).commands, ())
        with open(self.state, "w", encoding="utf-8") as fh:      # uszkodzony magazyn (prawa 0600 zostają)
            fh.write("{nie json")
        self.assertEqual(self.grants(self.state, record=False).commands, ())


class ContractBlockTest(Case):
    def test_block_lives_in_the_command_section_and_is_replaced_on_resume(self):
        grants = testpolicy.contract_grants(self.policy("make test-changed BASE={base}", "make test"), [])
        text = handoff.command_policy(PROJECT, ["make install"], contract=grants)
        section = text[text.index("Polecenia projektu w tym kroku"):text.index("Zabronione polecenia:")]
        self.assertIn(handoff.CONTRACT_COMMANDS_OPEN, section)
        self.assertIn(f"- make test-changed BASE={SHA}\n", section)
        stripped = handoff.strip_contract_commands("kartka" + text + "dalej")
        self.assertNotIn(handoff.CONTRACT_COMMANDS_OPEN, stripped)
        self.assertNotIn(SHA, stripped)
        self.assertEqual(stripped, "kartka" + handoff.command_policy(PROJECT, ["make install"]) + "dalej")

    def test_no_contract_keeps_master_text(self):
        self.assertEqual(handoff.contract_commands(testpolicy.ContractGrants()), "")
        self.assertEqual(handoff.runner_policy(handoff.HOST_RUNNER, PROJECT, [], contract=None),
                         handoff.command_policy(PROJECT, []))


if __name__ == "__main__":
    unittest.main()
