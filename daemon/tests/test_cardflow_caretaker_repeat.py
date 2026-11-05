"""Ten sam powód drugi raz i krok bez postępu (K-37, PT-148): wyzwalacze `repeat` i `no-progress` w rejestrze
`caretaker.TRIGGERS`.

Najpierw czyste funkcje `caretaker.py` na złożonych dziennikach (kandydaci, id, `takes_over`, kartka), potem
`FlowEngine` na złożonych dziennikach (opiekun zamiast automatycznego ponowienia) i przebiegi z atrapą `claude`
(`bez-wyniku`, `blad-opiekun`, `czekaj`, `zgoda-zabroniona`).
"""
import os
import tempfile
import shutil
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker
from tide_daemon.cardflow.state import fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_caretaker import Journal, ev

REPEAT = "repeat:PT-7:no-result"


def chain(reasons, first="K-1-PT-7-1"):
    """Kroki PT-7 kolejno: pierwszy próba 0, następne próba 1 (po automatycznym ponowieniu) z podanymi powodami."""
    j = Journal()
    parent = ""
    for n, reason in enumerate(reasons, 1):
        name = first[:-1] + str(n)
        j.step(name, 0 if n == 1 else 1, parent)
        j.error(name, reason)
        if n == 1 and len(reasons) > 1:
            j.add("step.retry", step=name, ticket="PT-7")
        parent = name
    return j


def user_retry_then_fail(j, reason="no-result"):
    """Użytkownik ponawia ostatni krok (`step.decision` bez pola `caretaker`), krok rusza od próby zerowej."""
    last = list(j.st.steps)[-1]
    j.add("step.decision", step=last, ticket="PT-7", choice="retry", answer="")
    name = f"K-1-PT-7-{len(j.st.steps) + 1}"
    j.step(name, 0, last)
    j.error(name, reason)
    return name


