"""Kontrakt testów w profilu projektu (K-69, PT-278): zapis i odczyt `tests` przez BoardStore."""
import os
import unittest

from tide_daemon.board import project_tests
from tide_daemon.board.model import BoardError

from .test_board_core import BoardCase, git_out

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "box-profile", "box.yaml")
CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class ProjectTestsCase(BoardCase):
    def box(self, slug="app"):
        return os.path.join(self.root, "projects", slug, "box.yaml")

    def project(self, slug="app"):
        return {p["slug"]: p for p in self.store.snapshot()["projects"]}[slug]

    def head(self):
        return git_out(self.root, "rev-parse", "HEAD").strip()

    def tree(self):
        return git_out(self.root, "status", "--porcelain", "--untracked-files=all")


class SaveAndReadTest(ProjectTestsCase):
    def test_upsert_writes_profile_with_schema_and_snapshot_returns_same_values(self):
        result = self.store.upsert_project({"slug": "app", "name": "App", "tests": CONTRACT})
        self.assertEqual((result["ok"], result["changed"], result["commitError"]), (True, True, ""))
        self.assertEqual(read(self.box()), 'tide-box: 1\ntests:\n  changed: "make test-changed BASE={base}"\n'
                                           '  full: "make test"\n')
        project = self.project()
        self.assertEqual((project["tests"], project["testsError"]), (CONTRACT, ""))
        self.assertEqual(project["testsGrantNote"], {"changed": "", "full": ""})
        self.assertEqual(self.log()[-1], "project app: create")
        self.assertEqual(git_out(self.root, "show", "--name-only", "--format=", "HEAD").split(),
                         ["projects/app.md", "projects/app/box.yaml"], "jeden commit z plikiem projektu")
        self.assertEqual(self.tree(), "")

    def test_result_equals_reference_profile_and_load_reads_it(self):
        reference = read(FIXTURE)
        tests, error = project_tests.load_tests(reference)
        self.assertEqual((tests, error), (CONTRACT, ""))
        self.assertEqual(project_tests.save_tests(reference, tests), reference)
        self.assertEqual(project_tests.save_tests(reference.replace("make test\"", "make lint\""), tests), reference)
        stripped = reference[:reference.index("tests:")]
        self.assertEqual(project_tests.save_tests(stripped, tests), reference)
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.write("projects/app/box.yaml", stripped)
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.assertEqual(read(self.box()), reference)

    def test_partial_update_keeps_other_field_and_empty_string_clears(self):
        self.store.upsert_project({"slug": "app", "name": "App", "tests": CONTRACT})
        self.store.upsert_project({"slug": "app", "tests": {"full": "make lint"}})
        self.assertEqual(self.project()["tests"], {"changed": CONTRACT["changed"], "full": "make lint"})
        self.store.upsert_project({"slug": "app", "tests": {"changed": ""}})
        self.assertEqual(self.project()["tests"], {"changed": "", "full": "make lint"})
        self.assertEqual(read(self.box()), 'tide-box: 1\ntests:\n  full: "make lint"\n')

    def test_clearing_both_removes_key_and_missing_field_leaves_file(self):
        self.store.upsert_project({"slug": "app", "name": "App", "tests": CONTRACT})
        before = read(self.box())
        self.store.upsert_project({"slug": "app", "description": "inny opis"})
        self.assertEqual(read(self.box()), before)
        head = self.head()
        self.store.upsert_project({"slug": "app", "tests": {"changed": "", "full": ""}})
        self.assertEqual(read(self.box()), "tide-box: 1\n")
        self.assertEqual(self.project()["tests"], {"changed": "", "full": ""})
        self.assertNotEqual(self.head(), head)
        again = self.head()
        self.assertFalse(self.store.upsert_project({"slug": "app", "tests": {"changed": "", "full": ""}})["changed"])
        self.assertEqual(self.head(), again)

    def test_no_tests_field_does_not_create_profile(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.store.upsert_project({"slug": "app", "tests": {"changed": "", "full": ""}})
        self.assertFalse(os.path.exists(self.box()))
        self.assertEqual(self.project()["tests"], {"changed": "", "full": ""})

    def test_preserves_rest_of_file_byte_for_byte(self):
        original = ("# profil\ntide-box: 1\napt:\n  - \"git\"   # narzędzia\nnetwork:\n  tests: \"nie kontrakt\"\n"
                    "  domains:\n    - \"a.example\"\n\n# przed testami\ntests:\n  # stary\n  changed: \"make c BASE={base}\"\n"
                    "\n  full: 'make x'\n\n# po testach\nverify: \"true\"   # koniec\n")
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.write("projects/app/box.yaml", original)
        self.assertEqual(self.project()["tests"], {"changed": "make c BASE={base}", "full": "make x"})
        self.store.upsert_project({"slug": "app", "tests": {"changed": "make test-changed BASE={base}", "full": "make test"}})
        expected = original.replace('  # stary\n  changed: "make c BASE={base}"\n\n  full: \'make x\'\n',
                                    '  changed: "make test-changed BASE={base}"\n  full: "make test"\n')
        self.assertEqual(read(self.box()), expected)
        self.store.upsert_project({"slug": "app", "tests": {"changed": "", "full": ""}})
        self.assertEqual(read(self.box()), original[:original.index("tests:\n  # stary")] +
                         original[original.index("\n# po testach"):])

    def test_appends_block_when_missing_without_touching_other_keys(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.write("projects/app/box.yaml", "tide-box: 1\napt: [git]\nnetwork:\n  tests: x\nverify: \"true\"")
        self.store.upsert_project({"slug": "app", "tests": {"full": "make test"}})
        self.assertEqual(read(self.box()), "tide-box: 1\napt: [git]\nnetwork:\n  tests: x\nverify: \"true\"\n"
                                           "tests:\n  full: \"make test\"\n")
        self.assertEqual(self.project()["tests"]["full"], "make test")

    def test_project_file_is_untouched_by_tests_write(self):
        repo = self.repo("app")
        self.store.upsert_project({"slug": "app", "name": "App", "repo": repo, "allowedCommands": ["make test"],
                                   "deniedCommands": ["make install"], "aiDocs": "tide", "description": "Opis",
                                   "tags": ["a"]})
        md = os.path.join(self.root, "projects", "app.md")
        before_bytes, before = read_bytes(md), self.project()
        self.store.upsert_project({"slug": "app", "tests": CONTRACT})
        self.assertEqual(read_bytes(md), before_bytes)
        after = self.project()
        for key in before:
            if key not in ("tests", "testsError", "testsGrantNote"):
                self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.log()[-1], "project app: update")
        self.assertEqual(git_out(self.root, "show", "--name-only", "--format=", "HEAD").split(),
                         ["projects/app/box.yaml"])

    def test_same_commit_for_project_change_and_tests(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        count = len(self.log())
        self.store.upsert_project({"slug": "app", "description": "nowy", "tests": CONTRACT})
        self.assertEqual(len(self.log()), count + 1)
        self.assertEqual(sorted(git_out(self.root, "show", "--name-only", "--format=", "HEAD").split()),
                         ["projects/app.md", "projects/app/box.yaml"])
        self.assertEqual(self.tree(), "")


class ValidationTest(ProjectTestsCase):
    def test_rejected_contracts_change_nothing(self):
        self.store.upsert_project({"slug": "app", "name": "App", "tests": CONTRACT})
        before, head, tree = read(self.box()), self.head(), self.tree()
        bad = [
            {"changed": "make test"},
            {"changed": "make test BASE={other}"},
            {"changed": "make test BASE={base} X={y}"},
            {"changed": "make test\nmake lint {base}"},
            {"changed": "make test {base}\0"},
            {"changed": "make " + "x" * 300 + " {base}"},
            {"full": "make test {base}"},
            {"full": "make test {x}"},
            {"full": "a\nb"},
            {"full": 5},
            {"changed": None},
            {"nieznane": "x"},
            "make test",
        ]
        for tests in bad:
            with self.subTest(tests=tests):
                with self.assertRaises(BoardError) as ctx:
                    self.store.upsert_project({"slug": "app", "description": "zmiana", "tests": tests})
                self.assertTrue(str(ctx.exception).startswith("tests") or "tests" in str(ctx.exception))
        self.assertEqual((read(self.box()), self.head(), self.tree()), (before, head, ""))

    def test_messages_are_readable(self):
        for tests, text in (({"changed": "make test"}, "{base}"), ({"changed": "a {base} {x}"}, "tylko parametr"),
                            ({"changed": "a\nb {base}"}, "jedną linią"), ({"changed": "{base}" + "x" * 300}, "300"),
                            ({"full": "make {base}"}, "tests.full")):
            with self.assertRaises(BoardError) as ctx:
                self.store.upsert_project({"slug": "app", "name": "App", "tests": tests})
            self.assertIn(text, str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "projects")))

    def test_limit_boundary_is_accepted(self):
        command = "x" * (300 - len(" {base}")) + " {base}"
        self.assertEqual(len(command), 300)
        self.store.upsert_project({"slug": "app", "name": "App", "tests": {"changed": command}})
        self.assertEqual(self.project()["tests"]["changed"], command)

    def test_star_and_compound_commands_are_valid_with_grant_note(self):
        note = project_tests.grant_note("pytest *")
        self.assertEqual(note, "polecenie zawiera `*` (albo nie jest pojedynczym poleceniem) — Tide nie nada mu "
                               "automatycznej zgody; uruchomienie wymaga zgody w kroku")
        self.assertEqual(project_tests.grant_note("make test && make lint"), note)
        self.assertEqual(project_tests.grant_note("make test"), "")
        self.assertEqual(project_tests.grant_note(""), "")
        self.store.upsert_project({"slug": "app", "name": "App",
                                   "tests": {"changed": "pytest * {base}", "full": "pytest *"}})
        project = self.project()
        self.assertEqual(project["tests"], {"changed": "pytest * {base}", "full": "pytest *"})
        self.assertEqual(project["testsGrantNote"], {"changed": note, "full": note})
        self.store.upsert_project({"slug": "app", "tests": {"changed": "make test BASE={base}",
                                                             "full": "make a; make b"}})
        self.assertEqual(self.project()["testsGrantNote"], {"changed": "", "full": note})


