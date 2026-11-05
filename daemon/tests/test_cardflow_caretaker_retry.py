"""Opiekun ponawia krok z instrukcją (K-37, PT-150): akcja `retry` w obu ścieżkach (z decyzją i bez), wspólna
ścieżka akcji (para „zamiar → wynik” `caretaker-action` i `caretaker.action`), limit działań, nieaktualne akcje
(`stale`), restart i pierwszeństwo slotu kroku opiekuna.

Najpierw czyste funkcje `caretaker.py` na złożonych dziennikach (`stale`, `owned`, limit, historia), potem
`FlowEngine`: kolejność slotów na złożonych dziennikach i przebiegi z atrapą `claude` (`ATRAPA-OPIEKUN: retry`,
`retry-obcy`, `retry-powoli` w powodzie błędu kroku ticketu).
"""
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker, decisions, history
from tide_daemon.cardflow.state import derived, fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker import Journal

PROBLEM = "error:K-1-PT-7-1"
REPEAT = "repeat:PT-7:exit"


def repeat_trigger(st):
    """Atrapa wyzwalacza bez decyzji (jak `repeat:` z #6): te same kroki co `error`, ale bez `decision`."""
    return [{"trigger": "repeat", "step": c["step"], "ticket": c["ticket"], "cause": "exit"}
            for c in caretaker._error_trigger(st)]


def chain(j):
    """PT-7: pierwsza próba i automatyczne ponowienie, obie z błędem — problem `error:K-1-PT-7-1`."""
    j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
    return j


def care(j, step="K-1-care-1", n=1, problem=PROBLEM, decision="step:K-1-PT-7-2", target="K-1-PT-7-2"):
    return j.add(caretaker.STARTED, problem=problem, key=f"caretaker:{problem}:{n}", n=n, step=step, target=target,
                 root="K-1-PT-7-1", ticket="PT-7", trigger=problem.split(":")[0], triggers=[problem.split(":")[0]],
                 decision=decision, watermark=j.events[-1]["seq"])


def acted(j, step, ok=True, error="", reason="Format daty.", code="", stale=False, target="K-1-PT-7-2"):
    return j.add(caretaker.ACTION, problem=PROBLEM, step=step, target=target, ticket="PT-7", action="retry",
                 params={"step": target, "instruction": "Popraw datę.", "reason": reason}, ok=ok, error=error,
                 code=code, label="PT-7", retried=target, stale=stale)


class RetryResultTest(unittest.TestCase):
    problem = {"id": PROBLEM, "trigger": "error", "step": "K-1-PT-7-2", "options": []}

    def test_retry_needs_step_instruction_and_reason(self):
        base = {"result": "done", "action": "retry", "step": "K-1-PT-7-2", "instruction": "Popraw datę.",
                "reason": "Test pada na dacie."}
        made = caretaker.parse_result(base, self.problem, Journal().st)
        self.assertEqual(made, {"ok": True, "action": "retry", "params": {
            "step": "K-1-PT-7-2", "instruction": "Popraw datę.", "reason": "Test pada na dacie."}})
        for field in ("step", "instruction", "reason"):
            with self.subTest(field=field):
                made = caretaker.parse_result({**base, field: ""}, self.problem, Journal().st)
                self.assertEqual(made["code"], "bad-caretaker-result")
        self.assertTrue(caretaker.parse_result({**base, "instruction": "x" * 4000}, self.problem, Journal().st)["ok"])
        self.assertFalse(caretaker.parse_result({**base, "instruction": "x" * 4001}, self.problem, Journal().st)["ok"])
        # Krok spoza problemu nie jest błędem formatu: rozstrzyga go akcja (`not-problem-step`, liczy się do limitu).
        self.assertTrue(caretaker.parse_result({**base, "step": "K-9-obcy"}, self.problem, Journal().st)["ok"])

    def test_brief_lists_the_step_to_retry(self):
        j = chain(Journal())
        problem = caretaker.problems(j.st)[0]
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        for part in ("Krok problemu: K-1-PT-7-2", "Kroki do ponowienia (pole `step` akcji `retry`): K-1-PT-7-2",
                     "`retry`", "`instruction`", "`reason`", "najwyżej 3 razy"):
            self.assertIn(part, text)

    def test_problem_steps_with_and_without_a_decision(self):
        j = chain(Journal())
        j.step("K-1-PT-8-1", ticket="PT-8")
        j.add("step.finished", step="K-1-PT-8-1", ticket="PT-8", result="error", reason="x")
        st = j.st
        self.assertEqual(caretaker.problem_steps({"decision": "step:K-1-PT-7-2", "step": "K-1-PT-7-2"}, st),
                         ["K-1-PT-7-2"])
        # Bez decyzji: tylko kroki łańcucha bez następcy (pierwsza próba ma następcę, PT-8 to inny łańcuch).
        self.assertEqual(caretaker.problem_steps({"decision": "", "root": "K-1-PT-7-1", "ticket": "PT-7"}, st),
                         ["K-1-PT-7-2"])
        self.assertEqual(caretaker.problem_steps({"decision": "", "root": "", "ticket": ""}, st),
                         ["K-1-PT-7-2", "K-1-PT-8-1"])


