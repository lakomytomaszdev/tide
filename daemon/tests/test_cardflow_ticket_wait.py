"""Czekanie kroku na inny ticket karty (06.11, K-38/PT-191 czekał na PT-171).

Odpowiedź „czekaj” wznawiała krok od razu, krok kończył się błędem i wracał do użytkownika. Teraz krok sam zgłasza
`waitFor` albo użytkownik wybiera „Czekaj na …”, a Tide wznawia go po scaleniu wskazanego ticketu do gałęzi karty."""
import json
import os
from unittest import mock

from .privatebus import wait_until
from . import test_cardflow_plan_integration as plan_tests
from .test_cardflow import FlowInProcess


class TicketWaitTest(FlowInProcess):
    proposal = plan_tests.PlanFlowTest.proposal
    submit = plan_tests.PlanFlowTest.submit
    approve = plan_tests.PlanFlowTest.approve

    def two_tickets(self, card, waiting_mode):
        """#1 i #2 bez blokerów; #2 potrzebuje pracy #1 (numer z planu w kartce — `waitFor` przyjmuje też #1)."""
        path = self.proposal(card)
        with open(path, encoding="utf-8") as fh:
            source = fh.read().split("| #1 |")[0]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source + "| #1 | Moduł portów | todo | | Sonnet | | standard | Opus | |\n"
                              "| #2 | Dowód granicy | todo | | Sonnet | | standard | Opus | |\n")
        tickets = os.path.join(os.path.dirname(path), "tickets")
        with open(os.path.join(tickets, "1.md"), "w", encoding="utf-8") as fh:
            fh.write("# Moduł portów\n\nATRAPA: powoli 3\nATRAPA-PLIK: 1\n\n- [ ] Wynik.\n")
        with open(os.path.join(tickets, "2.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# Dowód granicy\n\nATRAPA: {waiting_mode}\nATRAPA-CZEKA: #1\nATRAPA-PLIK: 2\n\n- [ ] Wynik.\n")
        return path

    def steps_of(self, card, ticket, kind="ticket"):
        return [s for s in self.of(card, "step.started") if s.get("ticket") == ticket and s.get("kind") == kind]

    def merged(self, card, ticket):
        return any(e["key"] == ticket and e.get("ok") for e in self.results(card, "merge"))

    def test_step_declares_wait_and_resumes_by_itself_after_the_other_ticket_merges(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.two_tickets(card, "czeka-na"))["ok"])
        # Scalenie blokera czeka na sprawdzenie trwałego zaparkowania; bez wyścigu z czasem wykonania atrapy.
        gate = mock.patch.object(self.flow.engine, "_merge")
        gate.start()
        self.addCleanup(gate.stop)
        mapping = self.approve(card)["map"]
        first, second = mapping["#1"], mapping["#2"]
        self.assertTrue(wait_until(lambda: any(d.get("waitTicket") for d in self.of(card, "step.decision")),
                                   timeout=20), self.events(card))
        decision = next(d for d in self.of(card, "step.decision") if d.get("waitTicket"))
        self.assertEqual((decision["ticket"], decision["waitTicket"], decision["choice"], decision.get("auto")),
                         (second, first, "wait-ticket", True))
        shown = self.shown(card)
        self.assertEqual([d for d in shown["derived"]["flow"]["decisions"] if d.get("ticket") == second], [],
                         "krok czekający na ticket nie pyta użytkownika")
        row = next(t for t in shown["derived"]["tickets"] if t["id"] == second)
        if not self.merged(card, first):
            self.assertEqual(row.get("phase"), "kolejka", row)
            self.assertIn(f"czeka na {first}", row.get("why", ""))
        self.restart()
        gate.stop()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=60), self.events(card))
        resumed = self.steps_of(card, second)
        self.assertEqual(len(resumed), 2, "jedno wznowienie — bez automatycznego ponawiania przed scaleniem")
        merge = next(e for e in self.results(card, "merge") if e["key"] == first and e.get("ok"))
        self.assertLess(merge["seq"], resumed[1]["seq"], "wznowienie dopiero po scaleniu blokera")
        with open(os.path.join(resumed[1]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn(f"Ticket {first}, na który czekał ten krok, jest scalony do gałęzi karty", prompt)
        self.assertIn("git merge --no-edit", prompt)
        with open(os.path.join(resumed[1]["stepDir"], "out", "dependency-read.txt"), encoding="utf-8") as fh:
            self.assertIn("krok 11", fh.read(), "wznowiony autor odczytał wynik blokera ze swojego worktree")
        self.assertFalse(self.of(card, "step.retry") and any(
            e["ticket"] == second for e in self.of(card, "step.retry")), "bez automatycznego ponowienia")

    def test_user_chooses_wait_in_error_window_and_step_resumes_after_merge(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.two_tickets(card, "czeka-na-blad"))["ok"])
        gate = mock.patch.object(self.flow.engine, "_merge")
        gate.start()
        self.addCleanup(gate.stop)
        mapping = self.approve(card)["map"]
        first, second = mapping["#1"], mapping["#2"]

        def decision():
            found = [d for d in self.shown(card)["derived"]["flow"]["decisions"]
                     if d.get("ticket") == second and d.get("kind") == "error"]
            return found[0] if found else None
        self.assertTrue(wait_until(decision, timeout=30), self.events(card))
        self.assertFalse(self.merged(card, first))
        step = decision()["step"]
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "wait-ticket", "step": step, "waitTicket": "#1"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        gate.stop()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=60), self.events(card))
        resumed = [s for s in self.steps_of(card, second) if s.get("parentStep") == step]
        self.assertEqual(len(resumed), 1)
        merge = next(e for e in self.results(card, "merge") if e["key"] == first and e.get("ok"))
        self.assertLess(merge["seq"], resumed[0]["seq"])

    def test_wait_target_must_be_another_live_ticket_of_the_plan(self):
        from tide_daemon.cardflow.engine import FlowEngine, FlowError
        from tide_daemon.cardflow.state import FlowState
        st = FlowState()
        st.plan_approved = {"rows": [{"id": "PT-1", "workId": "#1", "blockers": []},
                                     {"id": "PT-2", "workId": "#2", "blockers": ["PT-1"]},
                                     {"id": "PT-3", "workId": "#3", "blockers": []}]}
        self.assertEqual(FlowEngine._wait_ticket_target(st, {"ticket": "PT-2"}, "pt-1"), "PT-1")
        self.assertEqual(FlowEngine._wait_ticket_target(st, {"ticket": "PT-3"}, "#2"), "PT-2")
        for target in ("", "PT-9", "PT-1", "#1"):
            with self.assertRaises(FlowError, msg=target):
                FlowEngine._wait_ticket_target(st, {"ticket": "PT-1"}, target)
        with self.assertRaises(FlowError, msg="PT-2 ma bloker PT-1 — PT-1 nie może czekać na PT-2"):
            FlowEngine._wait_ticket_target(st, {"ticket": "PT-1"}, "PT-2")
        st.rejected.add("PT-3")
        with self.assertRaises(FlowError):
            FlowEngine._wait_ticket_target(st, {"ticket": "PT-1"}, "PT-3")

    def test_error_window_recommends_waiting_for_a_named_unmerged_ticket(self):
        from tide_daemon.cardflow import decisions
        from tide_daemon.cardflow.state import FlowState
        st = FlowState()
        st.plan_approved = {"rows": [{"id": "PT-171", "workId": "#1", "blockers": []},
                                     {"id": "PT-191", "workId": "#2", "blockers": []}]}
        step = {"step": "K-1-PT-191-3", "ticket": "PT-191", "kind": "ticket", "executor": "Codex mocny",
                "attempt": 1, "parentStep": "K-1-PT-191-2"}
        reason = ("Automatyczne ponowienie: zatwierdzony bloker PT-171 nadal niespełniony. Nie ponawiać przed "
                  "integracją PT-171.")
        done = {"result": "error", "reason": reason, "fault": "unknown", "faultCause": ""}
        st.steps[step["step"]] = step
        st.finished[step["step"]] = done
        built = decisions._error(st, step["step"], step, done)
        first = built["options"][0]
        self.assertEqual((first["id"], first["recommended"], first["params"]),
                         ("wait-ticket-PT-171", True,
                          {"choice": "wait-ticket", "step": step["step"], "waitTicket": "PT-171"}))
        self.assertEqual(sum(o["recommended"] for o in built["options"]), 1)
        st.results[("merge", "PT-171")] = {"ok": True}
        self.assertFalse(any(o["id"].startswith("wait-ticket") for o in decisions._error(st, step["step"], step, done)["options"]),
                         "scalonego ticketu nie ma na co czekać")