class DamagedFileTest(ProjectTestsCase):
    def test_damaged_block_gives_empty_contract_error_and_board_error(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.store.upsert_project({"slug": "other", "name": "Other", "tests": CONTRACT})
        for text in ("tide-box: 1\ntests: make test\n", "tide-box: 1\ntests:\n  - make test\n",
                     "tide-box: 1\ntests:\n  changed: [a]\n", "tide-box: 1\ntests:\n  changed: \"a\n",
                     "tide-box: 1\ntests:\n  changed: \"make test\"\n", "tide-box: 1\ntests:\n  extra: \"x\"\n",
                     "tide-box: 1\ntests:\n  full: \"a\"\n    full: \"b\"\n", "tide-box: 1\ntests:\n"):
            with self.subTest(text=text):
                self.write("projects/app/box.yaml", text)
                snapshot = self.store.snapshot()
                projects = {p["slug"]: p for p in snapshot["projects"]}
                self.assertEqual(projects["app"]["tests"], {"changed": "", "full": ""})
                self.assertTrue(projects["app"]["testsError"].startswith("tests:"), projects["app"]["testsError"])
                self.assertEqual(projects["other"]["tests"], CONTRACT, "lista projektów działa dalej")
                self.assertTrue(any(e["path"].endswith("projects/app/box.yaml") for e in snapshot["errors"]))

    def test_saving_over_damaged_block_repairs_it(self):
        self.store.upsert_project({"slug": "app", "name": "App"})
        self.write("projects/app/box.yaml", "tide-box: 1\ntests: zepsute\nverify: \"true\"\n")
        self.store.upsert_project({"slug": "app", "tests": {"full": "make test"}})
        self.assertEqual(read(self.box()), "tide-box: 1\ntests:\n  full: \"make test\"\nverify: \"true\"\n")
        self.assertEqual(self.project()["testsError"], "")


if __name__ == "__main__":
    unittest.main()
