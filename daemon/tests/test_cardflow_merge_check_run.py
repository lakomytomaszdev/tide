"""PT-329 (K-88): przebieg próby testów po scaleniu pod zgodą w kolejce outside, wynik i odmowa.

Próbę z poleceniem zakłada PT-318 (`merge.check-requested`). Tu: pseudokrok `merge-check-<ticket>-<n>`, zgoda tą
samą ścieżką co prośba `outside` kroku (reguły projektu, PT-311), start w kolejce outside w worktree gałęzi karty,
zapis `merge.check-finished` i problem `merge-check-denied` po odmowie.

`MergeCheckRunTest` — silnik bez wątku przepływu, dziennik ułożony ręcznie, prawdziwe `make` i atrapa
`systemd-run` (biegnie w piaskownicy kroku). `MergeCheckRunFlowTest` — pełny przepływ z prawdziwym gitem; test
odtwarzający konflikt semantyczny dwóch ticketów: `test_semantic_conflict_of_two_tickets_fails_the_check_of_the_second`.
"""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow import engine as engine_mod
from tide_daemon.cardflow import mergecheck
from tide_daemon.cardflow import trialhold
from tide_daemon.cardflow.decisions import build_decisions
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import derived, fold, ticket_of
from tide_daemon.tasks import stepstop

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from . import test_cardflow_merge_check as gate_tests     # klasa PT-318 tylko przez moduł: jej testy biegną raz

START = "a" * 40
TICKET = "PT-2"
MAKEFILE = ("test-changed:\n"
            "\t@echo \"przebieg BASE=$(BASE)\"\n"
            "\t@if [ -f slow ]; then sleep 3; fi\n"
            "\t@if [ -f fail ]; then echo 'test PT-1 pada'; exit 3; fi\n"
            "\t@echo ok\n")
RULE = "make test-changed *"


