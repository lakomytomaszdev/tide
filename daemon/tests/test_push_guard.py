"""tools/push_guard.py: strażnik treści przed push do repozytorium klienta (K-91, PT-322).

Repozytorium-atrapa: master z czystym commitem i gałąź karty z trafieniami w temacie, treści
i diffie oraz scaleniem z numerem; po przeredagowaniu (przepisaniu gałęzi) wynik jest czysty.
"""
import json
import os
import subprocess
import tempfile
import unittest

from .gitrepos import commit, git

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(REPO, "tools", "push_guard.py")


def guard(repo, *extra, branch="card", target="master"):
    done = subprocess.run(["/usr/bin/python3", TOOL, repo, branch, target, *extra],
                          capture_output=True, text=True, encoding="utf-8")
    return done.returncode, done.stdout, done.stderr


class PushGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "LogViewer")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        commit(self.repo, "base.txt", "start\n", "add base file")

    def branch(self):
        git(self.repo, "checkout", "-q", "-b", "card")

    def test_clean_branch_exits_zero(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "add parser for log lines")
        code, out, _ = guard(self.repo)
        self.assertEqual(code, 0)
        self.assertIn("czysto", out)

    def test_hits_in_subject_body_and_diff(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "feat: add parser (K-12)")
        commit(self.repo, "b.cs", "// ok\n// see YouTrack and S-7\nclass B {}\n", "add b")
        commit(self.repo, "c.cs", "class C {}\n",
               "add c\n\nbody line\nCo-Authored-By: Someone <x@y.z>\nGenerated with a tool")
        commit(self.repo, "d.cs", "// refs DLE-9\n// Claude wrote this\n", "add d")
        code, out, _ = guard(self.repo, "--prefix", "DLE", "--json")
        self.assertEqual(code, 1)
        hits = json.loads(out)["hits"]
        found = {(h["where"], h["file"], h["line"], h["rule"]) for h in hits}
        self.assertIn(("temat", None, None, "card"), found)
        self.assertIn(("diff", "b.cs", 2, "youtrack"), found)
        self.assertIn(("diff", "b.cs", 2, "case"), found)
        self.assertIn(("treść", None, 2, "trailer"), found)
        self.assertIn(("treść", None, 3, "generated"), found)
        self.assertIn(("diff", "d.cs", 1, "ticket"), found)
        self.assertIn(("diff", "d.cs", 2, "ai"), found)

    def test_text_report_lists_commits_to_reword(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "feat: add parser (K-12)")
        code, out, _ = guard(self.repo)
        self.assertEqual(code, 1)
        self.assertIn("DO PRZEREDAGOWANIA", out)
        self.assertIn("feat: add parser (K-12)", out)
        self.assertIn("Wysyłka wstrzymana", out)

    def test_merge_commit_titles_with_ticket_numbers_are_the_only_hits(self):
        self.branch()
        for number in (1, 2, 5):
            git(self.repo, "checkout", "-q", "-b", f"dle-{number}", "card")
            commit(self.repo, f"f{number}.cs", "class F {}\n", f"add feature {number}")
            git(self.repo, "checkout", "-q", "card")
            git(self.repo, "merge", "-q", "--no-ff", f"dle-{number}", "-m", f"Merge branch 'dle-{number}': DLE-{number}")
        code, out, _ = guard(self.repo, "--prefix", "DLE", "--json")
        self.assertEqual(code, 1)
        hits = json.loads(out)["hits"]
        self.assertEqual(len(hits), 3)
        self.assertTrue(all(h["merge"] and h["where"] == "temat" and h["rule"] == "ticket" for h in hits))
        self.assertEqual(sorted(h["match"] for h in hits), ["DLE-1", "DLE-2", "DLE-5"])
        _, text, _ = guard(self.repo, "--prefix", "DLE")
        self.assertIn("(scalenie)", text)

    def test_clean_after_rewording(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "add a (DLE-3)")
        self.assertEqual(guard(self.repo, "--prefix", "DLE")[0], 1)
        git(self.repo, "commit", "-q", "--amend", "-m", "add a")
        code, out, _ = guard(self.repo, "--prefix", "DLE")
        self.assertEqual(code, 0)
        self.assertIn("czysto", out)

    def test_prefixes_from_plans_and_project_config(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "add a (ZZZ-4)")
        plans = os.path.join(self._tmp.name, "plans")
        os.makedirs(plans)
        with open(os.path.join(plans, "p.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\nticket-prefix: ZZZ\n---\n")
        self.assertEqual(guard(self.repo)[0], 0)
        self.assertEqual(guard(self.repo, "--plans", plans)[0], 1)

    def test_default_config_knows_dle_prefix_of_logviewer(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "add a (DLE-3)")
        self.assertEqual(guard(self.repo)[0], 1)

    def test_domain_word_exception_per_project(self):
        self.branch()
        os.makedirs(os.path.join(self.repo, "src", "Domain"))
        commit(self.repo, "src/Domain/Tide.cs", "class Tide {}\n", "add tide model")
        commit(self.repo, "notes.txt", "internal tide notes\n", "add notes")
        config = os.path.join(self._tmp.name, "cfg.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"projects": {"logviewer": {"allow": [
                {"rule": "tide", "path": "src/Domain/*"}, {"rule": "tide", "line": "^add tide model$"}]}}}, fh)
        code, out, _ = guard(self.repo, "--config", config, "--json")
        self.assertEqual(code, 1)
        hits = json.loads(out)["hits"]
        self.assertEqual([(h["file"], h["rule"]) for h in hits], [("notes.txt", "tide")])

    def test_no_false_hits_on_lookalikes(self):
        self.branch()
        commit(self.repo, "a.cs", "var h = \"SHA-256\"; // UTF-8, AES-128, said, maintain, ASK-4\n",
               "use hash helper")
        code, out, _ = guard(self.repo)
        self.assertEqual((code, "czysto" in out), (0, True))

    def test_removed_lines_are_not_hits(self):
        git(self.repo, "checkout", "-q", "-b", "card")
        commit(self.repo, "a.cs", "ok\n", "add a")
        git(self.repo, "checkout", "-q", "master")
        commit(self.repo, "old.cs", "// K-5 old\n", "add old")
        git(self.repo, "checkout", "-q", "card")
        git(self.repo, "merge", "-q", "--no-ff", "master", "-m", "Merge master")
        commit(self.repo, "old.cs", "// clean\n", "tidy old")
        code, _, _ = guard(self.repo)
        self.assertEqual(code, 0)

    def test_errors_exit_two(self):
        self.branch()
        code, _, err = guard(self.repo, branch="nie-ma")
        self.assertEqual(code, 2)
        self.assertIn("push_guard", err)


if __name__ == "__main__":
    unittest.main()
