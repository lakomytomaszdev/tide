"""PT-318 (K-88): testy po scaleniu — wykrycie scalenia nie-FF, polecenie z kontraktu testów lub Makefile,
bramka karty, odczyt wyniku próby, ponowienie i trwałość po restarcie.

Konflikt semantyczny PT-264×PT-288 (09.11) wyszedł dopiero w PT-289: ticket B testował się na własnym punkcie
startu, choć przed jego scaleniem na gałąź karty wszedł ticket A. Test odtwarzający:
`MergeCheckFlowTest.test_non_ff_merge_requests_check_and_holds_the_card`.
"""
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow import mergecheck
from tide_daemon.cardflow.decisions import build_decisions
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.gitops import FlowGit
from tide_daemon.cardflow.state import fold, ticket_of

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from . import test_cardflow_plan_integration as plan_tests

START = "a" * 40
CHANGED_MK = "test-changed:\n\t@echo zmiany $(BASE)\n"
FULL_MK = "test:\n\t@echo pelne\n"


def requested(ticket="PT-2", n=1, seq=10, **extra):
    event = {"seq": seq, "type": mergecheck.REQUESTED, "ticket": ticket, "check": f"{ticket}:{n}", "start": START,
             "before": "b" * 40, "after": "c" * 40, "mergeSeq": 5, "retryOf": ""}
    if "unavailable" not in extra:
        event.update(command=f"make test-changed BASE={START}", source="makefile")
    event.update(extra)
    return event


def finished(check, code, ticket="PT-2", seq=20):
    return {"seq": seq, "type": mergecheck.FINISHED, "ticket": ticket, "check": check, "exit": code, "log": "/x.log"}


STARTED = {"seq": 1, "type": "card.started", "entry": "plan", "executor": "Sonnet"}


class SelectCommandTest(unittest.TestCase):
    def test_contract_with_base_wins_over_makefile(self):
        chosen = mergecheck.select_command("tools/run-tests --since {base}", {"test-changed", "test"}, START)
        self.assertEqual(chosen, {"command": f"tools/run-tests --since {START}", "source": "contract"})

    def test_makefile_changed_target_uses_full_start_sha(self):
        chosen = mergecheck.select_command("", {"test-changed", "test"}, START)
        self.assertEqual(chosen, {"command": f"make test-changed BASE={START}", "source": "makefile"})

    def test_makefile_with_only_full_target_falls_back_to_full_run(self):
        self.assertEqual(mergecheck.select_command("", {"test"}, START),
                         {"command": "make test", "source": "makefile", "fallback": "full"})

    def test_no_command_and_unknown_start_mean_unavailable(self):
        self.assertEqual(mergecheck.select_command("", {"lint"}, START), {})
        self.assertEqual(mergecheck.select_command("make test-changed BASE={base}", {"test-changed"}, ""), {})
        self.assertEqual(mergecheck.select_command("", {"test-changed"}, "HEAD"), {})

    def test_contract_that_is_not_a_single_command_never_gives_a_command(self):
        """Review r1, MAJOR-3: łańcuch, podpowłoka ani powłoka z kontraktu nie tworzą wykonywalnej próby; brak
        przejścia na Makefile — błąd zatwierdzonego kontraktu ma być widoczny jako próba niedostępna."""
        for contract in ("echo {base} && make test", "make test-changed BASE={base}; rm -rf build",
                         "make test-changed BASE={base} | tee log", "make test-changed BASE=$(git rev-parse HEAD)",
                         "make test-changed BASE=`git rev-parse HEAD`", "(make test-changed BASE={base})",
                         "bash -c 'make test-changed BASE={base}'", "sh -c 'make test'",
                         "env X=1 make test", "PATH=/tmp make test", "make test > out.log",
                         "make test-changed BASE={base}\nmake test",
                         # Review r2, MAJOR-1: powłoka i przełączniki wskazane ścieżką.
                         "/bin/sh -c 'echo {base} && make test'", "/bin/bash -c 'make test-changed BASE={base}'",
                         "/usr/bin/env sh -c 'echo {base} && make test'", "/usr/bin/env make test",
                         "./sh -c 'make test'", "/usr/bin/sudo make test", "/usr/bin/timeout 60 sh -c 'make test'",
                         "LC_ALL=C /bin/dash -c 'make test'", "python3 -c 'import os; os.system(\"a && b\")'",
                         "/usr/bin/python3 -Sc 'print(1)'", "node -e 'x'", "awk 'BEGIN{system(\"make test\")}'"):
            with self.subTest(contract=contract):
                chosen = mergecheck.select_command(contract, {"test-changed", "test"}, START)
                self.assertNotIn("command", chosen)
                self.assertEqual(chosen, {"source": "contract", "reason": "contract-not-single"})

    def test_single_contract_command_with_quoted_operators_and_variables_is_accepted(self):
        for contract, command in (("make test-changed BASE={base} JOBS=4", f"make test-changed BASE={START} JOBS=4"),
                                  ("LC_ALL=C.UTF-8 pytest -k 'a or b' --base {base}",
                                   f"LC_ALL=C.UTF-8 pytest -k 'a or b' --base {START}"),
                                  ("/usr/bin/make test-changed BASE={base}", f"/usr/bin/make test-changed BASE={START}"),
                                  ("/usr/bin/python3 -m pytest -p no:cacheprovider --base {base}",
                                   f"/usr/bin/python3 -m pytest -p no:cacheprovider --base {START}"),
                                  ("python3 tools/test_runner.py -c fast --base {base}",
                                   f"python3 tools/test_runner.py -c fast --base {START}")):
            with self.subTest(contract=contract):
                self.assertEqual(mergecheck.select_command(contract, set(), START),
                                 {"command": command, "source": "contract"})

    def test_targets_ignore_assignments_and_comments(self):
        texts = ["TEST := x\ntest-changed ::= y\n# test:\nbuild test: deps\n", "test-changed:\n\t@true\n"]
        self.assertEqual(mergecheck.make_targets(texts), {"build", "test", "test-changed"})


