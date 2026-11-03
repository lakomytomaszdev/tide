"""Decyzje użytkownika po kroku w tle i ponowienia na prawdziwych atrapach procesu."""
import json
import os

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class StepDecisionTest(FlowInProcess):
    def decide(self, card_id, **request):
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def test_decision_can_name_its_step_and_only_a_waiting_one(self):
        # K-15: przy dwóch pytaniach naraz odpowiedź trafiała do najnowszego kroku, a nie do zamierzonego.
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        asked = self.of(card_id, "step.finished")[0]["step"]
        wrong = self.decide(card_id, choice="answer", answer="x", step="K-999-PT-1-1")
        self.assertEqual(wrong["error"], "not-waiting")
        self.assertEqual(self.of(card_id, "step.decision"), [])
        right = self.decide(card_id, choice="answer", answer="ISO 8601", step=asked)
        self.assertTrue(right["ok"], right)
        self.assertEqual(right["step"], asked)
        self.wait_column(card_id, "review")

    def test_question_answer_resumes_same_ticket_worktree_and_session(self):
        notices = []
        self.flow.notifier = notices.append
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        started = self.start(card_id)
        self.assertTrue(started["ok"], started)
        card = self.wait_column(card_id, "waiting")
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["kind"], "question")
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["technical"]["question"], "Który format daty?")
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0]["card"], notices[0]["result"]), (card_id, "question"))
        self.flow.tick()
        self.assertEqual(len(notices), 1)
        blocked = self.start(card_id)
        self.assertEqual(blocked["error"], "flow-active")

        answer = self.decide(card_id, choice="answer", answer="ISO 8601")
        self.assertTrue(answer["ok"], answer)
        self.wait_column(card_id, "review")
        steps = self.of(card_id, "step.started")
        first, resumed = steps[0], steps[1]
        self.assertEqual((resumed["kind"], resumed["ticket"], resumed["worktree"], resumed["session"]),
                         (first["kind"], first["ticket"], first["worktree"], first["session"]))
        self.assertEqual(resumed["parentStep"], first["step"])
        with open(os.path.join(resumed["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn("--resume", call["argv"])
        self.assertIn("Odpowiedź użytkownika: ISO 8601", call["prompt"])
        self.assertEqual([e["choice"] for e in self.of(card_id, "step.decision")], ["answer"])
        self.assertEqual(len([e for e in self.of(card_id, "step.notice") if e["result"] == "question"]), 1)

    def test_question_notification_is_once_across_ticks_and_restart(self):
        notices = []
        self.flow.notifier = notices.append
        card_id = self.card(description="Test.\n\nATRAPA: pytanie")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        self.assertEqual(len(notices), 1)
        self.flow.tick()
        self.restart()
        self.flow.notifier = notices.append
        self.flow.tick()
        self.assertTrue(wait_until(lambda: len(self.of(card_id, "step.notice")) == 1, timeout=5))
        self.assertEqual(len(notices), 1)

    def test_grant_is_exact_and_persists_for_next_step_of_project(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: zgoda")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertEqual((card["derived"]["flow"]["decisions"][0]["kind"], card["derived"]["flow"]["decisions"][0]["technical"]["command"]),
                         ("permission", "git fetch origin"))
        self.assertTrue(self.decide(card_id, choice="allow")["ok"])
        self.wait_column(card_id, "review")
        project = self.store.load().projects["proj"]
        self.assertIn("git fetch origin", project.allowed_commands)
        self.assertNotIn("git", project.allowed_commands)
        self.assertEqual(self.of(card_id, "step.decision")[0]["command"], "git fetch origin")
        resumed = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"][1]
        with open(os.path.join(resumed["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        self.assertIn("Bash(git fetch origin)", call["argv"])
        self.assertNotIn("Bash(git fetch *)", call["argv"])
        with open(os.path.join(resumed["stepDir"], "in", "settings.json"), encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertFalse(settings["sandbox"]["autoAllowBashIfSandboxed"])
        next_card = self.card(description="Jeszcze jeden eksport.\n\nATRAPA: zgoda")
        self.assertTrue(self.start(next_card)["ok"])
        self.wait_column(next_card, "review")
        self.assertEqual([e["result"] for e in self.of(next_card, "step.finished") if e["ticket"] != ""],
                         ["done", "done"])

    def test_forbidden_command_cannot_be_granted_but_denial_resumes(self):
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)
        card_id = self.card(description="Test.\n\nATRAPA: zgoda-zabroniona")
        self.assertTrue(self.start(card_id)["ok"])
        # K-35: na polecenie, na które nie da się zezwolić, Tide sam odmawia i wznawia krok z prośbą o pojedyncze
        # polecenia. Atrapa uparcie powtarza prośbę, więc po limicie automatycznych odmów decyzja wraca do użytkownika.
        card = self.wait_column(card_id, "waiting")
        self.assertFalse(card["derived"]["flow"]["decisions"][0]["technical"]["canAllow"])
        auto = [d for d in self.of(card_id, "step.decision") if d.get("auto")]
        self.assertEqual([(d["choice"], d["auto"]) for d in auto], [("deny", True)] * 3)
        resumed = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"][1]
        with open(os.path.join(resumed["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn("Uruchamiaj polecenia pojedynczo", json.load(fh)["prompt"])
        self.assertEqual(self.decide(card_id, choice="allow")["error"], "forbidden-command")
        self.assertTrue(self.decide(card_id, choice="deny")["ok"])
        self.wait_column(card_id, "review")
        self.assertNotIn("make install", self.store.load().projects["proj"].allowed_commands)

    def test_quoted_escaped_and_piped_forbidden_commands_cannot_be_granted(self):
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)
        for mode in ("zgoda-zabroniona-cytat", "zgoda-zabroniona-ukosnik"):
            with self.subTest(mode=mode):
                card_id = self.card(description=f"Test.\n\nATRAPA: {mode}")
                self.assertTrue(self.start(card_id)["ok"])
                card = self.wait_column(card_id, "waiting")
                self.assertFalse(card["derived"]["flow"]["decisions"][0]["technical"]["canAllow"])
                self.assertTrue(self.of(card_id, "step.decision")[0]["auto"])
                self.assertEqual(self.decide(card_id, choice="allow")["error"], "forbidden-command")
                self.assertTrue(self.decide(card_id, choice="deny")["ok"])
                self.wait_column(card_id, "review")

    def test_piped_forbidden_command_is_split_and_only_the_rest_can_be_granted(self):
        # PT-124: łańcuch z poleceniem, na które można zezwolić, czeka na użytkownika (bez automatycznej odmowy);
        # zabronione z łańcucha nie przechodzi ani w całości, ani wskazane indeksem.
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)
        card_id = self.card(description="Test.\n\nATRAPA: zgoda-zabroniona-potok")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertFalse(card["derived"]["flow"]["decisions"][0]["technical"]["canAllow"])
        self.assertEqual(self.of(card_id, "step.decision"), [])
        [decision] = card["derived"]["flow"]["decisions"]
        self.assertEqual([(c["text"], c["forbidden"]) for c in decision["commands"]],
                         [("pwd", False), ("make install", True)])
        self.assertEqual(self.decide(card_id, choice="allow")["error"], "forbidden-command")
        self.assertEqual(self.decide(card_id, choice="allow", allow=[1])["error"], "forbidden-command")
        self.assertTrue(self.decide(card_id, choice="deny")["ok"])
        self.assertNotIn("make install", self.store.load().projects["proj"].allowed_commands)

    def test_empty_allowlist_disables_claude_auto_allow(self):
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "allowedCommands": []})))
        self.assertTrue(changed["ok"], changed)
        card_id = self.card(description="Test.\n\nATRAPA: zgoda")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        step = self.of(card_id, "step.started")[0]
        with open(os.path.join(step["stepDir"], "in", "settings.json"), encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertFalse(settings["sandbox"]["autoAllowBashIfSandboxed"])

    def test_long_command_is_shown_in_full_but_cannot_be_granted(self):
        card_id = self.card(description="Test.\n\nATRAPA: zgoda-dluga")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        command = "git fetch origin " + "x" * 300
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["technical"]["command"], command)
        self.assertFalse(card["derived"]["flow"]["decisions"][0]["technical"]["canAllow"])
        self.assertEqual(self.of(card_id, "step.decision")[0]["command"], command)
        self.assertEqual(self.decide(card_id, choice="allow")["error"], "command-too-long")
        self.assertTrue(self.decide(card_id, choice="deny")["ok"])
        self.wait_column(card_id, "review")

    def test_cli_permission_denial_without_result_becomes_permission_question(self):
        card_id = self.card(description="Test.\n\nATRAPA: cli-zgoda")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["kind"], "permission")
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["technical"]["command"], "git fetch origin")
        self.assertEqual([e["result"] for e in self.of(card_id, "step.finished")], ["denied"])

    def test_first_error_retries_once_and_then_proceeds(self):
        card_id = self.card(description="Test.\n\nATRAPA: blad-raz")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "review")
        attempts = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"]
        self.assertEqual([s.get("attempt") for s in attempts], [0, 1])
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)
        self.assertEqual([e["result"] for e in self.of(card_id, "step.finished")][:2], ["error", "done"])

    def test_second_error_waits_and_manual_retry_keeps_worktree(self):
        card_id = self.card(description="Test.\n\nATRAPA: blad-dwa")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["kind"], "error")
        first_two = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"]
        self.assertEqual([s.get("attempt") for s in first_two], [0, 1])
        self.restart()
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)
        self.assertTrue(self.decide(card_id, choice="retry")["ok"])
        self.wait_column(card_id, "review")
        attempts = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"]
        self.assertEqual([s.get("attempt") for s in attempts], [0, 1, 0])
        self.assertEqual(len({s["worktree"] for s in attempts}), 1)
        self.assertEqual(len(self.of(card_id, "step.retry")), 1)

    def test_change_executor_after_second_error_uses_same_worktree(self):
        card_id = self.card(description="Test.\n\nATRAPA: blad-zmien")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        self.assertTrue(self.decide(card_id, choice="change-executor", executor="Haiku")["ok"])
        self.wait_column(card_id, "review")
        attempts = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"]
        self.assertEqual([s["executor"] for s in attempts], ["Sonnet", "Sonnet", "Haiku"])
        self.assertEqual(len({s["worktree"] for s in attempts}), 1)

    def test_change_executor_is_kept_for_review_fixes(self):
        card_id = self.card(description="Test.\n\nATRAPA: blad-zmien\nATRAPA: review-requests 1")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        self.assertTrue(self.decide(card_id, choice="change-executor", executor="Haiku")["ok"])
        self.wait_column(card_id, "review")
        steps = self.of(card_id, "step.started")
        self.assertEqual([(s["kind"], s["executor"]) for s in steps],
                         [("ticket", "Sonnet"), ("ticket", "Sonnet"), ("ticket", "Haiku"),
                          ("review", "Opus"), ("fix", "Haiku"), ("review", "Opus"),
                          ("close", "Sonnet")])

    def test_change_reviewer_keeps_author_model_separate_and_persists(self):
        card_id = self.card(description="Test.\n\nATRAPA: review-error\nATRAPA: review-requests 1")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertEqual(card["derived"]["flow"]["stepKind"], "review")
        self.assertEqual(self.decide(card_id, choice="change-executor", executor="Sonnet")["error"], "bad-executor")
        self.assertTrue(self.decide(card_id, choice="change-executor", executor="Haiku")["ok"])
        self.wait_column(card_id, "review")
        self.assertEqual([(s["kind"], s["executor"]) for s in self.of(card_id, "step.started")],
                         [("ticket", "Sonnet"), ("review", "Opus"), ("review", "Opus"),
                          ("review", "Haiku"), ("fix", "Sonnet"), ("review", "Haiku"),
                          ("close", "Sonnet")])

    def test_timeout_stops_both_attempts_and_preserves_worktree(self):
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 15, "stepTimeoutMinutes": 0.02}, fh)
        card_id = self.card(description="Test.\n\nATRAPA: czekaj")
        started = self.start(card_id)
        self.assertTrue(started["ok"], started)
        card = self.wait_column(card_id, "waiting", timeout=30)
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["kind"], "error")
        finished = self.of(card_id, "step.finished")
        self.assertEqual([(e["result"], e["reason"]) for e in finished],
                         [("error", "timeout"), ("error", "timeout")])
        self.assertTrue(all(e["wip"]["stopped"] for e in finished))
        self.assertEqual(len({s["worktree"] for s in self.of(card_id, "step.started")}), 1)

    def test_no_progress_reports_since_after_one_automatic_retry(self):
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 0.02, "stepTimeoutMinutes": 15}, fh)
        card_id = self.card(description="Test.\n\nATRAPA: czekaj")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting", timeout=30)
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["kind"], "error")
        self.assertTrue(card["derived"]["flow"]["stalledSince"])
        self.assertIn("od ", card["derived"]["note"])
        self.assertEqual([e["reason"] for e in self.of(card_id, "step.finished")],
                         ["no-progress", "no-progress"])

    def test_local_offline_on_retry_waits_without_cloud_fallback(self):
        from tide_daemon.models import config as models_config
        path = os.path.join(self.base, "config", "tide", "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"agent": "local:qwen3-coder:30b"}, "agentProgram": "codex",
                       "executors": [{"name": "Lokalny", "provider": "local", "classes": ["lokalny"]},
                                     {"name": "Lokalny recenzent", "provider": "local", "program": "codex",
                                      "model": "qwen2.5-coder:7b", "endpoint": "local", "classes": ["lokalny"]}]}, fh)
        self.flow.engine.models_config = lambda: models_config.load(path)
        self.flow.engine.model_probe = lambda endpoint: {"online": True, "kind": "ollama",
            "models": [{"name": "qwen3-coder:30b"}, {"name": "qwen2.5-coder:7b"}]}
        card_id = self.card(description="Test.\n\nATRAPA: blad-local")
        self.assertTrue(self.start(card_id, executor="Lokalny", preferredProvider="local")["ok"])
        self.started(card_id)
        self.flow.engine.model_probe = lambda endpoint: {"online": False, "error": "offline", "models": []}
        card = self.wait_column(card_id, "waiting", timeout=30)
        self.assertEqual(card["derived"]["flow"]["decisions"][0]["kind"], "error")
        self.assertEqual([s["provider"] for s in self.of(card_id, "step.started")], ["local", "local"])
        self.assertEqual([e["reason"] for e in self.of(card_id, "step.finished")],
                         ["awaria testowa", "local-unavailable"])


