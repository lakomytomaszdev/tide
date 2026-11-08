"""PT-149: rozmowa korzysta z kontraktu, strażnika i idempotencji akcji opiekuna w tle."""
import json
import os
from types import SimpleNamespace
from unittest import mock

from tide_daemon.board.model import Project
from tide_daemon.cardflow import caretaker, execution, history
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks import agentcmd, launch
from tide_daemon.tasks.core import TaskFiles

from .test_cardflow_caretaker import Journal
from .test_cardflow_caretaker_retry import EngineOnJournal, chain, PROBLEM


class CaretakerChatTest(EngineOnJournal):
    def setUp(self):
        super().setUp()
        self.engine.tmp_root = os.path.join(self.base, "tmp")
        self.engine.task_files = TaskFiles(os.path.join(self.base, "tasks"))
        for event in chain(Journal()).events:
            self.add("K-1", event["type"], **{k: v for k, v in event.items() if k not in ("type", "ts", "seq")})
        self.worktree = os.path.join(self.base, "card")
        self.git_dir = os.path.join(self.base, "git-dir")
        os.makedirs(self.worktree)
        os.makedirs(self.git_dir)
        with open(os.path.join(self.git_dir, "HEAD"), "w") as fh:
            fh.write("ref: refs/heads/card/k-1\n")
        self.add("K-1", "result", action="card-branch", key="card", ok=True,
                 branch="card/k-1", worktree=self.worktree)
        item = SimpleNamespace(id="K-1", projects=[], title="Eksport")
        project = Project(slug="proj", repo=self.base, name="Projekt")
        self.engine._project = mock.Mock(return_value=(item, project))
        self.engine.terminal = mock.Mock()
        self.engine.terminal.open.return_value = "tab"
        self.engine.git.sha = mock.Mock(return_value="a" * 40)
        self.engine._caretaker_setup = mock.Mock(return_value={"reason": "", "note": "", "enabled": True,
                                                              "executor": "Sonnet", "limit": 3})
        for patcher in (
            mock.patch.object(agentcmd, "resolve_git_dir", return_value=self.git_dir),
            mock.patch.object(execution, "resolve", return_value=execution.Execution("Sonnet", "claude", "/bin/claude", "sonnet", "anthropic")),
            mock.patch.object(self.engine, "_console_ended", return_value=False),
            mock.patch.object(self.engine, "freeze_unit", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.name = self.engine.talk_to_caretaker("K-1", {"problem": PROBLEM})["step"]
        self.step = self.st.caretakers[self.name]

    @property
    def st(self):
        return fold(self.journal.read("K-1"))

    def write_action(self, n=1, **changes):
        path = os.path.join(self.step["stepDir"], "out", f"action-{n}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"result": "done", "action": "retry", "step": "K-1-PT-7-2",
                       "instruction": "Popraw datę.", "reason": "Format daty.", **changes}, fh)
        return path

    def poll(self):
        self.engine._poll_interactive("K-1", self.st.caretakers[self.name])

    def test_open_has_context_chat_brief_and_the_background_sandbox(self):
        with open(os.path.join(self.step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            brief = fh.read()
        self.assertIn("Tryb rozmowy", brief)
        self.assertIn("action-<n>.json", brief)
        self.assertNotIn("Nie zadawaj pytań", brief)
        self.assertTrue(os.path.isdir(os.path.join(self.step["stepDir"], "in", "context")))
        with open(self.step["taskFile"], encoding="utf-8") as fh:
            task = json.load(fh)
        self.assertEqual(task["workdir"], os.path.join(self.step["stepDir"], "out"))
        self.assertTrue(task["cleanEnv"])
        env = launch.agent_env(task, {"SSH_AUTH_SOCK": "secret", "MY_TOKEN": "secret"})
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertNotIn("MY_TOKEN", env)
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], agentcmd.DEAD_BUS)
        self.assertNotIn("-p", task["argv"])
        self.assertEqual(task["argv"][task["argv"].index("--permission-prompts") + 1], "none")
        self.assertEqual(task["argv"][task["argv"].index("--permission-mode") + 1], "dontAsk")
        self.assertIn(f"Write(/{task['workdir']}/**)", task["argv"])
        with open(os.path.join(self.step["stepDir"], "in", "settings.json")) as fh:
            sandbox = json.load(fh)["sandbox"]
        self.assertEqual(sandbox["filesystem"]["allowWrite"], [task["workdir"]])
        self.assertTrue(sandbox["failIfUnavailable"])
        shown = self.engine.derived({"id": "K-1", "column": "todo"})
        self.assertTrue(shown["flow"]["canTalkToCaretaker"])
        self.assertEqual(shown["flow"]["running"][0]["kindLabel"], "rozmowa z opiekunem")

    def test_duplicate_finished_card_and_no_terminal(self):
        with self.assertRaises(FlowError) as err:
            self.engine.talk_to_caretaker("K-1", {})
        self.assertEqual(err.exception.code, "console-open")
        self.add("K-1", "step.console_closed", step=self.name)
        self.engine.terminal.available.return_value = False
        with self.assertRaises(FlowError) as err:
            self.engine.talk_to_caretaker("K-1", {})
        self.assertEqual(err.exception.code, "no-terminal")
        self.add("K-1", "card.finished", column="done")
        with self.assertRaises(FlowError) as err:
            self.engine.talk_to_caretaker("K-1", {})
        self.assertEqual(err.exception.code, "no-step")
        self.assertFalse(self.engine.derived({"id": "K-1", "column": "todo"})["flow"]["canTalkToCaretaker"])

    def test_prepared_caretaker_chat_reads_project_dirs_and_writes_only_out(self):
        self.add("K-1", "step.console_closed", step=self.name)
        data = os.path.join(self.base, "dane")
        os.makedirs(data)
        project = self.engine._project.return_value[1]
        project.read_dirs = [data]

        name = self.engine.talk_to_caretaker("K-1", {"problem": PROBLEM})["step"]
        step = self.st.caretakers[name]
        with open(step["taskFile"], encoding="utf-8") as fh:
            task = json.load(fh)
        with open(os.path.join(step["stepDir"], "in", "settings.json"), encoding="utf-8") as fh:
            fs = json.load(fh)["sandbox"]["filesystem"]
        self.assertIn(data, fs["allowRead"])
        self.assertIn(data, fs["denyWrite"])
        self.assertIn(["--add-dir", data], [task["argv"][i:i + 2] for i in range(len(task["argv"]) - 1)])
        out = os.path.join(step["stepDir"], "out")
        self.assertEqual(fs["allowWrite"], [out])
        self.assertEqual(task["workdir"], out)
        self.assertTrue(task["cleanEnv"])
        self.assertIn(f"Write(/{out}/**)", task["argv"])
        self.assertIn(f"Edit(/{data}/**)", task["argv"])

    def test_codex_interactive_keeps_out_only_and_disables_commands_mcp_and_temp_writes(self):
        directory = self.worktree + ".codex-chat.step"
        bg = agentcmd.caretaker_interactive(agentcmd.Step(
            tool="codex", binary="/bin/codex", prompt="Rozmowa", worktree=self.worktree, repo=self.base,
            step_dir=directory, step_id="K-1-chat-codex", model="test-model", codex_mcp=("test_server",),
            tmp_root=self.engine.tmp_root))
        out = os.path.join(directory, "out")
        self.assertEqual(bg.workdir, out)
        self.assertNotIn("exec", bg.argv)
        self.assertNotIn("--json", bg.argv)
        self.assertEqual(bg.argv[-2:], ["--", "Rozmowa"])
        self.assertIn("sandbox_workspace_write.writable_roots=" + json.dumps([out]), bg.argv)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", bg.argv)
        self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true", bg.argv)
        self.assertIn("mcp_servers.test_server.enabled=false", bg.argv)
        self.assertIn('approval_policy="never"', bg.argv)
        for feature in agentcmd.CODEX_NO_COMMANDS:
            self.assertIn(f"features.{feature}=false", bg.argv)

    def test_codex_chat_receives_inline_context(self):
        self.add("K-1", "step.console_closed", step=self.name)
        selected = execution.Execution("Codex mocny", "codex", "/bin/codex", "test-model", "openai")
        with mock.patch.object(execution, "resolve", return_value=selected), \
                mock.patch.object(agentcmd, "list_codex_mcp", return_value=[]):
            reply = self.engine.talk_to_caretaker("K-1", {"problem": PROBLEM})
        step = self.st.caretakers[reply["step"]]
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertIn("Kontekst jest w treści", prompt)
        self.assertIn('"type": "step.finished"', prompt)
        self.assertIn("120 kB", prompt)
        self.assertIn("Tryb rozmowy", prompt)
        self.assertIn("action-<n>.json", prompt)

    def test_failed_console_open_releases_the_chat_for_another_attempt(self):
        self.engine._finish_caretaker_chat("K-1", self.step)
        self.engine.terminal.open.return_value = ""
        with self.assertRaises(FlowError) as err:
            self.engine.talk_to_caretaker("K-1", {"problem": PROBLEM})
        self.assertEqual(err.exception.code, "no-terminal")
        self.assertTrue(all(s.get("consoleClosed") for s in self.st.caretakers.values()))
        self.engine.terminal.open.return_value = "tab"
        self.assertTrue(self.engine.talk_to_caretaker("K-1", {"problem": PROBLEM})["ok"])

    def test_restart_before_entered_recovers_the_prepared_console(self):
        events = self.journal.read("K-1")
        cut = next(i for i, e in enumerate(events) if e["type"] == "step.entered")
        # Restart ma widzieć ten sam prefiks w trwałej historii, nie tylko
        # w przekazanej projekcji, którą kontrola procesu ponownie odczytuje.
        for path in (self.journal.path("K-1"), self.journal.mirror_path("K-1")):
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(event) + "\n" for event in events[:cut])
        before_entered = fold(self.journal.read_complete("K-1"))
        step = before_entered.caretakers[self.name]
        self.assertEqual(step["mode"], "interactive")
        self.assertTrue(step["taskFile"] and step["consoleUnit"])
        self.engine.terminal.open.reset_mock()
        self.engine._recover_console_openings("K-1", before_entered)
        self.engine.terminal.open.assert_called_once()
        self.assertEqual(self.engine.terminal.open.call_args.args[0].task_file, step["taskFile"])
        self.assertEqual([e["seq"] for e in self.journal.read_complete("K-1")],
                         list(range(1, cut + 2)))

    def test_claimed_task_without_launcher_identity_cannot_be_declared_stopped(self):
        os.rename(self.step["taskFile"], self.step["taskFile"] + ".claimed")
        with mock.patch.object(launch, "process_alive", return_value=None), \
                mock.patch("tide_daemon.cardflow.caretakerchat.stepstop.terminate") as terminate:
            self.assertFalse(self.engine._stop_caretaker_chat(self.step))
        terminate.assert_not_called()

    def test_retry_once_after_restart_and_no_limit_cost(self):
        self.write_action()
        self.poll()
        self.poll()
        restarted = FlowEngine(self.engine.store, self.journal, tmp_root=self.base)
        restarted.git = self.engine.git
        restarted._console_ended = lambda _: False
        restarted._poll_interactive("K-1", self.step)
        events = self.journal.read("K-1")
        self.assertEqual(len([e for e in events if e["type"] == "step.decision"]), 1)
        self.assertEqual(len([e for e in events if e["type"] == "intent" and e.get("action") == "caretaker-action"]), 1)
        self.assertEqual(len([e for e in events if e["type"] == caretaker.ACTION]), 1)
        self.assertEqual(caretaker.owned(self.st, PROBLEM)["count"], 0)
        texts = [h["text"] for h in history.build(self.st, []) if h["kind"] == "caretaker"]
        self.assertEqual(texts, ["Opiekun (rozmowa): ponowił PT-7 — Format daty."])

    def test_retry_of_a_planning_step_is_not_blocked_by_chat(self):
        # Osobna karta poziomu planowania, bez ticketu — ten sam pusty ticket co rozmowa.
        self.add("K-2", "card.started", entry="plan", executor="Sonnet", provider="anthropic", column="todo")
        self.add("K-2", "step.started", step="K-2-plan", kind="planning", ticket="", attempt=1)
        self.add("K-2", "step.finished", step="K-2-plan", result="error", reason="test")
        self.add("K-2", "step.started", step="K-2-chat", kind="caretaker-chat", ticket="", mode="interactive")
        reply = self.engine.resolve_step("K-2", {"choice": "retry", "step": "K-2-plan", "answer": "Popraw plan."},
                                         caretaker_problem="error:K-2-plan", caretaker_step="K-2-chat-action-1")
        self.assertTrue(reply["ok"])

    def test_chat_escalation_before_background_run_closes_on_user_retry_and_replays(self):
        # Druga próba mogła skończyć się podczas pauzy, zanim ruszył opiekun w tle.
        self.engine._finish_caretaker_chat("K-1", self.step)
        self.add("K-1", "card.paused")
        self.name = self.engine.talk_to_caretaker("K-1", {"problem": PROBLEM})["step"]
        self.step = self.st.caretakers[self.name]
        self.write_action(action="escalate", summary="Potrzebna decyzja.", recommendation="Ponów krok.")
        self.poll()
        self.assertFalse(self.st.caretaker_runs)
        before = caretaker.owned(self.st, PROBLEM)
        self.assertEqual((before["count"], before["runs"], before["handed"], before["closedSeq"]),
                         (0, [], True, 0))

        # Decyzja w innym łańcuchu tego samego ticketu nie zamyka tej eskalacji.
        self.add("K-1", "step.started", step="unrelated", kind="ticket", ticket="PT-7")
        self.add("K-1", "step.finished", step="unrelated", result="done")
        self.add("K-1", "step.decision", step="unrelated", choice="retry")
        self.assertTrue(caretaker.owned(self.st, PROBLEM)["handed"])

        self.engine.resolve_step("K-1", {"choice": "retry", "step": "K-1-PT-7-2"})
        decision = self.st.step_decisions["K-1-PT-7-2"]
        self.assertFalse(caretaker.owned(self.st, PROBLEM)["handed"])
        self.engine._finish_caretaker_chat("K-1", self.step)
        self.add("K-1", "card.resumed")
        self.add("K-1", "step.started", step="K-1-PT-7-3", kind="ticket", ticket="PT-7",
                 parentStep="K-1-PT-7-2", attempt=2)
        self.add("K-1", "step.finished", step="K-1-PT-7-3", ticket="PT-7", result="error", reason="test")

        # Nowy silnik odtwarza wyłącznie dziennik; rozmowa nadal nie zużywa limitu.
        restarted = FlowEngine(self.engine.store, self.journal, tmp_root=self.base)
        st = fold(restarted.journal.read("K-1"))
        own = caretaker.owned(st, PROBLEM)
        self.assertEqual((own["count"], own["runs"], own["handed"], own["closedSeq"]),
                         (0, [], False, decision["seq"]))
        self.assertIn(PROBLEM, [p["id"] for p in caretaker.problems(st)])
        with mock.patch.object(restarted, "_caretaker_setup", return_value=self.engine._caretaker_setup()), \
                mock.patch.object(restarted, "_card_column", return_value="todo"), \
                mock.patch.object(restarted, "_caretaker_launch") as launch_run:
            restarted._caretake("K-1", st)
        launch_run.assert_called_once()
        run = launch_run.call_args.args[2]
        self.assertEqual((run["problem"], run["target"], run["n"]), (PROBLEM, "K-1-PT-7-3", 1))

    def test_incomplete_file_is_not_rejected_while_it_is_still_being_written(self):
        path = os.path.join(self.step["stepDir"], "out", "action-1.json")
        with open(path, "w") as fh:
            fh.write('{"result":')
        self.poll()
        self.assertFalse(self.st.caretaker_outcomes)
        self.write_action()
        self.poll()
        self.assertTrue(self.st.caretaker_outcomes[f"{self.name}-action-1"]["ok"])

    def restart_at(self, cut):
        from tide_daemon.cardflow.engine import _CaretakerPort
        self.write_action()
        def interrupted(card, action, key, work, **fields):
            self.add(card, "intent", action=action, key=key, **fields)
            if cut == "effect":
                run = self.st.caretaker_chat_actions[key]
                _CaretakerPort(self.engine, card, run).retry(self.engine._caretaker_problem(self.st, run), fields["params"])
            raise RuntimeError("restart")
        with mock.patch.object(self.engine, "_act", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.poll()
        intent = self.st.open_intents()[0]
        self.engine._reconcile("K-1", intent)
        self.poll()
        self.assertEqual(len([e for e in self.journal.read("K-1") if e["type"] == "step.decision"]), 1)
        self.assertTrue(next(iter(self.st.caretaker_outcomes.values()))["ok"])

    def test_restart_between_intent_and_effect(self):
        self.restart_at("intent")

    def test_restart_between_effect_and_result(self):
        self.restart_at("effect")

    def test_forbidden_action_and_symlink_do_not_change_the_card(self):
        self.write_action(action="approve-plan")
        self.poll()
        self.assertFalse(self.st.step_decisions)
        outcome = next(iter(self.st.caretaker_outcomes.values()))
        self.assertEqual(outcome["code"], caretaker.BAD_ACTION)
        self.assertIn("approve-plan", outcome["error"])
        path = self.write_action(2)
        link = os.path.join(self.step["stepDir"], "out", "action-3.json")
        os.symlink(path, link)
        self.poll()
        self.assertFalse(self.st.caretaker_outcomes[f"{self.name}-action-3"].get("ok", False))

    def test_user_decision_wins_over_a_late_chat_action(self):
        self.add("K-1", "step.decision", step="K-1-PT-7-2", ticket="PT-7", choice="retry", answer="Inaczej")
        self.write_action()
        self.poll()
        outcome = self.st.caretaker_outcomes[f"{self.name}-action-1"]
        self.assertTrue(outcome["stale"])
        self.assertEqual(caretaker.owned(self.st, PROBLEM)["count"], 0)

    def test_guard_stops_chat_and_never_executes_later_actions(self):
        with open(os.path.join(self.worktree, "changed.txt"), "w") as fh:
            fh.write("zapis rozmowy")
        self.write_action()
        with mock.patch("tide_daemon.cardflow.caretakerchat.stepstop.terminate", return_value=True) as stop:
            self.poll()
        stop.assert_not_called()  # nieprzejęte zadanie anulowano atomowo przed startem launchera
        self.assertEqual(self.st.finished[self.name]["reason"], "caretaker-wrote")
        self.assertFalse(self.st.step_decisions)
        self.write_action(2)
        self.poll()
        self.assertFalse(self.st.caretaker_chat_actions)

    def test_guard_remembers_a_write_even_when_stop_failed_and_the_file_was_removed(self):
        path = os.path.join(self.worktree, "changed.txt")
        with open(path, "w") as fh:
            fh.write("zapis")
        with mock.patch.object(self.engine, "_stop_caretaker_chat", return_value=False):
            self.poll()
        self.assertNotIn(self.name, self.st.finished)
        os.unlink(path)
        self.write_action()
        with mock.patch.object(self.engine, "_stop_caretaker_chat", return_value=True):
            self.poll()
        self.assertEqual(self.st.finished[self.name]["reason"], "caretaker-wrote")
        self.assertFalse(self.st.step_decisions)
