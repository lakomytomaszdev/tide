"""K-105 PT-412: karty w spoczynku poza pełnym przebiegiem karty w ticku (`cardflow/rest.py`).

Rejestr czynników wykluczających i jedna funkcja spoczynku (`rest.at_rest`): każdy czynnik z kartki ticketu z osobna
wyklucza spoczynek zakończonej karty, czynnik z wyjątkiem też, a czynnik dopisany w teście działa bez zmiany funkcji.
Kolejki (`heavyqueue`, `stepqueue` — obie są w bazie ticketu) przez nazwany punkt wejścia `has_queued_operation`:
operacja kolejki innej karty wyklucza spoczynek, błąd odczytu kolejki też.

Karty są prawdziwymi plikami Tablicy (`BoardStore`), dzienniki prawdziwym `Journal`, silnik prawdziwym `FlowEngine`
(bez procesów kroków, jak `EngineOnJournal` z PT-150)."""
import contextlib
import json
import os
import subprocess
import types
import unittest
from unittest import mock

from tide_daemon.board.readcontext import BoardRead
from tide_daemon.cardflow import caretaker, mergecheck, rest

from .test_cardflow_caretaker_retry import EngineOnJournal


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    if done.returncode:
        raise AssertionError(done.stderr)
    return done.stdout


class RestCase(EngineOnJournal):
    """Projekt `proj` na Tablicy z repozytorium git; karty projektu."""

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "start")
        self.engine.store.upsert_project({"slug": "proj", "repo": self.repo})

    def card(self, title):
        return self.engine.store.create_card({"title": title, "project": "proj"})["id"]

    def finished(self, title="Zakończona", *, column="done"):
        """Zakończona karta bez pracy: `card.started` i `card.finished` w dzienniku, karta w Gotowe na Tablicy."""
        card = self.card(title)
        self.add(card, "card.started", entry="now", executor="Sonnet", provider="anthropic", column="todo")
        self.add(card, "card.finished", column=column)
        self.engine.store.move_card(card, column)
        return card

    def active(self, title="Aktywna"):
        card = self.card(title)
        self.add(card, "card.started", entry="now", executor="Sonnet", provider="anthropic", column="todo")
        self.engine.store.move_card(card, "todo")
        return card

    def verdict(self, card, factors=None):
        probe = rest.Probe(self.engine, card, self.journal.state(card), BoardRead(self.engine.store))
        return rest.at_rest(probe, factors)

    def step(self, card, name, ticket="PT-1", kind="ticket", **fields):
        self.add(card, "step.started", step=name, ticket=ticket, kind=kind, provider="anthropic", attempt=0, **fields)


