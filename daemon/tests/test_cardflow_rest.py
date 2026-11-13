"""K-105 PT-412: karty w spoczynku poza pełnym przebiegiem karty w ticku (`cardflow/rest.py`).

Rejestr czynników wykluczających i jedna funkcja spoczynku (`rest.at_rest`): każdy czynnik z kartki ticketu z osobna
wyklucza spoczynek zakończonej karty, czynnik z wyjątkiem też, a czynnik dopisany w teście działa bez zmiany funkcji.
Kolejki (`heavyqueue`, `stepqueue` — obie są w bazie ticketu) przez nazwany punkt wejścia `has_queued_operation`:
operacja kolejki innej karty wyklucza spoczynek, błąd odczytu kolejki też.

Karty są prawdziwymi plikami Tablicy (`BoardStore`), dzienniki prawdziwym `Journal`, silnik prawdziwym `FlowEngine`
(bez procesów kroków, jak `EngineOnJournal` z PT-150)."""
import os
import types
import unittest
from unittest import mock

from tide_daemon.board.readcontext import BoardRead
from tide_daemon.cardflow import caretaker, mergecheck, rest

from .test_cardflow_caretaker_retry import EngineOnJournal


class RestCase(EngineOnJournal):
    def finished(self, title="Zakończona", *, column="done"):
        """Zakończona karta bez pracy: `card.started` i `card.finished` w dzienniku, karta w Gotowe na Tablicy."""
        card = self.engine.store.create_card({"title": title})["id"]
        self.add(card, "card.started", entry="now", executor="Sonnet", provider="anthropic", column="todo")
        self.add(card, "card.finished", column=column)
        self.engine.store.move_card(card, column)
        return card

    def active(self, title="Aktywna"):
        card = self.engine.store.create_card({"title": title})["id"]
        self.add(card, "card.started", entry="now", executor="Sonnet", provider="anthropic", column="todo")
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


if __name__ == "__main__":
    unittest.main()
