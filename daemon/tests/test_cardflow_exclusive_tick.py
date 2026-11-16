"""Regresja 13.11 (K-113 PT-468-3): polecenie wymagające ciszy (`exclusive`) nie biegnie obok innych ciężkich poleceń
— tej samej karty ani innej — i nie rusza, gdy one biegną. K-109 PT-482.

13.11 pomiar „po K-113, box.enabled=true” (krok PT-468-3, `exclusive: true`) był nieważny: obciążenie po pomiarze
21,57 przy progu 8 (docs/tide-daemon.md, „Zmiany K-113 i wyniki przed/po K-113”), a karta zapisała, że obok biegło
`make test-changed` innej karty. Diagnoza i dowody: notka kroku PT-482 (docs/agents/tickets/k-109-flow/pt-482.md).

Test idzie całą drogą usługi, przez `FlowEngine.tick`: prawdziwe kroki (atrapa agenta prosi o polecenie `outside`),
zgody przez `resolve_step`, start przez atrapę `systemd-run` i prawdziwe `make`, którego cele czekają na plik bramki.
Dopóki trwa wydzielanie wykonania poleceń (K-109: `cardflow/commands.py`, nowa ścieżka startu jednostki w #2
i dalszych ticketach), ten test musi przechodzić bez zmian w oczekiwaniach — zmiana protokołu, kolejki albo startu
nie może przywrócić zachowania z 13.11.
"""
import json
import os
import time
from unittest import mock

from tide_daemon.cardflow import heavyqueue

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git

# Cele czekają na plik `$PT482_GATE/<BASE>`: test decyduje, kiedy polecenie się kończy.
MAKEFILE = ("test-changed test-cardflow:\n"
            "\t@while [ ! -e \"$(PT482_GATE)/$(BASE)\" ]; do sleep 0.05; done\n"
            "\t@echo \"koniec $(BASE)\"\n")
QUIET = "make test-cardflow BASE=pomiar"         # pomiar: ciężkie z wzorca i `exclusive: true` z wyniku kroku
HELD_EXCLUSIVE = "wstrzymane: biegnie polecenie wymagające ciszy: "
WAITS_QUIET = "czeka na ciszę: biegnie "


def body(command: str, exclusive: bool = False) -> str:
    asked = {"command": command, **({"exclusive": True} if exclusive else {})}
    return f"ATRAPA: poza-zadane\nATRAPA-POZA: {json.dumps(asked)}\n"


