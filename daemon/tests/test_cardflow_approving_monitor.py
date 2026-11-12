"""PT-407 (K-102): drugie zatwierdzenie planu w trakcie dostaje od razu „zatwierdzanie trwa”.

Prawdziwy `CardFlowMonitor` z prawdziwym workerem kolejki (`tide-cardflow`) i pętlą zdarzeń Qt; silnik to atrapa,
której `approve_plan` czeka na `threading.Event`. Test nie mierzy czasu — sprawdza kolejność: odpowiedź na drugie
wywołanie przychodzi, zanim test zwolni atrapę (lista zdarzeń `order`), a atrapa jest wywołana dokładnie raz.
Ten sam kod obsługuje D-Bus (`BoardMonitor.approve_card_plan` / `approve_card_environment`); wariant na prywatnej
magistrali: test_cardflow_approving_dbus.py.
"""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.board_monitor import BoardMonitor
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow_monitor import CardFlowMonitor

from .privatebus import app
from .test_cardflow_busy_reads import pump

CARD = "K-7"
TRUE_ANSWER = {"ok": True, "id": CARD, "approving": True, "message": "Zatwierdzanie trwa…"}


def pending_plan():
    return SimpleNamespace(plan_proposed={"seq": 2}, plan_approved=None, plan_revision=None)


def pending_revision():
    return SimpleNamespace(plan_proposed={"seq": 2}, plan_approved={"seq": 5}, plan_revision={"pending": True})


def approved_plan():
    return SimpleNamespace(plan_proposed={"seq": 2}, plan_approved={"seq": 5}, plan_revision=None)


class ApprovingCase(unittest.TestCase):
    state = staticmethod(pending_plan)

    def setUp(self):
        app()
        self.entered, self.release = threading.Event(), threading.Event()
        self.order, self.calls = [], []
        self.engine = mock.MagicMock()
        self.engine.journal.lock = threading.RLock()
        self.engine.journal.has_dirty.return_value = False
        self.engine.journal.take_dirty.return_value = []
        self.engine.take_notices.return_value = []
        self.engine.journal.state.side_effect = lambda card: self.state()
        self.engine.approve_plan.side_effect = self.hold
        self.engine.approve_environment.side_effect = self.hold_environment
        self.monitor = CardFlowMonitor(self.engine, None, poll_ms=3_600_000)
        self.addCleanup(self.monitor.stop)
        self.addCleanup(self.release.set)

    def hold(self, card):
        self.calls.append(("plan", card))
        self.entered.set()
        self.assertTrue(self.release.wait(30), "atrapa nie została zwolniona")
        return {"ok": True, "id": card, "map": {}}

    def hold_environment(self, card, text, only_plan):
        self.calls.append(("environment", card))
        self.entered.set()
        self.assertTrue(self.release.wait(30), "atrapa nie została zwolniona")
        return {"ok": True, "id": card}

    def recorder(self, name, sink):
        def reply(text):
            self.order.append(name)
            sink.append(json.loads(text))
        return reply

    def start_first(self, how="plan"):
        first = []
        if how == "plan":
            self.monitor.approve_plan(CARD, self.recorder("first", first))
        else:
            self.monitor.approve_environment(CARD, "tide-box: 1\n", False, self.recorder("first", first))
        self.assertTrue(pump(self.entered.is_set), "atrapa zatwierdzania nie wystartowała w kolejce")
        return first

    def finish(self, first):
        self.order.append("release")
        self.release.set()
        self.assertTrue(pump(lambda: first), "pierwsze zatwierdzenie bez odpowiedzi po zwolnieniu atrapy")