class MergeCheckRunTest(FlowInProcess):
    merge_checks_pass = False

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.repo, "Makefile"), "w", encoding="utf-8") as fh:
            fh.write(MAKEFILE)
        git(self.repo, "add", "Makefile")
        git(self.repo, "commit", "-qm", "makefile")
        self.head = git(self.repo, "rev-parse", "HEAD").strip()
        self.card_id = self.card(description="Dodaj eksport.")
        self.flow.stop()
        self.engine = self.flow.engine
        self.engine._merge_check_contract = lambda card: "make test-changed BASE={base}"
        journal = self.engine.journal
        journal.append(self.card_id, "card.started", project="proj", repo=self.repo, entry="plan",
                       cardBranch="master", baseRef="refs/heads/master", baseBranch="master",
                       baseCommit=self.master, baseVersion=1, executor="Sonnet")
        journal.append(self.card_id, "result", action="card-branch", key="master", ok=True, branch="master",
                       worktree=self.repo, base=self.master)
        journal.append(self.card_id, mergecheck.ARMED)
        self.request(1)

    # --- pomocnicze -----------------------------------------------------------------------------

    def request(self, n, retry_of=""):
        return self.engine.journal.append(
            self.card_id, mergecheck.REQUESTED, ticket=TICKET, check=f"{TICKET}:{n}", start=START, before="b" * 40,
            after=self.head, command=f"make test-changed BASE={START}", source="makefile", mergeSeq=None,
            retryOf=retry_of)

    def remember_rule(self):
        with open(self.settings, encoding="utf-8") as fh:
            data = json.load(fh)
        data["outsideCommands"] = {"proj": [RULE]}
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def events(self, card_id=None):
        return self.engine.journal.read(card_id or self.card_id)

    def of(self, kind, card_id=None):
        return [e for e in self.events(card_id) if e["type"] == kind]

    def state(self):
        return fold(self.events())

    def tick(self):
        return self.engine._merge_checks(self.card_id)

    def name(self, n=1):
        return mergecheck.step_name(f"{TICKET}:{n}")

    def decisions(self, name):
        return [e for e in self.of("step.decision") if e.get("step") == name]

    def starts(self, name):
        return [e for e in self.of("outside.started") if e.get("step") == name]

    def finished(self):
        return self.of(mergecheck.FINISHED)

    def held(self):
        return [(entry["check"], entry["status"]) for entry in mergecheck.gate(self.state())]

    def run_to_end(self, timeout=20):
        """Kolejne przebiegi bramki do zapisu wyniku próby (atrapa `systemd-run` uruchamia prawdziwe `make`)."""
        def done():
            self.tick()
            return self.finished()
        self.assertTrue(wait_until(done, timeout=timeout), self.events())

    def assert_history_ok(self):
        trialhold.read_process_history(self.engine.journal, self.card_id)   # ValueError = nieznana tożsamość

    def fresh_engine(self):
        """Restart usługi: nowy silnik na tej samej tablicy, bez pamięci procesów i kolejki."""
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.models import executors
        engine = FlowEngine(self.store, claude_projects=os.path.join(self.base, "home", ".claude", "projects"),
                            systemd_run=self.fakes["systemd-run"], catalog=executors.default_catalog,
                            tmp_root=os.path.join(self.base, "tmp"), settings=self.settings,
                            which=lambda name: self.fakes.get(name), grace=5.0)
        engine._merge_check_contract = self.engine._merge_check_contract
        self.engine = engine
        return engine

    def consent_decisions(self, name):
        return [d for d in build_decisions(self.state()) if d["step"] == name]

    # --- zgoda ----------------------------------------------------------------------------------

    def test_without_project_rule_the_check_asks_and_does_not_start(self):
        self.tick()
        self.tick()
        name = self.name()
        steps = self.of(mergecheck.STEP)
        self.assertEqual([(s["step"], s["ticket"], s["check"], s["kind"], s["worktree"], s["after"]) for s in steps],
                         [(name, TICKET, f"{TICKET}:1", "merge-check", self.repo, self.head)])
        self.assertFalse(self.decisions(name), "bez reguły projektu nie ma zgody z urzędu")
        self.assertFalse(self.starts(name))
        self.assertEqual(self.held(), [(f"{TICKET}:1", "pending")])
        asked = self.consent_decisions(name)
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0]["kind"], "permission")
        self.assertEqual([(o["choice"], o["method"], o["params"]) for o in asked[0]["options"]],
                         [("run-always", "ResolveCardStep", {"choice": "run-always", "step": name}),
                          ("run", "ResolveCardStep", {"choice": "run", "step": name}),
                          ("deny", "ResolveCardStep", {"choice": "deny", "step": name})])
        self.assertIn(f"make test-changed BASE={START}", asked[0]["happened"])
        view = derived(self.state(), {"column": "progress"})
        self.assertEqual(view["column"], "waiting", view)
        self.assertEqual([c["phase"] for c in view["flow"]["mergeChecks"]], ["consent"])
        # Decyzja człowieka z panelu: zgoda na tę jedną próbę.
        reply = self.engine.resolve_step(self.card_id, {"choice": "run", "step": name})
        self.assertEqual(reply["choice"], "run")
        decision = self.decisions(name)[-1]
        self.assertEqual((decision["check"], decision["kind"], decision["command"], "auto" in decision),
                         (f"{TICKET}:1", "merge-check", f"make test-changed BASE={START}", False))
        self.assertFalse(self.consent_decisions(name), "po decyzji panel nie pyta drugi raz")
        self.run_to_end()
        self.assertEqual([(e["ticket"], e["check"], e["exit"]) for e in self.finished()], [(TICKET, f"{TICKET}:1", 0)])
        self.assertTrue(self.finished()[0]["log"].endswith(os.path.join(f".{name}.step", "outside", "outside.log")))
        started = self.starts(name)[0]
        self.assertEqual((started["worktree"], started["head"], started["command"]),
                         (self.repo, self.head, f"make test-changed BASE={START}"))
        with open(started["log"], encoding="utf-8") as fh:
            self.assertIn(f"przebieg BASE={START}", fh.read())
        self.assertEqual(self.held(), [], "wynik 0 otwiera bramkę")
        self.assert_history_ok()

    def test_consent_question_and_denial_wait_for_me_while_another_ticket_works(self):
        """Review r1 MAJOR-1: pytanie o zgodę i odmowa to decyzje karty — „Czeka na mnie” mimo pracującego ticketu
        (jak decyzja opiekuna); próba w kolejce zostaje „W toku”, a pauza i ręczny Backlog mają pierwszeństwo."""
        self.tick()
        name = self.name()
        self.engine.journal.append(self.card_id, "step.started", step="K-1-PT-3-1", ticket="PT-3", kind="ticket",
                                   executor="Sonnet", worktree=self.repo)
        self.assertTrue(self.state().running())
        view = self.view_without_plan()
        self.assertEqual(view["column"], "waiting", view)
        self.assertEqual(view["question"], f"Uruchomić testy po scaleniu {TICKET}: make test-changed BASE={START}?")
        self.assertEqual(view["note"], f"{TICKET}: testy po scaleniu czekają na zgodę na polecenie poza piaskownicą")
        self.assertIsNone(self.view_without_plan("backlog"), "ręczny Backlog wygrywa")
        self.engine.journal.append(self.card_id, "card.paused")
        self.assertEqual(self.view_without_plan()["column"], "progress", "pauza wygrywa")
        self.engine.journal.append(self.card_id, "card.resumed")
        self.engine.resolve_step(self.card_id, {"choice": "deny", "step": name})
        view = self.view_without_plan()
        self.assertEqual(view["column"], "waiting", view)
        self.assertEqual(view["note"], f"{TICKET}: odmowa zgody na testy po scaleniu — ponów albo zaakceptuj")
        self.assertEqual([c["status"] for c in view["flow"]["mergeChecks"]], ["merge-check-denied"])
        # Ten sam stan na karcie z planem (fixture bez propozycji planu): pytanie o zgodę wygrywa z resztą reguł.
        self.assertEqual(derived(self.state(), {"column": "progress"})["note"], view["note"])

    def view_without_plan(self, column="progress"):
        """Widok tego samego dziennika z wejściem `now` (fixture nie ma propozycji planu, a reguły karty z planem
        zwracają wtedy „propozycja planu wymaga poprawy” — tu sprawdzamy kolejność reguł bramki i pracy)."""
        events = [{**e, "entry": "now"} if e["type"] == "card.started" else e for e in self.events()]
        return derived(fold(events), {"column": column})

    def test_queued_check_stays_in_progress_while_another_ticket_works(self):
        self.remember_rule()
        with mock.patch.object(engine_mod, "OUTSIDE_PER_PROJECT", 1):
            self.engine._outside_active = {"K-1-PT-9-1": (self.card_id, "tide-outside-K-1-PT-9-1")}
            self.tick()
            self.engine.journal.append(self.card_id, "step.started", step="K-1-PT-3-1", ticket="PT-3",
                                       kind="ticket", executor="Sonnet", worktree=self.repo)
            view = self.view_without_plan()
            self.engine._outside_active = {}
        self.assertEqual([c["phase"] for c in view["flow"]["mergeChecks"]], ["queued"])
        self.assertEqual(view["column"], "progress", view)
        self.assertNotIn("testy po scaleniu", view["note"], "próba w kolejce nie zasłania pracy innego ticketu")
        self.assertFalse(view.get("question"))

    def test_project_rule_gives_auto_decision_and_a_failing_run_keeps_the_gate_closed(self):
        self.remember_rule()
        with open(os.path.join(self.repo, "fail"), "w", encoding="utf-8") as fh:
            fh.write("1")
        self.tick()
        name = self.name()
        decision = self.decisions(name)
        self.assertEqual([(d["choice"], d.get("auto"), d["check"]) for d in decision], [("run", True, f"{TICKET}:1")])
        self.run_to_end()
        # `make` kończy się kodem 2, gdy przepis padł (tu `exit 3`); liczba różna od 0 trafia do wyniku bez zmian.
        self.assertEqual([e["exit"] for e in self.finished()], [2])
        self.assertEqual([e["exit"] for e in self.of("outside.finished")], [2])
        with open(self.finished()[0]["log"], encoding="utf-8") as fh:
            self.assertIn("test PT-1 pada", fh.read())
        self.assertEqual(self.held(), [(f"{TICKET}:1", "merge-check-failed")])
        problem = [d for d in build_decisions(self.state()) if d["kind"] == "merge-check"]
        self.assertEqual([d["id"] for d in problem], [f"merge-check-failed:{TICKET}:1"])
        self.tick()
        self.assertEqual(len(self.starts(name)), 1, "zakończona próba nie startuje drugi raz")
        self.assertEqual(len(self.finished()), 1)
        self.assert_history_ok()

    # --- odmowa i ponowienie --------------------------------------------------------------------

    def test_denial_closes_the_gate_and_the_retry_needs_a_new_decision_without_auto_consent(self):
        self.tick()
        name = self.name()
        self.engine.resolve_step(self.card_id, {"choice": "deny", "step": name, "instead": "nie teraz"})
        self.assertEqual(self.held(), [(f"{TICKET}:1", "merge-check-denied")])
        self.tick()
        self.assertFalse(self.starts(name), "odmowa nie startuje przebiegu")
        problems = [d for d in build_decisions(self.state()) if d["kind"] == "merge-check"]
        self.assertEqual([d["id"] for d in problems], [f"merge-check-denied:{TICKET}:1"])
        self.assertEqual(problems[0]["options"][0]["params"],
                         {"choice": "retry-merge-check", "ticket": TICKET, "check": f"{TICKET}:1"})
        self.assertFalse(self.consent_decisions(name), "po odmowie nie ma pytania o zgodę tej próby")
        view = derived(self.state(), {"column": "progress"})
        self.assertEqual(view["column"], "waiting")
        with self.assertRaises(FlowError):
            self.engine.resolve_step(self.card_id, {"choice": "run", "step": name})   # już rozstrzygnięta
        # Ponowienie (PT-318): nowa próba i nowy pseudokrok. Reguła projektu jest, ale odmowa człowieka tego
        # polecenia w tym tickecie wygrywa (PT-311): bez zgody automatycznej, znowu pytanie.
        self.remember_rule()
        reply = self.engine.resolve_step(self.card_id, {"choice": "retry-merge-check", "ticket": TICKET,
                                                        "check": f"{TICKET}:1"})
        self.assertEqual(reply["check"], f"{TICKET}:2")
        self.tick()
        second = self.name(2)
        self.assertFalse(self.decisions(second))
        self.assertEqual([(e["step"], e["reason"]) for e in self.of("outside.auto-skipped")],
                         [(second, "denied-before")])
        self.assertFalse(self.starts(second))
        self.assertEqual(len(self.consent_decisions(second)), 1)
        self.assertEqual(self.held(), [(f"{TICKET}:2", "pending")])
        self.assert_history_ok()

    def test_consent_of_one_check_does_not_carry_over_to_the_retry(self):
        with open(os.path.join(self.repo, "fail"), "w", encoding="utf-8") as fh:
            fh.write("1")
        self.tick()
        self.engine.resolve_step(self.card_id, {"choice": "run", "step": self.name()})
        self.run_to_end()
        self.assertEqual(self.held(), [(f"{TICKET}:1", "merge-check-failed")])
        self.engine.resolve_step(self.card_id, {"choice": "retry-merge-check", "ticket": TICKET,
                                                "check": f"{TICKET}:1"})
        self.tick()
        self.tick()
        second = self.name(2)
        self.assertFalse(self.decisions(second), "zgoda pierwszej próby nie przechodzi na kolejną")
        self.assertFalse(self.starts(second))
        self.assertEqual(len(self.consent_decisions(second)), 1)
        self.assertEqual(len(self.of("outside.started")), 1)

    def test_human_denial_after_auto_consent_before_start_wins(self):
        self.remember_rule()
        with mock.patch.object(engine_mod, "OUTSIDE_PER_PROJECT", 1):
            self.engine._outside_active = {"K-1-PT-9-1": (self.card_id, "tide-outside-K-1-PT-9-1")}
            self.tick()
            name = self.name()
            self.assertEqual([d.get("auto") for d in self.decisions(name)], [True])
            self.assertFalse(self.starts(name), "miejsce zajęte")
            self.engine.resolve_step(self.card_id, {"choice": "deny", "step": name})
            self.assertEqual([e["reason"] for e in self.of("outside.decision-invalidated")], ["denied"])
            self.assertEqual(self.held(), [(f"{TICKET}:1", "merge-check-denied")])
            self.engine._outside_active = {}
            self.tick()
        self.assertFalse(self.starts(name), "odmowa człowieka wygrywa ze zgodą automatyczną")

    # --- kolejka, pauza, zatrzymanie ------------------------------------------------------------

    def test_check_waits_in_the_outside_queue_while_the_slot_is_taken(self):
        self.remember_rule()
        with mock.patch.object(engine_mod, "OUTSIDE_PER_PROJECT", 1):
            self.engine._outside_active = {"K-1-PT-9-1": (self.card_id, "tide-outside-K-1-PT-9-1")}
            for _ in range(3):
                self.tick()
            name = self.name()
            self.assertFalse(self.starts(name), "limit kolejki nie jest omijany")
            self.assertIn(name, self.engine._outside_waiting)
            self.assertEqual([c["phase"] for c in derived(self.state(), {"column": "progress"})["flow"]["mergeChecks"]],
                             ["queued"])
            self.engine._outside_active = {}
            self.run_to_end()
        self.assertEqual([e["exit"] for e in self.finished()], [0])
        self.assertNotIn(name, self.engine._outside_waiting)

    def test_paused_card_gets_no_auto_consent_and_does_not_start_until_resumed(self):
        self.remember_rule()
        self.engine.journal.append(self.card_id, "card.paused")
        self.tick()
        name = self.name()
        self.assertFalse(self.decisions(name))
        self.assertEqual([(e["step"], e["reason"]) for e in self.of("outside.auto-skipped")], [(name, "paused")])
        # Zgoda człowieka na wstrzymanej karcie też nie startuje przebiegu.
        self.engine.resolve_step(self.card_id, {"choice": "run", "step": name})
        self.tick()
        self.assertFalse(self.starts(name))
        self.engine.journal.append(self.card_id, "card.resumed")
        self.run_to_end()
        self.assertEqual(len(self.starts(name)), 1)

    def test_paused_card_after_auto_consent_returns_to_the_queue_on_resume(self):
        self.remember_rule()
        with mock.patch.object(engine_mod, "OUTSIDE_PER_PROJECT", 1):
            self.engine._outside_active = {"K-1-PT-9-1": (self.card_id, "tide-outside-K-1-PT-9-1")}
            self.tick()
            name = self.name()
            self.assertEqual([d.get("auto") for d in self.decisions(name)], [True])
            self.engine.journal.append(self.card_id, "card.paused")
            self.engine._outside_active = {}
            self.tick()
            self.assertFalse(self.starts(name), "wstrzymana karta nie startuje próby")
            self.assertNotIn(name, self.engine._outside_waiting, "i nie trzyma miejsca w kolejce")
            self.engine.journal.append(self.card_id, "card.resumed")
            self.run_to_end()
        self.assertEqual(len(self.starts(name)), 1)

    def test_stopped_unit_without_exit_code_gives_stopped_result(self):
        self.tick()
        name = self.name()
        self.engine.resolve_step(self.card_id, {"choice": "run", "step": name})
        folder = tempfile.mkdtemp(prefix="merge-check-stopped-")
        self.addCleanup(subprocess.run, ["rm", "-rf", folder], check=False)
        os.makedirs(os.path.join(folder, "outside", "in"))
        log = os.path.join(folder, "outside", "outside.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("przerwany przebieg\n")
        # Jednostka zatrzymana (`systemctl --user stop …scope`) bez pliku kodu wyjścia (PT-315).
        self.engine.journal.append(self.card_id, "outside.started", step=name, ticket=TICKET,
                                   command=f"make test-changed BASE={START}", unit=f"tide-outside-{name}", log=log,
                                   exitFile=os.path.join(folder, "outside", "in", "exit-code"), worktree=self.repo,
                                   head=self.head)

        def stopped(*args, timeout=30):
            out = b"inactive\n" if args[0] == "is-active" else b""
            return subprocess.CompletedProcess(args, 3 if args[0] == "is-active" else 0, out, b"")
        with mock.patch.object(stepstop, "_systemctl", stopped), mock.patch.object(engine_mod, "OUTSIDE_STOP_GRACE", 0):
            self.tick()
        self.assertEqual([e["exit"] for e in self.finished()], ["zatrzymane"])
        self.assertEqual([e["exit"] for e in self.of("outside.finished")], ["zatrzymane"])
        self.assertEqual(self.held(), [(f"{TICKET}:1", "merge-check-failed")], "zatrzymane nie jest wynikiem 0")
        self.assert_history_ok()

    # --- restart usługi -------------------------------------------------------------------------

    def test_restart_while_waiting_for_consent_keeps_the_gate_and_one_check_step(self):
        self.tick()
        self.fresh_engine()
        self.tick()
        self.tick()
        name = self.name()
        self.assertEqual(len(self.of(mergecheck.STEP)), 1)
        self.assertFalse(self.decisions(name))
        self.assertFalse(self.starts(name))
        self.assertEqual(self.held(), [(f"{TICKET}:1", "pending")])
        self.assertEqual(len(self.consent_decisions(name)), 1)

    def test_restart_seeds_the_queue_with_the_consent_of_the_check(self):
        """Review r1 NIT-1: zgoda próby zapisana przed restartem wraca do zasiewu kolejki z czasem zgody."""
        self.tick()
        name = self.name()
        self.engine.resolve_step(self.card_id, {"choice": "run", "step": name})
        engine = self.fresh_engine()
        engine._seed_outside_waiting(engine_mod.time.time())
        self.assertIn(name, engine._outside_waiting)
        self.assertEqual(engine._outside_waiting[name][0], self.card_id)

    def test_restart_during_the_run_neither_starts_it_again_nor_loses_the_result(self):
        self.remember_rule()
        with open(os.path.join(self.repo, "slow"), "w", encoding="utf-8") as fh:
            fh.write("1")
        self.tick()
        name = self.name()
        self.assertEqual(len(self.starts(name)), 1)
        self.fresh_engine()
        self.tick()
        self.assertEqual(len(self.starts(name)), 1, "po restarcie przebieg nie startuje drugi raz")
        self.assertEqual(self.held(), [(f"{TICKET}:1", "pending")], "bramka trzyma kartę do wyniku")
        self.assertEqual([c["phase"] for c in derived(self.state(), {"column": "progress"})["flow"]["mergeChecks"]],
                         ["running"])
        self.run_to_end()
        self.fresh_engine()
        self.tick()
        self.assertEqual(len(self.starts(name)), 1)
        self.assertEqual([e["exit"] for e in self.finished()], [0])
        self.assertEqual(self.held(), [])

    # --- commit planu a trwający przebieg (review r2 MAJOR-1) -----------------------------------

    def plan_commits_now(self):
        """Prawdziwe `_plan_next` na bieżącym stanie z zatwierdzonym planem bez wierszy; zwraca liczbę prób commita
        planu (atrapa `_plan_commit`, gałąź karty = worktree próby)."""
        st = self.state()
        st.plan_approved = {"seq": 1, "rows": []}
        calls = []
        with mock.patch.object(self.engine, "_planning_branch",
                               return_value={"branch": "master", "worktree": self.repo}), \
                mock.patch.object(self.engine, "_plan_commit", side_effect=lambda *a: calls.append(a)):
            self.engine._plan_next(self.card_id, st)
        return len(calls)

    def test_plan_is_not_committed_into_the_card_worktree_while_the_check_runs(self):
        self.remember_rule()
        with open(os.path.join(self.repo, "slow"), "w", encoding="utf-8") as fh:
            fh.write("1")
        self.assertEqual(self.plan_commits_now(), 1, "bez przebiegu plan jest commitowany jak dotąd")
        self.tick()
        name = self.name()
        self.assertEqual(len(self.starts(name)), 1)
        # Inny ticket karty pracuje w trakcie przebiegu: zmiana planu nie commituje go w worktree próby.
        self.engine.journal.append(self.card_id, "step.started", step="K-1-PT-3-1", ticket="PT-3", kind="ticket",
                                   executor="Sonnet", worktree=self.repo)
        self.assertEqual(self.plan_commits_now(), 0, "trwający przebieg próby to praca w worktree karty")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.starts(name)[0]["head"])
        self.run_to_end()
        self.assertEqual([e["exit"] for e in self.finished()], [0])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.plan_commits_now(), 1, "po wyniku commit planu wraca")

    # --- „Zawsze w projekcie” (review r2 NIT-1) --------------------------------------------------

    def test_run_always_remembers_the_family_and_the_retry_gets_auto_consent(self):
        with open(os.path.join(self.repo, "fail"), "w", encoding="utf-8") as fh:
            fh.write("1")
        self.tick()
        self.engine.resolve_step(self.card_id, {"choice": "run-always", "step": self.name()})
        with open(self.settings, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["outsideCommands"], {"proj": [RULE]})
        self.assertEqual([(d["choice"], d.get("auto")) for d in self.decisions(self.name())], [("run-always", None)])
        self.run_to_end()
        self.assertEqual(self.held(), [(f"{TICKET}:1", "merge-check-failed")])
        os.remove(os.path.join(self.repo, "fail"))
        self.engine.resolve_step(self.card_id, {"choice": "retry-merge-check", "ticket": TICKET,
                                                "check": f"{TICKET}:1"})
        second = self.name(2)

        def second_done():
            self.tick()
            return [e for e in self.finished() if e["check"] == f"{TICKET}:2"]
        self.assertTrue(wait_until(second_done, timeout=20), self.events())
        self.assertEqual([(d["choice"], d.get("auto")) for d in self.decisions(second)], [("run", True)],
                         "kolejna próba dostaje zgodę automatyczną z zapamiętanej reguły")
        self.assertEqual(self.held(), [])

    def test_run_always_is_refused_for_a_risky_command(self):
        self.engine.journal.append(
            self.card_id, mergecheck.REQUESTED, ticket="PT-5", check="PT-5:1", start=START, before="b" * 40,
            after=self.head, command="sh tools/check.sh", source="contract", mergeSeq=None, retryOf="")
        self.tick()
        name = mergecheck.step_name("PT-5:1")
        self.assertTrue(self.of(mergecheck.STEP))
        with self.assertRaises(FlowError) as raised:
            self.engine.resolve_step(self.card_id, {"choice": "run-always", "step": name})
        self.assertEqual(raised.exception.code, "risky-command")
        self.assertFalse(self.decisions(name))
        with open(self.settings, encoding="utf-8") as fh:
            self.assertNotIn("outsideCommands", json.load(fh))

    def test_restart_between_outside_finish_and_check_result_writes_the_result_once(self):
        self.remember_rule()
        self.tick()
        name = self.name()
        real = self.engine._merge_check_finish
        with mock.patch.object(self.engine, "_merge_check_finish", side_effect=RuntimeError("awaria")):
            def outside_done():
                try:
                    self.tick()
                except RuntimeError:
                    pass
                return self.of("outside.finished")
            self.assertTrue(wait_until(outside_done, timeout=20), self.events())
        self.assertFalse(self.finished())
        self.assertTrue(callable(real))
        self.fresh_engine()
        self.tick()
        self.tick()
        self.assertEqual([e["exit"] for e in self.finished()], [0])
        self.assertEqual(len(self.starts(name)), 1)

    # --- model ----------------------------------------------------------------------------------

    def test_only_a_human_decision_reaches_the_check_from_resolve(self):
        self.tick()
        name = self.name()
        for request in ({"choice": "run", "step": name, "auto": True}, {"choice": "allow", "step": name},
                        {"choice": "run-ticket", "step": name}):
            with self.subTest(request=request), self.assertRaises(FlowError):
                self.engine.resolve_step(self.card_id, request)
        self.assertFalse(self.decisions(name))


