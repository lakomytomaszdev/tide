"""PT-377 (K-39 #7): priorytet karty i kolejność kroków czekających na miejsce u dostawcy (`cardflow/stepqueue`).

Zwolnione miejsce bierze krok karty o wyższym priorytecie, przy równym — krok czekający dłużej, a nie karta sprawdzana
w ticku jako pierwsza. Testy silnika biegną przez `FlowEngine._admit` na złożonych dziennikach (bez procesów kroków,
jak `SlotPriorityTest` z PT-150); karty i ich priorytety są prawdziwymi plikami Tablicy (`BoardStore`)."""
import json
import os
import types
import unittest
from datetime import datetime
from unittest import mock

from tide_daemon.board.model import BoardError
from tide_daemon.cardflow import caretaker, stepqueue
from tide_daemon.cardflow.engine import FlowDeferred
from tide_daemon.cardflow.state import fold

from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker_retry import EngineOnJournal


def at(hhmm: str, second: int = 0) -> float:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 12, hour, minute, second).astimezone().timestamp()


def executor(provider="anthropic", model="sonnet"):
    return types.SimpleNamespace(provider=provider, local=False, route=None, model=model, name=model.title())


class QueueOnJournal(EngineOnJournal):
    """`providerSlots` = 1 (anthropic) i 1 (openai); K-1 zajmuje miejsce u anthropic. Karty K-1…K-4 są na Tablicy."""

    def setUp(self):
        super().setUp()
        with open(self.engine.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 1, "openai": 1}}, fh)
        for n in range(1, 5):
            reply = self.engine.store.create_card({"title": f"Karta {n}"})
            self.assertEqual(reply["id"], f"K-{n}")
            self.add(f"K-{n}", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                     preferredProvider="anthropic", column="todo")
        self.add("K-1", "step.started", step="K-1-PT-1-1", kind="ticket", ticket="PT-1", provider="anthropic")
        self.sonnet, self.codex = executor(), executor("openai", "codex")

    def priority(self, card, value):
        self.engine.store.update_card(card, {"priority": value})

    def admit(self, card, selected, ticket, kind="ticket", step=None, *, clock=None):
        """Jeden start kroku jak w `_launch`: przyjęty krok dostaje `step.started` (zajmuje miejsce)."""
        step = step or f"{card}-{ticket}-1"
        saved = self.journal.clock
        if clock is not None:
            self.journal.clock = lambda: clock
        try:
            try:
                self.engine._admit(card, selected, ticket, kind, step)
            except FlowDeferred as exc:
                return str(exc)
            self.add(card, "step.started", step=step, kind=kind, ticket=ticket, provider=selected.provider)
            return ""
        finally:
            self.journal.clock = saved

    def round(self, steps):
        """Obrót pętli w podanej kolejności kart; zwraca kroki, które ruszyły."""
        started = []
        for card, selected, ticket in steps:
            step = f"{card}-{ticket}-1"
            if step in fold(self.journal.read(card)).steps:
                continue
            if not self.admit(card, selected, ticket):
                started.append(step)
        return started

    def free_slot(self):
        self.add("K-1", "step.finished", step="K-1-PT-1-1", result="done")

    def states(self, skip=()):
        return {c: self.journal.state(c) for c in self.journal.cards() if c not in skip}