class DuplicateApprovalTest(ApprovingCase):
    def test_second_approve_plan_answers_before_the_dummy_is_released(self):
        first, second = self.start_first(), []
        self.monitor.approve_plan(CARD, self.recorder("second", second))
        self.assertEqual(second, [TRUE_ANSWER])
        self.assertEqual(first, [], "pierwsze wywołanie czeka na atrapę")
        self.finish(first)
        self.assertEqual(self.order, ["second", "release", "first"])
        self.assertEqual(self.calls, [("plan", CARD)], "atrapa wywołana dokładnie raz")
        self.assertTrue(first[0]["ok"])

    def test_approve_environment_during_plan_approval_answers_at_once(self):
        first, second = self.start_first("plan"), []
        self.monitor.approve_environment(CARD, "tide-box: 1\n", False, self.recorder("second", second))
        self.assertEqual(second, [TRUE_ANSWER])
        self.finish(first)
        self.assertEqual(self.order, ["second", "release", "first"])
        self.assertEqual(self.calls, [("plan", CARD)])
        self.engine.approve_environment.assert_not_called()

    def test_approve_plan_during_environment_approval_answers_at_once(self):
        first, second = self.start_first("environment"), []
        self.monitor.approve_plan(CARD, self.recorder("second", second))
        self.assertEqual(second, [TRUE_ANSWER])
        self.finish(first)
        self.assertEqual(self.order, ["second", "release", "first"])
        self.assertEqual(self.calls, [("environment", CARD)])
        self.engine.approve_plan.assert_not_called()

    def test_other_cards_are_not_blocked(self):
        first, other = self.start_first(), []
        self.monitor.approve_plan("K-8", self.recorder("other", other))
        self.assertEqual(other, [], "inna karta czeka w kolejce, ale nie dostaje „trwa”")
        self.finish(first)
        self.assertTrue(pump(lambda: other))
        self.assertEqual(other[0].get("approving"), None)
        self.assertEqual(self.calls, [("plan", CARD), ("plan", "K-8")])


class DifferenceApprovalTest(ApprovingCase):
    """Zatwierdzenie różnicy planu to ta sama metoda — chroni je wyłącznie zbiór w monitorze."""
    state = staticmethod(pending_revision)

    def test_second_approve_of_the_plan_difference_answers_before_the_dummy_is_released(self):
        first, second = self.start_first(), []
        self.monitor.approve_plan(CARD, self.recorder("second", second))
        self.assertEqual(second, [TRUE_ANSWER])
        self.finish(first)
        self.assertEqual(self.order, ["second", "release", "first"])
        self.assertEqual(self.calls, [("plan", CARD)])

    def test_environment_approval_that_would_approve_the_difference_is_guarded(self):
        first, second = self.start_first("environment"), []
        self.monitor.approve_environment(CARD, "tide-box: 1\n", True, self.recorder("second", second))
        self.assertEqual(second, [TRUE_ANSWER])
        self.finish(first)
        self.assertEqual(self.calls, [("environment", CARD)])


class EnvironmentWithoutPlanTest(ApprovingCase):
    """ApproveCardEnvironment, które nie zatwierdza planu, zachowuje dotychczasową kolejkę."""
    state = staticmethod(approved_plan)

    def test_environment_calls_for_an_approved_plan_are_both_queued(self):
        first, second = self.start_first("environment"), []
        self.monitor.approve_environment(CARD, "tide-box: 1\n", False, self.recorder("second", second))
        self.assertEqual(second, [], "bez zatwierdzania planu nie ma odpowiedzi „trwa”")
        self.finish(first)
        self.assertTrue(pump(lambda: second))
        self.assertEqual(self.calls, [("environment", CARD), ("environment", CARD)])
        self.assertNotIn("approving", second[0])

    def test_plan_approval_is_not_blocked_by_an_environment_call_for_an_approved_plan(self):
        first, second = self.start_first("environment"), []
        self.monitor.approve_plan(CARD, self.recorder("second", second))
        self.assertEqual(second, [])
        self.finish(first)
        self.assertTrue(pump(lambda: second))


