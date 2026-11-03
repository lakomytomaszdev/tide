"""Opiekun karty (K-37, PT-143): błąd kroku → krok opiekuna → eskalacja.

Najpierw czyste funkcje modułu `cardflow/caretaker.py` na złożonych dziennikach (id problemu, liczenie działań,
rejestr akcji, walidacja wyniku, kartka, decyzja i `derived`), potem przebiegi przez `FlowEngine` z atrapą `claude`
(tests/flowfakes.py): atrapa rozpoznaje kartkę opiekuna po pierwszej linii „Jesteś opiekunem karty”, a co ma
odpowiedzieć, mówi „ATRAPA-OPIEKUN: …” w powodzie błędu kroku ticketu (`ATRAPA: blad-opiekun`).
"""
import json
import os
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker, decisions, history
from tide_daemon.cardflow.state import derived, fold

from . import flowfakes
from .privatebus import wait_until
from .test_cardflow import FlowInProcess

CARD = "K-1"


def ev(seq, etype, /, **fields):
    return {"seq": seq, "ts": f"2026-11-03T10:{seq // 60:02d}:{seq % 60:02d}+00:00", "type": etype, **fields}


class Journal:
    """Złożony dziennik karty z ticketem PT-7 i łańcuchem prób kroku ticketu."""

    def __init__(self, provider="anthropic"):
        self.events = [ev(1, "card.started", entry="now", executor="Sonnet", program="claude", model="sonnet",
                          provider="anthropic" if provider != "local" else "local", preferredProvider=provider,
                          repo="/r", column="todo")]

    def add(self, etype, /, **fields):
        self.events.append(ev(len(self.events) + 1, etype, **fields))
        return self.events[-1]

    def step(self, name, attempt=0, parent="", kind="ticket", ticket="PT-7"):
        self.add("step.started", step=name, kind=kind, ticket=ticket, executor="Sonnet", provider="anthropic",
                 attempt=attempt, parentStep=parent, worktree="/w", branch="ticket/pt-7", stepDir="/w.step")

    def error(self, name, reason="awaria testowa", **extra):
        self.add("step.finished", step=name, ticket="PT-7", result="error", reason=reason, **extra)

    def failed_twice(self, first, second, parent=""):
        self.step(first, 0, parent)
        self.error(first)
        self.add("step.retry", step=first, ticket="PT-7")
        self.step(second, 1, first)
        self.error(second)

    def care(self, target, root, n, step, problem=None):
        problem = problem or f"error:{root}"
        return self.add(caretaker.STARTED, problem=problem, key=f"caretaker:{problem}:{n}", n=n, step=step,
                        target=target, root=root, ticket="PT-7", trigger="error", triggers=["error"],
                        watermark=len(self.events))

    def escalate(self, target, step, root, option="change-executor"):
        return self.add(caretaker.ESCALATED, problem=f"error:{root}", step=step, target=target, ticket="PT-7",
                        action="escalate", summary="Test pada na tym samym.", recommendation="Zmień wykonawcę.",
                        optionId=option, failed=False, error="", code="")

    @property
    def st(self):
        return fold(self.events)