class RestFunctionTest(RestCase):
    def test_finished_card_without_work_is_at_rest(self):
        card = self.finished()
        self.assertEqual(self.verdict(card), rest.Verdict(True))

    def test_every_factor_of_the_ticket_alone_excludes_a_finished_card(self):
        """Kartka PT-412: czynniki z osobna — każdy scenariusz na nowej zakończonej karcie bez innej pracy."""
        def unfinished(card):
            self.add(card, "card.started", entry="now", executor="Sonnet", column="todo")

        def column(card):
            self.engine.store.move_card(card, "todo")

        def running(card):
            self.step(card, f"{card}-run")

        def console(card):
            self.step(card, f"{card}-con", mode="interactive")
            self.add(card, "step.finished", step=f"{card}-con", ticket="PT-1", result="done")

        def intent(card):
            self.add(card, "intent", action="cleanup", key="PT-1", repo="/r", branch="b", worktree="/w")

        def background(card):
            self.add(card, "intent", action="youtrack-note", key="PT-1", youtrack=True, payload={})

        def held(card):
            self.step(card, f"{card}-old", ticket="PT-173")
            self.add(card, "step.finished", step=f"{card}-old", ticket="PT-173", result="done")

        def main_merge(card):
            self.add(card, "main-merge.waiting", key="m-1", reason="czeka na czysty worktree głównej")

        def base(card):
            self.add(card, "card.base-requested", decision="base-1", baseRef="refs/heads/x", baseCommit="a" * 40)

        def outside(card):
            self.step(card, f"{card}-out")
            self.add(card, "step.finished", step=f"{card}-out", ticket="PT-1", result="denied", outside=True,
                     command="make test")
            self.add(card, "step.decision", step=f"{card}-out", ticket="PT-1", choice="run")

        def outside_live(card):
            self.engine._outside_waiting[f"{card}-live"] = (card, "proj", 0.0, 0.0, True)

        def merge_check(card):
            self.add(card, mergecheck.ARMED)
            self.add(card, mergecheck.REQUESTED, ticket="PT-1", check="PT-1:1", command="make test",
                     after="b" * 40, start="b" * 40, before="b" * 40)

        def caretaker_run(card):
            self.add(card, caretaker.STARTED, problem="stalled:card", key="caretaker:stalled:card:1", n=1,
                     step=f"{card}-care-1", target="", root="", ticket="", trigger="stalled", triggers=["stalled"],
                     decision="", watermark=1)

        def stage_error(card):
            self.add(card, "flow.error", error="FlowError: x", stage="close", step="", ticket="", at=1.0)

        def decision(card):
            self.step(card, f"{card}-q")
            self.add(card, "step.finished", step=f"{card}-q", ticket="PT-1", result="question", question="?")
            self.add(card, "step.notice", step=f"{card}-q", ticket="PT-1", result="question")

        def review_decision(card):
            self.add(card, "review.question", ticket="PT-1", round=3, question="Ponowić?")

        def plan(card):
            self.add(card, "plan.proposed", rows=[], step="", sourceKind="planning")

        def verdict(card):
            self.step(card, f"{card}-r", kind="review", round=1)
            self.add(card, "step.finished", step=f"{card}-r", ticket="PT-1", result="done")

        def rejection(card):
            self.add(card, "review.decision", ticket="PT-1", round=1, choice="reject")

        def notice(card):
            self.engine._notices.append({"card": card, "step": "x", "ticket": "", "result": "error", "detail": ""})

        def notice_due(card):
            # Błąd kroku po ponowieniu bez `step.notice` i bez decyzji (próg powiadomienia `_queue_notices`).
            self.add(card, "step.started", step=f"{card}-e", ticket="PT-1", kind="ticket", provider="anthropic",
                     attempt=1)
            self.add(card, "step.finished", step=f"{card}-e", ticket="PT-1", result="error", reason="x")

        def queue_of_another_card(card):
            other = self.active("Inna karta z poleceniem w toku")
            self.step(other, f"{other}-o")
            self.add(other, "step.finished", step=f"{other}-o", ticket="PT-1", result="denied", outside=True,
                     command="make test")
            self.add(other, "step.decision", step=f"{other}-o", ticket="PT-1", choice="run")
            self.add(other, "outside.started", step=f"{other}-o", ticket="PT-1", command="make test", unit="u",
                     log="/nonexistent/o.log", exitFile="/nonexistent/exit", worktree="/w", head="")

        def step_queue_of_another_card(card):
            other = self.active("Inna karta czekająca u dostawcy")
            self.add(other, "step.waiting", ticket="PT-2", kind="ticket", provider="anthropic", cause="slots",
                     reason="czeka na miejsce", since="2026-11-13T10:00:00+01:00")

        cases = [
            ("card-unfinished", unfinished), ("card-unfinished", column), ("steps", running), ("steps", console),
            ("intents", intent), ("intents", background), ("held", held), ("main-merge", main_merge),
            ("base", base), ("outside", outside), ("outside", outside_live), ("outside", merge_check),
            ("caretaker", caretaker_run), ("stage-errors", stage_error), ("decisions", decision),
            ("decisions", review_decision), ("plan", plan), ("reviews", verdict), ("reviews", rejection),
            ("notices", notice), ("notices", notice_due), ("queues", queue_of_another_card),
            ("queues", step_queue_of_another_card),
        ]
        for expected, make in cases:
            with self.subTest(factor=expected, scenario=make.__name__):
                self.engine._outside_waiting.clear()
                self.engine._notices.clear()
                card = self.finished(make.__name__)
                self.assertTrue(self.verdict(card).rest, "punkt wyjścia: karta w spoczynku")
                make(card)
                found = self.verdict(card)
                self.assertFalse(found.rest, make.__name__)
                self.assertEqual(found.factor, expected, found)
                self.assertEqual(found.error, "")
                for c in self.journal.cards():     # następny scenariusz zaczyna bez cudzych operacji kolejki
                    st = self.journal.state(c)
                    for name in st.outside_started:
                        self.add(c, "outside.finished", step=name, ticket="PT-1", exit=0, tail="")
                    if st.started is not None and not st.card_finished:
                        self.add(c, "card.finished", column="done")
                        self.engine.store.move_card(c, "done")

    def test_tracker_note_waits_only_in_a_youtrack_project(self):
        card = self.finished()
        self.add(card, "result", action="merge", key="PT-1", ok=True)
        youtrack = types.SimpleNamespace(youtrack=True)
        plain = types.SimpleNamespace(youtrack=False)
        with mock.patch.object(self.engine, "_project_read", return_value=(None, plain)):
            self.assertTrue(self.verdict(card).rest)
        with mock.patch.object(self.engine, "_project_read", return_value=(None, youtrack)):
            self.assertEqual(self.verdict(card).factor, "tracker")
        self.add(card, "result", action="youtrack-note", key="PT-1", ok=True)
        with mock.patch.object(self.engine, "_project_read", return_value=(None, youtrack)):
            self.assertTrue(self.verdict(card).rest)

    def test_k38_card_runs_the_public_inventory_and_never_rests(self):
        factor = next(f for f in rest.FACTORS if f.name == "k38-public")
        probe = rest.Probe(self.engine, "K-38", self.journal.state(self.finished()), BoardRead(self.engine.store))
        self.assertEqual(rest.at_rest(probe, [factor]), rest.Verdict(False, "k38-public"))

    def test_card_missing_from_the_board_is_not_at_rest(self):
        card = self.finished()
        self.engine.store.delete_card(card) if hasattr(self.engine.store, "delete_card") else \
            os.unlink(self.engine.store.load().cards[int(card[2:])].path)
        self.assertEqual(self.verdict(card).factor, "card-unfinished")

    def test_factor_raising_or_failing_to_read_excludes_rest(self):
        card = self.finished()
        for error in (OSError("odczyt"), ValueError("nieznana historia"), RuntimeError("błąd programu")):
            with self.subTest(error=error):
                def broken(_probe, error=error):
                    raise error
                factors = [*rest.FACTORS, rest.Factor("zepsuty", "test", broken)]
                found = self.verdict(card, factors)
                self.assertEqual((found.rest, found.factor), (False, "zepsuty"))
                self.assertIn(str(error), found.error)

    def test_board_read_error_excludes_rest(self):
        card = self.finished()
        read = mock.Mock()
        type(read).board = mock.PropertyMock(side_effect=OSError("Tablica nieczytelna"))
        found = rest.at_rest(rest.Probe(self.engine, card, self.journal.state(card), read))
        self.assertEqual((found.rest, found.factor), (False, "card-unfinished"))
        self.assertIn("Tablica nieczytelna", found.error)