class RepeatTriggerTest(unittest.TestCase):
    def test_same_result_and_cause_twice_in_a_ticket_is_one_problem_with_the_error_trigger(self):
        j = chain(["no-result", "no-result"])
        found = caretaker.problems(j.st)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["id"], found[0]["triggers"]), ("error:K-1-PT-7-1", ["error", "repeat", ]))
        self.assertEqual(found[0]["trigger"], "error", "dzisiejszy problem z decyzją nie zmienia się")

    def test_repeat_alone_has_the_id_from_ticket_and_cause_and_no_decision(self):
        j = chain(["no-result", "no-result"])
        with mock.patch.object(caretaker, "TRIGGERS", [caretaker._repeat_trigger]):
            found = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["trigger"], p["decision"], p["step"], p["ticket"]) for p in found],
                         [(REPEAT, "repeat", "", "K-1-PT-7-2", "PT-7")])
        self.assertEqual(caretaker.problem_id("repeat", j.st, ticket="PT-7", cause="no-result"), REPEAT)
        self.assertNotRegex(found[0]["id"], r"\d{3,}", "bez seq dziennika")

    def test_first_failure_and_different_causes_do_not_start_the_trigger(self):
        self.assertEqual(caretaker._repeat_trigger(chain(["no-result"]).st), [])
        j = chain(["no-result", "exit"])
        self.assertEqual(caretaker._repeat_trigger(j.st), [])
        self.assertEqual(caretaker.problems(j.st)[0]["triggers"], ["error"])
        # Użytkownik ponawia, a krok pada z inną przyczyną niż poprzedni: dzisiejsze ponowienie zostaje.
        user_retry_then_fail(j, "bad-result")
        self.assertEqual(caretaker._repeat_trigger(j.st), [])
        self.assertFalse(caretaker.takes_over(j.st, list(j.st.steps)[-1]))

    def test_free_text_reasons_are_the_same_cause_only_when_they_are_equal(self):
        self.assertEqual(len(caretaker._repeat_trigger(chain(["test pada na dacie", "test pada na dacie"]).st)), 1)
        self.assertEqual(caretaker._repeat_trigger(chain(["test pada na dacie", "test pada na liczbie"]).st), [])

    def test_login_and_provider_limit_twice_in_a_row_do_not_start_it(self):
        for reason in ("login", "limit"):
            with self.subTest(reason=reason):
                j = chain([reason, reason])
                self.assertEqual(caretaker._repeat_trigger(j.st), [])
                self.assertEqual(caretaker.problems(j.st), [])
        j = chain(["exit", "exit"])
        j.events[-1]["faultCause"] = "limit"
        j.events[-1]["fault"] = "provider"
        self.assertEqual(caretaker._repeat_trigger(j.st), [])
        j = chain(["abandoned", "abandoned"])
        self.assertEqual(caretaker._repeat_trigger(j.st), [], "wina użytkownika")

    def test_another_ticket_in_between_does_not_make_two_failures_consecutive_in_a_ticket(self):
        j = chain(["no-result"])
        j.step("K-1-PT-8-1", 1, ticket="PT-8")
        j.add("step.finished", step="K-1-PT-8-1", ticket="PT-8", result="error", reason="no-result")
        self.assertEqual(caretaker._repeat_trigger(j.st), [], "po jednym błędzie w każdym tickecie")

    def test_a_success_between_failures_breaks_the_repeat(self):
        j = chain(["no-result"])
        j.step("K-1-PT-7-2", 1, "K-1-PT-7-1")
        j.add("step.finished", step="K-1-PT-7-2", ticket="PT-7", result="done")
        j.step("K-1-PT-7-3", 0, "K-1-PT-7-2", kind="fix")
        j.error("K-1-PT-7-3", "no-result")
        self.assertEqual(caretaker._repeat_trigger(j.st), [])

    def test_plan_steps_use_the_kind_of_the_step_instead_of_the_ticket(self):
        j = Journal()
        for n, name in enumerate(("K-1-plan-1", "K-1-plan-2"), 1):
            j.step(name, n - 1, "K-1-plan-1" if n == 2 else "", kind="planning", ticket="")
            j.add("step.finished", step=name, ticket="", result="error", reason="no-result")
        found = caretaker._repeat_trigger(j.st)
        self.assertEqual([(c["step"], c["kind"], c["ticket"]) for c in found], [("K-1-plan-2", "planning", "")])
        self.assertEqual(caretaker.problems(j.st)[0]["id"], "repeat:planning:no-result")

    def test_a_decided_rejected_or_continued_step_is_not_a_candidate(self):
        j = chain(["no-result", "no-result"])
        j.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="change-executor", executor="Opus", auto=True)
        self.assertEqual(caretaker._repeat_trigger(j.st), [], "mocniejszy wykonawca zostaje (D3)")
        j = chain(["no-result", "no-result"])
        j.add("ticket.rejected", ticket="PT-7")
        self.assertEqual(caretaker._repeat_trigger(j.st), [])

    def test_repeat_after_a_caretaker_retry_joins_the_known_problem(self):
        j = chain(["no-result", "no-result"])
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        j.add("step.retry", step="K-1-PT-7-2", ticket="PT-7", caretaker="error:K-1-PT-7-1", caretakerStep="K-1-care-1")
        j.step("K-1-PT-7-3", 1, "K-1-PT-7-2")
        j.error("K-1-PT-7-3", "no-result")
        found = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["step"]) for p in found], [("error:K-1-PT-7-1", "K-1-PT-7-3")])
        self.assertEqual(caretaker.owned(j.st, "error:K-1-PT-7-1")["count"], 1)

    def test_user_retry_closes_the_problem_and_the_next_repeat_belongs_to_the_same_chain(self):
        j = chain(["no-result", "no-result"])
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", outcome={"ok": True})
        j.escalate("K-1-PT-7-2", "K-1-care-1", "K-1-PT-7-1")
        last = user_retry_then_fail(j)
        found = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["trigger"], p["decision"], p["step"]) for p in found],
                         [("error:K-1-PT-7-1", "repeat", "", last)])
        own = caretaker.owned(j.st, "error:K-1-PT-7-1")
        self.assertEqual((own["count"], own["handed"], own["next"]), (0, False, 2))
        self.assertTrue(caretaker.takes_over(j.st, last))