class ProblemIdTest(unittest.TestCase):
    def test_three_errors_in_one_parent_chain_have_the_same_id_without_seq(self):
        j = Journal()
        j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
        first = caretaker.problems(j.st)
        self.assertEqual([p["id"] for p in first], ["error:K-1-PT-7-1"])
        j.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="change-executor", executor="Opus",
              auto=True, escalated=True)
        j.step("K-1-PT-7-3", 0, "K-1-PT-7-2")
        j.error("K-1-PT-7-3")
        j.add("step.retry", step="K-1-PT-7-3", ticket="PT-7")
        j.step("K-1-PT-7-4", 1, "K-1-PT-7-3")
        j.error("K-1-PT-7-4")
        third = caretaker.problems(j.st)
        self.assertEqual([(p["id"], p["step"]) for p in third], [("error:K-1-PT-7-1", "K-1-PT-7-4")])
        for seq in range(2, len(j.events) + 1):
            self.assertNotIn(f":{seq}", third[0]["id"].replace("K-1-PT-7-1", ""))
        self.assertEqual(caretaker.problem_id("error", j.st, step="K-1-PT-7-4"), "error:K-1-PT-7-1")

    def test_other_id_shapes(self):
        self.assertEqual(caretaker.problem_id("repeat", ticket="PT-7", cause="exit"), "repeat:PT-7:exit")
        self.assertEqual(caretaker.problem_id("flow-error", stage="merge"), "flow-error:merge")
        self.assertEqual(caretaker.problem_id("stalled", stage="card"), "stalled:card")
        self.assertEqual(caretaker.problem_id("waiting", decision="step:K-1-PT-7-2"), "waiting:step:K-1-PT-7-2")

    def test_user_decision_keeps_the_id_and_resets_the_count(self):
        j = Journal()
        j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        own = caretaker.owned(j.st, "error:K-1-PT-7-1")
        self.assertEqual((own["count"], own["next"], own["open"]["step"], own["handed"]), (1, 2, "K-1-care-1", False))
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", reason="", outcome={"ok": True})
        j.escalate("K-1-PT-7-2", "K-1-care-1", "K-1-PT-7-1")
        own = caretaker.owned(j.st, "error:K-1-PT-7-1")
        self.assertEqual((own["count"], own["open"], own["handed"]), (1, None, True))
        j.add("step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="retry", answer="")
        own = caretaker.owned(j.st, "error:K-1-PT-7-1")
        self.assertEqual((own["count"], own["next"], own["handed"]), (0, 2, False))
        j.failed_twice("K-1-PT-7-3", "K-1-PT-7-4", parent="K-1-PT-7-2")
        self.assertEqual([p["id"] for p in caretaker.problems(j.st)], ["error:K-1-PT-7-1"])
        self.assertEqual(caretaker.owned(j.st, "error:K-1-PT-7-1")["count"], 0)

    def test_two_triggers_on_one_chain_make_one_problem(self):
        j = Journal()
        j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")

        def repeat(st):
            return [{"trigger": "repeat", "step": "K-1-PT-7-2", "ticket": "PT-7", "cause": "exit"}]

        with mock.patch.object(caretaker, "TRIGGERS", [*caretaker.TRIGGERS, repeat]):
            found = caretaker.problems(j.st)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["id"], found[0]["triggers"]), ("error:K-1-PT-7-1", ["error", "repeat"]))

    def test_caretaker_step_is_never_a_problem_step(self):
        j = Journal()
        j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
        j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", executor="Opus", attempt=1)
        j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="error", reason="bad-caretaker-result",
              outcome={"ok": False})
        st = j.st
        self.assertNotIn("K-1-care-1", st.steps)
        self.assertEqual([p["step"] for p in caretaker.problems(st)], ["K-1-PT-7-2"])


class NoCaretakerTest(unittest.TestCase):
    def test_login_limit_and_user_fault_keep_todays_decision_without_a_problem(self):
        for reason, extra in (("login", {}), ("limit", {}), ("exit", {"fault": "user", "faultCause": "stopped"}),
                              ("exit", {"fault": "provider", "faultCause": "limit"})):
            with self.subTest(reason=reason, extra=extra):
                j = Journal()
                j.step("K-1-PT-7-1", 0 if reason == "login" else 1)
                j.error("K-1-PT-7-1", reason, **extra)
                self.assertEqual(caretaker.problems(j.st), [])
                for decision in decisions.build_decisions(j.st):
                    self.assertNotIn("caretaker", decision)

    def test_paused_backlog_done_archived_and_local_only_cards(self):
        j = Journal()
        j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
        self.assertEqual(caretaker.unavailable(j.st, "todo"), "")
        for column in ("backlog", "done", "archived"):
            self.assertTrue(caretaker.unavailable(j.st, column), column)
        j.add("card.paused")
        self.assertEqual(caretaker.unavailable(j.st, "todo"), "karta wstrzymana")
        local = Journal(provider="local")
        local.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")
        self.assertEqual(caretaker.unavailable(local.st, "todo"), caretaker.LOCAL_ONLY_NOTE)
        decision = decisions.build_decisions(local.st)[0]
        self.assertEqual(decision["kind"], "error")
        self.assertIn("pracy tylko lokalnej", decision["why"])
        self.assertNotIn("caretaker", decision)
        self.assertFalse(derived(local.st, {"column": "todo"})["flow"]["caretaker"]["enabled"])


