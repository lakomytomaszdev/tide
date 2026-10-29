"""PT-78: propozycja planu, zatwierdzenie i wykonanie według blokerów."""
import json
import os
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class PlanFlowTest(FlowInProcess):
    def proposal(self, card_id, invalid=False):
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-10-29-plan.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan karty\ncard: " + card_id +
                     "\ntracker: local\nticket-prefix: PT\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Pierwszy plasterek | todo | | Sonnet | konieczny | standard | Opus | |\n"
                     "| #2 | Drugi plasterek | todo | | Sonnet | po pierwszym | standard | Opus | #1 |\n")
        for number in (1, 2):
            with open(os.path.join(folder, "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: gotowe\n\n## Kryteria akceptacji\n\n- [ ] Wynik {number}.\n"
                         if not invalid or number == 1 else "# Brak kryteriów\n")
        return path

    def submit(self, card_id, path):
        box = []
        self.flow.start_card(card_id, json.dumps({"entry": "plan", "planPath": path}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        return json.loads(box[0])

    def approve(self, card_id):
        box = []
        self.flow.approve_plan(card_id, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        return json.loads(box[0])

    def test_invalid_proposal_records_errors_without_reserving(self):
        card = self.card()
        reply = self.submit(card, self.proposal(card, invalid=True))
        self.assertEqual((reply["ok"], reply["error"]), (False, "invalid-plan"))
        self.assertTrue(any("kryteriów" in e for e in reply["errors"]))
        self.assertEqual(self.results(card, "reserve"), [])
        self.assertEqual(self.of(card, "plan.checked")[-1]["valid"], False)
        self.assertEqual(self.shown(card)["boardColumn"], "waiting")

    def test_approval_assigns_ids_then_runs_blocked_ticket_after_merge(self):
        card = self.card(description="Plan z pliku")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertEqual(self.results(card, "reserve"), [], "propozycja nie bierze numerów")
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["plan"]["waves"], [["#1"], ["#2"]])
        reply = self.approve(card)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(set(reply["map"]), {"#1", "#2"})
        self.assertEqual(len(set(reply["map"].values())), 2)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=45), self.events(card))
        steps = [s for s in self.of(card, "step.started") if s.get("kind") == "ticket"]
        self.assertEqual([s["ticket"] for s in steps], [reply["map"]["#1"], reply["map"]["#2"]])
        merge = self.results(card, "merge")
        self.assertEqual([r["key"] for r in merge], [reply["map"]["#1"], reply["map"]["#2"]])
        second_start = next(e["seq"] for e in self.events(card) if e["type"] == "step.started" and
                            e.get("ticket") == reply["map"]["#2"])
        first_merge = next(e["seq"] for e in self.events(card) if e["type"] == "result" and
                           e.get("action") == "merge" and e.get("key") == reply["map"]["#1"])
        self.assertGreater(second_start, first_merge)
        self.assertEqual(self.shown(card)["boardColumn"], "review")
        from tide_daemon.coordination.repoplans import RepoPlans
        plans, _ = RepoPlans(ttl=0).read([self.repo])
        approved_plan = next(p for p in plans if p.card == card and
                             os.path.basename(p.path).endswith(f"{card.lower()}-flow.md"))
        self.assertEqual([r.ticket for r in approved_plan.rows], list(reply["map"].values()))
        self.assertEqual([r.state for r in approved_plan.rows], ["gotowe", "gotowe"])
        self.assertEqual(approved_plan.updated, merge[-1]["ts"])
        self.assertGreaterEqual(len(self.results(card, "plan-commit")), 3)
        self.assertTrue(all(os.path.isfile(os.path.join(os.path.dirname(approved_plan.path).replace("plans", "tickets"),
                                                        f"{card.lower()}-flow", r.ticket.lower() + ".md"))
                            for r in approved_plan.rows))

    def test_restart_between_reservation_and_approval_reuses_ids(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        journal = self.flow.engine.journal
        original = journal.append
        interrupted = False

        def stop_before_map(ident, kind, **data):
            nonlocal interrupted
            if kind == "plan.approved" and not interrupted:
                interrupted = True
                raise OSError("przerwanie przed mapą")
            return original(ident, kind, **data)

        with mock.patch.object(journal, "append", side_effect=stop_before_map):
            with self.assertRaises(OSError):
                self.flow.engine.approve_plan(card)
        reserved = [r["ticket"] for r in self.results(card, "reserve")]
        self.assertEqual(len(reserved), 2)
        self.restart()
        reply = self.approve(card)
        self.assertEqual(list(reply["map"].values()), reserved)
        self.assertEqual(len(self.results(card, "reserve")), 2)
        self.assertEqual(self.approve(card)["map"], reply["map"])

    def test_restart_after_plan_commit_before_journal_result_keeps_numbering_and_plan(self):
        from tide_daemon.cardflow.state import fold
        from tide_daemon.coordination.repoplans import RepoPlans
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        engine = self.flow.engine
        mapping = engine.approve_plan(card)["map"]
        journal = engine.journal
        original = journal.append
        interrupted = False

        def stop_after_commit(ident, kind, **data):
            nonlocal interrupted
            if kind == "result" and data.get("action") == "plan-commit" and not interrupted:
                interrupted = True
                raise OSError("przerwanie po commicie planu")
            return original(ident, kind, **data)

        with mock.patch.object(journal, "append", side_effect=stop_after_commit):
            with self.assertRaises(OSError):
                engine._plan_next(card, fold(journal.read(card)))
        self.restart()
        self.assertTrue(wait_until(lambda: self.results(card, "plan-commit"), timeout=15))
        self.assertEqual(len(self.results(card, "reserve")), 2)
        plans, _ = RepoPlans(ttl=0).read([self.repo])
        approved_plan = next(p for p in plans if p.card == card and
                             os.path.basename(p.path).endswith(f"{card.lower()}-flow.md"))
        self.assertEqual([r.ticket for r in approved_plan.rows], list(mapping.values()))


if __name__ == "__main__":
    import unittest
    unittest.main()