class AutoDenyRepeatTest(unittest.TestCase):
    def denied(self, j, name, command, parent="", auto=None):
        j.step(name, 0, parent)
        j.add("step.finished", step=name, ticket="PT-7", result="denied", command=command)
        if auto is not None:
            j.add("step.decision", step=name, ticket="PT-7", kind="ticket", choice="deny", answer="Pojedynczo.",
                  command=command, auto=auto)

    def test_second_denial_of_the_same_command_after_an_automatic_denial_is_a_repeat(self):
        j = Journal()
        self.denied(j, "K-1-PT-7-1", "a && b", auto=True)
        self.denied(j, "K-1-PT-7-2", "a &&  b", parent="K-1-PT-7-1")
        found = caretaker._repeat_trigger(j.st)
        self.assertEqual([(c["step"], c["cause"].startswith("denied-")) for c in found], [("K-1-PT-7-2", True)])
        self.assertTrue(caretaker.takes_over(j.st, "K-1-PT-7-2"))
        problem = caretaker.problems(j.st)[0]
        self.assertEqual((problem["trigger"], problem["decision"]), ("repeat", ""))
        self.assertEqual(caretaker.problem_steps(problem, j.st), ["K-1-PT-7-2"])

    def test_other_command_or_a_denial_by_the_user_is_not_a_repeat(self):
        j = Journal()
        self.denied(j, "K-1-PT-7-1", "a && b", auto=True)
        self.denied(j, "K-1-PT-7-2", "c && d", parent="K-1-PT-7-1")
        self.assertEqual(caretaker._repeat_trigger(j.st), [])
        j = Journal()
        self.denied(j, "K-1-PT-7-1", "a && b", auto=False)
        self.denied(j, "K-1-PT-7-2", "a && b", parent="K-1-PT-7-1")
        self.assertEqual(caretaker._repeat_trigger(j.st), [])

    def test_a_first_denial_has_no_repeat_so_the_automatic_denial_stays(self):
        j = Journal()
        self.denied(j, "K-1-PT-7-1", "a && b")
        self.assertFalse(caretaker.takes_over(j.st, "K-1-PT-7-1"))


class TakesOverTest(unittest.TestCase):
    def repeating(self):
        j = chain(["no-result", "no-result"])
        return user_retry_then_fail(j), j

    def test_the_caretaker_takes_over_only_when_it_can_work(self):
        last, j = self.repeating()
        self.assertTrue(caretaker.takes_over(j.st, last))
        self.assertFalse(caretaker.takes_over(j.st, last, "backlog"))
        paused = Journal()
        paused.events = list(j.events)
        paused.add("card.paused")
        self.assertFalse(caretaker.takes_over(paused.st, last))
        local = chain(["no-result", "no-result"])
        local.events[0]["preferredProvider"] = "local"
        self.assertFalse(caretaker.takes_over(local.st, "K-1-PT-7-2"))

    def test_after_the_problem_is_handed_to_the_user_the_decision_replaces_the_blind_retry(self):
        from tide_daemon.cardflow import decisions
        last, j = self.repeating()
        self.assertFalse(caretaker.handed_repeat(j.st, last))
        self.assertEqual([d["step"] for d in decisions.build_decisions(j.st)], [], "próba zerowa: jeszcze bez decyzji")
        j.care(last, "K-1-PT-7-1", 1, "K-1-care-1", problem="error:K-1-PT-7-1")
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", outcome={"ok": True})
        j.escalate(last, "K-1-care-1", "K-1-PT-7-1")
        self.assertTrue(caretaker.takes_over(j.st, last), "automatyczne ponowienie nie wraca")
        self.assertTrue(caretaker.handed_repeat(j.st, last))
        found = decisions.build_decisions(j.st)
        self.assertEqual([(d["step"], d["kind"]) for d in found], [(last, "error")], "karta nie stoi bez decyzji")
        self.assertEqual(found[0]["caretaker"]["summary"], "Test pada na tym samym.")


