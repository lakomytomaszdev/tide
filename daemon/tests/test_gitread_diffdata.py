"""`Git.Diff` źródła `card` jako dane (K-36, PT-357): `gitread/diffdata.py` i `GitMonitor` na tymczasowych
repozytoriach i dzienniku karty w katalogu tymczasowym. Przez D-Bus: test_git_dbus.py."""
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tide_daemon.cardflow.journal import Journal
from tide_daemon.gitread import diffdata
from tide_daemon.gitread.diffparse import parse_diff
from tide_daemon.gitread.runner import GitReader
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


class GitMonitorTest(unittest.TestCase):
    def test_diff_reply_comes_later_from_the_pool_and_the_loop_stays_free(self):
        from tide_daemon.git_monitor import GitMonitor
        from .privatebus import app
        app()
        entered, release = threading.Event(), threading.Event()
        seen = []

        def slow(reader, journal, text):
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

        def broken(reader, journal, text):
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
