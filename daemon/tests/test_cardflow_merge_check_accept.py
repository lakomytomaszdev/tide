"""PT-320 (K-88): testy po scaleniu — „Akceptuj mimo porażki” dla dokładnej próby (`ticket`, `check`, `after`).

Przyczyna interwencji: bramka testów po scaleniu (PT-318) zamknięta porażką, odmową zgody albo brakiem polecenia
miała jedno wyjście — ponowienie. Gdy porażka nie dotyczyła karty (np. znany, niezależny test), karta stała do ręcznej
poprawki dziennika przez koordynatora. Teraz panel karty ma drugie wyjście: jawną akceptację tej jednej próby.

- `AcceptFoldTest` — model (`mergecheck`, `build_decisions`) bez usługi,
- `AcceptEngineTest` — silnik bez wątku przepływu, dziennik ułożony ręcznie: walidacja, brak zapisu przy błędzie,
  brak polecenia, restart,
- `AcceptFlowTest` — pełny przepływ z prawdziwym gitem: akceptacja przepuszcza kartę, kolejne scalenie nie-FF
  znowu trzyma bramkę,
- `AcceptDbusTest` — test odtwarzający przez prywatną szynę D-Bus: porażka prawdziwego `make` pod regułą projektu,
  akceptacja z pytania w Snapshot() i start zablokowanego ticketu. Na bazie ticketu `ResolveCardStep` odrzuca
  `accept-merge-check` (`bad-request`), więc test pada.
"""
import json
import os
import unittest

from tide_daemon.cardflow import mergecheck
from tide_daemon.cardflow.decisions import build_decisions
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase, git
from . import test_cardflow_merge_check as gate_tests     # klasy PT-318 tylko przez moduł: ich testy biegną raz

START, AFTER = "a" * 40, "c" * 40
TICKET = "PT-2"
STARTED = gate_tests.STARTED
requested, finished = gate_tests.requested, gate_tests.finished


def denial(check="PT-2:1", ticket="PT-2", seq=20):
    return {"seq": seq, "type": "step.decision", "step": mergecheck.step_name(check), "ticket": ticket,
            "kind": "merge-check", "check": check, "choice": "deny"}


def accepted(check="PT-2:1", after=AFTER, ticket="PT-2", seq=60):
    return {"seq": seq, "type": mergecheck.ACCEPTED, "ticket": ticket, "check": check, "after": after, "by": "user"}


PROBLEMS = {
    mergecheck.FAILED: [STARTED, requested(), finished("PT-2:1", 1)],
    mergecheck.DENIED: [STARTED, requested(), denial()],
    mergecheck.UNAVAILABLE: [STARTED, requested(unavailable=True)],
}


def held(events):
    return [(entry["check"], entry["status"]) for entry in mergecheck.gate(fold(events))]


class AcceptFoldTest(unittest.TestCase):
    def test_acceptance_of_the_current_check_opens_the_gate_for_each_problem(self):
        for problem, events in PROBLEMS.items():
            with self.subTest(problem=problem):
                self.assertEqual(held(events), [("PT-2:1", problem)])
                self.assertEqual(held([*events, accepted()]), [])

    def test_acceptance_of_another_check_or_tip_changes_nothing(self):
        events = PROBLEMS[mergecheck.FAILED]
        for stale in (accepted(check="PT-2:2"), accepted(after="d" * 40), accepted(ticket="PT-3"),
                      {**accepted(), "after": None}):
            with self.subTest(stale=stale):
                self.assertEqual(held([*events, stale]), [("PT-2:1", mergecheck.FAILED)])

    def test_acceptance_does_not_carry_over_to_the_next_check(self):
        events = [*PROBLEMS[mergecheck.FAILED], accepted()]
        retry = requested(n=2, seq=70, retryOf="PT-2:1")
        self.assertEqual(held([*events, retry]), [("PT-2:2", mergecheck.PENDING)])
        self.assertEqual(held([*events, retry, finished("PT-2:2", 1, seq=80)]), [("PT-2:2", mergecheck.FAILED)])
        # Akceptacja starszej próby zapisana po ponowieniu niczego nie otwiera.
        self.assertEqual(held([*events, retry, finished("PT-2:2", 1, seq=80), accepted(seq=90)]),
                         [("PT-2:2", mergecheck.FAILED)])
        other = requested(ticket="PT-3", seq=70, after="e" * 40)
        self.assertEqual(held([*events, other]), [("PT-3:1", mergecheck.PENDING)])

    def test_problem_offers_retry_and_acceptance_of_the_exact_check_and_tip(self):
        for problem, events in PROBLEMS.items():
            with self.subTest(problem=problem):
                found = [d for d in build_decisions(fold(events)) if d["kind"] == "merge-check"]
                self.assertEqual([d["id"] for d in found], [f"{problem}:PT-2:1"])
                options = found[0]["options"]
                self.assertEqual([(o["method"], o["params"], o["recommended"]) for o in options], [
                    ("ResolveCardStep", {"choice": "retry-merge-check", "ticket": "PT-2", "check": "PT-2:1"}, True),
                    ("ResolveCardStep", {"choice": "accept-merge-check", "ticket": "PT-2", "check": "PT-2:1",
                                         "after": AFTER}, False)])
                self.assertEqual(options[1]["title"], "Akceptuj mimo porażki")
                self.assertFalse([d for d in build_decisions(fold([*events, accepted()]))
                                  if d["kind"] == "merge-check"], "zaakceptowana próba nie jest już pytaniem")