class RegistryTest(unittest.TestCase):
    def test_only_escalate_is_registered(self):
        self.assertEqual(set(caretaker.ACTIONS), {"escalate"})
        for name in ("approve-plan", "approve", "allow", "run", "run-always", "outside", "merge", "push", "reject",
                     "reject-ticket"):
            self.assertNotIn(name, caretaker.ACTIONS)
        self.assertEqual(set(caretaker.HISTORY_LABELS), {"escalate"})
        self.assertEqual(set(caretaker.BRIEF_SECTIONS), {"error"})
        self.assertEqual([t.__name__ for t in caretaker.TRIGGERS], ["_error_trigger"])


class ParseResultTest(unittest.TestCase):
    problem = {"id": "error:K-1-PT-7-1", "trigger": "error", "step": "K-1-PT-7-2",
               "options": ["retry", "change-executor", "reject"]}

    def parse(self, data):
        return caretaker.parse_result(data, self.problem, Journal().st)

    def test_escalate(self):
        made = self.parse({"result": "done", "action": "escalate", "summary": "S", "recommendation": "R",
                           "optionId": "retry", "notes": ["note.md"]})
        self.assertEqual(made, {"ok": True, "action": "escalate",
                                "params": {"summary": "S", "recommendation": "R", "optionId": "retry"}})

    def test_actions_outside_the_registry(self):
        for name in ("approve-plan", "allow", "merge", "push", "reject"):
            with self.subTest(name=name):
                made = self.parse({"result": "done", "action": name, "summary": "S", "recommendation": "R"})
                self.assertEqual((made["ok"], made["code"], made["action"]), (False, "bad-caretaker-action", name))

    def test_missing_file_bad_format_and_other_results(self):
        self.assertEqual(self.parse(None)["code"], "bad-caretaker-result")
        self.assertEqual(self.parse("")["code"], "bad-caretaker-result")
        self.assertEqual(self.parse([1, 2])["code"], "bad-caretaker-result")
        asked = self.parse({"result": "question", "question": "Ponowić?"})
        self.assertEqual((asked["ok"], asked["code"], asked["summary"]), (False, "caretaker-question", "Ponowić?"))
        for result in ("error", "denied", "outside", "cokolwiek"):
            self.assertEqual(self.parse({"result": result, "reason": "x"})["code"], "caretaker-result", result)

    def test_field_lengths_and_option(self):
        base = {"result": "done", "action": "escalate", "summary": "S", "recommendation": "R"}
        self.assertTrue(self.parse({**base, "summary": "x" * 1200})["ok"])
        self.assertEqual(self.parse({**base, "summary": "x" * 1201})["code"], "bad-caretaker-result")
        self.assertTrue(self.parse({**base, "recommendation": "x" * 600})["ok"])
        self.assertEqual(self.parse({**base, "recommendation": "x" * 601})["code"], "bad-caretaker-result")
        self.assertEqual(self.parse({**base, "recommendation": ""})["code"], "bad-caretaker-result")
        self.assertEqual(self.parse({**base, "summary": 5})["code"], "bad-caretaker-result")
        self.assertEqual(self.parse({**base, "optionId": "merge"})["code"], "bad-caretaker-result")
        self.assertEqual(self.parse({"result": "done"})["code"], "bad-caretaker-result")

    def test_action_not_allowed_for_the_problem_kind(self):
        limited = {"only": caretaker.Action(kinds=("flow-error",), params={}, apply=None, description="x")}
        with mock.patch.dict(caretaker.ACTIONS, limited):
            made = self.parse({"result": "done", "action": "only"})
        self.assertEqual(made["code"], "bad-caretaker-action")


