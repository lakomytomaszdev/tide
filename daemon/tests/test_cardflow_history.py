"""PT-129: `derived.flow.history[]` — zdania, próby złożone po `parentStep`, stare zlecenia, „Wznów rozmowę”.

Dziennik składany z publicznych zdarzeń, wynik przez publiczne `derived` (tak jak widzi go Tablica).
"""
import unittest

from tide_daemon.cardflow.state import derived

from .test_cardflow_decisions import Events

CARD = {"column": "todo"}
ENTRY_KEYS = {"id", "ts", "kind", "kindLabel", "text", "actor", "old", "step", "ticket", "result", "attempts",
              "canResume"}


def history(events):
    return derived(events.state(), CARD)["flow"]["history"]


def step_lines(items):
    return [i for i in items if i["kind"] in ("step", "review")]


class AttemptsTest(unittest.TestCase):
    def test_three_attempts_of_one_step_are_one_line(self):
        events = Events()
        events.step("S-0", attempt=0, result="error", reason="exit")
        events.step("S-1", attempt=1, parent="S-0", result="error", reason="exit")
        events.step("S-2", attempt=2, parent="S-1", result="done")
        lines = step_lines(history(events))
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(set(line), ENTRY_KEYS)
        self.assertEqual([(a["step"], a["attempt"], a["result"]) for a in line["attempts"]],
                         [("S-0", 0, "error"), ("S-1", 1, "error"), ("S-2", 2, "done")])
        self.assertEqual(line["text"], "PT-1: Sonnet — 3 próby, ostatnia: gotowe")
        self.assertEqual(line["step"], "S-2")
        self.assertIsInstance(line["ts"], float)

    def test_single_attempt_and_other_steps_stay_separate_lines(self):
        events = Events()
        events.step("S-0", result="done")
        events.step("R-0", kind="review", executor="Opus", result="done")
        events.step("S-9", ticket="PT-2", result="question", question="?")
        lines = step_lines(history(events))
        self.assertEqual([line["text"] for line in lines],
                         ["PT-1: Sonnet — gotowe", "review PT-1: Opus — gotowe", "PT-2: Sonnet — pyta"])
        self.assertTrue(all(len(line["attempts"]) == 1 for line in lines))

    def test_parent_of_another_kind_does_not_fold(self):
        events = Events()
        events.step("R-0", kind="review", executor="Opus", result="done")
        events.step("F-0", kind="fix", parent="R-0", result="done")
        self.assertEqual(len(step_lines(history(events))), 2)

    def test_running_attempt_is_marked_as_running(self):
        events = Events()
        events.step("S-0", result="error", reason="exit")
        events.step("S-1", attempt=1, parent="S-0")
        line = step_lines(history(events))[0]
        self.assertEqual(line["text"], "PT-1: Sonnet — 2 próby, ostatnia: pracuje")
        self.assertEqual(line["attempts"][-1]["resultLabel"], "trwa")

    def test_polish_plural_of_attempts(self):
        events = Events()
        events.step("S-0", result="error", reason="exit")
        for n in range(1, 5):
            events.step(f"S-{n}", attempt=n, parent=f"S-{n - 1}", result="error", reason="exit")
        self.assertIn("5 prób", step_lines(history(events))[0]["text"])


class OldOrdersTest(unittest.TestCase):
    def two_orders(self):
        events = Events()
        events.step("A-0", result="done")
        events.add("card.paused")
        events.add("card.started", entry="now", executor="Opus", program="claude", provider="anthropic")
        events.step("B-0", ticket="PT-2", result="done")
        return events

    def test_entries_before_last_card_started_and_superseded_are_old(self):
        items = history(self.two_orders())
        by_text = {i["text"]: i["old"] for i in items}
        self.assertTrue(by_text["PT-1: Sonnet — gotowe"])
        self.assertTrue(by_text["Karta wstrzymana"])
        self.assertFalse(by_text["PT-2: Sonnet — gotowe"])
        started = [i for i in items if i["text"].startswith("Zlecenie")]
        self.assertEqual([i["old"] for i in started], [True, False])

    def test_old_steps_cannot_be_resumed(self):
        events = Events()
        events.step("A-0", result="stopped", reason="interactive-closed")
        events.add("card.started", entry="now", executor="Opus", program="claude", provider="anthropic")
        line = step_lines(history(events))[0]
        self.assertTrue(line["old"])
        self.assertFalse(line["canResume"])