class AcceptEngineTest(FlowInProcess):
    """Silnik bez wątku przepływu; dziennik karty ułożony ręcznie jak w testach przebiegu (PT-329)."""
    merge_checks_pass = False

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.engine = self.flow.engine

    def new_card(self):
        """Karta z uzbrojoną bramką i bieżącą próbą `PT-2:1`; `problem_events` — zdarzenia po próbie."""
        card = self.card(description="Dodaj eksport.")
        journal = self.engine.journal
        journal.append(card, "card.started", project="proj", repo=self.repo, entry="plan", cardBranch="master",
                       baseRef="refs/heads/master", baseBranch="master", baseCommit=self.master, baseVersion=1,
                       executor="Sonnet")
        journal.append(card, "result", action="card-branch", key="master", ok=True, branch="master",
                       worktree=self.repo, base=self.master)
        journal.append(card, mergecheck.ARMED)
        return card

    def request(self, card, n=1, *, unavailable=False, ticket=TICKET, after=AFTER):
        data = {"ticket": ticket, "check": f"{ticket}:{n}", "start": START, "before": "b" * 40, "after": after,
                "mergeSeq": None, "retryOf": f"{ticket}:{n - 1}" if n > 1 else ""}
        if unavailable:
            data["unavailable"] = True
        else:
            data.update(command=f"make test-changed BASE={START}", source="makefile")
        return self.engine.journal.append(card, mergecheck.REQUESTED, **data)

    def fail(self, card, n=1, code=1, ticket=TICKET):
        self.engine.journal.append(card, mergecheck.FINISHED, ticket=ticket, check=f"{ticket}:{n}", exit=code,
                                   log="/tmp/przebieg.log")

    def deny(self, card, n=1):
        name = mergecheck.step_name(f"{TICKET}:{n}")
        self.engine.journal.append(card, "step.decision", step=name, ticket=TICKET, kind="merge-check",
                                   check=f"{TICKET}:{n}", choice="deny", answer="", command="make test-changed",
                                   executor="")

    def accept(self, card, check="PT-2:1", after=AFTER, ticket=TICKET, **extra):
        return self.engine.resolve_step(card, {"choice": "accept-merge-check", "ticket": ticket, "check": check,
                                               "after": after, **extra})

    def journal(self, card):
        return self.engine.journal.read(card)

    def accepted_events(self, card):
        return [e for e in self.journal(card) if e["type"] == mergecheck.ACCEPTED]

    def assert_refused(self, card, request, code=None):
        before = self.journal(card)
        with self.assertRaises(FlowError) as raised:
            self.engine.resolve_step(card, request)
        if code:
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.journal(card), before, "błąd bez zapisu")
        return raised.exception

    # --- scenariusze ----------------------------------------------------------------------------

    def test_acceptance_records_exact_check_and_opens_the_gate_for_failure_denial_and_unavailable(self):
        for problem in (mergecheck.FAILED, mergecheck.DENIED, mergecheck.UNAVAILABLE):
            with self.subTest(problem=problem):
                card = self.new_card()
                self.request(card, unavailable=problem == mergecheck.UNAVAILABLE)
                if problem == mergecheck.FAILED:
                    self.fail(card, code="przekroczony czas")
                elif problem == mergecheck.DENIED:
                    self.deny(card)
                self.assertEqual(self.engine._merge_checks(card)[0]["status"], problem)
                before = len(self.journal(card))
                reply = self.accept(card)
                self.assertEqual((reply["ok"], reply["choice"], reply["check"], reply["after"]),
                                 (True, "accept-merge-check", "PT-2:1", AFTER))
                self.assertEqual(self.engine._merge_checks(card), [], "bramka otwarta dla tej próby")
                added = self.journal(card)[before:]
                self.assertEqual([{k: e.get(k) for k in ("type", "ticket", "check", "after", "by")} for e in added],
                                 [{"type": mergecheck.ACCEPTED, "ticket": TICKET, "check": "PT-2:1", "after": AFTER,
                                   "by": "user"}], "jedno zdarzenie akceptacji; żadnego polecenia ani pseudokroku")
                self.assertFalse([e for e in self.journal(card) if e["type"] in ("outside.started", mergecheck.STEP)])

    def test_stale_passed_pending_open_and_repeated_acceptance_is_refused_without_writing(self):
        card = self.new_card()
        self.assert_refused(card, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                   "after": AFTER}, "not-waiting")        # bramka otwarta — brak próby
        self.request(card)
        self.assert_refused(card, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                   "after": AFTER}, "not-waiting")        # próba czeka na przebieg
        self.fail(card)
        for stale in ({"check": "PT-2:2"}, {"check": "PT-2:0"}, {"after": "d" * 40}, {"ticket": "PT-3"}):
            with self.subTest(stale=stale):
                request = {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1", "after": AFTER,
                           **stale}
                self.assert_refused(card, request, "not-waiting")
        for bad in ({"after": AFTER[:12]}, {"after": ""}, {"check": ""}, {"ticket": 7}, {"step": "x"},
                    {"answer": "tak"}, {"auto": True}):
            with self.subTest(bad=bad):
                request = {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1", "after": AFTER,
                           **bad}
                self.assert_refused(card, request, "bad-request")
        self.assert_refused(card, {"choice": "retry-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                   "after": AFTER}, "bad-request")       # `after` tylko z akceptacją
        self.assertTrue(self.accept(card)["ok"])
        self.assert_refused(card, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                   "after": AFTER}, "not-waiting")        # już zaakceptowana
        self.assert_refused(card, {"choice": "retry-merge-check", "ticket": TICKET, "check": "PT-2:1"},
                            "not-waiting")                               # otwarta bramka nie czeka na ponowienie
        passed = self.new_card()
        self.request(passed)
        self.fail(passed, code=0)
        self.assert_refused(passed, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                     "after": AFTER}, "not-waiting")      # próba udana

    def test_only_an_explicit_call_accepts_never_the_caretaker(self):
        card = self.new_card()
        self.request(card)
        self.fail(card)
        request = {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1", "after": AFTER}
        for context in ({"caretaker_problem": "flow-error:advance"}, {"caretaker_step": "K-1-care-1"}):
            with self.subTest(context=context):
                before = self.journal(card)
                with self.assertRaises(FlowError):
                    self.engine.resolve_step(card, dict(request), **context)
                self.assertEqual(self.journal(card), before)
        self.assertEqual(self.engine._merge_checks(card)[0]["status"], mergecheck.FAILED)

    def test_acceptance_does_not_carry_over_to_the_retry_or_another_merge(self):
        card = self.new_card()
        self.request(card)
        self.fail(card)
        tip = git(self.repo, "rev-parse", "HEAD").strip()
        reply = self.engine.resolve_step(card, {"choice": "retry-merge-check", "ticket": TICKET, "check": "PT-2:1"})
        self.assertEqual(reply["check"], "PT-2:2")
        # Akceptacja starszej próby po ponowieniu — nieaktualna; nowa próba trzyma bramkę.
        self.assert_refused(card, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                   "after": AFTER}, "not-waiting")
        # Repozytorium bez Makefile i kontraktu: nowa próba na wierzchołku `tip` jest niedostępna i trzyma bramkę.
        self.assertEqual([(e["check"], e["status"], e["requested"]["after"])
                          for e in mergecheck.gate(fold(self.journal(card)))],
                         [("PT-2:2", mergecheck.UNAVAILABLE, tip)])
        self.assert_refused(card, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:2",
                                   "after": AFTER}, "not-waiting")        # stary wierzchołek
        self.assertTrue(self.accept(card, check="PT-2:2", after=tip)["ok"])
        self.assertEqual(mergecheck.gate(fold(self.journal(card))), [])
        # Kolejne scalenie nie-FF innego ticketu zakłada własną próbę, która znowu trzyma bramkę.
        self.request(card, ticket="PT-3", after="e" * 40)
        self.assertEqual([(e["check"], e["status"]) for e in mergecheck.gate(fold(self.journal(card)))],
                         [("PT-3:1", mergecheck.PENDING)])

    def test_restart_after_acceptance_keeps_the_gate_open_for_this_check(self):
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.models import executors
        card = self.new_card()
        self.request(card, unavailable=True)
        self.accept(card)
        self.engine._merge_checks(card)
        count = len(self.journal(card))
        fresh = FlowEngine(self.store, claude_projects=os.path.join(self.base, "home", ".claude", "projects"),
                           systemd_run=self.fakes["systemd-run"], catalog=executors.default_catalog,
                           tmp_root=os.path.join(self.base, "tmp"), settings=self.settings,
                           which=lambda name: self.fakes.get(name), grace=5.0)
        self.assertEqual(fresh._merge_checks(card), [], "restart nie zamyka bramki zaakceptowanej próby")
        self.assertEqual(len(fresh.journal.read(card)), count, "restart niczego nie dopisuje")
        with self.assertRaises(FlowError):
            fresh.resolve_step(card, {"choice": "accept-merge-check", "ticket": TICKET, "check": "PT-2:1",
                                      "after": AFTER})
        self.assertEqual(len(fresh.journal.read(card)), count)


