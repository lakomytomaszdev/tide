"""Decyzje użytkownika po kroku w tle i ponowienia na prawdziwych atrapach procesu."""
import json
import unittest
import os
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess


class StepDecisionTest(FlowInProcess):
    def decide(self, card_id, **request):
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def test_outside_command_runs_on_consent_and_its_output_resumes_the_step(self):
        # 03.11: krok prosi o polecenie, którego piaskownica nie wykona (sieć, Docker, pełne testy).
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        flow = card["derived"]["flow"]
        self.assertEqual((flow["action"], flow["command"]), ("outside", "echo zewnetrzne-ok"))
        self.assertIn("sieć", flow["reason"])
        self.assertEqual(self.decide(card_id, choice="allow")["error"], "no-step-question")
        self.assertTrue(self.decide(card_id, choice="run")["ok"])
        self.assertTrue(wait_until(lambda: "poza piaskownicą" in str(self.shown(card_id)["derived"].get("text"))
                                   or self.of(card_id, "outside.finished"), timeout=10))
        self.wait_column(card_id, "review", timeout=30)
        finished = self.of(card_id, "outside.finished")
        self.assertEqual((finished[0]["exit"], finished[0]["tail"].strip()), (0, "zewnetrzne-ok"))
        resumed = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"][1]
        with open(os.path.join(resumed["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn("uruchomił poza piaskownicą", prompt)
        self.assertIn("zewnetrzne-ok", prompt)

    def test_remembered_outside_command_runs_without_asking_next_time(self):
        first = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(first)["ok"])
        self.wait_column(first, "waiting")
        self.assertTrue(self.decide(first, choice="run-always")["ok"])
        self.wait_column(first, "review", timeout=30)
        second = self.card(description="Drugi eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(second)["ok"])
        self.wait_column(second, "review", timeout=40)
        decision = self.of(second, "step.decision")[0]
        self.assertEqual((decision["choice"], decision["auto"]), ("run", True))

    def test_remembered_family_and_one_outside_run_per_project(self):
        # 03.11, K-31: „pamiętaj” pamiętało dokładne polecenie, a pięć przebiegów e2e szło naraz.
        from tide_daemon.board.commands import outside_allowed, outside_matches, outside_pattern
        self.assertEqual(outside_pattern("E2E_PORT=4311 npm run e2e -- tests/e2e/devices"), "npm run e2e *")
        self.assertEqual(outside_pattern("npx playwright test tests/e2e/programs"), "npx playwright test *")
        self.assertEqual(outside_pattern("npm install --save-dev eslint@^9"), "npm install *")
        self.assertTrue(outside_matches("npm run e2e -- tests/e2e/x --workers=2", ["npm run e2e *"]))
        self.assertFalse(outside_matches("npm run lint", ["npm run e2e *"]))
        self.assertTrue(outside_allowed("E2E_PORT=4311 npm run e2e", []))
        for bad in ("PATH=/tmp npm test", "NODE_OPTIONS=--require=x npm test", "npm test && rm -rf x", "make install"):
            self.assertFalse(outside_allowed(bad, ["make install"]), bad)
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        self.assertEqual(self.shown(card_id)["derived"]["flow"]["pattern"], "echo zewnetrzne-ok *")
        self.flow.stop()
        engine = self.flow.engine
        engine._outside_active = {"K-99-PT-9-1": (card_id, "tide-outside-K-99-PT-9-1")}
        with mock.patch.object(engine, "_outside_exit", return_value="/nonexistent/in/exit-code"):
            self.assertTrue(engine._outside_busy(card_id))
        engine._outside_active = {}
        self.assertFalse(engine._outside_busy(card_id))
        # Po restarcie usługi polecenie w toku (outside.started bez zakończenia) nie może wywrócić przebiegu karty.
        engine._outside_active = None
        step = {"step": "K-1-PT-1-9", "ticket": "PT-1", "stepDir": self.base}
        engine.journal.append(card_id, "outside.started", step="K-1-PT-1-9", ticket="PT-1", command="echo x",
                              unit="tide-outside-K-1-PT-1-9", log="/nonexistent", exitFile=os.path.join(self.base, "in", "exit-code"))
        self.assertIsNone(engine._outside_ready(card_id, step, {"command": "echo x"}, {"choice": "run"}))

    def test_auto_consent_runs_safe_commands_and_asks_only_for_risky_ones(self):
        # 03.11: „nie mam ochoty co 2 minuty klikać zgody” — zwykłe pojedyncze polecenia Tide zatwierdza sam.
        from tide_daemon.board.commands import outside_risky
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 15.0, "autoConsent": True}, fh)
        for safe in ("npm run e2e -- tests/x", "E2E_PORT=4311 npx playwright test", "dotnet test Tests.csproj"):
            self.assertEqual(outside_risky(safe), "", safe)
        for risky in ("git push origin master", "npm publish", "rm -rf build", "npm install /home/x/pkg",
                      "cat ../../.ssh/id_rsa", "curl https://x"):
            self.assertTrue(outside_risky(risky), risky)
        outside = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(outside)["ok"])
        self.wait_column(outside, "review", timeout=40)
        self.assertEqual([(d["choice"], d.get("auto")) for d in self.of(outside, "step.decision")], [("run", True)])
        # Prośba zgłoszona przy ręcznych zgodach zostaje zatwierdzona po ich włączeniu.
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 15.0, "autoConsent": False}, fh)
        waiting = self.card(description="Dodaj eksport.\n\nATRAPA: zgoda")
        self.assertTrue(self.start(waiting)["ok"])
        self.wait_column(waiting, "waiting")
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 15.0, "autoConsent": True}, fh)
        self.wait_column(waiting, "review", timeout=40)
        self.assertEqual([(d["choice"], d.get("auto")) for d in self.of(waiting, "step.decision")], [("allow", True)])

    def test_declined_outside_command_resumes_without_it(self):
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        self.assertTrue(self.decide(card_id, choice="deny")["ok"])
        self.wait_column(card_id, "review", timeout=30)
        self.assertEqual(self.of(card_id, "outside.started"), [])

    def test_expired_login_asks_at_once_without_burning_the_retry(self):
        # 03.11 07:58: wygasłe logowanie Claude wyglądało jak „exit” i zużywało automatyczne ponowienie.
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: login")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        finished = self.of(card_id, "step.finished")
        self.assertEqual([(f["result"], f["reason"]) for f in finished], [("error", "login")])
        self.assertEqual(self.of(card_id, "step.retry"), [])
        self.assertEqual(card["derived"]["flow"]["action"], "error")
        self.assertIn("zaloguj się", card["derived"]["note"])
        self.assertTrue(self.decide(card_id, choice="retry")["ok"])
        self.wait_column(card_id, "review")

    def test_leftover_files_send_the_step_back_once_to_clean_up(self):
        # K-35/PT-132: autor zostawił nieśledzony plik diagnostyczny i karta stała z flow.error.
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: smieci")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "review")
        cleanup = self.of(card_id, "step.cleanup")
        self.assertEqual(len(cleanup), 1)
        tickets = [s for s in self.of(card_id, "step.started") if s["kind"] == "ticket"]
        self.assertEqual(tickets[1]["parentStep"], cleanup[0]["step"])
        with open(os.path.join(tickets[1]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn("worktree ticketu ma niezacommitowane", json.load(fh)["prompt"])
        self.assertEqual(self.of(card_id, "flow.error"), [])

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
        self.assertEqual(card["derived"]["flow"]["action"], "answer")
        self.assertEqual(card["derived"]["flow"]["question"], "Który format daty?")
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
        self.assertEqual((card["derived"]["flow"]["action"], card["derived"]["flow"]["command"]),
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

    def test_too_long_command_is_denied_with_advice_to_split_it(self):
        # 03.11, K-31/DPT-5: `find` z 61 plikami odmówiony z mylącym powodem „łańcuch z &&”.
        card_id = self.card()
        self.flow.stop()
        engine = self.flow.engine
        engine.journal.append(card_id, "step.decision", step="s0", ticket="PT-1", kind="ticket", choice="run",
                              command="echo x", auto=True)
        engine._auto_deny(card_id, "s1", {"ticket": "PT-1", "kind": "ticket"}, "rm " + "plik.ts " * 60)
        decision = self.of(card_id, "step.decision")[-1]
        self.assertEqual((decision["choice"], decision["step"]), ("deny", "s1"))
        self.assertIn("dłuższe niż 300 znaków", decision["answer"])
        for n in (2, 3):
            engine._auto_deny(card_id, f"s{n}", {"ticket": "PT-1", "kind": "ticket"}, "a && b")
        self.assertEqual(len([d for d in self.of(card_id, "step.decision") if d.get("choice") == "deny"]), 3,
                         "zapamiętane uruchomienie poza piaskownicą nie zużywa limitu odmów")

    def test_forbidden_command_cannot_be_granted_but_denial_resumes(self):
        changed = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "deniedCommands": ["make install"]})))
        self.assertTrue(changed["ok"], changed)
        card_id = self.card(description="Test.\n\nATRAPA: zgoda-zabroniona")
        self.assertTrue(self.start(card_id)["ok"])
        # K-35: na polecenie, na które nie da się zezwolić, Tide sam odmawia i wznawia krok z prośbą o pojedyncze
        # polecenia. Atrapa uparcie powtarza prośbę, więc po limicie automatycznych odmów decyzja wraca do użytkownika.
        card = self.wait_column(card_id, "waiting")
        self.assertFalse(card["derived"]["flow"]["canAllow"])
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
        for mode in ("zgoda-zabroniona-cytat", "zgoda-zabroniona-ukosnik", "zgoda-zabroniona-potok"):
            with self.subTest(mode=mode):
                card_id = self.card(description=f"Test.\n\nATRAPA: {mode}")
                self.assertTrue(self.start(card_id)["ok"])
                card = self.wait_column(card_id, "waiting")
                self.assertFalse(card["derived"]["flow"]["canAllow"])
                self.assertTrue(self.of(card_id, "step.decision")[0]["auto"])
                self.assertEqual(self.decide(card_id, choice="allow")["error"], "forbidden-command")
                self.assertTrue(self.decide(card_id, choice="deny")["ok"])
                self.wait_column(card_id, "review")

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
        self.assertEqual(card["derived"]["flow"]["command"], command)
        self.assertFalse(card["derived"]["flow"]["canAllow"])
        self.assertEqual(self.of(card_id, "step.decision")[0]["command"], command)
        self.assertEqual(self.decide(card_id, choice="allow")["error"], "command-too-long")
        self.assertTrue(self.decide(card_id, choice="deny")["ok"])
        self.wait_column(card_id, "review")

    def test_cli_permission_denial_without_result_becomes_permission_question(self):
        card_id = self.card(description="Test.\n\nATRAPA: cli-zgoda")
        self.assertTrue(self.start(card_id)["ok"])
        card = self.wait_column(card_id, "waiting")
        self.assertEqual(card["derived"]["flow"]["action"], "permission")
        self.assertEqual(card["derived"]["flow"]["command"], "git fetch origin")
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
        self.assertEqual(card["derived"]["flow"]["action"], "error")
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
        self.assertEqual(card["derived"]["flow"]["action"], "error")
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
        self.assertEqual(card["derived"]["flow"]["action"], "error")
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
        self.assertEqual(card["derived"]["flow"]["action"], "error")
        self.assertEqual([s["provider"] for s in self.of(card_id, "step.started")], ["local", "local"])
        self.assertEqual([e["reason"] for e in self.of(card_id, "step.finished")],
                         ["awaria testowa", "local-unavailable"])