class PriorityOrderTest(QueueOnJournal):
    def two_waiting(self):
        """Karta A (K-2, normalny) czeka od 10:00, karta B (K-3, wysoki) od 10:05 — na jedno miejsce u anthropic."""
        self.priority("K-3", "high")
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("10:00")))
        self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("10:05")))
        self.free_slot()

    def test_higher_priority_card_takes_the_slot_when_checked_last(self):
        self.two_waiting()
        self.assertEqual(self.round([("K-2", self.sonnet, "PT-2"), ("K-3", self.sonnet, "PT-3")]), ["K-3-PT-3-1"],
                         "A sprawdzana pierwsza ustępuje B o wyższym priorytecie")
        self.assertEqual(self.round([("K-2", self.sonnet, "PT-2")]), [], "limit dostawcy nadal obowiązuje")

    def test_higher_priority_card_takes_the_slot_when_checked_first(self):
        self.two_waiting()
        self.assertEqual(self.round([("K-3", self.sonnet, "PT-3"), ("K-2", self.sonnet, "PT-2")]), ["K-3-PT-3-1"])
        self.add("K-3", "step.finished", step="K-3-PT-3-1", result="done")
        self.assertEqual(self.round([("K-3", self.sonnet, "PT-3"), ("K-2", self.sonnet, "PT-2")]), ["K-2-PT-2-1"],
                         "po B miejsce bierze A — bez zagłodzenia")

    def test_low_priority_yields_to_a_normal_card_waiting_later(self):
        self.priority("K-2", "low")
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("09:00")))
        self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("10:00")))
        self.free_slot()
        self.assertEqual(self.round([("K-2", self.sonnet, "PT-2"), ("K-3", self.sonnet, "PT-3")]), ["K-3-PT-3-1"])

    def test_equal_priority_longer_wait_goes_first(self):
        self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("10:00")))
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("10:05")))
        self.free_slot()
        self.assertEqual(self.round([("K-2", self.sonnet, "PT-2"), ("K-3", self.sonnet, "PT-3")]), ["K-3-PT-3-1"],
                         "K-3 czeka dłużej, choć jest sprawdzana druga")

    def test_same_second_tie_is_decided_by_card_ticket_and_kind_without_mutual_wait(self):
        self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("10:00")))
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("10:00")))
        since = {self.journal.state(c).waiting[(t, "ticket")]["since"] for c, t in (("K-2", "PT-2"), ("K-3", "PT-3"))}
        self.assertEqual(len(since), 1, "oba czekają od tej samej sekundy")
        self.free_slot()
        order = [("K-3", self.sonnet, "PT-3"), ("K-2", self.sonnet, "PT-2")]
        self.assertEqual(self.round(order), ["K-2-PT-2-1"], "remis: stały porządek (karta, ticket, rodzaj)")
        self.add("K-2", "step.finished", step="K-2-PT-2-1", result="done")
        self.assertEqual(self.round(order), ["K-3-PT-3-1"], "drugi rusza zaraz po pierwszym — bez cyklu")
        # Wprost: z dwóch kroków o tym samym `since` dokładnie jeden stoi przed drugim, nigdy obaj nawzajem.
        since = since.pop()
        a = stepqueue.Entry("K-2", "PT-2", "ticket", "anthropic", since)
        b = stepqueue.Entry("K-3", "PT-3", "ticket", "anthropic", since)
        self.assertEqual((stepqueue.ahead([a, b], a), stepqueue.ahead([a, b], b)), (0, 1))
        c = stepqueue.Entry("K-2", "PT-2", "review", "anthropic", since)
        self.assertEqual((stepqueue.ahead([a, c], a), stepqueue.ahead([a, c], c)), (0, 0), "ta sama karta nie czeka")

    def test_priority_change_reorders_the_waiting_steps(self):
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("10:00")))
        self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("10:05")))
        self.priority("K-3", "high")
        self.priority("K-3", "normal")     # powrót do normalnego: K-2 czeka dłużej
        self.free_slot()
        self.assertEqual(self.round([("K-3", self.sonnet, "PT-3"), ("K-2", self.sonnet, "PT-2")]), ["K-2-PT-2-1"])


