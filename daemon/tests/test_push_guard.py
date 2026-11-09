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
            git(self.repo, "merge", "-q", "--no-ff", f"dle-{number}", "-m",
                f"Merge ticket DLE-{number} into card K-89")
        code, out, _ = guard(self.repo, "--prefix", "DLE", "--json")
        self.assertEqual(code, 1)
        hits = json.loads(out)["hits"]
        self.assertEqual(len(hits), 6)  # po 2 trafienia na scalenie: ticket i card
        self.assertTrue(all(h["merge"] and h["where"] == "temat" for h in hits))
        self.assertEqual(sorted(h["match"] for h in hits if h["rule"] == "ticket"), ["DLE-1", "DLE-2", "DLE-5"])
        self.assertEqual({h["rule"] for h in hits}, {"ticket", "card"})
        _, text, _ = guard(self.repo, "--prefix", "DLE")
        self.assertIn("DO PRZEREDAGOWANIA (opis commita): 3", text)
        self.assertEqual(text.count("(scalenie)"), 3)

    def test_worktree_of_card_uses_repository_project_config(self):
        self.branch()
        git(self.repo, "checkout", "-q", "master")
        work = os.path.join(self._tmp.name, "LogViewer-worktrees", "k-89-slug")
        git(self.repo, "worktree", "add", "-q", "-b", "k89", work, "master")
        commit(work, "a.cs", "class A {}\n", "add a (DLE-3)")
        code, out, err = guard(work, "--json", branch="k89")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(data["project"], "logviewer")
        self.assertIn("DLE", data["prefixes"])
        self.assertEqual(data["warnings"], [])
        self.assertEqual(err, "")

    def test_unknown_project_warns_and_prints_project_and_prefixes(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "add a")
        other = os.path.join(self._tmp.name, "Inny")
        os.rename(self.repo, other)
        code, out, err = guard(other)
        self.assertEqual(code, 0)
        self.assertIn("OSTRZEŻENIE", err)
        self.assertIn("projekt: inny", out)
        self.assertIn("PT-*", out)

    def test_merge_commit_conflict_resolution_is_scanned(self):
        git(self.repo, "checkout", "-q", "-b", "card")
        commit(self.repo, "base.txt", "card side\n", "card edit")
        git(self.repo, "checkout", "-q", "-b", "side", "master")
        commit(self.repo, "base.txt", "side edit\n", "side edit")
        git(self.repo, "checkout", "-q", "card")
        merged = subprocess.run(["git", "merge", "--no-ff", "-m", "Merge work", "side"], cwd=self.repo,
                                capture_output=True, text=True)
        self.assertNotEqual(merged.returncode, 0)
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("resolved\n// K-89 conflict fixed by Claude\n")
        git(self.repo, "add", "base.txt")
        git(self.repo, "commit", "-q", "--no-edit")
        code, out, _ = guard(self.repo, "--json")
        self.assertEqual(code, 1)
        hits = [h for h in json.loads(out)["hits"] if h["where"] == "diff"]
        self.assertEqual({(h["file"], h["line"], h["rule"], h["merge"]) for h in hits},
                         {("base.txt", 2, "card", True), ("base.txt", 2, "ai", True)})

    def test_plus_plus_line_is_not_taken_for_file_header(self):
        self.branch()
        commit(self.repo, "a.txt", "ok\n++ not a header\nsee K-4\n", "add a")
        code, out, _ = guard(self.repo, "--json")
        self.assertEqual(code, 1)
        self.assertEqual([(h["file"], h["line"]) for h in json.loads(out)["hits"]], [("a.txt", 3)])

    def test_bad_allow_regex_and_missing_explicit_config_exit_two(self):
        self.branch()
        commit(self.repo, "a.cs", "class A {}\n", "add a")
        config = os.path.join(self._tmp.name, "bad.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"projects": {"logviewer": {"allow": [{"rule": "tide", "line": "("}]}}}, fh)
        code, _, err = guard(self.repo, "--config", config)
        self.assertEqual(code, 2)
        self.assertIn("regex", err)
        code, _, err = guard(self.repo, "--config", os.path.join(self._tmp.name, "nie-ma.json"))
        self.assertEqual(code, 2)
        self.assertIn("push_guard", err)

    def test_lowercase_ids_in_branch_names_are_hits(self):
        self.branch()
        git(self.repo, "checkout", "-q", "-b", "ticket/dle-1-x", "card")
        commit(self.repo, "f.cs", "class F {}\n", "add f")
        git(self.repo, "checkout", "-q", "card")
        git(self.repo, "merge", "-q", "--no-ff", "ticket/dle-1-x", "-m", "Merge branch 'ticket/dle-1-x' into card/k-89-y")
        code, out, _ = guard(self.repo, "--json")
        self.assertEqual(code, 1)
        self.assertEqual({h["rule"] for h in json.loads(out)["hits"]}, {"ticket", "card"})

    def test_lowercase_ids_outside_branch_names_are_not_hits(self):
        self.branch()
        commit(self.repo, "h.cs", "// maintain k-1 style, dle-2 lookalike\n", "add h")
        self.assertEqual(guard(self.repo)[0], 0)

    def test_default_plans_dir_of_repository_supplies_prefixes(self):
        self.branch()
        plans = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(plans)
        with open(os.path.join(plans, "p.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\nticket-prefix: ZZZ\n---\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "add plans")
        base = git(self.repo, "rev-parse", "HEAD").strip()
        commit(self.repo, "a.cs", "class A {}\n", "add a (ZZZ-4)")
        self.assertEqual(guard(self.repo, target=base)[0], 1)

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