class BriefTest(unittest.TestCase):
    def test_first_no_progress_keeps_the_automatic_retry_for_tickets_and_plans(self):
        for kind, ticket in (("ticket", "PT-7"), ("planning", ""), ("plan-review", "")):
            with self.subTest(kind=kind):
                j = Journal()
                j.step("step-0", 0, kind=kind, ticket=ticket)
                j.add("step.finished", step="step-0", ticket=ticket, result="error", reason="no-progress")
                self.assertEqual(caretaker.problems(j.st), [])

    def test_no_progress_context_for_plan_steps_and_repeated_attempt_zero(self):
        for kind, ticket, reasons in (("planning", "", ["exit", "no-progress"]),
                                      ("plan-review", "", ["exit", "no-progress"]),
                                      ("ticket", "PT-7", ["no-progress", "no-progress"])):
            with self.subTest(kind=kind):
                j = Journal()
                for n, reason in enumerate(reasons):
                    name = f"step-{n}"
                    j.step(name, 0 if ticket else n, "step-0" if n else "", kind=kind, ticket=ticket)
                    j.add("step.finished", step=name, ticket=ticket, result="error", reason=reason,
                          stalledSince="2026-11-03T10:00:00+00:00",
                          messages=[{"role": "assistant", "text": "Sprawdzam plan."}])
                    if n == 0:
                        j.add("step.decision", step=name, ticket=ticket, choice="retry")
                j.events[-1]["ts"] = "2026-11-03T10:25:30+00:00"
                found = caretaker.problems(j.st)
                self.assertEqual(len(found), 1)
                text = caretaker.brief(found[0], j.st, card="K-1", out_dir="/o", worktree="/w")
                self.assertIn("## Krok bez postępu", text)
                self.assertIn("Czas zastoju: 25 min 30 s.", text)
                self.assertIn("- assistant: Sprawdzam plan.", text)

    def test_secrets_are_masked_before_normalizing_and_truncating_brief_fields(self):
        for source, limit in (("messages", caretaker.SESSION_CHARS), ("command", 600)):
            for secret, prefix in (("abcdefgh\nijklmnop", ""),
                                   ("ghp_" + "A1b2" * 10, "x" * (limit - 12) + " ")):
                with self.subTest(source=source, multiline="\n" in secret):
                    with mock.patch.dict(os.environ, {"PRIVATE_SECRET": secret if "\n" in secret else "unused-test-value"}):
                        j = Journal()
                        if source == "command":
                            for n in (1, 2):
                                name = f"step-{n}"
                                j.step(name, 0, "step-1" if n == 2 else "")
                                j.add("step.finished", step=name, ticket="PT-7", result="denied",
                                      command=prefix + secret + " && b")
                                if n == 1:
                                    j.add("step.decision", step=name, choice="deny", auto=True)
                        else:
                            j.step("step-1", 1)
                            j.error("step-1", "no-progress", messages=[{"role": "assistant",
                                                                        "text": prefix + secret}])
                        text = caretaker.brief(caretaker.problems(j.st)[0], j.st,
                                               card="K-1", out_dir="/o", worktree="/w")
                        self.assertIn("•••", text)
                        self.assertNotIn("abcdefgh", text)
                        self.assertNotIn("ijklmnop", text)
                        self.assertNotIn("ghp_A1b2", text)

    def test_repeat_brief_names_both_steps_cause_and_blind_retry_warning(self):
        j = chain(["no-result", "no-result"])
        last = user_retry_then_fail(j)
        problem = caretaker.problems(j.st)[0]
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        for part in ("## Ten sam powód drugi raz", "Poprzedni krok: K-1-PT-7-2, ten krok: " + last,
                     "wynik (error) i tę samą przyczynę: no-result", "ślepą powtórką",
                     "Id problemu: repeat:PT-7:no-result"):
            self.assertIn(part, text)

    def test_repeat_brief_with_a_denied_command_masks_secrets(self):
        command = "GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 x && y"
        j = Journal()
        for name, parent in (("K-1-PT-7-1", ""), ("K-1-PT-7-2", "K-1-PT-7-1")):
            j.step(name, 0, parent)
            j.add("step.finished", step=name, ticket="PT-7", result="denied", command=command)
            if not parent:
                j.add("step.decision", step=name, ticket="PT-7", choice="deny", command=command, auto=True)
        problem = caretaker.problems(j.st)[0]
        self.assertEqual(problem["trigger"], "repeat")
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        self.assertIn("Polecenie odmówione dwa razy: GITHUB_TOKEN=•••", text)
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", text)

    def test_no_progress_brief_has_the_stall_time_and_the_session_tail(self):
        j = Journal()
        j.step("K-1-PT-7-1", 1)
        j.add("step.finished", step="K-1-PT-7-1", ticket="PT-7", result="error", reason="no-progress",
              stalledSince="2026-11-03T10:00:00+00:00",
              messages=[{"role": "assistant", "text": f"Sprawdzam {n}."} for n in range(1, 9)])
        j.events[-1]["ts"] = "2026-11-03T10:25:30+00:00"
        problems = caretaker.problems(j.st)
        self.assertEqual((problems[0]["trigger"], problems[0]["triggers"]), ("error", ["error", "no-progress"]))
        text = caretaker.brief(problems[0], j.st, card="K-1", out_dir="/o", worktree="/w")
        for part in ("## Krok bez postępu", "od 2026-11-03T10:00:00+00:00", "Czas zastoju: 25 min 30 s.",
                     "Końcówka zdarzeń sesji (ostatnie 5):", "- assistant: Sprawdzam 8.", "- assistant: Sprawdzam 4."):
            self.assertIn(part, text)
        self.assertNotIn("Sprawdzam 3.", text, "tylko końcówka")
        self.assertEqual(text.count("## Problem: błąd kroku"), 1)

    def test_no_progress_without_session_events_says_so(self):
        j = Journal()
        j.step("K-1-PT-7-1", 1)
        j.add("step.finished", step="K-1-PT-7-1", ticket="PT-7", result="error", reason="no-progress")
        problem = caretaker.problems(j.st)[0]
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/o", worktree="/w")
        self.assertIn("Czas zastoju: nieznany.", text)
        self.assertIn("wynik kroku nie ma zdarzeń sesji", text)

    def test_other_errors_do_not_get_the_no_progress_section(self):
        j = chain(["exit", "bad-result"])
        text = caretaker.brief(caretaker.problems(j.st)[0], j.st, card="K-1", out_dir="/o", worktree="/w")
        self.assertNotIn("## Krok bez postępu", text)
        self.assertNotIn("## Ten sam powód drugi raz", text)


