"""Rozwiązanie wykonawcy z katalogu dla kroku karty, bez Qt i bez prawdziwego serwera."""
import os
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow import execution
from tide_daemon.models import executors


class ExecutionTest(unittest.TestCase):
    def test_snap_cli_falls_back_to_bundled_cli_for_current_models(self):
        with tempfile.TemporaryDirectory() as temp:
            bundled = os.path.join(temp, "codex")
            with open(bundled, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(bundled, 0o755)
            with mock.patch.object(execution.codex_homes, "DESKTOP_CODEX", bundled):
                chosen = execution.resolve("Codex Astra", executors.default_catalog(),
                                           which=lambda _name: "/snap/bin/codex")
            self.assertEqual(chosen.binary, bundled)

    def test_codex_effort_is_kept_only_for_codex(self):
        catalog = executors.default_catalog()
        codex = execution.resolve("Codex mocny", catalog, which=lambda name: "/fake/" + name)
        claude = execution.resolve("Sonnet", catalog, which=lambda name: "/fake/" + name)
        self.assertEqual((codex.tool, codex.model, codex.effort), ("codex", "gpt-6.1-sol", "high"))
        self.assertEqual((claude.tool, claude.effort), ("claude", ""))

    def test_other_provider_routes_to_its_api_and_keeps_token_as_file_path(self):
        token = os.path.expanduser("~/token-pt-60-test")
        catalog = executors.Catalog(
            (executors.Executor("Inny", "codex", "model-inny", "other"),),
            {"other": executors.Provider("other", "Inny", "https://example.test/v1", "openai", token)})
        chosen = execution.resolve("Inny", catalog, which=lambda name: "/fake/" + name)
        self.assertEqual((chosen.tool, chosen.provider, chosen.local), ("codex", "other", False))
        self.assertEqual((chosen.route.url, chosen.route.kind, chosen.route.token_file),
                         ("https://example.test", "openai", token))

    def test_local_without_configuration_is_refused(self):
        with self.assertRaises(execution.ExecutionError) as caught:
            execution.resolve("Lokalny", executors.default_catalog(), which=lambda name: "/fake/" + name)
        self.assertEqual(caught.exception.code, "local-unavailable")


class AgentWhichTest(unittest.TestCase):
    """Usługa systemd bez ~/.local/bin w PATH nadal znajduje `claude` z instalatora Claude Code."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = temp.name
        self.bin = os.path.join(self.home, ".local", "bin")
        os.makedirs(self.bin)
        empty = os.path.join(self.home, "empty-path")
        os.makedirs(empty)
        patcher = mock.patch.dict(os.environ, {"PATH": empty})
        patcher.start()
        self.addCleanup(patcher.stop)

    def program(self, folder, name, mode=0o755):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, mode)
        return path

    def test_local_bin_is_used_when_path_has_no_program(self):
        claude = self.program(self.bin, "claude")
        self.assertEqual(execution.agent_which("claude", home=self.home), claude)
        chosen = execution.resolve("Sonnet", executors.default_catalog(),
                                   which=lambda name: execution.agent_which(name, home=self.home))
        self.assertEqual(chosen.binary, claude)

    def test_path_wins_over_local_bin(self):
        self.program(self.bin, "claude")
        other = os.path.join(self.home, "other")
        os.makedirs(other)
        path_claude = self.program(other, "claude")
        with mock.patch.dict(os.environ, {"PATH": other}):
            self.assertEqual(execution.agent_which("claude", home=self.home), path_claude)

    def test_missing_or_not_executable_program_is_none(self):
        self.program(self.bin, "codex", mode=0o644)
        self.assertIsNone(execution.agent_which("codex", home=self.home))
        self.assertIsNone(execution.agent_which("claude", home=self.home))
        with self.assertRaises(execution.ExecutionError) as caught:
            execution.resolve("Sonnet", executors.default_catalog(),
                              which=lambda name: execution.agent_which(name, home=self.home))
        self.assertEqual(caught.exception.code, "no-program")


if __name__ == "__main__":
    unittest.main()
