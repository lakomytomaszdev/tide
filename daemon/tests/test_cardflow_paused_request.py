"""K-125 #2 (PT-487): wstrzymana karta nie ocenia w ticku prośby „uruchom poza piaskownicą” bez decyzji.

Pauza jest sprawdzana na stanie ticku w pamięci przed `_consent_recheck`: brak odczytów Tablicy z powodu prośby
(`BoardStore.load` w `_outside_recheck_rules` i `consent.automatic`) i brak zapisów w dzienniku, także
`outside.auto-skipped`. Po wznowieniu ocena rusza w następnym ticku dzisiejszą drogą. Odczyty liczy `Counting`
z test_cardflow_tick_reads.py (granica `BoardStore.load` i pełnego ścisłego odczytu dziennika); czasu nie mierzymy."""
import contextlib
import json
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow import consent
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.state import fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_tick_reads import Counting

COMMAND = "echo zewnetrzne-ok"


class Watch:
    """Wywołania oceny prośby w jednym ticku i wczytania Tablicy wykonane wewnątrz `_consent_recheck`."""

    def __init__(self, engine):
        self.engine = engine
        self.recheck = self.automatic = self.skip = 0
        self.recheck_loads = 0

    @contextlib.contextmanager
    def __call__(self):
        real_recheck, real_automatic, real_skip = FlowEngine._consent_recheck, consent.automatic, consent.skip_reason
        with Counting(self.engine)() as count:
            def recheck(this, card, step, done):
                self.recheck += 1
                before = len(count.loads)
                try:
                    return real_recheck(this, card, step, done)
                finally:
                    self.recheck_loads += len(count.loads) - before

            def automatic(*args, **kwargs):
                self.automatic += 1
                return real_automatic(*args, **kwargs)

            def skip(*args, **kwargs):
                self.skip += 1
                return real_skip(*args, **kwargs)
            with mock.patch.object(FlowEngine, "_consent_recheck", recheck), \
                    mock.patch.object(consent, "automatic", automatic), \
                    mock.patch.object(consent, "skip_reason", skip):
                yield self