class DecisionAndViewTest(unittest.TestCase):
    def setUp(self):
        self.j = Journal()
        self.j.failed_twice("K-1-PT-7-1", "K-1-PT-7-2")

    def test_decision_is_hidden_while_the_caretaker_works(self):
        self.j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        self.j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", executor="Opus",
                   worktree="/w")
        st = self.j.st
        self.assertEqual(decisions.build_decisions(st), [])
        out = derived(st, {"column": "todo"})
        self.assertEqual((out["column"], out["note"]), ("progress", "opiekun sprawdza PT-7"))
        care = out["flow"]["caretaker"]
        self.assertEqual((care["enabled"], [w["step"] for w in care["working"]], care["escalated"]),
                         (True, ["K-1-care-1"], []))
        running = [r for r in out["flow"]["running"] if r["kind"] == "caretaker"]
        self.assertEqual([(r["id"], r["kindLabel"]) for r in running], [("K-1-care-1", "opiekun")])

    def test_escalation_brings_the_decision_back_with_the_recommended_option_first(self):
        self.j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        self.j.add("step.started", step="K-1-care-1", kind="caretaker", ticket="PT-7", executor="Opus")
        self.j.add("step.finished", step="K-1-care-1", ticket="PT-7", result="done", reason="",
                   outcome={"ok": True, "action": "escalate"})
        self.j.escalate("K-1-PT-7-2", "K-1-care-1", "K-1-PT-7-1")
        out = derived(self.j.st, {"column": "todo"})
        decision = out["flow"]["decisions"][0]
        self.assertEqual(decision["caretaker"], {"summary": "Test pada na tym samym.",
                                                 "recommendation": "Zmień wykonawcę.", "tried": [],
                                                 "optionId": "change-executor", "failed": False})
        self.assertEqual([(o["id"], o["recommended"]) for o in decision["options"]],
                         [("change-executor", True), ("retry", False), ("reject", False)])
        self.assertTrue(all("selected" not in o for o in decision["options"]))
        self.assertEqual(out["column"], "waiting")
        self.assertEqual([e["target"] for e in out["flow"]["caretaker"]["escalated"]], ["K-1-PT-7-2"])
        texts = [(h["text"], h["actor"]) for h in out["flow"]["history"] if h["kind"] == "caretaker"]
        self.assertEqual(texts, [("Opiekun: oddaje decyzję — Test pada na tym samym.", "caretaker")])

    def test_failed_action_is_in_tried_and_history(self):
        self.j.care("K-1-PT-7-2", "K-1-PT-7-1", 1, "K-1-care-1")
        self.j.add(caretaker.ESCALATED, problem="error:K-1-PT-7-1", step="K-1-care-1", target="K-1-PT-7-2",
                   ticket="PT-7", action="merge", summary="", recommendation="", optionId="", failed=True,
                   error="nieznana akcja opiekuna „merge”", code="bad-caretaker-action")
        decision = decisions.build_decisions(self.j.st)[0]
        self.assertTrue(decision["caretaker"]["failed"])
        self.assertEqual([t["text"] for t in decision["caretaker"]["tried"]],
                         ["Opiekun: nie udało się wykonać działania „merge” — nieznana akcja opiekuna „merge”"])
        self.assertEqual(decision["options"][0]["id"], "retry")       # bez polecenia opiekuna — jak dziś
        entries = [h for h in history.build(self.j.st, []) if h["kind"] == "caretaker"]
        self.assertEqual([h["actor"] for h in entries], ["caretaker"])

    def test_brief_lists_actions_forbids_and_masks_secrets(self):
        j = Journal()
        reason = "GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 export TIDE_X_SECRET=sekret-z-env-42"
        j.step("K-1-PT-7-1", 1)
        j.error("K-1-PT-7-1", reason)
        problem = caretaker.problems(j.st)[0]
        text = caretaker.brief(problem, j.st, card="K-1", out_dir="/w.caretaker.step/out", worktree="/w")
        self.assertTrue(text.startswith("Jesteś opiekunem karty K-1"))
        for part in ("## Dostępne akcje", "`escalate`", "`summary`", "`recommendation`", "`optionId`",
                     "retry, change-executor, reject", "## Zakazy", "Plik wyniku: /w.caretaker.step/out/result.json"):
            self.assertIn(part, text)
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", text)
        self.assertNotIn("sekret-z-env-42", text)
        self.assertIn("GITHUB_TOKEN=•••", text)


