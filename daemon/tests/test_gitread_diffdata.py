"""`Git.Diff` źródła `card` jako dane (K-36, PT-357): `gitread/diffdata.py` i `GitMonitor` na tymczasowych
repozytoriach i dzienniku karty w katalogu tymczasowym. Przez D-Bus: test_git_dbus.py."""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import CancelledError
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow.journal import Journal
from tide_daemon.gitread import diffdata
from tide_daemon.gitread.diffparse import parse_diff
from tide_daemon.gitread.runner import GitReader
from tide_daemon.worktrees.cancellation import Cancellation
from .gitrepos import COMMIT_TS, commit, git
from .test_gitread import arm_traps


def make_repo(path: Path, main: str = "master") -> Path:
    """Repozytorium bez zdalnego `origin`: gałąź główna `main` z dwoma plikami."""
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", main)
    commit(path, "a.txt", "alfa\nbeta\ngamma\n")
    commit(path, "b.txt", "jeden\n")
    return path


def card_branch(repo: Path, branch: str, main: str = "master") -> None:
    """Gałąź karty od `main`: zmiana, nowy plik i usunięcie."""
    git(repo, "checkout", "-q", "-b", branch, main)
    commit(repo, "a.txt", "alfa\nBETA\ngamma\ndelta\n", "zmiana a")
    commit(repo, "c.txt", "nowy\nplik\n", "dodaj c")
    git(repo, "rm", "-q", "b.txt")
    git(repo, "commit", "-q", "-m", "usuń b")
    git(repo, "checkout", "-q", main)


def started(journal: Journal, card: str, repo: Path, *, main="master", base=True, branch="card/x") -> None:
    """Zdarzenia, które zapisuje start zlecenia: `card.started` (repozytorium, baza) i para gałęzi karty."""
    sha = git(repo, "rev-parse", main).strip()
    fields = {"baseRef": f"refs/heads/{main}", "baseBranch": main, "baseCommit": sha} if base else {}
    journal.append(card, "card.started", entry="now", repo=str(repo), project=repo.name, **fields)
    if branch:
        journal.append(card, "intent", action="card-branch", key=branch, repo=str(repo), branch=branch,
                       worktree=str(repo) + "-wt", base=sha if base else "")
        journal.append(card, "result", action="card-branch", key=branch, ok=True, branch=branch,
                       worktree=str(repo) + "-wt", base=sha, existing=not base)


def refs(repo: Path) -> str:
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)") + git(repo, "rev-parse", "HEAD")


class CardDiffTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.journal = Journal(str(self.root / "board"), mirror=str(self.root / "state"))
        self.reader = GitReader(timeout=30)
        self.addCleanup(self.reader.close)

    def ask(self, **request):
        return diffdata.request(self.reader, self.journal, json.dumps(request))

    def card_repo(self, card="K-1", main="master", name="proj"):
        repo = make_repo(self.root / name, main)
        card_branch(repo, f"card/{card.lower()}", main)
        started(self.journal, card, repo, main=main, branch=f"card/{card.lower()}")
        return repo

    def assert_matches_git(self, reply, repo, start, end):
        """Pliki, hunki i sumy jak w `git diff <start> <end>` i `git diff --numstat`."""
        self.assertTrue(reply["ok"], reply)
        expected = parse_diff(git(repo, "diff", start, end))
        self.assertEqual(reply["files"], expected["files"])
        self.assertEqual(reply["totals"], expected["totals"])
        numstat = [line.split("\t") for line in git(repo, "diff", "--numstat", start, end).splitlines()]
        self.assertEqual(sorted((p, int(a), int(d)) for a, d, p in numstat),
                         sorted((f["path"], f["additions"], f["deletions"]) for f in reply["files"]))
        self.assertEqual(reply["totals"]["additions"], sum(int(a) for a, _d, _p in numstat))
        self.assertEqual(reply["totals"]["deletions"], sum(int(d) for _a, d, _p in numstat))


