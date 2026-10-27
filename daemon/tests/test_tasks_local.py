"""Nowe zadanie na modelu lokalnym (PT-17) — bez prawdziwego serwera modeli i bez prawdziwych agentów.

- argv i środowisko: Codex i Claude Code × Ollama na tym komputerze / Ollama na innym komputerze /
  serwer zgodny z OpenAI × z tokenem / bez tokenu; token nigdy w argv ani w pliku zadania,
- kontekst: z roli, z załadowanego modelu, za mały dla Claude Code,
- launch.py: zmienne modelu i token z pliku docierają do agenta (atrapa zapisuje swoje środowisko),
  powłoka po agencie ich nie ma, nieczytelny token — agent nie startuje, a komunikat nie zdradza treści,
- Planner: rola `agent` z models.json (atrapa odczytu serwera), odmowy (nieustawiona, serwer nie
  odpowiada, modelu nie ma, rola z błędem, zły model), tytuł karty „· lokalnie”, zapamiętany wybór,
- sesje: Codex z `model_provider` innym niż chmura i Claude Code z modelem spoza Claude'a to `localModel`,
  tytuł karty z znacznikiem stanu niesie „· lokalnie”.
"""
import json
import os
import shutil
import tempfile
import unittest

from . import gitrepos
from .test_tasks_core import fake_bin, quiet_git, run_launcher, write_exec
from tide_daemon import tabmarks
from tide_daemon.agents.claude import ClaudeLog
from tide_daemon.agents.codex import CodexLog
from tide_daemon.agents.localmodel import (is_local_claude_model, is_local_codex_provider, known_models,
                                           session_is_local)
from tide_daemon.agents.scanner import AgentScanner
from tide_daemon.models import config as models_config
from tide_daemon.tasks import Planner, TaskError, TaskFiles, agent_argv
from tide_daemon.tasks import local
from tide_daemon.tasks.launch import command

TOKEN = "sekretny-token-PT17"
ENV_AGENT = """#!/usr/bin/python3
import json, os, sys
keys = [k for k in os.environ if k.startswith(("ANTHROPIC", "CLAUDE_CODE", "CODEX_OSS", "TIDE_MODEL_TOKEN"))]
with open(os.environ["TIDE_FAKE_AGENT_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env": {k: os.environ[k] for k in sorted(keys)}}) + "\\n")
"""
# Powłoka po agencie: zapisuje, które zmienne modelu jeszcze widzi.
AFTER_SHELL = """#!/bin/sh
env | grep -E '^(ANTHROPIC|CLAUDE_CODE|CODEX_OSS|TIDE_MODEL_TOKEN|TIDE_TASK)' | sort > "$TIDE_SHELL_LOG"
"""


def agent(endpoint="local", url="http://127.0.0.1:11434", kind="ollama", model="qwen3-coder:30b", ctx=None,
          token_file=""):
    return local.LocalAgent(endpoint, url, kind, model, ctx, token_file)


