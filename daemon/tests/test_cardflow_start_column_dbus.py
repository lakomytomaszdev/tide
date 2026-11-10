"""PT-313: udany StartCard zdejmuje kartę z Backlogu — przez D-Bus na prywatnej magistrali.

Dziś ruch Backlog → Do zrobienia robi tylko okno (launchHook); karta uruchomiona przez D-Bus (`tide go`, skrypty
koordynatora) stała w Backlogu, więc jej stan z dziennika był niewidoczny. Test patrzy na to, co widać z zewnątrz:
zapisaną kolumnę i kolejność w Snapshot(), zdarzenia dziennika i kolumnę wyświetlaną.
"""
import json
import os
import signal

from .privatebus import requires_dbus, wait_until
from .test_cardflow_approval_start_dbus import kill_fakes
from .test_cardflow_dbus import FlowCase


def stored(case, card_id) -> dict:
    return next(c for c in case.call("Snapshot")["cards"] if c["id"] == card_id)


@requires_dbus
class StartColumnTest(FlowCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(kill_fakes, self.base)

    def test_start_from_backlog_moves_card_to_the_end_of_todo(self):
        first = self.card(title="Pierwsza", column="todo")
        second = self.card(title="Druga", column="todo")
        card_id = self.card(description="ATRAPA: czekaj", column="backlog")
        self.assertEqual(stored(self, card_id)["column"], "backlog")
        self.assertTrue(self.start(card_id)["ok"])
        card = stored(self, card_id)
        self.assertEqual(card["column"], "todo")
        todo = sorted((c for c in self.call("Snapshot")["cards"] if c["column"] == "todo"), key=lambda c: c["order"])
        # Nowe karty trafiają na górę kolumny (CreateCard), więc „Druga” stoi przed „Pierwszą”; startowana — na końcu.
        self.assertEqual([c["id"] for c in todo], [second, first, card_id])
        moved = self.of(card_id, "card.moved")
        self.assertEqual([(e["from"], e["to"]) for e in moved], [("backlog", "todo")], self.events(card_id))
        self.assertFalse(self.of(card_id, "card.paused"))
        # Migawka po starcie pokazuje stan przepływu z dziennika, a nie pusty stan Backlogu.
        shown = self.wait_column(card_id, "progress")
        self.assertEqual(shown["derived"]["flow"]["ticket"], "PT-1")

    def test_start_of_a_card_outside_backlog_keeps_its_column_and_order(self):
        card_id = self.card(description="ATRAPA: czekaj", column="todo")
        before = stored(self, card_id)
        self.assertTrue(self.start(card_id)["ok"])
        after = stored(self, card_id)
        self.assertEqual((after["column"], after["order"]), (before["column"], before["order"]))
        self.assertFalse(self.of(card_id, "card.moved"), self.events(card_id))

    def test_failed_start_leaves_the_card_in_backlog(self):
        card_id = self.card(column="backlog")
        reply = self.call("StartCard", card_id, json.dumps({"entry": "now", "executor": "Nie ma takiego"}))
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(stored(self, card_id)["column"], "backlog")
        # Brak projektu z repozytorium to ta sama granica: zlecenie odrzucone przed card.started.
        loose = self.call("CreateCard", json.dumps({"title": "Bez projektu", "column": "backlog"}))["id"]
        reply = self.start(loose)
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(stored(self, loose)["column"], "backlog")
        self.assertFalse(self.of(card_id, "card.moved") or self.of(loose, "card.moved"))

    def test_window_move_to_todo_after_the_start_changes_no_order(self):
        other = self.card(title="Inna", column="todo")
        card_id = self.card(description="ATRAPA: czekaj", column="backlog")
        self.assertTrue(self.start(card_id)["ok"])
        order = lambda: [c["id"] for c in sorted((c for c in self.call("Snapshot")["cards"]
                                                  if c["column"] == "todo"), key=lambda c: c["order"])]
        self.assertEqual(order(), [other, card_id])
        # Okno wysyła swój ruch po odpowiedzi (koniec kolumny: -1) — bez skutku dla kolejności i przepływu.
        self.assertTrue(self.call("MoveCard", card_id, "todo", -1)["ok"])
        self.wait_column(card_id, "progress")
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(order(), [other, card_id])
        self.assertFalse(self.of(card_id, "card.paused") or self.of(card_id, "card.finished"))
        self.assertEqual(stored(self, card_id)["column"], "todo")
