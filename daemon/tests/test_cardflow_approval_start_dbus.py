"""PT-109: przebieg „Zatwierdź plan i rozpocznij implementację” → start pierwszego kroku, przez D-Bus.

Każdy wariant to osobny test na prywatnej magistrali: usługa z tymczasową Tablicą, tymczasowym repozytorium
i atrapami programów (tests/flowfakes.py), propozycja dwóch ticketów (#2 blokowany przez #1) z pliku planu.
Test patrzy tylko na to, co widać z zewnątrz — zdarzenia dziennika karty (`plan.approved`, wyniki `reserve`,
`step.started`, `step.waiting`) i Snapshot() — i czeka na prawdziwy start kroku (zdarzenie `step.started`
oraz plik `fake-call.json` atrapy), a nie tylko na `plan.approved`. Pierwszy ticket ma w kartce
`ATRAPA: czekaj`, więc żyje i trzyma drugi: „brak startu drugiej” to dowód, nie przypadek czasu.

Limit kroków dostawcy: w ustawieniach `providerSlots.anthropic = 1`, a slot zajmuje krok innej karty, więc dla
ticketu nie ma wolnego miejsca (zero wolnych). Wartość 0 w `providerSlots` usługa traktuje jak brak ustawienia
(domyślne 3), dlatego wyczerpanie odtwarzamy zajętym slotem, nie zerem.
"""
import json
import os
import signal

from .privatebus import requires_dbus, wait_until
from .test_cardflow_dbus import FlowCase


def kill_fakes(base: str) -> None:
    """Procesy atrap (`sleep` z ATRAPA: czekaj) przeżywają usługę — sprzątamy je po ścieżce katalogu testu."""
    me = os.getpid()
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                line = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if base in line and "fakebin" in line:
            try:
                os.kill(int(name), signal.SIGKILL)
            except OSError:
                pass