class CommandTest(unittest.TestCase):
    def test_codex_on_this_computer_uses_builtin_ollama(self):
        cmd = local.options("codex", agent())
        self.assertEqual(cmd.argv, ["--oss", "--local-provider", "ollama", "--model=qwen3-coder:30b"])
        self.assertEqual(cmd.env, {"CODEX_OSS_BASE_URL": "http://127.0.0.1:11434/v1"})
        self.assertEqual(cmd.secret_env, {})
        for url in ("http://localhost:11434", "http://[::1]:11434", "http://127.0.0.2:8080"):
            self.assertIn("--oss", local.options("codex", agent(url=url)).argv, url)

    def test_codex_on_mac_gets_own_provider_and_token_through_env(self):
        cmd = local.options("codex", agent("mac", "http://mac-studio:11434", ctx=65536, token_file="/k/mac.token"))
        self.assertEqual(cmd.argv, [
            "-c", 'model_provider="tide-mac"',
            "-c", 'model_providers.tide-mac.name="Tide: mac"',
            "-c", 'model_providers.tide-mac.base_url="http://mac-studio:11434/v1"',
            "-c", 'model_providers.tide-mac.wire_api="responses"',
            "-c", 'model_providers.tide-mac.env_key="TIDE_MODEL_TOKEN"',
            "-c", 'shell_environment_policy.exclude=["TIDE_MODEL_TOKEN"]',
            "-c", "features.shell_snapshot=false",
            "--model=qwen3-coder:30b", "-c", "model_context_window=65536"])
        self.assertEqual(cmd.env, {})
        self.assertEqual(cmd.secret_env, {"TIDE_MODEL_TOKEN": "/k/mac.token"})

    def test_codex_without_token_and_openai_server(self):
        mac = local.options("codex", agent("mac", "http://mac-studio:11434"))
        self.assertNotIn("--oss", mac.argv)
        self.assertFalse(any("env_key" in a or "shell_environment_policy" in a or "shell_snapshot" in a
                             for a in mac.argv))
        self.assertEqual(mac.secret_env, {})
        lms = local.options("codex", agent("lm.studio", "http://10.0.0.5:1234", kind="openai", model="qwen/qwen3"))
        self.assertIn('model_provider="tide-lm_studio"', lms.argv, "kropka w nazwie punktu psułaby ścieżkę -c")
        self.assertIn('model_providers.tide-lm_studio.base_url="http://10.0.0.5:1234/v1"', lms.argv)
        # Ollama na tym komputerze z tokenem (np. pośrednik) — też własny dostawca, bo --oss nie niesie tokenu.
        proxied = local.options("codex", agent(token_file="/k/t"))
        self.assertNotIn("--oss", proxied.argv)
        self.assertEqual(proxied.secret_env, {"TIDE_MODEL_TOKEN": "/k/t"})
        # Openai na tym komputerze — nie wbudowana Ollama.
        self.assertNotIn("--oss", local.options("codex", agent(kind="openai")).argv)

    def test_claude_env_for_each_server(self):
        here = local.options("claude", agent())
        self.assertEqual(here.argv, ["--model=qwen3-coder:30b"])
        self.assertEqual(here.env, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:11434", "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "ollama",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            **{name: "qwen3-coder:30b" for name in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                                                    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                                                    "ANTHROPIC_DEFAULT_FABLE_MODEL")}})
        self.assertEqual(here.secret_env, {})
        self.assertEqual((here.unset_env, here.token_files), ([], []))
        mac = local.options("claude", agent("mac", "http://mac-studio:11434", ctx=131072, token_file="/k/mój token"))
        self.assertEqual(mac.env["ANTHROPIC_BASE_URL"], "http://mac-studio:11434")
        # Token nie jest zmienną środowiska (dziedziczyłyby ją polecenia Bash agenta): Claude Code bierze go
        # z apiKeyHelper, który czyta plik przy każdym odświeżeniu; w argv jest tylko ścieżka.
        self.assertEqual(mac.argv, ["--model=qwen3-coder:30b", "--settings",
                                    json.dumps({"apiKeyHelper": "cat -- '/k/mój token'"}, ensure_ascii=False)])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", mac.env)
        self.assertEqual(mac.secret_env, {})
        self.assertEqual(mac.unset_env, ["ANTHROPIC_AUTH_TOKEN"], "token użytkownika nie przykryje apiKeyHelper")
        self.assertEqual(mac.token_files, ["/k/mój token"], "launch.py sprawdza plik przed startem")
        self.assertNotIn("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", mac.env, "na tym komputerze psuje Bash (bwrap)")
        self.assertEqual((mac.env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], mac.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]),
                         ("131072", "32000"))
        lms = local.options("claude", agent("lms", "http://10.0.0.5:1234", kind="openai", ctx=32768))
        self.assertEqual(lms.env["ANTHROPIC_BASE_URL"], "http://10.0.0.5:1234")
        self.assertEqual(lms.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "8192")

    def test_small_window_is_not_given_to_claude(self):
        """16k (ten komputer): z prawdziwym oknem Claude Code kompaktuje bez końca — zostaje założenie 200k."""
        env = local.options("claude", agent(ctx=16384)).env
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", env)
        self.assertNotIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS", env)
        self.assertIn("-c", local.options("codex", agent(ctx=16384)).argv, "Codex dostaje okno także małe")

    def test_full_argv_keeps_prompt_last_and_options_first(self):
        cmd = local.options("codex", agent())
        argv = agent_argv("codex", "/snap/bin/codex", "-zrób coś", ["/i/a.png"], ["/b/docs/x"], cmd.argv)
        self.assertEqual(argv, ["/snap/bin/codex", "--oss", "--local-provider", "ollama", "--model=qwen3-coder:30b",
                                "--add-dir", "/b/docs/x", "--image", "/i/a.png", "--", "-zrób coś"])
        argv = agent_argv("claude", "claude", "zrób coś", (), (), local.options("claude", agent()).argv)
        self.assertEqual(argv, ["claude", "--model=qwen3-coder:30b", "--", "zrób coś"])
        # Model zaczynający się od „-” nie stanie się opcją programu.
        self.assertEqual(local.options("claude", agent(model="-p")).argv, ["--model=-p"])
        self.assertEqual(local.options("codex", agent(model="--help")).argv[-1], "--model=--help")

    def test_server_name(self):
        self.assertEqual(agent().server, "ten komputer")
        self.assertEqual(agent("mac", "http://mac-studio:11434").server, "mac-studio")