class CurrentCauseTest(QueueOnJournal):
    def quota(self, provider):
        """Limit tygodniowy przekroczony u `provider` (atrapa `_low_limit`)."""
        info = {"provider": provider, "remaining": 2, "window": "weekly", "reset": "pon 10:00"}
        return mock.patch.object(self.engine, "_low_limit", side_effect=lambda p: info if p == provider else None)

    def test_higher_priority_step_waiting_for_another_providers_weekly_limit_does_not_block(self):
        self.priority("K-3", "high")
        with self.quota("openai"):
            reason = self.admit("K-3", self.codex, "PT-3", clock=at("09:00"))
            self.assertTrue(reason.startswith("czeka na limit Codex"), reason)
            self.assertEqual(self.journal.state("K-3").waiting[("PT-3", "ticket")]["cause"], "quota")
            self.free_slot()
            self.assertEqual(self.round([("K-2", self.sonnet, "PT-2")]), ["K-2-PT-2-1"],
                             "krok o niższym priorytecie u innego dostawcy rusza")

    def test_saved_slots_wait_of_a_provider_now_past_its_weekly_limit_does_not_block(self):
        """Zapisany `cause: slots`, ale w tym ticku dostawca (openai) przekroczył limit: bieżąca przyczyna to limit."""
        self.priority("K-3", "high")
        self.add("K-4", "step.started", step="K-4-PT-9-1", kind="ticket", ticket="PT-9", provider="openai")
        self.assertTrue(self.admit("K-3", self.codex, "PT-3", clock=at("09:00")).startswith("limit kroków dostawcy"))
        self.assertEqual(self.journal.state("K-3").waiting[("PT-3", "ticket")]["cause"], "slots")
        self.free_slot()
        with self.quota("openai"):
            entries = stepqueue.order(self.states(), priority_of=lambda _c: "normal",
                                      quota_of=lambda p: "limit" if p == "openai" else "")
            mine = next(e for e in entries if e.card == "K-3")
            self.assertEqual((mine.cause, mine.queued), ("quota", False), "bieżąca przyczyna, nie zapisany cause")
            self.assertEqual(next(e for e in self.engine._step_order(self.states()) if e.card == "K-3").cause, "quota")
            self.assertEqual(self.round([("K-2", self.sonnet, "PT-2")]), ["K-2-PT-2-1"])

    def test_saved_quota_wait_whose_limit_renewed_is_back_in_the_queue(self):
        self.priority("K-3", "high")
        with self.quota("anthropic"):
            self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("09:00")).startswith("czeka na limit"))
        self.assertEqual(self.journal.state("K-3").waiting[("PT-3", "ticket")]["cause"], "quota")
        self.free_slot()
        self.assertEqual(self.round([("K-2", self.sonnet, "PT-2"), ("K-3", self.sonnet, "PT-3")]), ["K-3-PT-3-1"],
                         "limit odnowiony: K-3 wraca do kolejki i bierze miejsce przed K-2")

    def test_new_ticket_held_by_its_card_limit_does_not_take_the_slot(self):
        """K-3 (wysoki) czekała na miejsce u dostawcy z nowym ticketem, a teraz karta ma pełny limit ticketów."""
        self.priority("K-3", "high")
        self.engine.store.update_card("K-3", {"maxTickets": 1})
        rows = [{"id": t, "workId": t, "title": t, "blockers": []} for t in ("PT-3", "PT-5")]
        self.add("K-3", "plan.approved", rows=rows, map={})
        self.assertTrue(self.admit("K-3", self.sonnet, "PT-3", clock=at("09:00")))
        self.add("K-3", "step.started", step="K-3-PT-5-1", kind="ticket", ticket="PT-5", provider="openai")
        self.free_slot()
        entries = self.engine._step_order(self.states())
        held = next(e for e in entries if e.card == "K-3")
        self.assertEqual((held.cause, held.reason), ("ticket", "czeka na miejsce w karcie (1 z 1)"))
        self.assertEqual(self.round([("K-2", self.sonnet, "PT-2")]), ["K-2-PT-2-1"])