class CardSourceTest(CardDiffTestCase):
    def test_card_diff_matches_git_diff_and_numstat_from_pinned_base(self):
        repo = self.card_repo()
        base = git(repo, "rev-parse", "master").strip()
        reply = self.ask(source="card", card="K-1")
        self.assert_matches_git(reply, repo, base, "card/k-1")
        self.assertEqual({f["path"]: f["status"] for f in reply["files"]}, {"a.txt": "M", "b.txt": "D", "c.txt": "A"})
        self.assertTrue(reply["files"][0]["hunks"])
        date = datetime.fromtimestamp(COMMIT_TS, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(reply["from"], {"ref": "refs/heads/master", "sha": base, "date": date})
        self.assertEqual(reply["to"]["date"], date)
        self.assertEqual(reply["to"]["ref"], "refs/heads/card/k-1")
        self.assertEqual(reply["to"]["sha"], git(repo, "rev-parse", "card/k-1").strip())
        self.assertEqual((reply["source"], reply["card"], reply["repo"]), ("card", "K-1", str(repo)))
        self.assertEqual((reply["truncated"], reply["warnings"]), (False, []))
        self.assertIn("K-1", reply["title"])
        self.assertEqual(set(reply), {"ok", "source", "card", "repo", "title", "from", "to", "files", "totals",
                                      "truncated", "warnings"})

    def test_range_stays_at_pinned_base_after_start_branch_moves(self):
        repo = self.card_repo()
        base = git(repo, "rev-parse", "master").strip()
        commit(repo, "nowe-na-master.txt", "x\n")
        reply = self.ask(source="card", card="K-1")
        self.assertEqual(reply["from"]["sha"], base)
        self.assertNotIn("nowe-na-master.txt", [f["path"] for f in reply["files"]])
        self.assert_matches_git(reply, repo, base, "card/k-1")

    def test_target_ref_starts_at_merge_base_of_target_and_card(self):
        repo = self.card_repo()
        base = git(repo, "rev-parse", "master").strip()
        commit(repo, "z-master.txt", "x\n")
        git(repo, "checkout", "-q", "card/k-1")
        git(repo, "merge", "-q", "--no-edit", "master")
        git(repo, "checkout", "-q", "master")
        tip = git(repo, "rev-parse", "master").strip()
        own = self.ask(source="card", card="K-1")
        self.assertEqual(own["from"]["sha"], base)
        self.assertIn("z-master.txt", [f["path"] for f in own["files"]])  # scalone do karty po starcie
        target = self.ask(source="card", card="K-1", targetRef="refs/heads/master")
        self.assertEqual(target["from"], {"ref": "refs/heads/master", "sha": tip, "date": target["from"]["date"]})
        self.assertNotIn("z-master.txt", [f["path"] for f in target["files"]])
        self.assert_matches_git(target, repo, tip, "card/k-1")
        short = self.ask(source="card", card="K-1", targetRef="master")
        self.assertEqual(short["from"]["sha"], tip)

    def test_bad_target_ref_is_bad_request_and_writes_nothing(self):
        self.card_repo()
        escape = self.root / "wyjscie"
        for ref in ("nie-ma-takiego", f"--output={escape}", "", 5):
            reply = self.ask(source="card", card="K-1", targetRef=ref)
            if ref == "":
                self.assertTrue(reply["ok"], reply)  # pusty = brak celu
                continue
            self.assertEqual((reply["ok"], reply["error"]), (False, "bad-request"), (ref, reply))
            self.assertTrue(reply["message"])
        self.assertFalse(escape.exists())
        tree = git(self.root / "proj", "rev-parse", "HEAD^{tree}").strip()
        orphan = git(self.root / "proj", "commit-tree", tree, "-m", "sierota")  # commit bez rodzica
        git(self.root / "proj", "update-ref", "refs/heads/sierota", orphan.strip())
        reply = self.ask(source="card", card="K-1", targetRef="refs/heads/sierota")
        self.assertEqual(reply["error"], "bad-request")
        self.assertIn("wspólnej historii", reply["message"])

    def test_unknown_base_without_target_ref_asks_for_explicit_ref(self):
        repo = make_repo(self.root / "proj")
        card_branch(repo, "card/k-1")
        started(self.journal, "K-1", repo, base=False, branch="card/k-1")
        reply = self.ask(source="card", card="K-1")
        self.assertEqual((reply["ok"], reply["error"]), (False, "unknown-base"))
        self.assertIn("jawny ref porównania", reply["message"])
        with_target = self.ask(source="card", card="K-1", targetRef="refs/heads/master")
        self.assertTrue(with_target["ok"], with_target)
        self.assert_matches_git(with_target, repo, "master", "card/k-1")

    def test_pinned_base_missing_from_repository_is_unknown_base(self):
        repo = make_repo(self.root / "proj")
        card_branch(repo, "card/k-1")
        self.journal.append("K-1", "card.started", entry="now", repo=str(repo), baseRef="refs/heads/master",
                            baseBranch="master", baseCommit="1" * 40)
        self.journal.append("K-1", "result", action="card-branch", key="card/k-1", ok=True, branch="card/k-1")
        self.assertEqual(self.ask(source="card", card="K-1")["error"], "unknown-base")

    def test_unknown_card_card_without_branch_and_bad_requests_have_own_codes(self):
        repo = make_repo(self.root / "proj")
        started(self.journal, "K-2", repo, branch="")
        started(self.journal, "K-3", repo, branch="card/k-3")  # zapisana gałąź, której w repozytorium nie ma
        self.journal.append("K-4", "card.moved", **{"from": "backlog", "to": "todo"})  # dziennik bez zlecenia
        cases = [({"source": "card", "card": "K-99"}, "unknown-card"),
                 ({"source": "card", "card": "K-4"}, "unknown-card"),
                 ({"source": "card", "card": "K-2"}, "no-branch"),
                 ({"source": "card", "card": "K-3"}, "no-branch"),
                 ({"source": "card", "card": "abc"}, "bad-request"),
                 ({"source": "card"}, "bad-request"),
                 ({"source": "ticket", "card": "K-2"}, "bad-request"),
                 ({"card": "K-2"}, "bad-request"),
                 ({"source": "card", "card": "K-2", "options": []}, "bad-request")]
        for request, code in cases:
            reply = diffdata.request(self.reader, self.journal, json.dumps(request))
            self.assertEqual((reply["ok"], reply["error"]), (False, code), (request, reply))
            self.assertTrue(reply["message"])
        for text in ("nie json", "[]", None):
            self.assertEqual(diffdata.request(self.reader, self.journal, text)["error"], "bad-request")
        reply = diffdata.request(self.reader, None, json.dumps({"source": "card", "card": "K-2"}))
        self.assertEqual(reply["error"], "unavailable")

    def test_repository_path_comes_only_from_the_journal(self):
        repo = self.card_repo()
        other = make_repo(self.root / "inne")
        reply = self.ask(source="card", card="K-1", repo=str(other), repoPath=str(other))
        self.assertEqual(reply["repo"], str(repo))


class MainBranchNamesTest(CardDiffTestCase):
    """Historia 60: repozytorium bez `origin` z gałęzią główną `main` albo `develop`."""

    def check(self, main):
        repo = self.card_repo(main=main)
        self.assertEqual(git(repo, "remote").strip(), "")
        reply = self.ask(source="card", card="K-1")
        self.assertEqual(reply["from"]["ref"], f"refs/heads/{main}")
        self.assert_matches_git(reply, repo, main, "card/k-1")
        target = self.ask(source="card", card="K-1", targetRef=main)
        self.assert_matches_git(target, repo, main, "card/k-1")

    def test_main(self):
        self.check("main")

    def test_develop(self):
        self.check("develop")


class GroupCardTest(CardDiffTestCase):
    def test_card_from_card_group_reads_repository_saved_at_start(self):
        """Karta grupy kart z innego projektu: repozytorium z `card.started` tej karty, nie innej karty grupy."""
        first = self.card_repo("K-1", name="proj")
        second = self.card_repo("K-2", main="develop", name="inny-projekt")
        self.assertFalse(git(first, "branch", "--list", "card/k-2").strip())
        reply = self.ask(source="card", card="K-2")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["repo"], str(second))
        self.assertEqual(reply["from"]["ref"], "refs/heads/develop")
        self.assert_matches_git(reply, second, "develop", "card/k-2")


