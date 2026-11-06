"""PT-246: rzeczywiste powłoki w natywnych profilach, bez modelu i konfiguracji użytkownika.

Uruchomienie poza piaskownicą kroku:
TIDE_READ_DIRS_NATIVE=1 python3 -m unittest daemon.tests.test_project_read_dirs_native -v
Claude dostaje atrapę API na localhost, która zleca jedno Bash i kończy rozmowę. Codex używa
`sandbox linux` bez API. Brak dowodu lub niedostępna piaskownica to porażka, nigdy skip po próbie.
"""
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from . import gitrepos
from tide_daemon.tasks import agentcmd

PROBE = '''import json, pathlib, shlex, subprocess, sys
out, target, alias = sys.argv[1:]
report = {}
for name, root in (("direct", target), ("symlink", alias)):
    file = shlex.quote(root + "/data.txt")
    dest = shlex.quote(root + "/renamed.txt")
    commands = {"read": "cat -- " + file,
                "write": "printf changed > " + file,
                "create": "printf changed > " + shlex.quote(root + "/new.txt"),
                "delete": "rm -- " + file,
                "rename": "mv -- " + file + " " + dest}
    report[name] = {}
    for action, command in commands.items():
        run = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
        report[name][action] = {"code": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
pathlib.Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
'''


def api_handler(command):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if "/messages" not in self.path:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
                return
            has_result = any(block.get("type") == "tool_result"
                for msg in data.get("messages", []) for block in
                (msg.get("content") if isinstance(msg.get("content"), list) else []))
            content = ({"type": "text", "text": "Gotowe."} if has_result else
                       {"type": "tool_use", "id": "toolu_tide_probe", "name": "Bash",
                        "input": {"command": command, "description": "Sprawdź profil plików"}})
            message = {"id": "msg_tide_probe", "type": "message", "role": "assistant", "model": "test",
                       "content": [content], "stop_reason": "end_turn" if has_result else "tool_use",
                       "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            self.send_response(200)
            if data.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                start = {**content, "input": {}} if not has_result else {"type": "text", "text": ""}
                delta = ({"type": "input_json_delta", "partial_json": json.dumps(content["input"])}
                         if not has_result else {"type": "text_delta", "text": content["text"]})
                events = [("message_start", {"message": {**message, "content": [], "stop_reason": None}}),
                          ("content_block_start", {"index": 0, "content_block": start}),
                          ("content_block_delta", {"index": 0, "delta": delta}),
                          ("content_block_stop", {"index": 0}),
                          ("message_delta", {"delta": {"stop_reason": message["stop_reason"],
                                                        "stop_sequence": None},
                                             "usage": {"output_tokens": 1}}),
                          ("message_stop", {})]
                for event, payload in events:
                    self.wfile.write(("event: " + event + "\ndata: " + json.dumps({"type": event, **payload})
                                      + "\n\n").encode())
            else:
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(message).encode())
    return Handler


@unittest.skipUnless(os.environ.get("TIDE_READ_DIRS_NATIVE") == "1", "natywne piaskownice wymagają outside")
class NativeReadDirsTest(unittest.TestCase):
    def setUp(self):
        self.evidence = Path(__file__).resolve().parents[2] / "build" / "test-results" / "read-dirs-native"
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="fixture-", dir=self.evidence)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.repo = self.home / "repo"
        self.wt = self.home / "wt"
        self.step_dir = self.home / "step"
        self.data = self.home / "data"
        self.data.mkdir(parents=True)
        (self.data / "data.txt").write_text("TIDE_READ_PROOF\n")
        patch = mock.patch.dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
                                GIT_AUTHOR_NAME="Tester", GIT_AUTHOR_EMAIL="tester@example.com",
                                GIT_COMMITTER_NAME="Tester", GIT_COMMITTER_EMAIL="tester@example.com")
        patch.start()
        self.addCleanup(patch.stop)
        self.repo.mkdir()
        gitrepos.git(str(self.repo), "init", "-q", "-b", "master")
        gitrepos.commit(str(self.repo), "a.txt")
        gitrepos.git(str(self.repo), "worktree", "add", "-q", "-b", "step", str(self.wt))
        self.alias = self.wt / "data-link"
        self.alias.symlink_to(self.data, target_is_directory=True)

    def command(self, tool):
        binary = (shutil.which(tool) or ("/snap/bin/codex" if tool == "codex" else
                                       os.path.expanduser("~/.local/bin/claude")))
        self.assertTrue(os.access(binary, os.X_OK), f"Brak {tool}: {binary}")
        step = agentcmd.Step(tool, binary, "Wykonaj próbę profilu.", str(self.wt), str(self.repo),
            str(self.step_dir), "native-probe", model="test", read_dirs=(str(self.data),),
            allowed_tools=("Bash",), permission_mode="acceptEdits", codex_mcp=(),
            base_env={"HOME": str(self.home), "USER": os.environ.get("USER", "tester"),
                      "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        cmd = agentcmd.background(step)
        cmd.prepare()
        probe = self.step_dir / "out" / "probe.py"
        probe.write_text(PROBE)
        report = self.step_dir / "out" / "proof.json"
        script = shlex.join(["/usr/bin/python3", str(probe), str(report), str(self.data), str(self.alias)])
        return cmd, report, script

    def verify(self, tool, report, run):
        (self.evidence / f"{tool}-process.log").write_text(run.stdout + "\n" + run.stderr)
        self.assertEqual(run.returncode, 0, run.stderr[-4000:])
        self.assertTrue(report.is_file(), f"Brak dowodu: {self.evidence / (tool + '-process.log')}")
        proof = json.loads(report.read_text())
        (self.evidence / f"{tool}-proof.json").write_text(report.read_text())
        for route in ("direct", "symlink"):
            self.assertEqual(proof[route]["read"]["code"], 0, proof)
            self.assertEqual(proof[route]["read"]["stdout"], "TIDE_READ_PROOF\n", proof)
            for action in ("write", "create", "delete", "rename"):
                self.assertNotEqual(proof[route][action]["code"], 0, (route, action, proof))
                self.assertTrue(proof[route][action]["stderr"], (route, action, proof))
        self.assertEqual((self.data / "data.txt").read_text(), "TIDE_READ_PROOF\n")
        self.assertFalse((self.data / "new.txt").exists())
        self.assertFalse((self.data / "renamed.txt").exists())

    def test_codex_native_filesystem_read_and_shell_write_denials(self):
        cmd, report, script = self.command("codex")
        options = []
        for i, arg in enumerate(cmd.argv[:-1]):
            if arg == "-c" and cmd.argv[i + 1].startswith("sandbox_workspace_write."):
                options += [arg, cmd.argv[i + 1]]
        isolated = self.home / "codex"
        isolated.mkdir()
        env = dict(cmd.env, CODEX_HOME=str(isolated))
        run = subprocess.run([cmd.argv[0], "sandbox", "linux", "--full-auto", *options,
                              "--", "/bin/sh", "-c", script],
                             cwd=self.wt, env=env, text=True, capture_output=True, timeout=60)
        self.verify("codex", report, run)

    def test_claude_native_filesystem_read_and_shell_write_denials(self):
        cmd, report, script = self.command("claude")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), api_handler(script))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = dict(cmd.env, ANTHROPIC_API_KEY="test-not-a-real-key",
                   ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", ANTHROPIC_MAX_RETRIES="0")
        run = subprocess.run(cmd.argv, input=cmd.stdin, cwd=self.wt, env=env,
                             text=True, capture_output=True, timeout=60)
        self.verify("claude", report, run)
