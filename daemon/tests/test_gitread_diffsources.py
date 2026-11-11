"""`Git.Diff` źródła `ticket`, `commit` i `range` (K-36, PT-349) oraz walidacja `repo`/refów (`gitread/validate.py`)
na prawdziwych tymczasowych repozytoriach i dzienniku karty w katalogu tymczasowym."""
import json
import os
import unittest
from unittest import mock

from tide_daemon.gitread import diffdata, validate
from tide_daemon.gitread.diffparse import parse_diff
from tide_daemon.gitread.runner import GitReadError
from .gitrepos import commit, git
from .test_gitread_diffdata import CardDiffTestCase, make_repo, refs, started


def ticket_branch(journal, card, repo, ticket, card_branch="card/k-1"):
    """Gałąź ticketu od gałęzi karty (jeden plik `<ticket>.txt`) i wpisy dziennika `reserve` i `ticket-branch`."""
    branch = f"ticket/{ticket.lower()}"
    git(repo, "checkout", "-q", "-b", branch, card_branch)
    commit(repo, f"{ticket.lower()}.txt", f"{ticket}\n", f"work {ticket}")
    git(repo, "checkout", "-q", "master")
    journal.append(card, "intent", action="reserve", key=ticket)
    journal.append(card, "result", action="reserve", key=ticket, ok=True, ticket=ticket, title=f"tytuł {ticket}")
    journal.append(card, "intent", action="ticket-branch", key=ticket, branch=branch)
    journal.append(card, "result", action="ticket-branch", key=ticket, ok=True, ticket=ticket, branch=branch,
                   worktree=str(repo) + "-wt", base=git(repo, "rev-parse", card_branch).strip())
    return branch


def merge_ticket(journal, card, repo, ticket, branch, card_branch="card/k-1"):
    """Scalenie ticketu do gałęzi karty (bez checkoutu gałęzi głównej) z parą zamiar/wynik w dzienniku."""
    card_tip = git(repo, "rev-parse", card_branch).strip()
    tip = git(repo, "rev-parse", branch).strip()
    journal.append(card, "intent", action="merge", key=ticket, branch=branch, into=card_branch,
                   expectedTip=tip, cardTip=card_tip)
    git(repo, "checkout", "-q", card_branch)
    git(repo, "merge", "-q", "--no-ff", "-m", f"Merge ticket {ticket}", branch)
    git(repo, "checkout", "-q", "master")
    journal.append(card, "result", action="merge", key=ticket, ok=True, commit=git(repo, "rev-parse", card_branch).strip())
    return card_tip, tip


