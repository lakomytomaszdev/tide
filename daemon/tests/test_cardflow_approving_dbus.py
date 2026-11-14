"""PT-407 (K-102): drugie zatwierdzenie planu w trakcie, przez D-Bus na prywatnej magistrali.

Prawdziwa usługa (proces z atrapami programów) z tymczasową Tablicą. Kolejkę mutacji trzyma atrapa gita
(`GIT_GATE` z K-98 #2): polecenie `git worktree list` — pierwsze, o które pyta rezerwacja numerów w trakcie
zatwierdzania — czeka, aż test usunie plik bramki. Pierwsze wywołanie jest asynchroniczne; drugie wraca
z `approving: true`, zanim test zwolni bramkę (asercja kolejności: pierwsze nie jest jeszcze zakończone),
bez progów czasu. Atrapa „dokładnie raz” to jedno `plan.approved` i dwie rezerwacje numerów po zwolnieniu.
Pełne warianty zachowania kolejki bez magistrali: test_cardflow_approving_monitor.py.
"""
import contextlib
import json
import os

from .privatebus import requires_dbus, wait_until
from . import test_cardflow_approval_start_dbus as start_tests   # moduł, nie klasa: bez dubla testów bazowych
from .test_cardflow_busy_reads import GIT_GATE
from .test_cardflow_dbus import FlowCase

try:
    from PyQt6.QtDBus import QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply
except ImportError:  # pragma: no cover
    QDBusMessage = None

APPROVING = {"ok": True, "approving": True, "message": "Zatwierdzanie trwa…"}


@requires_dbus
class ApprovingBusTest(FlowCase):
    write_plan = start_tests.ApprovalStartBusTest.write_plan
    propose = start_tests.ApprovalStartBusTest.propose

    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: start_tests.kill_fakes(self.base))
        import shutil
        fakebin = os.path.join(self.base, "fakebin")
        self.gate = os.path.join(self.base, "git-gate")
        with open(os.path.join(fakebin, "git"), "w", encoding="utf-8") as fh:
            fh.write(GIT_GATE.format(gate=self.gate, git=shutil.which("git", path="/usr/bin:/bin")))
        os.chmod(os.path.join(fakebin, "git"), 0o755)
        self.addCleanup(self.open_gate)

    # --- pomocnicze -------------------------------------------------------------------------------

    def close_gate(self):
        with open(self.gate, "w", encoding="utf-8") as fh:
            fh.write("worktree list")

    def open_gate(self):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.gate)

    def async_call(self, member, *args):
        msg = QDBusMessage.createMethodCall("org.tide.Daemon", "/Board", "org.tide.Daemon.Board", member)
        msg.setArguments(list(args))
        return QDBusPendingCallWatcher(self.service.bus.asyncCall(msg, 120000))

    def finished(self, pending):
        self.assertTrue(wait_until(pending.isFinished, timeout=120), "pierwsze zatwierdzenie bez odpowiedzi")
        return json.loads(QDBusPendingReply(pending).argumentAt(0))

    def hold_first(self, card, member="ApproveCardPlan", *args):
        """Pierwsze zatwierdzenie utknęło na bramce gita w kolejce usługi; zwraca oczekujące wywołanie."""
        self.close_gate()
        pending = self.async_call(member, card, *args)
        self.assertTrue(wait_until(lambda: os.path.exists(self.gate + ".entered"), timeout=60),
                        "zatwierdzanie nie doszło do rezerwacji numerów (bramka gita)")
        self.assertFalse(pending.isFinished(), "pierwsze zatwierdzenie skończyło się przed drugim wywołaniem")
        return pending

    def assert_second_is_answered_first(self, pending, member, *args):
        second = self.call(member, *args)
        self.assertEqual({k: second[k] for k in APPROVING}, APPROVING, second)
        self.assertFalse(pending.isFinished(), "drugie wywołanie wróciło dopiero po pierwszym")
        return second

    def release(self, pending):
        self.open_gate()
        return self.finished(pending)

    def numbers(self, card):
        return [r["ticket"] for r in self.results(card, "reserve")]

    # --- warianty ----------------------------------------------------------------------------------

    def test_second_approve_card_plan_returns_approving_before_the_first_finishes(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        pending = self.hold_first(card)
        second = self.assert_second_is_answered_first(pending, "ApproveCardPlan", card)
        self.assertEqual(second["id"], card)
        first = self.release(pending)
        self.assertTrue(first["ok"], first)
        self.assertNotIn("approving", first)
        self.assertEqual(len(self.of(card, "plan.approved")), 1, "zatwierdzenie planu wykonało się raz")
        self.assertEqual(len(self.numbers(card)), 2)
        again = self.call("ApproveCardPlan", card)
        self.assertEqual((again["ok"], again.get("already")), (True, True), again)

    def test_snapshot_after_the_first_finishes_is_not_approving(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        pending = self.hold_first(card)
        self.assert_second_is_answered_first(pending, "ApproveCardPlan", card)
        self.assertTrue(self.release(pending)["ok"])
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["plan"]["status"] in ("approved", "done"),
                                   timeout=30))
        plan = self.shown(card)["derived"]["flow"]["plan"]
        self.assertFalse(plan["approving"], plan)
        self.assertEqual(plan["approvalError"], "")

    def test_environment_approval_during_plan_approval_returns_approving(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        pending = self.hold_first(card)
        self.assert_second_is_answered_first(pending, "ApproveCardEnvironment", card, "tide-box: 1\n", False)
        self.assertTrue(self.release(pending)["ok"])
        self.assertEqual(len(self.of(card, "plan.approved")), 1)
        self.assertEqual(len(self.numbers(card)), 2)

    def test_plan_approval_during_environment_approval_returns_approving(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        pending = self.hold_first(card, "ApproveCardEnvironment", "tide-box: 1\n", False)
        self.assert_second_is_answered_first(pending, "ApproveCardPlan", card)
        self.assertTrue(self.release(pending)["ok"])
        self.assertEqual(len(self.of(card, "plan.approved")), 1)
        self.assertEqual(len(self.numbers(card)), 2)

    def test_second_approve_of_the_plan_difference_returns_approving_and_snapshot_is_clean_afterwards(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        reply = self.call("EditCardPlan", card, json.dumps({"action": "return", "feedback": "Dodaj ticket"}))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=60), self.events(card))
        plan = self.wait_column(card, "waiting")["derived"]["flow"]["plan"]
        self.assertEqual(plan["status"], "pending")
        self.assertFalse(plan["approving"], "różnica planu nie ma trwałego plan.approval_requested")
        pending = self.hold_first(card)
        self.assert_second_is_answered_first(pending, "ApproveCardPlan", card)
        first = self.release(pending)
        self.assertTrue(first["ok"], first)
        self.assertEqual(len(self.of(card, "plan.approved")), 1)
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["plan"]["status"] in ("approved", "done"),
                                   timeout=30))
        self.assertFalse(self.shown(card)["derived"]["flow"]["plan"]["approving"])