class ResumeTest(unittest.TestCase):
    def test_only_current_questions_and_stopped_steps_can_resume(self):
        events = Events()
        for name, result in (("Q", "question"), ("D", "denied"), ("S", "stopped"), ("G", "done"), ("E", "error")):
            events.step(name, ticket=f"PT-{name}", result=result, worktree="/wt/" + name)
        resumable = {line["step"]: line["canResume"] for line in step_lines(history(events))}
        self.assertEqual(resumable, {"Q": True, "D": True, "S": True, "G": False, "E": False})

    def test_running_step_has_no_resume(self):
        events = Events().step("S-0")
        self.assertFalse(step_lines(history(events))[0]["canResume"])

    def test_resume_follows_the_latest_attempt(self):
        events = Events()
        events.step("S-0", result="question", question="?")
        events.step("S-1", attempt=1, parent="S-0", result="done")
        self.assertFalse(step_lines(history(events))[0]["canResume"])

    def test_step_that_cannot_be_entered_has_no_resume(self):
        events = Events()
        events.step("S-0", result="question", question="?")
        events.step("S-1", ticket="PT-2")                  # ten sam worktree i trwa — wejście zablokowane
        by_step = {line["step"]: line["canResume"] for line in step_lines(history(events))}
        self.assertFalse(by_step["S-0"])


class ActorTest(unittest.TestCase):
    def test_user_events_are_marked_and_others_are_not(self):
        events = Events(entry="plan")
        events.add("plan.proposed", planner="Opus", rows=[])
        events.add("plan.approved", rows=[], map={})
        events.step("S-0", result="done")
        events.add("review.verdict", ticket="PT-1", round=1, verdict="APPROVE")
        events.add("card.paused")
        actors = {i["text"]: i["actor"] for i in history(events)}
        self.assertEqual(actors["Plan zatwierdzony"], "user")
        self.assertEqual(actors["Karta wstrzymana"], "user")
        self.assertEqual(actors["Plan zaproponowany przez Opus"], "Opus")
        self.assertEqual(actors["PT-1: Sonnet — gotowe"], "Sonnet")
        self.assertEqual(actors["Review PT-1: zatwierdza"], "tide")

    def test_automatic_decisions_are_tide_not_user(self):
        events = Events()
        events.step("S-0", result="denied")
        events.add("step.decision", step="S-0", ticket="PT-1", choice="deny", auto=True)
        events.add("step.decision", step="S-0", ticket="PT-1", choice="transfer", executor="Codex", automatic=True)
        events.add("step.decision", step="S-0", ticket="PT-1", choice="allow")
        items = [i for i in history(events) if i["kind"] == "decision"]
        self.assertEqual([i["actor"] for i in items], ["tide", "tide", "user"])
        self.assertTrue(items[0]["text"].startswith("Tide odmówił polecenia"))
        self.assertIn("Tide przeniósł krok", items[1]["text"])
        self.assertIn("Codex", items[1]["text"])
        self.assertIn("zgoda na polecenie", items[2]["text"])

    def test_review_decision_is_user_event(self):
        events = Events()
        events.add("review.decision", ticket="PT-1", round=3, choice="retry")
        events.add("review.decision", ticket="PT-1", round=3, choice="reject")
        events.add("ticket.rejected", ticket="PT-1")
        items = [i for i in history(events) if i["kind"] == "decision"]
        self.assertEqual([i["actor"] for i in items], ["user", "user"])
        self.assertIn("kontynuacja", items[0]["text"])
        self.assertEqual(items[1]["text"], "Ticket PT-1 odrzucony")

    def test_entries_are_chronological_and_have_no_iso_text(self):
        events = Events()
        events.step("S-0", result="done")
        events.add("card.paused")
        items = history(events)
        self.assertEqual([i["ts"] for i in items], sorted(i["ts"] for i in items))
        for item in items:
            self.assertNotIn("2026-", item["text"])
            self.assertNotIn("T1", item["text"])


if __name__ == "__main__":
    unittest.main()