class TicketSourceTest(CardDiffTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.card_repo()
        self.b1 = ticket_branch(self.journal, "K-1", self.repo, "PT-1")
        self.b2 = ticket_branch(self.journal, "K-1", self.repo, "PT-2")

    def ticket(self, ticket, **extra):
        return self.ask(source="ticket", card="K-1", ticket=ticket, **extra)

    def test_ticket_diff_starts_at_merge_base_with_card_branch(self):
        reply = self.ticket("PT-1")
        base = git(self.repo, "merge-base", "card/k-1", self.b1).strip()
        self.assert_matches_git(reply, self.repo, base, self.b1)
        self.assertEqual([f["path"] for f in reply["files"]], ["pt-1.txt"])
        self.assertEqual((reply["source"], reply["card"], reply["ticket"], reply["repo"]),
                         ("ticket", "K-1", "PT-1", str(self.repo)))
        self.assertEqual(reply["title"], "PT-1 · zmiany ticketu względem gałęzi karty")
        self.assertEqual(reply["to"]["ref"], f"refs/heads/{self.b1}")
        self.assertEqual(reply["from"]["sha"], base)

    def test_second_ticket_does_not_contain_files_of_the_first_merged_one(self):
        merge_ticket(self.journal, "K-1", self.repo, "PT-1", self.b1)
        reply = self.ticket("PT-2")
        self.assertEqual([f["path"] for f in reply["files"]], ["pt-2.txt"])
        self.assertEqual(reply["totals"], {"files": 1, "additions": 1, "deletions": 0})

    def test_merged_ticket_with_deleted_branch_uses_range_recorded_in_journal(self):
        before = self.ticket("PT-1")
        card_tip, tip = merge_ticket(self.journal, "K-1", self.repo, "PT-1", self.b1)
        git(self.repo, "branch", "-q", "-D", self.b1)
        reply = self.ticket("PT-1")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual((reply["files"], reply["totals"]), (before["files"], before["totals"]))
        self.assertEqual(reply["to"]["sha"], tip)
        self.assertEqual(reply["from"]["sha"], git(self.repo, "merge-base", card_tip, tip).strip())

    def test_merged_ticket_with_surviving_branch_does_not_show_an_empty_range(self):
        before = self.ticket("PT-1")
        merge_ticket(self.journal, "K-1", self.repo, "PT-1", self.b1)
        reply = self.ticket("PT-1")
        self.assertEqual((reply["files"], reply["totals"]), (before["files"], before["totals"]))

    def test_deleted_branch_without_merge_record_is_no_ticket_range(self):
        git(self.repo, "branch", "-q", "-D", self.b1)
        reply = self.ticket("PT-1")
        self.assertEqual((reply["ok"], reply["error"]), (False, "no-ticket-range"))
        self.assertIn("PT-1", reply["message"])

    def test_recorded_shas_missing_from_repository_are_no_ticket_range(self):
        self.journal.append("K-1", "intent", action="merge", key="PT-1", expectedTip="a" * 40, cardTip="b" * 40)
        self.journal.append("K-1", "result", action="merge", key="PT-1", ok=True)
        git(self.repo, "branch", "-q", "-D", self.b1)
        self.assertEqual(self.ticket("PT-1")["error"], "no-ticket-range")

    def test_failed_or_option_like_merge_record_is_no_ticket_range(self):
        git(self.repo, "branch", "-q", "-D", self.b1)
        tip = git(self.repo, "rev-parse", "card/k-1").strip()
        self.journal.append("K-1", "intent", action="merge", key="PT-1", expectedTip=tip, cardTip=tip)
        self.journal.append("K-1", "result", action="merge", key="PT-1", ok=False, error="konflikt")
        self.assertEqual(self.ticket("PT-1")["error"], "no-ticket-range")
        self.journal.append("K-1", "intent", action="merge", key="PT-1", expectedTip="--output=/x", cardTip=tip)
        self.journal.append("K-1", "result", action="merge", key="PT-1", ok=True)
        self.assertEqual(self.ticket("PT-1")["error"], "no-ticket-range")

    def test_unknown_ticket_and_bad_input_are_bad_request(self):
        for request in (dict(ticket="PT-99"), dict(ticket="nie ticket"), dict(ticket="-x"), dict(ticket=5),
                        dict(ticket=None)):
            self.assertEqual(self.ask(source="ticket", card="K-1", **request)["error"], "bad-request", request)
        self.assertEqual(self.ask(source="ticket", card="K-77", ticket="PT-1")["error"], "unknown-card")

    def test_repo_of_ticket_source_comes_only_from_the_journal(self):
        reply = self.ask(source="ticket", card="K-1", ticket="PT-1", repo="/etc")
        self.assertEqual(reply["repo"], str(self.repo))

    def test_reading_does_not_write_journal_or_change_refs_and_index(self):
        events = len(self.journal.read_complete("K-1"))
        before = refs(self.repo)
        index = git(self.repo, "ls-files", "-s")
        self.ticket("PT-1")
        self.ticket("PT-9")
        self.assertEqual(len(self.journal.read_complete("K-1")), events)
        self.assertEqual((refs(self.repo), git(self.repo, "ls-files", "-s")), (before, index))


class CommitAndRangeTest(CardDiffTestCase):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.root / "proj")
        self.other = make_repo(self.root / "other")
        self.known = [str(self.repo)]

    def ask_repo(self, **request):
        return diffdata.request(self.reader, self.journal, json.dumps(request), self.known)

    def test_commit_diff_against_first_parent(self):
        commit(self.repo, "n.txt", "nowy\n", "dodaj n")
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        reply = self.ask_repo(source="commit", repo=str(self.repo), sha=sha)
        self.assert_matches_git(reply, self.repo, sha + "^", sha)
        self.assertEqual(reply["title"], f"commit {sha[:7]} — dodaj n")
        self.assertEqual(reply["from"]["sha"], git(self.repo, "rev-parse", sha + "^").strip())
        self.assertEqual((reply["source"], reply["repo"]), ("commit", os.path.realpath(self.repo)))

    def test_merge_commit_is_compared_with_first_parent(self):
        git(self.repo, "checkout", "-q", "-b", "side")
        commit(self.repo, "side.txt", "s\n")
        git(self.repo, "checkout", "-q", "master")
        commit(self.repo, "main.txt", "m\n")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "scal side", "side")
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        reply = self.ask_repo(source="commit", repo=str(self.repo), sha="HEAD")
        self.assert_matches_git(reply, self.repo, sha + "^1", sha)
        self.assertEqual([f["path"] for f in reply["files"]], ["side.txt"])

    def test_root_commit_is_compared_with_empty_tree(self):
        root = git(self.repo, "rev-list", "--max-parents=0", "HEAD").split()[0]
        reply = self.ask_repo(source="commit", repo=str(self.repo), sha=root)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual({f["path"]: f["status"] for f in reply["files"]}, {"a.txt": "A"})
        self.assertEqual(reply["from"], {"ref": "empty-tree", "sha": diffdata.EMPTY_TREES[40], "date": ""})
        self.assertEqual(reply["totals"], {"files": 1, "additions": 3, "deletions": 0})

    def test_range_starts_at_merge_base_and_ignores_changes_on_from(self):
        git(self.repo, "checkout", "-q", "-b", "feat")
        commit(self.repo, "feat.txt", "f\n")
        git(self.repo, "checkout", "-q", "master")
        commit(self.repo, "main-only.txt", "m\n")
        base = git(self.repo, "merge-base", "master", "feat").strip()
        reply = self.ask_repo(source="range", repo=str(self.repo), **{"from": "master", "to": "feat"})
        self.assert_matches_git(reply, self.repo, base, "feat")
        self.assertEqual([f["path"] for f in reply["files"]], ["feat.txt"])
        self.assertEqual((reply["from"]["sha"], reply["title"]), (base, "feat względem master"))
        full = self.ask_repo(source="range", repo=str(self.repo), **{"from": "refs/heads/master",
                                                                     "to": git(self.repo, "rev-parse", "feat").strip()})
        self.assertEqual(full["files"], reply["files"])

    def test_worktree_from_the_list_is_accepted(self):
        wt = self.root / "wt"
        git(self.repo, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
        commit(wt, "w.txt", "w\n")
        self.known.append(str(wt))
        reply = self.ask_repo(source="commit", repo=str(wt), sha="HEAD")
        self.assertEqual([f["path"] for f in reply["files"]], ["w.txt"])

    def test_options_path_works_for_commit(self):
        commit(self.repo, "n.txt", "a\nb\n", "dodaj n")
        reply = self.ask_repo(source="commit", repo=str(self.repo), sha="HEAD", options={"path": "n.txt"})
        self.assertEqual((reply["path"], reply["totals"]["files"]), ("n.txt", 1))


class ValidationTest(CardDiffTestCase):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.root / "proj")
        self.outside = make_repo(self.root / "outside")
        os.symlink(self.outside, self.root / "link")
        self.known = [str(self.repo)]

    def calls(self, **request):
        with mock.patch.object(self.reader, "run", wraps=self.reader.run) as run:
            reply = diffdata.request(self.reader, self.journal, json.dumps(request), self.known)
        return reply, run.call_count

    def test_repos_outside_the_known_list_are_rejected_without_running_git(self):
        for repo in (str(self.outside), str(self.root / "link"), f"{self.repo}/../outside", str(self.root),
                     "/etc", "proj", "", None, 7, f"{self.repo}\0", "/nie/ma"):
            for request in (dict(source="commit", repo=repo, sha="HEAD"),
                            dict(source="range", repo=repo, **{"from": "master", "to": "master"})):
                reply, count = self.calls(**request)
                self.assertEqual((reply["ok"], reply["error"], count), (False, "bad-request", 0), (repo, request))

    def test_symlink_to_a_known_repo_resolves_to_the_canonical_path(self):
        os.symlink(self.repo, self.root / "alias")
        reply, _ = self.calls(source="commit", repo=str(self.root / "alias"), sha="HEAD")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["repo"], os.path.realpath(self.repo))

    def test_dot_dot_is_rejected_even_when_it_leads_back_to_a_known_repo(self):
        reply, count = self.calls(source="commit", repo=f"{self.root}/outside/../proj", sha="HEAD")
        self.assertEqual((reply["error"], count), ("bad-request", 0))

    def test_refs_starting_with_dash_or_not_a_commit_are_bad_request(self):
        for ref in ("--output=/tmp/x", "-p", "--help", "", None, 3, "nie-ma-takiego", "master\n--output=x",
                    "HEAD:a.txt"):
            reply, _ = self.calls(source="commit", repo=str(self.repo), sha=ref)
            self.assertEqual((reply["ok"], reply["error"]), (False, "bad-request"), ref)
            reply, _ = self.calls(source="range", repo=str(self.repo), **{"from": "master", "to": ref})
            self.assertEqual(reply["error"], "bad-request", ref)
            reply, _ = self.calls(source="range", repo=str(self.repo), **{"from": ref, "to": "master"})
            self.assertEqual(reply["error"], "bad-request", ref)

    def test_dash_ref_never_reaches_git(self):
        reply, count = self.calls(source="commit", repo=str(self.repo), sha="--output=/tmp/x")
        self.assertEqual((reply["error"], count), ("bad-request", 0))
        self.assertFalse(os.path.exists("/tmp/x"))

    def test_range_without_common_history_is_bad_request(self):
        git(self.repo, "checkout", "-q", "--orphan", "alone")
        git(self.repo, "rm", "-rqf", ".")
        commit(self.repo, "z.txt", "z\n")
        reply, _ = self.calls(source="range", repo=str(self.repo), **{"from": "master", "to": "alone"})
        self.assertEqual(reply["error"], "bad-request")

    def test_validate_functions_directly(self):
        self.assertEqual(validate.known_repo(str(self.repo), [str(self.repo), "x", None]), os.path.realpath(self.repo))
        with self.assertRaises(GitReadError) as raised:
            validate.known_repo(str(self.repo), [])
        self.assertEqual(raised.exception.code, "bad-request")
        self.assertEqual(validate.commit_of(self.reader, str(self.repo), "master"),
                         git(self.repo, "rev-parse", "master").strip())

    def test_no_known_repos_means_every_repo_is_rejected(self):
        reply = diffdata.request(self.reader, self.journal,
                                 json.dumps(dict(source="commit", repo=str(self.repo), sha="HEAD")))
        self.assertEqual(reply["error"], "bad-request")

    def test_commit_source_does_not_touch_refs_index_or_journal(self):
        self.journal.append("K-1", "card.started", entry="now", repo=str(self.repo))
        events = len(self.journal.read_complete("K-1"))
        before, index = refs(self.repo), git(self.repo, "ls-files", "-s")
        self.calls(source="commit", repo=str(self.repo), sha="HEAD")
        self.calls(source="range", repo=str(self.repo), **{"from": "master", "to": "HEAD"})
        self.assertEqual((len(self.journal.read_complete("K-1")), refs(self.repo), git(self.repo, "ls-files", "-s")),
                         (events, before, index))


