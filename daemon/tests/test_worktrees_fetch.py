"""Worktrees: „Pobierz” (`Worktrees.Fetch`, PT-358).

Prawdziwe, tymczasowe repozytoria z gołym „zdalnym” w katalogu (bez sieci poza lokalnym serwerem 401
na 127.0.0.1). Sprawdzamy wynik, zapisane refy, brak pytań o hasło, maskowanie poświadczeń, brak
`--prune`, odmowę dla ścieżek spoza listy, wspólną blokadę ze scalaniem karty i limit czasu.
"""
import contextlib
import http.server
import io
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from . import gitrepos
from . import test_worktrees_divergence as base
from tide_daemon.cardflow.gitops import lock_for
from tide_daemon.worktrees import WorktreeScanner, fetch
from tide_daemon.worktrees.git import Git

git, commit = gitrepos.git, gitrepos.commit


class Rec(Git):
    def __init__(self):
        super().__init__()
        self.calls = []

    def run(self, cwd, *args, **kwargs):
        self.calls.append(args)
        return super().run(cwd, *args, **kwargs)

    def fetches(self):
        return [c for c in self.calls if "fetch" in c]


class Unauthorized(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="tide-test"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("git"), "brak gita")
class FetchTestCase(base.DivergenceTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.repo("proj")
        self.bare = self.add_remote(self.proj)
        git(self.proj, "push", "-q", "-u", "origin", "master")
        self.other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", self.bare, self.other)

    def scanner(self, git_impl=None):
        scanner = WorktreeScanner(self.root, git=git_impl or Git())
        scanner.refresh()
        return scanner

    def push_commits(self, n, branch="master"):
        for i in range(n):
            commit(self.other, f"r{branch.replace('/', '-')}{i}{time.monotonic_ns()}.txt")
        git(self.other, "push", "-q", "origin", f"HEAD:{branch}")

    def rev(self, ref, cwd=None):
        return git(cwd or self.proj, "rev-parse", ref).strip()


class FetchResultTest(FetchTestCase):
    def test_fetch_updates_remote_refs_and_last_fetch_with_commit_counts(self):
        scanner = self.scanner()
        self.assertEqual(self.item(scanner)["lastFetch"], "")
        self.push_commits(3)
        result = scanner.fetch(self.proj)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["newCommits"], 3)
        self.assertEqual(result["remotes"][0]["branches"][0],
                         {"branch": "master", "ref": "origin/master", "new": False, "commits": 3})
        self.assertEqual(result["message"], "pobrano: origin — 3 nowe commity na master")
        self.assertEqual(self.rev("refs/remotes/origin/master"), self.rev("refs/remotes/origin/master", self.other))
        scanner.refresh()
        item = self.item(scanner)
        self.assertNotEqual(item["lastFetch"], "")
        self.assertEqual(item["upstreamBehind"], 3)

    def item(self, scanner):
        return {w["path"]: w for w in scanner.snapshot([])}[self.proj]

    def test_fetch_without_news_says_so(self):
        result = self.scanner().fetch(self.proj)
        self.assertEqual((result["ok"], result["newCommits"]), (True, 0))
        self.assertEqual(result["message"], "pobrano: origin — brak nowych commitów")

    def test_new_remote_branch_and_single_commit(self):
        scanner = self.scanner()
        git(self.other, "checkout", "-q", "-b", "feat/new")
        self.push_commits(1, "feat/new")
        result = scanner.fetch(self.proj)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message"], "pobrano: origin — 1 nowy commit na feat/new")
        self.assertTrue(result["remotes"][0]["branches"][0]["new"])

    def test_all_remotes_are_fetched(self):
        backup = self.add_remote(self.proj, "backup")
        git(self.proj, "push", "-q", "backup", "master")
        scanner = self.scanner()
        self.push_commits(2)
        git(self.other, "push", "-q", backup, "HEAD:master")
        result = scanner.fetch(self.proj)
        self.assertTrue(result["ok"], result)
        self.assertEqual([r["name"] for r in result["remotes"]], ["origin", "backup"])
        self.assertIn("backup — 2 nowe commity na master", result["message"])

    def test_repository_without_remote_is_refused_without_fetch(self):
        solo = self.repo("solo")
        rec = Rec()
        scanner = self.scanner(rec)
        rec.calls.clear()
        result = scanner.fetch(solo)
        self.assertEqual((result["ok"], result["reason"]), (False, "no-remote"))
        self.assertEqual(rec.fetches(), [])

    def test_fetch_does_not_prune_and_does_not_touch_head_or_worktree(self):
        git(self.other, "checkout", "-q", "-b", "feat/gone")
        self.push_commits(1, "feat/gone")
        git(self.proj, "fetch", "-q", "origin")
        gone = self.rev("refs/remotes/origin/feat/gone")
        wt = self.worktree(self.proj, "feat/work")
        commit(wt, "w.txt")
        with open(os.path.join(wt, "w.txt"), "a", encoding="utf-8") as fh:
            fh.write("dirty\n")
        before = (self.rev("HEAD"), self.rev("HEAD", wt), git(wt, "status", "--porcelain"),
                  git(self.proj, "status", "--porcelain"), git(self.proj, "for-each-ref", "refs/heads"))
        git(self.bare, "branch", "-D", "feat/gone")
        self.push_commits(1)
        scanner = self.scanner()
        result = scanner.fetch(self.proj)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.rev("refs/remotes/origin/feat/gone"), gone)
        after = (self.rev("HEAD"), self.rev("HEAD", wt), git(wt, "status", "--porcelain"),
                 git(self.proj, "status", "--porcelain"), git(self.proj, "for-each-ref", "refs/heads"))
        self.assertEqual(before, after)

    def test_command_has_no_prune_force_tags_or_plus_refspec(self):
        rec = Rec()
        scanner = self.scanner(rec)
        rec.calls.clear()
        scanner.fetch(self.proj)
        (call,) = rec.fetches()
        self.assertIn("--no-prune", call)
        self.assertEqual(call[-2:], ("--", "origin"))
        for forbidden in ("--prune", "-p", "--force", "-f", "--tags", "-t", "--prune-tags", "--update-head-ok"):
            self.assertNotIn(forbidden, call)
        self.assertFalse([a for a in call if a.startswith("+")])
        verbs = {c[0] for c in rec.calls if c and not c[0].startswith("-")}
        self.assertFalse(verbs & {"push", "pull", "reset", "stash", "rebase", "merge", "checkout", "clone"}, verbs)