class StepOptionsTest(FlowInProcess):
    """PT-126: pytanie kroku z `options[]` — opcje w decyzji, wybór w `resume_context`, zły format bez błędu."""

    decide = StepDecisionTest.decide

    def ask(self, mode):
        card_id = self.card(description=f"Dodaj eksport.\n\nATRAPA: {mode}")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        return card_id, card["derived"]["flow"]["decisions"][0]

    def test_valid_options_reach_decision_and_choice_resumes_with_title_and_effect(self):
        card_id, decision = self.ask("pytanie-opcje")
        self.assertEqual([(o["title"], o["recommended"]) for o in decision["options"]],
                         [("ISO 8601", True), ("Lokalny", False)])
        self.assertTrue(decision["freeText"])
        option = decision["options"][0]
        answer = self.decide(card_id, **json.loads(json.dumps(option["params"])))
        self.assertTrue(answer["ok"], answer)
        self.wait_column(card_id, "review")
        resumed = self.of(card_id, "step.started")[1]
        with open(os.path.join(resumed["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn("Odpowiedź użytkownika: ISO 8601", prompt)
        self.assertIn("Skutek opcji: Daty w UTC, bez stref.", prompt)

    def test_invalid_options_leave_only_the_answer_field_and_no_step_error(self):
        card_id, decision = self.ask("pytanie-zle-opcje")
        self.assertEqual(decision["options"], [])
        self.assertTrue(decision["freeText"])
        self.assertEqual(self.of(card_id, "step.finished")[0]["result"], "question")