class MonitorReposTest(unittest.TestCase):
    def test_monitor_passes_the_known_repos_source_to_the_request(self):
        from tide_daemon.git_monitor import GitMonitor
        seen = []
        monitor = GitMonitor(repos_source=lambda: ["/a", "/b"])
        self.addCleanup(monitor.stop)
        with mock.patch.object(diffdata, "request", lambda reader, journal, text, repos=(): seen.append(repos) or {}):
            monitor._compute(monitor.reader, None, "{}", monitor._known_repos())
        self.assertEqual(seen, [["/a", "/b"]])

    def test_repos_are_computed_in_the_calling_thread(self):
        import threading
        from tide_daemon.git_monitor import GitMonitor
        threads, seen = [], []
        monitor = GitMonitor(repos_source=lambda: threads.append(threading.current_thread()) or ["/a"],
                             compute=lambda reader, journal, text, repos: seen.append(repos) or {})
        self.addCleanup(monitor.stop)
        monitor.diff("{}", lambda text: None)
        monitor.reader.close()
        self.assertEqual(threads, [threading.current_thread()])
    def test_failing_repos_source_means_no_known_repos(self):
        from tide_daemon.git_monitor import GitMonitor

        def broken():
            raise RuntimeError("tablica")

        monitor = GitMonitor(repos_source=broken)
        self.addCleanup(monitor.stop)
        self.assertEqual(monitor._known_repos(), [])


if __name__ == "__main__":
    unittest.main()
