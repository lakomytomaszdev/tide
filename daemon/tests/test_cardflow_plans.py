"""Sprawdzanie propozycji planu przed rezerwacją numerów."""
import os
import tempfile
import unittest

from daemon.tide_daemon.cardflow import plans
from daemon.tide_daemon.models.executors import default_catalog


class ProposalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "plan.md")
        os.mkdir(os.path.join(self.tmp.name, "tickets"))

    def write(self, rows, cards):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ncard: K-2\ntracker: local\nticket-prefix: PT\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + rows)
        for name, body in cards.items():
            with open(os.path.join(self.tmp.name, "tickets", name + ".md"), "w", encoding="utf-8") as fh:
                fh.write(body)

    def check(self):
        return plans.read(self.path, "K-2", default_catalog(), allowed_roots=(self.tmp.name,))

    def test_valid_plan_has_stable_waves_and_cards(self):
        self.write("| #1 | Pierwszy | todo | | Sonnet | prosty | standard | Opus | |\n"
                   "| #2 | Drugi | todo | | Codex mocny | trudny | mocny | Codex Astra | #1 |\n"
                   "| #3 | Trzeci | todo | | Sonnet | niezależny | standard | Opus | |\n",
                   {n: "# Kartka\n\n## Kryteria akceptacji\n\n- [ ] Działa.\n" for n in ("1", "2", "3")})
        got = self.check()
        self.assertEqual(got.errors, [])
        self.assertEqual(got.waves, [["#1", "#3"], ["#2"]])
        self.assertEqual(got.rows[1]["blockers"], ["#1"])

    def test_ticket_from_existing_to_tickets_format_has_criteria(self):
        self.write("| #1 | Pierwszy | todo | | Sonnet | | standard | Opus | |\n",
                   {"1": "# #1: Pierwszy\n\n**Co zbudować:** Opis.\n\n"
                         "**Blokery:** brak\n\n- [ ] Gotowe.\n\n## Dla recenzenta\n"})
        self.assertEqual(self.check().errors, [])

    def test_invalid_proposal_lists_all_errors(self):
        self.write("| #1 | Pierwszy | todo | | Nieznany | | standard | Opus | #2 |\n"
                   "| #2 | Drugi | todo | | Sonnet | | standard | Opus | #1 |\n",
                   {"1": "# Kartka\n", "2": "## Kryteria akceptacji\n- [ ] Działa\n"})
        got = self.check()
        self.assertTrue(any("nieznany wykonawca" in e for e in got.errors))
        self.assertTrue(any("kartka nie ma kryteriów" in e for e in got.errors))
        self.assertTrue(any("cykl" in e for e in got.errors))

    def test_path_outside_project_is_rejected(self):
        self.write("| #1 | X | todo | | Sonnet | | standard | Opus | |\n", {})
        self.assertIn("repozytorium", plans.read(self.path, "K-2", default_catalog(),
                                                allowed_roots=("/nonexistent",)).errors[0])

    def test_second_markdown_table_and_default_strong_reviewer(self):
        self.write("| #1 | X | todo | | Codex mocny | | mocny | | |\n",
                   {"1": "## Kryteria akceptacji\n- [ ] Działa\n"})
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("\n| A | B |\n| --- | --- |\n| 1 | 2 |\n")
        got = self.check()
        self.assertEqual(got.rows[0]["reviewer"], "Codex Astra")
        self.assertTrue(any("jedną tabelę" in e for e in got.errors))

    def test_card_identity_local_class_and_prefix_are_checked(self):
        self.write("| OTHER-1 | X | todo | | Sonnet | | lokalny | Opus | |\n",
                   {"OTHER-1": "## Kryteria akceptacji\n- [ ] Działa\n"})
        with open(self.path, encoding="utf-8") as fh:
            content = fh.read().replace("card: K-2\n", "")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(content)
        joined = " | ".join(self.check().errors)
        for fragment in ("klucz card", "prefiks ID", "klasa lokalny"):
            self.assertIn(fragment, joined)


class SavedPlanStateTest(unittest.TestCase):
    def test_ticket_transitions_update_plan_state_and_time(self):
        from daemon.tide_daemon.cardflow.engine import FlowEngine
        from daemon.tide_daemon.cardflow.state import fold

        events = []

        def add(event_type, **data):
            seq = len(events) + 1
            events.append({"type": event_type, "seq": seq, "ts": f"2026-10-29T10:00:{seq:02d}+01:00", **data})

        add("card.started", entry="plan")
        add("plan.proposed", prefix="PT")
        add("plan.approved", planFile="docs/agents/plans/2026-10-29-k-2-flow.md",
            rows=[{"id": "PT-1", "title": "Pierwszy", "executor": "Sonnet", "reviewer": "Opus",
                   "blockers": [], "class": "standard"}], waves=[["PT-1"]])
        engine = FlowEngine.__new__(FlowEngine)
        engine._plan_card = lambda card, row: "- [ ] Działa.\n"

        def saved():
            return engine._plan_files("K-2", fold(events),
                                      {"PT-1"} if any(e.get("action") == "merge" and e.get("ok")
                                                   for e in events) else set())[
                "docs/agents/plans/2026-10-29-k-2-flow.md"].decode("utf-8")

        self.assertIn("| PT-1 | Pierwszy | todo |", saved())
        add("ticket.assigned", ticket="PT-1")
        self.assertIn("| PT-1 | Pierwszy | w-toku |", saved())
        add("step.started", step="s1", ticket="PT-1", kind="ticket")
        self.assertIn("updated: 2026-10-29T10:00:05+01:00", saved())
        add("step.finished", step="s1", result="done")
        self.assertIn("| PT-1 | Pierwszy | review |", saved())
        add("step.started", step="s2", ticket="PT-1", kind="review", round=1)
        self.assertIn("| PT-1 | Pierwszy | review |", saved())
        add("step.finished", step="s2", result="question")
        self.assertIn("| PT-1 | Pierwszy | czeka |", saved())
        add("result", action="merge", key="PT-1", ok=True)
        self.assertIn("| PT-1 | Pierwszy | gotowe |", saved())
        self.assertIn("status: done", saved())
        self.assertIn("updated: 2026-10-29T10:00:09+01:00", saved())


if __name__ == "__main__":
    unittest.main()
