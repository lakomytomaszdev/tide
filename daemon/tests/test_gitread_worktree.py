"""`Git.Diff` źródło `worktree` (K-36, PT-360): grupy „W indeksie” i „Poza indeksem”, nieśledzone pliki,
walidacja `path` wspólną funkcją z PT-349 i brak zmian indeksu, refów i dziennika. Prawdziwe tymczasowe repozytoria."""
import json
import os
import shutil
import unittest
from unittest import mock

from tide_daemon.gitread import diffdata
from .gitrepos import commit, git
from .test_gitread_diffdata import CardDiffTestCase, make_repo, refs


def dirty_repo(root):
    """Repozytorium z: `a.txt` zmienionym w indeksie, `b.txt` zmienionym poza indeksem, `both.txt` zmienionym
    w obu miejscach i nieśledzonym `new.txt`."""
    repo = make_repo(root / "proj")
    commit(repo, "both.txt", "1\n2\n3\n")
    with open(repo / "a.txt", "w", encoding="utf-8") as fh:
        fh.write("alfa\nSTAGED\ngamma\n")
    git(repo, "add", "a.txt")
    with open(repo / "b.txt", "w", encoding="utf-8") as fh:
        fh.write("jeden\ndwa\n")
    with open(repo / "both.txt", "w", encoding="utf-8") as fh:
        fh.write("1\nSTAGED\n3\n")
    git(repo, "add", "both.txt")
    with open(repo / "both.txt", "w", encoding="utf-8") as fh:
        fh.write("1\nSTAGED\n3\nUNSTAGED\n")
    with open(repo / "new.txt", "w", encoding="utf-8") as fh:
        fh.write("sekret-nowej-tresci\n")
    return repo


class WorktreeSourceTest(CardDiffTestCase):
    def setUp(self):
        super().setUp()
        self.repo = dirty_repo(self.root)
        self.known = [str(self.repo)]

    def ask_wt(self, path=None, **extra):
        return diffdata.request(self.reader, self.journal,
                                json.dumps({"source": "worktree", "path": str(self.repo) if path is None else path,
                                            **extra}), self.known)

    def test_files_are_split_into_staged_and_unstaged_groups(self):
        reply = self.ask_wt()
        self.assertTrue(reply["ok"], reply)
        by_group = {}
        for f in reply["files"]:
            by_group.setdefault(f["group"], []).append(f["path"])
        self.assertEqual(by_group, {"staged": ["a.txt", "both.txt"], "unstaged": ["b.txt", "both.txt"]})
        self.assertEqual([(g["id"], g["name"], g["totals"]["files"]) for g in reply["groups"]],
                         [("staged", "W indeksie", 2), ("unstaged", "Poza indeksem", 2)])
        self.assertEqual(reply["totals"]["files"], 4)

    def test_groups_match_git_diff_cached_and_git_diff(self):
        from tide_daemon.gitread.diffparse import parse_diff
        reply = self.ask_wt()
        for group, args in (("staged", ("diff", "--cached")), ("unstaged", ("diff",))):
            expected = parse_diff(git(self.repo, *args))["files"]
            got = [{k: v for k, v in f.items() if k != "group"} for f in reply["files"] if f["group"] == group]
            self.assertEqual(got, expected, group)

    def test_untracked_files_are_listed_without_content(self):
        reply = self.ask_wt()
        self.assertEqual(reply["untracked"], ["new.txt"])
        self.assertNotIn("sekret-nowej-tresci", json.dumps(reply))
        self.assertNotIn("new.txt", [f["path"] for f in reply["files"]])

    def test_untracked_files_inside_directories_are_listed_one_by_one(self):
        os.makedirs(self.repo / "dir" / "sub")
        for name in ("dir/x.txt", "dir/sub/y.txt"):
            (self.repo / name).write_text("x\n", encoding="utf-8")
        self.assertEqual(self.ask_wt()["untracked"], ["dir/sub/y.txt", "dir/x.txt", "new.txt"])

    def test_untracked_list_is_capped(self):
        with mock.patch.object(diffdata, "MAX_UNTRACKED", 1):
            (self.repo / "z.txt").write_text("z\n", encoding="utf-8")
            reply = self.ask_wt()
        self.assertEqual(len(reply["untracked"]), 1)
        self.assertTrue(any("nieśledzonych" in w for w in reply["warnings"]), reply["warnings"])

    def test_title_source_and_endpoints(self):
        reply = self.ask_wt()
        self.assertEqual((reply["source"], reply["repo"], reply["title"]),
                         ("worktree", os.path.realpath(self.repo),
                          f"Niezacommitowane zmiany: {os.path.realpath(self.repo)}"))
        self.assertEqual(reply["from"]["ref"], "HEAD")
        self.assertEqual(reply["from"]["sha"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(reply["to"]["ref"], "worktree")

    def test_clean_worktree_has_no_files_and_no_untracked(self):
        clean = make_repo(self.root / "clean")
        reply = self.ask_wt(str(clean))
        self.assertEqual(reply["ok"], False)  # nieznany katalog: spoza listy
        self.known.append(str(clean))
        reply = self.ask_wt(str(clean))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["files"], reply["untracked"], reply["totals"]["files"]), ([], [], 0))

    def test_unborn_head_shows_staged_files_against_nothing(self):
        fresh = self.root / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q", "-b", "master")
        (fresh / "s.txt").write_text("s\n", encoding="utf-8")
        git(fresh, "add", "s.txt")
        self.known.append(str(fresh))
        reply = self.ask_wt(str(fresh))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual([(f["path"], f["group"], f["status"]) for f in reply["files"]], [("s.txt", "staged", "A")])
        self.assertEqual(reply["from"]["sha"], "")

    def test_options_path_and_group_read_one_file(self):
        staged = self.ask_wt(options={"path": "both.txt", "group": "staged"})
        unstaged = self.ask_wt(options={"path": "both.txt", "group": "unstaged"})
        self.assertEqual((staged["group"], unstaged["group"]), ("staged", "unstaged"))
        self.assertEqual((staged["files"][0]["group"], staged["files"][0]["additions"]), ("staged", 1))
        self.assertEqual([l["text"] for h in unstaged["files"][0]["hunks"] for l in h["lines"] if l["kind"] == "+"],
                         ["UNSTAGED"])
        self.assertEqual((staged["path"], staged["totals"]["files"]), ("both.txt", 1))

    def test_options_path_without_group_searches_staged_then_unstaged(self):
        self.assertEqual(self.ask_wt(options={"path": "b.txt"})["group"], "unstaged")
        self.assertEqual(self.ask_wt(options={"path": "a.txt"})["group"], "staged")
        self.assertEqual(self.ask_wt(options={"path": "nie-ma.txt"})["error"], "unknown-path")
        self.assertEqual(self.ask_wt(options={"path": "a.txt", "group": "x"})["error"], "bad-request")

    def test_large_diff_is_truncated_per_group_with_a_prefixed_warning(self):
        with mock.patch.object(diffdata, "MAX_LINES", 5):
            reply = self.ask_wt()
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["truncated"])
        self.assertEqual(sorted((f["group"], f["path"]) for f in reply["files"]),
                         [("staged", "a.txt"), ("staged", "both.txt"), ("unstaged", "b.txt"),
                          ("unstaged", "both.txt")])
        self.assertTrue(any(w.startswith("W indeksie: ") for w in reply["warnings"]), reply["warnings"])

    def test_reading_does_not_change_index_refs_or_journal(self):
        self.journal.append("K-1", "card.started", entry="now", repo=str(self.repo))
        events = len(self.journal.read_complete("K-1"))
        index = self.repo / ".git" / "index"
        os.utime(index, (1_700_000_000, 1_700_000_000))  # stat plików w indeksie się rozjedzie z mtime
        os.utime(self.repo / "b.txt", None)
        before = (refs(self.repo), git(self.repo, "ls-files", "-s"), os.stat(index).st_mtime_ns,
                  index.read_bytes())
        self.ask_wt()
        self.ask_wt(options={"path": "both.txt", "group": "unstaged"})
        after = (refs(self.repo), git(self.repo, "ls-files", "-s"), os.stat(index).st_mtime_ns, index.read_bytes())
        self.assertEqual(after, before)
        self.assertEqual(len(self.journal.read_complete("K-1")), events)
        self.assertFalse((self.repo / ".git" / "index.lock").exists())

    def test_path_comes_from_the_request_not_the_journal(self):
        self.assertEqual(self.ask_wt(path=None)["repo"], os.path.realpath(self.repo))