class EngineJournalTest(unittest.TestCase):
    """Prawdziwy `FlowEngine` na dzienniku w katalogu tymczasowym: opiekun zamiast automatycznego ponowienia."""

    def setUp(self):
        import json
        from tide_daemon.board.store import BoardStore
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.cardflow.journal import Journal as CardJournal
        from tide_daemon.models import executors
        self.base = tempfile.mkdtemp(prefix="tide-pt148-")
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
        self.journal = journal
        journal.append("K-1", "card.started", entry="now", executor="Sonnet", provider="anthropic",
                       preferredProvider="anthropic", column="todo")

    def add(self, etype, **fields):
        return self.journal.append("K-1", etype, **fields)

    def failed(self, name, attempt, reason, parent="", ticket="PT-7"):
        self.add("step.started", step=name, kind="ticket", ticket=ticket, attempt=attempt, parentStep=parent,
                 provider="anthropic", executor="Sonnet")
        self.add("step.finished", step=name, ticket=ticket, result="error", reason=reason)

    def advance(self):
        with mock.patch.object(self.engine, "_resume_step") as resume:
            self.engine._advance("K-1", self._last())
        return resume

    def _last(self):
        st = fold(self.journal.read("K-1"))
        st.last_step = list(st.steps)[-1]
        return st

    def after_user_retry(self, second_reason):
        self.failed("K-1-PT-7-1", 0, "no-result")
        self.add("step.retry", step="K-1-PT-7-1", ticket="PT-7")
        self.failed("K-1-PT-7-2", 1, "no-result", "K-1-PT-7-1")
        self.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", kind="ticket", choice="retry", answer="")
        self.failed("K-1-PT-7-3", 0, second_reason, "K-1-PT-7-2")

    def test_same_cause_twice_gives_no_automatic_retry(self):
        self.after_user_retry("no-result")
        resume = self.advance()
        self.assertEqual(resume.call_count, 0)
        retries = [e for e in self.journal.read("K-1") if e["type"] == "step.retry"]
        self.assertEqual([e["step"] for e in retries], ["K-1-PT-7-1"], "automatyczne step.retry nie powstaje")

    def test_a_different_cause_keeps_todays_automatic_retry(self):
        self.after_user_retry("bad-result")
        resume = self.advance()
        self.assertEqual(resume.call_count, 1)
        self.assertEqual(resume.call_args.args[3], {"choice": "auto"})
        retries = [e["step"] for e in self.journal.read("K-1") if e["type"] == "step.retry"]
        self.assertEqual(retries, ["K-1-PT-7-1", "K-1-PT-7-3"])

    def test_without_a_working_caretaker_the_automatic_retry_stays(self):
        self.after_user_retry("no-result")
        self.add("card.paused")
        self.assertEqual(self.advance().call_count, 1, "wstrzymana karta: dzisiejsze ponowienie")

    def test_disabled_caretaker_keeps_the_automatic_retry_after_a_repeated_failure(self):
        self.after_user_retry("no-result")
        self.engine.set_flow_settings({"caretakerEnabled": False})
        self.assertEqual(self.advance().call_count, 1, "wyłączony opiekun: dzisiejsze ponowienie")
        self.engine._caretake("K-1", self._last())
        self.assertFalse([e for e in self.journal.read("K-1") if e["type"] == caretaker.STARTED])

    def test_local_caretaker_replaces_the_automatic_retry_despite_a_cloud_setting(self):
        self.add("card.started", entry="now", executor="Lokalny", provider="local",
                 preferredProvider="local", column="todo")
        self.after_user_retry("no-result")
        self.engine.set_flow_settings({"caretakerExecutor": "Opus"})
        self.assertEqual(self.advance().call_count, 0, "lokalny opiekun przejmuje powtarzający się błąd")

    def test_the_second_automatic_denial_is_replaced_by_the_caretaker(self):
        step = {"ticket": "PT-7", "kind": "ticket"}
        for n, parent in ((1, ""), (2, "K-1-PT-7-1")):
            self.add("step.started", step=f"K-1-PT-7-{n}", kind="ticket", ticket="PT-7", attempt=0,
                     parentStep=parent, provider="anthropic")
            self.add("step.finished", step=f"K-1-PT-7-{n}", ticket="PT-7", result="denied", command="a && b")
            self.engine._auto_deny("K-1", f"K-1-PT-7-{n}", step, "a && b")
        decisions = [e for e in self.journal.read("K-1") if e["type"] == "step.decision"]
        self.assertEqual([(d["step"], d["choice"], d["auto"]) for d in decisions], [("K-1-PT-7-1", "deny", True)])
        problems = caretaker.problems(self._last())
        self.assertEqual([(p["trigger"], p["step"], p["decision"]) for p in problems],
                         [("repeat", "K-1-PT-7-2", "")])


