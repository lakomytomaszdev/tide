"""PT-156: okablowanie scenariuszy 03.11, bez pomiaru jakości diagnozy modelu.

Procesy wykonawców pochodzą z flowfakes. Akcje opiekuna są zadane przez test.
Git przygotowania review i zapisu planu jest atrapą granicy piaskownicy; gałęzie,
worktrees, procesy, kontekst, strażnik opiekuna i dziennik są rzeczywiste.
"""
import json
import os
from pathlib import Path
import time
from unittest import TestCase, expectedFailure, mock

from tide_daemon.cardflow import caretaker
from tide_daemon.cardflow.gitops import FlowGitError
from tide_daemon.cardflow.state import fold

from . import test_cardflow_fault_report as faults
from . import test_cardflow_plan_integration as plans
from .privatebus import wait_until


FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/caretaker-0311"
RETRY = {"result": "done", "action": "retry", "step": "$target",
         "instruction": "Zapisz result.json w katalogu out wskazanym w kartce, poza worktree.",
         "reason": "Dziennik pokazuje zapis wyniku w złym katalogu."}


class Scenario0311Test(faults.FaultReportCase):
    proposal = plans.PlanFlowTest.proposal
    submit = plans.PlanFlowTest.submit
    approve = plans.PlanFlowTest.approve

    def setUp(self):
        super().setUp()
        with open(self.settings, encoding="utf-8") as fh:
            settings = json.load(fh)
        settings["caretakerActionLimit"] = 2
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump(settings, fh)
        self.reply = RETRY
        self.stage_fails = False
        self.stage_calls = 0
        self.preparing_retry = False
        self.install_boundaries(self.flow.engine)
        # Odpowiedź trafia wyłącznie do atrapy procesu. Silnik dostaje prawdziwy plik wyniku.
        original = caretaker.brief

        def brief(*args, **kwargs):
            return original(*args, **kwargs) + "\nATRAPA-WYNIK: " + json.dumps(self.reply) + "\n"

        patch = mock.patch.object(caretaker, "brief", side_effect=brief)
        patch.start()
        self.addCleanup(patch.stop)

    def install_boundaries(self, engine):
        def clean_tip(repo, branch, path):
            self.stage_calls += 1
            if self.stage_fails and not (self.preparing_retry and self.reply.get("action") == "retry-stage"):
                raise FlowGitError("przygotowanie review: katalog etapu niedostępny; GITHUB_TOKEN=" + faults.TOKEN)
            return engine.git.sha(repo, branch)

        advance = engine._advance_card

        def advance_card(card, *, own=None):
            self.preparing_retry = own is not None
            try:
                return advance(card, own=own)
            finally:
                self.preparing_retry = False

        for patch in (
            mock.patch.object(engine.git, "clean_tip", side_effect=clean_tip),
            mock.patch.object(engine.git, "commit_plan", side_effect=lambda repo, branch, tree, files:
                              {"commit": engine.git.sha(repo, branch), "already": False}),
            mock.patch.object(engine, "_advance_card", side_effect=advance_card),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def wait_for(self, predicate, card):
        self.assertTrue(wait_until(predicate, timeout=45),
                        [(e["type"], e.get("kind"), e.get("action"), e.get("result"),
                          e.get("reason") or e.get("errors") or e.get("error")) for e in self.events(card)])

    def view(self, card):
        return self.flow.engine.derived({"id": card, "column": "todo"})["flow"]

    def prepare(self, case, bad=None):
        if case == "1":
            with open(self.settings, encoding="utf-8") as fh:
                settings = json.load(fh)
            settings["escalateErrors"] = True
            with open(self.settings, "w", encoding="utf-8") as fh:
                json.dump(settings, fh)
        if case in ("1", "4a", "4b"):
            self.reply = dict(RETRY)
            if case != "1":
                self.reply["report"] = {"description": "Tide nie udostępnia katalogu wyniku. GITHUB_TOKEN=" + faults.TOKEN}
            if case == "4b":
                self.reply = {"result": "done", "action": "retry-stage", "report": self.reply["report"]}
                self.stage_fails = True
            if bad:
                self.reply = bad
            mode = {"1": "wynik-worktree", "4a": "odmowa-zapisu", "4b": "gotowe"}[case]
            card = self.card(description="Scenariusz 03.11\nATRAPA: " + mode)
            self.assertTrue(self.start(card, executor="Haiku" if case == "1" else "Sonnet")["ok"])
            if case == "4b":
                self.wait_for(lambda: self.of(card, "flow.error"), card)
                self.assertGreaterEqual(self.stage_calls, 1)
                self.assertEqual(self.of(card, "flow.error-persisted"), [])
                self.flow.engine.clock = lambda: time.time() + 11
            return card

        self.reply = {"result": "done", "action": "return-plan", "reason": "Brak migracji z decyzji A.",
                      "feedback": "Dodaj ticket na migrację formatu z decyzji A; rozpoczęte tickety przepisz bez zmian."}
        if case == "2":
            self.reply.update(reason="Poprawka planu nie przeszła sprawdzania formatu.",
                              feedback="Wiersz #1: bloker — brak kryterium; błąd standardu 1.2. Uzupełnij kartkę.")
        if bad:
            self.reply = bad
        card = self.card(description="Scenariusz 03.11")
        path = self.proposal(card)
        with open(os.path.join(os.path.dirname(path), "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("Decyzja A: migracja formatu jest osobnym zadaniem przed eksportem.\n"
                     "GITHUB_TOKEN=" + faults.TOKEN + "\n")
        if case == "3":
            with open(os.path.join(os.path.dirname(path), "tickets/1.md"), "w", encoding="utf-8") as fh:
                fh.write("# Eksport\nATRAPA: blad-opiekun\nATRAPA-PLIK: eksport\n"
                         "ATRAPA-POWOD: Brak migracji z decyzji A.\n"
                         "- [ ] Eksport działa.\n## Dla recenzenta\nSprawdź migrację.\n")
        self.assertTrue(self.submit(card, path)["ok"])
        if case == "3":
            self.assertTrue(self.approve(card)["ok"])
        else:
            box = []
            self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "ATRAPA: invalid-amend"}), box.append)
            self.wait_for(lambda: box, card)
            self.assertTrue(json.loads(box[0])["ok"])
        return card

    def check_context(self, card, case):
        runs = [s for s in self.of(card, "step.started") if s["kind"] == "caretaker"]
        self.assertTrue(runs)
        for step in runs:
            folder = Path(step["stepDir"]) / "in/context"
            files = {str(p.relative_to(folder)): p.read_text() for p in folder.rglob("*") if p.is_file()}
            text = "\n".join(files.values())
            self.assertNotIn(faults.TOKEN, text)
            self.assertNotIn(faults.ENV_SECRET, text)
            self.assertIn("•••", text)
            self.assertIn("docs/agent-plan-standard.md", files)
            if case == "1":
                self.assertTrue(any("Zapisano wynik:" in body and "result.json" in body
                                    for name, body in files.items() if name.endswith("step-log.md")))
                self.assertIn("•••", text)
            elif case == "2":
                self.assertIn("plan-fix/errors.md", files)
                self.assertIn("plan-fix/failed-plan.md", files)
                self.assertIn("błędny plan", files["plan-fix/failed-plan.md"])
            elif case == "3":
                self.assertIn("plan/approved.md", files)
                self.assertIn("Decyzja A: migracja", files["plan/spec.md"])
            elif case == "4a":
                self.assertIn("odmowa Write:", text)
                self.assertIn("•••", text)
            else:
                self.assertIn("przygotowanie review: katalog etapu niedostępny", text)
                self.assertIn("•••", text)
            with open(Path(step["stepDir"]) / "out/fake-call.json", encoding="utf-8") as fh:
                call = json.load(fh)
            self.assertIn("--disallowedTools", call["argv"])
            argv = call["argv"]
            allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
            self.assertFalse([rule for rule in allowed if rule.startswith(("Write", "Edit"))])
            self.assertEqual(call["cwd"], str(Path(step["stepDir"]) / "out"))
            with open(argv[argv.index("--settings") + 1], encoding="utf-8") as fh:
                settings = json.load(fh)
            self.assertEqual(settings["sandbox"]["filesystem"]["allowWrite"], [call["cwd"]])
            self.assertTrue(self.of(card, "caretaker.guard"), "strażnik rzeczywiście działa")

    def check_forbidden(self, card, before):
        events = self.events(card)[before:]
        self.assertFalse([e for e in events if e["type"] in
                          ("plan.approved", "plan.revision-approved", "card.main-merged", "ticket.rejected")])
        self.assertFalse([e for e in events if e["type"] == "step.decision" and e.get("choice") == "allow"])
        self.assertFalse([e for e in events if e["type"] == "intent" and e.get("action") == "merge-main"])

    def good(self, case):
        card = self.prepare(case)
        before = len(self.events(card))
        if case in ("2", "3"):
            self.wait_for(lambda: len(self.of(card, "plan.reviewed")) == 2, card)
            expected = ["plan-revision"]  # przed pierwszym zatwierdzeniem zatwierdza cały plan
        else:
            self.wait_for(lambda: any(s["kind"] == "review" for s in self.of(card, "step.started")), card)
            expected = []
        self.flow.stop()
        view = self.view(card)
        self.assertEqual([d["kind"] for d in view["decisions"]], expected)
        self.assertFalse(self.of(card, "caretaker.escalated"))
        [action] = self.of(card, "caretaker.action")
        self.assertTrue(action["ok"], action)
        self.assertEqual(action["action"], {"1": "retry", "2": "return-plan", "3": "return-plan",
                                            "4a": "retry", "4b": "retry-stage"}[case])
        history = [h["text"] for h in view["history"] if h["kind"] == "caretaker"]
        self.assertTrue(history)
        self.assertTrue(all(t.startswith("Opiekun: ") for t in history))
        if case == "1":
            attempts = [s for s in self.of(card, "step.started") if s["kind"] == "ticket"]
            self.assertTrue(any(s["attempt"] == 1 for s in attempts))
            self.assertGreater(len({s["executor"] for s in attempts}), 1)
            self.assertTrue(any(e.get("instruction") for e in self.of(card, "step.retry")))
        elif case == "3":
            revision = fold(self.events(card)).plan_revision
            self.assertEqual(revision["difference"]["added"], ["#3"])
            self.assertEqual(revision["difference"]["changed"], [])
            self.assertEqual(revision["difference"]["removed"], [])
        elif case == "2":
            self.assertIsNone(fold(self.events(card)).plan_approved)
        elif case == "4b":
            self.assertEqual(len(self.of(card, "flow.error-persisted")), 1)
            self.assertEqual(len(self.of(card, "stage.retry")), 1)
            self.assertEqual(fold(self.events(card)).flow_error, "")
        if case in ("4a", "4b"):
            [reported] = self.of(card, "caretaker.reported")
            self.assertTrue(reported["ok"], reported)
            [saved] = self.tide_cards()
            self.assertEqual(saved.column, "backlog")
            self.assertNotIn(faults.TOKEN, saved.description)
            self.assertNotIn(faults.ENV_SECRET, saved.description)
        self.check_context(card, case)
        self.check_forbidden(card, before)
        return card

    def test_report_deduplicates_second_occurrence(self):
        first = self.good("4a")
        # Pierwszy scenariusz został już sprawdzony; jego późniejsze review/scalenie
        # nie należy do testu drugiego wystąpienia zgłoszenia.
        self.flow.engine.journal.append(first, "card.paused")
        self.flow = self.make_flow()
        self.install_boundaries(self.flow.engine)
        second = self.good("4a")
        [saved] = self.tide_cards()
        self.assertEqual(saved.description.count("### "), 2)
        self.assertEqual(self.of(first, "caretaker.reported")[0]["reported"], saved.id)
        self.assertEqual(self.of(second, "caretaker.reported")[0]["reported"], saved.id)
        self.assertIn("odmowa Write", saved.description)
        self.assertIn("•••", saved.description)

    def test_restart_after_retry_effect_before_action_result(self):
        engine = self.flow.engine
        resume = engine._resume_step

        def delay(card, st, step, decision, **kwargs):
            if not decision.get("caretaker"):
                return resume(card, st, step, decision, **kwargs)

        with mock.patch.object(engine, "_resume_step", side_effect=delay):
            card = self.prepare("1")
            self.wait_for(lambda: self.of(card, "caretaker.action"), card)
            self.flow.stop()
        events = self.events(card)
        cut = next(i for i, e in enumerate(events) if e["type"] in ("step.decision", "step.retry")
                   and e.get("caretaker")) + 1
        # Awaria po trwałym skutku, przed wynikiem pary caretaker-action.
        for path in (engine.journal.path(card), engine.journal.mirror_path(card)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(e) + "\n" for e in events[:cut]))
        target = events[cut - 1]["step"]
        self.flow = self.make_flow()
        self.install_boundaries(self.flow.engine)
        self.wait_for(lambda: any(s["kind"] == "review" for s in self.of(card, "step.started")), card)
        self.flow.stop()
        self.assertEqual(len(self.of(card, "caretaker.started")), 1)
        self.assertEqual(len(self.of(card, "caretaker.action")), 1)
        self.assertEqual(len(self.results(card, "caretaker-action")), 1)
        self.assertEqual(len([e for e in self.events(card) if e["type"] in ("step.decision", "step.retry")
                              and e.get("caretaker")]), 1)
        self.assertEqual(len([s for s in self.of(card, "step.started") if s.get("parentStep") == target]), 1)
        self.assertEqual(self.view(card)["decisions"], [])

    def bad(self, case, malformed=False, known_contract=False):
        answer = {"result": "question", "question": "Co zrobić?"} if malformed else {
            "result": "done", "action": "approve-plan"}
        card = self.prepare(case, answer)
        before = len(self.events(card))
        self.wait_for(lambda: self.of(card, "caretaker.escalated"), card)
        self.flow.stop()
        [decision] = self.view(card)["decisions"]
        annotation = decision["caretaker"]
        self.check_context(card, case)
        self.check_forbidden(card, before)
        self.assertEqual(decision["kind"], {"1": "error", "2": "plan-revision-failed", "3": "error",
                                           "4a": "error", "4b": "caretaker"}[case])
        self.assertTrue(annotation["summary"])
        notices = [e for e in self.events(card) if e["type"] in ("step.notice", "flow.notice")
                   and e.get("result") in ("error", "caretaker")]
        self.assertEqual(len(notices), 1)
        # Ponowne złożenie dziennika i kolejkowanie powiadomień nie tworzą duplikatów.
        for _ in range(2):
            self.flow.engine._queue_notices(card)
            self.flow.engine._caretake(card, fold(self.events(card)))
        self.assertEqual(len(self.view(card)["decisions"]), 1)
        self.assertEqual(len([e for e in self.events(card) if e["type"] in ("step.notice", "flow.notice")
                              and e.get("result") in ("error", "caretaker")]), 1)
        if known_contract:
            self.assertEqual(len(self.of(card, "caretaker.started")), 1)
            self.assertEqual(len(self.of(card, "caretaker.escalated")), 1)
            return
        # To kryterium PT-156 jest sprzeczne z kontraktem PT-143. Testy oczekiwanej
        # porażki utrwalają lukę, bez zmiany wcześniejszego kontraktu w tym tickecie.
        self.assertEqual(len(self.of(card, "caretaker.started")), 2)
        self.assertTrue(annotation["recommendation"])
        self.assertEqual(len(annotation["tried"]), 2)


