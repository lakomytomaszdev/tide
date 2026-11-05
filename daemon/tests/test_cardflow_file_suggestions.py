"""PT-197: publiczny wynik i start na prawdziwych, datowanych historiach lokalnego gita."""
import json
import os
import time
from unittest import mock

from .privatebus import wait_until
from .test_cardflow_base import StartBaseSetup
from .test_cardflow_dbus import git


class FileSuggestionsTest(StartBaseSetup):
    def commit(self, files, date, branch=None):
        if branch:
            git(self.repo, "checkout", "-qb", branch)
        for path, content in files.items():
            target = os.path.join(self.repo, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(content)
        git(self.repo, "add", "--all")
        env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        git(self.repo, "commit", "--allow-empty", "-qm", "dated change", env=env)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def suggest(self, card, text=""):
        box = []
        self.board.suggest_card_base(card, json.dumps({"text": text, "revision": 9, "scope": "files"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=8), "analiza nie odpowiedziała")
        result = json.loads(box[0])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["revision"], 9)
        return result

    def test_feature_only_evidence_no_mutation_and_explicit_start_from_sha(self):
        touch = self.commit({"src/żółć file.txt": "feature only"}, "2026-01-01T12:00:00Z", "feature/files")
        tip = self.commit({}, "2026-02-01T12:00:00Z")
        git(self.repo, "checkout", "-q", "master")
        card = self.card(description="Popraw `src/żółć file.txt`.")
        before = (git(self.repo, "show-ref"), git(self.repo, "status", "--porcelain"), self.shown(card))
        result = self.suggest(card)
        self.assertFalse(result["analysis"]["partial"], result)
        self.assertEqual(len(result["candidates"]), 1, result)
        candidate = result["candidates"][0]
        self.assertEqual((candidate["ref"], candidate["commit"], candidate["strength"], candidate["source"]),
                         ("refs/heads/feature/files", tip, "heuristic", "files"))
        self.assertEqual(candidate["evidence"], [{"path": "src/żółć file.txt", "commit": touch,
                                               "timestamp": 1767268800, "time": "2026-01-01T12:00:00+00:00"}])
        self.assertEqual(before, (git(self.repo, "show-ref"), git(self.repo, "status", "--porcelain"), self.shown(card)))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "src", "żółć file.txt")))
        reply = self.start(card, baseRef=candidate["ref"], baseCommit=candidate["commit"])
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(git(reply["worktree"], "rev-parse", "HEAD").strip(), tip)
        with open(os.path.join(reply["worktree"], "src", "żółć file.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "feature only")

    def test_shared_ancestor_and_ties_do_not_reward_unrelated_tip_date(self):
        touch = self.commit({"src/shared.py": "shared"}, "2026-01-01T12:00:00Z", "feature/a")
        git(self.repo, "branch", "feature/b")
        self.commit({}, "2026-03-01T12:00:00Z")
        git(self.repo, "checkout", "-q", "feature/b")
        self.commit({}, "2026-04-01T12:00:00Z")
        git(self.repo, "checkout", "-q", "master")
        result = self.suggest(self.card(description="`src/shared.py`"))
        self.assertEqual({c["ref"] for c in result["candidates"]}, {"refs/heads/feature/a", "refs/heads/feature/b"})
        self.assertEqual([c["rank"] for c in result["candidates"]], [1, 1])
        self.assertTrue(all(c["evidence"][0]["commit"] == touch for c in result["candidates"]))

    def test_deleted_path_is_not_evidence_and_local_remote_refs_remain_equal(self):
        touch = self.commit({"src/gone.py": "gone", "src/present.py": "present"},
                            "2026-01-01T12:00:00Z", "feature/present")
        git(self.repo, "rm", "src/gone.py")
        tip = self.commit({}, "2026-02-01T12:00:00Z")
        git(self.repo, "update-ref", "refs/remotes/origin/feature/present", tip)
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feature/present")
        git(self.repo, "checkout", "-q", "master")
        card = self.card(description="")
        self.assertEqual(self.suggest(card, "src/gone.py")["candidates"], [])
        result = self.suggest(card, "src/present.py")
        self.assertEqual({c["ref"] for c in result["candidates"]},
                         {"refs/heads/feature/present", "refs/remotes/origin/feature/present"})
        self.assertEqual([c["rank"] for c in result["candidates"]], [1, 1])
        self.assertTrue(all(c["evidence"][0]["commit"] == touch for c in result["candidates"]))

    def test_ranking_by_count_then_touch_time_and_unique_basename(self):
        self.commit({"src/one.py": "one", "src/two.py": "two"}, "2026-01-01T12:00:00Z", "feature/two")
        git(self.repo, "checkout", "-q", "master")
        self.commit({"src/one.py": "one"}, "2026-04-01T12:00:00Z", "feature/new")
        git(self.repo, "checkout", "-q", "master")
        self.commit({"src/one.py": "one"}, "2026-02-01T12:00:00Z", "feature/old")
        result = self.suggest(self.card(description="`one.py` `src/two.py`"))
        self.assertEqual([c["name"] for c in result["candidates"]], ["feature/two", "feature/new", "feature/old"])
        self.assertEqual([c["matchedPaths"] for c in result["candidates"]], [2, 1, 1])
        git(self.repo, "checkout", "-q", "master")
        self.commit({"other/one.py": "duplicate"}, "2026-03-01T12:00:00Z", "feature/duplicate")
        result = self.suggest(self.card(description="`one.py`"))
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("Niejednoznaczna" in w for w in result["warnings"]))

    def test_traversal_globs_outside_and_shell_metacharacters_stay_data(self):
        names = ["src/a.py", "src/a;$(touch PWN).py", "--option.py", ":(glob)literal.py"]
        self.commit(dict.fromkeys(names, "data"), "2026-01-01T12:00:00Z", "feature/safe")
        git(self.repo, "checkout", "-q", "master")
        card = self.card(description="")
        bad = self.suggest(card, "`../src/a.py` `/etc/passwd` `src/*.py` `src/a?.py` `src/[a].py` src/a.py?")
        self.assertEqual(bad["candidates"], [])
        self.assertTrue(bad["warnings"])
        result = self.suggest(card, " ".join("`" + n + "`" for n in names))
        self.assertEqual({e["path"] for e in result["candidates"][0]["evidence"]}, set(names))
        absolute = self.suggest(card, "`" + os.path.join(self.repo, "src/a.py") + "`")
        self.assertEqual(absolute["candidates"][0]["evidence"][0]["path"], "src/a.py")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "PWN")))

    def test_explicit_root_path_disambiguates_root_file_from_basename(self):
        self.commit({"config.py": "root"}, "2026-01-01T12:00:00Z", "feature/root")
        git(self.repo, "checkout", "-q", "master")
        self.commit({"other/config.py": "other"}, "2026-02-01T12:00:00Z", "feature/other")
        card = self.card(description="")
        self.assertEqual(self.suggest(card, "config.py")["candidates"], [])
        for path in ("./config.py", os.path.join(self.repo, "config.py")):
            result = self.suggest(card, "`" + path + "`")
            self.assertEqual([c["ref"] for c in result["candidates"]], ["refs/heads/feature/root"])
            self.assertEqual(result["candidates"][0]["evidence"][0]["path"], "config.py")

    def test_path_branch_and_history_limits_report_partial_results(self):
        names = [f"src/file-{i}.py" for i in range(21)]
        self.commit(dict.fromkeys(names, "data"), "2026-01-01T12:00:00Z", "feature/limited")
        card = self.card(description="")
        result = self.suggest(card, " ".join(names))
        self.assertTrue(result["analysis"]["partial"])
        self.assertEqual(result["candidates"][0]["matchedPaths"], 20)
        for i in range(101):
            git(self.repo, "branch", f"limit/{i:03}")
        result = self.suggest(card, names[0])
        self.assertTrue(result["analysis"]["partial"])
        self.assertEqual(result["analysis"]["branches"], 100)
        self.assertEqual(len(result["candidates"]), 99)  # master też zajmuje miejsce, ale nie ma pliku

    def test_old_touch_outside_200_commits_is_not_a_false_evidence(self):
        self.commit({"src/old.py": "old"}, "2026-01-01T12:00:00Z", "feature/deep")
        for i in range(200):
            git(self.repo, "commit", "--allow-empty", "-qm", f"empty {i}")
        result = self.suggest(self.card(description="src/old.py"))
        self.assertTrue(result["analysis"]["partial"])
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("200 commitów" in w for w in result["warnings"]))

    def test_time_budget_returns_partial_and_does_not_block_form_calls(self):
        self.commit({"src/a.py": "a"}, "2026-01-01T12:00:00Z", "feature/timeout")
        card = self.card(description="src/a.py")
        real_run = self.flow.engine.git.run

        def slow(repo, *args, timeout=None):
            if "log" in args:
                time.sleep(min(.1, timeout))
            return real_run(repo, *args, timeout=timeout)

        with mock.patch("tide_daemon.cardflow.filesuggestions.MAX_SECONDS", .03), \
                mock.patch.object(self.flow.engine.git, "run", side_effect=slow):
            start = time.monotonic()
            result = self.suggest(card)
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(result["analysis"]["partial"], result)
        self.assertTrue(any("częściowy" in w for w in result["warnings"]))
        self.assertEqual(self.shown(card)["id"], card)
