"""K-105 PT-415: historia procesów i stan procesu karty pamiętane między tickami według wersji dziennika (PT-411).

Granice liczenia: model tożsamości procesów (`processidentity.process_identities` wołany przez `fold` i przez kontrolę
`read_process_history`) oraz złożenie stanu procesu karty (`trialhold._checked_state` — `fold` z kontrolą tożsamości);
pełne ścisłe odczyty dziennika jak w PT-411 (`Journal._read_strict`). Bramki przed skutkiem (`fresh=True`) czytają
świeżo mimo trafienia w pamięć; odczyty bez skutku korzystają z pamięci."""
import collections
import contextlib
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tide_daemon.cardflow import boxstep, processidentity, state as state_module, trialhold
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.journal import HistoryChanged, Journal
from tide_daemon.cardflow.state import fold

from .boxflow import BoxFlow, SLUG
from .test_cardflow_tick_reads import TickFixture, _external_event, _lines


class Counts:
    """Wywołania modelu tożsamości i złożenia stanu procesu karty (według karty) oraz pełne ścisłe odczyty."""

    def __init__(self, journal):
        self.journal = journal
        self.identities, self.states, self.reads = [], collections.Counter(), []

    @contextlib.contextmanager
    def __call__(self):
        real_identities, real_checked = processidentity.process_identities, trialhold._checked_state
        real_full = self.journal._read_strict

        def identities(events):
            self.identities.append(events[0].get("card") if events else None)
            return real_identities(events)

        def checked(events):
            self.states[events[0].get("card") if events else None] += 1
            return real_checked(events)

        def full(card):
            self.reads.append(card)
            return real_full(card)
        with mock.patch.object(state_module, "process_identities", side_effect=identities), \
                mock.patch.object(trialhold, "process_identities", side_effect=identities), \
                mock.patch.object(trialhold, "_checked_state", side_effect=checked), \
                mock.patch.object(self.journal, "_read_strict", side_effect=full):
            yield self


STEP = dict(step="out-1", ticket="PT-8", kind="ticket", executor="Sonnet", provider="anthropic", model="sonnet",
            attempt=0, parentStep="", stepDir="/wt/PT-8.step", worktree="/wt/PT-8", branch="ticket/pt-8")


def _same_state(test, a, b):
    test.assertEqual(vars(a), vars(b))


class ProcessMemoryCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        os.makedirs(os.path.join(temp.name, "board"))
        self.journal = Journal(os.path.join(temp.name, "board"), mirror=os.path.join(temp.name, "mirror"))
        for card in ("K-1", "K-2"):
            self.journal.append(card, "card.started", project="proj")
            self.journal.append(card, "card.paused")

    def mirror(self, card="K-1"):
        return self.journal.mirror_path(card)

    def text(self, card="K-1"):
        with open(self.mirror(card), encoding="utf-8") as fh:
            return fh.read()

    def external_append(self, event, card="K-1"):
        with open(self.mirror(card), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def without_memory(self, card="K-1"):
        """Wynik bez pamięci: świeży pełny odczyt i nowe złożenie."""
        return fold(self.journal.read_fresh(card))


class RememberedProcessStateTest(ProcessMemoryCase):
    def test_unchanged_journal_needs_no_identity_model_nor_state_fold(self):
        for card in ("K-1", "K-2"):
            trialhold.process_state(self.journal, card)
        with Counts(self.journal)() as count:
            for card in ("K-1", "K-2"):
                trialhold.read_process_history(self.journal, card)
                trialhold.process_state(self.journal, card)
        self.assertEqual((count.identities, dict(count.states), count.reads), ([], {}, []))

    def test_append_to_one_card_recomputes_only_that_card_and_equals_the_result_without_memory(self):
        for card in ("K-1", "K-2"):
            trialhold.process_state(self.journal, card)
        events = self.journal.read_complete("K-1")
        self.external_append(_external_event(events, "K-1"))
        with Counts(self.journal)() as count:
            first = {card: trialhold.process_state(self.journal, card) for card in ("K-1", "K-2")}
        self.assertEqual(dict(count.states), {"K-1": 1})
        self.assertEqual(count.identities, ["K-1"], "jeden model tożsamości (w fold) dla zmienionej karty")
        for card in ("K-1", "K-2"):
            _same_state(self, first[card][1], self.without_memory(card))
        self.assertEqual(first["K-1"][0][-1]["key"], "zewnetrzny")

    def test_own_write_recomputes(self):
        trialhold.process_state(self.journal, "K-1")
        self.journal.append("K-1", "card.resumed")
        self.assertNotIn("K-1", self.journal._derived)
        with Counts(self.journal)() as count:
            _, st = trialhold.process_state(self.journal, "K-1")
        self.assertEqual(dict(count.states), {"K-1": 1})
        self.assertFalse(st.paused)

    def test_same_size_in_place_change_with_restored_mtime_recomputes(self):
        trialhold.process_state(self.journal, "K-1")
        text = self.text()
        st = os.stat(self.mirror())
        time.sleep(0.02)                 # zgrubny zegar jądra: ctime zmiany różni się od ctime pierwszego odczytu
        with open(self.mirror(), "r+", encoding="utf-8") as fh:
            fh.write(text.replace('"proj"', '"prok"'))
        os.utime(self.mirror(), ns=(st.st_atime_ns, st.st_mtime_ns))
        after = os.stat(self.mirror())
        self.assertEqual((after.st_size, after.st_mtime_ns, after.st_ino), (st.st_size, st.st_mtime_ns, st.st_ino))
        self.assertNotEqual(after.st_ctime_ns, st.st_ctime_ns)
        with Counts(self.journal)() as count:
            _, state = trialhold.process_state(self.journal, "K-1")
        self.assertEqual(dict(count.states), {"K-1": 1})
        self.assertEqual(count.reads, ["K-1"], "zmiana samego ctime: pełny odczyt i nowe złożenie")
        self.assertEqual(state.started["project"], "prok")
        _same_state(self, state, self.without_memory())

    def test_unknown_identity_is_an_error_on_every_call_and_never_remembered(self):
        events = self.journal.read_complete("K-1")
        # Zakończenie outside bez startu przechodzi ścisły odczyt, a model tożsamości daje unknown.
        self.external_append({"v": 1, "seq": len(events) + 1, "ts": "2026-11-12T12:00:00+01:00", "card": "K-1",
                              "type": "outside.finished", "step": "K-1-PT-1-1", "ticket": "PT-1", "exit": 0})
        for _ in range(3):
            with Counts(self.journal)() as count:
                with self.assertRaises(ValueError):
                    trialhold.process_state(self.journal, "K-1")
                with self.assertRaises(ValueError):
                    trialhold.read_process_history(self.journal, "K-1")
            self.assertEqual(count.states["K-1"], 2, "błąd kontroli nie jest pamiętany jako historia")
            self.assertNotIn("K-1", self.journal._derived)

    def test_read_or_stat_error_is_not_remembered_and_the_next_call_reads_in_full(self):
        trialhold.process_state(self.journal, "K-1")
        for failing in ("_strict_open", "fstat"):
            with self.subTest(failing=failing):
                if failing == "fstat":
                    patch = mock.patch("tide_daemon.cardflow.journal.os.fstat", side_effect=OSError("stat"))
                else:
                    patch = mock.patch.object(self.journal, "_strict_open", side_effect=OSError("odczyt"))
                with patch, self.assertRaises(OSError):
                    trialhold.process_state(self.journal, "K-1")
                self.assertNotIn("K-1", self.journal._derived)
                self.assertNotIn("K-1", self.journal._strict_memory)
                with Counts(self.journal)() as count:
                    _, st = trialhold.process_state(self.journal, "K-1")
                self.assertEqual((count.reads, dict(count.states)), (["K-1"], {"K-1": 1}))
                _same_state(self, st, self.without_memory())

    def test_returned_state_is_a_copy_with_own_field_containers(self):
        self.journal.append("K-1", "step.started", **STEP)
        events, first = trialhold.process_state(self.journal, "K-1")
        entry = self.journal._strict_memory["K-1"]
        self.assertTrue(all(a is b for a, b in zip(events, entry.events)), "te same obiekty zdarzeń co w PT-411")
        self.assertEqual(len(events), len(entry.events))
        _, second = trialhold.process_state(self.journal, "K-1")
        self.assertIsNot(first, second)
        for name, value in vars(first).items():
            if isinstance(value, (dict, list, set)):
                self.assertIsNot(value, getattr(second, name), name)
        self.assertIs(first.steps["out-1"], second.steps["out-1"], "wartości zagnieżdżone są wspólne (Journal.state)")
        self.assertIs(first.process_identities["out-1"], second.process_identities["out-1"])
        # Silnik zmienia pola i kontenery kopii — kolejny odczyt tego nie widzi.
        first.paused = False
        first.steps["obcy"] = {"step": "obcy"}
        first.intents.clear()
        first.rejected.add("PT-9")
        _, third = trialhold.process_state(self.journal, "K-1")
        _same_state(self, third, self.without_memory())
        self.assertTrue(third.paused)

    def test_memory_holds_no_copy_of_the_events(self):
        trialhold.process_state(self.journal, "K-1")
        entry, version, st = self.journal._derived["K-1"]
        self.assertIs(entry, self.journal._strict_memory["K-1"])
        self.assertEqual(version, entry.version)
        self.assertNotIsInstance(st, (list, tuple))

    def test_fresh_variant_reads_in_full_and_bypasses_the_memory(self):
        trialhold.process_state(self.journal, "K-1")
        before = self.journal._derived["K-1"]
        with Counts(self.journal)() as count:
            trialhold.process_state(self.journal, "K-1", fresh=True)
            trialhold.read_process_history(self.journal, "K-1", fresh=True)
        self.assertEqual(count.reads, ["K-1", "K-1"])
        self.assertIs(self.journal._derived["K-1"], before, "świeży odczyt ani nie czyta, ani nie zapisuje pamięci")

    def test_forget_except_drops_derived_entries(self):
        for card in ("K-1", "K-2"):
            trialhold.process_state(self.journal, card)
        self.journal.forget_except(["K-2"])
        self.assertEqual(set(self.journal._derived), {"K-2"})

    def test_a_replaced_read_never_reuses_a_state_of_other_events(self):
        """Zdarzenia nie z pamięci ścisłej (np. podmieniony odczyt) nigdy nie trafiają w zapamiętany stan."""
        trialhold.process_state(self.journal, "K-1")
        other = self.journal.read_fresh("K-1")[:1]
        with mock.patch.object(self.journal, "read_complete", return_value=other):
            _, st = trialhold.process_state(self.journal, "K-1")
        self.assertFalse(st.paused)

    def test_phase_is_a_view_over_the_memory_and_keeps_its_error(self):
        trialhold.process_state(self.journal, "K-1")
        phase = trialhold.HistoryPhase(self.journal)
        with Counts(self.journal)() as count:
            events, st = trialhold.process_state(self.journal, "K-1", phase)
            trialhold.read_process_history(self.journal, "K-1", phase)
        self.assertEqual((count.reads, dict(count.states)), ([], {}))
        self.assertTrue(all(a is b for a, b in zip(events, self.journal._strict_memory["K-1"].events)))
        _same_state(self, st, self.without_memory())
        broken = trialhold.HistoryPhase(self.journal)
        with mock.patch.object(self.journal, "_strict_open", side_effect=OSError("odczyt")):
            self.journal._strict_memory.pop("K-1")             # pamięć K-1 nie zasłania wstrzykniętego błędu
            for _ in range(2):
                with self.assertRaises(OSError):
                    trialhold.process_state(self.journal, "K-1", broken)
        self.assertNotIn("K-1", self.journal._derived)
        with self.assertRaises(OSError):                       # zapamiętany błąd fazy rzucany w obrębie fazy
            trialhold.read_process_history(self.journal, "K-1", broken)
        trialhold.read_process_history(self.journal, "K-1")    # poza fazą błąd nie jest pamięcią


class ProcessMemoryTickTest(TickFixture):
    def other_card(self):
        self.journal.append("K-900", "card.started", project="proj")

    def test_second_tick_without_changes_computes_no_identity_model_nor_card_state(self):
        self.other_card()
        self.engine.tick()
        with Counts(self.journal)() as count:
            self.engine.tick()
        self.assertEqual(count.identities, [], "model tożsamości procesów w ticku bez zmian")
        self.assertEqual(dict(count.states), {}, "złożenie stanu procesu karty w ticku bez zmian")
        self.assertEqual(count.reads, [])

    def test_append_to_one_card_recomputes_only_that_card_in_the_next_tick(self):
        self.other_card()
        self.engine.tick()
        self.engine.tick()
        events = self.journal.read_complete(self.card_id)
        with open(self.journal.mirror_path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write(_lines([_external_event(events, self.card_id)]))
        with Counts(self.journal)() as count:
            self.engine.tick()
        self.assertEqual(set(count.states), {self.card_id}, count.states)
        _, st = trialhold.process_state(self.journal, self.card_id)
        self.assertEqual(vars(st), vars(fold(self.journal.read_fresh(self.card_id))))

    def test_unknown_identity_blocks_every_tick_while_the_history_is_broken(self):
        events = self.journal.read_complete(self.card_id)
        broken = dict(next(e for e in events if e["type"] == "step.started"))
        broken.pop("ticket", None)
        broken.update(seq=len(events) + 1, unit="")
        for path in (self.journal.path(self.card_id), self.journal.mirror_path(self.card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_lines(events + [broken]))
        self.restarted()
        for _ in range(3):
            self.engine._warnings.pop("journal", None)
            with mock.patch.object(self.engine, "_recover_started_steps") as recover:
                self.engine.tick()
            recover.assert_not_called()
            self.assertIn("odzyskiwanie wstrzymane", self.engine._warnings.get("journal", ""))
            self.assertNotIn(self.card_id, self.journal._derived)

    def test_recovery_from_the_phase_is_refused_after_an_external_append_despite_a_memory_hit(self):
        self.lose_step_started()
        journal = self.restarted().journal
        card = self.card_id
        st = journal.state(card)
        trialhold.process_state(journal, card)               # rozgrzana pamięć
        phase = trialhold.HistoryPhase(journal)
        events = trialhold.read_process_history(journal, card, phase)
        with open(journal.mirror_path(card), "a", encoding="utf-8") as fh:
            fh.write(_lines([_external_event(events, card)]))
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
        with Counts(journal)() as count, mock.patch.object(journal, "unchanged", guard):
            self.engine._recover_started_steps(card, st, history=phase)
        self.assertEqual(dict(count.states), {}, "decyzja z fazy nad pamięcią (bez nowego złożenia)")
        self.assertEqual(refused, [card])
        self.assertEqual(self.started_events(), [])

    def queue_outside(self):
        card, append = self.card_id, self.journal.append
        self.command = "make test-changed BASE=1"
        append(card, "step.started", **STEP)
        append(card, "step.finished", step="out-1", ticket="PT-8", result="denied", outside=True,
               command=self.command, canAllow=True)
        append(card, "step.decision", step="out-1", ticket="PT-8", choice="run")
        return self.journal.state(card).steps["out-1"]

    def test_outside_gate_with_step_and_command_reads_fresh_despite_a_memory_hit(self):
        step = self.queue_outside()
        self.engine._outside_history(self.card_id)
        with Counts(self.journal)() as count:
            self.engine._outside_history(self.card_id)
            self.engine._outside_history(self.card_id, step, fresh=False)
        self.assertEqual((count.reads, dict(count.states)), ([], {}), "bariera ticku i obserwacja z pamięci")
        with Counts(self.journal)() as count:
            self.engine._outside_history(self.card_id, step)
            self.engine._outside_history(self.card_id, step, command=self.command)
        self.assertEqual(count.reads, [self.card_id, self.card_id], "bramka zgody i startu outside czyta świeżo")
        # Zmiana dziennika po ticku (zewnętrzny start tego outside): bramka startu odmawia jak dotąd.
        events = self.journal.read_complete(self.card_id)
        self.engine._outside_history(self.card_id, step, fresh=False)
        with open(self.journal.mirror_path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write(_lines([{"v": 1, "seq": len(events) + 1, "ts": "2026-11-12T12:00:00+01:00",
                              "card": self.card_id, "type": "outside.started", "step": "out-1", "ticket": "PT-8",
                              "command": self.command, "unit": "u", "log": "/nonexistent/log",
                              "exitFile": "/nonexistent/in/exit-code", "worktree": "/wt/PT-8", "head": ""}]))
        with self.assertRaises(FlowError):
            self.engine._outside_history(self.card_id, step, command=self.command)

    def test_process_check_reads_fresh_and_sees_an_external_append_after_the_tick(self):
        self.engine.tick()
        step = self.journal.state(self.card_id).steps[self.step["step"]]
        with Counts(self.journal)() as count:
            self.engine._process_check(self.card_id, step)
        self.assertEqual(count.reads, [self.card_id], "bramka przed startem procesu: świeży odczyt")
        events = self.journal.read_complete(self.card_id)
        broken = dict(next(e for e in events if e["type"] == "step.started"))
        broken.update(seq=len(events) + 1, ticket="PT-999")         # niespójna tożsamość tego samego kroku
        with open(self.journal.mirror_path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write(_lines([broken]))
        with self.assertRaises(FlowError):
            self.engine._process_check(self.card_id, step)

    def test_inventory_reads_the_memory_and_writes_only_after_a_fresh_read(self):
        self.engine.tick()
        calls = []
        real = trialhold.process_state

        def spy(journal, card, phase=None, *, fresh=False):
            calls.append(fresh)
            return real(journal, card, phase, fresh=fresh)
        with mock.patch.object(trialhold, "process_state", side_effect=spy), Counts(self.journal)() as count:
            trialhold.inventory(self.engine, self.card_id, None)
        self.assertEqual((calls, count.reads), ([False], []), "bez zapisu: tylko pamięć")
        calls.clear()
        appended = []
        handoff = [("K-1-PT-1-9", {"ticket": "PT-1"})]
        with mock.patch.object(trialhold, "process_state", side_effect=spy), \
                mock.patch.object(trialhold, "_inventory_items", return_value=([], handoff)), \
                mock.patch.object(self.journal, "append", side_effect=lambda *a, **k: appended.append(a[:2])):
            trialhold.inventory(self.engine, self.card_id, None)
        self.assertEqual(calls, [False, True], "zapis inwentaryzacji dopiero po świeżym odczycie")
        self.assertEqual(appended, [(self.card_id, "k38.handoff")])

    HEAVY = "make test-changed BASE=1"

    def merge_check(self):
        """Próba testów po scaleniu z zgodą, czekająca na start (PT-329)."""
        from tide_daemon.cardflow import mergecheck
        card, append, sha = self.card_id, self.journal.append, "b" * 40
        check = "PT-7:1"
        name = mergecheck.step_name(check)
        append(card, mergecheck.ARMED)
        append(card, mergecheck.REQUESTED, ticket="PT-7", check=check, command=self.HEAVY, after=sha, start=sha,
               before=sha)
        append(card, mergecheck.STEP, step=name, ticket="PT-7", kind=mergecheck.KIND, check=check,
               command=self.HEAVY, after=sha, worktree="/wt/card", stepDir="/wt/card.step")
        append(card, "step.decision", step=name, ticket="PT-7", choice="run")
        st = self.journal.state(card)
        entry = next(e for e in mergecheck.gate(st) if mergecheck.step_name(e["check"]) == name)
        return name, self.engine._merge_check_step(card, entry), entry, sha

    @contextlib.contextmanager
    def merge_check_launch(self, sha):
        """K-109 PT-483: start próby przez `Runner.run_command` (atrapa runnera zamiast Popen)."""
        from tide_daemon.cardflow import heavyqueue
        from tide_daemon.tasks import runner as runner_mod

        def run(request):
            files = runner_mod.command_files(request.folder, request.attempt)
            return runner_mod.CommandHandle(request.attempt, files.unit, files.log, files.exit_file, ("true",))
        runner = mock.Mock()
        runner.run_command.side_effect = run
        with mock.patch.object(self.engine, "_merge_check_head", return_value=sha), \
                mock.patch.object(self.engine, "runner", runner, create=True), \
                mock.patch.object(self.engine, "_load_reader", create=True,
                                  return_value=lambda: heavyqueue.Load(0.5, 8)):
            yield runner.run_command

    def test_merge_check_start_reads_fresh_under_the_lock_before_popen(self):
        name, step, entry, sha = self.merge_check()
        self.engine._merge_check_history(self.card_id)          # rozgrzana pamięć (obserwacja czekania)
        with self.merge_check_launch(sha) as popen, Counts(self.journal)() as count:
            self.engine._merge_check_start(self.card_id, step, entry)
        popen.assert_called_once()
        self.assertEqual(count.reads[0], self.card_id, "start próby po świeżym pełnym odczycie")
        self.assertIn(name, self.journal.state(self.card_id).outside_launched)
        kinds = [e["type"] for e in self.journal.read(self.card_id) if e.get("step") == name and
                 e["type"].startswith("command.")]
        self.assertEqual(kinds, ["command.intent", "command.started"])

    def test_merge_check_start_is_refused_after_an_external_outside_start_despite_a_memory_hit(self):
        name, step, entry, sha = self.merge_check()
        events = self.engine._merge_check_history(self.card_id)  # tick: odczyt z pamięci
        with open(self.journal.mirror_path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write(_lines([{"v": 1, "seq": len(events) + 1, "ts": "2026-11-12T12:00:00+01:00",
                              "card": self.card_id, "type": "outside.started", "step": name, "ticket": "PT-7",
                              "command": self.HEAVY, "unit": "u", "log": "/nonexistent/log",
                              "exitFile": "/nonexistent/in/exit-code", "worktree": "/wt/card", "head": sha}]))
        # Nawet gdy tolerancyjna projekcja przepuściłaby start, decyduje świeży ścisły odczyt pod blokadą.
        with self.merge_check_launch(sha) as popen, \
                mock.patch.object(self.engine, "_merge_check_still_allowed", return_value=True) as allowed, \
                self.assertRaises(FlowError):
            self.engine._merge_check_start(self.card_id, step, entry)
        popen.assert_not_called()
        allowed.assert_not_called()

    def test_outside_unit_is_not_stopped_when_the_fresh_confirmation_fails(self):
        from tide_daemon.tasks import stepstop
        started = {"unit": "u-old", "ts": "2020-01-01T00:00:00+00:00", "log": "/nonexistent/log",
                   "exitFile": "/nonexistent/in/exit-code"}
        with mock.patch.object(stepstop, "_systemctl") as systemctl:
            self.assertIsNone(self.engine._outside_collect(self.card_id, "out-x", started, confirm=lambda: False))
        systemctl.assert_not_called()
        with mock.patch.object(stepstop, "_systemctl") as systemctl:
            code, _ = self.engine._outside_collect(self.card_id, "out-x", started, confirm=lambda: True)
        self.assertEqual(code, "przekroczony czas")
        systemctl.assert_any_call("stop", "u-old.scope")


class ProcessMemoryBoxTickTest(BoxFlow):
    """MAJOR 1 review PT-415: przy `box.enabled=true` cykl życia pudełka (`box/lifecycle.tick`) bierze stan procesu
    karty z pamięci — rozgrzany tick bez zmian nie woła modelu tożsamości ani złożenia stanu dla żadnej karty."""

    PROFILE = {"tide-box": 1, "ports": {"pool": [49123, 49132], "env": ["APP_PORT"]}}

    def setUp(self):
        super().setUp()
        self.flow.stop()
        self.card_id = self.card()
        self.engine.journal.append(self.card_id, "card.started", project=SLUG)
        self.build_box(self.PROFILE)

    def test_second_tick_with_the_box_enabled_computes_no_identity_model_nor_card_state(self):
        from tide_daemon.box import lifecycle
        journal = self.engine.journal
        self.assertTrue(boxstep.settings_of(self.engine).enabled)
        self.engine.tick()
        seen = []
        real_state = trialhold.process_state

        def spy(journal_, card, phase=None, *, fresh=False):
            seen.append((card, fresh))
            return real_state(journal_, card, phase, fresh=fresh)
        real_tick = lifecycle.tick
        ticks = []
        with Counts(journal)() as count, mock.patch.object(trialhold, "process_state", side_effect=spy), \
                mock.patch.object(lifecycle, "tick", side_effect=lambda engine: ticks.append(1) or real_tick(engine)):
            self.engine.tick()
        self.assertEqual(ticks, [1], f"tick nie doszedł do cyklu życia pudełka: {self.engine._warnings}")
        self.assertIn((self.card_id, False), seen, "cykl życia pudełka bierze stan procesu karty z pamięci")
        self.assertEqual(count.identities, [], "model tożsamości procesów w ticku bez zmian")
        self.assertEqual(dict(count.states), {}, "złożenie stanu procesu karty w ticku bez zmian")


if __name__ == "__main__":
    unittest.main()