class WorktreeValidationTest(CardDiffTestCase):
    def setUp(self):
        super().setUp()
        self.repo = dirty_repo(self.root)
        self.outside = make_repo(self.root / "outside")
        os.symlink(self.outside, self.root / "link")
        self.known = [str(self.repo)]

    def calls(self, **request):
        with mock.patch.object(self.reader, "run", wraps=self.reader.run) as run:
            reply = diffdata.request(self.reader, self.journal, json.dumps({"source": "worktree", **request}),
                                     self.known)
        return reply, run.call_count

    def test_paths_outside_the_list_are_bad_request_without_running_git(self):
        for path in (str(self.outside), str(self.root / "link"), f"{self.repo}/../outside", f"{self.repo}/..",
                     str(self.root), "/etc", "proj", "", None, 7, f"{self.repo}\0", "/nie/ma"):
            reply, count = self.calls(path=path)
            self.assertEqual((reply["ok"], reply["error"], count), (False, "bad-request", 0), path)

    def test_missing_path_field_is_bad_request(self):
        reply, count = self.calls()
        self.assertEqual((reply["error"], count), ("bad-request", 0))

    def test_symlink_to_a_listed_worktree_resolves_to_its_canonical_path(self):
        os.symlink(self.repo, self.root / "alias")
        reply, _ = self.calls(path=str(self.root / "alias"))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["repo"], os.path.realpath(self.repo))

    def test_deleted_worktree_directory_is_an_error_with_a_message(self):
        gone = self.root / "gone"
        shutil.copytree(self.repo, gone)
        self.known.append(str(gone))
        shutil.rmtree(gone)
        reply, count = self.calls(path=str(gone))
        self.assertEqual((reply["ok"], reply["error"], count), (False, "no-worktree", 0))
        self.assertIn("nie istnieje", reply["message"])
        self.assertIn(str(gone), reply["message"])

    def test_invalid_options_are_bad_request(self):
        for options in ({"group": "nope"}, {"group": 3}, {"path": ""}):
            reply, _ = self.calls(path=str(self.repo), options=options)
            self.assertEqual(reply["error"], "bad-request", options)

    def test_other_sources_ignore_the_worktree_path_field(self):
        reply = diffdata.request(self.reader, self.journal,
                                 json.dumps({"source": "commit", "path": str(self.repo), "sha": "HEAD"}), self.known)
        self.assertEqual(reply["error"], "bad-request")


if __name__ == "__main__":
    unittest.main()
