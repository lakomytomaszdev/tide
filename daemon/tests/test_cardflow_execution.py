"""Rozwiązanie wykonawcy z katalogu dla kroku karty, bez Qt i bez prawdziwego serwera."""
import os
import unittest

from tide_daemon.cardflow import execution
from tide_daemon.models import executors


class ExecutionTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
