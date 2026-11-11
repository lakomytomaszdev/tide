"""K-98 #3 (PT-344): tick i odzyskiwanie bez powielania odczytów historii i Tablicy.

- jedna niezmienna faza odczytu historii na tick (`trialhold.HistoryPhase`): rozpoznanie po restarcie,
  `_recover_started_steps` i `boxstep.recover_network` dzielą jeden pełny ścisły odczyt karty; zapis tego procesu
  unieważnia odczyt karty, a każda mutacja z fazy robi osobny świeży pełny odczyt i odmawia przy zmianie,
- jeden kontekst odczytu Tablicy (`BoardRead` z #1) na tick; zapis Tablicy go unieważnia, a ścieżka zapisu czyta świeżo.

Testy liczą odczyty na granicy dziennika (`Journal.read_complete`) i magazynu Tablicy (`BoardStore.load`); czasu nie
mierzą. Procesy kroków są prawdziwe (atrapy z flowfakes.py), tak jak w RestartInProcessTest."""
import collections
import contextlib
import json
import os
import signal
import tempfile
import unittest
from unittest import mock

from tide_daemon.board.readcontext import BoardRead
from tide_daemon.board.store import BoardStore
from tide_daemon.cardflow import boxstep, trialhold
from tide_daemon.cardflow.engine import FlowDeferred
from tide_daemon.cardflow.journal import HistoryChanged, Journal
from tide_daemon.cardflow.state import fold

from . import flowfakes
from .boxflow import BoxFlow, SLUG
from .privatebus import wait_until
from .test_cardflow import FlowInProcess, pass_merge_checks


def _lines(events):
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def _external_event(events, card):
    """Zdarzenie dopisane spoza procesu usługi (inna kopia usługi, ręczna edycja)."""
    return {"v": 1, "seq": len(events) + 1, "ts": "2026-11-11T12:00:00+01:00", "card": card,
            "type": "flow.notice", "key": "zewnetrzny", "result": "plan"}


class Counting:
    """Liczniki jednego ticku: pełne odczyty historii (karta, faza) i wczytania Tablicy (kontekst / zapis)."""

    def __init__(self, engine):
        self.engine = engine
        self.reads, self.loads = [], []
        self.phase_end = None
        self.in_write = 0

    @contextlib.contextmanager
    def __call__(self):
        journal, store = self.engine.journal, self.engine.store
        real_read, real_load, real_execute = journal.read_complete, store.load, store._execute
        real_network = boxstep.recover_network

        def read(card):
            self.reads.append(card)
            return real_read(card)

        def load(*args, **kwargs):
            self.loads.append("write" if self.in_write else "read")
            return real_load(*args, **kwargs)

        def execute(*args, **kwargs):
            self.in_write += 1
            try:
                return real_execute(*args, **kwargs)
            finally:
                self.in_write -= 1

        def network(*args, **kwargs):
            try:
                return real_network(*args, **kwargs)
            finally:
                if self.phase_end is None:
                    self.phase_end = len(self.reads)
        with mock.patch.object(journal, "read_complete", side_effect=read), \
                mock.patch.object(store, "load", side_effect=load), \
                mock.patch.object(store, "_execute", side_effect=execute), \
                mock.patch.object(boxstep, "recover_network", side_effect=network):
            yield self

    @property
    def phase(self):
        return collections.Counter(self.reads[:self.phase_end])


