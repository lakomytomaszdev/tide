"""Sprawdzanie propozycji planu przed rezerwacją numerów."""
import os
import tempfile
import unittest

from daemon.tide_daemon.cardflow import plans
from daemon.tide_daemon.models.executors import default_catalog


class PlanFixture(unittest.TestCase):
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

class ProposalTest(PlanFixture):
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

    def test_wrong_format_version_names_the_value(self):
        # K-35: poprawka planu wpisała `tide-plan: 1.2` (numer standardu) — komunikat mówi, co zmienić.
        self.write("| #1 | X | todo | | Sonnet | | standard | Opus | |\n", {"1": "- [ ] Działa.\n"})
        with open(self.path, encoding="utf-8") as fh:
            text = fh.read().replace("tide-plan: 1\n", "tide-plan: 1.2\n")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.assertEqual(self.check().errors, ["nagłówek ma tide-plan: 1.2, a powinien mieć tide-plan: 1"])

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

    def test_card_identity_local_class_and_work_id_are_checked(self):
        self.write("| OTHER-1 | X | todo | | Sonnet | | lokalny | Opus | |\n",
                   {"OTHER-1": "## Kryteria akceptacji\n- [ ] Działa\n"})
        with open(self.path, encoding="utf-8") as fh:
            content = fh.read().replace("card: K-2\n", "")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(content)
        joined = " | ".join(self.check().errors)
        for fragment in ("klucz card", "numeru roboczego", "klasa lokalny"):
            self.assertIn(fragment, joined)


class TicketFromOtherPlanTest(PlanFixture):
    """K-33 D2: istniejący ticket `todo` z innego planu repozytorium zachowuje numer w propozycji."""

    OTHER = {"PT-42": {"title": "Rozpoznawanie pytań: drugie zdanie modelu lokalnego",
                       "path": "/repo/docs/agents/plans/2026-10-27-board-and-docker.md"}}

    def check_other(self, other=None):
        return plans.read(self.path, "K-2", default_catalog(), allowed_roots=(self.tmp.name,),
                          other_plans=self.OTHER if other is None else other)

    def test_todo_ticket_from_other_plan_keeps_number_and_origin(self):
        self.write("| #1 | Integracja | todo | | Sonnet | | standard | Opus | |\n"
                   "| PT-42 | Rozpoznawanie pytań: drugie  zdanie modelu lokalnego | todo | | Sonnet | | standard | Opus | #1 |\n",
                   {n: "## Kryteria akceptacji\n- [ ] Działa\n" for n in ("1", "PT-42")})
        got = self.check_other()
        self.assertEqual(got.errors, [])
        self.assertEqual(got.waves, [["#1"], ["PT-42"]])
        self.assertEqual(got.rows[1]["fromPlan"], self.OTHER["PT-42"]["path"])
        self.assertNotIn("fromPlan", got.rows[0])

    def test_other_title_is_rejected(self):
        self.write("| PT-42 | Inny tytuł | todo | | Sonnet | | standard | Opus | |\n",
                   {"PT-42": "## Kryteria akceptacji\n- [ ] Działa\n"})
        self.assertTrue(any("ten sam tytuł" in e for e in self.check_other().errors))

    def test_unknown_or_not_todo_ticket_still_needs_work_id(self):
        self.write("| PT-43 | Coś | todo | | Sonnet | | standard | Opus | |\n",
                   {"PT-43": "## Kryteria akceptacji\n- [ ] Działa\n"})
        self.assertTrue(any("numeru roboczego" in e for e in self.check_other().errors))
        self.assertTrue(any("numeru roboczego" in e for e in self.check().errors))

    def test_other_prefix_is_rejected(self):
        other = {"LOG-42": {"title": "X", "path": "/repo/p.md"}}
        self.write("| LOG-42 | X | todo | | Sonnet | | standard | Opus | |\n",
                   {"LOG-42": "## Kryteria akceptacji\n- [ ] Działa\n"})
        self.assertTrue(any("numeru roboczego" in e for e in self.check_other(other).errors))