class FetchFailureTest(FetchTestCase):
    def test_missing_remote_directory_gives_one_sentence_without_prompt(self):
        git(self.proj, "remote", "set-url", "origin", os.path.join(self.tmp, "nie-ma-takiego-katalogu"))
        scanner = self.scanner()
        started = time.monotonic()
        result = scanner.fetch(self.proj)
        self.assertLess(time.monotonic() - started, fetch.FETCH_TIMEOUT)
        self.assertFalse(result["ok"])
        self.assertTrue(result["reason"])
        self.assertTrue(result["message"].startswith("origin: "))
        self.assertNotIn("\n", result["message"])

    def serve_401(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_remote_requiring_credentials_never_calls_askpass(self):
        marker = os.path.join(self.tmp, "askpass-called")
        askpass = os.path.join(self.tmp, "askpass.sh")
        with open(askpass, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\necho called > {marker}\necho secret\n")
        os.chmod(askpass, os.stat(askpass).st_mode | stat.S_IXUSR)
        git(self.proj, "remote", "set-url", "origin", f"http://127.0.0.1:{self.serve_401()}/repo.git")
        with mock.patch.dict(os.environ, {"GIT_ASKPASS": askpass, "SSH_ASKPASS": askpass}):
            scanner = self.scanner()
            result = scanner.fetch(self.proj)
        self.assertFalse(os.path.exists(marker), "git wywołał GIT_ASKPASS")
        self.assertEqual((result["ok"], result["reason"]), (False, "auth"))
        self.assertNotIn("\n", result["message"])

    def test_credentials_from_url_are_masked_in_message_and_logs(self):
        git(self.proj, "remote", "set-url", "origin", f"http://user:tok3n-secret@127.0.0.1:{self.serve_401()}/repo.git")
        scanner = self.scanner()
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            result = scanner.fetch(self.proj)
        self.assertFalse(result["ok"])
        for text in (result["message"], logs.getvalue(), repr(result)):
            self.assertNotIn("tok3n-secret", text)
            self.assertNotIn("user:", text)

    def test_mask_credentials(self):
        self.assertEqual(fetch.mask_credentials("fatal: unable to access 'https://user:tok@en@host.example/x.git/': e"),
                         "fatal: unable to access 'https://***@host.example/x.git/': e")
        self.assertEqual(fetch.mask_credentials("ssh://git@host/x and /srv/x@y"), "ssh://***@host/x and /srv/x@y")
        self.assertEqual(fetch.mask_credentials("https://host/a@b"), "https://host/a@b")

    def test_environment_is_batch_and_without_askpass(self):
        env = fetch.fetch_env({"GIT_ASKPASS": "/x", "SSH_ASKPASS": "/y", "SSH_AUTH_SOCK": "/s", "GIT_SSH": "/opt/my ssh"})
        self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["SSH_ASKPASS"]), ("0", "", ""))
        self.assertEqual(env["GIT_SSH_COMMAND"], "'/opt/my ssh' -o BatchMode=yes")
        self.assertEqual(env["SSH_AUTH_SOCK"], "/s")
        self.assertEqual(fetch.fetch_env({"GIT_SSH_COMMAND": "ssh -i k"})["GIT_SSH_COMMAND"], "ssh -i k -o BatchMode=yes")
        self.assertEqual(fetch.fetch_env({}, "ssh -p 2222")["GIT_SSH_COMMAND"], "ssh -p 2222 -o BatchMode=yes")
        self.assertEqual(fetch.fetch_env({})["GIT_SSH_COMMAND"], "ssh -o BatchMode=yes")

    def test_time_limit_stops_a_hanging_fetch(self):
        slow = os.path.join(self.tmp, "slowgit.sh")
        real = shutil.which("git")
        with open(slow, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\ncase " $* " in *" fetch "*) sleep 30;; esac\nexec {real} "$@"\n')
        os.chmod(slow, os.stat(slow).st_mode | stat.S_IXUSR)
        scanner = self.scanner(Git(executable=slow))
        started = time.monotonic()
        with mock.patch.object(fetch, "FETCH_TIMEOUT", 1.0):
            result = scanner.fetch(self.proj)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual((result["ok"], result["reason"]), (False, "timeout"))
        self.assertIn("limit czasu", result["message"])