class RegistryContractTest(RestCase):
    """Punkt podpięcia K-107/K-108/K-109/K-39: czynnik dopisany do rejestru wyklucza spoczynek bez zmiany `at_rest`."""

    def test_factor_registered_in_the_test_excludes_rest_without_changing_the_function(self):
        card = self.finished()
        self.assertTrue(self.verdict(card).rest)
        work = {"pending": True}
        before = list(rest.FACTORS)
        self.addCleanup(lambda: rest.FACTORS.__setitem__(slice(None), before))
        rest.register("atrapa-nowej-pracy", "atrapa: nowa praca karty czeka", lambda probe: work["pending"])
        self.assertEqual(self.verdict(card), rest.Verdict(False, "atrapa-nowej-pracy"))
        work["pending"] = False
        self.assertTrue(self.verdict(card).rest)
        with self.assertRaises(ValueError):
            rest.register("atrapa-nowej-pracy", "drugi raz", lambda probe: True)

    def test_registry_names_are_unique_and_documented(self):
        names = [f.name for f in rest.FACTORS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[0], "card-unfinished")
        with open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "tide-daemon.md"), encoding="utf-8") as fh:
            doc = fh.read()
        for factor in rest.FACTORS:
            self.assertIn(f"`{factor.name}`", doc, f"czynnik {factor.name} nie jest na liście w dokumentacji usługi")
        for queue in rest.QUEUES:
            self.assertIn(f"`{queue.name}`", doc)