class LauncherEnvTest(unittest.TestCase):
    def run_task(self, payload_extra: dict, token: str | None = TOKEN, user_env: dict | None = None,
                 tool: str = "codex"):
        tmp = tempfile.mkdtemp(prefix="tide-local-launch-")
        self.addCleanup(shutil.rmtree, tmp)
        fake = write_exec(os.path.join(tmp, tool), ENV_AGENT)
        shell = write_exec(os.path.join(tmp, "after-shell"), AFTER_SHELL)
        token_file = os.path.join(tmp, "mac.token")
        if token is not None:
            with open(token_file, "w", encoding="utf-8") as fh:
                fh.write(token + "\n")
        log, shell_log = os.path.join(tmp, "agent.log"), os.path.join(tmp, "shell.log")
        cmd = local.options(tool, agent("mac", "http://mac-studio:11434", token_file=token_file))
        argv = agent_argv(tool, fake, "napraw test", (), (), cmd.argv)
        files = TaskFiles(os.path.join(tmp, "run"))
        payload = {"cwd": tmp, "argv": argv, "title": "x · lokalnie", **cmd.task_fields()}
        payload.update(payload_extra)
        task = files.write(payload)
        with open(task, encoding="utf-8") as fh:
            self.assertNotIn(TOKEN, fh.read(), "plik zadania nie zawiera tokenu")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE_CODE"))}
        env.update(TIDE_FAKE_AGENT_LOG=log, TIDE_SHELL_LOG=shell_log, SHELL=shell)
        env.update(user_env or {})
        run = run_launcher(task, env)
        lines = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh]
        after = None
        if os.path.exists(shell_log):
            with open(shell_log, encoding="utf-8") as fh:
                after = fh.read().splitlines()
        return run, lines, after, argv

    def test_token_and_model_env_reach_the_agent_only(self):
        run, lines, after, argv = self.run_task({})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(lines), 1)
        got = lines[0]
        self.assertEqual(got["argv"], argv[1:])
        self.assertNotIn(TOKEN, json.dumps(got["argv"]))
        self.assertEqual(got["env"], {"TIDE_MODEL_TOKEN": TOKEN})
        self.assertEqual(after, ["TIDE_TASK=1"], "powłoka po agencie nie ma zmiennych modelu lokalnego ani tokenu")

    def test_claude_token_never_in_environment(self):
        user = {"ANTHROPIC_AUTH_TOKEN": "token-bramki-uzytkownika"}
        run, lines, after, argv = self.run_task({}, tool="claude", user_env=user)
        self.assertEqual(run.returncode, 0, run.stderr)
        got = lines[0]
        self.assertNotIn(TOKEN, json.dumps(got))
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", got["env"], "apiKeyHelper ma pierwszeństwo tylko bez tej zmiennej")
        self.assertEqual(got["env"]["ANTHROPIC_BASE_URL"], "http://mac-studio:11434")
        self.assertEqual(got["argv"][1], "--settings")
        self.assertIn("apiKeyHelper", got["argv"][2])
        self.assertIn("ANTHROPIC_AUTH_TOKEN=token-bramki-uzytkownika", after, "po agencie wraca wartość użytkownika")
        self.assertNotIn(TOKEN, run.stdout + run.stderr)

    def test_shell_after_agent_gets_the_users_own_values_back(self):
        """Zmienne, które użytkownik miał przed startem, wracają w powłoce po agencie z jego wartościami;
        dodane przez Tide znikają."""
        user = {"ANTHROPIC_BASE_URL": "https://gateway.example", "ANTHROPIC_API_KEY": "klucz-uzytkownika"}
        run, lines, after, _argv = self.run_task({}, user_env=user, tool="claude")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(lines[0]["env"]["ANTHROPIC_BASE_URL"], "http://mac-studio:11434")
        self.assertEqual(lines[0]["env"]["ANTHROPIC_API_KEY"], "")
        self.assertEqual(after, ["ANTHROPIC_API_KEY=klucz-uzytkownika", "ANTHROPIC_BASE_URL=https://gateway.example",
                                 "TIDE_TASK=1"])

    def test_unreadable_token_does_not_start_the_agent(self):
        for tool, token in [(t, k) for t in ("codex", "claude") for k in (None, "", "zły\ntoken z nową linią", "ąę")]:
            with self.subTest(tool=tool, token=token):
                run, lines, after, _argv = self.run_task({}, token=token, tool=tool)
                self.assertEqual(lines, [], "agent bez tokenu nie startuje")
                self.assertIn("agent nie wystartował", run.stdout)
                if token:
                    self.assertNotIn(token, run.stdout + run.stderr)
                self.assertIsNotNone(after, "zostaje zwykła powłoka")

    def test_bad_env_in_task_is_rejected(self):
        for extra in ({"env": {"lower-case": "x"}}, {"env": {"A; rm -rf /": "x"}}, {"secretEnv": ["X"]},
                      {"env": {"X": 1}}, {"unsetEnv": "X"}, {"unsetEnv": ["a-b"]}, {"tokenFiles": [1]},
                      {"tokenFiles": "/k"}):
            with self.subTest(extra=extra):
                run, lines, _after, _argv = self.run_task(extra)
                self.assertEqual(lines, [])
                self.assertIn("nie ma zadania", run.stdout)

    def test_cloud_task_keeps_plain_shell(self):
        self.assertEqual(command({"argv": ["claude", "--", "x"]}),
                         ["/bin/sh", "-c", '"$0" "$@"; exec "${SHELL:-/bin/sh}"', "claude", "--", "x"])