class OrderProjectsTest(PlanFixture):
    """K-33 #4: propozycja z kolumną Projekt (standard 1.3) sprawdzana względem projektów zlecenia."""

    PROJECTS = {"plasma-tide": {"name": "plasma-tide", "prefix": "PT", "tracker": "local", "trackerProject": "",
                                "configured": True},
                "mobileapp": {"name": "mobileapp", "prefix": "APP5", "tracker": "youtrack", "trackerProject": "APP5",
                            "configured": True}}
    CARD = "## Kryteria akceptacji\n- [ ] Działa\n"

    def write10(self, rows, names=("1", "2", "3")):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ncard: K-2\ntracker: local\nticket-prefix: PT\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery | Projekt |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + rows)
        for name in names:
            with open(os.path.join(self.tmp.name, "tickets", name + ".md"), "w", encoding="utf-8") as fh:
                fh.write(self.CARD)

    def check(self, projects=None):
        return plans.read(self.path, "K-2", default_catalog(), allowed_roots=(self.tmp.name,),
                          projects=self.PROJECTS if projects is None else projects)

    def test_mixed_trackers_with_cross_project_blocker_pass(self):
        self.write10("| #1 | API | todo | | Sonnet | | standard | Opus | | mobileapp |\n"
                     "| #2 | Widok | todo | | Sonnet | | standard | Opus | | |\n"
                     "| #3 | Klient | todo | | Sonnet | | standard | Opus | #1 | plasma-tide |\n")
        got = self.check()
        self.assertEqual(got.errors, [])
        self.assertEqual([r["project"] for r in got.rows], ["mobileapp", "plasma-tide", "plasma-tide"])
        self.assertEqual(got.waves, [["#1", "#2"], ["#3"]])
        self.assertEqual(plans.source_label(self.PROJECTS["plasma-tide"]), "lokalny PT")
        self.assertEqual(plans.source_label(self.PROJECTS["mobileapp"]), "YouTrack APP5")

    def test_project_outside_order_is_rejected_with_row(self):
        self.write10("| #1 | API | todo | | Sonnet | | standard | Opus | | legal |\n", names=("1",))
        errors = self.check().errors
        self.assertTrue(any(e.startswith("#1:") and "spoza projektów zlecenia" in e and "legal" in e
                            for e in errors), errors)

    def test_ready_id_with_prefix_of_other_project_is_rejected(self):
        self.write10("| APP5-7 | API | todo | | Sonnet | | standard | Opus | | plasma-tide |\n", names=("APP5-7",))
        errors = self.check().errors
        self.assertTrue(any(e.startswith("APP5-7:") and "prefiks projektu „mobileapp”" in e for e in errors), errors)

    def test_cycle_through_projects_names_rows_and_projects(self):
        self.write10("| #1 | API | todo | | Sonnet | | standard | Opus | #3 | mobileapp |\n"
                     "| #2 | Niezależny | todo | | Sonnet | | standard | Opus | | |\n"
                     "| #3 | Klient | todo | | Sonnet | | standard | Opus | #1 | plasma-tide |\n")
        errors = self.check().errors
        self.assertIn("#1, #3: plan zawiera cykl blokerów przez projekty mobileapp, plasma-tide", errors)

    def test_more_than_six_projects_is_rejected(self):
        many = {f"p{i}": {"name": f"p{i}", "prefix": f"P{i}", "tracker": "local", "configured": True}
                for i in range(1, 8)}
        rows = "".join(f"| #{i} | T{i} | todo | | Sonnet | | standard | Opus | | p{i} |\n" for i in range(1, 8))
        self.write10(rows, names=[str(i) for i in range(1, 8)])
        errors = self.check(many).errors
        self.assertTrue(any(e.startswith("#7:") and "najwyżej 6 projektów" in e for e in errors), errors)
        self.assertEqual(plans.PLAN_PROJECTS_MAX, 6)

    def test_youtrack_project_without_tracker_on_board_is_rejected(self):
        projects = {**self.PROJECTS, "mobileapp": {**self.PROJECTS["mobileapp"], "configured": False}}
        self.write10("| #1 | API | todo | | Sonnet | | standard | Opus | | mobileapp |\n", names=("1",))
        errors = self.check(projects).errors
        self.assertTrue(any(e.startswith("#1:") and "nie skonfigurowano trackera YouTrack" in e
                            for e in errors), errors)

    def test_single_project_order_rejects_foreign_project_and_keeps_rows(self):
        self.write10("| #1 | API | todo | | Sonnet | | standard | Opus | | |\n", names=("1",))
        single = {"plasma-tide": self.PROJECTS["plasma-tide"]}
        got = self.check(single)
        self.assertEqual(got.errors, [])
        self.assertNotIn("project", got.rows[0], "plan jednoprojektowy zapisuje wiersze jak dotąd")
        self.write10("| #1 | API | todo | | Sonnet | | standard | Opus | | mobileapp |\n", names=("1",))
        self.assertTrue(any("spoza projektów zlecenia" in e for e in self.check(single).errors))


class OtherPlanTodosFilterTest(unittest.TestCase):
    """Filtr silnika: ID ze stanem innym niż `todo` w którymkolwiek planie się nie liczy."""

    def test_id_with_non_todo_state_elsewhere_is_dropped(self):
        from types import SimpleNamespace
        from daemon.tide_daemon.cardflow.engine import FlowEngine as Engine

        def plan(path, card, rows):
            return SimpleNamespace(path=path, card=card,
                                   rows=[SimpleNamespace(ticket=t, title=t, state=st) for t, st in rows])

        found = [plan("a.md", "K-1", [("PT-1", "todo"), ("PT-2", "todo")]),
                 plan("b.md", "K-3", [("PT-2", "done")]),
                 plan("c.md", "K-2", [("PT-9", "todo")])]
        engine = SimpleNamespace(_repo_plans=SimpleNamespace(read=lambda repos: (found, None)))
        self.assertEqual(sorted(Engine._other_plan_todos(engine, "/repo", "K-2")), ["PT-1"])


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