class ReadOnlyTest(CardDiffTestCase):
    def test_reading_adds_no_events_and_changes_no_refs(self):
        repo = self.card_repo()
        journal_file = Path(self.journal.path("K-1"))
        mirror_file = Path(self.journal.mirror_path("K-1"))
        before = (len(self.journal.read("K-1")), journal_file.read_bytes(), mirror_file.read_bytes(), refs(repo))
        for request in ({"source": "card", "card": "K-1"},
                        {"source": "card", "card": "K-1", "targetRef": "master"},
                        {"source": "card", "card": "K-1", "targetRef": "nie-ma"}):
            diffdata.request(self.reader, self.journal, json.dumps(request))
        self.assertEqual(before, (len(Journal(self.journal.root, mirror=self.journal.mirror).read("K-1")),
                                  journal_file.read_bytes(), mirror_file.read_bytes(), refs(repo)))

    def test_trap_configuration_does_not_run_for_card_diff(self):
        """Pułapka z PT-348: konfiguracja repozytorium wskazuje skrypt wszędzie (diff.external, textconv, filtry,
        haki, fsmonitor, pager, gpg) — `Git.Diff` karty go nie uruchamia."""
        repo = self.card_repo()
        marker = arm_traps(repo, self.root)
        (repo / "a.txt").write_text("roboczo\n")
        reply = self.ask(source="card", card="K-1")
        self.assertTrue(reply["ok"], reply)
        self.assertIn("a.txt", [f["path"] for f in reply["files"]])
        reply = self.ask(source="card", card="K-1", targetRef="master")
        self.assertTrue(reply["ok"], reply)
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")


