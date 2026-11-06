"""Opcjonalny test natywnego init, bez modelu, kluczy i usług użytkownika.

TIDE_SKILLS_CLAUDE_BIN=/ścieżka/do/claude włącza próbę. Proces kończymy po init;
adres API wskazuje zamknięty port lokalny, a HOME i konfiguracja są atrapami.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import unittest

from . import test_background_skills as skill_tests
from .test_agentcmd import BackgroundBase
from tide_daemon.tasks import agentcmd


@unittest.skipUnless(os.environ.get("TIDE_SKILLS_CLAUDE_BIN"), "opcjonalny test natywnego Claude Code")
class NativeSkillsTest(BackgroundBase):
    def test_init_lists_required_skills_without_user_hooks_or_mcp(self):
        required = ("to-spec", "to-tickets", "grill-with-docs", "tdd", "diagnosing-bugs")
        for name in required:
            skill_tests.BackgroundSkillsTest.skill(self, os.path.join(self.home, ".claude", "skills"), name)
        marker = os.path.join(self.home, "hook-must-not-run")
        user_settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
                         "command": f"touch {marker}"}]}]}, "permissions": {"allow": ["Bash(*)"]}}
        with open(os.path.join(self.home, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(user_settings, fh)
        with open(os.path.join(self.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"must-not-load": {"command": "/nonexistent/user-mcp"}}}, fh)
        cmd = agentcmd.background(self.step(binary=os.environ["TIDE_SKILLS_CLAUDE_BIN"]))
        cmd.prepare()
        env = dict(cmd.env, ANTHROPIC_API_KEY="test-not-a-real-key", ANTHROPIC_BASE_URL="http://127.0.0.1:1",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", ANTHROPIC_MAX_RETRIES="0")
        proc = subprocess.Popen(cmd.argv, cwd=cmd.cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        captured = bytearray()
        init = None
        try:
            proc.stdin.write(cmd.stdin.encode("utf-8"))
            proc.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 30
                pending = b""
                while init is None and time.monotonic() < deadline:
                    if not selector.select(timeout=0.2):
                        continue
                    data = os.read(proc.stdout.fileno(), 65536)
                    if not data:
                        break
                    captured.extend(data)
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") == "system" and event.get("subtype") == "init":
                            init = event
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            proc.stdout.close()
        evidence = Path(__file__).resolve().parents[2] / "build" / "test-results" / "skills-init"
        evidence.mkdir(parents=True, exist_ok=True)
        log = evidence / f"{self.sid}-step.log"
        log.write_bytes(captured)
        self.assertIsNotNone(init, f"Brak init: {log}\n{captured.decode('utf-8', errors='replace')}")
        listing = json.dumps([init.get("skills", []), init.get("slash_commands", [])])
        for name in required:
            self.assertIn(f"tide-user-skills:{name}", listing, str(log))
        self.assertFalse(init.get("mcp_servers"), str(log))
        self.assertFalse(os.path.exists(marker), str(log))