class AcceptFlowTest(FlowInProcess):
    """Pełny przepływ z prawdziwym gitem, bez polecenia testów (próba niedostępna nie potrzebuje zgody)."""
    merge_checks_pass = False
    _GATE = gate_tests.MergeCheckFlowTest
    proposal, submit, approve = _GATE.proposal, _GATE.submit, _GATE.approve
    commit_files, parallel_plan, run_plan = _GATE.commit_files, _GATE.parallel_plan, _GATE.run_plan
    merges, checks, ticket_starts, state, settle = _GATE.merges, _GATE.checks, _GATE.ticket_starts, _GATE.state, \
        _GATE.settle
    flow_view, problems = _GATE.flow_view, _GATE.problems
    del _GATE

    def option(self, card, check, choice):
        decision = next(d for d in self.problems(card) if d["technical"]["check"] == check)
        return next(o for o in decision["options"] if o["params"]["choice"] == choice)

    def test_accepting_the_shown_check_lets_the_card_go_and_the_next_merge_holds_the_gate_again(self):
        card = self.card()
        mapping = self.run_plan(card, count=3, blocked=False)
        self.assertTrue(wait_until(lambda: self.checks(card), timeout=60), self.events(card))
        check = self.checks(card)[0]
        self.assertTrue(check["unavailable"])
        self.assertTrue(wait_until(lambda: [d["id"] for d in self.problems(card)]
                                   == [f"merge-check-unavailable:{check['check']}"], timeout=40), self.flow_view(card))
        self.settle()
        self.assertEqual(len(self.merges(card)), 2, "bramka zamknięta: trzecie scalenie czeka")
        option = self.option(card, check["check"], "accept-merge-check")
        self.assertEqual(option["params"], {"choice": "accept-merge-check", "ticket": check["ticket"],
                                            "check": check["check"], "after": check["after"]})
        reply = self.flow.engine.resolve_step(card, dict(option["params"]))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual([(e["ticket"], e["check"], e["after"], e["by"]) for e in self.of(card, mergecheck.ACCEPTED)],
                         [(check["ticket"], check["check"], check["after"], "user")])
        # Bramka otwarta: trzecie scalenie przechodzi i zakłada własną próbę, która znowu trzyma kartę.
        self.assertTrue(wait_until(lambda: len(self.merges(card)) == 3, timeout=60), self.events(card))
        third = self.merges(card)[2]["key"]
        self.assertIn(third, mapping.values())
        self.assertTrue(wait_until(lambda: any(c["ticket"] == third for c in self.checks(card)), timeout=20),
                        self.events(card))
        new = next(c for c in self.checks(card) if c["ticket"] == third)
        self.assertTrue(wait_until(lambda: [d["id"] for d in self.problems(card)]
                                   == [f"merge-check-unavailable:{new['check']}"], timeout=20), self.flow_view(card))
        self.settle()
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("kind") == "close"],
                         "akceptacja poprzedniej próby nie otwiera bramki nowej")
        with self.assertRaises(FlowError):        # ponowne użycie starego pytania — nieaktualne
            self.flow.engine.resolve_step(card, dict(option["params"]))
        self.assertEqual(len(self.of(card, mergecheck.ACCEPTED)), 1)


