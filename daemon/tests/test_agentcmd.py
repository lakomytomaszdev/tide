"""Wspólne miejsce składania polecenia agenta (PT-76) — tasks/agentcmd.py.

- interaktywnie (Konsola): to samo, co dawało agent_argv + local.options — Claude Code i Codex × chmura,
  Ollama na tym komputerze, inny punkt z tokenem,
- w tle (`claude -p`, `codex exec`): argv i środowisko według wzoru piaskownicy z PT-75, polecenie przez
  stdin, środowisko bez agenta SSH, tokenów GitHuba i pomocników haseł, martwy D-Bus, token punktu nigdy
  w argv ani w środowisku polecenia,
- zatrzymanie kroku na atrapie programu (skrypt pisze pliki i czeka): proces kończy się, na gałęzi
  zostaje commit WIP z nowymi i zmienionymi plikami, wracają ostatnie wiadomości z atrapy dziennika.
Prawdziwy model nigdy nie jest wywoływany.
"""
import json
import os
import shutil
import tempfile
import unittest

from tide_daemon.tasks import agentcmd, local

OLLAMA = local.LocalAgent("local", "http://127.0.0.1:11434", "ollama", "qwen3-coder:30b", 65536)
MAC = local.LocalAgent("mac", "http://mac.lan:11434", "ollama", "qwen3-coder:30b", 65536, "/home/u/.tok")


class InteractiveTest(unittest.TestCase):
    def test_cloud_command_is_plain_agent_argv(self):
        cmd = agentcmd.interactive("claude", "/bin/claude", "Popraw nagłówek", ["/t/1/a.png"], ["/b/docs"])
        self.assertEqual(cmd.argv, agentcmd.agent_argv("claude", "/bin/claude", "Popraw nagłówek", ["/t/1/a.png"],
                                                       ["/b/docs"]))
        self.assertEqual((cmd.env, cmd.secret_env, cmd.unset_env, cmd.token_files), ({}, {}, [], []))

    def test_local_command_is_model_options_then_agent_argv(self):
        for tool in ("claude", "codex"):
            for agent in (OLLAMA, MAC):
                opts = local.options(tool, agent)
                cmd = agentcmd.interactive(tool, "/bin/" + tool, "zrób to", [], [], agent)
                self.assertEqual(cmd.argv, agentcmd.agent_argv(tool, "/bin/" + tool, "zrób to", [], [], opts.argv))
                self.assertEqual((cmd.env, cmd.secret_env, cmd.unset_env, cmd.token_files),
                                 (opts.env, opts.secret_env, opts.unset_env, opts.token_files))

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(agentcmd.UnknownTool):
            agentcmd.interactive("vim", "vim", "a b")


SECRET = "sekretny-token-PT76"
DIRTY_ENV = {"HOME": "/h", "USER": "u", "PATH": "/usr/bin:/bin", "LANG": "pl_PL.UTF-8", "SSH_AUTH_SOCK": "/run/ssh",
             "GH_TOKEN": "gh1", "GITHUB_TOKEN": "gh2", "GH_ENTERPRISE_TOKEN": "gh3", "GITHUB_ENTERPRISE_TOKEN": "gh4",
             "GIT_ASKPASS": "/usr/bin/ksshaskpass", "SSH_ASKPASS": "/usr/bin/ksshaskpass",
             "SSH_ASKPASS_REQUIRE": "prefer", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
             "XDG_RUNTIME_DIR": "/run/user/1000", "ANTHROPIC_API_KEY": "sk-ant-x", "TIDE_MODEL_TOKEN": SECRET}
FORBIDDEN = ("SSH_AUTH_SOCK", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
             "XDG_RUNTIME_DIR", "ANTHROPIC_API_KEY", "TIDE_MODEL_TOKEN")


class BackgroundBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-agentcmd-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.home, "proj")
        self.worktree = os.path.join(self.home, "proj-wt", "pt-1")
        self.step_dir = os.path.join(self.home, "proj-wt", "pt-1.step")
        self.tmp_root = os.path.join(self.root, "tmp")
        for folder in (self.repo, self.worktree, self.tmp_root):
            os.makedirs(folder)
        self.token_file = os.path.join(self.root, "token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(SECRET + "\n")

    def step(self, tool="claude", **kw):
        base = dict(DIRTY_ENV, HOME=self.home)
        args = dict(tool=tool, binary="/bin/" + tool, prompt="Zrób ticket", worktree=self.worktree, repo=self.repo,
                    step_dir=self.step_dir, step_id="s1", session_id="11111111-2222-3333-4444-555555555555",
                    base_env=base, tmp_root=self.tmp_root)
        args.update(kw)
        return agentcmd.Step(**args)

    def settings(self, cmd):
        return json.loads(cmd.files[os.path.join(self.step_dir, "in", "settings.json")])


class BackgroundClaudeTest(BackgroundBase):
    def test_argv_follows_the_sandbox_pattern_and_prompt_goes_through_stdin(self):
        cmd = agentcmd.background(self.step(model="haiku"))
        settings = os.path.join(self.step_dir, "in", "settings.json")
        self.assertEqual(cmd.argv, ["/bin/claude", "-p", "--model=haiku", "--setting-sources=", "--settings", settings,
                                    "--permission-prompts", "none", "--strict-mcp-config",
                                    "--output-format", "stream-json", "--verbose",
                                    "--session-id", "11111111-2222-3333-4444-555555555555"])
        self.assertEqual(cmd.stdin, "Zrób ticket")
        self.assertNotIn("Zrób ticket", " ".join(cmd.argv))
        self.assertEqual(cmd.cwd, self.worktree)
        self.assertEqual(cmd.session_id, "11111111-2222-3333-4444-555555555555")

    def test_resume_and_tool_lists(self):
        cmd = agentcmd.background(self.step(resume=True, allowed_tools=("Bash(make test*)", "Bash(git status)"),
                                            disallowed_tools=("Bash(make install*)",), permission_mode="acceptEdits"))
        self.assertIn("--resume", cmd.argv)
        self.assertNotIn("--session-id", cmd.argv)
        self.assertEqual(cmd.argv[cmd.argv.index("--allowedTools") + 1:cmd.argv.index("--allowedTools") + 3],
                         ["Bash(make test*)", "Bash(git status)"])
        self.assertEqual(cmd.argv[cmd.argv.index("--disallowedTools") + 1], "Bash(make install*)")
        self.assertEqual(cmd.argv[cmd.argv.index("--permission-mode") + 1], "acceptEdits")

    def test_resume_needs_a_session_id(self):
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step(resume=True, session_id=""))

    def test_sandbox_settings_match_the_pt75_pattern(self):
        sandbox = self.settings(agentcmd.background(self.step()))["sandbox"]
        inp, out = os.path.join(self.step_dir, "in"), os.path.join(self.step_dir, "out")
        self.assertEqual(sandbox, {
            "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {"denyRead": ["~"], "allowRead": [self.worktree, self.repo + "/.git", inp, out],
                           "allowWrite": [out]},
            "network": {"allowedDomains": []}})

    def test_step_files_exclude_sandbox_dummies_and_set_the_author(self):
        cmd = agentcmd.background(self.step(git_name="Krok", git_email="krok@tide.invalid"))
        inp = os.path.join(self.step_dir, "in")
        config = cmd.files[os.path.join(inp, "gitconfig")]
        self.assertIn("name = Krok", config)
        self.assertIn(f"excludesFile = {inp}/ignore", config)
        patterns = cmd.files[os.path.join(inp, "ignore")].split()
        self.assertEqual(len(patterns), 12)
        self.assertIn("/.bashrc", patterns)
        self.assertIn("/.mcp.json", patterns)
        self.assertEqual(cmd.env["GIT_CONFIG_GLOBAL"], os.path.join(inp, "gitconfig"))

    def test_prepare_creates_directories_and_files_and_start_runs_in_the_worktree(self):
        cmd = agentcmd.background(self.step(binary="/bin/cat"))
        cmd.argv = ["/bin/sh", "-c", "pwd; cat; env"]
        cmd.prepare()
        self.assertTrue(os.path.isdir(os.path.join(self.step_dir, "out")))
        self.assertTrue(os.path.isfile(os.path.join(self.step_dir, "in", "settings.json")))
        self.assertTrue(os.path.isdir(cmd.env["CLAUDE_CODE_TMPDIR"]))
        proc = cmd.start()
        out, _ = proc.communicate()
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(out.startswith(self.worktree + "\nZrób ticket"))
        self.assertNotIn("SSH_AUTH_SOCK", out)

    def test_directories_must_stay_out_of_tmp_and_the_temp_dir_out_of_home(self):
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step(step_dir=os.path.join(self.tmp_root, "step")))
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step(tmp_root=os.path.join(self.home, "tmp")))
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step(step_dir=os.path.join(self.home, ".local", "share", "tide", "step")))