class StaleTest(unittest.TestCase):
    def test_with_a_decision_the_users_decision_wins(self):
        j = chain(Journal())
        run = care(j)
        self.assertEqual(caretaker.stale(j.st, run, j.events), "")
        j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", executor="Opus")
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", outcome={"ok": True})
        self.assertEqual(caretaker.stale(j.st, run, j.events), "", "własne zdarzenia opiekuna")
        j.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="retry", answer="")
        self.assertEqual(caretaker.stale(j.st, run, j.events), "użytkownik już zdecydował")

    def test_with_a_decision_another_decision_id_is_stale(self):
        j = chain(Journal())
        run = care(j, decision="step:K-1-PT-7-0")
        self.assertEqual(caretaker.stale(j.st, run, j.events), "decyzja problemu już nie czeka")

    def stale_without_decision(self, *added, problem_gone=False):
        with mock.patch.object(caretaker, "TRIGGERS", [repeat_trigger]):
            j = chain(Journal())
            self.assertEqual([p["id"] for p in caretaker.problems(j.st)], [REPEAT])
            run = care(j, problem=REPEAT, decision="")
            for etype, fields in added:
                j.add(etype, **fields)
            return caretaker.stale(j.st, run, j.events)

    def test_without_a_decision_own_events_are_not_stale(self):
        own = [("step.waiting", {"ticket": "PT-7", "kind": "caretaker", "reason": "limit kroków dostawcy"}),
               ("intent", {"action": "step-start", "key": "K-1-care-1", "kind": "caretaker"}),
               ("result", {"action": "step-start", "key": "K-1-care-1", "ok": True}),
               ("caretaker.guard", {"step": "K-1-care-1", "cause": "start", "wrote": []}),
               ("step.started", {"step": "K-1-care-1", "kind": "caretaker", "ticket": "PT-7"}),
               ("step.activity", {"step": "K-1-care-1"}),
               ("step.finished", {"step": "K-1-care-1", "ticket": "PT-7", "result": "done"}),
               ("caretaker.action", {"problem": "other", "step": "K-1-care-0", "ok": True}),
               ("step.retry", {"step": "K-1-PT-9-1", "ticket": "PT-7", "caretaker": "other"}),
               # Zdarzenia innego łańcucha nie dotyczą problemu.
               ("step.started", {"step": "K-1-PT-8-1", "kind": "ticket", "ticket": "PT-8"}),
               ("step.decision", {"step": "K-1-PT-8-1", "ticket": "PT-8", "choice": "retry"})]
        self.assertEqual(self.stale_without_decision(*own), "")

    def test_without_a_decision_events_from_outside_the_caretaker_are_stale(self):
        cases = {
            "step.started": ("step.started", {"step": "K-1-PT-7-3", "kind": "ticket", "ticket": "PT-7",
                                              "parentStep": "K-1-PT-7-2"}),
            "step.retry": ("step.retry", {"step": "K-1-PT-7-1", "ticket": "PT-7"}),
            "step.decision": ("step.decision", {"step": "K-1-PT-7-1", "ticket": "PT-7", "choice": "retry"}),
            "card.paused": ("card.paused", {}),
            "card.moved": ("card.moved", {"to": "todo", "from": "waiting"}),
            "plan.returned": ("plan.returned", {"feedback": "x"}),
            "plan.edited": ("plan.edited", {}),
        }
        for kind, event in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.stale_without_decision(event), f"po starcie opiekuna: {kind}")

    def test_without_a_decision_a_problem_that_is_gone_is_stale(self):
        found = self.stale_without_decision(("ticket.rejected", {"ticket": "PT-7"}))
        self.assertEqual(found, "problem już nie występuje")


