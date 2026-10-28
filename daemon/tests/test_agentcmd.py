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
import signal
import subprocess
import tempfile
import sys
import textwrap
import time
import unittest
import uuid

from . import gitrepos
from tide_daemon.tasks import agentcmd, local, stepstop

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
        os.makedirs(self.tmp_root)
        os.makedirs(self.repo)
        gitrepos.git(self.repo, "init", "-q", "-b", "master")
        gitrepos.commit(self.repo, "a.txt")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-1", self.worktree)
        self.gitdir = os.path.join(self.repo, ".git", "worktrees", "pt-1")
        self.sid = "b" + uuid.uuid4().hex[:8]
        self.token_file = os.path.join(self.root, "token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(SECRET + "\n")

    def step(self, tool="claude", **kw):
        base = dict(DIRTY_ENV, HOME=self.home)
        args = dict(tool=tool, binary="/bin/" + tool, prompt="Zrób ticket", worktree=self.worktree, repo=self.repo,
                    step_dir=self.step_dir, step_id=self.sid, session_id="11111111-2222-3333-4444-555555555555",
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

    def test_step_id_is_a_short_plain_name_and_unit_names_do_not_collide(self):
        for bad in ("", "a" * 65, "x/../../../home/user", "a.b", "a b", "ł", "..", "a/b"):
            with self.assertRaises(agentcmd.StepError, msg=repr(bad)):
                agentcmd.background(self.step(step_id=bad))
        self.assertEqual(agentcmd.background(self.step(step_id="a_b")).unit, "tide-step-a_b")
        self.assertEqual(agentcmd.background(self.step(step_id="a-b")).unit, "tide-step-a-b")
        self.assertEqual(agentcmd.background(self.step(step_id="A" * 64)).unit, "tide-step-" + "A" * 64)

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
        proc = cmd.start(stdout=subprocess.PIPE)
        out, _ = proc.communicate()
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(out.startswith(self.worktree + "\nZrób ticket"))
        self.assertNotIn("SSH_AUTH_SOCK", out)

    def test_step_dir_must_not_be_inside_the_worktree(self):
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step(step_dir=os.path.join(self.worktree, ".step")))

    def test_a_token_file_outside_home_is_denied_to_the_sandbox_commands(self):
        agent = local.LocalAgent("mac", "http://mac.lan:11434", "ollama", "m", None, self.token_file)
        deny = self.settings(agentcmd.background(self.step(local=agent)))["sandbox"]["filesystem"]["denyRead"]
        self.assertEqual(deny, ["~", self.token_file])
        home_token = os.path.join(self.home, ".config", "tok")
        agent = local.LocalAgent("mac", "http://mac.lan:11434", "ollama", "m", None, home_token)
        self.assertEqual(self.settings(agentcmd.background(self.step(local=agent)))["sandbox"]["filesystem"]["denyRead"],
                         ["~"])

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
        self.assertEqual(cmd.env["CLAUDE_CODE_TMPDIR"], os.path.join(self.tmp_root, "tide-step-" + self.sid))

    def test_codex_environment_is_clean(self):
        self.check_clean(agentcmd.background(self.step("codex", codex_mcp=())), "codex")

    def test_other_endpoint_token_stays_out_of_argv_and_environment(self):
        for tool in ("claude", "codex"):
            cmd = agentcmd.background(self.step(tool, codex_mcp=(), local=local.LocalAgent("mac", "http://mac.lan:11434", "ollama",
                                                                           "qwen3-coder:30b", 65536, self.token_file)))
            blob = json.dumps([cmd.argv, cmd.env, cmd.stdin, cmd.files])
            self.assertNotIn(SECRET, blob, tool)
            self.assertIn(self.token_file, blob if tool == "claude" else json.dumps(cmd.secret_env))
        codex = agentcmd.background(self.step("codex", codex_mcp=(), local=local.LocalAgent("mac", "http://mac.lan:11434", "ollama",
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
        cmd = agentcmd.background(self.step("codex", codex_mcp=(), local=local.LocalAgent(
            "mac", "http://mac.lan:11434", "ollama", "m", None, self.token_file)))
        cmd.argv = ["/bin/sh", "-c", "echo $TIDE_MODEL_TOKEN"]
        cmd.prepare()
        out, _ = cmd.start(stdout=subprocess.PIPE).communicate()
        self.assertEqual(out.strip(), SECRET)
        self.assertNotIn(local.TOKEN_ENV, cmd.env)


class BackgroundCodexTest(BackgroundBase):
    mcp = ()

    def test_codex_exec_writes_only_to_the_worktree_git_dir_objects_refs_logs_and_out(self):
        cmd = agentcmd.background(self.step("codex", model="gpt-5-codex", codex_mcp=()))
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
        cmd = agentcmd.background(self.step("codex", local=agent, codex_mcp=()))
        self.assertEqual(cmd.argv[:3 + len(local.options("codex", agent).argv)],
                         ["/bin/codex", "exec"] + local.options("codex", agent).argv + ["--json"])
        self.assertEqual(cmd.env["CODEX_OSS_BASE_URL"], "http://127.0.0.1:11434/v1")

    def test_resume_puts_the_options_before_the_subcommand(self):
        cmd = agentcmd.background(self.step("codex", resume=True, session_id="abc", codex_mcp=()))
        plain = agentcmd.background(self.step("codex", codex_mcp=()))
        self.assertEqual(cmd.argv[:-3], plain.argv[:-1])           # te same opcje przed „resume”
        self.assertEqual(cmd.argv[-3:], ["resume", "abc", "-"])

    def test_the_git_dir_comes_from_the_repository_not_from_a_file_the_agent_can_rewrite(self):
        for forged in (self.repo + "/.git", os.path.dirname(self.home), "/etc"):
            with open(os.path.join(self.worktree, ".git"), "w", encoding="utf-8") as fh:
                fh.write(f"gitdir: {forged}\n")
            cmd = agentcmd.background(self.step("codex", codex_mcp=()))
            roots = json.loads(next(a for a in cmd.argv if a.startswith("sandbox_workspace_write")).partition("=")[2])
            self.assertEqual(roots[0], self.gitdir)
            self.assertEqual(cmd.git_dir, self.gitdir)
            self.assertNotIn(forged, roots)

    def test_a_worktree_that_the_repository_does_not_know_is_refused(self):
        stray = os.path.join(self.home, "stray")
        os.makedirs(stray)
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step("codex", worktree=stray, codex_mcp=()))
        # wpis worktree przestawiony na cudzy katalog (agent poprzedniego kroku pisał do .git/worktrees/<wt>)
        with open(os.path.join(self.gitdir, "gitdir"), "w", encoding="utf-8") as fh:
            fh.write("/home/ktos/.git\n")
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step("claude"))

    def test_every_mcp_server_of_the_user_is_disabled_for_the_step(self):
        cmd = agentcmd.background(self.step("codex", codex_mcp=("linear", "my server")))
        self.assertIn("mcp_servers.linear.enabled=false", cmd.argv)
        self.assertIn('mcp_servers."my server".enabled=false', cmd.argv)
        self.assertEqual(cmd.argv[cmd.argv.index("mcp_servers.linear.enabled=false") - 1], "-c")

    def test_mcp_servers_are_listed_by_the_program_and_a_failure_refuses_the_start(self):
        def program(body):
            path = os.path.join(self.root, "fake-codex")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o755)
            return path
        ok = program('[ "$1 $2 $3" = "mcp list --json" ] && echo \'[{"name":"linear","enabled":true},{"name":"b"}]\'')
        self.assertEqual(agentcmd.list_codex_mcp(ok), ("linear", "b"))
        cmd = agentcmd.background(self.step("codex", binary=ok))
        self.assertIn("mcp_servers.b.enabled=false", cmd.argv)
        for body in ("exit 3", "echo nie-json", "echo '{}'", "sleep 30"):
            with self.assertRaises(agentcmd.StepError, msg=body):
                agentcmd.list_codex_mcp(program(body), timeout=0.5)

    def test_codex_step_dir_must_not_be_hidden_to_the_snap(self):
        with self.assertRaises(agentcmd.StepError):
            agentcmd.background(self.step("codex", codex_mcp=(), step_dir=os.path.join(self.home, ".cache", "step")))


# Atrapa programu agenta: pisze pliki w worktree (zmieniony śledzony, nowe nieśledzone, atrapa piaskownicy),
# uruchamia dziecko (`sleep`) i — na życzenie — wnuka we własnej sesji (`setsid`), zgłasza gotowość i czeka.
DUMMY = """#!/usr/bin/python3
import os, signal, subprocess, sys, time
if os.environ.get("DUMMY_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open("tracked.txt", "a") as fh:
    fh.write("zmiana agenta\\n")
with open("nowy.txt", "w") as fh:
    fh.write("nieśledzony\\n")
os.makedirs("src/glebokie", exist_ok=True)
with open("src/glebokie/inny.py", "w") as fh:
    fh.write("x = 1\\n")
os.makedirs(".claude", exist_ok=True)
with open(".claude/.cc-writes", "w") as fh:
    fh.write("atrapa\\n")
with open("zignorowany.log", "w") as fh:
    fh.write("log\\n")
child = subprocess.Popen(["sleep", "300"])
grand = subprocess.Popen(["sleep", "300"], start_new_session=True) if os.environ.get("DUMMY_SETSID") else None
with open(os.environ["DUMMY_READY"] + ".tmp", "w") as fh:
    fh.write("%d %d" % (child.pid, grand.pid if grand else 0))
os.rename(os.environ["DUMMY_READY"] + ".tmp", os.environ["DUMMY_READY"])
time.sleep(300)
"""

LAUNCHER = """
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from tide_daemon.tasks import agentcmd
spec = json.loads(sys.argv[2])
ready = spec.pop("ready")
cmd = agentcmd.background(agentcmd.Step(**spec))
cmd.env["DUMMY_READY"] = ready
cmd.prepare()
proc = cmd.start()
print(proc.pid, flush=True)
time.sleep(300)
"""

BRANCH = "feat/pt-1-x"


def systemd_scopes_work() -> bool:
    try:
        return subprocess.run(["/usr/bin/systemd-run", "--user", "--scope", "--collect", "--quiet", "--", "true"],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except OSError:
        return False


@unittest.skipUnless(systemd_scopes_work(), "brak `systemd-run --user --scope` — testy kroków w jednostce pominięte")
class StopStepTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-stop-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        gitrepos.git(self.repo, "init", "-q", "-b", "master")
        gitrepos.commit(self.repo, "tracked.txt", "start\n")
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("*.log\n")
        gitrepos.git(self.repo, "add", ".gitignore")
        gitrepos.git(self.repo, "commit", "-q", "-m", "ignore")
        self.hook_marker = os.path.join(self.root, "hook-ran")
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        with open(hook, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\ntouch {self.hook_marker}\nexit 1\n")
        os.chmod(hook, 0o755)
        self.worktree = os.path.join(self.root, "repo-wt", "pt-1")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", BRANCH, self.worktree)
        self.gitdir = os.path.join(self.repo, ".git", "worktrees", "pt-1")
        self.step_dir = os.path.join(self.root, "repo-wt", "pt-1.step")
        self.ready = os.path.join(self.root, "ready")
        self.tmp_root = os.path.join(self.root, "tmp")
        os.makedirs(self.tmp_root)
        self.session = "11111111-2222-3333-4444-555555555555"
        self.projects = os.path.join(self.root, "claude", "projects")
        self.sid = "t" + uuid.uuid4().hex[:8]
        self.marker = os.path.join(self.root, "ESCAPED")
        self.units = []
        self.addCleanup(self._cleanup_units)

    def _cleanup_units(self):
        for unit in self.units:
            subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", unit + ".scope"], capture_output=True)

    def spec(self, **extra):
        spec = dict(tool="claude", binary=self.dummy(), prompt="zrób", worktree=self.worktree, repo=self.repo,
                    step_dir=self.step_dir, step_id=self.sid, session_id=self.session,
                    base_env={"HOME": os.path.join(self.root, "home"), "PATH": os.environ["PATH"]},
                    tmp_root=self.tmp_root)
        spec.update(extra)
        return spec

    def dummy(self):
        path = os.path.join(self.root, "dummy.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(DUMMY)
        os.chmod(path, 0o755)
        return path

    def wait_ready(self):
        deadline = time.time() + 15
        while not os.path.exists(self.ready):
            self.assertLess(time.time(), deadline, "atrapa nie zgłosiła gotowości")
            time.sleep(0.05)
        with open(self.ready, encoding="utf-8") as fh:
            child, grand = (int(x) for x in fh.read().split())
        return child, grand

    def start(self, ignore_term=False, setsid=False):
        cmd = agentcmd.background(agentcmd.Step(**self.spec()))
        cmd.env["DUMMY_READY"] = self.ready
        if ignore_term:
            cmd.env["DUMMY_IGNORE_TERM"] = "1"
        if setsid:
            cmd.env["DUMMY_SETSID"] = "1"
        cmd.prepare()
        proc = cmd.start()
        self.units.append(cmd.unit)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        child, grand = self.wait_ready()
        return proc, cmd, child, grand

    def write_claude_log(self, lines):
        folder = os.path.join(self.projects, agentcmd_escape(self.worktree))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, self.session + ".jsonl"), "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def stop(self, proc, cmd, **kw):
        args = dict(cmd.stop_kwargs(), branch=BRANCH, claude_projects=self.projects, grace=5)
        args.update(kw)
        return stepstop.stop_step(proc, **args)

    def wait_gone(self, *pids):
        deadline = time.time() + 5
        while any(alive(p) for p in pids) and time.time() < deadline:
            time.sleep(0.05)
        return not any(alive(p) for p in pids)

    def test_stop_ends_the_process_and_its_children_and_commits_every_change(self):
        self.write_claude_log([
            {"type": "user", "message": {"role": "user", "content": "Zrób ticket"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Zaczynam"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Prawie gotowe, brakuje testu"}]}}])
        proc, cmd, child, _ = self.start()
        result = self.stop(proc, cmd)
        self.assertTrue(result.stopped)
        self.assertIsNotNone(proc.poll())
        self.assertTrue(self.wait_gone(child), "dziecko atrapy przeżyło zatrzymanie")
        self.assertNotEqual(subprocess.run(["systemctl", "--user", "is-active", "--quiet", cmd.unit + ".scope"]).returncode, 0)
        self.assertTrue(result.committed, result.error)
        self.assertEqual(result.error, "")
        # atrapa piaskownicy zostaje na dysku, ale nie w commicie; plik z .gitignore jest w notatkach
        self.assertEqual(gitrepos.git(self.worktree, "status", "--porcelain"), "?? .claude/\n")
        self.assertIn("zignorowany.log", " ".join(result.notes))
        shown = gitrepos.git(self.worktree, "show", "--name-only", "--format=%s%n%an", "HEAD").splitlines()
        self.assertEqual(shown[0], "WIP: step stopped before it finished")
        self.assertEqual(shown[1], "Krok")
        self.assertEqual(sorted(n for n in shown[2:] if n), ["nowy.txt", "src/glebokie/inny.py", "tracked.txt"])
        self.assertEqual(result.commit, gitrepos.git(self.worktree, "rev-parse", "HEAD").strip())
        self.assertEqual(result.files, 3)
        self.assertEqual(gitrepos.git(self.worktree, "branch", "--show-current").strip(), BRANCH)
        self.assertEqual([m["role"] for m in result.messages], ["user", "assistant", "assistant"])
        self.assertEqual(result.messages[-1]["text"], "Prawie gotowe, brakuje testu")
        self.assertNotIn("meta", [m["text"] for m in result.messages])

    def test_a_grandchild_in_its_own_session_does_not_survive_the_stop(self):
        proc, cmd, child, grand = self.start(setsid=True)
        self.assertTrue(alive(grand))
        self.assertTrue(self.stop(proc, cmd).stopped)
        self.assertTrue(self.wait_gone(child, grand), "wnuk po setsid przeżył zatrzymanie jednostki")

    def test_the_step_survives_the_death_of_the_process_that_started_it(self):
        spec = self.spec(binary=self.dummy())
        launcher = subprocess.Popen([sys.executable, "-c", LAUNCHER, os.path.dirname(os.path.dirname(os.path.dirname(stepstop.__file__))),
                                     json.dumps(dict(spec, ready=self.ready))], stdout=subprocess.PIPE, text=True,
                                    start_new_session=True)
        self.addCleanup(lambda: launcher.poll() is None and launcher.kill())
        self.addCleanup(launcher.stdout.close)
        step_pid = int(launcher.stdout.readline())
        self.units.append("tide-step-" + self.sid)
        child, _ = self.wait_ready()
        os.kill(launcher.pid, signal.SIGKILL)       # „restart usługi”
        launcher.wait()
        time.sleep(0.5)
        self.assertTrue(alive(step_pid), "krok zginął razem z procesem, który go uruchomił")
        self.assertTrue(alive(child))
        result = stepstop.stop_step(None, unit="tide-step-" + self.sid, worktree=self.worktree, step_dir=self.step_dir,
                                    repo=self.repo, git_dir=self.gitdir, branch=BRANCH, tool="claude", grace=5)
        self.assertTrue(result.stopped)
        self.assertTrue(result.committed, result.error)
        self.assertTrue(self.wait_gone(step_pid, child))

    def test_the_step_output_goes_to_a_file_not_to_a_pipe_of_the_service(self):
        proc, cmd, _, _ = self.start()
        self.assertIsNone(proc.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.step_dir, "out", "step.log")))
        self.stop(proc, cmd)

    def test_the_service_git_runs_no_hooks(self):
        proc, cmd, _, _ = self.start()
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        self.assertFalse(os.path.exists(self.hook_marker))

    def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(self):
        proc, cmd, _, _ = self.start(ignore_term=True)
        started = time.time()
        result = self.stop(proc, cmd, grace=0.5)
        self.assertTrue(result.stopped)
        self.assertLess(time.time() - started, 5)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        self.assertTrue(result.committed)

    def test_stopping_twice_commits_nothing_new(self):
        proc, cmd, _, _ = self.start()
        self.assertTrue(self.stop(proc, cmd).committed)
        again = self.stop(proc, cmd)
        self.assertTrue(again.stopped)
        self.assertFalse(again.committed)
        self.assertEqual(again.error, "")

    def test_without_a_unit_a_dead_pid_is_never_signalled_through_its_group(self):
        # Lider grupy ginie, w grupie zostaje cudzy proces — stary kod robił killpg(pid) także dla martwego pidu.
        leader = subprocess.Popen(
            [sys.executable, "-c", "import subprocess,time; c=subprocess.Popen(['sleep','60']); print(c.pid,flush=True)"],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        bystander = int(leader.stdout.readline())
        leader.wait()
        leader.stdout.close()
        self.addCleanup(lambda: alive(bystander) and os.kill(bystander, signal.SIGKILL))
        result = stepstop.stop_step(leader, unit="", worktree=self.worktree, step_dir=self.step_dir, repo=self.repo,
                                    git_dir=self.gitdir, branch=BRANCH, tool="claude")
        self.assertTrue(result.stopped)
        self.assertTrue(alive(bystander))

    def test_without_a_unit_a_live_pid_is_stopped(self):
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        result = stepstop.stop_step(proc.pid, unit="", worktree=self.worktree, step_dir=self.step_dir, repo=self.repo,
                                    git_dir=self.gitdir, branch=BRANCH, tool="claude", grace=2)
        self.assertTrue(result.stopped)
        self.assertEqual(proc.wait(timeout=5), -signal.SIGTERM)

    def test_an_already_finished_step_is_still_snapshotted(self):
        proc, cmd, _, _ = self.start()
        subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", cmd.unit + ".scope"], check=True)
        proc.wait(timeout=10)
        result = self.stop(proc, cmd)
        self.assertTrue(result.stopped)
        self.assertTrue(result.committed)

    def test_stale_locks_of_the_killed_step_do_not_lose_the_changes(self):
        proc, cmd, _, _ = self.start()
        locks = [os.path.join(self.gitdir, "index.lock"), os.path.join(self.gitdir, "HEAD.lock"),
                 os.path.join(self.repo, ".git", "refs", "heads", BRANCH + ".lock")]
        for lock in locks:
            os.makedirs(os.path.dirname(lock), exist_ok=True)
            with open(lock, "w"):
                pass
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        self.assertFalse(any(os.path.exists(lock) for lock in locks))

    def test_detached_head_main_branches_and_a_foreign_head_are_not_committed_to(self):
        proc, cmd, _, _ = self.start()
        # agent przestawił HEAD w katalogu worktree (Codex ma tam zapis) na gałąź główną
        with open(os.path.join(self.gitdir, "HEAD"), "w", encoding="utf-8") as fh:
            fh.write("ref: refs/heads/master\n")
        before = gitrepos.git(self.repo, "rev-parse", "master").strip()
        result = self.stop(proc, cmd)
        self.assertFalse(result.committed)
        self.assertEqual(result.error, "wrong-branch")
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "master").strip(), before)
        self.assertIn("nowy.txt", gitrepos.git(self.worktree, "status", "--porcelain"))
        with open(os.path.join(self.gitdir, "HEAD"), "w", encoding="utf-8") as fh:
            fh.write(gitrepos.git(self.repo, "rev-parse", "master").strip() + "\n")
        self.assertEqual(stepstop.git_snapshot(self.repo, self.gitdir, self.worktree, self.step_dir, BRANCH).error,
                         "detached")
        self.assertEqual(stepstop.git_snapshot(self.repo, self.gitdir, self.worktree, self.step_dir, "master").error,
                         "protected-branch")

    def forge_filter(self, config_path, ran=None):
        """Filtr `clean` zdefiniowany tam, gdzie agent mógłby pisać, i `.gitattributes`, który go włącza."""
        touch = f"{self.marker} {ran}" if ran else self.marker
        with open(config_path, "a", encoding="utf-8") as fh:
            fh.write(f'[filter "x"]\n\tclean = touch {touch} && cat\n')
        with open(os.path.join(self.worktree, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write("* filter=x\n")

    def test_a_forged_dot_git_pointer_does_not_run_agent_filters_outside_the_sandbox(self):
        proc, cmd, _, _ = self.start()
        fake = os.path.join(self.worktree, ".fakegit")
        shutil.copytree(os.path.join(self.repo, ".git"), fake, ignore=shutil.ignore_patterns("worktrees"))
        with open(os.path.join(fake, "HEAD"), "w", encoding="utf-8") as fh:
            fh.write(f"ref: refs/heads/{BRANCH}\n")
        self.forge_filter(os.path.join(fake, "config"))
        with open(os.path.join(self.worktree, ".git"), "w", encoding="utf-8") as fh:
            fh.write(f"gitdir: {fake}\n")
        result = self.stop(proc, cmd)
        self.assertFalse(os.path.exists(self.marker), "filtr agenta wykonał się poza piaskownicą")
        self.assertTrue(result.committed, result.error)
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", BRANCH).strip(), result.commit)

    def test_a_per_worktree_config_cannot_define_filters_for_the_service_git(self):
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        proc, cmd, _, _ = self.start()
        config = os.path.join(self.gitdir, "config.worktree")
        self.forge_filter(config)
        result = self.stop(proc, cmd)
        self.assertFalse(os.path.exists(self.marker), "filtr z config.worktree wykonał się poza piaskownicą")
        self.assertTrue(result.committed, result.error)
        self.assertTrue(os.path.isfile(config), "config.worktree zniknął po zatrzymaniu")

    # --- git usługi w piaskownicy (decyzja 8) -------------------------------------------------

    def untracked_names(self, ref="HEAD"):
        return gitrepos.git(self.worktree, "show", "--name-only", "--format=", ref).split()

    def test_nested_repositories_do_not_run_their_filters_outside_the_sandbox_and_are_not_pulled_in(self):
        proc, cmd, _, _ = self.start()
        fsm = self.marker + ".fsm"
        for name, gitfile in (("sub1", False), ("sub2", True)):
            sub = os.path.join(self.worktree, name)
            os.makedirs(sub)
            gitrepos.git(sub, "init", "-q", "-b", "m")
            gitrepos.commit(sub, "f.txt", "f\n")
            with open(os.path.join(sub, ".gitattributes"), "w", encoding="utf-8") as fh:
                fh.write("* filter=x\n")
            with open(os.path.join(sub, ".git", "config"), "a", encoding="utf-8") as fh:
                fh.write(f'[filter "x"]\n\tclean = "touch {self.marker} && cat"\n[core]\n\tfsmonitor = "touch {fsm}"\n')
            time.sleep(0.05)
            with open(os.path.join(sub, "f.txt"), "w", encoding="utf-8") as fh:
                fh.write("zmiana\n")
            if gitfile:
                shutil.move(os.path.join(sub, ".git"), os.path.join(self.worktree, "gd2"))
                with open(os.path.join(sub, ".git"), "w", encoding="utf-8") as fh:
                    fh.write("gitdir: ../gd2\n")
        # trzecie: gitlink już w historii kroku, brudny
        sub3 = os.path.join(self.worktree, "sub3")
        os.makedirs(sub3)
        gitrepos.git(sub3, "init", "-q", "-b", "m")
        gitrepos.commit(sub3, "f.txt", "f\n")
        gitrepos.git(self.worktree, "add", "sub3")
        gitrepos.git(self.worktree, "commit", "-q", "--no-verify", "-m", "gitlink")
        gitrepos.commit(sub3, "g.txt", "g\n")
        with open(os.path.join(sub3, ".git", "config"), "a", encoding="utf-8") as fh:   # dopiero teraz: git testu nie ma go wykonać
            fh.write(f'[core]\n\tfsmonitor = "touch {fsm}"\n')
        result = self.stop(proc, cmd)
        self.assertFalse(os.path.exists(self.marker), "filtr zagnieżdżonego repozytorium wykonał się poza piaskownicą")
        self.assertFalse(os.path.exists(fsm), "fsmonitor zagnieżdżonego repozytorium wykonał się poza piaskownicą")
        self.assertTrue(result.committed, result.error)
        files = self.untracked_names()
        # sub3 to gitlink już śledzony w gałęzi kroku: WIP zapisuje nowy wskaźnik, nie treść zagnieżdżonego repozytorium
        self.assertEqual(sorted(files), ["nowy.txt", "src/glebokie/inny.py", "sub3", "tracked.txt"])
        notes = " ".join(result.notes)
        for name in ("sub1", "sub2", "gd2"):
            self.assertIn(name, notes)

    def test_a_forged_commondir_cannot_redirect_the_service_git(self):
        proc, cmd, _, _ = self.start()
        fake = os.path.join(self.gitdir, "fakecommon")
        shutil.copytree(os.path.join(self.repo, ".git"), fake, ignore=shutil.ignore_patterns("worktrees"))
        os.makedirs(os.path.join(fake, "worktrees"))
        ran = os.path.join(self.worktree, "FILTER-RAN")
        self.forge_filter(os.path.join(fake, "config"), ran)
        with open(os.path.join(self.gitdir, "commondir"), "w", encoding="utf-8") as fh:
            fh.write("fakecommon\n")
        result = self.stop(proc, cmd)
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(ran), "git usługi użył podrobionego commondir (GIT_COMMON_DIR nie wygrał)")
        self.assertTrue(result.committed, result.error)
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", BRANCH).strip(), result.commit)
        self.assertIn("commondir", " ".join(result.notes))

    def test_a_planted_symlink_under_the_commondir_temp_name_does_not_overwrite_its_target(self):
        proc, cmd, _, _ = self.start()
        victim = os.path.join(self.root, "victim.txt")
        with open(victim, "w", encoding="utf-8") as fh:
            fh.write("nietkniete\n")
        commondir = os.path.join(self.gitdir, "commondir")
        with open(commondir, "w", encoding="utf-8") as fh:
            fh.write("../../elsewhere\n")
        os.symlink(victim, commondir + ".tide-new")
        result = self.stop(proc, cmd)
        with open(victim, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "nietkniete\n", "przywracanie commondir zapisało przez dowiązanie")
        self.assertFalse(os.path.islink(commondir))
        self.assertTrue(os.path.isfile(commondir))
        with open(commondir, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "../..")
        self.assertTrue(result.committed, result.error)
        self.assertIn("commondir", " ".join(result.notes))

    def test_the_service_git_sandbox_hides_the_user_runtime_dir_and_its_sockets(self):
        # Sonda: filtr w `config.worktree` (konfiguracja agenta, tor ataku) wykonuje się w piaskownicy gita usługi i zapisuje
        # w worktree, co widzi. Skrypt leży w worktree: /tmp i /run są w piaskownicy puste, a worktree jest do niej podpięty.
        uid = os.getuid()
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        proc, cmd, _, _ = self.start()
        script = os.path.join(self.worktree, "probe.sh")
        out = os.path.join(self.worktree, "probe.out")
        paths = [f"/run/user/{uid}/bus", f"/run/user/{uid}/systemd/private", f"/run/user/{uid}"]
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\necho ran > {out}\n")
            for path in paths:
                fh.write(f'[ -e {path} ] && echo "VISIBLE {path}" >> {out}\n')
            fh.write("cat\n")
        os.chmod(script, 0o755)
        with open(os.path.join(self.gitdir, "config.worktree"), "a", encoding="utf-8") as fh:
            fh.write(f'[filter "probe"]\n\tclean = {script}\n')
        with open(os.path.join(self.worktree, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write("probe.txt filter=probe\n")
        with open(os.path.join(self.worktree, "probe.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        with open(out, encoding="utf-8") as fh:
            seen = fh.read().split()
        self.assertEqual(seen[:1], ["ran"], "sonda nie wykonała się w piaskownicy")
        self.assertNotIn("VISIBLE", seen, "piaskownica gita usługi widzi /run/user/$UID: " + " ".join(seen))

    def test_repository_hooks_other_than_pre_commit_do_not_run(self):
        proc, cmd, _, _ = self.start()
        ran = os.path.join(self.worktree, "HOOK-RAN")
        for name in ("post-commit", "post-index-change", "reference-transaction", "post-checkout"):
            hook = os.path.join(self.repo, ".git", "hooks", name)
            with open(hook, "w", encoding="utf-8") as fh:
                fh.write(f"#!/bin/sh\ntouch {ran} {self.marker}\n")
            os.chmod(hook, 0o755)
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        self.assertFalse(os.path.exists(ran), "hak repozytorium wykonał się (core.hooksPath nie działa)")
        self.assertFalse(os.path.exists(self.marker))

    def test_a_directory_under_the_old_aside_name_does_not_open_the_per_worktree_config(self):
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        proc, cmd, _, _ = self.start()
        config = os.path.join(self.gitdir, "config.worktree")
        self.forge_filter(config)
        os.makedirs(os.path.join(self.gitdir, "config.worktree.tide-aside", "x"))
        with open(config, encoding="utf-8") as fh:
            before = fh.read()
        result = self.stop(proc, cmd)
        self.assertFalse(os.path.exists(self.marker), "filtr z config.worktree wykonał się poza piaskownicą")
        self.assertTrue(result.committed, result.error)
        with open(config, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)       # usługa nie rusza plików w katalogu gita kroku

    def test_without_the_sandbox_the_snapshot_is_refused_and_the_changes_stay(self):
        proc, cmd, _, _ = self.start()
        old, stepstop.BWRAP = stepstop.BWRAP, "/nonexistent/bwrap"
        self.addCleanup(setattr, stepstop, "BWRAP", old)
        result = self.stop(proc, cmd)
        self.assertTrue(result.stopped)
        self.assertFalse(result.committed)
        self.assertEqual(result.error, "no-sandbox")
        self.assertIn("nowy.txt", gitrepos.git(self.worktree, "status", "--porcelain"))
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", BRANCH).strip(),
                         gitrepos.git(self.repo, "rev-parse", "master").strip())

    def test_the_home_directory_is_invisible_to_the_service_git(self):
        home = tempfile.mkdtemp(prefix="tide-pt76-home-", dir=os.path.expanduser("~"))
        self.addCleanup(shutil.rmtree, home, True)
        secret = os.path.join(home, "secret.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("SEKRET-76\n")
        proc, cmd, _, _ = self.start()
        config = os.path.join(self.gitdir, "config.worktree")
        gitrepos.git(self.repo, "config", "extensions.worktreeConfig", "true")
        with open(config, "a", encoding="utf-8") as fh:
            fh.write(f'[filter "x"]\n\tclean = "cat {secret} >> {self.worktree}/LEAK.txt; cat"\n')
        with open(os.path.join(self.worktree, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write("nowy.txt filter=x\n")
        subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", cmd.unit + ".scope"], check=True)
        proc.wait(timeout=10)
        snap = stepstop.git_snapshot(self.repo, self.gitdir, self.worktree, self.step_dir, BRANCH, home=home)
        self.assertTrue(snap.committed, snap.error)
        leak = os.path.join(self.worktree, "LEAK.txt")
        if os.path.exists(leak):
            with open(leak, encoding="utf-8") as fh:
                self.assertNotIn("SEKRET-76", fh.read())
        self.assertNotIn("SEKRET-76", gitrepos.git(self.worktree, "show", "HEAD"))

    def test_symlinks_are_committed_as_links_not_followed(self):
        proc, cmd, _, _ = self.start()
        os.symlink("/etc", os.path.join(self.worktree, "etcdir"))
        os.symlink("/etc/hostname", os.path.join(self.worktree, "leak"))
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        tree = gitrepos.git(self.worktree, "ls-tree", "HEAD")
        self.assertRegex(tree, r"120000 blob \w+\s+etcdir")
        self.assertRegex(tree, r"120000 blob \w+\s+leak")
        self.assertNotIn("etc/hostname", self.untracked_names())

    def test_assume_unchanged_and_skip_worktree_bits_set_by_the_agent_do_not_hide_changes(self):
        proc, cmd, _, _ = self.start()
        gitrepos.git(self.worktree, "update-index", "--assume-unchanged", "tracked.txt")
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        self.assertIn("tracked.txt", self.untracked_names())
        self.assertIn("assume-unchanged", " ".join(result.notes))
        self.assertEqual(gitrepos.git(self.worktree, "ls-files", "-v", "tracked.txt").split()[0], "H")

    def test_skip_worktree_on_a_file_missing_from_disk_is_a_sparse_checkout_and_stays(self):
        with open(os.path.join(self.worktree, "sparse.txt"), "w", encoding="utf-8") as fh:
            fh.write("s\n")
        gitrepos.git(self.worktree, "add", "sparse.txt")
        gitrepos.git(self.worktree, "commit", "-q", "--no-verify", "-m", "sparse")
        proc, cmd, _, _ = self.start()
        gitrepos.git(self.worktree, "update-index", "--skip-worktree", "sparse.txt")
        os.unlink(os.path.join(self.worktree, "sparse.txt"))
        result = self.stop(proc, cmd)
        self.assertTrue(result.committed, result.error)
        self.assertIn("sparse.txt", gitrepos.git(self.worktree, "ls-tree", "--name-only", "HEAD").split())
        self.assertNotIn("sparse.txt", self.untracked_names())

    def test_stopping_one_step_leaves_another_step_unit_running(self):
        other = subprocess.Popen(["/usr/bin/systemd-run", "--user", "--scope", "--collect", "--quiet",
                                  "--unit=tide-step-other" + self.sid, "--", "sleep", "300"])
        self.units.append("tide-step-other" + self.sid)
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        deadline = time.time() + 10
        while not stepstop.unit_state("tide-step-other" + self.sid) == "active" and time.time() < deadline:
            time.sleep(0.05)
        proc, cmd, _, _ = self.start()
        self.assertTrue(self.stop(proc, cmd).stopped)
        self.assertEqual(stepstop.unit_state("tide-step-other" + self.sid), "active")

    def test_a_failing_systemctl_means_not_stopped_and_the_worktree_is_left_alone(self):
        proc, cmd, _, _ = self.start()
        fake = os.path.join(self.root, "fakebin")
        os.makedirs(fake)
        with open(os.path.join(fake, "systemctl"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho 'Failed to connect to bus' >&2\nexit 1\n")
        os.chmod(os.path.join(fake, "systemctl"), 0o755)
        lock = os.path.join(self.gitdir, "index.lock")
        with open(lock, "w"):
            pass
        old = os.environ["PATH"]
        os.environ["PATH"] = fake + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        result = self.stop(proc, cmd, grace=0.3)
        os.environ["PATH"] = old
        self.assertFalse(result.stopped)
        self.assertEqual(result.error, "not-stopped")
        self.assertFalse(result.committed)
        self.assertTrue(os.path.exists(lock), "ruszono worktree żywego kroku")
        self.assertIsNone(proc.poll())              # nikt go nie zatrzymał
        self.assertEqual(stepstop.unit_state(cmd.unit), "active")

    def test_a_live_process_is_not_stopped_just_because_its_unit_looks_inactive(self):
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.assertFalse(stepstop.terminate(proc, unit="tide-step-nieistniejaca", grace=0.2))


    def test_last_messages_of_a_codex_session_log(self):
        sessions = os.path.join(self.root, "codex", "sessions", "2026", "10", "28")
        os.makedirs(sessions)
        other = os.path.join(sessions, "rollout-2026-10-28T10-00-00-aaaa.jsonl")
        mine = os.path.join(sessions, "rollout-2026-10-28T11-00-00-bbbb.jsonl")
        meta = lambda cwd: {"type": "session_meta", "payload": {"id": "bbbb", "cwd": cwd}}
        msg = lambda role, kind, text: {"type": "response_item", "payload": {
            "type": "message", "role": role, "content": [{"type": kind, "text": text}]}}
        for path, cwd in ((other, "/gdzie/indziej"), (mine, self.worktree)):
            with open(path, "w", encoding="utf-8") as fh:
                for record in (meta(cwd), msg("user", "input_text", "<environment_context>x"),
                               msg("user", "input_text", "Zrób ticket"), msg("assistant", "output_text", "Czytam kod"),
                               {"type": "event_msg", "payload": {"type": "agent_message", "message": "Czytam kod"}},
                               msg("assistant", "output_text", "Piszę test")):
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        proc, cmd, _, _ = self.start()
        result = self.stop(proc, cmd, tool="codex", session_id="", claude_projects="",
                           codex_roots=[os.path.join(self.root, "codex")], limit=2)
        self.assertEqual(result.session_log, mine)
        self.assertEqual([(m["role"], m["text"]) for m in result.messages],
                         [("assistant", "Czytam kod"), ("assistant", "Piszę test")])

    def test_no_session_log_means_no_messages_not_an_error(self):
        proc, cmd, _, _ = self.start()
        result = self.stop(proc, cmd)
        self.assertEqual((result.messages, result.session_log, result.error), ([], "", ""))
        self.assertTrue(result.committed)


CODEX = shutil.which("codex") or "/snap/bin/codex"


@unittest.skipUnless(os.access(CODEX, os.X_OK), "brak Codexa — test prawdziwej piaskownicy pominięty")
class CodexSandboxIntegrationTest(unittest.TestCase):
    """Katalogi zapisu ze złożonego kroku w prawdziwej piaskownicy Codexa (Landlock; `codex sandbox linux` nie
    woła modelu ani nie wymaga logowania). Katalog w zwykłym katalogu domowym: snap nie widzi /tmp hosta."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-pt76-", dir=os.path.expanduser("~"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo, self.wt = os.path.join(self.root, "repo"), os.path.join(self.root, "wt")
        os.makedirs(self.repo)
        gitrepos.git(self.repo, "init", "-q", "-b", "master")
        gitrepos.commit(self.repo, "a.txt")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "step", self.wt)

    def test_commit_from_the_worktree_works_but_hooks_and_config_stay_read_only(self):
        step = agentcmd.Step("codex", CODEX, "x", self.wt, self.repo, os.path.join(self.root, "step"), "s1", codex_mcp=())
        roots = next(a for a in agentcmd.background(step).argv if a.startswith("sandbox_workspace_write"))
        with open(os.path.join(self.wt, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        script = ("git add -A && git commit -q -m s; echo commit=$?; touch %s/.git/hooks/evil; echo hook=$?; "
                  "touch %s/.git/config.x; echo cfg=$?; touch %s/other; echo other=$?" % (self.repo, self.repo, self.root))
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        run = subprocess.run([CODEX, "sandbox", "linux", "--full-auto", "-c", roots, "--", "sh", "-c", script],
                             cwd=self.wt, env=env, capture_output=True, text=True, timeout=60)
        if "commit=" not in run.stdout:
            self.skipTest("piaskownica Codexa niedostępna: " + (run.stderr.strip() or "brak wyniku")[:200])
        self.assertEqual(run.stdout.split(), ["commit=0", "hook=1", "cfg=1", "other=1"])
        self.assertEqual(gitrepos.git(self.wt, "log", "-1", "--format=%s").strip(), "s")


def agentcmd_escape(path: str) -> str:
    from tide_daemon.agents.claude import escape_cwd
    return escape_cwd(path)


def _gone(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rpartition(")")[2].split()[0] == "Z"
    except OSError:
        return True


if __name__ == "__main__":
    unittest.main()
