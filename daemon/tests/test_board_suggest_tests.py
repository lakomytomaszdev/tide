"""Podpowiedź i źródło kontraktu testów (K-69, PT-282): `suggest_tests`, walidacja `repo`, `suggest_project_tests`.

Jednostkowo, bez D-Bus (transport: test_board_dbus.py, `SuggestProjectTests`). Fixture'y repozytoriów są te same co
w testach decyzji `effective_tests` (#2), więc zgodność wykrycia sprawdza porównanie obu funkcji na tych drzewach.
"""
import os
import subprocess
import tempfile
import unittest

from tide_daemon.board.model import BoardError
from tide_daemon.board.store import NO_TESTS_SUGGESTION, git_repo_path
from tide_daemon.cardflow.testpolicy import (CHANGED_TARGET, FULL_TARGET, effective_tests, make_targets,
                                             repo_facts, suggest_tests)

from .test_board_contract_approval import ApprovalCase
from .test_cardflow_effective_tests import CHANGED, LEGACY, NO_MAKEFILE, NO_TARGETS, ONLY_TEST, tree

CONTRACT = {"changed": "make test-changed BASE={base}", "full": "make test"}
ONLY_CHANGED = {"Makefile": "test-changed:\n\t@true\n"}


def suggestion(case, files):
    return suggest_tests(repo_facts(tree(case, files)))


class SuggestTestsTest(unittest.TestCase):
    def test_both_targets(self):
        result = suggestion(self, CHANGED)
        self.assertEqual((result["changed"], result["full"], result["source"]),
                         ("make test-changed BASE={base}", "make test", "make"))
        self.assertEqual(result["facts"], {"makeTargets": ["test", "test-changed"], "legacy": False})

    def test_only_test_gives_full_without_changed(self):
        result = suggestion(self, ONLY_TEST)
        self.assertEqual((result["changed"], result["full"], result["source"]), ("", "make test", "make"))
        self.assertTrue(result["facts"]["legacy"])

    def test_only_test_changed_gives_changed_without_full(self):
        result = suggestion(self, ONLY_CHANGED)
        self.assertEqual((result["changed"], result["full"], result["source"]),
                         ("make test-changed BASE={base}", "", "make"))
        self.assertFalse(result["facts"]["legacy"])

    def test_no_targets_or_no_makefile_is_empty(self):
        for files in (NO_TARGETS, NO_MAKEFILE, {}):
            with self.subTest(files=sorted(files)):
                result = suggestion(self, files)
                self.assertEqual((result["changed"], result["full"], result["source"]), ("", "", ""))
                self.assertEqual(result["facts"], {"makeTargets": [], "legacy": False})

    def test_empty_facts_are_empty(self):
        self.assertEqual(suggest_tests({})["source"], "")
        self.assertEqual(suggest_tests(None)["changed"], "")

    def test_agrees_with_effective_tests_on_the_same_fixtures(self):
        for name, files in (("changed", CHANGED), ("legacy", LEGACY), ("only-test", ONLY_TEST),
                            ("only-changed", ONLY_CHANGED), ("no-targets", NO_TARGETS), ("no-makefile", NO_MAKEFILE)):
            with self.subTest(case=name):
                root = tree(self, files)
                found = suggest_tests(repo_facts(root))
                policy = effective_tests(None, root, False, "")
                targets = set(make_targets(root))
                self.assertEqual(set(found["facts"]["makeTargets"]), targets & {CHANGED_TARGET, FULL_TARGET})
                self.assertEqual(found["facts"]["legacy"], policy.legacy)
                self.assertEqual(found["full"], policy.full)
                self.assertEqual(found["changed"] or (found["full"] if policy.legacy else ""), policy.changed)

    def test_facts_come_from_make_targets_only(self):
        # Odczyt celów nie wychodzi poza repozytorium: dowiązanie pliku mk/x.mk i całego katalogu mk są pomijane.
        outside = tree(self, {"evil.mk": "test-changed:\n", "mk/x.mk": "test-changed:\n"})
        file_link = tree(self, {"Makefile": "test:\n"})
        os.mkdir(os.path.join(file_link, "mk"))
        os.symlink(os.path.join(outside, "evil.mk"), os.path.join(file_link, "mk", "x.mk"))
        dir_link = tree(self, {"Makefile": "test:\n"})
        os.symlink(os.path.join(outside, "mk"), os.path.join(dir_link, "mk"))
        for root in (file_link, dir_link):
            with self.subTest(root=os.path.basename(root)):
                result = suggest_tests(repo_facts(root))
                self.assertEqual((result["changed"], result["full"]), ("", "make test"))

    def test_missing_directory_is_empty(self):
        self.assertEqual(suggest_tests(repo_facts(""))["source"], "")
        self.assertEqual(suggest_tests(repo_facts("/nonexistent/tide-tests/repo"))["source"], "")