class QueueEntryPointTest(RestCase):
    """Operacje kolejek (poprawka po audycie 12.11): obie kolejki są w bazie ticketu, więc test idzie na prawdziwych
    kolejkach; kolejka testowa sprawdza tylko punkt podpięcia następnych kolejek."""

    def test_both_queues_of_the_base_are_wired_to_the_entry_point(self):
        self.assertEqual([q.name for q in rest.QUEUES], ["heavyqueue", "stepqueue"])

    def test_heavy_queue_operation_of_another_card_excludes_rest_while_running_and_waiting(self):
        card = self.finished()
        other = self.active("Inna karta")
        self.step(other, "o-1")
        self.add(other, "step.finished", step="o-1", ticket="PT-1", result="denied", outside=True, command="make test")
        self.add(other, "step.decision", step="o-1", ticket="PT-1", choice="run")
        self.assertEqual(self.verdict(card).factor, "queues", "czekająca zgoda w kolejce")
        self.add(other, "outside.started", step="o-1", ticket="PT-1", command="make test", unit="u",
                 log="/nonexistent/o.log", exitFile="/nonexistent/exit", worktree="/w", head="")
        self.assertEqual(self.verdict(card).factor, "queues", "trwające polecenie")
        self.add(other, "outside.finished", step="o-1", ticket="PT-1", exit=0, tail="")
        self.add(other, "step.started", step="o-2", ticket="PT-1", kind="ticket", provider="anthropic",
                 parentStep="o-1", attempt=0)
        self.add(other, "step.finished", step="o-2", ticket="PT-1", result="done")
        self.assertTrue(self.verdict(card).rest, self.verdict(card))

    def test_live_engine_queue_entry_of_another_card_excludes_rest(self):
        card = self.finished()
        self.engine._outside_active = {"x-1": ("K-99", "unit")}
        self.assertEqual(self.verdict(card).factor, "queues")
        self.engine._outside_active = {}
        self.assertTrue(self.verdict(card).rest)

    def test_step_queue_entry_of_another_card_excludes_rest(self):
        card = self.finished()
        other = self.active("Inna karta")
        self.add(other, "step.waiting", ticket="PT-2", kind="ticket", provider="anthropic", cause="quota",
                 reason="limit tygodniowy", since="2026-11-13T10:00:00+01:00")
        self.assertEqual(self.verdict(card).factor, "queues")
        self.add(other, "card.finished", column="done")
        self.engine.store.move_card(other, "done")
        self.assertTrue(self.verdict(card).rest)

    def test_queue_read_error_excludes_rest(self):
        card = self.finished()
        for name in ("heavyqueue", "stepqueue"):
            with self.subTest(queue=name):
                target = {"heavyqueue": "operations", "stepqueue": "order"}[name]
                module = rest.queueview if name == "heavyqueue" else rest.stepqueue
                with mock.patch.object(module, target, side_effect=OSError("kolejka nie odpowiada")):
                    found = self.verdict(card)
                self.assertEqual((found.rest, found.factor), (False, "queues"))
                self.assertIn("kolejka nie odpowiada", found.error)

    def test_test_queue_registered_at_the_entry_point_excludes_rest(self):
        card = self.finished()
        before = list(rest.QUEUES)
        self.addCleanup(lambda: rest.QUEUES.__setitem__(slice(None), before))
        ops = []
        rest.register_queue("kolejka-testowa", lambda engine, states: list(ops))
        self.assertTrue(self.verdict(card).rest)
        ops.append(("kolejka-testowa", "K-77", "op"))
        self.assertEqual(self.verdict(card).factor, "queues")


