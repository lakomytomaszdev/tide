"""K-88/PT-312: wybór wykonawcy poprawek — ręczna zmiana autora, eskalacja, dotychczasowy autor.

PT-261 i PT-277: od 3. rundy poprawki pisał recenzent i sam je potem oceniał. Testy budują dziennik karty
i wołają `_fix` z przechwyconym `_launch`; wykonawcy pochodzą z domyślnego katalogu."""
import unittest
from types import SimpleNamespace

from tide_daemon.cardflow import engine as engine_mod
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.state import fold
from tide_daemon.models import executors

CARD = "K-1"
TICKET = "PT-1"
# Dane wykonawców z domyślnego katalogu (program, model, dostawca, siła myślenia).
EXECUTORS = {e.name: e for e in executors.DEFAULT_EXECUTORS}


class _Journal:
    def __init__(self, events):
        self.events = events

    def read(self, card):
        return list(self.events)

    def state(self, card):
        return fold(self.events)


class FixExecutorTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.launched = []
        engine = object.__new__(engine_mod.FlowEngine)
        engine.models_config = None
        engine.catalog = executors.default_catalog
        engine.model_probe = None
        engine.which = lambda tool: "/usr/bin/" + tool
        engine.journal = _Journal(self.events)
        engine._project = lambda card: (SimpleNamespace(title="T", description="D"), SimpleNamespace(repo="/r"))
        engine._ticket_text = lambda *args: ("T", "D")
        engine._note = lambda *args: ""
        engine._next_step_id = lambda *args: f"fix-{len(self.launched) + 1}"
        engine._launch = lambda item, project, selected, ticket, *args, **kw: self.launched.append(
            (selected, kw))
        self.engine = engine
        self.seq = 0

    # --- dziennik ---------------------------------------------------------------------------------

    def add(self, event_type, **fields):
        self.seq += 1
        self.events.append({"type": event_type, "seq": self.seq, **fields})
        return self.events[-1]

    def step(self, name, kind, executor, round_no=0, **extra):
        e = EXECUTORS[executor]
        return self.add("step.started", step=name, kind=kind, ticket=TICKET, round=round_no, executor=e.name,
                        program=e.program, model=e.model, provider=e.provider,
                        effort=e.effort if e.program == "codex" else "", routeUrl="", routeKind="",
                        routeTokenFile="", **extra)

    def card(self, author, reviewer, klass=None, preferred=None):
        a, r = EXECUTORS[author], EXECUTORS[reviewer]
        self.add("card.started", entry="now", executor=author, program=a.program, model=a.model,
                 provider=a.provider, reviewer=reviewer, reviewerProgram=r.program, reviewerModel=r.model,
                 reviewerProvider=r.provider, preferredProvider=preferred or a.provider)
        if klass is not None:
            row = {"id": TICKET, "blockers": [], "executor": author, "reviewer": reviewer}
            if klass:
                row["class"] = klass
            self.add("plan.approved", rows=[row])
        self.add("result", action="card-branch", key=CARD, ok=True, branch="card/k-1", worktree="/w/card")
        self.add("result", action="ticket-branch", key=TICKET, ok=True, ticket=TICKET, branch="ticket/pt-1",
                 worktree="/w/pt-1", base="abc")
        self.step("s-1", "ticket", author)

    def rounds(self, reviewer, count):
        """`count` rund REQUEST_CHANGES; poprawki między rundami wybiera sam `_fix` (jak przepływ)."""
        review = None
        for round_no in range(1, count + 1):
            review = self.step(f"r-{round_no}", "review", reviewer, round_no)
            if round_no < count:
                self.fix(review)
        return review

    def fix(self, review):
        self.engine._fix(CARD, self.engine.journal.state(CARD), review)
        selected, kw = self.launched[-1]
        self.step(f"f-{review['round']}", "fix", selected.name, review["round"],
                  **({"executorReason": kw["executor_reason"]} if kw.get("executor_reason") else {}))
        return selected.name, kw.get("executor_reason")

    def next_reviewer(self):
        assigned, previous = self.engine._reviewer_context(self.engine.journal.state(CARD), TICKET)
        return previous.get("executor") or assigned.get("reviewer")

    # --- scenariusze -----------------------------------------------------------------------------

    def test_strong_ticket_round_three_fix_stays_with_the_author_not_the_reviewer(self):
        # Odtworzenie PT-261/PT-277: autor Opus, recenzent Codex Astra, klasa mocny.
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        review = self.rounds("Codex Astra", 3)
        name, reason = self.fix(review)
        self.assertEqual((name, reason), ("Opus", "author"))
        self.assertNotEqual(name, self.next_reviewer())

    def test_standard_ticket_escalates_to_a_strong_executor_other_than_the_reviewer(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        review = self.rounds("Codex standard", 3)
        name, reason = self.fix(review)
        self.assertEqual((name, reason), ("Opus", "escalated"))
        self.assertIn("mocny", EXECUTORS[name].classes)
        self.assertNotEqual(name, self.next_reviewer())

    def test_missing_class_in_a_six_column_plan_and_od_razu_counts_as_standard(self):
        for klass in ("", None):
            with self.subTest(klass=klass):
                self.setUp()
                self.card("Codex standard", "Sonnet", klass=klass, preferred="openai")
                name, reason = self.fix(self.rounds("Sonnet", 3))
                self.assertEqual((name, reason), ("Codex mocny", "escalated"))

    def test_lokalny_class_never_escalates(self):
        self.card("Sonnet", "Haiku", klass="lokalny", preferred="anthropic")
        self.assertEqual(self.fix(self.rounds("Haiku", 3)), ("Sonnet", "author"))

    def test_only_strong_executor_is_the_reviewer_so_the_author_fixes_without_error(self):
        # Domyślna para Anthropic: Sonnet pisze, Opus recenzuje — jedyny mocny Anthropic to recenzent.
        self.card("Sonnet", "Opus", klass="standard", preferred="anthropic")
        for count in (3, 4):
            review = self.rounds("Opus", count) if count == 3 else self.step("r-4", "review", "Opus", 4)
            name, reason = self.fix(review)
            self.assertEqual((name, reason), ("Sonnet", "author"))
            self.assertNotEqual(name, self.next_reviewer())

    def test_only_strong_executor_is_the_author_so_the_author_fixes(self):
        # Ticket standard z autorem Codex mocny (ręczny wybór): mocny OpenAI to sam autor.
        self.card("Codex mocny", "Codex standard", klass="standard", preferred="openai")
        self.assertEqual(self.fix(self.rounds("Codex standard", 3)), ("Codex mocny", "author"))

    def test_manual_author_change_wins_over_escalation(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        review = self.rounds("Codex standard", 3)
        self.add("author.reassigned", ticket=TICKET, executor="Haiku")
        name, reason = self.fix(review)
        self.assertEqual((name, reason), ("Haiku", "override"))
        self.assertNotEqual(name, self.next_reviewer())

    def test_rounds_before_escalation_stay_with_the_author(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        self.rounds("Codex standard", 3)
        reasons = [(e["executor"], e.get("executorReason")) for e in self.events
                   if e["type"] == "step.started" and e["kind"] == "fix"]
        self.assertEqual(reasons, [("Sonnet", "author"), ("Sonnet", "author")])

    def test_escalation_skips_a_new_reviewer_chosen_after_the_review(self):
        # Ręczna zmiana recenzenta na Opus: następne review zrobi Opus, więc poprawek nie pisze Opus.
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        review = self.rounds("Codex standard", 3)
        o = EXECUTORS["Opus"]
        self.add("reviewer.reassigned", ticket=TICKET, reviewer="Opus", reviewerProgram=o.program,
                 reviewerModel=o.model, reviewerProvider=o.provider)
        name, reason = self.fix(review)
        self.assertEqual((name, reason), ("Sonnet", "author"))
        self.assertEqual(self.next_reviewer(), "Opus")

    def test_old_history_where_the_reviewer_fixed_returns_the_work_to_the_author(self):
        # Dziennik sprzed poprawki: poprawki rundy 3 pisał recenzent (bez executorReason).
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.rounds("Codex Astra", 2)
        self.step("f-3", "fix", "Codex Astra", 3)
        review = self.step("r-4", "review", "Codex Astra", 4)
        self.assertEqual(self.fix(review), ("Opus", "author"))

    def test_later_rounds_keep_the_escalated_fixer_and_the_author_branch_keeps_the_original_author(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        self.fix(self.rounds("Codex standard", 3))
        review = self.step("r-4", "review", "Codex standard", 4)
        self.assertEqual(self.fix(review), ("Opus", "escalated"))

    def test_catalog_change_of_the_author_still_stops_the_author_branch(self):
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        review = self.rounds("Codex Astra", 3)
        # Krok autora zapisał inny model niż dziś w katalogu.
        for e in self.events:
            if e["type"] == "step.started" and e["kind"] in ("ticket", "fix"):
                e["model"] = "opus-stary"
        with self.assertRaises(FlowError) as ctx:
            self.engine._fix(CARD, self.engine.journal.state(CARD), review)
        self.assertEqual(ctx.exception.code, "changed-executor")

    def test_fix_never_runs_on_the_next_reviewer_across_scenarios(self):
        scenarios = [("Opus", "Codex Astra", "mocny", "mixed"), ("Sonnet", "Codex standard", "standard", "anthropic"),
                     ("Sonnet", "Opus", "standard", "anthropic"), ("Codex standard", "Sonnet", "", "openai"),
                     ("Codex standard", "Codex mocny", "standard", "openai")]
        for author, reviewer, klass, preferred in scenarios:
            with self.subTest(author=author, reviewer=reviewer):
                self.setUp()
                self.card(author, reviewer, klass=klass, preferred=preferred)
                review = self.rounds(reviewer, 3)
                name, _ = self.fix(review)
                fixes = [e["executor"] for e in self.events if e["type"] == "step.started" and e["kind"] == "fix"]
                self.assertNotIn(reviewer, fixes)
                self.assertNotEqual(name, self.next_reviewer())


class ResumeReasonTest(unittest.TestCase):
    def test_resumed_fix_keeps_its_reason_and_a_new_executor_is_an_override(self):
        reason = engine_mod.FlowEngine._resumed_fix_reason
        self.assertEqual(reason({"kind": "fix", "executorReason": "escalated"}, False), "escalated")
        self.assertEqual(reason({"kind": "fix", "executorReason": "author"}, False), "author")
        self.assertEqual(reason({"kind": "fix", "executorReason": "escalated"}, True), "override")
        self.assertEqual(reason({"kind": "fix"}, False), "")
        self.assertEqual(reason({"kind": "ticket"}, True), "")

    def test_review_question_threshold_is_unchanged(self):
        self.assertEqual((engine_mod.REVIEW_ROUNDS, engine_mod.ESCALATE_FIX_ROUND), (6, 3))


if __name__ == "__main__":
    unittest.main()
