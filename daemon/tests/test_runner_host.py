"""PT-180 — HostRunner buduje dokładnie dzisiejsze argv i środowisko kroku.

Złoty plik `fixtures/runner/host_argv.json` powstał z kodu sprzed wprowadzenia `Runner` (`agentcmd.background`
+ `Background.start`). Test składa kroki (Claude, Codex, wznowienie, punkt lokalny z tokenem), przechwytuje
wywołanie `subprocess.Popen` i porównuje z zapisem. Prawdziwy program ani systemd-run nie startują.
Gdy pliku brakuje albo jest pusty, test go zapisuje i kończy się błędem (odtworzenie świadome: opróżnij plik).
"""
import json
import os
from unittest import mock

from tide_daemon.tasks import agentcmd, local
from tide_daemon.tasks.runner import HostRunner
from .test_agentcmd import BackgroundBase

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "runner", "host_argv.json")


def start_step(step, systemd_run=""):
    """Jedyne miejsce, które zna sposób uruchomienia kroku w tym teście."""
    return HostRunner(systemd_run).start(step)


class HostArgvTest(BackgroundBase):
    def setUp(self):
        super().setUp()
        self.sid = "bgolden01"

    def cases(self):
        mac = local.LocalAgent("mac", "http://mac.lan:11434", "ollama", "qwen3-coder:30b", 65536, self.token_file)
        return {
            "claude": self.step(),
            "claude-model": self.step(model="haiku", permission_mode="acceptEdits",
                                      allowed_tools=("Bash(make test*)",), disallowed_tools=("Bash(git push*)",),
                                      enforce_command_policy=True),
            "claude-local": self.step(local=mac, model="qwen3-coder:30b"),
            "codex": self.step("codex", model="gpt-5-codex", effort="high", codex_mcp=()),
            "codex-resume": self.step("codex", resume=True, session_id="abc", codex_mcp=("linear",)),
        }

    def capture(self, step):
        calls = []

        def popen(argv, **kw):
            calls.append({"argv": argv, "cwd": kw.get("cwd"), "env": kw.get("env"),
                          "newSession": kw.get("start_new_session"),
                          "stdin": kw["stdin"].read().decode() if kw.get("stdin") else None})
            return mock.Mock(returncode=None)

        with mock.patch.object(agentcmd.subprocess, "Popen", popen):
            bg, _proc = start_step(step, "/x/systemd-run")
        self.assertEqual(len(calls), 1)
        got = {"unit": bg.unit, "gitDir": bg.git_dir, "files": bg.files, "dirs": bg.dirs, "popen": calls[0]}
        return json.loads(json.dumps(got, sort_keys=True).replace(self.root, "<ROOT>"))

    def test_argv_and_env_match_the_recorded_pre_refactor_output(self):
        got = {name: self.capture(step) for name, step in self.cases().items()}
        if not os.path.exists(GOLDEN) or not os.path.getsize(GOLDEN):
            os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
            with open(GOLDEN, "w", encoding="utf-8") as fh:
                json.dump(got, fh, indent=1, sort_keys=True, ensure_ascii=False)
                fh.write("\n")
            self.fail("zapisano złoty plik " + GOLDEN)
        with open(GOLDEN, encoding="utf-8") as fh:
            want = json.load(fh)
        self.assertEqual(sorted(got), sorted(want))
        for name in got:
            with self.subTest(name):
                self.assertEqual(got[name], want[name])

    def test_env_is_the_environment_the_started_step_gets(self):
        """`HostRunner.env` = środowisko kroku z `start` bez XDG_RUNTIME_DIR i zmiennych z `secret_env` (także dla
        punktu lokalnego, który zmienia i usuwa zmienne)."""
        for name, step in self.cases().items():
            with self.subTest(name):
                calls = []
                with mock.patch.object(agentcmd.subprocess, "Popen",
                                       lambda argv, **kw: calls.append(kw) or mock.Mock(returncode=None)):
                    bg, _proc = start_step(step, "/x/systemd-run")
                started = {k: v for k, v in calls[0]["env"].items()
                           if k != "XDG_RUNTIME_DIR" and k not in bg.secret_env}
                self.assertEqual(HostRunner().env(step), started)
        self.assertIn("ANTHROPIC_BASE_URL", HostRunner().env(self.cases()["claude-local"]))