class TickFixture(FlowInProcess):
    """Karta „Od razu” z żywym krokiem; monitor zatrzymany, ticki wywołuje test (jak usługa po restarcie)."""

    def setUp(self):
        super().setUp()
        self.card_id = self.card(description="Dodaj eksport.\n\nATRAPA: czekaj")
        self.start(self.card_id)
        self.step = self.started(self.card_id)
        self.pid = self.unit_pid(self.step)
        self.addCleanup(self.kill)
        self.wait_column(self.card_id, "progress")
        self.flow.stop()
        self.engine = self.flow.engine
        self.journal = self.engine.journal

    def kill(self):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.pid, signal.SIGKILL)

    def restarted(self):
        """Nowy silnik na tej samej Tablicy, bez monitora i bez procesów w pamięci."""
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.models import executors
        engine = FlowEngine(self.store, claude_projects=os.path.join(self.base, "home", ".claude", "projects"),
                            systemd_run=self.fakes["systemd-run"], catalog=executors.default_catalog,
                            tmp_root=os.path.join(self.base, "tmp"), settings=self.settings,
                            which=lambda name: self.fakes.get(name), grace=5.0)
        pass_merge_checks(engine)
        self.engine, self.journal = engine, engine.journal
        return engine

    def lose_step_started(self):
        """Awaria między wynikiem startu jednostki a `step.started` (jak test_restart_after_launch_result_…)."""
        events = self.journal.read_complete(self.card_id)
        cut = max(i for i, e in enumerate(events) if e["type"] == "step.started")
        for path in (self.journal.path(self.card_id), self.journal.mirror_path(self.card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_lines(events[:cut]))

    def started_events(self):
        return [e for e in self.journal.read_complete(self.card_id) if e["type"] == "step.started"]


class HistoryPhaseTickTest(TickFixture):
    def test_recovery_phase_reads_each_card_history_once(self):
        self.engine.tick()                       # rozgrzany stan: pamięci dziennika i projekcji
        with Counting(self.engine)() as count:
            self.engine.tick()
        self.assertIsNotNone(count.phase_end, "tick nie doszedł do recover_network")
        cards = self.journal.cards_complete()
        self.assertEqual(dict(count.phase), {card: 1 for card in cards},
                         "faza odzyskiwania czyta komplet historii karty więcej niż raz")

    def test_every_recover_started_steps_call_of_a_tick_shares_the_phase_read(self):
        self.engine.tick()
        calls, reads_inside = [], []
        real_recover, real_read = self.engine._recover_started_steps, self.journal.read_complete
        inside = []

        def recover(*args, **kwargs):
            calls.append(kwargs.get("history"))
            inside.append(1)
            try:
                return real_recover(*args, **kwargs)
            finally:
                inside.pop()

        def read(card):
            if inside:
                reads_inside.append(card)
            return real_read(card)
        with mock.patch.object(self.engine, "_recover_started_steps", side_effect=recover), \
                mock.patch.object(self.journal, "read_complete", side_effect=read):
            self.engine.tick()
        self.assertEqual(len(calls), 2, "pętla recovery i _tick_card")
        self.assertTrue(all(h is not None and h is calls[0] for h in calls), "jedna faza ticku w obu wywołaniach")
        self.assertEqual(reads_inside, [], "bez zapisu historia karty nie jest czytana ponownie")

    def test_restart_recovers_one_owner_with_separate_fresh_reads_before_and_after_the_write(self):
        self.lose_step_started()
        self.restarted()
        with Counting(self.engine)() as count:
            self.engine.tick()
        started = self.started_events()
        self.assertEqual(len(started), 1, started)
        self.assertEqual(started[0]["unit"], self.step["unit"])
        self.assertTrue(flowfakes.alive(self.pid), "ten sam proces żyje; recovery nie startuje drugiego")
        self.assertEqual(flowfakes.unit_pid(self.runtime, self.step["unit"]), self.pid)
        # Faza: 1 odczyt karty; zapis odtworzonego startu: świeży odczyt porównania + odczyt zapisu;
        # recover_network po własnym zapisie czyta kartę ponownie (osobny odczyt po zapisie).
        self.assertEqual(count.phase[self.card_id], 4, count.reads)

    def test_change_or_truncation_between_read_and_write_refuses_without_effect_when_warm(self):
        events = self.journal.read_complete(self.card_id)
        crashed = events[:max(i for i, e in enumerate(events) if e["type"] == "step.started")]
        for source in ("mirror", "board"):
            for change in ("append", "truncate"):
                with self.subTest(source=source, change=change):
                    self.check_refusal(crashed, source, change)

    def check_refusal(self, crashed, source, change):
        card = self.card_id
        for path in (self.journal.path(card), self.journal.mirror_path(card)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_lines(crashed))
        journal = self.restarted().journal
        if source == "board":
            os.unlink(journal.mirror_path(card))      # bez kopii usługi autorytatywny jest dziennik w Tablicy
        path = journal.mirror_path(card) if source == "mirror" else journal.path(card)
        # Rozgrzany stan: pamięć tolerancyjnego odczytu, projekcja i odczyt fazy.
        journal.read(card)
        st = journal.state(card)
        phase = trialhold.HistoryPhase(journal)
        events = trialhold.read_process_history(journal, card, phase)
        self.assertTrue(any(e["type"] == "result" and e.get("action") == "step-start" for e in events))
        changed = events + [_external_event(events, card)] if change == "append" else events[:-1]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_lines(changed))
        with open(path, encoding="utf-8") as fh:
            written = fh.read()
        refused = []
        real_guard = journal.unchanged

        @contextlib.contextmanager
        def guard(*args):
            try:
                with real_guard(*args):
                    yield
            except HistoryChanged:
                refused.append(args[0])
                raise
        with mock.patch.object(journal, "unchanged", guard):
            self.engine._recover_started_steps(card, st, history=phase)
        self.assertEqual(refused, [card], "decyzja z fazy trafiła na świeży odczyt i została odmówiona")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), written, "odmowa bez skutku: dziennik niezmieniony")
        self.assertEqual(self.started_events(), [])
        # Kolejny tick czyta od nowa i decyduje na aktualnej historii.
        if change == "append":
            self.engine._recover_started_steps(card, journal.state(card), history=trialhold.HistoryPhase(journal))
            self.assertEqual(len(self.started_events()), 1)

    def test_incomplete_identity_keeps_the_tick_blocked_and_visible(self):
        events = self.journal.read_complete(self.card_id)
        broken = dict(next(e for e in events if e["type"] == "step.started"))
        broken.pop("ticket", None)
        broken.update(seq=len(events) + 1, unit="")
        for path in (self.journal.path(self.card_id), self.journal.mirror_path(self.card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_lines(events + [broken]))
        self.restarted()
        with mock.patch.object(self.engine, "_recover_started_steps") as recover, \
                mock.patch.object(boxstep, "recover_network") as network:
            self.engine.tick()
            self.engine.tick()
        recover.assert_not_called()
        network.assert_not_called()
        self.assertIn("odzyskiwanie wstrzymane", self.engine._warnings.get("journal", ""))
        self.assertTrue(flowfakes.alive(self.pid), "blokada nie kończy procesu bez dowodu")


class BoardTickContextTest(TickFixture):
    def test_warm_tick_loads_the_board_once(self):
        self.engine.tick()
        with Counting(self.engine)() as count:
            self.engine.tick()
        self.assertEqual(count.loads, ["read"], "tick bez zapisu Tablicy wczytuje ją najwyżej raz")

    def test_board_write_in_the_tick_invalidates_the_context_and_counts_its_reads_separately(self):
        self.engine.tick()
        item = self.store.load().cards[int(self.card_id.partition("-")[2])]
        with open(item.path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("base-commit:", text)
        with open(item.path, "w", encoding="utf-8") as fh:     # pole bazy zniknęło z pliku karty
            fh.write("".join(line for line in text.splitlines(keepends=True) if not line.startswith("base-commit:")))
        with Counting(self.engine)() as count:
            self.engine.tick()
        with open(item.path, encoding="utf-8") as fh:
            self.assertIn("base-commit:", fh.read(), "projekcja bazy odtworzyła pole")
        # Kontekst ticku (1), transakcja zapisu ze świeżym odczytem (2: plan i świeży plan), ponowne wczytanie
        # kontekstu po zapisie (1).
        self.assertEqual(collections.Counter(count.loads), {"read": 2, "write": 2}, count.loads)
        with Counting(self.engine)() as count:
            self.engine.tick()
        self.assertEqual(count.loads, ["read"])

    def test_user_move_detected_from_the_context_is_recorded_once(self):
        self.engine.tick()
        self.store.move_card(self.card_id, "backlog", 0, "progress")
        self.engine.tick()
        self.assertTrue(wait_until(lambda: self.of(self.card_id, "card.paused"), timeout=5), self.events(self.card_id))
        self.assertEqual(len(self.of(self.card_id, "card.moved")), 1)
        self.assertTrue(flowfakes.alive(self.pid))


class BoxNetworkPhaseTest(BoxFlow):
    PROFILE = {"tide-box": 1, "ports": {"pool": [49123, 49132], "env": ["APP_PORT"]}}

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.card_id = self.card()
        self.engine.journal.append(self.card_id, "card.started", project=SLUG)
        self.build_box(self.PROFILE)

    def place(self, step):
        return self.engine._box_place(self.card_id, mock.Mock(slug=SLUG), mock.Mock(provider="openai"),
                                      "PT-1", "ticket", "", step)

    def test_orphan_reservation_is_kept_when_history_changes_after_the_phase_read(self):
        journal = self.engine.journal
        self.place("first")                     # rezerwacja bez zamiaru startu: recovery ją zwalnia
        phase = trialhold.HistoryPhase(journal)
        events = trialhold.read_process_history(journal, self.card_id, phase)
        with open(journal.mirror_path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write(_lines([_external_event(events, self.card_id)]))
        with mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(self.engine, history=phase)
        with self.assertRaises(FlowDeferred):
            self.place("second")                # zmiana historii po odczycie: zwolnienie odmówione
        # Kolejny tick (nowa faza) czyta aktualną historię i zwalnia rezerwację.
        with mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(self.engine, history=trialhold.HistoryPhase(journal))
        self.assertEqual(self.place("second").port_env["PORT"], "49123")

    def test_recovery_phase_shares_one_read_with_the_network(self):
        journal = self.engine.journal
        self.place("first")
        phase = trialhold.HistoryPhase(journal)
        trialhold.read_process_history(journal, self.card_id, phase)
        reads = []
        real = journal.read_complete
        with mock.patch.object(journal, "read_complete", side_effect=lambda card: reads.append(card) or real(card)), \
                mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(self.engine, history=phase)
        # Bez osobnego odczytu fazy; jedyny odczyt to świeży odczyt przed zwolnieniem rezerwacji.
        self.assertEqual(reads, [self.card_id])
        self.assertEqual(self.place("second").port_env["PORT"], "49123")


class HistoryPhaseUnitTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        os.makedirs(os.path.join(temp.name, "board"))
        self.journal = Journal(os.path.join(temp.name, "board"), mirror=os.path.join(temp.name, "mirror"))
        self.journal.append("K-1", "card.started", project="proj")
        self.reads = []
        real = self.journal.read_complete
        patcher = mock.patch.object(self.journal, "read_complete",
                                    side_effect=lambda card: self.reads.append(card) or real(card))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_read_per_phase_until_this_process_writes_the_card(self):
        phase = trialhold.HistoryPhase(self.journal)
        first = trialhold.read_process_history(self.journal, "K-1", phase)
        self.assertEqual(trialhold.read_process_history(self.journal, "K-1", phase), first)
        self.assertEqual(self.reads, ["K-1"])
        self.journal.append("K-1", "card.paused")
        self.reads.clear()
        after = trialhold.read_process_history(self.journal, "K-1", phase)
        self.assertEqual([e["type"] for e in after], ["card.started", "card.paused"])
        self.assertEqual(self.reads, ["K-1"], "po własnym zapisie faza czyta kartę ponownie (osobny odczyt)")

    def test_unknown_history_stays_unknown_within_the_phase(self):
        with open(self.journal.mirror_path("K-1"), "a", encoding="utf-8") as fh:
            fh.write('{"v": 1, "seq": 2, "card": "K-1", "type": "step.started", "step": "x"')   # urwany zapis
        phase = trialhold.HistoryPhase(self.journal)
        for _ in range(2):
            with self.assertRaises(ValueError):
                trialhold.read_process_history(self.journal, "K-1", phase)
        self.assertEqual(self.reads, ["K-1"])
        with self.assertRaises(ValueError):    # bez fazy: zawsze świeży ścisły odczyt
            trialhold.read_process_history(self.journal, "K-1")
        self.assertEqual(self.reads, ["K-1", "K-1"])

    def test_journal_without_generation_is_never_cached(self):
        fake = mock.Mock(spec=["read_complete"])
        fake.read_complete.return_value = []
        phase = trialhold.HistoryPhase(fake)
        trialhold.read_process_history(fake, "K-1", phase)
        trialhold.read_process_history(fake, "K-1", phase)
        self.assertEqual(fake.read_complete.call_count, 2)

    def test_unchanged_guard_refuses_append_and_truncation_without_writing(self):
        basis = self.journal.read_complete("K-1")
        self.journal.append("K-1", "card.paused")          # zmiana po odczycie decyzji
        with open(self.journal.mirror_path("K-1"), encoding="utf-8") as fh:
            before = fh.read()
        with self.assertRaises(HistoryChanged):
            with self.journal.unchanged("K-1", basis):
                self.journal.append_complete("K-1", "card.resumed")
        with open(self.journal.mirror_path("K-1"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)
        current = self.journal.read_complete("K-1")
        with open(self.journal.mirror_path("K-1"), "w", encoding="utf-8") as fh:
            fh.write(_lines(current[:1]))                   # ucięcie kopii
        self.assertFalse(trialhold.HistoryPhase(self.journal).unchanged("K-1", current))
        with self.assertRaises(HistoryChanged):
            with self.journal.unchanged("K-1", current):
                self.journal.append_complete("K-1", "card.resumed")
        with self.journal.unchanged("K-1", current[:1]):
            self.journal.append_complete("K-1", "card.resumed")
        self.assertEqual([e["type"] for e in self.journal.read_complete("K-1")], ["card.started", "card.resumed"])

    def test_read_complete_stays_a_fresh_strict_read(self):
        phase = trialhold.HistoryPhase(self.journal)
        trialhold.read_process_history(self.journal, "K-1", phase)
        self.reads.clear()
        self.journal.read_complete("K-1")
        self.journal.read_complete("K-1")
        self.assertEqual(self.reads, ["K-1", "K-1"])
        with open(self.journal.mirror_path("K-1"), "a", encoding="utf-8") as fh:
            fh.write('{"v": 1, "seq": 9, "card": "K-1", "type": "card.paused"}\n')     # luka w numeracji
        with self.assertRaises(ValueError):
            self.journal.read_complete("K-1")


class BoardReadInvalidationTest(unittest.TestCase):
    def test_store_write_invalidates_the_context(self):
        with tempfile.TemporaryDirectory() as root:
            store = BoardStore(root)
            loads = []
            real = store.load
            with mock.patch.object(store, "load", side_effect=lambda: loads.append(1) or real()):
                read = BoardRead(store)
                read.board
                read.board
                self.assertEqual(len(loads), 1)
                read.once("k", lambda: "pamiec")
                store._write("notatka.md", "x\n")
                self.assertEqual(read.once("k", lambda: "nowa"), "nowa", "zapis czyści wartości pomocnicze")
                read.board
                self.assertEqual(len(loads), 2, "zapis Tablicy unieważnia kontekst")
                read.board
                self.assertEqual(len(loads), 2)


if __name__ == "__main__":
    unittest.main()
