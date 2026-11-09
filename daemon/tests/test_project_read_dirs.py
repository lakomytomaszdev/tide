"""PT-246: trwały kontrakt projektu i profile wszystkich wejść do kroku (bez modeli)."""
import json
import os
from types import SimpleNamespace

from .test_board_core import BoardCase
from .test_agentcmd import BackgroundBase
from tide_daemon.board.model import BoardError
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks import agentcmd, readaccess


class ProjectReadDirsTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.ws, "dane HUD")
        os.makedirs(self.data)
        self.alias = os.path.join(self.ws, "alias")
        os.symlink(self.data, self.alias)

    def test_old_project_roundtrip_normalization_dedup_and_durable_removal(self):
        self.store.upsert_project({"name": "HUD"})
        self.assertEqual(self.store.snapshot()["projects"][0]["readDirs"], [])
        self.store.upsert_project({"slug": "hud", "readDirs": [self.alias, self.data + "/../dane HUD"]})
        self.assertEqual(self.store.snapshot()["projects"][0]["readDirs"], [self.data])
        project = self.store.load().projects["hud"]
        self.assertEqual(readaccess.project_dirs([project]), [self.data])
        self.assertTrue(project.read_dir_bindings)
        self.store.upsert_project({"slug": "hud", "readDirs": []})
        self.assertEqual(self.store.load().projects["hud"].read_dirs, [])
        self.assertEqual(self.store.load().projects["hud"].read_dir_bindings, [])
        self.assertEqual(self.store.snapshot()["projects"][0]["readDirs"], [])

    def test_changed_alias_and_disappeared_directory_refuse_launch_without_granting_new_target(self):
        self.store.upsert_project({"name": "HUD", "readDirs": [self.alias]})
        other = os.path.join(self.ws, "other")
        os.makedirs(other)
        os.unlink(self.alias)
        os.symlink(other, self.alias)
        with self.assertRaisesRegex(ValueError, "zmienił się cel"):
            readaccess.project_dirs([self.store.load().projects["hud"]])
        self.assertEqual(self.store.snapshot()["projects"][0]["readDirs"], [self.data])
        os.rmdir(self.data)
        with self.assertRaisesRegex(ValueError, "nie istnieje"):
            readaccess.project_dirs([self.store.load().projects["hud"]])

    def test_canonical_path_replaced_by_symlink_is_refused(self):
        self.store.upsert_project({"name": "HUD", "readDirs": [self.data]})
        other = os.path.join(self.ws, "other")
        os.makedirs(other)
        os.rmdir(self.data)
        os.symlink(other, self.data)
        with self.assertRaisesRegex(ValueError, "zmienił się cel"):
            readaccess.project_dirs([self.store.load().projects["hud"]])

    def test_limits_and_invalid_requests_are_atomic(self):
        for value in ("/etc", ["relative"], ["/"], [os.path.expanduser("~")], ["/etc"],
                      [self.data + "/missing"], [self.data] * 33, [None], [self.data + "/*"]):
            with self.subTest(value=value), self.assertRaises(BoardError):
                self.store.upsert_project({"name": "HUD", "readDirs": value})
            self.assertFalse(self.store.exists())
        dirs = []
        for i in range(32):
            path = os.path.join(self.ws, str(i))
            os.makedirs(path)
            dirs.append(path)
        self.store.upsert_project({"name": "HUD", "readDirs": dirs})
        self.assertEqual(self.store.snapshot()["projects"][0]["readDirs"], dirs)

    def test_home_expansion_secrets_and_writable_overlap_in_both_directions(self):
        home = os.path.join(self.tmp, "home")
        cache = os.path.join(home, ".cache", "hud")
        os.makedirs(cache)
        secret = os.path.join(home, ".ssh")
        os.makedirs(secret)
        alias = os.path.join(self.ws, "secret-alias")
        os.symlink(secret, alias)
        self.assertEqual(readaccess.normalize(["~/.cache/hud", cache], home=home)[0], [cache])
        for folder in (home, secret, alias, os.path.dirname(home)):
            with self.assertRaises(ValueError):
                readaccess.normalize([folder], home=home)
        for writable in (cache, os.path.dirname(cache), os.path.join(cache, "nested")):
            with self.assertRaisesRegex(ValueError, "obszar zapisu"):
                readaccess.normalize([cache], home=home, writable=[writable])
        repo = self.repo("repo")
        with self.assertRaises(BoardError):
            self.store.upsert_project({"repo": repo, "readDirs": [repo]})

    def test_broken_manual_configuration_never_falls_back_to_an_empty_grant(self):
        self.store.upsert_project({"name": "HUD"})
        self.write("projects/hud.md", '---\nname: HUD\nread-dirs: {broken\n---\n')
        project = self.store.load().projects["hud"]
        with self.assertRaisesRegex(ValueError, "readDirs"):
            readaccess.project_dirs([project])

    def test_project_scope_and_fresh_settings_for_each_launch(self):
        for name in ("main", "extra", "unrelated"):
            self.store.upsert_project({"repo": self.repo(name)})
        self.store.upsert_project({"slug": "extra", "readDirs": [self.data]})
        engine = FlowEngine(self.store)
        card = SimpleNamespace(projects=[])
        main = self.store.load().projects["main"]
        args = (card, main, os.path.join(self.tmp, "wt"), os.path.join(self.tmp, "step"))
        self.assertEqual(engine._configured_reads(*args), [])
        card.projects = ["extra"]
        self.assertEqual(engine._configured_reads(*args), [self.data])
        self.store.upsert_project({"slug": "extra", "readDirs": []})
        self.assertEqual(engine._configured_reads(*args), [])
        self.store.upsert_project({"slug": "extra", "readDirs": [self.data]})
        order = [{"slug": "extra", "repo": self.store.load().projects["extra"].repo, "main": False}]
        card.projects = []
        self.assertEqual(engine._configured_reads(*args, order=order), [self.data])
        order[0]["repo"] = main.repo
        self.assertEqual(engine._configured_reads(*args, order=order), [])
        with self.assertRaisesRegex(FlowError, "obszar zapisu"):
            engine._configured_reads(SimpleNamespace(projects=["extra"]), main, self.data, args[-1])

    def test_start_result_recovery_preserves_read_profile_and_old_journals(self):
        for stored, expected in ((True, True), (False, False), (None, False)):
            with self.subTest(stored=stored):
                card = f"K-{1 if stored is True else 2 if stored is False else 3}"
                journal = Journal(self.root, mirror=os.path.join(self.tmp, "mirror"))
                engine = FlowEngine(self.store, journal=journal)
                journal.append(card, "card.started", project="proj", repo=self.ws, executor="Sonnet", program="claude")
                journal.append(card, "intent", action="step-start", key="step-1", ticket="PT-1",
                               worktree=self.data, branch="ticket/pt-1", repo=self.ws)
                journal.append(card, "result", action="step-start", key="step-1", ok=True,
                               **({"readDirsProfile": stored} if stored is not None else {}))
                engine._recover_started_steps(card, fold(journal.read(card)))
                recovered = fold(journal.read(card)).steps["step-1"]
                self.assertIs(recovered.get("readDirsProfile"), expected)
                self.assertEqual(engine._used_read_profile(card, self.data), expected)
                before = journal.read(card)
                engine._recover_started_steps(card, fold(before))
                self.assertEqual(journal.read(card), before)