class RepeatFlowTest(FlowInProcess):
    """Przebiegi przez FlowEngine z atrapą `claude`."""

    def caretaker_runs(self, card_id):
        return self.of(card_id, "caretaker.started")

    def tickets(self, card_id):
        return [s for s in self.of(card_id, "step.started") if s["kind"] != "caretaker"]

    def prompt(self, step):
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            return fh.read()

    def test_missing_result_twice_after_a_user_retry_starts_the_caretaker_instead_of_a_blind_retry(self):
        card_id = self.card(description="Eksport.\n\nATRAPA: bez-wyniku")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting", timeout=60)
        first = self.caretaker_runs(card_id)
        self.assertEqual([(r["trigger"], r["decision"]) for r in first], [("error", f"step:{first[0]['target']}")])
        retries = len(self.of(card_id, "step.retry"))
        self.assertEqual(retries, 1)
        reply = self.flow.engine.resolve_step(card_id, {"choice": "retry", "step": first[0]["target"]})
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: len(self.caretaker_runs(card_id)) == 2, timeout=60), self.events(card_id))
        second = self.caretaker_runs(card_id)[1]
        self.assertEqual((second["trigger"], second["decision"], second["n"]), ("repeat", "", 2))
        self.assertEqual(second["problem"], first[0]["problem"], "ten sam łańcuch, jeden problem")
        self.wait_column(card_id, "waiting", timeout=60)
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.of(card_id, "step.retry")), retries, [self.of(card_id, "step.retry"), self.of(card_id, "caretaker.action")])
        steps = self.tickets(card_id)
        self.assertEqual([s["attempt"] for s in steps], [0, 1, 0], "trzeci krok (próba zerowa) nie ma następcy")
        self.assertEqual(len(self.caretaker_runs(card_id)), 2)
        self.assertIn("## Ten sam powód drugi raz", self.prompt(self.of(card_id, "step.started")[-1]))