class ExclusiveNeverRunsBesideHeavyTickTest(FlowInProcess):
    """13.11 K-113 PT-468-3 — patrz opis modułu. Utrzymać w #2 i dalszych ticketach K-109."""

    def setUp(self):
        super().setUp()
        from tide_daemon.cardflow import gitops
        from tide_daemon.tasks.stepstop import sandbox_available
        if not sandbox_available():
            # Piaskownica kroku agenta nie ma zagnieżdżonego bwrap: commit planu przez git bez piaskownicy, jak
            # w test_cardflow_base_context.py. Kolejka poleceń, którą sprawdza test, nie zależy od tego adaptera.
            from .test_cardflow_gitops import LocalStepGit

            class ObjectGit(LocalStepGit):
                def __init__(self, *args, objects_only=False):
                    super().__init__(*args)
            for patch in (mock.patch.object(gitops, "sandbox_available", return_value=True),
                          mock.patch.object(gitops, "StepGit", ObjectGit)):
                patch.start()
                self.addCleanup(patch.stop)
        with open(os.path.join(self.repo, "Makefile"), "w", encoding="utf-8") as fh:
            fh.write(MAKEFILE)
        git(self.repo, "add", "Makefile")
        git(self.repo, "commit", "-qm", "gated make targets")
        self.gate = os.path.join(self.base, "gate")
        os.makedirs(self.gate)
        os.environ["PT482_GATE"] = self.gate        # przywraca patcher środowiska z FlowInProcess
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps(
            {"slug": "proj", "repo": self.repo, "aiDocs": "repo"})))["ok"])

    # --- przygotowanie: karta z dwoma ticketami (plan) i karty Od razu ----------------------------------

    def plan_card(self, tickets: dict) -> str:
        """Karta z przejętym planem: tickety bez blokerów biegną równolegle (ta sama karta)."""
        card = self.card(description="Pomiar i testy.")
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"), exist_ok=True)
        path = os.path.join(folder, "2026-11-16-pt482.md")
        rows = "".join(f"| {ticket} | Krok {ticket} | todo | | Sonnet | prośba outside | standard | Opus | |\n"
                       for ticket in tickets)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: PT-482\ncard: " + card + "\ntracker: local\nticket-prefix: PT\n"
                     "coordinator: claude\nstatus: active\nupdated: 2026-11-16T12:00:00+01:00\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + rows)
        for ticket, text in tickets.items():
            with open(os.path.join(folder, "tickets", ticket + ".md"), "w", encoding="utf-8") as fh:
                fh.write(f"# {ticket}\n\n{text}\n- [ ] Wynik.\n\n## Dla recenzenta\nSprawdź wynik.\n")
        box = []
        self.board.start_card(card, json.dumps({"entry": "import", "planPath": path, "executor": "Sonnet"}),
                              box.append)
        self.assertTrue(wait_until(lambda: box, timeout=15))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=30), self.events(card))
        box.clear()
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0]).get("ok", True), box)
        self.approve_environment(card)
        return card

    def now_card(self, text: str) -> str:
        card = self.card(description="Testy innej karty.\n\n" + text)
        self.assertTrue(self.start(card)["ok"])
        return card

    def asked(self, card: str, ticket: str) -> str:
        """Nazwa kroku ticketu, który czeka na zgodę na polecenie `outside`."""
        def found():
            st = self.flow.engine.journal.state(card)
            return next((name for name, done in st.finished.items() if done.get("outside")
                         and (st.steps.get(name) or {}).get("ticket") == ticket), None)
        name = wait_until(found, timeout=40)
        self.assertTrue(name, self.trouble(card))
        return name

    def trouble(self, card: str) -> str:
        return json.dumps([e for e in self.events(card) if e.get("ok") is False or "error" in e.get("type", "")
                           or e.get("type") == "step.finished"], ensure_ascii=False, indent=1)[-6000:]

    # --- ticki i stan -----------------------------------------------------------------------------------

    def allow(self, card: str, name: str) -> None:
        reply = self.engine.resolve_step(card, {"choice": "run", "step": name})
        self.assertEqual(reply.get("choice"), "run", reply)
        time.sleep(1.1)         # czas zgody w dzienniku ma sekundy: kolejność zgód ma być jednoznaczna

    def ticks(self, n: int = 3) -> None:
        for _ in range(n):
            self.engine.tick()

    def running(self) -> set:
        """Polecenia `outside` rozpoczęte i jeszcze niezakończone (z dzienników wszystkich kart)."""
        out = set()
        for card in self.engine.journal.cards():
            st = self.engine.journal.state(card)
            out |= {name for name in st.outside_started
                    if not any(e.get("type") == "outside.finished" and e.get("step") == name
                               for e in self.engine.journal.read(card))}
        return out

    def ever_started(self) -> set:
        return {e["step"] for card in self.engine.journal.cards() for e in self.engine.journal.read(card)
                if e.get("type") == "outside.started"}

    def release(self, base: str) -> None:
        with open(os.path.join(self.gate, base), "w", encoding="ascii") as fh:
            fh.write("ok\n")

    def wait_finished(self, card: str, name: str) -> None:
        def done():
            self.engine.tick()
            return any(e.get("type") == "outside.finished" and e.get("step") == name
                       for e in self.engine.journal.read(card))
        self.assertTrue(wait_until(done, timeout=30), self.engine._heavy_held_state())

    def held_reason(self, card: str, name: str) -> str:
        reason = self.engine._heavy_view(card).get(name)
        return reason if isinstance(reason, str) else repr(reason)

    # --- scenariusz -------------------------------------------------------------------------------------

    def test_exclusive_waits_for_heavy_of_both_cards_and_then_holds_heavy_of_both_cards(self):
        """Obie strony z 13.11, ta sama i inna karta:

        1. ciężkie w toku (PT ticket tej samej karty i `make test-changed` innej karty) → `exclusive` czeka;
        2. `exclusive` w toku → nowe ciężkie tej samej karty i innej karty czekają, aż się skończy;
        3. po jego końcu ruszają.
        Przez cały czas obok polecenia `exclusive` nie biegnie żadne inne ciężkie polecenie."""
        own = self.plan_card({"PT-7": body(QUIET, exclusive=True),
                              "PT-8": body("make test-changed BASE=wlasne-przed"),
                              "PT-9": body("make test-changed BASE=wlasne-po")})
        other_before = self.now_card(body("make test-changed BASE=inne-przed"))
        other_after = self.now_card(body("make test-changed BASE=inne-po"))
        quiet = self.asked(own, "PT-7")
        own_before, own_after = self.asked(own, "PT-8"), self.asked(own, "PT-9")
        before = self.first_asked(other_before)
        after = self.first_asked(other_after)
        self.flow.stop()
        self.engine = self.flow.engine
        self.engine._read_load = lambda: heavyqueue.Load(1.0, 16)       # spokojna maszyna: decyduje tylko kolejka

        # 1. Ciężkie w toku w obu kartach, potem zgoda na pomiar: pomiar czeka.
        self.allow(own, own_before)
        self.allow(other_before, before)
        self.ticks()
        self.assertEqual(self.running(), {own_before, before})
        self.allow(own, quiet)
        self.ticks()
        self.assertNotIn(quiet, self.ever_started())
        self.assertTrue(self.held_reason(own, quiet).startswith(WAITS_QUIET), self.held_reason(own, quiet))
        self.release("wlasne-przed")
        self.wait_finished(own, own_before)
        self.ticks()
        self.assertNotIn(quiet, self.ever_started(), "pomiar ruszył obok ciężkiego polecenia innej karty")
        self.release("inne-przed")
        self.wait_finished(other_before, before)
        self.ticks()
        self.assertEqual(self.running(), {quiet})

        # 2. Pomiar w toku: nowe ciężkie tej samej karty i innej karty czekają.
        self.allow(own, own_after)
        self.allow(other_after, after)
        self.ticks(5)
        self.assertEqual(self.running(), {quiet}, "ciężkie polecenie ruszyło obok pomiaru (13.11)")
        for card, name in ((own, own_after), (other_after, after)):
            self.assertTrue(self.held_reason(card, name).startswith(HELD_EXCLUSIVE), self.held_reason(card, name))

        # 3. Koniec pomiaru: oba ruszają (limit 2).
        self.release("pomiar")
        self.wait_finished(own, quiet)
        self.ticks()
        self.assertEqual(self.running(), {own_after, after})
        self.release("wlasne-po")
        self.release("inne-po")

    def test_heavy_classified_while_the_board_is_busy_still_waits_for_the_running_exclusive(self):
        """Przyczyna z 13.11 (K-113 PT-468-3, 17:26:20–17:38:39): K-99-PT-395-1 `make test-changed` ruszył 17:32:02
        obok pomiaru, a sekundę później K-99-PT-401-2 z tym samym poleceniem czekał „wstrzymane: biegnie polecenie
        wymagające ciszy”. Klasę PT-395-1 pierwszy policzył odczyt prezentacji (decyzja o zgodzie w migawce,
        `queueview.mark_decisions` → `commands.outside_class`) przy transakcji Tablicy trzymanej przez tick:
        `BoardBusy` dał „lekkie”, a pamięć klasy trzymała to do końca uruchomienia. Lekkie nie przechodzi przez
        kolejkę ciężkich, więc cisza go nie wstrzymała."""
        import threading
        from tide_daemon.board.store import BoardBusy
        own = self.now_card(body(QUIET, exclusive=True))
        other = self.card(description="Testy innej karty.\n\n" + body("make test-changed BASE=inne").replace(
            "poza-zadane", "poza-na-sygnal"))
        self.assertTrue(self.start(other)["ok"])
        quiet = self.first_asked(own)
        started = wait_until(lambda: [e for e in self.of(other, "step.started") if e.get("kind") == "ticket"],
                             timeout=30)
        self.assertTrue(started, self.trouble(other))
        self.flow.stop()
        self.engine = self.flow.engine
        self.engine._read_load = lambda: heavyqueue.Load(1.0, 16)
        self.allow(own, quiet)
        self.ticks()
        self.assertEqual(self.running(), {quiet})
        # Krok innej karty prosi o `make test-changed` dopiero teraz, gdy pomiar biegnie.
        with open(os.path.join(started[0]["stepDir"], "out", "release-agent"), "w", encoding="ascii") as fh:
            fh.write("ok\n")

        def asked():
            self.engine.tick()
            st = self.engine.journal.state(other)
            return next((n for n, d in st.finished.items() if d.get("outside")), None)
        name = wait_until(asked, timeout=30)
        self.assertTrue(name, self.trouble(other))
        # Publikacja migawki (wątek główny, bez czekania na Tablicę) w chwili, gdy transakcję trzyma inny wątek.
        holding, done = threading.Event(), threading.Event()

        def hold():
            with self.store.transaction():
                holding.set()
                done.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(holding.wait(5))
            with self.store.without_waiting() as probe, self.engine.journal.presentation():
                try:
                    self.engine.derived({"id": other, "column": "progress"})
                except BoardBusy:
                    pass        # publikacja odrzuca taką wersję; ważne, co zostaje w pamięci silnika
            self.assertTrue(probe.busy, "odczyt prezentacji nie trafił na zajętą Tablicę")
        finally:
            done.set()
            holder.join()
        self.allow(other, name)
        self.ticks(5)
        self.assertEqual(self.running(), {quiet}, "ciężkie polecenie innej karty ruszyło obok pomiaru (13.11)")
        self.assertTrue(self.held_reason(other, name).startswith(HELD_EXCLUSIVE), self.held_reason(other, name))
        self.release("pomiar")
        self.wait_finished(own, quiet)
        self.ticks()
        self.assertEqual(self.running(), {name})
        self.release("inne")

    def first_asked(self, card: str) -> str:
        def found():
            st = self.flow.engine.journal.state(card)
            return next((name for name, done in st.finished.items() if done.get("outside")), None)
        name = wait_until(found, timeout=40)
        self.assertTrue(name, json.dumps(self.events(card), ensure_ascii=False, indent=1)[-4000:])
        return name