def numbered(count: int, prefix: str = "linia") -> str:
    return "".join(f"{prefix} {n}\n" for n in range(count))


class BigDiffTest(CardDiffTestCase):
    """PT-355: limity całości, plik `large`, binarne z rozmiarami, `options.path` i `options.context`."""

    def big_repo(self):
        """Karta dodająca: duży plik (3001 linii), kilka średnich plików, mały plik i plik binarny."""
        repo = make_repo(self.root / "proj")
        commit(repo, "ctx.txt", numbered(60, "ctx"))
        (repo / "bin.dat").write_bytes(b"\x00\x01\x02" * 10)
        git(repo, "add", "bin.dat")
        git(repo, "commit", "-q", "-m", "binarny")
        git(repo, "checkout", "-q", "-b", "card/k-1")
        commit(repo, "big.txt", numbered(3001), "duży plik")
        for n in range(5):
            commit(repo, f"mid{n}.txt", numbered(40, f"m{n}"), f"średni {n}")
        commit(repo, "small.txt", "jeden\n", "mały")
        text = numbered(60, "ctx").replace("ctx 30\n", "ZMIANA\n")
        commit(repo, "ctx.txt", text, "zmiana w środku")
        (repo / "bin.dat").write_bytes(b"\x00\x01\x02" * 100)
        git(repo, "add", "bin.dat")
        git(repo, "commit", "-q", "-m", "binarny większy")
        git(repo, "checkout", "-q", "master")
        started(self.journal, "K-1", repo, branch="card/k-1")
        return repo

    def by_path(self, reply):
        return {f["path"]: f for f in reply["files"]}

    def test_default_limits_leave_normal_diff_untouched_and_mark_large_file(self):
        repo = self.big_repo()
        reply = self.ask(source="card", card="K-1")
        self.assertTrue(reply["ok"], reply)
        files = self.by_path(reply)
        big = files["big.txt"]
        self.assertEqual((big["large"], big["hunks"], big["lines"], big["additions"]), (True, [], 3001, 3001))
        self.assertNotIn("truncated", big)
        self.assertTrue(files["small.txt"]["hunks"])
        self.assertNotIn("large", files["small.txt"])
        self.assertNotIn("oldSize", files["small.txt"])
        self.assertEqual(reply["truncated"], False)
        self.assertEqual(reply["warnings"], [])
        self.assertEqual(reply["totals"]["files"], len(files))
        self.assertEqual(reply["totals"]["additions"], 3001 + 5 * 40 + 1 + 1)

    def test_binary_file_has_sizes_and_new_or_removed_side_is_none(self):
        repo = self.big_repo()
        files = self.by_path(self.ask(source="card", card="K-1"))
        binary = files["bin.dat"]
        self.assertEqual((binary["binary"], binary["oldSize"], binary["newSize"], binary["hunks"]),
                         (True, 30, 300, []))
        git(repo, "checkout", "-q", "card/k-1")
        (repo / "nowy.bin").write_bytes(b"\x00" * 7)
        git(repo, "add", "nowy.bin")
        git(repo, "commit", "-q", "-m", "nowy binarny")
        git(repo, "checkout", "-q", "master")
        files = self.by_path(self.ask(source="card", card="K-1"))
        self.assertEqual((files["nowy.bin"]["status"], files["nowy.bin"]["oldSize"], files["nowy.bin"]["newSize"]),
                         ("A", None, 7))

    def test_removed_binary_file_has_old_size_only(self):
        repo = make_repo(self.root / "proj")
        (repo / "stary.bin").write_bytes(b"\x00\x01" * 8)
        git(repo, "add", "stary.bin")
        git(repo, "commit", "-q", "-m", "binarny")
        git(repo, "checkout", "-q", "-b", "card/k-1")
        git(repo, "rm", "-q", "stary.bin")
        git(repo, "commit", "-q", "-m", "usuń")
        git(repo, "checkout", "-q", "master")
        started(self.journal, "K-1", repo, branch="card/k-1")
        entry = self.by_path(self.ask(source="card", card="K-1"))["stary.bin"]
        self.assertEqual((entry["status"], entry["binary"], entry["oldSize"], entry["newSize"]), ("D", True, 16, None))

    def check_truncated(self, reply, repo):
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["truncated"])
        self.assertTrue(any("limit" in w and "doczytać" in w for w in reply["warnings"]), reply["warnings"])
        base = git(repo, "rev-parse", "master").strip()
        numstat = {p: (int(a) if a != "-" else 0, int(d) if d != "-" else 0) for a, d, p in
                   (line.split("\t") for line in git(repo, "diff", "--numstat", base, "card/k-1").splitlines())}
        files = self.by_path(reply)
        self.assertEqual(set(files), set(numstat))  # lista wszystkich plików zawsze
        for path, f in files.items():
            self.assertEqual((f["additions"], f["deletions"]), numstat[path], path)
        self.assertEqual(reply["totals"], {"files": len(numstat), "additions": sum(a for a, _ in numstat.values()),
                                           "deletions": sum(d for _, d in numstat.values())})
        cut = [p for p, f in files.items() if f.get("truncated")]
        self.assertTrue(cut)
        for path in cut:
            self.assertEqual(files[path]["hunks"], [], path)
        return files

    def test_line_limit_truncates_content_but_keeps_full_file_list(self):
        repo = self.big_repo()
        with mock.patch.object(diffdata, "MAX_LINES", 3030):  # bin.dat, big.txt (3006 linii), ctx.txt, ucięty mid0
            reply = self.ask(source="card", card="K-1")
        files = self.check_truncated(reply, repo)
        self.assertTrue(files["ctx.txt"]["hunks"])  # plik w całości w limicie zachowuje treść
        self.assertTrue(files["mid0.txt"]["truncated"])
        self.assertTrue(files["big.txt"]["large"])
        self.assertNotIn("truncated", files["big.txt"])  # large wypiera truncated
        self.assertTrue(files["bin.dat"]["binary"])
        self.assertEqual(files["bin.dat"]["newSize"], 300)
        self.assertIn("pokazano część zmian", " ".join(reply["warnings"]))

    def test_byte_limit_truncates_the_same_way(self):
        repo = self.big_repo()
        with mock.patch.object(diffdata, "MAX_BYTES", 2500):
            reply = self.ask(source="card", card="K-1")
        self.check_truncated(reply, repo)

    def test_limits_are_the_documented_ones(self):
        self.assertEqual((diffdata.MAX_BYTES, diffdata.MAX_LINES, diffdata.LARGE_LINES),
                         (2 * 1024 * 1024, 20000, 3000))

    def test_real_line_limit_cuts_a_diff_of_more_than_20000_lines(self):
        repo = make_repo(self.root / "proj")
        git(repo, "checkout", "-q", "-b", "card/k-1")
        for n in range(9):  # 9 plików po 2900 linii: żaden nie jest `large`, razem ponad 20 000 linii diffu
            commit(repo, f"p{n}.txt", numbered(2900, f"p{n}"), f"plik {n}")
        git(repo, "checkout", "-q", "master")
        started(self.journal, "K-1", repo, branch="card/k-1")
        reply = self.ask(source="card", card="K-1")
        self.assertTrue(reply["ok"], reply)
        files = self.by_path(reply)
        self.assertTrue(reply["truncated"])
        self.assertEqual(len(files), 9)
        self.assertEqual(sum(1 for f in files.values() if f["hunks"]), 6)  # 6 plików w całości mieści się w limicie
        self.assertEqual(sum(1 for f in files.values() if f.get("truncated")), 3)
        self.assertEqual(reply["totals"]["additions"], 9 * 2900)

    def test_path_returns_one_file_with_hunks_also_for_large_and_truncated_files(self):
        repo = self.big_repo()
        with mock.patch.object(diffdata, "MAX_LINES", 3030):
            listing = self.ask(source="card", card="K-1")
        cut = next(p for p, f in self.by_path(listing).items() if f.get("truncated"))
        for path, lines in (("big.txt", 3001), (cut, None), ("small.txt", 1)):
            reply = self.ask(source="card", card="K-1", options={"path": path})
            self.assertTrue(reply["ok"], reply)
            self.assertEqual([f["path"] for f in reply["files"]], [path])
            file = reply["files"][0]
            self.assertTrue(file["hunks"], path)
            self.assertNotIn("large", file)
            self.assertNotIn("truncated", file)
            self.assertEqual((reply["path"], reply["truncated"]), (path, False))
            self.assertEqual(reply["totals"], {"files": 1, "additions": file["additions"],
                                               "deletions": file["deletions"]})
            if lines:
                self.assertEqual(sum(len(h["lines"]) for h in file["hunks"]), lines)
        base = git(repo, "rev-parse", "master").strip()
        expected = parse_diff(git(repo, "diff", base, "card/k-1", "--", "big.txt"))["files"]
        self.assertEqual(self.ask(source="card", card="K-1", options={"path": "big.txt"})["files"], expected)

    def test_context_expands_context_lines_of_one_file(self):
        repo = self.big_repo()

        def context_lines(**options):
            reply = self.ask(source="card", card="K-1", options={"path": "ctx.txt", **options})
            self.assertTrue(reply["ok"], reply)
            return reply["files"][0]["hunks"]

        default = context_lines()
        wide = context_lines(context=20)
        self.assertEqual(sum(1 for l in default[0]["lines"] if l["kind"] == " "), 6)  # domyślnie 3 + 3
        self.assertEqual(sum(1 for l in wide[0]["lines"] if l["kind"] == " "), 40)  # 20 + 20
        self.assertEqual(wide, parse_diff(git(repo, "diff", "-U20", "master", "card/k-1", "--", "ctx.txt"))
                         ["files"][0]["hunks"])
        self.assertEqual(context_lines(context=3), default)
        self.assertEqual(sum(1 for l in context_lines(context=0)[0]["lines"] if l["kind"] == " "), 0)

    def test_binary_path_returns_sizes(self):
        self.big_repo()
        reply = self.ask(source="card", card="K-1", options={"path": "bin.dat"})
        file = reply["files"][0]
        self.assertEqual((file["binary"], file["oldSize"], file["newSize"]), (True, 30, 300))

    def test_path_limit_marks_a_cut_single_file(self):
        self.big_repo()
        with mock.patch.object(diffdata, "PATH_MAX_LINES", 100):
            reply = self.ask(source="card", card="K-1", options={"path": "big.txt"})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["truncated"], reply["files"][0]["truncated"]), (True, True))
        self.assertTrue(any("limit" in w for w in reply["warnings"]))

    def test_option_validation(self):
        self.big_repo()
        for options, code in (({"context": 5}, "bad-request"), ({"path": ""}, "bad-request"),
                              ({"path": 5}, "bad-request"), ({"path": "small.txt", "context": -1}, "bad-request"),
                              ({"path": "small.txt", "context": "3"}, "bad-request"),
                              ({"path": "small.txt", "context": True}, "bad-request"),
                              ({"path": "small.txt", "context": 1.5}, "bad-request"),
                              ({"path": "nie-ma.txt"}, "unknown-path"),
                              ({"path": "--output=/tmp/x"}, "unknown-path")):
            reply = self.ask(source="card", card="K-1", options=options)
            self.assertEqual((reply["ok"], reply["error"]), (False, code), (options, reply))
        self.assertTrue(self.ask(source="card", card="K-1", options={"nieznane": 1})["ok"])  # addytywnie
        self.assertTrue(self.ask(source="card", card="K-1", options={})["ok"])