class BackgroundEnvironmentTest(BackgroundBase):
    def check_clean(self, cmd, tool):
        env = cmd.env
        for name in FORBIDDEN:
            self.assertNotIn(name, env)
        self.assertEqual({k: env[k] for k in ("HOME", "USER", "PATH", "LANG")},
                         {"HOME": self.home, "USER": "u", "PATH": "/usr/bin:/bin", "LANG": "pl_PL.UTF-8"})
        self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["SSH_ASKPASS"], env["SSH_ASKPASS_REQUIRE"]),
                         ("0", "/bin/false", "/bin/false", "never"))
        self.assertIn("IdentityAgent=none", env["GIT_SSH_COMMAND"])
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])
        pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(pairs, {"credential.helper": "", "core.askPass": ""})
        self.assertEqual(env["GH_CONFIG_DIR"], os.path.join(self.step_dir, "in", "gh"))
        self.assertNotEqual(env["DBUS_SESSION_BUS_ADDRESS"], DIRTY_ENV["DBUS_SESSION_BUS_ADDRESS"])
        self.assertFalse(os.path.exists(env["DBUS_SESSION_BUS_ADDRESS"].partition("=")[2]))
        self.assertEqual(env.get("GIT_CONFIG_NOSYSTEM"), "1" if tool == "codex" else None)

    def test_claude_environment_is_clean(self):
        cmd = agentcmd.background(self.step())
        self.check_clean(cmd, "claude")
        self.assertEqual(cmd.env["CLAUDE_CODE_TMPDIR"], os.path.join(self.tmp_root, "tide-step-s1"))

    def test_codex_environment_is_clean(self):
        self.check_clean(agentcmd.background(self.step("codex")), "codex")

    def test_other_endpoint_token_stays_out_of_argv_and_environment(self):
        for tool in ("claude", "codex"):
            cmd = agentcmd.background(self.step(tool, local=local.LocalAgent("mac", "http://mac.lan:11434", "ollama",
                                                                           "qwen3-coder:30b", 65536, self.token_file)))
            blob = json.dumps([cmd.argv, cmd.env, cmd.stdin, cmd.files])
            self.assertNotIn(SECRET, blob, tool)
            self.assertIn(self.token_file, blob if tool == "claude" else json.dumps(cmd.secret_env))
        codex = agentcmd.background(self.step("codex", local=local.LocalAgent("mac", "http://mac.lan:11434", "ollama",
                                                                            "m", None, self.token_file)))
        self.assertEqual(codex.secret_env, {local.TOKEN_ENV: self.token_file})
        self.assertIn(f'shell_environment_policy.exclude=["{local.TOKEN_ENV}"]', codex.argv)

    def test_claude_token_goes_through_the_api_key_helper_in_the_settings_file(self):
        cmd = agentcmd.background(self.step(local=local.LocalAgent("mac", "http://mac.lan:11434", "ollama",
                                                                   "qwen3-coder:30b", 65536, self.token_file)))
        self.assertEqual(self.settings(cmd)["apiKeyHelper"], f"cat -- {self.token_file}")
        self.assertEqual(cmd.argv.count("--settings"), 1)
        self.assertEqual(cmd.env["ANTHROPIC_BASE_URL"], "http://mac.lan:11434")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", cmd.env)
        self.assertIn("--model=qwen3-coder:30b", cmd.argv)

    def test_start_gives_codex_the_token_only_in_its_own_environment(self):
        cmd = agentcmd.background(self.step("codex", local=local.LocalAgent(
            "mac", "http://mac.lan:11434", "ollama", "m", None, self.token_file)))
        cmd.argv = ["/bin/sh", "-c", "echo $TIDE_MODEL_TOKEN"]
        cmd.prepare()
        out, _ = cmd.start().communicate()
        self.assertEqual(out.strip(), SECRET)
        self.assertNotIn(local.TOKEN_ENV, cmd.env)


class BackgroundCodexTest(BackgroundBase):
    def setUp(self):
        super().setUp()
        self.gitdir = os.path.join(self.repo, ".git", "worktrees", "pt-1")
        os.makedirs(self.gitdir)
        with open(os.path.join(self.worktree, ".git"), "w", encoding="utf-8") as fh:
            fh.write(f"gitdir: {self.gitdir}\n")

    def test_codex_exec_writes_only_to_the_worktree_git_dir_objects_refs_logs_and_out(self):
        cmd = agentcmd.background(self.step("codex", model="gpt-5-codex"))
        git = os.path.join(self.repo, ".git")
        roots = [self.gitdir, git + "/objects", git + "/refs", git + "/logs", os.path.join(self.step_dir, "out")]
        self.assertEqual(cmd.argv, ["/bin/codex", "exec", "--model=gpt-5-codex", "--json", "--cd", self.worktree,
                                    "--sandbox", "workspace-write",
                                    "-c", "sandbox_workspace_write.writable_roots=" + json.dumps(roots),
                                    "-c", 'approval_policy="never"', "-"])
        self.assertEqual(cmd.stdin, "Zrób ticket")
        self.assertEqual(cmd.session_id, "")
        self.assertNotIn("CLAUDE_CODE_TMPDIR", cmd.env)
        self.assertNotIn(git, roots)

    def test_codex_local_model_options_come_right_after_exec(self):
        agent = local.LocalAgent("local", "http://127.0.0.1:11434", "ollama", "qwen3-coder:30b", 65536)
        cmd = agentcmd.background(self.step("codex", local=agent))
        self.assertEqual(cmd.argv[:3 + len(local.options("codex", agent).argv)],
                         ["/bin/codex", "exec"] + local.options("codex", agent).argv + ["--json"])
        self.assertEqual(cmd.env["CODEX_OSS_BASE_URL"], "http://127.0.0.1:11434/v1")

    def test_resume(self):
        cmd = agentcmd.background(self.step("codex", resume=True, session_id="abc"))
        self.assertEqual(cmd.argv[:4], ["/bin/codex", "exec", "resume", "abc"])

    def test_codex_step_dir_must_not_be_hidden_to_the_snap(self):
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step("codex", step_dir=os.path.join(self.home, ".cache", "step")))


if __name__ == "__main__":
    unittest.main()