class ReadOnlyProfileTest(BackgroundBase):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.home, "dane HUD")
        os.makedirs(self.data)

    def test_claude_initial_resume_and_interactive_share_hard_write_denials(self):
        for resume in (False, True):
            step = self.step(read_dirs=(self.data,), resume=resume)
            background = agentcmd.background(step)
            fs = self.settings(background)["sandbox"]["filesystem"]
            self.assertIn(self.data, fs["allowRead"])
            self.assertIn(self.data, fs["denyWrite"])
            self.assertNotIn(self.data, fs["allowWrite"])
            command = agentcmd.restricted_interactive(step)
            with open(os.path.join(self.step_dir, "in", "settings.json")) as fh:
                self.assertEqual(json.load(fh)["sandbox"]["filesystem"], fs)
            self.assertIn(f"Edit(/{self.data}/**)", command.argv)
            self.assertNotIn("-p", command.argv)
            self.assertIn("--resume" if resume else "--session-id", command.argv)

    def test_codex_initial_resume_and_interactive_never_grant_data_write_or_tmp_write(self):
        for resume in (False, True):
            step = self.step(tool="codex", read_dirs=(self.data,), codex_mcp=(), resume=resume)
            background = agentcmd.background(step)
            interactive = agentcmd.restricted_interactive(step)
            for argv in (background.argv, interactive.argv):
                self.assertNotIn("--add-dir", argv)
                roots = json.loads(next(a.split("=", 1)[1] for a in argv
                                        if a.startswith("sandbox_workspace_write.writable_roots=")))
                self.assertNotIn(self.data, roots)
                self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
                self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true", argv)
                self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
            self.assertNotIn("exec", interactive.argv)
            self.assertEqual("resume" in interactive.argv, resume)

    def test_existing_process_profile_is_a_snapshot(self):
        first = agentcmd.background(self.step(read_dirs=(self.data,)))
        second = agentcmd.background(self.step(read_dirs=()))
        self.assertIn(self.data, self.settings(first)["sandbox"]["filesystem"]["denyWrite"])
        self.assertNotIn(self.data, self.settings(second)["sandbox"]["filesystem"]["allowRead"])

    def test_restricted_console_keeps_screenshots_and_previous_output_read_only(self):
        image = os.path.join(self.step_dir, "in", "images", "1.png")
        previous = os.path.join(self.home, "previous", "out")
        for tool in ("claude", "codex"):
            cmd = agentcmd.restricted_interactive(self.step(tool=tool, read_dirs=(self.data,),
                context_dirs=(previous,), codex_mcp=()), images=[image])
            if tool == "claude":
                self.assertIn(image, cmd.argv[-1])
                with open(os.path.join(self.step_dir, "in", "settings.json")) as fh:
                    fs = json.load(fh)["sandbox"]["filesystem"]
                self.assertIn(previous, fs["denyWrite"])
                self.assertIn(os.path.dirname(image), fs["denyWrite"])
            else:
                self.assertEqual(cmd.argv[cmd.argv.index("--image") + 1], image)
