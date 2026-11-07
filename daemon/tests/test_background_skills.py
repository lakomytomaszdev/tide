"""Skille kroku są dostępne bez wczytania ustawień, hooków ani MCP użytkownika."""
import json
import os

from .test_agentcmd import BackgroundBase
from tide_daemon.tasks import agentcmd


class BackgroundSkillsTest(BackgroundBase):
    def test_caretaker_keeps_skills_and_read_only_command_policy(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                root = os.path.join(self.home, ".claude" if tool == "claude" else ".agents", "skills")
                path = self.skill(root, "caretaker-" + tool)
                cmd = agentcmd.background(self.step(tool, codex_mcp=(), write_out_only=True,
                    enforce_command_policy=True, allowed_tools=("Bash(make install)",)))
                self.assertIn(path, cmd.stdin)
                self.assertEqual(cmd.workdir, os.path.join(self.step_dir, "out"))
                cmd.prepare()
                if tool == "claude":
                    self.assertIn("--plugin-dir", cmd.argv)
                    self.assertNotIn("Bash(make install)", cmd.argv)
                    self.assertIn(f"Edit(/{os.path.normpath(self.worktree)}/**)", cmd.argv)
                    fs = self.settings(cmd)["sandbox"]["filesystem"]
                    self.assertEqual(fs["allowWrite"], [cmd.workdir])
                    self.assertIn(os.path.dirname(path), fs["denyWrite"])
                else:
                    roots = json.loads(next(arg.split("=", 1)[1] for arg in cmd.argv
                        if arg.startswith("sandbox_workspace_write.writable_roots=")))
                    self.assertEqual(roots, [cmd.workdir])

    def skill(self, root, name, extra=""):
        folder = os.path.join(root, name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "SKILL.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {name}\ndescription: Procedura {name}\n{extra}---\nInstrukcja\n")
        return path

    def test_claude_step_exposes_user_skills_without_enabling_setting_sources(self):
        source = os.path.join(self.home, "procedures")
        paths = [self.skill(source, name) for name in
                 ("to-spec", "to-tickets", "grill-with-docs", "tdd", "diagnosing-bugs")]
        os.makedirs(os.path.join(self.home, ".claude"))
        os.symlink(source, os.path.join(self.home, ".claude", "skills"))
        cmd = agentcmd.background(self.step())
        self.assertIn("--setting-sources=", cmd.argv)
        self.assertIn("--strict-mcp-config", cmd.argv)
        self.assertIn("--plugin-dir", cmd.argv)
        for path in paths:
            self.assertIn(path, cmd.stdin)
            self.assertIn(os.path.dirname(path), self.settings(cmd)["sandbox"]["filesystem"]["allowRead"])
        cmd.prepare()
        plugin = cmd.argv[cmd.argv.index("--plugin-dir") + 1]
        for name in ("to-spec", "to-tickets", "grill-with-docs", "tdd", "diagnosing-bugs"):
            with open(os.path.join(plugin, "skills", name, "SKILL.md"), encoding="utf-8") as fh:
                self.assertIn(f"name: {name}", fh.read())

    def test_claude_does_not_import_executable_skill_metadata_or_sibling_configuration(self):
        root = os.path.join(self.home, ".claude", "skills")
        path = self.skill(root, "to-spec", extra=(
            "hooks:\n  SessionStart: dangerous-hook\nallowed-tools: Bash(*)\n"
            "context: fork\ndisable-model-invocation: true\n"))
        for name in ("settings.json", ".mcp.json"):
            with open(os.path.join(self.home, ".claude", name), "w", encoding="utf-8") as fh:
                fh.write("user-configuration-must-not-load")
        cmd = agentcmd.background(self.step(enforce_command_policy=True, allowed_tools=("Bash(git status*)",)))
        wrappers = [text for file, text in cmd.files.items() if file.endswith("SKILL.md")]
        self.assertEqual(len(wrappers), 1)
        self.assertIn(path, wrappers[0])
        for forbidden in ("dangerous-hook", "allowed-tools", "context: fork", "disable-model-invocation",
                          "user-configuration-must-not-load"):
            self.assertNotIn(forbidden, json.dumps(cmd.files))
        fs = self.settings(cmd)["sandbox"]["filesystem"]
        folder = os.path.dirname(path)
        self.assertIn(folder, fs["allowRead"])
        self.assertIn(folder, fs["denyWrite"])
        self.assertNotIn(folder, fs["allowWrite"])
        self.assertNotIn(folder, cmd.argv)
        self.assertEqual(self.settings(cmd)["permissions"]["allow"], [f"Read(/{folder}/**)"])
        self.assertEqual(self.settings(cmd)["permissions"]["deny"], [f"Edit(/{folder}/**)"])
        self.assertFalse(self.settings(cmd)["sandbox"]["autoAllowBashIfSandboxed"])

    def test_codex_gets_both_skill_roots_without_adding_them_to_writable_roots(self):
        modern = os.path.join(self.home, ".agents", "skills")
        legacy = os.path.join(self.home, ".codex", "skills")
        paths = [self.skill(modern, name) for name in ("to-spec", "grill-with-docs", "diagnosing-bugs")]
        paths += [self.skill(legacy, name) for name in ("to-tickets", "tdd")]
        self.skill(legacy, "to-spec")
        for resume in (False, True):
            with self.subTest(resume=resume):
                cmd = agentcmd.background(self.step("codex", codex_mcp=("user-server",), resume=resume))
                entries = json.loads(cmd.files[os.path.join(self.step_dir, "in", "skills.json")])
                self.assertEqual({entry["path"] for entry in entries}, set(paths))
                for path in paths:
                    self.assertIn(path, cmd.stdin)
                roots = json.loads(next(arg.split("=", 1)[1] for arg in cmd.argv
                                        if arg.startswith("sandbox_workspace_write.writable_roots=")))
                self.assertFalse(any(root.startswith((modern, legacy)) for root in roots))
                self.assertIn("mcp_servers.user-server.enabled=false", cmd.argv)
                self.assertIn('approval_policy="never"', cmd.argv)
                self.assertNotIn("--plugin-dir", cmd.argv)

    def test_nested_and_symlinked_skills_are_deduplicated_and_cycles_are_ignored(self):
        root = os.path.join(self.home, ".claude", "skills")
        path = self.skill(os.path.join(root, "nested"), "to-spec")
        os.symlink(root, os.path.join(root, "cycle"))
        os.symlink(os.path.dirname(path), os.path.join(root, "alias"))
        os.symlink("/nonexistent/missing-skill", os.path.join(root, "broken"))
        self.skill(root, "tdd")
        cmd = agentcmd.background(self.step(resume=True))
        entries = json.loads(cmd.files[os.path.join(self.step_dir, "in", "skills.json")])
        self.assertEqual({entry["name"] for entry in entries}, {"to-spec", "tdd"})
        self.assertEqual(len(entries), 2)
        self.assertIn(path, cmd.stdin)
        self.assertIn("--resume", cmd.argv)

    def test_missing_skills_are_explicit_and_do_not_enable_a_plugin(self):
        for tool in ("claude", "codex"):
            cmd = agentcmd.background(self.step(tool, codex_mcp=()))
            self.assertIn("Brak dostępnych skilli użytkownika", cmd.stdin)
            self.assertIn("result=error", cmd.stdin)
            self.assertNotIn("--plugin-dir", cmd.argv)
            self.assertEqual(json.loads(cmd.files[os.path.join(self.step_dir, "in", "skills.json")]), [])

    def test_prepare_refreshes_the_catalog_when_resuming(self):
        root = os.path.join(self.home, ".claude", "skills")
        self.skill(root, "to-spec")
        first = agentcmd.background(self.step())
        first.prepare()
        self.skill(root, "tdd")
        second = agentcmd.background(self.step(resume=True))
        second.prepare()
        with open(os.path.join(self.step_dir, "in", "skills.json"), encoding="utf-8") as fh:
            self.assertEqual({entry["name"] for entry in json.load(fh)}, {"to-spec", "tdd"})
        os.unlink(os.path.join(root, "to-spec", "SKILL.md"))
        third = agentcmd.background(self.step(resume=True))
        third.prepare()
        plugin = third.argv[third.argv.index("--plugin-dir") + 1]
        self.assertFalse(os.path.exists(os.path.join(plugin, "skills", "to-spec", "SKILL.md")))
