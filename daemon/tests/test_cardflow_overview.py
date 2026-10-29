"""PT-73: alarm postoju, powiadomienia i zrobione tickety w raporcie."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import derived, fold
from tide_daemon.reports.plans import _attach_flow


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.board = os.path.join(self.temp.name, "board")
        os.mkdir(self.board)
        self.now = [1_800_000_000.0]
        self.journal = Journal(self.board, mirror=os.path.join(self.temp.name, "mirror"),
                               clock=lambda: self.now[0])
        self.store = SimpleNamespace(root=self.board,
                                     load=lambda: SimpleNamespace(cards={1: SimpleNamespace(column="todo")}))
        self.engine = FlowEngine(self.store, self.journal, clock=lambda: self.now[0])
        self.journal.append("K-1", "card.started", entry="plan", repo=self.temp.name, column="todo")

    def test_plan_approval_notifies_once(self):
        self.journal.append("K-1", "plan.proposed", rows=[], title="Plan")
        self.journal.append("K-1", "plan.reviewed", verdict="APPROVE")
        self.engine._queue_notices("K-1")
        self.engine._queue_notices("K-1")
        self.assertEqual([n["result"] for n in self.engine.take_notices()], ["plan"])

    def test_card_stalls_once_and_new_step_clears_marker(self):
        self.now[0] += 181
        self.engine._guard_card("K-1")
        self.engine._guard_card("K-1")
        self.assertEqual([n["result"] for n in self.engine.take_notices()], ["stalled"])
        self.assertTrue(derived(fold(self.journal.read("K-1")), {"column": "todo"})["flow"]["stalledSince"])
        self.journal.append("K-1", "step.started", step="s1", ticket="PT-1", kind="ticket", executor="Sonnet")
        shown = derived(fold(self.journal.read("K-1")), {"column": "todo"})
        self.assertEqual(shown["flow"]["stalledSince"], "")
        self.assertEqual(shown["flow"]["steps"][0]["startedTs"], self.now[0])

    def test_named_limit_does_not_stall(self):
        self.journal.append("K-1", "step.waiting", ticket="PT-1", kind="ticket", reason="limit dostawcy")
        self.now[0] += 181
        self.engine._guard_card("K-1")
        self.assertFalse([e for e in self.journal.read("K-1") if e["type"] == "card.stalled"])

    def test_completed_ticket_without_next_step_is_reported_as_stalled(self):
        self.journal.append("K-1", "plan.approved", rows=[{"id": "PT-1", "title": "Zrobione", "blockers": []}])
        self.journal.append("K-1", "result", action="merge", key="PT-1", ok=True)
        self.now[0] += 181
        self.engine._guard_card("K-1")
        self.assertEqual(self.engine.take_notices()[0]["result"], "stalled")

    def test_report_reads_completed_ticket_with_executor_from_journal(self):
        old_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.temp.name, "state")
        self.addCleanup(lambda: os.environ.pop("XDG_STATE_HOME", None) if old_state is None
                        else os.environ.__setitem__("XDG_STATE_HOME", old_state))
        journal = Journal(self.board, clock=lambda: self.now[0])
        journal.append("K-2", "card.started", entry="plan", repo=self.temp.name)
        journal.append("K-2", "plan.approved", rows=[{"id": "PT-2", "title": "Eksport"}])
        journal.append("K-2", "step.started", step="s2", ticket="PT-2", kind="ticket", executor="Astra")
        journal.append("K-2", "result", action="merge", key="PT-2", ok=True)
        project = SimpleNamespace(path=self.temp.name, is_repo=True, plans=[])
        _attach_flow([project], self.now[0] - 60, self.now[0] + 60, self.board)
        self.assertEqual(project.plans[0]["tickets"][0]["note"], "wykonał Astra")