class ReviewerRuleTest(unittest.TestCase):
    def test_manual_executor_change_releases_provider_rule_but_not_model_rule(self):
        # 03.11, K-16/LEGAL-66: Codex padł, użytkownik wybrał Sonnet, poprawki zrobił Opus — karta stała na
        # „katalog zmienił recenzenta”, a użytkownik nie miał czego wybrać.
        from types import SimpleNamespace
        from tide_daemon.cardflow.engine import reviewer_changed
        sonnet = SimpleNamespace(tool="claude", model="sonnet", provider="anthropic", effort="", route=None, local=False)
        expected = {"program": "claude", "model": "sonnet", "provider": "anthropic", "effort": "",
                    "routeUrl": "", "routeKind": "", "routeTokenFile": ""}
        mixed = {"preferredProvider": "mixed"}
        self.assertTrue(reviewer_changed(sonnet, expected, {"model": "opus", "provider": "anthropic"}, mixed, False))
        self.assertFalse(reviewer_changed(sonnet, expected, {"model": "opus", "provider": "anthropic"}, mixed, True))
        self.assertTrue(reviewer_changed(sonnet, expected, {"model": "sonnet", "provider": "anthropic"}, mixed, True),
                        "ten sam model co autor jest odnotowany (notka), ale nie blokuje review")


class ReviewRoundsTest(FlowInProcess):
    def test_default_rounds_before_question_and_escalation(self):
        # 03.11: po 3 rundach karta stawała i czekała na „Ponów poprawki”; teraz domyślnie 6, a od 3. rundy
        # poprawki robi wykonawca recenzji.
        from tide_daemon.cardflow import engine as engine_mod
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"stepGuardMinutes": 15.0}, fh)
        self.assertEqual(self.flow.engine._review_rounds(), engine_mod.REVIEW_ROUNDS)
        self.assertEqual((engine_mod.REVIEW_ROUNDS, engine_mod.ESCALATE_FIX_ROUND), (6, 3))