class TickCase(RestCase):
    """Cały tick silnika w procesie; licznik na granicy przebiegu karty (`FlowEngine._tick_card`)."""

    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        self.journal.monotonic = lambda: self.now[0]      # zegar okresu kontroli pełnej (wstrzykiwalny)

    @contextlib.contextmanager
    def passes(self):
        """Karty z pełnym przebiegiem w ticku (lista w kolejności)."""
        seen = []
        real = self.engine._tick_card

        def counted(card, *args, **kwargs):
            seen.append(card)
            return real(card, *args, **kwargs)
        with mock.patch.object(self.engine, "_tick_card", side_effect=counted):
            yield seen

    def tick(self):
        with self.passes() as seen:
            self.engine.tick()
        return seen


class TickRestTest(TickCase):
    def setUp(self):
        super().setUp()
        self.done = self.finished("Zakończona")
        self.live = self.active("Aktywna")

    def test_finished_card_without_work_skips_the_full_pass_from_the_second_tick(self):
        self.assertEqual(self.tick(), [self.done, self.live], "pierwszy tick silnika: pełny przebieg każdej karty")
        for _ in range(3):
            self.assertEqual(self.tick(), [self.live])

    def test_new_journal_event_brings_the_full_pass_back_in_the_next_tick(self):
        self.tick()
        self.assertEqual(self.tick(), [self.live])
        self.add(self.done, "flow.notice", key="z-procesu", result="plan")         # zapis tego procesu
        self.assertEqual(self.tick(), [self.done, self.live])
        self.assertEqual(self.tick(), [self.live])
        events = self.journal.read_fresh(self.done)
        with open(self.journal.mirror_path(self.done), "a", encoding="utf-8") as fh:      # zapis spoza procesu
            fh.write(json.dumps({"v": 1, "seq": len(events) + 1, "ts": "2026-11-13T12:00:00+01:00",
                                 "card": self.done, "type": "flow.notice", "key": "zewnetrzny",
                                 "result": "plan"}) + "\n")
        self.assertEqual(self.tick(), [self.done, self.live])
        self.assertEqual(self.tick(), [self.live])

    def test_manual_move_on_the_board_is_detected_in_the_next_tick_and_handled_by_the_full_pass(self):
        self.tick()
        self.assertEqual(self.tick(), [self.live])
        self.engine.store.move_card(self.done, "archived")       # ruch użytkownika bez zdarzenia w dzienniku
        self.assertEqual(self.tick(), [self.done, self.live])
        self.assertEqual(self.tick(), [self.live], "Archiwum: nadal zakończona, znowu w spoczynku")
        self.engine.store.move_card(self.done, "todo")
        self.assertEqual(self.tick(), [self.done, self.live])
        self.assertEqual(self.tick(), [self.done, self.live], "poza Gotowe/Archiwum karta nie jest w spoczynku")

    def test_first_tick_of_a_new_engine_and_the_full_check_period_run_every_card_in_full(self):
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.models import executors
        self.tick()
        self.assertEqual(self.tick(), [self.live])
        self.now[0] += rest.PERIOD - 1
        self.assertEqual(self.tick(), [self.live])
        self.now[0] += 1
        self.assertEqual(self.tick(), [self.done, self.live], "upływ okresu kontroli pełnej")
        self.assertEqual(self.tick(), [self.live])
        restarted = FlowEngine(self.engine.store, self.journal, catalog=executors.default_catalog,
                               settings=self.engine.settings, tmp_root=self.base)
        self.engine = restarted
        self.assertEqual(self.tick(), [self.done, self.live], "pierwszy tick po restarcie usługi")
        self.assertEqual(self.tick(), [self.live])

    def test_failure_of_one_full_pass_does_not_stop_the_others_and_brings_its_full_pass_back(self):
        other = self.finished("Druga zakończona")
        real = self.engine._tick_card

        def failing(card, *args, **kwargs):
            if card == other:
                raise RuntimeError("błąd przebiegu karty")
            return real(card, *args, **kwargs)
        with mock.patch.object(self.engine, "_tick_card", side_effect=failing):
            with self.passes() as seen:
                self.engine.tick()
        self.assertEqual(seen, [self.done, self.live, other], "pozostałe karty przeszły")
        self.assertEqual(self.tick(), [self.live, other], "karta z błędem: pełny przebieg w następnym ticku")
        self.assertEqual(self.tick(), [self.live])
        with mock.patch.object(self.engine, "_queue_notices", side_effect=RuntimeError("powiadomienie")):
            self.now[0] += rest.PERIOD
            self.assertEqual(self.tick(), [self.done, self.live, other])
        self.assertEqual(self.tick(), [self.done, self.live, other], "błąd po `_tick_card` też przywraca przebieg")
        self.assertEqual(self.tick(), [self.live])

    def test_queue_operation_of_another_card_excludes_rest_and_rest_returns_after_a_full_pass(self):
        self.tick()
        self.assertEqual(self.tick(), [self.live])
        self.add(self.live, "step.waiting", ticket="PT-2", kind="ticket", provider="anthropic", cause="quota",
                 reason="limit tygodniowy", since="2026-11-13T10:00:00+01:00")
        self.assertEqual(self.tick(), [self.done, self.live], "operacja kolejki kroków innej karty")
        self.assertEqual(self.tick(), [self.done, self.live])
        self.add(self.live, "card.paused")        # wpis wypada z kolejki
        self.assertEqual(self.tick(), [self.done, self.live], "po zakończeniu operacji: najpierw pełny przebieg")
        self.assertEqual(self.tick(), [self.live])
        with mock.patch.object(rest.stepqueue, "order", side_effect=OSError("kolejka nie odpowiada")):
            self.assertEqual(self.tick(), [self.done, self.live], "błąd odczytu kolejki")

    def test_factor_excluding_rest_brings_the_full_pass_back(self):
        self.tick()
        self.assertEqual(self.tick(), [self.live])
        work = {"pending": True}
        before = list(rest.FACTORS)
        self.addCleanup(lambda: rest.FACTORS.__setitem__(slice(None), before))
        rest.register("atrapa-nowej-pracy", "atrapa", lambda probe: work["pending"] and probe.card == self.done)
        self.assertEqual(self.tick(), [self.done, self.live])
        work["pending"] = False
        self.assertEqual(self.tick(), [self.done, self.live])
        self.assertEqual(self.tick(), [self.live])

    def test_revert_of_the_board_journal_of_a_resting_card_is_restored_with_a_warning_in_the_next_tick(self):
        self.tick()
        self.assertEqual(self.tick(), [self.live])
        board, mirror = self.journal.path(self.done), self.journal.mirror_path(self.done)
        with open(board, encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        with open(board, "w", encoding="utf-8") as fh:        # revert w repozytorium Tablicy
            fh.write("".join(lines[:1]))
        self.tick()
        self.assertIn("odtworzony z kopii", self.engine._warnings.get(self.done, ""))
        with open(board, encoding="utf-8") as fh, open(mirror, encoding="utf-8") as mh:
            self.assertEqual(fh.read(), mh.read())

    def test_second_tick_with_resting_cards_opens_no_journal_file_for_content(self):
        """Granica dziennika: ścisły odczyt (pełny i przyrost — `_pread_all`), tolerancyjny odczyt (`_read`),
        kontrola zgodności (`Journal._check_read`) i własny odczyt pętli odtwarzania (`startorder.events`)."""
        from tide_daemon.cardflow import journal as journal_module, startorder
        self.add(self.live, "card.finished", column="done")
        self.engine.store.move_card(self.live, "done")
        self.finished("Trzecia")
        self.tick()
        reads = []

        def counting(name, real):
            return lambda *args, **kwargs: reads.append((name, args[:1])) or real(*args, **kwargs)
        with mock.patch.object(journal_module, "_pread_all", side_effect=counting("strict", journal_module._pread_all)), \
                mock.patch.object(journal_module, "_read", side_effect=counting("tolerant", journal_module._read)), \
                mock.patch.object(self.journal, "_check_read", side_effect=counting("check", self.journal._check_read)), \
                mock.patch.object(startorder, "events", side_effect=counting("startorder", startorder.events)):
            seen = self.tick()
        self.assertEqual(seen, [], "wszystkie karty w spoczynku")
        self.assertEqual(reads, [], "drugi tick otworzył treść dziennika")


class GuardAndBusyTest(TickCase):
    def test_worktree_guard_and_main_branch_work_for_a_repo_whose_cards_all_rest(self):
        repo = self.repo
        tree = os.path.join(self.base, "wt")
        git(repo, "worktree", "add", "-q", "-b", "ticket/a", tree)
        card = self.card("Zakończona z repozytorium")
        self.add(card, "card.started", entry="now", executor="Sonnet", provider="anthropic", column="todo", repo=repo)
        self.add(card, "card.finished", column="done")
        self.engine.store.move_card(card, "done")
        self.assertEqual(self.tick(), [card])
        self.engine._main_branches.clear()
        self.assertEqual(self.tick(), [], "karta repozytorium w spoczynku")
        self.assertEqual(self.engine._main_branches.get(repo), "master")
        admin = os.path.join(repo, ".git", "worktrees", "wt")
        os.unlink(os.path.join(admin, "HEAD"))
        os.unlink(os.path.join(admin, "gitdir"))
        self.assertEqual(self.tick(), [])
        self.assertTrue(os.path.isfile(os.path.join(admin, "HEAD")), "strażnik odtworzył wpis worktree")
        self.assertIn(tree, git(repo, "worktree", "list"))

    def test_busy_worktrees_and_hidden_sessions_are_the_same_as_without_rest(self):
        done = self.finished("Zakończona z krokami")
        self.step(done, "d-1", session="sesja-d", worktree=os.path.join(self.base, "wt-d"))
        self.add(done, "step.finished", step="d-1", ticket="PT-1", result="done")
        self.add(done, "card.finished", column="done")
        live = self.active("Aktywna z krokiem")
        self.step(live, "l-1", session="sesja-l", worktree=os.path.join(self.base, "wt-l"))
        console = self.active("Aktywna z Konsolą")
        self.step(console, "c-1", session="sesja-c", worktree=os.path.join(self.base, "wt-c"), mode="interactive")
        self.add(console, "step.finished", step="c-1", ticket="PT-1", result="done")
        self.tick()
        self.assertEqual(self.tick(), [live, console], "zakończona karta w spoczynku")
        with_rest = (self.engine._busy, self.engine._hidden)
        with mock.patch.object(rest.TickRest, "resting", return_value=False):
            self.assertEqual(self.tick(), [done, live, console])
        self.assertEqual((self.engine._busy, self.engine._hidden), with_rest)
        self.assertIn("sesja-d", with_rest[1][0])
        self.assertIn(os.path.realpath(os.path.join(self.base, "wt-d")), with_rest[1][1])


if __name__ == "__main__":
    unittest.main()