class CaretakerFlowTest(FlowInProcess):
    """Przebiegi przez FlowEngine z atrapą `claude`."""

    def failing_card(self, care="escalate", extra=""):
        return self.card(description="Eksport.\n\nATRAPA: blad-opiekun\n"
                                     f"ATRAPA-POWOD: test pada {extra}ATRAPA-OPIEKUN: {care}")

    def caretaker_steps(self, card_id):
        return [s for s in self.of(card_id, "step.started") if s["kind"] == "caretaker"]

    def test_error_starts_caretaker_hides_decision_and_escalation_notifies_once(self):
        card_id = self.failing_card("powoli")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
        card = self.wait_column(card_id, "progress")
        flow = card["derived"]["flow"]
        self.assertEqual(flow["decisions"], [])
        self.assertTrue(card["derived"]["note"].startswith("opiekun sprawdza PT-"), card["derived"]["note"])
        self.assertEqual([(r["kind"], r["kindLabel"]) for r in flow["running"]], [("caretaker", "opiekun")])
        self.assertTrue(flow["caretaker"]["enabled"])
        self.assertEqual(len(flow["caretaker"]["working"]), 1)
        self.assertEqual(self.of(card_id, "step.notice"), [])
        events = self.events(card_id)
        started = next(e for e in events if e["type"] == "caretaker.started")
        step = self.caretaker_steps(card_id)[0]
        self.assertLess(started["seq"], step["seq"])
        self.assertEqual(started["watermark"], started["seq"] - 1)
        self.assertEqual(started["step"], step["step"])
        self.assertEqual(started["key"], f"caretaker:{started['problem']}:1")
        self.assertTrue(started["problem"].startswith("error:K-"))
        self.assertEqual(step["executor"], "Opus")         # najmocniejsza klasa preferowanego dostawcy (anthropic)

        card = self.wait_column(card_id, "waiting", timeout=30)
        decision = card["derived"]["flow"]["decisions"][0]
        self.assertEqual(decision["kind"], "error")
        self.assertEqual(decision["caretaker"]["summary"], "Krok dwa razy padł na tym samym teście.")
        self.assertEqual(decision["caretaker"]["recommendation"], "Zmień wykonawcę na mocniejszy model.")
        self.assertEqual(decision["caretaker"]["tried"], [])
        self.assertEqual([(o["id"], o["recommended"]) for o in decision["options"]][:2],
                         [("change-executor", True), ("retry", False)])
        self.assertEqual(sum(o["recommended"] for o in decision["options"]), 1)
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "step.notice")) == 1, timeout=5))
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(len(self.of(card_id, "step.notice")), 1, "eskalacja powiadamia raz")
        self.assertEqual(self.of(card_id, "step.notice")[0]["step"], decision["step"])
        self.assertEqual(len(self.caretaker_steps(card_id)), 1)
        history_texts = [h["text"] for h in card["derived"]["flow"]["history"] if h["kind"] == "caretaker"]
        self.assertEqual(history_texts, ["Opiekun: oddaje decyzję — Krok dwa razy padł na tym samym teście."])
        self.assertEqual(card["derived"]["flow"]["caretaker"]["working"], [])
        self.assertEqual([e["target"] for e in card["derived"]["flow"]["caretaker"]["escalated"]],
                         [decision["step"]])

    def test_failed_caretaker_results_hand_the_decision_back(self):
        cases = {"akcja-approve-plan": "bad-caretaker-action", "akcja-allow": "bad-caretaker-action",
                 "akcja-merge": "bad-caretaker-action", "akcja-push": "bad-caretaker-action",
                 "akcja-reject": "bad-caretaker-action", "bez-wyniku": "bad-caretaker-result",
                 "zly-format": "bad-caretaker-result", "pytanie": "caretaker-question", "blad": "caretaker-result"}
        for care, code in cases.items():
            with self.subTest(care=care):
                card_id = self.failing_card(care)
                self.assertTrue(self.start(card_id)["ok"])
                card = self.wait_column(card_id, "waiting", timeout=40)
                escalated = self.of(card_id, "caretaker.escalated")
                self.assertEqual([(e["failed"], e["code"]) for e in escalated], [(True, code)])
                decision = card["derived"]["flow"]["decisions"][0]
                self.assertTrue(decision["caretaker"]["failed"])
                texts = [h["text"] for h in card["derived"]["flow"]["history"] if h["kind"] == "caretaker"]
                self.assertEqual(len(texts), 1)
                self.assertTrue(texts[0].startswith("Opiekun: nie udało się"), texts)
                self.assertIn(escalated[0]["error"], texts[0])
                self.assertEqual(len(self.caretaker_steps(card_id)), 1, "bez opiekuna dla kroku opiekuna")
                self.assertTrue(wait_until(lambda: len(self.of(card_id, "step.notice")) == 1, timeout=5))
                if care == "pytanie":
                    self.assertEqual(decision["caretaker"]["summary"], "Czy ponowić krok z inną instrukcją?")

    def test_brief_and_sandbox_of_the_caretaker_step(self):
        card_id = self.failing_card(extra="GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 "
                                          "export TIDE_FAKE_SECRET_TOKEN=atrapa-sekret-z-env-42 ")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting", timeout=40)
        step = self.caretaker_steps(card_id)[0]
        self.assertTrue(step["stepDir"].endswith(f".caretaker-{step['step']}.step"))
        out = os.path.join(step["stepDir"], "out")
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertTrue(prompt.startswith("Jesteś opiekunem karty " + card_id))
        for part in ("## Dostępne akcje", "`escalate`", "## Zakazy", f"Plik wyniku: {out}/result.json",
                     "Powód zakończenia kroku:", "GITHUB_TOKEN=•••"):
            self.assertIn(part, prompt)
        for secret in ("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "atrapa-sekret-z-env-42",
                       "Dozwolone polecenia i wzorce"):
            self.assertNotIn(secret, prompt)
        with open(os.path.join(step["stepDir"], "in", "settings.json"), encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertEqual(settings["sandbox"]["filesystem"]["allowWrite"], [out])
        self.assertFalse(settings["sandbox"]["autoAllowBashIfSandboxed"])
        with open(os.path.join(out, "fake-call.json"), encoding="utf-8") as fh:
            argv = json.load(fh)["argv"]
        self.assertNotIn("--allowedTools", argv)
        banned = argv[argv.index("--disallowedTools") + 1:]
        self.assertIn("Bash", banned)
        self.assertIn(f"Edit(/{step['worktree']}/**)", banned)
        self.assertEqual(argv[argv.index("--add-dir") + 1], out)

    def test_login_error_gives_todays_decision_without_caretaker(self):
        card_id = self.card(description="Eksport.\n\nATRAPA: login")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting", timeout=30)
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(self.caretaker_steps(card_id), [])
        self.assertEqual(self.of(card_id, "caretaker.started"), [])
        self.assertNotIn("caretaker", card["derived"]["flow"]["decisions"][0])

    def test_restart_between_caretaker_started_and_step_started_does_not_duplicate(self):
        card_id = self.failing_card("powoli")
        self.assertTrue(self.start(card_id)["ok"])
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=30), self.events(card_id))
        step = self.caretaker_steps(card_id)[0]
        pid = self.unit_pid(step)
        journal = self.flow.engine.journal
        self.flow.stop()
        wait_until(lambda: False, timeout=0.3)
        events = self.events(card_id)
        at = next(i for i, e in enumerate(events) if e["type"] == "step.started" and e["kind"] == "caretaker")
        self.assertEqual(events[at - 1]["type"], "result")
        truncated = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events[:at])
        for path in (journal.path(card_id), journal.mirror_path(card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(truncated)
        self.flow = self.make_flow()
        self.assertTrue(wait_until(lambda: self.caretaker_steps(card_id), timeout=5), self.events(card_id))
        self.assertTrue(flowfakes.alive(pid), "krok opiekuna nie wystartował drugi raz ani nie zginął")
        self.wait_column(card_id, "waiting", timeout=40)
        self.assertEqual(len(self.caretaker_steps(card_id)), 1)
        self.assertEqual(len(self.of(card_id, "caretaker.started")), 1)
        self.assertEqual(len([r for r in self.results(card_id, "step-start") if r["key"] == step["step"]]), 1)
        self.assertEqual(len(self.of(card_id, "caretaker.escalated")), 1)


if __name__ == "__main__":
    unittest.main()