class LimitAndOwnershipTest(unittest.TestCase):
    def three_retries(self):
        j = chain(Journal())
        steps = ["K-1-PT-7-2", "K-1-PT-7-4", "K-1-PT-7-6"]
        for n, target in enumerate(steps, 1):
            care(j, step=f"K-1-care-{n}", n=n, target=target, decision=f"step:{target}")
            j.add("step.started", step=f"K-1-care-{n}", kind="caretaker", ticket="PT-7")
            j.add("step.finished", step=f"K-1-care-{n}", ticket="PT-7", result="done", outcome={"ok": True})
            j.add("step.decision", step=target, ticket="PT-7", choice="retry", answer="Popraw datę.", auto=True,
                  caretaker=PROBLEM)
            acted(j, f"K-1-care-{n}", target=target, reason=f"Powód {n}.")
            j.step(f"K-1-PT-7-{2 * n + 1}", 0, target)
            j.error(f"K-1-PT-7-{2 * n + 1}")
            j.add("step.retry", step=f"K-1-PT-7-{2 * n + 1}", ticket="PT-7")
            j.step(f"K-1-PT-7-{2 * n + 2}", 1, f"K-1-PT-7-{2 * n + 1}")
            j.error(f"K-1-PT-7-{2 * n + 2}")
        return j

    def test_retried_step_fails_again_and_belongs_to_the_same_problem(self):
        j = self.three_retries()
        self.assertEqual([(p["id"], p["step"]) for p in caretaker.problems(j.st)], [(PROBLEM, "K-1-PT-7-8")])
        own = caretaker.owned(j.st, PROBLEM)
        self.assertEqual((own["count"], own["handed"], own["next"]), (3, False, 4))

    def test_limit_escalation_has_three_tried_entries_and_is_handed(self):
        j = self.three_retries()
        j.add(caretaker.ESCALATED, problem=PROBLEM, step="K-1-care-3", target="K-1-PT-7-8", ticket="PT-7",
              action="", summary=caretaker.limit_summary(j.st, PROBLEM, 3), recommendation="", optionId="",
              failed=True, limit=True, count=3, error="wyczerpany limit działań opiekuna (3)", code=caretaker.LIMIT)
        st = j.st
        own = caretaker.owned(st, PROBLEM)
        self.assertEqual((own["count"], own["handed"]), (3, True))
        self.assertNotIn("", st.caretaker_outcomes)
        decision = decisions.build_decisions(st)[0]
        tried = decision["caretaker"]["tried"]
        self.assertEqual([(t["action"], t["ok"], t["reason"]) for t in tried],
                         [("retry", True, "Powód 1."), ("retry", True, "Powód 2."), ("retry", True, "Powód 3.")])
        self.assertEqual(tried[0]["text"], "Opiekun: ponowił PT-7 z instrukcją — Powód 1.")
        self.assertTrue(decision["caretaker"]["failed"])
        self.assertTrue(decision["caretaker"]["summary"].startswith("Opiekun wykonał 3 z 3 dozwolonych działań"))
        self.assertIn("1. retry (Powód 1.): ponowił PT-7 z instrukcją", decision["caretaker"]["summary"])
        self.assertEqual([e["problem"] for e in derived(st, {"column": "todo"})["flow"]["caretaker"]["escalated"]],
                         [PROBLEM])
        texts = [h["text"] for h in history.build(st, []) if h["kind"] == "caretaker"]
        self.assertTrue(texts[-1].startswith("Opiekun: oddaje decyzję po 3 działaniach (limit) — Opiekun wykonał"))

        # Decyzja użytkownika zamyka problem: kolejny błąd tego kroku zaczyna licznik od zera.
        j.add("step.decision", step="K-1-PT-7-8", ticket="PT-7", choice="retry", answer="")
        j.step("K-1-PT-7-9", 0, "K-1-PT-7-8")
        j.error("K-1-PT-7-9")
        j.add("step.retry", step="K-1-PT-7-9", ticket="PT-7")
        j.step("K-1-PT-7-10", 1, "K-1-PT-7-9")
        j.error("K-1-PT-7-10")
        st = j.st
        self.assertEqual([p["id"] for p in caretaker.problems(st)], [PROBLEM])
        own = caretaker.owned(st, PROBLEM)
        self.assertEqual((own["count"], own["handed"], own["next"]), (0, False, 4))
        self.assertEqual(derived(st, {"column": "todo"})["flow"]["caretaker"]["escalated"], [])

    def test_stale_action_is_not_counted_but_a_failed_one_is(self):
        j = chain(Journal())
        care(j, step="K-1-care-1", n=1)
        acted(j, "K-1-care-1", ok=False, error="stale", code="stale", stale=True)
        own = caretaker.owned(j.st, PROBLEM)
        self.assertEqual((own["count"], own["open"], own["handed"], own["next"]), (0, None, False, 2))
        care(j, step="K-1-care-2", n=2)
        acted(j, "K-1-care-2", ok=False, error="not-problem-step: krok K-9 nie należy do problemu",
              code="not-problem-step")
        own = caretaker.owned(j.st, PROBLEM)
        self.assertEqual((own["count"], own["handed"]), (1, False))
        texts = [h["text"] for h in history.build(j.st, []) if h["kind"] == "caretaker"]
        self.assertEqual(texts, [
            "Opiekun: działanie „retry” nieaktualne, nic nie wykonano",
            "Opiekun: nie udało się (ponowienie kroku) — not-problem-step: krok K-9 nie należy do problemu"])

    def test_history_has_the_caretaker_entry_without_a_duplicated_decision(self):
        j = chain(Journal())
        care(j)
        j.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="retry", answer="Popraw datę.", auto=True,
              caretaker=PROBLEM)
        acted(j, "K-1-care-1", reason="Dziennik pokazuje zły format daty.")
        entries = derived(j.st, {"column": "todo"})["flow"]["history"]
        mine = [(h["text"], h["actor"]) for h in entries if h["kind"] in ("caretaker", "decision")]
        self.assertEqual(mine, [("Opiekun: ponowił PT-7 z instrukcją — Dziennik pokazuje zły format daty.",
                                 "caretaker")])
        self.assertFalse([h for h in entries if h["text"].startswith("Decyzja: ponowienie kroku")])