class PausedOutsideRequestTest(FlowInProcess):
    """Karta „Od razu”, krok atrapy prosi o `echo zewnetrzne-ok` poza piaskownicą; bez automatycznej zgody przy wyniku
    prośba czeka na decyzję. Monitor zatrzymany, ticki wywołuje test."""

    def setUp(self):
        super().setUp()
        self.card_id = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.start(self.card_id)
        self.assertTrue(wait_until(lambda: self.of(self.card_id, "step.finished"), timeout=30),
                        self.events(self.card_id))
        self.flow.stop()
        self.engine = self.flow.engine
        self.journal = self.engine.journal
        finished = self.of(self.card_id, "step.finished")[-1]
        self.assertEqual((finished["result"], finished.get("outside"), finished.get("command")),
                         ("denied", True, COMMAND), finished)
        self.name = finished["step"]
        self.assertNotIn(self.name, self.state().step_decisions)

    def state(self):
        return fold(self.journal.read_complete(self.card_id))

    def remember(self):
        """Zapamiętana rodzina polecenia projektu: reguły dają automatyczną zgodę `run`."""
        data = json.loads(Path(self.settings).read_text())
        data["outsideCommands"] = {"proj": [COMMAND + " *"]}
        Path(self.settings).write_text(json.dumps(data))

    def pause(self):
        self.assertTrue(self.engine.set_paused(self.card_id, True)["ok"])

    def resume(self):
        self.assertTrue(self.engine.set_paused(self.card_id, False)["ok"])

    def ticked(self, *, warm=True):
        """Drugi tick (pierwszy rozgrzewa pamięci) pod licznikami; zwraca (liczniki, nowe zdarzenia karty).
        `warm=False` — liczony jest już pierwszy tick (pierwszy po wznowieniu)."""
        if warm:
            with mock.patch.object(self.engine, "_start_outside"):
                self.engine.tick()
        before = len(self.journal.read_complete(self.card_id))
        with mock.patch.object(self.engine, "_start_outside"), Watch(self.engine)() as watch:
            self.engine.tick()
        return watch, self.journal.read_complete(self.card_id)[before:]

    def test_active_card_evaluates_the_request_every_tick_and_reads_the_board_for_it(self):
        """Kontrola pomiaru: ta sama prośba w karcie aktywnej (reguły nie dają zgody) idzie dzisiejszą drogą."""
        watch, new = self.ticked()
        self.assertEqual((watch.recheck, watch.automatic), (1, 1))
        self.assertGreaterEqual(watch.recheck_loads, 2, "ocena prośby czyta Tablicę (_outside_recheck_rules, automatic)")
        self.assertEqual(new, [])

    def test_paused_card_skips_the_evaluation_without_board_reads_or_journal_writes(self):
        self.remember()                          # reguły dałyby zgodę — wstrzymanie i tak nie ocenia prośby
        self.pause()
        watch, new = self.ticked()
        self.assertEqual((watch.recheck, watch.automatic, watch.skip, watch.recheck_loads), (0, 0, 0, 0))
        self.assertEqual(new, [], "wstrzymana karta dopisała zdarzenia w ticku")
        self.assertEqual(self.of(self.card_id, "outside.auto-skipped"), [])
        st = self.state()
        self.assertNotIn(self.name, st.step_decisions)
        self.assertTrue(st.paused)

    def test_after_resume_the_next_tick_grants_the_automatic_run_as_today(self):
        self.remember()
        self.pause()
        self.ticked()
        self.resume()
        watch, new = self.ticked(warm=False)
        self.assertEqual(watch.recheck, 1)
        decisions = [e for e in new if e["type"] == "step.decision" and e["step"] == self.name]
        self.assertEqual([(d["choice"], d.get("auto"), d["command"]) for d in decisions], [("run", True, COMMAND)])
        self.assertEqual(self.of(self.card_id, "outside.auto-skipped"), [])

    def test_after_resume_a_human_deny_of_the_command_gives_one_skip_with_its_reason(self):
        """Odmowa człowieka tego polecenia w innym kroku tego ticketu (PT-311): po wznowieniu jedno
        `outside.auto-skipped` z powodem `denied-before`, kolejne ticki nie powtarzają wpisu."""
        self.remember()
        step = self.state().steps[self.name]
        prior = f"{self.name}-wczesniej"
        append = self.journal.append
        # Wcześniejsza prośba tego samego ticketu, odmówiona przez człowieka, a potem bieżąca prośba (ostatni krok).
        append(self.card_id, "step.started", step=prior, ticket=step["ticket"], kind="ticket",
               stepDir=str(Path(self.base, prior)), worktree=step["worktree"], baseVersion=step.get("baseVersion", 0))
        append(self.card_id, "step.finished", step=prior, ticket=step["ticket"], result="denied", outside=True,
               command=COMMAND, canAllow=True)
        append(self.card_id, "step.decision", step=prior, ticket=step["ticket"], kind="ticket", choice="deny",
               answer="nie teraz", command=COMMAND, executor="")
        current = f"{self.name}-teraz"
        append(self.card_id, "step.started", step=current, ticket=step["ticket"], kind="ticket",
               stepDir=str(Path(self.base, current)), worktree=step["worktree"],
               baseVersion=step.get("baseVersion", 0))
        append(self.card_id, "step.finished", step=current, ticket=step["ticket"], result="denied", outside=True,
               command=COMMAND, canAllow=True)
        self.pause()
        watch, new = self.ticked()
        self.assertEqual((watch.recheck, new), (0, []))
        self.resume()
        watch, _new = self.ticked(warm=False)
        self.engine.tick()
        self.assertEqual(watch.recheck, 1)
        self.assertEqual([(e["step"], e["reason"]) for e in self.of(self.card_id, "outside.auto-skipped")],
                         [(current, consent.DENIED_BEFORE)])
        self.assertNotIn(current, self.state().step_decisions)

    def test_paused_request_stays_open_and_the_panel_can_still_resolve_it(self):
        self.pause()
        self.ticked()
        self.assertNotIn(self.name, self.state().step_decisions)
        with mock.patch.object(self.engine, "_start_outside"):
            reply = self.engine.resolve_step(self.card_id, {"choice": "run", "step": self.name})
        self.assertTrue(reply["ok"], reply)
        decision = self.state().step_decisions[self.name]
        self.assertEqual((decision["choice"], decision.get("auto")), ("run", None))
        # Wstrzymana karta nie uruchamia polecenia także po decyzji panelu (PT-264) — to bez zmian.
        self.assertEqual(self.of(self.card_id, "outside.started"), [])


class PausedPlanningRequestTest(FlowInProcess):
    """`_advance_planning` zwraca przy pauzie przed oceną prośby kroku planowania (zachowane, teraz z testem)."""

    def test_paused_planning_request_is_not_evaluated_and_writes_nothing(self):
        card = self.card()
        self.flow.stop()
        eng = self.flow.engine
        Path(self.settings).write_text(json.dumps({**json.loads(Path(self.settings).read_text()),
                                                   "outsideCommands": {"proj": [COMMAND + " *"]}}))
        eng.journal.append(card, "card.started", project="proj", entry="planning", executor="Sonnet",
                           repo=self.repo, baseVersion=1)
        eng.journal.append(card, "step.started", step="plan-1", ticket="", kind="planning",
                           stepDir=str(Path(self.base, "plan-1")), worktree=self.repo, baseVersion=1)
        eng.journal.append(card, "step.finished", step="plan-1", ticket="", result="denied", outside=True,
                           command=COMMAND, canAllow=True)
        eng.journal.append(card, "card.paused")
        before = eng.journal.read_complete(card)
        st = fold(before)
        with Counting(eng)() as count, \
                mock.patch.object(FlowEngine, "_consent_recheck") as recheck, \
                mock.patch.object(consent, "automatic") as automatic:
            self.assertTrue(eng._advance_planning(card, st))
        self.assertEqual((recheck.call_count, automatic.call_count, count.loads), (0, 0, []))
        self.assertEqual(eng.journal.read_complete(card), before)
        # Po wznowieniu ta sama prośba jest oceniana jak dziś: automatyczna zgoda z zapamiętanej rodziny.
        eng.journal.append(card, "card.resumed")
        with mock.patch.object(eng, "_start_outside"):
            self.assertTrue(eng._advance_planning(card, eng.journal.state(card)))
        decisions = [e for e in eng.journal.read_complete(card) if e["type"] == "step.decision"]
        self.assertEqual([(d["step"], d["choice"], d.get("auto")) for d in decisions], [("plan-1", "run", True)])