class MergeCheckStatusTest(unittest.TestCase):
    def test_denial_of_the_current_check_is_a_problem_and_a_stale_one_is_not(self):
        started = {"seq": 1, "type": "card.started", "entry": "plan"}
        first = {"seq": 2, "type": mergecheck.REQUESTED, "ticket": TICKET, "check": f"{TICKET}:1",
                 "command": "make test-changed BASE=x", "after": "c" * 40}
        deny = {"seq": 3, "type": "step.decision", "step": mergecheck.step_name(f"{TICKET}:1"), "ticket": TICKET,
                "choice": "deny", "check": f"{TICKET}:1"}
        second = {**first, "seq": 4, "check": f"{TICKET}:2", "retryOf": f"{TICKET}:1"}
        self.assertEqual([h["status"] for h in mergecheck.gate(fold([started, first, deny]))], ["merge-check-denied"])
        self.assertEqual([h["status"] for h in mergecheck.gate(fold([started, first, deny, second]))], ["pending"])
        self.assertEqual(mergecheck.step_name("PT-12:3"), "merge-check-PT-12-3")


class MergeCheckRunFlowTest(FlowInProcess):
    """Pełny przepływ z prawdziwym gitem i prawdziwym `make` (bez atrapy `make` z testów PT-318). Pomocnicze
    metody z testów bramki (PT-318), bez dziedziczenia ich scenariuszy."""
    merge_checks_pass = False
    # Klasa PT-318 tylko w ciele klasy (nie jako nazwa modułu): loader `unittest` nie zbiera jej testów drugi raz.
    _GATE = gate_tests.MergeCheckFlowTest
    proposal, submit, approve = _GATE.proposal, _GATE.submit, _GATE.approve
    commit_files, parallel_plan, run_plan = _GATE.commit_files, _GATE.parallel_plan, _GATE.run_plan
    merges, checks, ticket_starts, state, settle = _GATE.merges, _GATE.checks, _GATE.ticket_starts, _GATE.state, \
        _GATE.settle
    flow_view, problems = _GATE.flow_view, _GATE.problems
    assert_check_for_merge = _GATE.assert_check_for_merge
    assert_first_merge_only_after_tide_plan_commits = _GATE.assert_first_merge_only_after_tide_plan_commits
    assert_one_check_for_second_merge = _GATE.assert_one_check_for_second_merge
    del _GATE

    CHECK_SCRIPT = (
        "#!/bin/sh\n"
        "# Model zależności testów: test pliku ATRAPA-n.md czyta moduł, który zmienia ticket scalony później.\n"
        "# Test pliku F pada, gdy na gałęzi karty po F scalono inny plik ATRAPA (konflikt semantyczny).\n"
        "base=\"$1\"\n"
        "status=0\n"
        "# Pierwszy commit gałęzi karty (pierwsi rodzice), w którym jest plik — scalenie, które go wniosło.\n"
        "arrived() {\n"
        "  for c in $(git rev-list --first-parent --reverse HEAD); do\n"
        "    if git cat-file -e \"$c:$1\" 2>/dev/null; then echo \"$c\"; return; fi\n"
        "  done\n"
        "}\n"
        "for f in $(git diff --name-only \"$base\" HEAD -- 'ATRAPA-*.md'); do\n"
        "  echo \"test $f\"\n"
        "  mf=$(arrived \"$f\")\n"
        "  for g in $(git ls-files 'ATRAPA-*.md'); do\n"
        "    [ \"$g\" = \"$f\" ] && continue\n"
        "    mg=$(arrived \"$g\")\n"
        "    if [ -n \"$mf\" ] && [ -n \"$mg\" ] && [ \"$mf\" != \"$mg\" ] && "
        "git merge-base --is-ancestor \"$mf\" \"$mg\"; then\n"
        "      echo \"PADA $f: $g zmienia jego zależność\"; status=1\n"
        "    fi\n"
        "  done\n"
        "done\n"
        "exit $status\n")
    CHECK_MAKEFILE = "test-changed:\n\t@sh tools/check.sh $(BASE)\n"
    PASS_MAKEFILE = "test-changed:\n\t@echo \"testy zmian od $(BASE)\"\n"

    def setUp(self):
        super().setUp()
        with open(self.settings, encoding="utf-8") as fh:
            data = json.load(fh)
        data["outsideCommands"] = {"proj": [RULE]}     # reguła projektu: zgoda automatyczna z reguły
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    @classmethod
    def write_check(cls, root):
        os.makedirs(os.path.join(root, "tools"), exist_ok=True)
        with open(os.path.join(root, "tools", "check.sh"), "w", encoding="utf-8") as fh:
            fh.write(cls.CHECK_SCRIPT)

    def finished_checks(self, card):
        return self.of(card, mergecheck.FINISHED)

    def test_semantic_conflict_of_two_tickets_fails_the_check_of_the_second(self):
        """Ticket A (scalony pierwszy) dodaje plik z testem; ticket B, wystartowany przed scaleniem A, nie dotyka
        plików A, ale zmienia zależność testu A. Każdy ticket przechodzi testy na własnym punkcie startu. Po scaleniu
        B próba z BASE=<start B> wybiera test A i pada; przy BASE=<before> test A nie zostałby wybrany."""
        self.commit_files(self.repo, {"Makefile": self.CHECK_MAKEFILE, "tools/check.sh": self.CHECK_SCRIPT})
        card = self.card()
        mapping = self.run_plan(card, count=2, blocked=True)
        first, second, check = self.assert_one_check_for_second_merge(card)
        self.assertTrue(wait_until(lambda: self.finished_checks(card), timeout=60), self.events(card))
        result = self.finished_checks(card)[0]
        self.assertEqual((result["ticket"], result["check"]), (second["key"], f"{second['key']}:1"))
        self.assertNotEqual(result["exit"], 0)
        decision = next(e for e in self.of(card, "step.decision")
                        if e.get("step") == mergecheck.step_name(check["check"]))
        self.assertEqual((decision["choice"], decision.get("auto"), decision["check"]), ("run", True, check["check"]))
        started = next(e for e in self.of(card, "outside.started")
                       if e.get("step") == mergecheck.step_name(check["check"]))
        card_worktree = ticket_of(self.state(card), second["key"])["cardWorktree"]
        self.assertEqual((started["worktree"], started["command"]),
                         (card_worktree, f"make test-changed BASE={check['start']}"))
        with open(result["log"], encoding="utf-8") as fh:
            log = fh.read()
        a_file = f"ATRAPA-{1 if first['key'] == mapping['#1'] else 2}.md"
        self.assertIn(f"PADA {a_file}", log)
        # Przy BASE=<before> (wierzchołek karty przed scaleniem B, już z A) test A nie zostałby wybrany.
        selected = git(self.repo, "diff", "--name-only", check["before"], check["after"]).split()
        self.assertNotIn(a_file, selected)
        self.assertIn(a_file, git(self.repo, "diff", "--name-only", check["start"], check["after"]).split())
        rerun = subprocess.run(["sh", "tools/check.sh", check["before"]], cwd=card_worktree, capture_output=True,
                               text=True)
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        # Bramka zamknięta: kolejny ticket nie startuje, problem karty z ponowieniem.
        self.assertTrue(wait_until(lambda: [d["id"] for d in self.problems(card)]
                                   == [f"merge-check-failed:{check['check']}"], timeout=20), self.flow_view(card))
        self.settle()
        self.assertFalse(self.ticket_starts(card, mapping["#3"]), self.events(card))
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("kind") == "close"])

    def test_passing_check_opens_the_gate_and_the_waiting_merge_goes_through(self):
        self.commit_files(self.repo, {"Makefile": self.PASS_MAKEFILE})
        card = self.card()
        mapping = self.run_plan(card, count=3, blocked=True)
        self.assertTrue(wait_until(lambda: self.finished_checks(card), timeout=90), self.events(card))
        first = self.finished_checks(card)[0]
        self.assertEqual(first["exit"], 0)
        self.assertEqual(first["check"], self.checks(card)[0]["check"])
        # Trzecie scalenie czekało na wynik próby drugiego; po wyniku 0 przechodzi, a zablokowany ticket startuje.
        self.assertTrue(wait_until(lambda: len(self.merges(card)) == 3, timeout=90), self.events(card))
        third = self.merges(card)[2]
        self.assertGreater(third["seq"], first["seq"])
        self.assertTrue(wait_until(lambda: self.ticket_starts(card, mapping["#4"]), timeout=90), self.events(card))
        for check in self.checks(card):
            self.assertTrue(any(f["check"] == check["check"] and f["exit"] == 0
                                for f in self.finished_checks(card)), self.events(card))

    def test_without_rule_the_card_asks_and_restart_does_not_start_the_check(self):
        with open(self.settings, encoding="utf-8") as fh:
            data = json.load(fh)
        data.pop("outsideCommands", None)
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.commit_files(self.repo, {"Makefile": self.PASS_MAKEFILE})
        card = self.card()
        mapping = self.run_plan(card, count=2, blocked=True)
        _first, _second, check = self.assert_one_check_for_second_merge(card)
        name = mergecheck.step_name(check["check"])
        self.assertTrue(wait_until(lambda: any(d["step"] == name and d["kind"] == "permission"
                                               for d in self.flow_view(card).get("decisions") or []), timeout=20),
                        self.flow_view(card))
        self.restart()
        self.settle()
        self.assertFalse([e for e in self.of(card, "outside.started") if e.get("step") == name])
        self.assertFalse(self.finished_checks(card))
        self.assertFalse(self.ticket_starts(card, mapping["#3"]))
        self.assertEqual(len([e for e in self.of(card, mergecheck.STEP) if e["step"] == name]), 1)
        reply = self.flow.engine.resolve_step(card, {"choice": "run", "step": name})
        self.assertEqual(reply["choice"], "run")
        self.assertTrue(wait_until(lambda: self.finished_checks(card), timeout=60), self.events(card))
        self.assertEqual(self.finished_checks(card)[0]["exit"], 0)
        self.assertTrue(wait_until(lambda: self.ticket_starts(card, mapping["#3"]), timeout=60), self.events(card))