class AcceptDbusTest(FlowCase):
    """Test odtwarzający przez prywatną szynę D-Bus: dwa tickety równolegle, trzeci zablokowany przez oba. Próba
    po drugim scaleniu biegnie pod regułą projektu (prawdziwe `make`) i pada; karta stoi. Akceptacja z pytania
    w Snapshot() przez /Board ResolveCardStep zapisuje `merge.check-accepted` i zablokowany ticket startuje."""
    MAKEFILE = "test-changed:\n\t@echo \"testy zmian od $(BASE)\"\n\t@echo 'test PT-1 pada'; exit 3\n"

    def setUp(self):
        super().setUp()
        path = os.path.join(self.base, "config", "tide", "cardflow.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["outsideCommands"] = {"proj": ["make test-changed *"]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with open(os.path.join(self.repo, "Makefile"), "w", encoding="utf-8") as fh:
            fh.write(self.MAKEFILE)
        git(self.repo, "add", "Makefile", env=self.git_env)
        git(self.repo, "commit", "-qm", "makefile", env=self.git_env)

    def plan(self, card):
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-11-10-card.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan karty\ncard: " + card +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Plasterek 1 | todo | | Sonnet | | standard | Opus | |\n"
                     "| #2 | Plasterek 2 | todo | | Sonnet | | standard | Opus | |\n"
                     "| #3 | Zablokowany | todo | | Sonnet | | standard | Opus | #1, #2 |\n")
        for number in (1, 2, 3):
            with open(os.path.join(folder, "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: powoli 2\nATRAPA-PLIK: {number}\n\n- [ ] Wynik.\n")
        return path

    def problems(self, card):
        flow = (self.shown(card).get("derived") or {}).get("flow") or {}
        return [d for d in flow.get("decisions") or [] if d["kind"] == "merge-check"]

    def test_accept_merge_check_through_board_bus_opens_the_gate_and_the_blocked_ticket_starts(self):
        card = self.card(description="Plan z pliku")
        proposed = self.call("StartCard", card, json.dumps({"entry": "plan", "planPath": self.plan(card)}))
        self.assertTrue(proposed["ok"], proposed)
        self.wait_column(card, "waiting", timeout=40)
        approved = self.call("ApproveCardPlan", card)
        self.assertTrue(approved["ok"], approved)
        self.call("ApproveCardEnvironment", card, "tide-box: 1\n", False)
        blocked = approved["map"]["#3"]
        self.assertTrue(wait_until(lambda: [f for f in self.of(card, mergecheck.FINISHED)], timeout=120),
                        self.events(card))
        result = self.of(card, mergecheck.FINISHED)[0]
        # Próba zapisuje kod wyjścia całego polecenia: `make` po błędzie przepisu (`exit 3`) kończy się kodem 2.
        self.assertEqual(result["exit"], 2)
        with open(result["log"], encoding="utf-8") as fh:
            self.assertIn("test PT-1 pada", fh.read())
        check = next(c for c in self.of(card, mergecheck.REQUESTED) if c["check"] == result["check"])
        self.assertTrue(wait_until(lambda: self.problems(card), timeout=20), self.shown(card).get("derived"))
        decision = self.problems(card)[0]
        self.assertEqual(decision["id"], f"merge-check-failed:{check['check']}")
        option = next(o for o in decision["options"] if o["params"]["choice"] == "accept-merge-check")
        self.assertEqual(option["params"], {"choice": "accept-merge-check", "ticket": check["ticket"],
                                            "check": check["check"], "after": check["after"]})
        self.assertFalse([s for s in self.of(card, "step.started") if s.get("ticket") == blocked],
                         "bramka zamknięta porażką")
        # Nieaktualny identyfikator — błąd bez zapisu.
        stale = self.call("ResolveCardStep", card, json.dumps({**option["params"], "after": "d" * 40}))
        self.assertFalse(stale.get("ok"), stale)
        self.assertFalse(self.of(card, mergecheck.ACCEPTED))
        reply = self.call("ResolveCardStep", card, json.dumps(option["params"]))
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual([(e["ticket"], e["check"], e["after"], e["by"]) for e in self.of(card, mergecheck.ACCEPTED)],
                         [(check["ticket"], check["check"], check["after"], "user")])
        self.assertTrue(wait_until(lambda: [s for s in self.of(card, "step.started") if s.get("ticket") == blocked],
                                   timeout=60), self.events(card))
        self.assertEqual(len([e for e in self.of(card, "outside.started")
                              if e.get("step") == mergecheck.step_name(check["check"])]), 1,
                         "akceptacja nie uruchamia testów ponownie")


if __name__ == "__main__":
    unittest.main()
