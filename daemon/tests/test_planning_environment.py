"""PT-190: sekcja „Środowisko projektu” w prompcie planowania, out/box.yaml w propozycji i migracja cardflow.json."""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from daemon.tide_daemon.box import migration
from daemon.tide_daemon.box import profile as bp
from daemon.tide_daemon.cardflow import environment, planning, plans
from daemon.tide_daemon.models.executors import default_catalog

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stack")
CARD = SimpleNamespace(id="K-9", title="Tytuł", description="Opis")
PROJECT = SimpleNamespace(name="Projekt")
REPO_DOC = "tide-box: 1\nstacks: [node]\nverify:\n  - npm run lint\nnetwork:\n  domains: [registry.npmjs.org]\n"


class BoardCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.board = os.path.join(self.tmp, "tide-board")
        os.makedirs(self.board)
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": self.board})
        patcher.start()
        self.addCleanup(patcher.stop)

    def worktree(self, fixture=None):
        path = os.path.join(self.tmp, "wt")
        if fixture:
            shutil.copytree(os.path.join(FIXTURES, fixture), path)
        else:
            os.makedirs(path)
            with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("# Projekt bez wzorca\n")
        return path

    def prompt(self, text, full=False):
        args = dict(card=CARD, project=PROJECT, catalog=default_catalog(), preferred="mixed",
                    assignment_rules="reguły", prefix="PT", out_dir="/out", environment=text)
        return planning.full_prompt(tool="claude", **args) if full else planning.prompt(**args)


class SectionTest(BoardCase):
    def test_detected_section_has_facts_and_template_proposal_in_both_prompts(self):
        text = environment.section(self.worktree("toolssuite"), "diag", "/out")
        for prompt in (self.prompt(text), self.prompt(text, full=True)):
            self.assertIn("## Środowisko projektu (wykryte)", prompt)
            self.assertIn("stacks:", prompt)
            self.assertIn("angular: \"19\"", prompt)
            self.assertIn("z szablonów Tide", prompt)
            self.assertIn("tide-box: 1", prompt)
            self.assertIn("registry.npmjs.org", prompt)
            self.assertIn("/out/box.yaml", prompt)
            self.assertNotIn("Czego potrzebuje środowisko", prompt)

    def test_section_is_added_without_changing_the_rest_of_the_prompt(self):
        text = environment.section(self.worktree("toolssuite"), "diag", "/out")
        for full in (False, True):
            base = self.prompt("", full)
            self.assertEqual(self.prompt(text, full).replace(text + "\n\n", "", 1), base)
            self.assertNotIn("Środowisko projektu", base)

    def test_red_template_items_are_listed_disabled_and_prompt_forbids_enabling_them(self):
        text = environment.section(self.worktree("infracore"), "diag4", "/out")
        self.assertIn("[wyłączona] domain 10.20.10.20 (ryzyko: red)", text)
        self.assertIn("Nie włączaj pozycji czerwonych", text)
        self.assertIn("nie pytaj o nie w trakcie kroków", text)
        self.assertNotIn("network.full: true", text)

    def test_repo_box_yaml_is_the_source_and_is_never_written_or_copied(self):
        wt = self.worktree("toolssuite")
        os.makedirs(os.path.join(wt, ".tide"))
        path = os.path.join(wt, ".tide", "box.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(REPO_DOC)
        before = os.stat(path).st_mtime_ns
        text = environment.section(wt, "diag", "/out")
        self.assertIn("z repozytorium (.tide/box.yaml)", text)
        self.assertIn("npm run lint", text)
        self.assertNotIn("z szablonów Tide", text)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), REPO_DOC)
        self.assertEqual(os.stat(path).st_mtime_ns, before)
        self.assertEqual(os.listdir(self.board), [])
        self.assertIsNone(bp.load_project("diag"))

    def test_broken_repo_box_yaml_falls_back_to_templates_with_a_note(self):
        wt = self.worktree("toolssuite")
        os.makedirs(os.path.join(wt, ".tide"))
        with open(os.path.join(wt, ".tide", "box.yaml"), "w", encoding="utf-8") as fh:
            fh.write("tide-box: 1\nnie-ma-takiego: 1\n")
        text = environment.section(wt, "diag", "/out")
        self.assertIn("niepoprawny", text)
        self.assertIn("z szablonów Tide", text)

    def test_project_without_pattern_gets_empty_proposal_and_one_question_instruction(self):
        text = environment.section(self.worktree(), "pusty", "/out")
        self.assertIn("stacks: []", text)
        self.assertIn("Projekt bez rozpoznanego wzorca", text)
        self.assertIn("dokładnie jedno pytanie „Czego potrzebuje środowisko?”", text)
        self.assertIn("nie pytaj o środowisko w żadnym innym miejscu", text)
        for prompt in (self.prompt(text), self.prompt(text, full=True)):
            self.assertIn("Czego potrzebuje środowisko?", prompt)

    def test_approved_profile_with_same_stacks_shows_only_approved_section_with_hash(self):
        wt = self.worktree("toolssuite")
        profile = {"tide-box": 1, "project": "diag", "stacks": ["node", "playwright"]}
        bp.save_project("diag", profile)
        text = environment.section(wt, "diag", "/out")
        self.assertIn("## Środowisko projektu (zatwierdzone)", text)
        self.assertIn(environment.recipe_hash(bp.load_project("diag")), text)
        self.assertNotIn("Środowisko projektu (wykryte)", text)

    def test_approved_profile_with_changed_detection_shows_both_sections(self):
        wt = self.worktree("toolssuite")
        bp.save_project("diag", {"tide-box": 1, "project": "diag", "stacks": ["node"]})
        text = environment.section(wt, "diag", "/out")
        self.assertIn("(zatwierdzone)", text)
        self.assertIn("(wykryte)", text)
        self.assertIn("różnią się", text)

    def test_missing_worktree_gives_no_section(self):
        self.assertEqual(environment.section("", "x", "/out"), "")


class PlanBoxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        os.mkdir(os.path.join(self.dir, "tickets"))
        with open(os.path.join(self.dir, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ncard: K-2\ntracker: local\nticket-prefix: PT\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Pierwszy | todo | | Sonnet | | standard | Opus | |\n")
        with open(os.path.join(self.dir, "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("# Kartka\n- [ ] Działa.\n")

    def box(self, text):
        with open(os.path.join(self.dir, "box.yaml"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self):
        return plans.read(os.path.join(self.dir, "plan.md"), "K-2", default_catalog(), allowed_roots=(self.dir,))

    def test_missing_box_yaml_is_not_an_error(self):
        got = self.read()
        self.assertEqual(got.errors, [])
        self.assertIsNone(got.box)

    def test_valid_box_yaml_is_accepted_and_returned(self):
        self.box("tide-box: 1\napt: [xvfb]\nverify:\n  - npm run lint\nnetwork:\n  domains: [registry.npmjs.org]\n")
        got = self.read()
        self.assertEqual(got.errors, [])
        self.assertEqual(got.box["apt"], ["xvfb"])

    def test_unknown_key_is_a_format_error(self):
        self.box("tide-box: 1\nnie-ma-takiego: 1\n")
        got = self.read()
        self.assertTrue(any(e.startswith("box.yaml: nie-ma-takiego: nieznany klucz") for e in got.errors), got.errors)

    def test_invalid_yaml_and_version_are_format_errors(self):
        self.box("tide-box: 7\n")
        self.assertTrue(any("box.yaml: tide-box" in e for e in self.read().errors))

    def test_enabled_red_item_without_accepted_red_is_a_format_error(self):
        self.box("tide-box: 1\nnetwork:\n  domains: [github.com, registry.npmjs.org]\n")
        errors = self.read().errors
        self.assertTrue(any("github.com" in e and "bez jawnego zaznaczenia" in e for e in errors), errors)
        self.assertFalse(any("registry.npmjs.org" in e for e in errors))

    def test_internal_address_and_pack_without_accept_are_errors(self):
        self.box("tide-box: 1\npacks: [dane]\nnetwork:\n  domains: [10.20.1.5]\n")
        errors = self.read().errors
        self.assertTrue(any("10.20.1.5" in e for e in errors), errors)
        self.assertTrue(any("pack:dane" in e for e in errors), errors)

    def test_red_item_with_explicit_accepted_red_passes(self):
        self.box("tide-box: 1\nnetwork:\n  domains: [github.com]\naccepted_red: [github.com]\n")
        self.assertEqual(self.read().errors, [])

    def test_network_full_needs_consent(self):
        self.box("tide-box: 1\nnetwork:\n  full: true\n")
        self.assertTrue(any("network.full" in e for e in self.read().errors))

    def test_box_yaml_symlink_outside_is_rejected(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = os.path.join(outside.name, "box.yaml")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("tide-box: 1\n")
        os.symlink(target, os.path.join(self.dir, "box.yaml"))
        self.assertTrue(any("box.yaml" in e for e in self.read().errors))

    def test_box_yaml_next_to_other_plan_names_is_ignored(self):
        os.rename(os.path.join(self.dir, "plan.md"), os.path.join(self.dir, "inny-plan.md"))
        self.box("nieistotne: [")
        got = plans.read(os.path.join(self.dir, "inny-plan.md"), "K-2", default_catalog(), allowed_roots=(self.dir,))
        self.assertEqual(got.errors, [])
        self.assertIsNone(got.box)


class MigrationTest(BoardCase):
    SETTINGS = {"networkDomains": {"proj": ["registry.npmjs.org", "10.20.1.5", "*.com"]},
                "outsideCommands": {"proj": ["npm ci", "npm test *", "make lint *"], "inny": ["dotnet test *"]},
                "stepTimeoutMinutes": 5}

    def test_proposal_maps_domains_and_commands_and_is_not_an_approved_profile(self):
        created = migration.migrate(migration.settings_slugs(self.SETTINGS), self.SETTINGS)
        path = os.path.join(self.board, "projects", "proj", "box.proposed.yaml")
        self.assertIn(path, created)
        self.assertFalse(os.path.exists(os.path.join(self.board, "projects", "proj", "box.yaml")))
        self.assertIsNone(bp.load_project("proj"))
        with open(path, encoding="utf-8") as fh:
            proposal = bp.parse(fh.read(), "project")
        self.assertEqual(proposal["network"]["domains"], ["registry.npmjs.org"])
        self.assertEqual(proposal["setup"], ["npm ci"])
        self.assertEqual(proposal["verify"], ["npm test", "make lint"])
        self.assertIn("do zatwierdzenia", proposal["notes"])
        self.assertIn("10.20.1.5", proposal["notes"])
        self.assertIn("*.com", proposal["notes"])
        self.assertTrue(os.path.exists(os.path.join(self.board, "projects", "inny", "box.proposed.yaml")))

    def test_created_once_and_not_when_profile_exists(self):
        migration.migrate(["proj"], self.SETTINGS)
        path = os.path.join(self.board, "projects", "proj", "box.proposed.yaml")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("# ręczna zmiana\n")
        self.assertEqual(migration.migrate(["proj"], self.SETTINGS), [])
        with open(path, encoding="utf-8") as fh:
            self.assertIn("ręczna zmiana", fh.read())
        bp.save_project("inny", {"tide-box": 1, "project": "inny"})
        self.assertEqual(migration.migrate(["inny"], self.SETTINGS), [])
        self.assertFalse(os.path.exists(os.path.join(self.board, "projects", "inny", "box.proposed.yaml")))

    def test_engine_migration_reads_cardflow_json_and_keeps_old_keys(self):
        import json
        from daemon.tide_daemon.cardflow.engine import FlowEngine
        settings = os.path.join(self.tmp, "cardflow.json")
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump(self.SETTINGS, fh)
        engine = FlowEngine.__new__(FlowEngine)
        engine.settings = settings
        created = engine.migrate_environment()
        self.assertEqual(len(created), 2)
        with open(settings, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), self.SETTINGS)

    def test_nothing_to_migrate_creates_nothing_and_bad_board_does_not_raise(self):
        self.assertEqual(migration.migrate(["proj"], {}), [])
        with mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": "/proc/nie-ma/tablicy"}):
            self.assertEqual(migration.migrate(["proj"], self.SETTINGS), [])


if __name__ == "__main__":
    unittest.main()