class OrderFunctionTest(QueueOnJournal):
    """`stepqueue.order` — jedna funkcja porządku: czekający w kolejności startu z powodem i wykluczeniami."""

    def test_order_lists_waiters_in_start_order_with_reasons_and_exclusions(self):
        with open(self.engine.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 1}}, fh)
        self.priority("K-4", "low")
        self.priority("K-3", "high")
        for n in range(5, 9):
            self.assertEqual(self.engine.store.create_card({"title": f"Karta {n}"})["id"], f"K-{n}")
            self.add(f"K-{n}", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                     preferredProvider="anthropic", column="todo")
        waits = [("K-4", "PT-4", "08:00"), ("K-2", "PT-2", "10:00"), ("K-3", "PT-3", "11:00"),
                 ("K-5", "PT-5", "07:00"), ("K-6", "PT-6", "07:30"), ("K-7", "PT-7", "07:45"),
                 ("K-8", "PT-8", "09:00")]
        for card, ticket, when in waits:
            self.assertTrue(self.admit(card, self.sonnet, ticket, clock=at(when)))
        self.add("K-5", "card.paused")                       # wstrzymana
        self.add("K-6", "ticket.rejected", ticket="PT-6")    # odrzucony ticket
        self.add("K-7", "card.finished", column="done")      # zakończona
        self.add("K-8", "step.started", step="K-8-PT-8-1", kind="ticket", ticket="PT-8", provider="anthropic")
        board = self.engine.store.load()
        entries = stepqueue.order(self.states(), priority_of=lambda c: board.cards[int(c[2:])].priority)
        self.assertEqual([(e.card, e.priority) for e in entries],
                         [("K-3", "high"), ("K-2", "normal"), ("K-4", "low")],
                         "wysoki, potem normalny, potem niski — bez wstrzymanej, odrzuconego, zakończonej i trwającego")
        self.assertTrue(all(e.queued and e.reason.startswith("limit kroków dostawcy anthropic") for e in entries))
        self.assertEqual([e.card for e in entries], [e.card for e in sorted(entries, key=stepqueue.key)])
        # Ten sam wynik z wywołania silnika (priorytety z Tablicy).
        self.assertEqual(self.engine._step_order(self.states()), entries)

    def test_not_yet_waiting_step_comes_after_waiters_of_its_priority(self):
        early = stepqueue.Entry("K-2", "PT-2", "ticket", "anthropic", "2026-11-12T10:00:00+01:00")
        fresh = stepqueue.Entry("K-3", "PT-3", "ticket", "anthropic", "")
        high = stepqueue.Entry("K-4", "PT-4", "ticket", "anthropic", "", stepqueue.HIGH)
        self.assertLess(stepqueue.key(early), stepqueue.key(fresh))
        self.assertLess(stepqueue.key(high), stepqueue.key(early), "wyższy priorytet przed dłużej czekającym")
        self.assertEqual(stepqueue.ahead([early, high], fresh), 2)
        bumped = stepqueue.Entry("K-5", "PT-5", "ticket", "anthropic", "", stepqueue.LOW, bumped=True)
        self.assertLess(stepqueue.key(bumped), stepqueue.key(high), "podbicie (#11) jest pierwszym polem klucza")

    def test_priority_values(self):
        self.assertEqual([stepqueue.of(v) for v in ("high", "normal", "low", "", None, "urgent", 1)],
                         ["high", "normal", "low", "normal", "normal", "normal", "normal"])

    def test_engine_start_calls_the_same_order_function(self):
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("10:00")))
        self.free_slot()
        with mock.patch.object(stepqueue, "order", wraps=stepqueue.order) as order:
            self.assertEqual(self.round([("K-3", self.sonnet, "PT-3")]), [])
        self.assertTrue(order.called, "start kroku liczy kolejkę przez stepqueue.order")
        self.assertEqual(order.call_args.kwargs["provider"], "anthropic")
        self.assertEqual(sorted(order.call_args.args[0]), ["K-1", "K-2", "K-4"], "stany innych kart z journal.state")

    def test_caretaker_kind_is_ordered_by_the_same_key(self):
        """Opiekun karty o wysokim priorytecie wyprzedza wcześniej czekający ticket karty normalnej."""
        self.priority("K-3", "high")
        self.add("K-3", "step.started", step="K-3-PT-3-1", kind="ticket", ticket="PT-3", provider="anthropic",
                 attempt=1)
        self.add("K-3", "step.finished", step="K-3-PT-3-1", ticket="PT-3", result="error", reason="x")
        self.add("K-3", caretaker.STARTED, problem="error:K-3-PT-3-1", key="caretaker:error:K-3-PT-3-1:1", n=1,
                 step="K-3-care-1", target="K-3-PT-3-1", root="K-3-PT-3-1", ticket="PT-3", trigger="error",
                 triggers=["error"], decision="step:K-3-PT-3-1", watermark=fold(self.journal.read("K-3")).last_seq)
        opus = executor(model="opus")
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2", clock=at("09:00")))
        self.assertTrue(self.admit("K-3", opus, "PT-3", caretaker.KIND, "K-3-care-1", clock=at("10:00")))
        self.free_slot()
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2"))
        self.assertEqual(self.admit("K-3", opus, "PT-3", caretaker.KIND, "K-3-care-1"), "")