class FetchMonitorTest(unittest.TestCase):
    def test_second_request_for_same_repository_via_symlink_is_busy(self):
        import json
        from types import SimpleNamespace
        from tide_daemon.worktree_monitor import WorktreeMonitor
        from .privatebus import app
        app()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        real = os.path.join(tmp, "real")
        os.mkdir(real)
        link = os.path.join(tmp, "link")
        os.symlink(real, link)
        entered, release = threading.Event(), threading.Event()

        def slow(path):
            entered.set()
            release.wait(5)
            return {"ok": True, "repoPath": path, "remotes": [], "message": "pobrano"}
        monitor = WorktreeMonitor(SimpleNamespace(fetch=slow, stop=lambda: None), lambda: [])
        monitor.refresh = lambda: None
        replies = []
        monitor.fetch(real, lambda data: replies.append(json.loads(data)))
        self.assertTrue(entered.wait(2))
        monitor.fetch(link, lambda data: replies.append(json.loads(data)))
        self.assertEqual([r["reason"] for r in replies], ["busy"])
        release.set()
        deadline = time.monotonic() + 3
        while len(replies) < 2 and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(.01)
        self.assertEqual([r["ok"] for r in replies], [False, True])
        monitor.fetch(link, lambda data: replies.append(json.loads(data)))  # po zakończeniu znów wolno
        self.assertTrue(monitor._fetching)
        release.set()
        monitor._fetch_pool.shutdown(wait=True)


class FetchRequestTest(FetchTestCase):
    def test_repository_under_git_boundary_is_refused_without_fetch(self):
        rec = Rec()
        scanner = self.scanner(rec)
        rec.calls.clear()
        self.push_commits(1)
        before = self.rev("refs/remotes/origin/master")
        with mock.patch.object(fetch, "boundary_active", return_value=True) as active:
            result = scanner.fetch(self.proj)
        active.assert_called()
        self.assertEqual((result["ok"], result["reason"]), (False, "boundary"))
        self.assertIn("pudełk", result["message"])
        self.assertEqual(rec.fetches(), [])
        self.assertEqual(self.rev("refs/remotes/origin/master"), before)

    def test_path_outside_worktrees_list_is_bad_request_without_git(self):
        stranger = self.repo("stranger")
        outside = os.path.join(self.tmp, "outside")
        shutil.copytree(self.proj, outside, symlinks=True)
        link = os.path.join(self.tmp, "link-to-outside")
        os.symlink(outside, link)
        rec = Rec()
        scanner = WorktreeScanner(os.path.join(self.root, "proj"), git=rec)
        scanner.refresh()
        self.assertIn(self.proj, {w.repo_path for w in scanner.items})
        rec.calls.clear()
        for path in (stranger, outside, link, os.path.join(self.proj, "..", "stranger"),
                     os.path.join(self.proj, "..", "..", "outside"), "", "relative/path", self.tmp, "\0"):
            result = scanner.fetch(path)
            self.assertEqual((result["ok"], result["reason"]), (False, "bad-request"), path)
        self.assertEqual(rec.calls, [])

    def test_symlink_to_listed_repository_resolves_to_the_listed_one(self):
        link = os.path.join(self.tmp, "link-to-proj")
        os.symlink(self.proj, link)
        self.assertTrue(self.scanner().fetch(link)["ok"])
        self.assertTrue(self.scanner().fetch(os.path.join(self.proj, "..", "proj"))["ok"])

    def test_fetch_does_not_wait_for_repository_lock_held_by_card_merge(self):
        """M1 (review K-36): fetch zapisuje tylko refs/remotes i FETCH_HEAD — bez blokady silnika (`lock_for`)."""
        scanner = self.scanner()
        held, release = threading.Event(), threading.Event()

        def card_merge():
            with lock_for(self.proj):
                held.set()
                release.wait(30)

        thread = threading.Thread(target=card_merge, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        self.push_commits(1)
        result = scanner.fetch(self.proj)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["newCommits"], 1)

    def test_second_fetch_of_same_repository_is_busy_without_waiting(self):
        scanner = self.scanner()
        lock = fetch.fetch_lock(os.path.join(self.proj, "..", "proj"))
        self.assertTrue(lock.acquire(blocking=False))
        try:
            result = scanner.fetch(self.proj)
        finally:
            lock.release()
        self.assertEqual((result["ok"], result["reason"]), (False, "busy"))
        self.assertTrue(scanner.fetch(self.proj)["ok"], "po zwolnieniu znów wolno")


if __name__ == "__main__":
    unittest.main()