class SemanticConflictModelTest(unittest.TestCase):
    """Model konfliktu z testu przepływu na samym gicie (bez usługi): dwa tickety z jednej bazy, scalenia `--no-ff`."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="merge-check-model-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.root], check=False)
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "T",
               "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x"}
        self.run_git = lambda *args: subprocess.run(["git", "-C", self.root, *args], check=True, env=env,
                                                    capture_output=True, text=True).stdout.strip()
        self.env = env
        self.run_git("init", "-q", "-b", "card")
        MergeCheckRunFlowTest.write_check(self.root)
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", "base")

    def ticket(self, name, start, path):
        self.run_git("checkout", "-qb", name, start)
        with open(os.path.join(self.root, path), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
        self.run_git("add", path)
        self.run_git("commit", "-qm", name)
        self.run_git("checkout", "-q", "card")

    def check(self, base):
        return subprocess.run(["sh", "tools/check.sh", base], cwd=self.root, env=self.env, capture_output=True,
                              text=True)

    def test_check_from_start_of_b_selects_and_fails_test_of_a_but_before_does_not(self):
        start = self.run_git("rev-parse", "card")
        self.ticket("a", start, "ATRAPA-1.md")
        self.ticket("b", start, "ATRAPA-2.md")          # B startuje przed scaleniem A i nie dotyka plików A
        self.run_git("merge", "-q", "--no-ff", "-m", "merge a", "a")
        before = self.run_git("rev-parse", "card")
        self.assertEqual(self.check(start).returncode, 0, "po samym A testy przechodzą")
        self.run_git("merge", "-q", "--no-ff", "-m", "merge b", "b")
        failed = self.check(start)
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("PADA ATRAPA-1.md", failed.stdout)
        passed = self.check(before)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertNotIn("test ATRAPA-1.md", passed.stdout, "przy BASE=<before> test A nie jest wybrany")


if __name__ == "__main__":
    unittest.main()