class OrderReadsTest(QueueOnJournal):
    """K-98: kolejność liczona ze stanów kart (`journal.state`) — bez odczytów dzienników na krok — i z kontekstu
    odczytu Tablicy bieżącego ticku; poza tickiem najwyżej jeden świeży odczyt Tablicy na decyzję."""

    def waiting_everywhere(self):
        self.priority("K-4", "high")
        for card, ticket in (("K-2", "PT-2"), ("K-3", "PT-3"), ("K-4", "PT-4")):
            self.assertTrue(self.admit(card, self.sonnet, ticket))
        for card in self.journal.cards():
            self.journal.state(card)          # rozgrzana pamięć projekcji, jak po fazie odczytu ticku

    def counted(self):
        from tide_daemon.cardflow import journal as journal_module
        reads, loads = [], []
        real_read, real_load = journal_module._read, self.engine.store.load
        patches = [mock.patch.object(journal_module, "_read", side_effect=lambda p: reads.append(p) or real_read(p)),
                   mock.patch.object(self.engine.store, "load",
                                     side_effect=lambda *a, **k: loads.append(1) or real_load(*a, **k))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return reads, loads

    def test_tick_context_gives_priorities_without_journal_or_board_reads(self):
        import threading
        from tide_daemon.board.readcontext import BoardRead
        self.waiting_everywhere()
        read = BoardRead(self.engine.store, invalidate_on_write=True)
        read.board
        self.engine._tick_read = (threading.get_ident(), read)
        self.addCleanup(setattr, self.engine, "_tick_read", None)
        reads, loads = self.counted()
        for card, ticket in (("K-2", "PT-2"), ("K-3", "PT-3"), ("K-4", "PT-4")):
            self.assertTrue(self.admit(card, self.sonnet, ticket), "ten sam powód — bez nowego zapisu")
        self.assertEqual(reads, [], "bez odczytów dzienników na krok")
        self.assertEqual(loads, [], "priorytety z kontekstu odczytu Tablicy ticku")

    def test_outside_a_tick_one_fresh_board_read_per_decision_and_none_without_waiters(self):
        self.waiting_everywhere()
        reads, loads = self.counted()
        self.assertTrue(self.admit("K-2", self.sonnet, "PT-2"))
        self.assertEqual((reads, len(loads)), ([], 1))
        loads.clear()
        self.assertEqual(self.admit("K-2", self.codex, "PT-7", step="K-2-PT-7-1"), "")
        self.assertEqual(loads, [], "nikt nie czeka u openai — Tablica nie jest czytana")


class PriorityCardKeyTest(FlowInProcess):
    """`priority` karty: UpdateCard, migawka i zła wartość w pliku."""

    def test_update_card_accepts_three_values_and_rejects_others(self):
        card = self.card()
        path = os.path.join(self.root, "cards", f"{card}.md")
        self.assertEqual(self.shown(card)["priority"], "normal", "brak klucza — normalny")
        for bad in ("urgent", "", None, 1, True, "HIGH", ["high"]):
            reply = json.loads(self.board.update_card(card, json.dumps({"priority": bad})))
            self.assertFalse(reply["ok"], bad)
        for value in ("high", "low"):
            reply = json.loads(self.board.update_card(card, json.dumps({"priority": value})))
            self.assertTrue(reply["ok"] and reply["changed"], reply)
            self.assertEqual(self.shown(card)["priority"], value)
            with open(path, encoding="utf-8") as fh:
                self.assertIn(f"priority: {value}", fh.read())
        reply = json.loads(self.board.update_card(card, json.dumps({"priority": "normal"})))
        self.assertTrue(reply["ok"] and reply["changed"], reply)
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("priority:", fh.read(), "normalny to brak klucza")
        self.assertEqual(self.shown(card)["priority"], "normal")

    def test_invalid_value_in_the_file_gives_normal_and_a_warning_in_the_snapshot(self):
        card = self.card()
        path = os.path.join(self.root, "cards", f"{card}.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("\n---\n", "\npriority: urgent\n---\n", 1))
        snapshot = json.loads(self.board.snapshot_json())
        shown = next(c for c in snapshot["cards"] if c["id"] == card)
        self.assertEqual(shown["priority"], "normal")
        self.assertTrue(any("niepoprawny priorytet" in e["message"] for e in snapshot["errors"]), snapshot["errors"])

    def test_create_card_accepts_priority(self):
        reply = json.loads(self.board.create_card(json.dumps({"title": "Pilne", "project": "proj", "priority": "high",
                                                              "column": "todo"})))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.shown(reply["id"])["priority"], "high")
        with self.assertRaises(BoardError):
            self.store.create_card({"title": "Zła", "priority": "pilne"})


if __name__ == "__main__":
    unittest.main()
