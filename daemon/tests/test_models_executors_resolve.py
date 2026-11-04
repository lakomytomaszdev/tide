"""Wykonawca z nazwy wpisanej w planie (K-41, 04.11: „Codex” zamiast „Codex mocny” kończyło się błędem formatu)."""
import unittest

from tide_daemon.models.executors import default_catalog


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.catalog = default_catalog()

    def name(self, *args):
        found = self.catalog.resolve(*args)
        return found.name if found else None

    def test_exact_case_and_spacing(self):
        self.assertEqual(self.name("Codex mocny"), "Codex mocny")
        self.assertEqual(self.name("codex  MOCNY"), "Codex mocny")
        self.assertEqual(self.name("opus"), "Opus")
        self.assertEqual(self.name("Fable"), "Fable")

    def test_provider_name_picks_the_tickets_class_with_another_model(self):
        self.assertEqual(self.name("Codex", "mocny"), "Codex mocny")
        self.assertEqual(self.name("Codex", "lekki"), "Codex lekki")
        self.assertEqual(self.name("OpenAI", "standard"), "Codex standard")
        self.assertEqual(self.name("Claude", "standard"), "Sonnet")
        # Recenzent nie może mieć modelu autora: „Codex” przy autorze Codex mocny (gpt-6.1-sol) to inny model.
        author = self.catalog.get("Codex mocny")
        reviewer = self.catalog.resolve("Codex", "mocny", author.model)
        self.assertNotEqual(reviewer.model, author.model)

    def test_unknown_stays_unknown(self):
        self.assertIsNone(self.name("Gemini", "mocny"))
        self.assertIsNone(self.name(""))