class EngineOnJournal(unittest.TestCase):
    """Prawdziwy `FlowEngine` na złożonych dziennikach w katalogu tymczasowym (bez procesów kroków).
    Zegar dziennika rośnie o sekundę na zdarzenie, więc `ts` porządkuje zdarzenia także między kartami."""

    def setUp(self):
        from tide_daemon.board.store import BoardStore
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.cardflow.journal import Journal as CardJournal
        from tide_daemon.models import executors
        self.base = tempfile.mkdtemp(prefix="tide-pt150-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        settings = os.path.join(self.base, "cardflow.json")
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 1}}, fh)
        store = BoardStore(os.path.join(self.base, "board"))
        journal = CardJournal(store.root, mirror=os.path.join(self.base, "mirror"))
        ticks = iter(range(1_800_000_000, 1_900_000_000))
        journal.clock = lambda: float(next(ticks))
        self.engine = FlowEngine(store, journal, catalog=executors.default_catalog, settings=settings,
                                 tmp_root=self.base)
        self.journal = self.engine.journal

    def add(self, card, etype, **fields):
        return self.journal.append(card, etype, **fields)


class RestartWithParallelDecisionTest(EngineOnJournal):
    """Review M1: restart po zapisie `step.decision` opiekuna, a przed wynikiem `caretaker-action`, gdy w karcie
    czeka też decyzja innego ticketu (PT-8 w `focus`). Dokończenie rozpoznaje własną decyzję i zapisuje sukces."""

    def test_restart_after_the_decision_with_another_waiting_decision_records_success_once(self):
        from tide_daemon.cardflow.engine import _CaretakerPort
        self.add("K-1", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", column="todo")
        for ticket in ("PT-7", "PT-8"):
            first, second = f"K-1-{ticket}-1", f"K-1-{ticket}-2"
            self.add("K-1", "step.started", step=first, kind="ticket", ticket=ticket, attempt=0, provider="anthropic")
            self.add("K-1", "step.finished", step=first, ticket=ticket, result="error", reason="x")
            self.add("K-1", "step.retry", step=first, ticket=ticket)
            self.add("K-1", "step.started", step=second, kind="ticket", ticket=ticket, attempt=1, parentStep=first,
                     provider="anthropic")
            self.add("K-1", "step.finished", step=second, ticket=ticket, result="error", reason="x")
        run = self.add("K-1", caretaker.STARTED, problem=PROBLEM, key=f"caretaker:{PROBLEM}:1", n=1,
                       step="K-1-care-1", target="K-1-PT-7-2", root="K-1-PT-7-1", ticket="PT-7", trigger="error",
                       triggers=["error"], decision="step:K-1-PT-7-2", watermark=11)
        self.add("K-1", "step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", provider="anthropic")
        params = {"step": "K-1-PT-7-2", "instruction": "Popraw datę.", "reason": "Format daty."}
        self.add("K-1", "step.finished", step="K-1-care-1", ticket="PT-7", result="done",
                 outcome={"ok": True, "action": "retry", "params": params})
        self.add("K-1", "intent", action="caretaker-action", key="K-1-care-1", problem=PROBLEM,
                 caretakerAction="retry", params=params)
        self.add("K-1", "step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="retry", answer="Popraw datę.",
                 command="", executor="", auto=True, caretaker=PROBLEM, caretakerStep="K-1-care-1")
        # Przed poprawką: `resolve_step` brał PT-8 jako `focus` i odrzucał PT-7 („nie czeka na decyzję”).
        self.assertEqual(_CaretakerPort(self.engine, "K-1", run).retry({"id": PROBLEM, "decision": "step:K-1-PT-7-2",
                                                                         "step": "K-1-PT-7-2"}, params),
                         {"target": "K-1-PT-7-2", "label": "PT-7"})
        with mock.patch.object(self.engine, "_guard_check", return_value=None), \
                mock.patch.object(self.engine, "_guard_rebase", return_value=None):
            self.engine._caretaker_act("K-1", run, "retry", params)
        events = self.journal.read("K-1")
        actions = [e for e in events if e["type"] == caretaker.ACTION]
        self.assertEqual([(a["ok"], a["error"], a["retried"]) for a in actions], [(True, "", "K-1-PT-7-2")])
        self.assertEqual(len([e for e in events if e["type"] == "step.decision" and e.get("caretaker")]), 1)
        self.assertEqual([(e["key"], e["ok"]) for e in events
                          if e["type"] == "result" and e["action"] == "caretaker-action"], [("K-1-care-1", True)])
        st = fold(events)
        self.assertNotIn("K-1-PT-8-2", st.step_decisions, "decyzja PT-8 nadal czeka na użytkownika")
        self.assertEqual(caretaker.owned(st, PROBLEM)["count"], 1)
        tried = [h["text"] for h in history.build(st, []) if h["kind"] == "caretaker"]
        self.assertEqual(tried, ["Opiekun: ponowił PT-7 z instrukcją — Format daty."])


class SlotPriorityTest(EngineOnJournal):
    """US 37 przez `FlowEngine._admit` na złożonych dziennikach (bez procesów kroków): `providerSlots` = 1,
    slot zajęty krokiem karty K-1; K-2 czeka na slot wcześniej; K-3 ma gotowy krok opiekuna i nowy ticket."""

    def setUp(self):
        super().setUp()
        for card in ("K-1", "K-2", "K-3"):
            self.add(card, "card.started", entry="now", executor="Sonnet", provider="anthropic",
                     preferredProvider="anthropic", column="todo")
        self.add("K-1", "step.started", step="K-1-PT-1-1", kind="ticket", ticket="PT-1", provider="anthropic")
        self.add("K-3", "step.started", step="K-3-PT-3-1", kind="ticket", ticket="PT-3", provider="anthropic",
                 attempt=1)
        self.add("K-3", "step.finished", step="K-3-PT-3-1", ticket="PT-3", result="error", reason="x")
        self.add("K-3", caretaker.STARTED, problem="error:K-3-PT-3-1", key="caretaker:error:K-3-PT-3-1:1", n=1,
                 step="K-3-care-1", target="K-3-PT-3-1", root="K-3-PT-3-1", ticket="PT-3", trigger="error",
                 triggers=["error"], decision="step:K-3-PT-3-1", watermark=3)
        self.sonnet = types.SimpleNamespace(provider="anthropic", local=False, route=None, model="sonnet",
                                            name="Sonnet")
        self.opus = types.SimpleNamespace(provider="anthropic", local=False, route=None, model="opus", name="Opus")

    def admit(self, card, selected, ticket, kind, step):
        """Jeden start kroku jak w `_launch`: przyjęty krok dostaje `step.started` (zajmuje slot)."""
        from tide_daemon.cardflow.engine import FlowDeferred
        try:
            self.engine._admit(card, selected, ticket, kind, step)
        except FlowDeferred as exc:
            return str(exc)
        self.add(card, "step.started", step=step, kind=kind, ticket=ticket, provider=selected.provider)
        return ""

    def round(self, *extra, only=None):
        """Obrót pętli w kolejności kart (`journal.cards()`); w K-3 nowy ticket celowo przed opiekunem. `extra` —
        kroki dodatkowych kart na końcu obrotu, `only` — wyłącznie te kroki (np. tylko karty sortowane po K-3)."""
        started = []
        steps = only if only is not None else (
            ("K-2", self.sonnet, "PT-2", "ticket", "K-2-PT-2-1"),
            ("K-3", self.sonnet, "PT-4", "ticket", "K-3-PT-4-1"),
            ("K-3", self.opus, "PT-3", caretaker.KIND, "K-3-care-1"), *extra)
        for card, selected, ticket, kind, step in steps:
            st = fold(self.journal.read(card))
            if step in st.steps or step in st.caretakers:
                continue
            if not self.admit(card, selected, ticket, kind, step):
                started.append(step)
        return started

    def finish(self, card, step):
        self.add(card, "step.finished", step=step, result="done")

    def test_caretaker_goes_before_new_tickets_of_its_card_but_not_before_other_cards(self):
        self.assertEqual(self.round(), [])
        waiting = fold(self.journal.read("K-3")).waiting
        self.assertEqual(waiting[("PT-3", caretaker.KIND)]["reason"], "limit kroków dostawcy anthropic: 1/1")
        self.assertTrue(waiting[("PT-4", "ticket")]["reason"].startswith("limit kroków dostawcy anthropic"))
        self.finish("K-1", "K-1-PT-1-1")
        self.assertEqual(self.round(), ["K-2-PT-2-1"], "K-2 czekała wcześniej i wyprzedza opiekuna K-3")
        self.assertEqual(self.round(), [])
        self.finish("K-2", "K-2-PT-2-1")
        self.assertEqual(self.round(), ["K-3-care-1"], "opiekun przed nowym ticketem swojej karty")
        self.finish("K-3", "K-3-care-1")
        self.assertEqual(self.round(), ["K-3-PT-4-1"])

    def test_an_earlier_waiting_card_sorted_after_the_caretakers_card_still_goes_first(self):
        """Review M3: K-4 zaczyna czekać przed opiekunem K-3, ale w obrocie pętli jest po K-3."""
        k4 = ("K-4", self.sonnet, "PT-5", "ticket", "K-4-PT-5-1")
        self.add("K-4", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", column="todo")
        self.assertEqual(self.round(only=[k4]), [], "K-4 czeka pierwsza")
        self.assertEqual(self.round(only=[
            ("K-3", self.sonnet, "PT-4", "ticket", "K-3-PT-4-1"),
            ("K-3", self.opus, "PT-3", caretaker.KIND, "K-3-care-1"), k4]), [])
        k3_waiting = fold(self.journal.read("K-3")).waiting
        self.assertTrue(k3_waiting[("PT-3", caretaker.KIND)]["reason"].startswith("limit kroków dostawcy anthropic"))
        self.finish("K-1", "K-1-PT-1-1")
        k3_first = [("K-3", self.sonnet, "PT-4", "ticket", "K-3-PT-4-1"),
                    ("K-3", self.opus, "PT-3", caretaker.KIND, "K-3-care-1"), k4]
        self.assertEqual(self.round(only=k3_first), ["K-4-PT-5-1"],
                         "K-4 czekała wcześniej: opiekun K-3 ustępuje mimo kolejności pętli")
        self.assertEqual(self.round(only=k3_first), [], "limit dostawcy nadal obowiązuje")
        self.finish("K-4", "K-4-PT-5-1")
        self.assertEqual(self.round(only=k3_first), ["K-3-care-1"], "opiekun przed nowym ticketem swojej karty")
        self.finish("K-3", "K-3-care-1")
        self.assertEqual(self.round(only=k3_first), ["K-3-PT-4-1"])

    def test_a_card_waiting_later_than_the_caretaker_does_not_overtake_it(self):
        """Opiekun K-3 czekał pierwszy; K-4 zaczyna czekać później i ustępuje, choć ma wcześniejszy obrót."""
        k4 = ("K-4", self.sonnet, "PT-5", "ticket", "K-4-PT-5-1")
        self.add("K-4", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", column="todo")
        caretaker_step = ("K-3", self.opus, "PT-3", caretaker.KIND, "K-3-care-1")
        self.assertEqual(self.round(only=[caretaker_step]), [])
        self.assertEqual(self.round(only=[k4]), [])
        self.finish("K-1", "K-1-PT-1-1")
        self.assertEqual(self.round(only=[caretaker_step, k4]), ["K-3-care-1"])

    def test_a_paused_card_does_not_hold_the_caretaker(self):
        k4 = ("K-4", self.sonnet, "PT-5", "ticket", "K-4-PT-5-1")
        self.add("K-4", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                 preferredProvider="anthropic", column="todo")
        self.assertEqual(self.round(only=[k4]), [])
        self.add("K-4", "card.paused")
        self.finish("K-1", "K-1-PT-1-1")
        self.assertEqual(self.round(only=[("K-3", self.opus, "PT-3", caretaker.KIND, "K-3-care-1")]),
                         ["K-3-care-1"])

    def test_reservation_counts_as_a_running_step_and_does_not_hold_other_cards(self):
        with open(self.engine.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 3}}, fh)
        # K-1 i K-2 zajmują dwa z trzech miejsc; ostatnie należy do opiekuna K-3, nie do jej nowego ticketu.
        self.assertEqual(self.round(), ["K-2-PT-2-1", "K-3-care-1"])
        with open(self.engine.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 4}}, fh)
        self.assertEqual(self.round(), ["K-3-PT-4-1"])

    def test_without_a_ready_caretaker_step_nothing_is_reserved(self):
        self.add("K-3", caretaker.ESCALATED, problem="error:K-3-PT-3-1", step="K-3-care-1", target="K-3-PT-3-1",
                 ticket="PT-3", failed=True, code="caretaker-not-started", error="x")
        self.finish("K-1", "K-1-PT-1-1")
        with open(self.engine.settings, "w", encoding="utf-8") as fh:
            json.dump({"providerSlots": {"anthropic": 2}}, fh)
        self.assertEqual(self.round()[:2], ["K-2-PT-2-1", "K-3-PT-4-1"])


