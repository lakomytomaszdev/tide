"""K-88/PT-312: wybór wykonawcy poprawek — ręczna zmiana autora, eskalacja, dotychczasowy autor.

PT-261 i PT-277: od 3. rundy poprawki pisał recenzent i sam je potem oceniał. Testy budują dziennik karty
i wołają `_fix` z przechwyconym `_launch`; wykonawcy pochodzą z domyślnego katalogu."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from .privatebus import wait_until
from .test_cardflow import FlowInProcess

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


class _FixJournal(unittest.TestCase):
    """Dziennik karty w pamięci i silnik z przechwyconym `_launch` (bez scenariuszy)."""

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
        self.add("card.started", project="proj", entry="now", executor=author, program=a.program, model=a.model,
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

    def reassign_reviewer(self, name):
        e = EXECUTORS[name]
        self.add("reviewer.reassigned", ticket=TICKET, reviewer=name, reviewerProgram=e.program,
                 reviewerModel=e.model, reviewerProvider=e.provider)


class FixExecutorTest(_FixJournal):

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

    # --- PT-330: cel eskalacji a polityka projektu ---------------------------------------------------------

    def policy(self, value, classes=()):
        project = SimpleNamespace(repo="/r", review_independence=value, review_independence_classes=list(classes))
        self.engine._project = lambda card: (SimpleNamespace(title="T", description="D"), project)

    def test_other_family_escalation_target_is_not_from_the_reviewers_family(self):
        # Dostawca zlecenia openai, recenzent Codex: mocny wykonawca openai to ta sama rodzina co recenzent.
        self.policy("other-family")
        self.card("Sonnet", "Codex standard", klass="standard", preferred="openai")
        name, reason = self.fix(self.rounds("Codex standard", 3))
        self.assertEqual((name, reason), ("Sonnet", "author"))

    def test_free_keeps_the_escalation_rule_unchanged(self):
        for value in ("free", ""):
            with self.subTest(policy=value):
                self.setUp()
                self.policy(value)
                self.card("Sonnet", "Codex standard", klass="standard", preferred="openai")
                self.assertEqual(self.fix(self.rounds("Codex standard", 3)), ("Codex mocny", "escalated"))

    def test_other_family_escalates_to_a_strong_executor_of_another_family(self):
        self.policy("other-family")
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        name, reason = self.fix(self.rounds("Codex standard", 3))
        self.assertEqual((name, reason), ("Opus", "escalated"))
        self.assertNotEqual((EXECUTORS[name].provider, EXECUTORS[name].program), ("openai", "codex"))

    def test_family_is_compared_by_provider_and_program_not_by_name(self):
        # Recenzent Fable (claude/anthropic, bez klasy mocny): Opus ma inną nazwę, ale tę samą rodzinę.
        for value, expected in (("other-family", ("Codex standard", "author")), ("free", ("Opus", "escalated"))):
            with self.subTest(policy=value):
                self.setUp()
                self.policy(value)
                self.card("Codex standard", "Fable", klass="standard", preferred="anthropic")
                self.assertEqual(self.fix(self.rounds("Fable", 3)), expected)

    def test_family_of_a_reassigned_next_reviewer_is_avoided_too(self):
        self.policy("other-family")
        self.card("Codex standard", "Codex Astra", klass="standard", preferred="anthropic")
        review = self.rounds("Codex Astra", 3)
        # Recenzent następnej rundy zmieniony ręcznie na Fable (rodzina claude/anthropic).
        self.reassign_reviewer("Fable")
        self.assertEqual(self.fix(review), ("Codex standard", "author"))

    def test_class_list_outside_other_family_classes_keeps_free_escalation(self):
        self.policy("other-family", classes=["mocny"])
        self.card("Sonnet", "Codex standard", klass="standard", preferred="openai")
        self.assertEqual(self.fix(self.rounds("Codex standard", 3)), ("Codex mocny", "escalated"))

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

    def test_manual_author_change_keeps_winning_in_later_rounds(self):
        # Review r2, MAJOR-3: po kroku nowego autora wpis author.reassigned jest starszy, a eskalacja nie może
        # samoczynnie zastąpić wyboru użytkownika.
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        review = self.rounds("Codex standard", 3)
        self.add("author.reassigned", ticket=TICKET, executor="Haiku")
        self.assertEqual(self.fix(review), ("Haiku", "override"))
        for round_no in (4, 5):
            review = self.step(f"r-{round_no}", "review", "Codex standard", round_no)
            self.assertEqual(self.fix(review), ("Haiku", "override"))
            self.assertNotEqual("Haiku", self.next_reviewer())

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


    def test_old_reviewer_fix_after_a_reviewer_change_is_not_taken_for_the_author(self):
        # Review r1, MAJOR-2: stary krok f-3 pisał Astra (recenzent rundy 3), potem recenzentem został Sonnet.
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.rounds("Codex Astra", 2)
        self.step("r-3", "review", "Codex Astra", 3)
        self.step("f-3", "fix", "Codex Astra", 3)
        self.reassign_reviewer("Sonnet")
        review = self.step("r-4", "review", "Sonnet", 4)
        self.assertEqual(self.fix(review), ("Opus", "author"))


class ResumeFixTest(_FixJournal):
    """Rzeczywista ścieżka `_resume_step` dla kroku `fix` (review r1, MAJOR-1): kartka z katalogu kroku,
    przechwycony `_launch`, bez uruchamiania agentów."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine._process_check = lambda card, step: self.engine.journal.state(card)
        self.engine._next_step_id = lambda *args: f"resume-{len(self.launched) + 1}"

    def fix_step(self, name, executor, round_no, **extra):
        step_dir = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(step_dir, "in"))
        with open(os.path.join(step_dir, "in", "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write("Popraw ticket.")
        return self.step(name, "fix", executor, round_no, stepDir=step_dir, session=f"sess-{name}", **extra)

    def resume(self, step, choice="retry", **decision):
        state = self.engine.journal.state(CARD)
        self.engine._resume_step(CARD, state, state.steps[step["step"]], {"choice": choice, **decision})
        selected, kw = self.launched[-1]
        return selected.name, kw.get("executor_reason"), kw.get("resume_session")

    def test_retry_of_an_old_reviewer_fix_goes_back_to_the_author_with_a_fresh_session(self):
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.rounds("Codex Astra", 2)
        self.step("r-3", "review", "Codex Astra", 3)
        step = self.fix_step("f-3", "Codex Astra", 3)        # stara historia: bez executorReason
        self.assertEqual(self.resume(step), ("Opus", "author", ""))
        self.assertEqual(self.next_reviewer(), "Codex Astra")

    def test_automatic_return_to_a_changed_author_stops_with_changed_executor(self):
        # Review r2, MAJOR-2: autor Opus zapisany z innym modelem niż dziś w katalogu; stary `fix` rundy 3 pisała
        # Astra. Automatyczny powrót do autora nie jest ręczną zgodą na zmianę modelu.
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.rounds("Codex Astra", 2)
        self.step("r-3", "review", "Codex Astra", 3)
        step = self.fix_step("f-3", "Codex Astra", 3)
        for e in self.events:
            if e["type"] == "step.started" and e["kind"] in ("ticket", "fix") and e["executor"] == "Opus":
                e["model"] = "opus-stary"
        launched = len(self.launched)
        with self.assertRaises(FlowError) as ctx:
            self.resume(step)
        self.assertEqual(ctx.exception.code, "changed-executor")
        self.assertEqual(len(self.launched), launched)

    def test_retry_of_an_escalated_fix_whose_executor_became_the_reviewer_moves_to_the_author(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        self.rounds("Codex standard", 3)
        step = self.fix_step("f-3", "Opus", 3, executorReason="escalated")
        self.reassign_reviewer("Opus")
        self.assertEqual(self.resume(step), ("Sonnet", "author", ""))
        self.assertEqual(self.next_reviewer(), "Opus")

    def test_retry_of_a_valid_escalated_fix_keeps_the_executor_session_and_reason(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        self.rounds("Codex standard", 3)
        step = self.fix_step("f-3", "Opus", 3, executorReason="escalated")
        self.assertEqual(self.resume(step), ("Opus", "escalated", "sess-f-3"))

    def test_retry_of_an_old_author_fix_keeps_the_session_and_records_the_reason(self):
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.step("r-1", "review", "Codex Astra", 1)
        step = self.fix_step("f-1", "Opus", 1)
        self.assertEqual(self.resume(step), ("Opus", "author", "sess-f-1"))

    def test_explicit_executor_change_is_an_override(self):
        self.card("Sonnet", "Codex standard", klass="standard", preferred="anthropic")
        self.rounds("Codex standard", 3)
        step = self.fix_step("f-3", "Opus", 3, executorReason="escalated")
        self.assertEqual(self.resume(step, "change-executor", executor="Haiku"), ("Haiku", "override", ""))

    def test_error_retry_and_automatic_escalation_keep_fix_separate_from_next_review(self):
        self.card("Sonnet", "Opus", klass="standard", preferred="anthropic")
        self.assertEqual(self.fix(self.rounds("Opus", 3)), ("Sonnet", "author"))
        step = self.fix_step("failed-fix", "Sonnet", 3, executorReason="author")
        engine = self.engine
        # Rzeczywista pętla błędu i doboru wykonawcy; porty procesu, planowania i gita są atrapami.
        engine._settings_data = lambda: {"escalateErrors": True}
        engine._process_state = lambda card: engine.journal.state(card)
        engine._advance_limit = lambda *args: False
        engine._advance_plan_review = lambda *args: False
        engine._advance_planning = lambda *args: False
        engine._caretaker_takes_over = lambda *args: False
        engine._codex_broken_mcp = lambda *args: False
        engine.journal.append = lambda card, event, **fields: self.add(event, **fields)
        engine.git = SimpleNamespace(clean_tip=lambda *args: "abc", review_diff=lambda *args: "")
        engine._rules = lambda repo: ""

        self.add("step.finished", step=step["step"], ticket=TICKET, result="error", reason="exit", exit=1)
        engine._advance(CARD, engine.journal.state(CARD))
        selected, kw = self.launched[-1]
        self.assertEqual((selected.name, kw["executor_reason"], kw["attempt"]), ("Sonnet", "author", 1))
        self.assertIn(step["step"], engine.journal.state(CARD).step_retries)
        retry = self.fix_step("failed-retry", selected.name, 3, executorReason=kw["executor_reason"],
                              attempt=kw["attempt"], parentStep=step["step"])
        self.add("step.finished", step=retry["step"], ticket=TICKET, result="error", reason="exit", exit=1)
        engine._advance(CARD, engine.journal.state(CARD))
        decision = engine.journal.state(CARD).step_decisions[retry["step"]]
        self.assertEqual((decision["choice"], decision["executor"], decision["auto"], decision["escalated"]),
                         ("change-executor", "Opus", True, True))
        engine._advance(CARD, engine.journal.state(CARD))
        selected, kw = self.launched[-1]
        self.assertEqual((selected.name, kw["executor_reason"]), ("Sonnet", "author"))
        fixed = self.fix_step("fixed", selected.name, 3, executorReason=kw["executor_reason"],
                              parentStep=retry["step"])
        self.add("step.finished", step=fixed["step"], ticket=TICKET, result="done")
        engine._advance(CARD, engine.journal.state(CARD))
        reviewer, kw = self.launched[-1]
        self.assertEqual((reviewer.name, kw["kind"], kw["round_no"]), ("Opus", "review", 4))
        self.assertNotEqual(fixed["executor"], reviewer.name)

    def test_automatic_changes_use_fix_rules_regardless_of_source(self):
        sources = (("change-executor", {"auto": True, "escalated": True}),
                   ("change-executor", {"auto": True, "refusal": True}),
                   ("change-executor", {"caretaker": "error:f-3"}),
                   ("change-executor", {"automatic": True}), ("transfer", {"automatic": True}))
        for choice, source in sources:
            for reviewer, expected in (("Opus", ("Sonnet", "author")),
                                       ("Codex standard", ("Opus", "escalated"))):
                with self.subTest(choice=choice, source=source, reviewer=reviewer):
                    self.setUp()
                    self.card("Sonnet", reviewer, klass="standard", preferred="anthropic")
                    self.rounds(reviewer, 3)
                    step = self.fix_step("f-3", "Sonnet", 3, executorReason="author")
                    name, reason, session = self.resume(step, choice, executor=reviewer, **source)
                    self.assertEqual((name, reason), expected)
                    self.assertNotEqual(name, self.next_reviewer())
                    self.assertEqual(session, "sess-f-3" if name == "Sonnet" else "")

    def test_rejected_limit_transfer_keeps_next_review_independent_also_after_retry(self):
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.assertEqual(self.fix(self.rounds("Codex Astra", 3)), ("Opus", "author"))
        step = self.fix_step("limited-fix", "Opus", 3, executorReason="author")
        engine = self.engine
        engine.journal.append = lambda card, event, **fields: self.add(event, **fields)
        engine.get_flow_settings = lambda: {
            "limitTransfer": {"mode": "auto", "executor": "Codex standard"}}
        engine._repair_wip_due = lambda *args: {"stopped": True, "files": 0}
        engine.git = SimpleNamespace(clean_tip=lambda *args: "abc", review_diff=lambda *args: "")
        engine._rules = lambda repo: ""

        # Dwie pełne próby: propozycja transferu, wznowienie i ponowny limit tego samego fix.
        for attempt in range(2):
            self.add("step.finished", step=step["step"], ticket=TICKET, result="error", reason="limit")
            self.assertTrue(engine._advance_limit(CARD, engine.journal.state(CARD)))
            decision = engine.journal.state(CARD).step_decisions[step["step"]]
            self.assertEqual((decision["choice"], decision["automatic"]), ("transfer", True))
            self.assertTrue(engine._advance_limit(CARD, engine.journal.state(CARD)))
            fixer, kw = self.launched[-1]
            self.assertEqual((fixer.name, kw["executor_reason"]), ("Opus", "author"))
            self.assertEqual(kw["resume_session"], step["session"])
            step = self.fix_step(f"resumed-fix-{attempt}", fixer.name, 3,
                                 executorReason=kw["executor_reason"], parentStep=step["step"])

        self.add("step.finished", step=step["step"], ticket=TICKET, result="done")
        engine._review(CARD, engine.journal.state(CARD), step, 4)
        reviewer, kw = self.launched[-1]
        self.assertEqual(kw["kind"], "review")
        self.assertNotEqual(step["executor"], reviewer.name)
        self.assertEqual(reviewer.name, "Codex Astra")
        self.assertFalse(any(e["type"] == "reviewer.reassigned" for e in self.events))

    def test_limit_transfer_matching_manual_author_still_reassigns_reviewer(self):
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.rounds("Codex Astra", 3)
        step = self.fix_step("limited-fix", "Opus", 3, executorReason="author")
        self.add("author.reassigned", ticket=TICKET, executor="Codex standard")
        self.add("step.finished", step=step["step"], ticket=TICKET, result="error", reason="limit")
        engine = self.engine
        engine.journal.append = lambda card, event, **fields: self.add(event, **fields)
        engine.get_flow_settings = lambda: {
            "limitTransfer": {"mode": "auto", "executor": "Codex standard"}}
        engine._repair_wip_due = lambda *args: {"stopped": True, "files": 0}
        engine.git = SimpleNamespace(clean_tip=lambda *args: "abc", review_diff=lambda *args: "")
        engine._rules = lambda repo: ""

        self.assertTrue(engine._advance_limit(CARD, engine.journal.state(CARD)))
        self.assertEqual(self.next_reviewer(), "Opus")
        self.assertTrue(engine._advance_limit(CARD, engine.journal.state(CARD)))
        fixer, kw = self.launched[-1]
        self.assertEqual((fixer.name, kw["executor_reason"], kw["resume_session"]),
                         ("Codex standard", "override", ""))
        fixed = self.fix_step("transferred-fix", fixer.name, 3, executorReason=kw["executor_reason"],
                              parentStep=step["step"])
        self.add("step.finished", step=fixed["step"], ticket=TICKET, result="done")
        engine._review(CARD, engine.journal.state(CARD), fixed, 4)
        reviewer, kw = self.launched[-1]
        self.assertEqual((reviewer.name, kw["kind"]), ("Opus", "review"))
        self.assertNotEqual(fixer.name, reviewer.name)

    def test_automatic_change_does_not_accept_changed_author_identity(self):
        self.card("Sonnet", "Opus", klass="standard", preferred="anthropic")
        self.rounds("Opus", 3)
        step = self.fix_step("f-3", "Sonnet", 3, executorReason="author")
        step["model"] = "sonnet-stary"
        with self.assertRaises(FlowError) as ctx:
            self.resume(step, "change-executor", executor="Opus", auto=True, escalated=True)
        self.assertEqual(ctx.exception.code, "changed-executor")

    def test_manual_author_assignment_wins_over_automatic_change(self):
        self.card("Sonnet", "Opus", klass="standard", preferred="anthropic")
        self.rounds("Opus", 3)
        step = self.fix_step("f-3", "Sonnet", 3, executorReason="author")
        self.add("author.reassigned", ticket=TICKET, executor="Haiku")
        self.assertEqual(self.resume(step, "change-executor", executor="Opus", auto=True, escalated=True),
                         ("Haiku", "override", ""))
        step = self.fix_step("f-3-override", "Haiku", 3, executorReason="override")
        self.add("author.reassigned", ticket=TICKET, executor="Sonnet")
        self.assertEqual(self.resume(step, "change-executor", executor="Opus", auto=True, escalated=True),
                         ("Sonnet", "override", ""))

    def test_automatic_change_respects_class_and_round_limits(self):
        for klass, round_no in (("standard", 2), ("mocny", 3), ("lokalny", 3)):
            with self.subTest(klass=klass, round_no=round_no):
                self.setUp()
                self.card("Sonnet", "Codex standard", klass=klass, preferred="anthropic")
                self.rounds("Codex standard", round_no)
                step = self.fix_step("failed-fix", "Sonnet", round_no, executorReason="author")
                self.assertEqual(self.resume(step, "change-executor", executor="Opus", auto=True, escalated=True),
                                 ("Sonnet", "author", "sess-failed-fix"))

    def test_manual_transfer_remains_an_override(self):
        self.card("Sonnet", "Opus", klass="standard", preferred="anthropic")
        self.rounds("Opus", 3)
        step = self.fix_step("f-3", "Sonnet", 3, executorReason="author")
        self.assertEqual(self.resume(step, "transfer", executor="Codex standard"),
                         ("Codex standard", "override", ""))

    def test_automatic_resume_preserves_manual_author_provider_exception(self):
        self.card("Sonnet", "Codex Astra", preferred="mixed")
        self.rounds("Codex Astra", 3)
        step = self.fix_step("f-3", "Sonnet", 3, executorReason="author")
        self.add("author.reassigned", ticket=TICKET, executor="Codex standard")
        self.assertEqual(self.resume(step, "change-executor", executor="Opus", auto=True, escalated=True),
                         ("Codex standard", "override", ""))
        self.assertNotEqual("Codex standard", self.next_reviewer())

    def test_resumed_fix_never_runs_on_the_next_reviewer(self):
        scenarios = [("Opus", "Codex Astra", "mocny", "mixed", "Codex Astra", None),
                     ("Sonnet", "Codex standard", "standard", "anthropic", "Opus", "Opus")]
        for author, reviewer, klass, preferred, fixer, new_reviewer in scenarios:
            with self.subTest(author=author, fixer=fixer):
                self.setUp()
                self.card(author, reviewer, klass=klass, preferred=preferred)
                self.rounds(reviewer, 3)
                step = self.fix_step("f-3", fixer, 3, **({"executorReason": "escalated"} if new_reviewer else {}))
                if new_reviewer:
                    self.reassign_reviewer(new_reviewer)
                name, _, _ = self.resume(step)
                self.assertNotEqual(name, self.next_reviewer())


class ThresholdTest(unittest.TestCase):
    def test_review_question_threshold_is_unchanged(self):
        self.assertEqual((engine_mod.REVIEW_ROUNDS, engine_mod.ESCALATE_FIX_ROUND), (6, 3))


class FixFlowTest(FlowInProcess):
    def test_question_after_the_threshold_and_retry_fix_is_not_written_by_the_next_reviewer(self):
        # Przepływ z atrapami: 3 rundy REQUEST_CHANGES → review.question (próg z ustawień, 3) → „Ponów poprawki”.
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 3")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting", timeout=50)
        self.assertEqual(len(self.of(card, "review.question")), 1, self.events(card))
        replies = []
        self.board.resolve_card_review(card, "retry", replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        self.assertTrue(json.loads(replies[0])["ok"])
        self.assertTrue(wait_until(lambda: any(s["kind"] == "review" and s["round"] == 4
                                               for s in self.of(card, "step.started")), timeout=30),
                        self.events(card))
        started = self.of(card, "step.started")
        fixes = [s for s in started if s["kind"] == "fix"]
        self.assertEqual([s.get("executorReason") for s in fixes[:2]], ["author", "author"])
        fix = next(s for s in fixes if s["round"] == 3)
        review = next(s for s in started if s["kind"] == "review" and s["round"] == 4)
        self.assertIn(fix.get("executorReason"), ("author", "escalated"))
        self.assertNotEqual(fix["executor"], review["executor"])


if __name__ == "__main__":
    unittest.main()