def config(roles=None, endpoints=None):
    data = {"endpoints": endpoints or {"local": {"url": "http://127.0.0.1:11434", "kind": "ollama"},
                                       "mac": {"url": "http://mac-studio:11434", "kind": "ollama",
                                               "tokenFile": "/k/mac.token"}},
            "roles": roles if roles is not None else {"agent": "local:qwen3-coder:30b"}}
    return lambda: models_config.from_dict(data, "/x/models.json")


def probe(online=True, models=("qwen3-coder:30b",), loaded=(), error=""):
    calls = []

    def run(endpoint):
        calls.append(endpoint.name)
        return {"online": online, "error": error, "models": [{"name": m} for m in models],
                "loaded": [{"name": n, "contextLength": c} for n, c in loaded]}
    run.calls = calls
    return run


@unittest.skipUnless(shutil.which("git"), "brak gita")
class PlannerLocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-tasks-local-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "work")
        self.alpha = os.path.join(self.root, "alpha")
        os.makedirs(self.alpha)
        gitrepos.git(self.alpha, "init", "-q", "-b", "master")
        gitrepos.commit(self.alpha, "README.md", "alpha\n")
        self.bin = fake_bin(os.path.join(self.tmp, "bin"))
        self.env = {"PATH": self.bin, "HOME": os.path.join(self.tmp, "home"),
                    "XDG_RUNTIME_DIR": os.path.join(self.tmp, "run"), "XDG_DATA_HOME": os.path.join(self.tmp, "data"),
                    "XDG_STATE_HOME": os.path.join(self.tmp, "state")}

    def planner(self, cfg=None, model_probe=None):
        return Planner(self.root, git=quiet_git(), env=self.env, models_config=cfg or config(),
                       model_probe=model_probe or probe())

    def request(self, **changes):
        base = {"repoPath": self.alpha, "tool": "claude", "prompt": "Popraw nagłówek listy", "newWorktree": True,
                "branch": "", "images": [], "model": "local"}
        base.update(changes)
        return base

    def test_local_claude_plan(self):
        check = probe(loaded=[("qwen3-coder:30b", 65536)])
        plan = self.planner(model_probe=check).prepare(self.request())
        self.assertEqual(check.calls, ["local"], "serwer sprawdzony teraz")
        self.assertEqual(plan.title, "alpha · claude/popraw-naglowek-listy · lokalnie")
        self.assertEqual(plan.argv[1:2], ["--model=qwen3-coder:30b"])
        self.assertEqual(plan.env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "65536", "kontekst z załadowanego modelu")
        with open(plan.task_file, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload, {"cwd": plan.cwd, "argv": plan.argv, "title": plan.title, "env": plan.env})
        result = plan.result("tab")
        self.assertEqual((result["model"], result["local"]),
                         ("local", {"endpoint": "local", "model": "qwen3-coder:30b", "server": "ten komputer"}))

    def test_local_codex_on_mac_with_token_and_role_ctx(self):
        cfg = config({"agent": {"model": "mac:qwen3-coder:480b", "ctx": 131072}})
        plan = self.planner(cfg, probe(models=("qwen3-coder:480b",), loaded=[("qwen3-coder:480b", 4096)])).prepare(
            self.request(tool="codex"))
        self.assertIn('model_provider="tide-mac"', plan.argv)
        self.assertIn("model_context_window=131072", plan.argv, "kontekst z roli wygrywa z załadowanym")
        with open(plan.task_file, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["secretEnv"], {"TIDE_MODEL_TOKEN": "/k/mac.token"})
        self.assertEqual(payload["tokenFiles"], ["/k/mac.token"])
        self.assertNotIn("env", payload)

    def test_cloud_is_the_default_and_unchanged(self):
        check = probe()
        plan = self.planner(model_probe=check).prepare(self.request(model="cloud"))
        self.assertEqual(check.calls, [], "chmura nie pyta serwera modeli")
        self.assertEqual(plan.argv, [os.path.join(self.bin, "claude"), "--", "Popraw nagłówek listy"])
        self.assertEqual(plan.title, "alpha · claude/popraw-naglowek-listy")
        with open(plan.task_file, encoding="utf-8") as fh:
            self.assertEqual(set(json.load(fh)), {"cwd", "argv", "title"})
        request = self.request()
        del request["model"]
        self.assertEqual(self.planner().prepare(request).model, "cloud")

    def test_refusals_create_nothing(self):
        cases = [
            ("bad-model", {}, None, {"model": "gpu"}),
            ("local-unset", config({}), None, {}),
            ("local-unavailable", config({"agent": "gone:qwen3-coder:30b"},
                                         {"local": {"url": "http://127.0.0.1:11434"}, "gone": {"url": "ftp://x"}}),
             None, {}),
            ("local-unavailable", None, probe(online=False, error="Model lokalny niedostępny: nie odpowiedział"), {}),
            ("local-unavailable", None, probe(models=("qwen3:8b",)), {}),
        ]
        for code, cfg, check, changes in cases:
            with self.subTest(code=code, changes=changes):
                planner = self.planner(cfg or config(), check)
                with self.assertRaises(TaskError) as ctx:
                    planner.prepare(self.request(**changes))
                self.assertEqual(ctx.exception.code, code, ctx.exception.message)
                self.assertTrue(ctx.exception.message)
                self.assertEqual(gitrepos.branches(self.alpha), ["master"], "odmowa nie zakłada gałęzi")
        with self.assertRaises(TaskError) as ctx:
            self.planner(config({}), None).prepare(self.request())
        self.assertIn("Tide → Modele", ctx.exception.message)
        with self.assertRaises(TaskError) as ctx:
            self.planner(model_probe=probe(models=())).prepare(self.request())
        self.assertIn("qwen3-coder:30b", ctx.exception.message)

    def test_ollama_latest_tag_counts_as_installed(self):
        cfg = config({"agent": "local:bge-m3"})
        plan = self.planner(cfg, probe(models=("bge-m3:latest",))).prepare(self.request())
        self.assertIn("--model=bge-m3", plan.argv)

    def test_remembers_local_choice(self):
        planner = self.planner()
        plan = planner.prepare(self.request(newWorktree=False))
        planner.remember(plan)
        self.assertEqual(planner.last_choice()["model"], "local")
        self.assertEqual(planner.defaults()["model"], "local")
        with open(planner.state_path, "w", encoding="utf-8") as fh:
            json.dump({"model": "gpu"}, fh)
        self.assertEqual(planner.last_choice()["model"], "cloud")

    def test_models_json_from_the_service_environment(self):
        path = os.path.join(self.tmp, "models.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"agent": "qwen3-coder:30b"}}, fh)
        planner = Planner(self.root, git=quiet_git(), env=dict(self.env, TIDE_MODELS_CONFIG=path), model_probe=probe())
        self.assertEqual(planner.local_agent().model, "qwen3-coder:30b")


class SessionDetectionTest(unittest.TestCase):
    """Lista dopuszczonych, nie wykluczeń: fałszywe „lokalnie” to fałszywa informacja o prywatności."""

    def feed(self, cls, records):
        log = cls("/x/rollout-2026-10-27T14-41-29-01a17d98-35d7-7002-927d-6c9f7506f213.jsonl"
                  if cls is CodexLog else "/x/proj/abc.jsonl")
        for record in records:
            log.feed(record)
        return log

    def codex(self, provider, model="qwen3-coder:30b"):
        meta = {"type": "session_meta", "timestamp": "2026-10-27T13:41:29Z",
                "payload": {"id": "s1", "cwd": "/w", "model_provider": provider}}
        turn = {"type": "turn_context", "timestamp": "2026-10-27T13:41:30Z",
                "payload": {"cwd": "/w", "model": model, "approval_policy": "never"}}
        return self.feed(CodexLog, [meta, turn])

    def test_codex_provider_allowlist(self):
        log = self.codex("ollama")
        self.assertEqual((log.provider, log.model, session_is_local(log)), ("ollama", "qwen3-coder:30b", True))
        for provider in ("ollama", "lmstudio", "oss", "tide-tok", "tide-mac"):
            self.assertTrue(session_is_local(self.codex(provider)), provider)
        for provider in ("openai", "azure-openai", "amazon-bedrock", "bedrock", "together", "fireworks", "azure",
                         "openrouter", "tide", "my-ollama-proxy", "", None):
            self.assertFalse(session_is_local(self.codex(provider)), provider)
            self.assertFalse(is_local_codex_provider(provider), provider)

    def test_claude_model(self):
        def answer(model):
            return {"type": "assistant", "timestamp": "2026-10-27T13:41:30Z", "cwd": "/w",
                    "message": {"model": model, "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}}
        log = self.feed(ClaudeLog, [answer("qwen3-coder:30b"), answer("<synthetic>")])
        self.assertEqual((log.model, session_is_local(log)), ("qwen3-coder:30b", True), "<synthetic> nie zmienia modelu")
        log = self.feed(ClaudeLog, [answer("qwen3-coder:30b"), answer("claude-opus-5-5")])
        self.assertEqual((log.model, session_is_local(log)), ("claude-opus-5-5", False), "po /model liczy się nowy")
        # Etykieta w stylu Ollamy „nazwa:tag” bez Claude'a — lokalny.
        for model in ("qwen3-coder:30b", "gpt-oss:20b", "bge-m3:latest",
                      "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M"):
            self.assertTrue(is_local_claude_model(model), model)
        # W razie wątpliwości — nie: Claude, aliasy, Bedrock (ARN, profile), modele przez bramkę.
        for model in ("claude-opus-5-5", "claude-opus-5-5[1m]", "us.anthropic.claude-sonnet-5-5-v1:0", "sonnet",
                      "<synthetic>", "", None, "gpt-5", "fable-5", "my-gateway-opus",
                      "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc",
                      "anthropic/claude-opus-4-8", "claude:latest", "global.anthropic.claude-haiku-5-5:0"):
            self.assertFalse(is_local_claude_model(model), model)

    def test_claude_model_known_from_models_json(self):
        """Model bez tagu, ale z models.json (rola albo model na serwerze) — lokalny; inaczej nie."""
        self.assertFalse(is_local_claude_model("qwen3-coder"))
        self.assertTrue(is_local_claude_model("qwen3-coder", {"qwen3-coder"}))
        self.assertTrue(is_local_claude_model("bge-m3", {"bge-m3:latest"}), "bge-m3 i bge-m3:latest to ten sam model")
        self.assertTrue(is_local_claude_model("qwen/qwen3-coder-next", {"qwen/qwen3-coder-next"}))
        self.assertFalse(is_local_claude_model("claude-opus-5-5", {"claude-opus-5-5"}), "Claude nigdy nie jest lokalny")

    def test_known_models_from_status_and_config(self):
        status = {"roles": {"agent": {"model": "qwen3-coder"}, "summary": {"model": "qwen3:8b"}},
                  "endpoints": {"mac": {"models": [{"name": "gemma4:27b"}, {"name": "qwen/qwen3-coder-next"}]}}}
        self.assertEqual(known_models(status), {"qwen3-coder", "qwen3:8b", "gemma4:27b", "qwen/qwen3-coder-next"})
        self.assertEqual(known_models(None), set())
        self.assertEqual(known_models({"roles": [], "endpoints": {"x": {"models": None}}}), set())

    def test_scanner_marks_sessions_with_known_models(self):
        tmp = tempfile.mkdtemp(prefix="tide-local-scan-")
        self.addCleanup(shutil.rmtree, tmp)
        folder = os.path.join(tmp, "claude", "projects", "-w")
        os.makedirs(folder)
        now = 1_793_016_000
        for name, model in (("a", "qwen3-coder"), ("b", "gpt-5"), ("c", "qwen3-coder:30b")):
            with open(os.path.join(folder, name + ".jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "assistant", "timestamp": "2026-10-26T12:00:00Z", "cwd": "/w",
                                     "message": {"model": model, "stop_reason": "end_turn", "content": []}}) + "\n")
            os.utime(os.path.join(folder, name + ".jsonl"), (now, now))
        scanner = AgentScanner(os.path.join(tmp, "claude"), os.path.join(tmp, "codex"))
        scanner.known_local_models = lambda: {"qwen3-coder"}
        scanner.discover(now)
        scanner.update()
        got = {s["sessionId"]: (s["model"], s["localModel"]) for s in scanner.sessions(now)}
        self.assertEqual(got, {"a": ("qwen3-coder", True), "b": ("gpt-5", False), "c": ("qwen3-coder:30b", True)})

    def test_tab_label(self):
        session = {"project": "calc", "branch": "claude/x", "localModel": True}
        self.assertEqual(tabmarks.session_label(session), "calc · claude/x · lokalnie")
        self.assertEqual(tabmarks.session_label({**session, "localModel": False}), "calc · claude/x")


if __name__ == "__main__":
    unittest.main()