class RepeatRetryFlowTest(FlowInProcess):
    """Opiekun ponawia krok problemu `repeat` (bez decyzji), a kolejne błędy z tą przyczyną to ten sam problem."""

    def failing_card(self, care="retry"):
        return self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                                     f"ATRAPA-POWOD: test pada ATRAPA-OPIEKUN: {care}")

    def children(self, card_id, parent):
        return [s for s in self.of(card_id, "step.started") if s.get("parentStep") == parent]

    def prompt(self, step):
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            return fh.read()

    def test_denials_after_caretaker_retries_stay_in_one_problem_until_the_action_limit(self):
        import json
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)
        card_id = self.card(description="Eksport.\n\nATRAPA: odmowa-opiekun")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=60), self.events(card_id))
        first = self.of(card_id, "caretaker.action")[0]
        self.assertTrue(first["ok"], first)
        self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")]
                                  or len(self.of(card_id, "step.decision")) > 1, timeout=60), self.events(card_id))
        self.assertEqual(len(self.of(card_id, "step.decision")), 1, "tylko pierwsza automatyczna odmowa")
        runs = self.of(card_id, "caretaker.started")
        self.assertEqual([r["n"] for r in runs], [1, 2, 3])
        self.assertEqual({r["problem"] for r in runs}, {first["problem"]})
        self.assertEqual(len([e for e in self.of(card_id, "step.retry") if e.get("caretaker")]), 3)
        st = fold(self.events(card_id))
        self.assertEqual([p["id"] for p in caretaker.problems(st)], [first["problem"]])
        self.assertTrue(caretaker.owned(st, first["problem"])["handed"])
        escalation = next(e for e in self.of(card_id, "caretaker.escalated") if e.get("limit"))
        self.assertEqual(escalation["count"], 3)
        self.assertTrue(escalation["summary"])
        self.assertEqual(len([s for s in self.of(card_id, "step.started") if s["kind"] != "caretaker"]), 5)

    def test_retry_without_a_decision_resumes_with_the_instruction_and_the_limit_escalates(self):
        with mock.patch.object(caretaker, "TRIGGERS", [caretaker._repeat_trigger]):
            card_id = self.failing_card()
            self.assertTrue(self.start(card_id)["ok"])
            self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=60),
                            self.events(card_id))
            first = self.of(card_id, "caretaker.action")[0]
            self.assertTrue(first["ok"], first)
            self.assertTrue(first["problem"].startswith("repeat:PT-"), first["problem"])
            retried = [r for r in self.of(card_id, "step.retry") if r.get("caretaker")]
            self.assertEqual([(r["step"], r["caretaker"], r["caretakerStep"]) for r in retried][:1],
                             [(first["target"], first["problem"], first["step"])])
            self.assertEqual([d for d in self.of(card_id, "step.decision") if d["step"] == first["target"]], [])
            self.assertTrue(wait_until(lambda: self.children(card_id, first["target"]), timeout=30))
            resumed = self.children(card_id, first["target"])[0]
            self.assertEqual(resumed["attempt"], 1)
            self.assertIn("Instrukcja od opiekuna karty", self.prompt(resumed))
            self.assertIn("Najpierw uruchom test eksportu", self.prompt(resumed))
            self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")],
                                       timeout=150), self.events(card_id))
            runs = self.of(card_id, "caretaker.started")
            self.assertEqual({r["problem"] for r in runs}, {first["problem"]})
            self.assertEqual([r["n"] for r in runs], [1, 2, 3])
            wait_until(lambda: False, timeout=1.0)
            self.assertEqual(len(self.of(card_id, "caretaker.started")), 3, "kolejny opiekun nie startuje")
            st = fold(self.events(card_id))
            self.assertEqual([p["id"] for p in caretaker.problems(st)], [first["problem"]])
            self.assertTrue(caretaker.owned(st, first["problem"])["handed"])

    def test_a_chain_with_an_open_error_problem_does_not_get_a_second_one(self):
        card_id = self.failing_card()
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: [e for e in self.of(card_id, "caretaker.escalated") if e.get("limit")],
                                   timeout=150), self.events(card_id))
        runs = self.of(card_id, "caretaker.started")
        self.assertEqual({r["problem"] for r in runs}, {runs[0]["problem"]})
        self.assertTrue(runs[0]["problem"].startswith("error:"))
        self.assertEqual([r["n"] for r in runs], [1, 2, 3])
        st = fold(self.events(card_id))
        found = caretaker.problems(st)
        self.assertEqual([p["id"] for p in found], [runs[0]["problem"]])
        self.assertIn("repeat", found[0]["triggers"])
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.of(card_id, "caretaker.started")), 3)

    def test_a_user_step_after_the_watermark_makes_the_action_of_a_repeat_problem_stale(self):
        with mock.patch.object(caretaker, "TRIGGERS", [caretaker._repeat_trigger]):
            card_id = self.failing_card("retry-powoli")
            self.assertTrue(self.start(card_id)["ok"])
            self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.started"), timeout=60),
                            self.events(card_id))
            run = self.of(card_id, "caretaker.started")[0]
            self.assertEqual(run["decision"], "")
            reply = self.flow.engine.resolve_step(card_id, {"choice": "retry", "step": run["target"],
                                                            "answer": "Instrukcja użytkownika"})
            self.assertTrue(reply["ok"], reply)
            self.assertTrue(wait_until(lambda: self.of(card_id, "caretaker.action"), timeout=60),
                            self.events(card_id))
            late = self.of(card_id, "caretaker.action")[0]
            self.assertEqual((late["ok"], late["error"], late["stale"]), (False, "stale", True))
            self.assertTrue(late["detail"].startswith("po starcie opiekuna: step.decision"), late)
            self.assertEqual([r for r in self.of(card_id, "step.retry") if r.get("caretaker")], [])
            self.assertTrue(wait_until(lambda: self.children(card_id, run["target"]), timeout=30))
            wait_until(lambda: False, timeout=1.0)
            self.assertEqual(len(self.children(card_id, run["target"])), 1, "nie powstaje drugi krok")
            self.assertNotIn("Instrukcja od opiekuna", self.prompt(self.children(card_id, run["target"])[0]))


class NoProgressFlowTest(FlowInProcess):
    guard_minutes = 0.05

    def test_the_caretaker_brief_has_the_stall_time_and_the_tail_of_the_session(self):
        card_id = self.card(description="Eksport.\n\nATRAPA: czekaj")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: [s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker"],
                                   timeout=90), self.events(card_id))
        step = next(s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker")
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertIn("## Krok bez postępu", prompt)
        self.assertRegex(prompt, r"Czas zastoju: \d+ min \d+ s\.")
        self.assertIn("Końcówka zdarzeń sesji (ostatnie 5):", prompt)
        self.assertIn("Robię ticket.", prompt)
        started = self.of(card_id, "caretaker.started")[0]
        self.assertEqual(started["trigger"], "error")
        self.assertIn("no-progress", started["triggers"])


if __name__ == "__main__":
    unittest.main()