class RetryFlowTest(FlowInProcess):
    """Przebiegi przez FlowEngine z atrapą `claude`."""

    def failing_card(self, care="retry"):
        return self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                                     f"ATRAPA-POWOD: test pada ATRAPA-OPIEKUN: {care}")

    def caretaker_steps(self, card_id):
        return [s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker"]

    def children(self, card_id, parent):
        return [s for s in self.of(card_id, "step.started") if s.get("parentStep") == parent]

    def prompt(self, step):
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            return fh.read()

    def test_retry_at_a_step_error_resumes_with_the_instruction_and_escalates_after_the_limit(self):
        card_id = self.failing_card()
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=40), self.events(card_id))
        first = self.of(card_id, "caretaker.action")[0]
        self.assertEqual((first["ok"], first["action"], first["stale"]), (True, "retry", False))
        decided = [d for d in self.of(card_id, "step.decision") if d["step"] == first["target"]]
        self.assertEqual([(d["choice"], d["auto"], d["caretaker"], d["answer"]) for d in decided],
                         [("retry", True, first["problem"],
                           "Najpierw uruchom test eksportu i popraw format daty na ISO 8601.")])
        self.assertTrue(wait_until(lambda: self.children(card_id, first["target"]), timeout=20))
        resumed = self.children(card_id, first["target"])[0]
        prompt = self.prompt(resumed)
        self.assertIn("Instrukcja od opiekuna karty", prompt)
        self.assertIn("Najpierw uruchom test eksportu i popraw format daty na ISO 8601.", prompt)
        self.assertNotIn("Użytkownik polecił ponowić", prompt)
        results = [r for r in self.results(card_id, "caretaker-action") if r["key"] == first["step"]]
        self.assertEqual([r["ok"] for r in results], [True])

        # Ponowiony krok znowu pada: ten sam problem; po trzech działaniach eskalacja, czwarty krok nie startuje.
        self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")],
                                   timeout=120), self.events(card_id))
        card = self.wait_column(card_id, "waiting", timeout=30)
        runs = self.of(card_id, "caretaker.started")
        self.assertEqual({r["problem"] for r in runs}, {first["problem"]})
        self.assertEqual([r["n"] for r in runs], [1, 2, 3])
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.caretaker_steps(card_id)), 3, "czwarty krok opiekuna nie startuje")
        escalated = self.of(card_id, "caretaker.escalated")
        self.assertEqual([(e["code"], e["failed"], e["count"]) for e in escalated], [("caretaker-limit", True, 3)])
        decision = card["derived"]["flow"]["decisions"][0]
        self.assertEqual(len(decision["caretaker"]["tried"]), 3)
        self.assertTrue(all(t["action"] == "retry" and t["ok"] and t["reason"] for t in decision["caretaker"]["tried"]))
        history_rows = card["derived"]["flow"]["history"]
        texts = [(h["text"], h["actor"]) for h in history_rows if h["kind"] == "caretaker"]
        self.assertEqual(texts[:3], [(f"Opiekun: ponowił {first['label']} z instrukcją — Dziennik kroku pokazuje błąd "
                                      "formatu daty.", "caretaker")] * 3)
        self.assertTrue(texts[3][0].startswith("Opiekun: oddaje decyzję po 3 działaniach (limit)"))
        self.assertFalse([h for h in history_rows if h["text"].startswith("Decyzja: ponowienie kroku")])
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "step.notice")) == 1, timeout=5))

    def test_retry_without_a_decision_goes_through_the_internal_retry_path(self):
        with mock.patch.object(caretaker, "TRIGGERS", [repeat_trigger]), \
                mock.patch.dict(caretaker.BRIEF_SECTIONS, {"repeat": caretaker._error_section}):
            card_id = self.failing_card()
            self.assertTrue(self.start(card_id)["ok"])
            self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=40),
                            self.events(card_id))
            first = self.of(card_id, "caretaker.action")[0]
            self.assertTrue(first["ok"], first)
            self.assertTrue(first["problem"].startswith("repeat:"))
            retried = [r for r in self.of(card_id, "step.retry") if r.get("caretaker")]
            self.assertEqual([(r["step"], r["caretaker"], r["caretakerStep"], r["instruction"]) for r in retried],
                             [(first["target"], first["problem"], first["step"],
                               "Najpierw uruchom test eksportu i popraw format daty na ISO 8601.")])
            self.assertEqual([d for d in self.of(card_id, "step.decision") if d["step"] == first["target"]], [])
            self.assertTrue(wait_until(lambda: self.children(card_id, first["target"]), timeout=20))
            resumed = self.children(card_id, first["target"])
            self.assertEqual([s["attempt"] for s in resumed], [1])
            self.assertIn("Instrukcja od opiekuna karty", self.prompt(resumed[0]))
            texts = [h["text"] for h in self.shown(card_id)["derived"]["flow"]["history"] if h["kind"] == "caretaker"]
            self.assertIn(f"Opiekun: ponowił {first['label']} z instrukcją — Dziennik kroku pokazuje błąd formatu "
                          "daty.", texts)

    def test_retry_of_a_step_outside_the_problem_fails_and_counts_towards_the_limit(self):
        card_id = self.failing_card("retry-obcy")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")],
                                   timeout=90), self.events(card_id))
        actions = self.of(card_id, "caretaker.action")
        self.assertEqual([(a["ok"], a["code"]) for a in actions], [(False, "not-problem-step")] * 3)
        self.assertIn("K-999-obcy-1 nie należy do problemu", actions[0]["error"])
        self.assertEqual([d for d in self.of(card_id, "step.decision") if d.get("caretaker")], [])
        self.assertEqual(len(self.caretaker_steps(card_id)), 3)
        card = self.wait_column(card_id, "waiting", timeout=30)
        tried = card["derived"]["flow"]["decisions"][0]["caretaker"]["tried"]
        self.assertEqual([(t["action"], t["ok"]) for t in tried], [("retry", False)] * 3)
        self.assertTrue(tried[0]["text"].startswith("Opiekun: nie udało się (ponowienie kroku) — not-problem-step"))

    def test_users_decision_during_the_caretakers_work_wins_and_the_late_action_is_stale(self):
        card_id = self.failing_card("retry-powoli")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
        run = self.of(card_id, "caretaker.started")[0]
        self.assertTrue([s for s in fold(self.events(card_id)).running() if s.get("kind") == "caretaker"],
                        "opiekun pracuje")
        # Decyzja użytkownika w trakcie pracy opiekuna zwykłym API decyzji (review M2): krok opiekuna tego ticketu
        # nie blokuje jej jako „zajęty”.
        reply = self.flow.engine.resolve_step(card_id, {"choice": "retry", "step": run["target"],
                                                        "answer": "Instrukcja użytkownika"})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["step"], reply["choice"]), (run["target"], "retry"))
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=30), self.events(card_id))
        late = self.of(card_id, "caretaker.action")
        self.assertEqual([(a["ok"], a["error"], a["stale"]) for a in late], [(False, "stale", True)])
        # Wznowiony z decyzji użytkownika krok pracuje w tym samym worktree, więc strażnik (PT-158) może odrzucić
        # wynik opiekuna przed odczytem akcji; nieaktualność i tak rozstrzyga pierwsza, a powód strażnika zostaje.
        self.assertTrue(late[0]["detail"].startswith("użytkownik już zdecydował"), late[0])
        self.assertIn(late[0]["action"], ("retry", ""))
        if not late[0]["action"]:
            self.assertIn("strażnik:", late[0]["detail"])
        self.assertEqual(self.results(card_id, "caretaker-action"), [], "nic nie wykonano")
        self.assertEqual([d for d in self.of(card_id, "step.decision") if d.get("caretaker")], [])
        self.assertEqual([(d["choice"], d["answer"], d.get("auto")) for d in self.of(card_id, "step.decision")
                          if d["step"] == run["target"]], [("retry", "Instrukcja użytkownika", None)])
        # Wznowienie z decyzji użytkownika (jedyne): opiekun nie dołożył drugiego.
        self.assertTrue(wait_until(lambda: self.children(card_id, run["target"]), timeout=20))
        wait_until(lambda: False, timeout=1.0)
        children = self.children(card_id, run["target"])
        self.assertEqual(len(children), 1)
        self.assertNotIn("Instrukcja od opiekuna", self.prompt(children[0]))
        self.assertIn("Instrukcja użytkownika", self.prompt(children[0]))
        st = fold(self.events(card_id))
        self.assertEqual(caretaker.owned(st, run["problem"])["count"], 0)
        self.assertEqual(self.of(card_id, "caretaker.escalated"), [])
        self.assertEqual(len(self.caretaker_steps(card_id)), 1)

    def test_event_outside_the_caretaker_after_the_watermark_makes_a_problem_without_a_decision_stale(self):
        with mock.patch.object(caretaker, "TRIGGERS", [repeat_trigger]), \
                mock.patch.dict(caretaker.BRIEF_SECTIONS, {"repeat": caretaker._error_section}):
            card_id = self.failing_card("retry-powoli")
            self.assertTrue(self.start(card_id)["ok"])
            self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
            journal = self.flow.engine.journal
            journal.append(card_id, "card.paused")
            journal.append(card_id, "card.resumed")
            self.assertTrue(wait_until(lambda: len(self.of(card_id, "caretaker.action")) >= 2, timeout=40),
                            self.events(card_id))
            first, second = self.of(card_id, "caretaker.action")[:2]
            self.assertEqual((first["ok"], first["error"], first["stale"]), (False, "stale", True))
            self.assertEqual(first["detail"], "po starcie opiekuna: card.paused")
            # Drugi krok opiekuna: tylko jego własne zdarzenia po `watermark` — akcja wykonana, liczy się raz.
            self.assertEqual((second["ok"], second["stale"]), (True, False), second)
            runs = self.of(card_id, "caretaker.started")
            self.assertEqual([r["n"] for r in runs[:2]], [1, 2])
            self.assertEqual(caretaker.owned(fold(self.events(card_id)), first["problem"])["runs"][0]["step"],
                             second["step"])

    def restart_between_intent_and_result(self, cut_after):
        """Restart usługi między zamiarem a wynikiem `caretaker-action` (wzorzec z `test_cardflow.py`): pierwsza
        usługa wykonuje akcję bez wznowienia kroku, dziennik jest przycinany po `cut_after`, nowa usługa kończy."""
        engine = self.flow.engine
        resume = engine._resume_step

        def without_caretaker_resume(card, st, step, decision, **kw):
            if decision.get("caretaker"):
                return None
            return resume(card, st, step, decision, **kw)

        card_id = self.failing_card()
        with mock.patch.object(engine, "_resume_step", side_effect=without_caretaker_resume):
            self.assertTrue(self.start(card_id)["ok"])
            self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=40),
                            self.events(card_id))
            journal = engine.journal
            self.flow.stop()
            wait_until(lambda: False, timeout=0.3)
        events = self.events(card_id)
        intent = next(i for i, e in enumerate(events) if e["type"] == "intent" and e["action"] == "caretaker-action")
        decision = next(i for i, e in enumerate(events) if e["type"] == "step.decision" and e.get("caretaker"))
        at = {"intent": intent, "decision": decision}[cut_after] + 1
        self.assertLess(intent, decision)
        truncated = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events[:at])
        for path in (journal.path(card_id), journal.mirror_path(card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(truncated)
        target = events[intent]["key"]
        run = next(e for e in events if e["type"] == "caretaker.started" and e["step"] == target)
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.children(card_id, run["target"]), timeout=30), self.events(card_id))
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len([d for d in self.of(card_id, "step.decision")
                              if d["step"] == run["target"] and d.get("caretaker")]), 1)
        self.assertEqual(len([a for a in self.of(card_id, "caretaker.action") if a["step"] == target]), 1)
        self.assertEqual(len([r for r in self.results(card_id, "caretaker-action") if r["key"] == target]), 1)
        self.assertEqual(len([i for i in self.of(card_id, "intent")
                              if i["action"] == "caretaker-action" and i["key"] == target]), 1)
        self.assertEqual(len(self.children(card_id, run["target"])), 1)

    def test_restart_after_the_intent_performs_the_action_exactly_once(self):
        self.restart_between_intent_and_result("intent")

    def test_restart_after_the_effect_but_before_the_result_performs_the_action_exactly_once(self):
        self.restart_between_intent_and_result("decision")


if __name__ == "__main__":
    unittest.main()