class ReleaseTest(ApprovingCase):
    """Po doręczeniu odpowiedzi karta znika ze zbioru: kolejne ApproveCardPlan wchodzi do kolejki."""

    def run_again(self):
        again = []
        self.release.set()   # kolejne wywołanie nie czeka na atrapę
        self.monitor.approve_plan(CARD, self.recorder("again", again))
        self.assertTrue(pump(lambda: again), "kolejne zatwierdzenie nie zostało przyjęte do kolejki")
        self.assertNotIn("approving", again[0])
        return again[0]

    def test_released_after_success(self):
        first = self.start_first()
        self.finish(first)
        self.assertEqual(self.monitor._approving, set())
        self.assertTrue(self.run_again()["ok"])
        self.assertEqual(len(self.calls), 2)

    def test_released_after_flow_error(self):
        self.engine.approve_plan.side_effect = FlowError("review-pending", "plan czeka na review")
        first = []
        self.monitor.approve_plan(CARD, self.recorder("first", first))
        self.assertTrue(pump(lambda: first))
        self.assertEqual((first[0]["ok"], first[0]["error"]), (False, "review-pending"))
        self.assertEqual(self.monitor._approving, set())
        self.engine.approve_plan.side_effect = None
        self.engine.approve_plan.return_value = {"ok": True, "id": CARD, "already": True}
        again = []
        self.monitor.approve_plan(CARD, self.recorder("again", again))
        self.assertTrue(pump(lambda: again))
        self.assertEqual(again[0]["already"], True)

    def test_released_after_unexpected_exception(self):
        self.engine.approve_plan.side_effect = RuntimeError("boom")
        first = []
        with mock.patch("builtins.print"):
            self.monitor.approve_plan(CARD, self.recorder("first", first))
            self.assertTrue(pump(lambda: first))
        self.assertEqual(first[0]["error"], "internal")
        self.assertEqual(self.monitor._approving, set())
        self.engine.approve_plan.side_effect = None
        self.engine.approve_plan.return_value = {"ok": True, "id": CARD}
        again = []
        self.monitor.approve_plan(CARD, self.recorder("again", again))
        self.assertTrue(pump(lambda: again))
        self.assertTrue(again[0]["ok"])

    def test_released_after_refusal_when_the_service_is_closing(self):
        self.monitor.stop()
        first = []
        self.monitor.approve_plan(CARD, self.recorder("first", first))
        self.assertEqual(first[0]["error"], "unavailable")
        self.assertEqual(self.monitor._approving, set())
        env = []
        self.monitor.approve_environment(CARD, "tide-box: 1\n", False, self.recorder("env", env))
        self.assertEqual(env[0]["error"], "unavailable")
        self.assertEqual(self.monitor._approving, set())
        self.engine.approve_plan.assert_not_called()

    def test_stop_with_approval_in_flight_clears_the_set(self):
        self.start_first()
        self.assertEqual(self.monitor._approving, {CARD})
        self.release.set()
        self.monitor.stop()
        self.assertEqual(self.monitor._approving, set())


class QueueOrderTest(ApprovingCase):
    def test_other_methods_keep_their_place_in_the_queue(self):
        order = []
        self.engine.edit_plan.side_effect = lambda card, data: order.append("edit") or {"ok": True}
        self.engine.moved.side_effect = lambda *a: order.append("move")
        first, edit = self.start_first(), []
        self.monitor.edit_plan(CARD, json.dumps({"action": "return"}), self.recorder("edit", edit))
        self.monitor.moved(CARD, "done")
        self.assertEqual(edit, [], "EditCardPlan czeka w kolejce za zatwierdzaniem")
        self.finish(first)
        self.assertTrue(pump(lambda: edit and len(order) == 2))
        self.assertEqual(order, ["edit", "move"])
        self.assertEqual(self.order, ["release", "first", "edit"])


class BoardMonitorEntryTest(ApprovingCase):
    """Wejście D-Bus (`BoardMonitor.approve_card_plan` / `approve_card_environment`) używa tego samego zbioru."""

    def test_board_monitor_methods_share_the_guard(self):
        board = BoardMonitor.__new__(BoardMonitor)
        board.flow = self.monitor
        first, second, third = [], [], []
        board.approve_card_plan(CARD, self.recorder("first", first))
        self.assertTrue(pump(self.entered.is_set))
        board.approve_card_plan(CARD, self.recorder("second", second))
        board.approve_card_environment(CARD, "tide-box: 1\n", False, self.recorder("third", third))
        self.assertEqual((second, third), ([TRUE_ANSWER], [TRUE_ANSWER]))
        self.finish(first)
        self.assertEqual(self.calls, [("plan", CARD)])


if __name__ == "__main__":
    unittest.main()