def _scenario(case, bad=False, malformed=False, known_contract=False):
    def test(self):
        self.bad(case, malformed, known_contract) if bad else self.good(case)
    return expectedFailure(test) if bad and not known_contract else test


for _case in ("1", "2", "3", "4a", "4b"):
    setattr(Scenario0311Test, "test_good_" + _case, _scenario(_case))
    setattr(Scenario0311Test, "test_bad_action_" + _case, _scenario(_case, bad=True))
    setattr(Scenario0311Test, "test_bad_result_" + _case, _scenario(_case, bad=True, malformed=True))
    # Te same kontrole bezpieczeństwa muszą też przejść zwyczajnie, żeby expectedFailure
    # nie ukrywało regresji kontekstu, powiadomień lub niedozwolonych skutków.
    setattr(Scenario0311Test, "test_current_rejection_action_" + _case,
            _scenario(_case, bad=True, known_contract=True))
    setattr(Scenario0311Test, "test_current_rejection_result_" + _case,
            _scenario(_case, bad=True, malformed=True, known_contract=True))


class ManualFixturesTest(TestCase):
    def test_five_reconstructed_logs_are_masked_and_have_diagnosis_criteria(self):
        for case, action in (("1", "retry"), ("2", "return-plan"), ("3", "return-plan"),
                             ("4a", "retry"), ("4b", "retry-stage")):
            with self.subTest(case=case):
                folder = FIXTURES / case
                expected = (folder / "expected.md").read_text()
                self.assertIn("`" + action + "`", expected)
                self.assertIn("Kryterium diagnozy:", expected)
                log = (folder / "step.log").read_text()
                self.assertTrue(log.strip())
                events = [json.loads(line) for line in (folder / "journal.jsonl").read_text().splitlines() if line]
                self.assertEqual(events[0]["type"], "card.started")
                self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
                text = log + json.dumps(events) + expected
                for secret in (faults.TOKEN, faults.ENV_SECRET):
                    self.assertNotIn(secret, text)
                self.assertNotRegex(text, r"ghp_[A-Za-z0-9]{20,}|/home/[^/\s]+/")