def git_init(path, files=None):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "-C", path, "init", "-q"], check=True)
    for rel, text in (files or {}).items():
        target = os.path.join(path, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
    return path


class RepoValidationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_valid_git_directory(self):
        repo = git_init(os.path.join(self.tmp, "repo"))
        self.assertEqual(git_repo_path(repo), repo)

    def test_worktree_with_git_file(self):
        repo = git_init(os.path.join(self.tmp, "main"))
        subprocess.run(["git", "-C", repo, "-c", "user.name=T", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        worktree = os.path.join(self.tmp, "wt")
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", worktree, "-b", "wt"], check=True)
        self.assertTrue(os.path.isfile(os.path.join(worktree, ".git")))
        self.assertEqual(git_repo_path(worktree), worktree)

    def test_rejections_are_readable(self):
        plain = os.path.join(self.tmp, "plain")
        os.mkdir(plain)
        for value, fragment in (("repo/wzgledna", "bezwzględną"), (os.path.join(self.tmp, "brak"), "nie ma katalogu"),
                                (plain, "nie jest repozytorium git"), (["x"], "napisem")):
            with self.subTest(value=value):
                with self.assertRaises(BoardError) as caught:
                    git_repo_path(value)
                self.assertIn(fragment, str(caught.exception))


class SuggestProjectTestsTest(ApprovalCase):
    """Usługa: wejście `repo` i `slug`, źródło z decyzji `effective_tests`, brak zapisu."""

    def setUp(self):
        super().setUp()
        self.ws_repo = git_init(os.path.join(self.ws, "app"), CHANGED)

    def suggest(self, **request):
        return self.store.suggest_project_tests(request)

    def assert_error(self, fragment, **request):
        with self.assertRaises(BoardError) as caught:
            self.suggest(**request)
        self.assertIn(fragment, str(caught.exception))
        return caught.exception

    def test_repo_input_suggests_without_project_fields(self):
        result = self.suggest(repo=self.ws_repo)
        self.assertEqual((result["ok"], result["changed"], result["full"], result["source"], result["message"]),
                         (True, "make test-changed BASE={base}", "make test", "make", ""))
        for key in ("effectiveSource", "unapproved", "testsApproved"):
            self.assertNotIn(key, result)

    def test_repo_validation_errors(self):
        plain = os.path.join(self.tmp, "plain")
        os.mkdir(plain)
        self.assert_error("bezwzględną", repo="app")
        self.assert_error("nie ma katalogu", repo=os.path.join(self.tmp, "brak"))
        self.assert_error("nie jest repozytorium git", repo=plain)
        self.assert_error("podaj slug", )
        self.assert_error("nieznane pole", repo=self.ws_repo, extra="x")

    def test_unknown_slug(self):
        self.assertEqual(self.assert_error("nie ma projektu", slug="brak").code, "not-found")

    def test_repo_without_targets_is_an_empty_result_without_error(self):
        repo = git_init(os.path.join(self.ws, "pusty"), NO_TARGETS)
        result = self.suggest(repo=repo)
        self.assertEqual((result["changed"], result["full"], result["source"], result["message"]),
                         ("", "", "", NO_TESTS_SUGGESTION))

    def test_project_without_contract_reports_detected_source(self):
        self.store.upsert_project({"slug": "app", "name": "App", "repo": self.ws_repo})
        result = self.suggest(slug="app")
        self.assertEqual((result["effectiveSource"], result["unapproved"], result["testsApproved"]),
                         ("detected", False, False))
        self.assertEqual((result["changed"], result["full"]), ("make test-changed BASE={base}", "make test"))

    def test_project_without_contract_and_targets_reports_rules(self):
        repo = git_init(os.path.join(self.ws, "bez"), NO_MAKEFILE)
        self.store.upsert_project({"slug": "bez", "name": "Bez", "repo": repo})
        result = self.suggest(slug="bez")
        self.assertEqual((result["effectiveSource"], result["unapproved"], result["source"]), ("rules", False, ""))
        self.assertEqual(result["message"], NO_TESTS_SUGGESTION)

    def test_approved_contract_is_profile_source(self):
        self.store.upsert_project({"slug": "app", "name": "App", "repo": self.ws_repo})
        self.assertTrue(self.approve(CONTRACT)["testsApproved"])
        result = self.suggest(slug="app")
        self.assertEqual((result["effectiveSource"], result["unapproved"], result["testsApproved"]),
                         ("profile", False, True))

    def test_unapproved_contract_warns_and_falls_back_to_detection(self):
        self.store.upsert_project({"slug": "app", "name": "App", "repo": self.ws_repo, "tests": CONTRACT})
        result = self.suggest(slug="app")
        self.assertEqual((result["effectiveSource"], result["unapproved"], result["testsApproved"]),
                         ("detected", True, False))

    def test_unapproved_contract_without_targets_falls_back_to_rules(self):
        repo = git_init(os.path.join(self.ws, "bez"), NO_MAKEFILE)
        self.store.upsert_project({"slug": "bez", "name": "Bez", "repo": repo, "tests": CONTRACT})
        result = self.suggest(slug="bez")
        self.assertEqual((result["effectiveSource"], result["unapproved"]), ("rules", True))

    def test_repo_missing_project_is_empty_without_error(self):
        self.store.upsert_project({"slug": "app", "name": "App", "repo": self.ws_repo})
        os.rename(self.ws_repo, self.ws_repo + ".gone")
        self.assertTrue(self.store.snapshot()["projects"][0]["repoMissing"])
        result = self.suggest(slug="app")
        self.assertEqual((result["ok"], result["changed"], result["full"], result["source"], result["message"]),
                         (True, "", "", "", NO_TESTS_SUGGESTION))
        self.assertEqual(result["effectiveSource"], "rules")

    def test_suggestion_never_writes_or_approves(self):
        self.store.upsert_project({"slug": "app", "name": "App", "repo": self.ws_repo})
        head = self.head()
        self.suggest(slug="app")
        self.suggest(repo=self.ws_repo)
        self.assertEqual(self.head(), head)
        self.assertFalse(os.path.exists(self.box()))
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(self.project()["testsApproved"])
        self.assertEqual(self.project()["tests"], {"changed": "", "full": ""})


if __name__ == "__main__":
    unittest.main()
