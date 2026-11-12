"""PT-375 (K-39 #5): limit równoległych ticketów na kartę, egzekwowany przez silnik w `_plan_next`.

Ticket w toku to ticket planu z krokiem, niescalony i nieodrzucony. Scalenie jest wstrzymane atrapą (`_merge`), więc
tickety po review zostają „w toku”, a stan limitu jest deterministyczny."""
import json
import os
import time
from unittest import mock

from tide_daemon.cardflow import ticketlimit

from .privatebus import wait_until
from . import test_cardflow_plan_integration as plan_tests
from . import test_cardflow as flow_tests


class TicketLimitBase(flow_tests.FlowInProcess):
    submit = plan_tests.PlanFlowTest.submit
    approve = plan_tests.PlanFlowTest.approve

    def plan(self, card, modes):
        """Plan z niezależnymi ticketami #1…#n (tryb atrapy na ticket), bez blokerów."""
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-11-12-limit.md")
        rows = "".join(f"| #{n} | Plasterek {n} | todo | | Sonnet | | standard | Opus | |\n"
                       for n in range(1, len(modes) + 1))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan karty\ncard: " + card +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\nsession: planning-123\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + rows)
        for n, mode in enumerate(modes, start=1):
            with open(os.path.join(folder, "tickets", f"{n}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Plasterek {n}\n\nATRAPA: {mode}\nATRAPA-PLIK: {n}\n\n- [ ] Wynik.\n")
        return path

    def settings_set(self, **values):
        with open(self.settings, encoding="utf-8") as fh:
            data = json.load(fh)
        data.update(values)
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def gate_merges(self):
        gate = mock.patch.object(self.flow.engine, "_merge")
        gate.start()
        self.addCleanup(gate.stop)
        return gate

    def started_tickets(self, card):
        return [s["ticket"] for s in self.of(card, "step.started") if s.get("kind") == "ticket"]

    def limit_waits(self, card, ticket=None):
        return [e for e in self.of(card, "step.waiting")
                if e.get("cause") == "card-limit" and (ticket is None or e.get("ticket") == ticket)]

    def merged(self, card, ticket):
        return any(e["key"] == ticket and e.get("ok") for e in self.results(card, "merge"))

    def quiet(self, seconds=1.2):
        """Kilka ticków odpytywania (150 ms) bez nowych zdarzeń do sprawdzenia, że nic się nie zapętla."""
        time.sleep(seconds)

    def start_plan(self, modes, *, ticket_limit=None, card_max=None):
        self.settings_set(ticketsPerCard=ticket_limit if ticket_limit is not None else 2)
        card = self.card()
        if card_max is not None:
            self.assertTrue(json.loads(self.board.update_card(card, json.dumps({"maxTickets": card_max})))["ok"])
        self.assertTrue(self.submit(card, self.plan(card, modes))["ok"])
        return card


class TicketLimitTest(TicketLimitBase):
    def test_four_free_tickets_start_two_and_the_third_waits_for_a_merge(self):
        # Dwa pierwsze tickety pracują kilka sekund (okno na sprawdzenie stanu), pozostałe kończą od razu.
        card = self.start_plan(["powoli 5", "powoli 5", "gotowe", "gotowe"], ticket_limit=2)
        mapping = self.approve(card)["map"]
        t1, t2, t3, t4 = (mapping[f"#{n}"] for n in range(1, 5))
        self.assertTrue(wait_until(lambda: self.limit_waits(card, t3) and self.limit_waits(card, t4), timeout=40),
                        self.events(card))
        self.quiet(1.0)
        self.assertEqual(sorted(set(self.started_tickets(card))), [t1, t2], "limit 2: trzeci ticket nie rusza")
        self.assertEqual(len(self.limit_waits(card, t3)), 1, "jeden zapis na zmianę powodu, bez zapętlenia")
        self.assertEqual(len(self.limit_waits(card, t4)), 1)
        waiting = self.limit_waits(card, t3)[0]
        self.assertEqual(waiting["reason"], "czeka na miejsce w karcie (2 z 2)")
        self.assertEqual(waiting["kind"], "ticket")
        shown = self.shown(card)["derived"]
        row = next(t for t in shown["tickets"] if t["id"] == t3)
        self.assertEqual((row["status"], row["statusLabel"]), ("card-limit", "czeka na miejsce w karcie (2 z 2)"))
        self.assertEqual(shown["flow"]["ticketLimit"], {"limit": 2, "running": 2, "override": None})
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=90), self.events(card))
        first_merge = min(e["seq"] for e in self.results(card, "merge") if e.get("ok"))
        for ticket in (t3, t4):
            starts = [s for s in self.of(card, "step.started") if s.get("ticket") == ticket and s["kind"] == "ticket"]
            self.assertGreater(starts[0]["seq"], first_merge, "ticket rusza dopiero po zwolnieniu miejsca")
            self.assertTrue(self.merged(card, ticket))

    def test_card_max_tickets_overrides_the_default_and_a_question_counts_as_in_progress(self):
        card = self.start_plan(["pytanie", "gotowe"], ticket_limit=2, card_max=1)
        mapping = self.approve(card)["map"]
        t1, t2 = mapping["#1"], mapping["#2"]
        self.assertTrue(wait_until(lambda: self.limit_waits(card, t2), timeout=40), self.events(card))
        self.quiet()
        self.assertEqual(self.started_tickets(card), [t1], "ticket z pytaniem do użytkownika zajmuje miejsce")
        self.assertTrue(self.of(card, "step.finished"), "krok pierwszego ticketu skończył się pytaniem")
        self.assertEqual(self.limit_waits(card, t2)[0]["reason"], "czeka na miejsce w karcie (1 z 1)")
        flow = self.shown(card)["derived"]["flow"]
        self.assertEqual(flow["ticketLimit"], {"limit": 1, "running": 1, "override": 1})

    def test_rejecting_a_ticket_in_progress_frees_the_slot(self):
        card = self.start_plan(["pytanie", "gotowe"], card_max=1)
        mapping = self.approve(card)["map"]
        t1, t2 = mapping["#1"], mapping["#2"]
        self.assertTrue(wait_until(lambda: self.limit_waits(card, t2), timeout=40), self.events(card))
        self.flow.engine.journal.append(card, "ticket.rejected", ticket=t1)
        self.assertTrue(wait_until(lambda: t2 in self.started_tickets(card), timeout=40), self.events(card))

    def test_lowering_the_limit_stops_nothing_and_blocks_new_tickets_until_the_count_drops(self):
        card = self.start_plan(["pytanie", "pytanie", "gotowe"], ticket_limit=2)
        mapping = self.approve(card)["map"]
        t1, t2, t3 = (mapping[f"#{n}"] for n in range(1, 4))
        self.assertTrue(wait_until(lambda: self.limit_waits(card, t3), timeout=40), self.events(card))
        self.settings_set(ticketsPerCard=1)
        self.assertTrue(wait_until(lambda: any(e["reason"].endswith("(2 z 1)") for e in self.limit_waits(card, t3)),
                                   timeout=30), self.limit_waits(card, t3))
        before = {e["type"] for e in self.events(card)}
        self.assertEqual(sorted(set(self.started_tickets(card))), [t1, t2])
        self.assertFalse(self.of(card, "step.stopped") or self.of(card, "step.cancelled"), "trwające nie są zatrzymywane")
        self.assertIn("czeka na miejsce w karcie (2 z 1)", [e["reason"] for e in self.limit_waits(card, t3)],
                      "powód odświeża się po zmianie limitu")
        self.assertNotIn("ticket.rejected", before)
        self.flow.engine.journal.append(card, "ticket.rejected", ticket=t1)
        self.quiet()
        self.assertNotIn(t3, self.started_tickets(card), "jeden ticket w toku przy limicie 1 — nadal brak miejsca")
        self.flow.engine.journal.append(card, "ticket.rejected", ticket=t2)
        self.assertTrue(wait_until(lambda: t3 in self.started_tickets(card), timeout=40), self.events(card))

    def test_invalid_card_value_in_the_file_gives_the_default_limit_and_a_warning(self):
        card = self.card()
        path = os.path.join(self.root, "cards", f"{card}.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("\n---\n", "\nmax-tickets: 9\n---\n", 1))
        board = self.store.load()
        shown = board.cards[int(card.split("-")[1])]
        self.assertIsNone(shown.max_tickets)
        self.assertTrue(any("limit ticketów" in e["message"] for e in board.errors), board.errors)


class TicketLimitSettingsTest(TicketLimitBase):
    def test_settings_default_and_validation(self):
        engine = self.flow.engine
        with open(self.settings, encoding="utf-8") as fh:
            data = json.load(fh)
        data.pop("ticketsPerCard")
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.assertEqual(engine.get_flow_settings()["ticketsPerCard"], 2, "brak klucza — domyślne 2")
        self.assertEqual(engine.set_flow_settings({"ticketsPerCard": 4})["ticketsPerCard"], 4)
        from tide_daemon.cardflow.engine import FlowError
        for bad in (0, 7, True, 2.5, "3", None):
            with self.assertRaises(FlowError, msg=repr(bad)) as raised:
                engine.set_flow_settings({"ticketsPerCard": bad})
            self.assertEqual(raised.exception.code, "bad-settings")
        self.assertEqual(engine.get_flow_settings()["ticketsPerCard"], 4, "odmowa nie zmienia pliku")
        self.settings_set(ticketsPerCard=99)
        self.assertEqual(engine.get_flow_settings()["ticketsPerCard"], 2, "zła wartość w pliku — domyślna")

    def test_update_card_validates_max_tickets_and_empty_removes_the_key(self):
        card = self.card()
        path = os.path.join(self.root, "cards", f"{card}.md")
        for bad in (0, 7, True, 1.5, "x", [1]):
            reply = json.loads(self.board.update_card(card, json.dumps({"maxTickets": bad})))
            self.assertFalse(reply["ok"], bad)
        reply = json.loads(self.board.update_card(card, json.dumps({"maxTickets": 3})))
        self.assertTrue(reply["ok"] and reply["changed"], reply)
        self.assertEqual(self.shown(card)["maxTickets"], 3)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("max-tickets: 3", fh.read())
        for empty in ("", None):
            self.assertTrue(json.loads(self.board.update_card(card, json.dumps({"maxTickets": 2})))["ok"])
            reply = json.loads(self.board.update_card(card, json.dumps({"maxTickets": empty})))
            self.assertTrue(reply["ok"] and reply["changed"], reply)
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn("max-tickets", fh.read())
            self.assertIsNone(self.shown(card)["maxTickets"])

    def test_ticketlimit_helpers(self):
        self.assertEqual([ticketlimit.effective(v, 3) for v in (None, 0, 7, True, 1, 6)], [3, 3, 3, 3, 1, 6])
        self.assertEqual(ticketlimit.effective(None, 99), 2)


class NowCardUnaffectedTest(flow_tests.FlowInProcess):
    """Historia 39: karta „Od razu” (jeden ticket) działa bez zmian także przy limicie 1."""
    wait_now_closed = flow_tests.NowInProcessTest.wait_now_closed

    def run_now(self, **card_values):
        with open(self.settings, encoding="utf-8") as fh:
            data = json.load(fh)
        data["ticketsPerCard"] = 1
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        card = self.card()
        if card_values:
            self.assertTrue(json.loads(self.board.update_card(card, json.dumps(card_values)))["ok"])
        reply = self.start(card)
        self.assertTrue(reply["ok"], reply)
        self.wait_now_closed(card)
        waits = [e for e in self.of(card, "step.waiting") if e.get("cause") == "card-limit"]
        self.assertEqual(waits, [], "kroki ticketu w toku (review, close) nie czekają na miejsce w karcie")
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")], ["ticket", "review", "close"])

    def test_one_ticket_card_with_the_default_limit_one(self):
        self.run_now()

    def test_one_ticket_card_with_max_tickets_one(self):
        self.run_now(maxTickets=1)