class MergeCheckGitTargetsTest(unittest.TestCase):
    """Cele czytane z gita (Makefile i mk/*.mk w commicie gałęzi karty) bez uruchamiania `make`."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tide-merge-check-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.base], check=False)
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(os.path.join(self.repo, "mk"))
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x"}
        self.run_git = lambda *args: subprocess.run(["git", "-C", self.repo, *args], check=True, env=env,
                                                    capture_output=True, text=True).stdout
        self.run_git("init", "-q")
        self.marker = os.path.join(self.base, "make-called")
        fakebin = os.path.join(self.base, "bin")
        os.makedirs(fakebin)
        fake = os.path.join(fakebin, "make")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\ntouch {self.marker}\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        patcher = mock.patch.dict(os.environ, {"PATH": fakebin + os.pathsep + os.environ.get("PATH", "")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = SimpleNamespace(git=FlowGit())

    def commit(self, files):
        for name, text in files.items():
            with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", "x")
        return self.run_git("rev-parse", "HEAD").strip()

    def test_full_target_only_gives_full_fallback_without_running_make(self):
        sha = self.commit({"Makefile": "-include mk/*.mk\n" + FULL_MK, "mk/lint.mk": "lint:\n\t@true\n"})
        targets = FlowEngine._merge_check_targets(self.engine, self.repo, sha)
        self.assertEqual(targets, {"test", "lint"})
        self.assertEqual(mergecheck.select_command("", targets, START)["fallback"], "full")
        self.assertFalse(os.path.exists(self.marker), "wykrycie celów nie może uruchamiać make")

    def test_changed_target_in_mk_file_is_found_in_the_commit_not_the_worktree(self):
        sha = self.commit({"Makefile": "-include mk/*.mk\n" + FULL_MK, "mk/tests.mk": CHANGED_MK})
        with open(os.path.join(self.repo, "mk", "tests.mk"), "w", encoding="utf-8") as fh:
            fh.write("")        # niezacommitowana zmiana worktree nie zmienia celów commita
        self.assertEqual(FlowEngine._merge_check_targets(self.engine, self.repo, sha), {"test", "test-changed"})
        self.assertFalse(os.path.exists(self.marker))


class MergeCheckPlanCommitsTest(MergeCheckGitTargetsTest):
    """Review r1, MAJOR-2: scalenie liczy się jak FF tylko wtedy, gdy `start..before` to wyłącznie commity planu,
    które Tide zapisał na tej karcie (wynik `plan-commit` w dzienniku). Nazwy plików niczego nie przesądzają."""

    PLAN = "docs/agents/plans/plan.md"
    CARD = "docs/agents/tickets/k-1-flow/pt-1.md"

    @staticmethod
    def plan(state, before="Opis planu.", after=""):
        text = f"---\ntide-plan: 1\nstatus: active\nupdated: {state}\n---\n\n{before}\n\n" \
               "| Ticket | Tytuł | Stan |\n| --- | --- | --- |\n" f"| PT-1 | A | {state} |\n"
        return text + (f"\n{after}\n" if after else "")

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.repo, "docs", "agents", "plans"))
        os.makedirs(os.path.join(self.repo, "docs", "agents", "tickets", "k-1-flow"))
        self.start = self.commit({"Makefile": FULL_MK, self.PLAN: self.plan("todo"), self.CARD: "# PT-1\n"})

    def only_plan(self, before, *recorded, start=None, already=False, plan_file=PLAN):
        results = {("plan-commit", str(n)): {"ok": True, "commit": sha, **({"already": True} if already else {})}
                   for n, sha in enumerate(recorded)}
        results[("merge", "PT-1")] = {"ok": True, "commit": before}
        st = SimpleNamespace(results=results, plan_approved={"planFile": plan_file})
        return FlowEngine._only_tide_plan_commits(self.engine, self.repo, st, start or self.start, before)

    def test_only_recorded_tide_plan_commits_count_as_fast_forward(self):
        first = self.commit({self.PLAN: self.plan("w-toku")})
        second = self.commit({self.PLAN: self.plan("review")})
        self.assertTrue(self.only_plan(second, first, second))
        self.assertFalse(self.only_plan(second, second), "jeden z commitów spoza dziennika karty")
        self.assertFalse(self.only_plan(second), "bez wyników plan-commit")
        self.assertFalse(self.only_plan(second, first, second, already=True),
                         "`already` to istniejący wierzchołek, nie commit Tide")
        self.assertFalse(self.only_plan(self.start, self.start), "pusty zakres to nie wyjątek, tylko FF")
        self.assertFalse(self.only_plan(second, first, second, plan_file=""), "bez pliku planu nie ma dowodu")
        self.assertFalse(self.only_plan(second, first, second, plan_file="docs/agents/plans/inny.md"))

    def test_manual_change_of_plan_files_requests_a_check(self):
        tide = self.commit({self.PLAN: self.plan("w-toku")})
        manual = self.commit({self.PLAN: "ręczna zmiana kartki\n"})
        self.assertFalse(self.only_plan(manual, tide))

    def test_recorded_plan_commit_carrying_manual_text_requests_a_check(self):
        """Review r2, MAJOR-2: commit Tide przejmuje ręczny tekst planu i dopiski kartek z worktree karty — sam SHA
        z `plan-commit` nie dowodzi automatycznej aktualizacji stanu."""
        cases = {"tekst po tabeli": {self.PLAN: self.plan("w-toku", after="Ręczny dopisek koordynatora.")},
                 "tekst przed tabelą": {self.PLAN: self.plan("w-toku", before="Opis planu.\n\nRęczna uwaga.")},
                 "dopisek kartki": {self.PLAN: self.plan("w-toku"), self.CARD: "# PT-1\n\nRęczny dopisek.\n"},
                 "sama kartka": {self.CARD: "# PT-1\n\nRęczny dopisek.\n"},
                 "plan bez tabeli": {self.PLAN: "---\nstatus: active\n---\n\nTabela zniknęła.\n"}}
        for name, files in cases.items():
            with self.subTest(name):
                self.run_git("checkout", "-q", "--detach", self.start)
                tide = self.commit({self.PLAN: self.plan("w-toku")})
                carrying = self.commit(files)
                self.assertFalse(self.only_plan(carrying, tide, carrying))
                self.assertFalse(self.only_plan(carrying, carrying, start=tide))

    def test_merge_of_another_ticket_or_foreign_start_requests_a_check(self):
        tide = self.commit({self.PLAN: self.plan("w-toku")})
        self.run_git("checkout", "-qb", "ticket", self.start)
        other = self.commit({"a.txt": "kod innego ticketu\n"})
        self.run_git("checkout", "-q", "-")
        self.run_git("merge", "-q", "--no-ff", "-m", "merge", "ticket")
        merged = self.run_git("rev-parse", "HEAD").strip()
        self.assertFalse(self.only_plan(merged, tide, merged), "commit scalenia ma dwóch rodziców")
        self.assertFalse(self.only_plan(tide, tide, start=other), "`start` nie jest przodkiem `before`")
        self.assertFalse(os.path.exists(self.marker))


class MergeCheckFoldTest(unittest.TestCase):
    def test_exit_zero_of_current_check_opens_the_gate(self):
        st = fold([STARTED, requested(), finished("PT-2:1", 0)])
        self.assertEqual(mergecheck.gate(st), [])

    def test_any_other_exit_keeps_the_gate_closed_as_failed(self):
        for code in (1, 2, "zatrzymane", "przekroczony czas", False, "0", None):
            with self.subTest(code=code):
                held = mergecheck.gate(fold([STARTED, requested(), finished("PT-2:1", code)]))
                self.assertEqual([(h["check"], h["status"]) for h in held], [("PT-2:1", "merge-check-failed")])

    def test_result_of_a_stale_check_changes_nothing(self):
        events = [STARTED, requested(), finished("PT-2:1", 1),
                  requested(n=2, seq=30, retryOf="PT-2:1"), finished("PT-2:1", 0, seq=40)]
        held = mergecheck.gate(fold(events))
        self.assertEqual([(h["check"], h["status"]) for h in held], [("PT-2:2", "pending")])
        held = mergecheck.gate(fold([*events, finished("PT-2:2", 0, seq=50)]))
        self.assertEqual(held, [])

    def test_unavailable_check_is_never_opened_by_a_result(self):
        events = [STARTED, requested(unavailable=True), finished("PT-2:1", 0)]
        held = mergecheck.gate(fold(events))
        self.assertEqual([h["status"] for h in held], ["merge-check-unavailable"])
        self.assertEqual(mergecheck.next_check(fold(events), "PT-2"), "PT-2:2")

    def test_problems_offer_retry_of_the_exact_check(self):
        for events, problem in (([STARTED, requested(unavailable=True)], "merge-check-unavailable"),
                                ([STARTED, requested(), finished("PT-2:1", "przekroczony czas")],
                                 "merge-check-failed")):
            with self.subTest(problem=problem):
                found = [d for d in build_decisions(fold(events)) if d["kind"] == "merge-check"]
                self.assertEqual([d["id"] for d in found], [f"{problem}:PT-2:1"])
                option = found[0]["options"][0]
                self.assertEqual((option["method"], option["params"]),
                                 ("ResolveCardStep", {"choice": "retry-merge-check", "ticket": "PT-2",
                                                      "check": "PT-2:1"}))
        rejected = fold([STARTED, requested(unavailable=True, source="contract", reason="contract-not-single"),
                         finished("PT-2:1", 0)])
        self.assertEqual([h["status"] for h in mergecheck.gate(rejected)], ["merge-check-unavailable"],
                         "odrzucony kontrakt nie otwiera bramki nawet po wyniku 0")
        found = [d for d in build_decisions(rejected) if d["kind"] == "merge-check"]
        self.assertEqual([d["id"] for d in found], ["merge-check-unavailable:PT-2:1"])
        self.assertEqual(found[0]["technical"]["reason"], "contract-not-single")
        self.assertEqual(found[0]["options"][0]["params"]["choice"], "retry-merge-check")
        pending = fold([STARTED, requested()])
        self.assertFalse([d for d in build_decisions(pending) if d["kind"] == "merge-check"],
                         "próba czekająca na przebieg nie jest decyzją użytkownika")


class MergeCheckFlowTest(FlowInProcess):
    """Pełny przepływ z prawdziwym gitem: plan z ticketami równoległymi, scalenia do gałęzi karty."""
    merge_checks_pass = False
    proposal = plan_tests.PlanFlowTest.proposal
    submit = plan_tests.PlanFlowTest.submit
    approve = plan_tests.PlanFlowTest.approve

    def setUp(self):
        super().setUp()
        self.make_marker = os.path.join(self.base, "make-called")
        fake = os.path.join(os.path.dirname(self.fakes["claude"]), "make")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\ntouch {self.make_marker}\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)

    # --- pomocnicze -----------------------------------------------------------------------------

    def commit_files(self, cwd, files, message="makefile"):
        for name, text in files.items():
            path = os.path.join(cwd, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        git(cwd, "add", "-A", *files)
        git(cwd, "commit", "-qm", message, "--", *files)
        return git(cwd, "rev-parse", "HEAD").strip()

    def parallel_plan(self, card, count, blocked):
        path = self.proposal(card)
        with open(path, encoding="utf-8") as fh:
            source = fh.read().split("| #1 |")[0]
        rows = [f"| #{n} | Plasterek {n} | todo | | Sonnet | | standard | Opus | |\n" for n in range(1, count + 1)]
        if blocked:
            rows.append(f"| #{count + 1} | Zablokowany | todo | | Sonnet | | standard | Opus | "
                        + ", ".join(f"#{n}" for n in range(1, count + 1)) + " |\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source + "".join(rows))
        for number in range(1, len(rows) + 1):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: powoli 2\nATRAPA-PLIK: {number}\n\n- [ ] Wynik.\n")
        return path

    def run_plan(self, card, count=2, blocked=True):
        self.assertTrue(self.submit(card, self.parallel_plan(card, count, blocked))["ok"])
        return self.approve(card)["map"]

    def merges(self, card):
        return [e for e in self.results(card, "merge") if e.get("ok")]

    def checks(self, card):
        return self.of(card, mergecheck.REQUESTED)

    def ticket_starts(self, card, ticket):
        return [s for s in self.of(card, "step.started") if s.get("ticket") == ticket]

    def state(self, card):
        return fold(self.events(card))

    def settle(self, seconds=2.0):
        """Kilka ticków przepływu (150 ms) bez zmiany stanu karty."""
        time.sleep(seconds)

    def finish(self, card, check, code):
        ticket = check.rsplit(":", 1)[0]
        self.flow.engine.journal.append(card, mergecheck.FINISHED, ticket=ticket, check=check, exit=code,
                                        log="/tmp/przebieg.log")

    def flow_view(self, card):
        return (self.shown(card).get("derived") or {}).get("flow") or {}

    def problems(self, card):
        return [d for d in self.flow_view(card).get("decisions") or [] if d["kind"] == "merge-check"]

    def retry(self, card, ticket, check):
        return self.flow.engine.resolve_step(card, {"choice": "retry-merge-check", "ticket": ticket, "check": check})

    def assert_check_for_merge(self, card, merge, check):
        self.assertEqual(check["ticket"], merge["key"])
        self.assertEqual(check["check"], f"{merge['key']}:1")
        start = next(r["base"] for r in self.results(card, "ticket-branch") if r.get("ticket") == merge["key"])
        self.assertEqual(check["start"], start)
        self.assertEqual(check["after"], merge["commit"])
        self.assertNotEqual(check["before"], check["start"])
        self.assertEqual(git(self.repo, "rev-parse", f"{check['after']}^1").strip(), check["before"])

    def assert_first_merge_only_after_tide_plan_commits(self, card):
        """Pierwsze scalenie karty z planem: między startem ticketu a `before` są tylko commity planu zapisane przez
        Tide na tej karcie (stan ticketu po starcie) — próby nie ma. Commit ręczny lub innego ticketu ją zakłada
        (`MergeCheckPlanCommitsTest`)."""
        first = self.merges(card)[0]
        start = next(r["base"] for r in self.results(card, "ticket-branch") if r.get("ticket") == first["key"])
        before = git(self.repo, "rev-parse", f"{first['commit']}^1").strip()
        made = {r.get("commit") for r in self.results(card, "plan-commit") if r.get("ok") and not r.get("already")}
        between = git(self.repo, "rev-list", f"{start}..{before}").split()
        self.assertTrue(set(between) <= made, (between, made))
        self.assertFalse([c for c in self.checks(card) if c["ticket"] == first["key"]])

    def assert_one_check_for_second_merge(self, card):
        self.assertTrue(wait_until(lambda: self.checks(card), timeout=60), self.events(card))
        first, second = self.merges(card)[:2]
        check = self.checks(card)[0]
        self.assert_check_for_merge(card, second, check)
        git(self.repo, "merge-base", "--is-ancestor", first["commit"], check["before"])
        self.assert_first_merge_only_after_tide_plan_commits(card)
        self.assertEqual(len(self.checks(card)), 1)
        return first, second, check

    # --- scenariusze ----------------------------------------------------------------------------

    def test_non_ff_merge_requests_check_and_holds_the_card(self):
        self.commit_files(self.repo, {"Makefile": "-include mk/*.mk\n" + FULL_MK, "mk/tests.mk": CHANGED_MK})
        card = self.card()
        mapping = self.run_plan(card, count=2, blocked=True)
        first, second, check = self.assert_one_check_for_second_merge(card)
        self.assertEqual(check["command"], f"make test-changed BASE={check['start']}")
        self.assertEqual((check["source"], "fallback" in check, "unavailable" in check), ("makefile", False, False))
        # Bramka zamknięta: zablokowany ticket nie startuje, domknięcie też nie.
        self.settle()
        self.assertFalse(self.ticket_starts(card, mapping["#3"]), self.events(card))
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("kind") == "close"])
        self.assertTrue(wait_until(lambda: [c["status"] for c in self.flow_view(card).get("mergeChecks", [])]
                                   == ["pending"], timeout=10), self.flow_view(card))
        with self.assertRaises(FlowError):
            self.retry(card, second["key"], check["check"])     # czeka na przebieg — nie na ponowienie
        # Porażka próby: problem karty z ponowieniem, bramka dalej zamknięta.
        self.finish(card, check["check"], "przekroczony czas")
        self.assertTrue(wait_until(lambda: [d["id"] for d in self.problems(card)]
                                   == [f"merge-check-failed:{check['check']}"], timeout=10), self.flow_view(card))
        self.settle(1.0)
        self.assertFalse(self.ticket_starts(card, mapping["#3"]))
        with self.assertRaises(FlowError):
            self.retry(card, second["key"], f"{second['key']}:7")   # nieaktualna próba — błąd bez zapisu
        self.assertEqual(len(self.checks(card)), 1)
        reply = self.retry(card, second["key"], check["check"])
        self.assertEqual(reply["check"], f"{second['key']}:2")
        again = self.checks(card)[-1]
        card_tip = git(self.repo, "rev-parse", ticket_of(self.state(card), second["key"])["cardBranch"]).strip()
        self.assertEqual((again["start"], again["after"], again["retryOf"]), (check["start"], card_tip, check["check"]))
        self.assertEqual(again["command"], f"make test-changed BASE={check['start']}")
        self.assertTrue(wait_until(lambda: not self.problems(card), timeout=10), "ponowienie zdejmuje problem")
        # Wynik starszej próby nic nie zmienia; wynik 0 bieżącej otwiera bramkę.
        self.finish(card, check["check"], 0)
        self.settle(1.0)
        self.assertFalse(self.ticket_starts(card, mapping["#3"]))
        self.finish(card, again["check"], 0)
        self.assertTrue(wait_until(lambda: self.ticket_starts(card, mapping["#3"]), timeout=30), self.events(card))
        self.assertFalse(os.path.exists(self.make_marker), "Tide nie uruchamia make przy wykryciu ani wyborze")
        self.assertFalse([e for e in self.events(card) if e["type"] == "merge.check-skipped"])

    def test_without_command_check_is_unavailable_and_retry_after_adding_target(self):
        card = self.card()
        mapping = self.run_plan(card, count=3, blocked=False)
        self.assertTrue(wait_until(lambda: self.checks(card), timeout=60), self.events(card))
        self.settle()
        check = self.checks(card)[0]
        self.assertTrue(check["unavailable"])
        self.assertNotIn("command", check)
        self.assertNotIn("reason", check)
        self.assertEqual(len(self.checks(card)), 1)
        self.assertFalse([e for e in self.events(card) if e["type"] == "merge.check-skipped"])
        # Bramka zamknięta: pozostałe tickety nie scalają się (czekają po review), domknięcie nie startuje.
        self.assertTrue(wait_until(lambda: self.flow_view(card) and self.shown(card)["derived"]["column"] == "waiting",
                                   timeout=40), self.shown(card).get("derived"))
        self.assertEqual(len(self.merges(card)), 2, self.events(card))
        self.assertEqual(check["ticket"], self.merges(card)[1]["key"])
        self.assert_first_merge_only_after_tide_plan_commits(card)
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("kind") == "close"])
        self.assertEqual([d["id"] for d in self.problems(card)], [f"merge-check-unavailable:{check['check']}"])
        # Ponowienie po dodaniu celu `test-changed` na gałęzi karty: nowe polecenie na bieżącym wierzchołku.
        card_worktree = ticket_of(self.state(card), check["ticket"])["cardWorktree"]
        tip = self.commit_files(card_worktree, {"Makefile": CHANGED_MK})
        reply = self.retry(card, check["ticket"], check["check"])
        again = self.checks(card)[-1]
        self.assertEqual(reply["check"], f"{check['ticket']}:2")
        git(self.repo, "merge-base", "--is-ancestor", tip, again["after"])
        self.assertEqual((again["command"], again["source"], again["retryOf"]),
                         (f"make test-changed BASE={check['start']}", "makefile", check["check"]))
        self.assertTrue(wait_until(lambda: not self.problems(card), timeout=10), "ponowienie zdejmuje problem")
        self.assertEqual(len(self.merges(card)), 2)
        self.finish(card, again["check"], 0)
        self.assertTrue(wait_until(lambda: len(self.merges(card)) == 3, timeout=60), self.events(card))
        waiting = self.merges(card)[2]["key"]
        self.assertIn(waiting, mapping.values())
        # Kolejne scalenie nie-FF znowu trzyma bramkę (własna próba trzeciego ticketu).
        self.assertTrue(wait_until(lambda: any(c["ticket"] == waiting for c in self.checks(card)), timeout=10))
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("kind") == "close"])

    def test_contract_command_replaces_base_even_with_makefile_target(self):
        self.commit_files(self.repo, {"Makefile": CHANGED_MK + FULL_MK})
        self.flow.engine._merge_check_contract = lambda card: "tools/run-tests --since {base}"
        card = self.card()
        self.run_plan(card, count=2, blocked=True)
        _first, _second, check = self.assert_one_check_for_second_merge(card)
        self.assertEqual((check["command"], check["source"]), (f"tools/run-tests --since {check['start']}",
                                                               "contract"))

    def test_contract_chain_gives_unavailable_check_without_command(self):
        """Review r1, MAJOR-3: zatwierdzony kontrakt z łańcuchem nie tworzy wykonywalnej próby ani nie otwiera bramki;
        Makefile z `test-changed` nie zastępuje błędnego kontraktu."""
        self.commit_files(self.repo, {"Makefile": CHANGED_MK + FULL_MK})
        self.flow.engine._merge_check_contract = lambda card: "echo {base} && make test"
        card = self.card()
        mapping = self.run_plan(card, count=2, blocked=True)
        self.assertTrue(wait_until(lambda: self.checks(card), timeout=60), self.events(card))
        check = self.checks(card)[0]
        self.assertTrue(check["unavailable"])
        self.assertNotIn("command", check)
        self.assertEqual((check["source"], check["reason"]), ("contract", "contract-not-single"))
        self.assertTrue(wait_until(lambda: [d["id"] for d in self.problems(card)]
                                   == [f"merge-check-unavailable:{check['check']}"], timeout=10), self.flow_view(card))
        self.assertEqual(check["ticket"], self.merges(card)[1]["key"])
        self.finish(card, check["check"], 0)        # wynik niedostępnej próby niczego nie otwiera
        self.settle()
        self.assertEqual(len(self.merges(card)), 2)
        self.assertFalse(self.ticket_starts(card, mapping["#3"]))
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("kind") == "close"])
        # Ponowienie po poprawce kontraktu wybiera polecenie od nowa.
        self.flow.engine._merge_check_contract = lambda card: "make test-changed BASE={base}"
        reply = self.retry(card, check["ticket"], check["check"])
        again = self.checks(card)[-1]
        self.assertEqual(reply["check"], f"{check['ticket']}:2")
        self.assertEqual((again["command"], again["source"]),
                         (f"make test-changed BASE={check['start']}", "contract"))
        self.assertFalse(os.path.exists(self.make_marker))

    def test_manual_ticket_card_note_taken_by_tide_plan_commit_requests_a_check(self):
        """Review r2, MAJOR-2: po starcie ticketów ktoś dopisuje treść do kartki w worktree karty, bez commita.
        Następny zapis planu przez Tide przejmuje dopisek swoim commitem `plan-commit`. Pierwsze scalenie ma wtedy
        w `start..before` wyłącznie zapisane commity planu Tide, ale jeden z nich niesie ręczną treść — próba."""
        # 09.11, K-89: kartki w worktree karty ma tylko projekt z planami w repozytorium (`ai-docs: repo`).
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps(
            {"slug": "proj", "repo": self.repo, "aiDocs": "repo"})))["ok"])
        self.commit_files(self.repo, {"Makefile": CHANGED_MK})
        card = self.card()
        mapping = self.run_plan(card, count=2, blocked=True)
        tickets = [mapping["#1"], mapping["#2"]]
        self.assertTrue(wait_until(lambda: {r.get("ticket") for r in self.results(card, "ticket-branch")
                                            if r.get("ok")} >= set(tickets), timeout=30), self.events(card))
        worktree = ticket_of(self.state(card), tickets[1])["cardWorktree"]
        found = [os.path.join(root, name) for root, _dirs, names in os.walk(os.path.join(worktree, "docs"))
                 for name in names if root.endswith("-flow") and name == f"{tickets[1].lower()}.md"]
        self.assertEqual(len(found), 1, found)
        note = "\nRęczny dopisek po starcie ticketów (bez commita).\n"
        with open(found[0], "a", encoding="utf-8") as fh:
            fh.write(note)
        self.assertTrue(wait_until(lambda: self.merges(card), timeout=60), self.events(card))
        first = self.merges(card)[0]
        start = next(r["base"] for r in self.results(card, "ticket-branch") if r.get("ticket") == first["key"])
        before = git(self.repo, "rev-parse", f"{first['commit']}^1").strip()
        rel = os.path.relpath(found[0], worktree)
        self.assertIn(note.strip(), git(self.repo, "show", f"{before}:{rel}"), "dopisek przejęty przed scaleniem")
        made = {r.get("commit") for r in self.results(card, "plan-commit") if r.get("ok") and not r.get("already")}
        between = git(self.repo, "rev-list", f"{start}..{before}").split()
        self.assertTrue(between and set(between) <= made, (between, made))
        self.assertTrue(wait_until(lambda: any(c["ticket"] == first["key"] for c in self.checks(card)), timeout=10),
                        self.events(card))
        check = next(c for c in self.checks(card) if c["ticket"] == first["key"])
        self.assertEqual((check["check"], check["start"], check["before"], check["command"]),
                         (f"{first['key']}:1", start, before, f"make test-changed BASE={start}"))
        self.settle()
        self.assertFalse(self.ticket_starts(card, mapping["#3"]), "bramka zamknięta")

    def test_restart_between_merge_result_and_check_adds_exactly_one_check(self):
        self.commit_files(self.repo, {"Makefile": CHANGED_MK})
        card = self.card()
        engine = self.flow.engine
        crash = mock.patch.object(engine, "_append_merge_check", side_effect=RuntimeError("awaria przed zapisem"))
        crash.start()
        mapping = self.run_plan(card, count=2, blocked=True)
        self.assertTrue(wait_until(lambda: len(self.merges(card)) == 2, timeout=60), self.events(card))
        self.settle()
        self.assertFalse(self.checks(card))
        self.assertFalse(self.ticket_starts(card, mapping["#3"]), "niezapisana próba nie przepuszcza karty")
        crash.stop()
        self.restart()
        self.assertTrue(wait_until(lambda: self.checks(card), timeout=20), self.events(card))
        self.settle()
        self.restart()
        self.settle()
        self.assertEqual(len(self.checks(card)), 1, "restart nie dubluje próby")
        self.assertEqual(self.checks(card)[0]["ticket"], self.merges(card)[1]["key"])
        self.assertFalse(self.ticket_starts(card, mapping["#3"]), "zamknięta bramka zostaje zamknięta po restarcie")
        self.finish(card, self.checks(card)[0]["check"], 0)
        self.assertTrue(wait_until(lambda: self.ticket_starts(card, mapping["#3"]), timeout=20), self.events(card))

    def test_now_card_merge_is_fast_forward_and_closes(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=40), self.events(card))
        self.assertTrue(self.of(card, mergecheck.ARMED))
        self.assertFalse(self.checks(card))


if __name__ == "__main__":
    unittest.main()