class CappedReadTest(unittest.TestCase):
    """Limit liczony przy czytaniu: nadmiar nie trafia do pamięci, a proces jest przerywany."""

    def setUp(self):
        self.cancel = Cancellation()
        self.env = dict(os.environ)

    def run_capped(self, argv, **limits):
        return self.cancel.run_capped(argv, timeout=10, env=self.env, **limits)

    def test_endless_output_is_cut_at_line_limit_without_reading_everything(self):
        started_at = time.monotonic()
        proc, cut = self.run_capped(["yes", "wiersz"], max_lines=1000)
        self.assertLess(time.monotonic() - started_at, 5)
        self.assertTrue(cut)
        self.assertEqual((proc.returncode, proc.stdout), (0, "wiersz\n" * 1000))

    def test_endless_output_is_cut_at_byte_limit_on_a_line_boundary(self):
        proc, cut = self.run_capped(["yes", "wiersz"], max_bytes=1000)
        self.assertTrue(cut)
        self.assertEqual(proc.stdout, "wiersz\n" * (1000 // 7))  # bez niepełnej ostatniej linii

    def test_output_exactly_at_the_limit_is_not_truncated(self):
        proc, cut = self.run_capped(["printf", "a\nb\nc\n"], max_lines=3)
        self.assertEqual((cut, proc.stdout, proc.returncode), (False, "a\nb\nc\n", 0))
        proc, cut = self.run_capped(["printf", "a\nb\nc\n"], max_bytes=6)
        self.assertEqual((cut, proc.stdout), (False, "a\nb\nc\n"))
        proc, cut = self.run_capped(["printf", "a\nb\nc\nd\n"], max_lines=3)
        self.assertEqual((cut, proc.stdout), (True, "a\nb\nc\n"))

    def test_exit_code_and_stderr_come_through_and_timeout_kills_the_process(self):
        proc, cut = self.run_capped(["sh", "-c", "echo błąd >&2; exit 3"], max_lines=10)
        self.assertEqual((cut, proc.returncode, proc.stderr.strip()), (False, 3, "błąd"))
        with self.assertRaises(subprocess.TimeoutExpired):
            self.cancel.run_capped(["sleep", "30"], timeout=.3, env=self.env, max_lines=10)
        self.cancel.close()
        with self.assertRaises(CancelledError):
            self.run_capped(["true"], max_lines=1)


class GitMonitorTest(unittest.TestCase):
    def test_diff_reply_comes_later_from_the_pool_and_the_loop_stays_free(self):
        from tide_daemon.git_monitor import GitMonitor
        from .privatebus import app
        app()
        entered, release = threading.Event(), threading.Event()
        seen = []

        def slow(reader, journal, text, repos):
            seen.append((threading.current_thread().name, journal, text))
            entered.set()
            release.wait(5)
            return {"ok": True, "echo": text}

        monitor = GitMonitor(lambda: "dziennik", compute=slow)
        self.addCleanup(monitor.stop)
        replies = []
        started_at = time.monotonic()
        monitor.diff('{"source":"card","card":"K-1"}', lambda data: replies.append(json.loads(data)))
        self.assertLess(time.monotonic() - started_at, 1.0)
        self.assertTrue(entered.wait(2))
        app().processEvents()
        self.assertEqual(replies, [], "odpowiedź przed końcem pracy w tle")
        release.set()
        deadline = time.monotonic() + 3
        while not replies and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(.01)
        self.assertEqual(replies, [{"ok": True, "echo": '{"source":"card","card":"K-1"}'}])
        self.assertTrue(seen[0][0].startswith("tide-gitread"))
        self.assertEqual(seen[0][1], "dziennik")

    def test_failure_in_background_is_a_reply_and_stop_refuses(self):
        from tide_daemon.git_monitor import GitMonitor
        from .privatebus import app
        app()

        def broken(reader, journal, text, repos):
            raise RuntimeError("zepsute")

        monitor = GitMonitor(compute=broken)
        replies = []
        monitor.diff("{}", lambda data: replies.append(json.loads(data)))
        deadline = time.monotonic() + 3
        while not replies and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(.01)
        self.assertEqual((replies[0]["ok"], replies[0]["error"]), (False, "failed"))
        monitor.stop()
        monitor.diff("{}", lambda data: replies.append(json.loads(data)))
        self.assertEqual(replies[1]["error"], "cancelled")


if __name__ == "__main__":
    unittest.main()