@requires_dbus
class ApprovalStartBusTest(FlowCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: kill_fakes(self.base))

    # --- pomocnicze -------------------------------------------------------------------------------

    def write_plan(self, card, first="ATRAPA: czekaj", second="ATRAPA: gotowe", reviewer=""):
        """Plan z dwoma ticketami (#2 po #1); `reviewer` pusty — bez wybranego recenzenta w kolumnie Review."""
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-11-03-przebieg.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Plan karty\ncard: " + card +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\nsession: planning-123\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     f"| #1 | Pierwszy plasterek | todo | | Sonnet | konieczny | standard | {reviewer} | |\n"
                     f"| #2 | Drugi plasterek | todo | | Sonnet | po pierwszym | standard | {reviewer} | #1 |\n")
        for number, marker in ((1, first), (2, second)):
            with open(os.path.join(folder, "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\n{marker}\n\n## Kryteria akceptacji\n\n- [ ] Wynik {number}.\n")
        return path

    def propose(self, card, **plan):
        """Propozycja z pliku, review planu i panel „Czeka na mnie” z planem do zatwierdzenia."""
        reply = self.call("StartCard", card, json.dumps({"entry": "plan", "planPath": self.write_plan(card, **plan)}))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=60), self.events(card))
        self.assertEqual(self.results(card, "reserve"), [], "propozycja nie bierze numerów")
        return self.wait_column(card, "waiting", timeout=40)

    def approve(self, card):
        reply = self.call("ApproveCardPlan", card)
        self.assertTrue(reply["ok"], reply)
        return reply

    def ticket_starts(self, card):
        return [e for e in self.of(card, "step.started") if e.get("kind") == "ticket"]

    def wait_start(self, card, ticket, timeout=60):
        """Czeka na prawdziwy start: zdarzenie dziennika i plik atrapy w katalogu wyjściowym kroku."""
        self.assertTrue(wait_until(lambda: any(e["ticket"] == ticket for e in self.ticket_starts(card)),
                                   timeout=timeout), f"{ticket} nie wystartował\n{self.events(card)}")
        started = next(e for e in self.ticket_starts(card) if e["ticket"] == ticket)
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(started["stepDir"], "out", "fake-call.json")),
                                   timeout=10), "atrapa programu nie dostała zlecenia")
        return started

    def settle(self, seconds=2.0):
        wait_until(lambda: False, timeout=seconds)

    def set_slots(self, anthropic):
        with open(os.path.join(self.base, "config", "tide", "cardflow.json"), "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": self.guard_minutes,
                       **({"providerSlots": {"anthropic": anthropic}} if anthropic else {})}, fh)

    def occupy_slot(self, marker):
        """Druga karta „Od razu” zajmuje jedyny slot dostawcy; wraca, gdy jej krok już naprawdę działa."""
        other = self.card(title="Zajmuje slot", description="Zajmij slot.\n\n" + marker)
        self.assertTrue(self.start(other)["ok"])
        self.assertTrue(wait_until(lambda: self.of(other, "step.started"), timeout=20), self.events(other))
        started = self.of(other, "step.started")[0]
        self.assertTrue(wait_until(lambda: os.path.isfile(os.path.join(started["stepDir"], "out", "fake-call.json")),
                                   timeout=10))
        return other

    def numbers(self, card):
        return [r["ticket"] for r in self.results(card, "reserve")]

    # --- warianty ----------------------------------------------------------------------------------

    def test_basic_approval_reserves_two_numbers_and_starts_only_the_first_wave(self):
        card = self.card(description="Plan z pliku")
        shown = self.propose(card)
        self.assertEqual(shown["derived"]["flow"]["plan"]["waves"], [["#1"], ["#2"]])
        approved = self.approve(card)
        first, second = approved["map"]["#1"], approved["map"]["#2"]
        self.assertNotEqual(first, second)
        events = self.of(card, "plan.approved")
        self.assertEqual(len(events), 1)
        self.assertEqual([row["workId"] for row in events[0]["rows"]], ["#1", "#2"])
        self.assertEqual(events[0]["map"], approved["map"])
        self.assertEqual(self.numbers(card), [first, second])
        started = self.wait_start(card, first)
        self.assertLess(max(r["seq"] for r in self.results(card, "reserve")), events[0]["seq"])
        self.assertLess(events[0]["seq"], started["seq"])
        self.settle()
        self.assertEqual([e["ticket"] for e in self.ticket_starts(card)], [first],
                         "drugi ticket jest zablokowany przez pierwszy i nie startuje")
        self.assertEqual(self.numbers(card), [first, second], "start nie bierze kolejnych numerów")

    def test_without_chosen_reviewer_the_catalog_rule_picks_another_model_of_the_same_provider(self):
        card = self.card(description="Plan z pliku")
        self.propose(card, first="ATRAPA: gotowe", reviewer="")
        plan_review = next(e for e in self.of(card, "step.started") if e["kind"] == "plan-review")
        approved = self.approve(card)
        first = approved["map"]["#1"]
        author = self.wait_start(card, first)
        self.assertEqual(plan_review["kind"], "plan-review")  # zatwierdzenie przechodzi mimo braku wybranego recenzenta
        rows = {row["workId"]: row for row in self.of(card, "plan.approved")[0]["rows"]}
        self.assertNotEqual(rows["#1"]["reviewer"], rows["#1"]["executor"])
        self.assertTrue(wait_until(lambda: any(e["kind"] == "review" and e["ticket"] == first
                                               for e in self.of(card, "step.started")), timeout=60), self.events(card))
        review = next(e for e in self.of(card, "step.started") if e["kind"] == "review" and e["ticket"] == first)
        self.assertEqual(review["executor"], rows["#1"]["reviewer"])
        self.assertNotEqual(review["model"], author["model"])
        self.assertEqual(review["provider"], author["provider"])

    def test_returned_plan_shows_difference_and_approving_it_starts_the_corrected_first_wave(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        reply = self.call("EditCardPlan", card, json.dumps({"action": "return", "feedback": "Dodaj ticket"}))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=60), self.events(card))
        self.assertEqual(self.of(card, "plan.revision-ready")[-1]["difference"]["added"], ["#3"])
        plan = self.wait_column(card, "waiting")["derived"]["flow"]["plan"]
        self.assertEqual(plan["status"], "pending")
        self.assertEqual(plan["revision"]["difference"]["added"], ["#3"])
        self.assertEqual(self.of(card, "plan.approved"), [], "różnica jeszcze nie zatwierdzona")
        self.assertEqual(self.numbers(card), [], "numery dopiero po zatwierdzeniu różnicy")
        approved = self.approve(card)
        self.assertEqual(sorted(approved["map"]), ["#1", "#2", "#3"])
        events = self.of(card, "plan.approved")
        self.assertEqual(len(events), 1)
        self.assertEqual([row["workId"] for row in events[0]["rows"]], ["#1", "#2", "#3"])
        self.assertEqual(events[0]["rows"][2]["title"], "Dodatkowy ticket")
        self.assertEqual(self.numbers(card), [approved["map"][k] for k in ("#1", "#2", "#3")])
        # Pierwsza fala poprawionego planu to #1 i #3 (bez blokerów); #2 czeka na #1, który żyje.
        self.wait_start(card, approved["map"]["#1"])
        self.wait_start(card, approved["map"]["#3"])
        self.settle()
        self.assertNotIn(approved["map"]["#2"], [e["ticket"] for e in self.ticket_starts(card)])

    def test_restart_between_approval_and_start_starts_the_step_once_without_double_numbers(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        self.set_slots(1)
        self.occupy_slot("ATRAPA: czekaj")
        approved = self.approve(card)
        first = approved["map"]["#1"]
        self.assertTrue(wait_until(lambda: any(e.get("ticket") == first for e in self.of(card, "step.waiting")),
                                   timeout=20), self.events(card))
        self.assertEqual(self.ticket_starts(card), [], "między zatwierdzeniem a startem")
        numbers = self.numbers(card)
        self.assertEqual(len(numbers), 2)
        self.restart()
        self.settle()
        self.assertEqual(self.ticket_starts(card), [], "po restarcie ticket nadal czeka na slot")
        self.assertEqual(len(self.of(card, "plan.approved")), 1)
        self.assertEqual(self.numbers(card), numbers, "restart nie zdubluje rezerwacji")
        self.set_slots(0)  # bez ustawienia: domyślne 3 sloty
        started = self.wait_start(card, first)
        self.settle()
        self.assertEqual([e["ticket"] for e in self.ticket_starts(card)], [first], "krok startuje raz")
        self.assertEqual(self.numbers(card), numbers)
        self.assertEqual(len([e for e in self.of(card, "step.started") if e.get("step") == started["step"]]), 1)

    def test_exhausted_provider_limit_keeps_ticket_queued_with_reason_and_starts_after_release(self):
        card = self.card(description="Plan z pliku")
        self.propose(card)
        self.set_slots(1)
        other = self.occupy_slot("ATRAPA: powoli 8")
        approved = self.approve(card)
        first, second = approved["map"]["#1"], approved["map"]["#2"]
        self.assertTrue(wait_until(lambda: any(e.get("ticket") == first and "limit" in e.get("reason", "")
                                               for e in self.of(card, "step.waiting")), timeout=20),
                        self.events(card))
        self.assertEqual(self.ticket_starts(card), [])

        def queued():
            tickets = {t["id"]: t for t in self.shown(card)["derived"]["tickets"]}
            return tickets.get(first) if "limit kroków dostawcy" in (tickets.get(first) or {}).get("waitReason", "") else None
        self.assertTrue(wait_until(queued, timeout=10), "powód kolejki widoczny w panelu karty")
        self.assertNotEqual(queued()["state"], "odrzucone")
        # BIEŻĄCE ZACHOWANIE (nie docelowe): kolejka na slot daje stan `czeka`, a kartka mówi `todo`.
        # Rozbieżność czeka na decyzję użytkownika (pytanie kroku PT-109, M1); zmiana stanu musi tu zawieść.
        self.assertEqual(queued()["state"], "czeka")
        self.restart()
        self.settle()
        self.assertEqual(self.of(card, "ticket.rejected"), [])
        self.assertEqual(self.ticket_starts(card), [], "po restarcie ticket nadal w kolejce, nie odrzucony ani zgubiony")
        tickets = {t["id"]: t for t in self.shown(card)["derived"]["tickets"]}
        self.assertEqual(sorted(tickets), sorted([first, second]), "ticket nie znika z panelu")
        self.assertNotEqual(tickets[first]["state"], "odrzucone")
        self.assertEqual(tickets[first]["state"], "czeka", "bieżące zachowanie, patrz komentarz wyżej")
        self.assertIn("limit kroków dostawcy", tickets[first]["waitReason"])
        # Zwolnienie slotu: krok drugiej karty kończy się sam, a ticket startuje bez działania użytkownika.
        self.wait_start(card, first, timeout=90)
        self.assertTrue(self.of(other, "step.finished"), "slot zwolnił się przez koniec kroku, nie przez użytkownika")
        self.assertEqual(self.of(card, "ticket.rejected"), [])
