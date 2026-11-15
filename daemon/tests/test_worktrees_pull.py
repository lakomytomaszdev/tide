"""Worktrees: „Zaciągnij” wyłącznie fast-forward, odmowy i wspólna blokada ze scalaniem karty (PT-353).

Prawdziwe, tymczasowe repozytoria z lokalnym gołym „zdalnym” (bez sieci). Każda odmowa sprawdza kod, niezmieniony
HEAD i niezmienione drzewo (`git status`). Testy współbieżności wymuszają przeplot zdarzeniami między wątkami
(`threading.Event`), bez polegania na `sleep`: drugi wątek czeka na blokadę repozytorium (`cardflow.gitops.lock_for`)
i żaden nie widzi stanu pośredniego drugiego. Do tego M1 z review K-36: „Pobierz” nie trzyma blokady silnika.
"""
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from . import gitrepos
from . import test_worktrees_divergence as base
from tide_daemon.cardflow.gitops import lock_for
from tide_daemon.cardflow.journal import Journal
from tide_daemon.worktrees import WorktreeScanner, fetch, pull
from tide_daemon.worktrees.git import Git

git, commit = gitrepos.git, gitrepos.commit
WAIT = 30  # s — tylko zabezpieczenie przed zawieszeniem testu, nie element przeplotu

# Podpolecenia gita, które „Zaciągnij” może uruchomić: odczyty i jedno `merge --ff-only`.
READ_ONLY = {"symbolic-ref", "rev-parse", "status", "rev-list", "config", "diff", "check-attr"}


class Hooked(Git):
    """Systemowy git z zapisem argv i zaczepem przed wybranym podpoleceniem (do wymuszania przeplotu)."""

    def __init__(self, executable="git"):
        super().__init__(executable=executable)
        self.calls = []
        self.hooks = {}  # podpolecenie → funkcja() wołana tuż przed nim (raz)

    def run(self, cwd, *args, **kwargs):
        self.calls.append(args)
        command = next((a for a in args if not a.startswith("-") and "=" not in a), "")
        hook = self.hooks.pop(command, None)
        if hook:
            hook()
        return super().run(cwd, *args, **kwargs)

    def commands(self):
        out = []
        for args in self.calls:
            rest = list(args)
            while rest and rest[0] == "-c":
                rest = rest[2:]
            out.append(rest[0] if rest else "")
        return out


@unittest.skipUnless(shutil.which("git"), "brak gita")
class PullTestCase(base.DivergenceTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.repo("proj")
        self.bare = self.add_remote(self.proj)
        git(self.proj, "push", "-q", "-u", "origin", "master")
        self.wt = self.worktree(self.proj, "feat/sync")
        git(self.wt, "push", "-q", "-u", "origin", "feat/sync")
        self.other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", self.bare, self.other)

    def scanner(self, git_impl=None, journal=None):
        scanner = WorktreeScanner(self.root, git=git_impl or Git())
        if journal is not None:
            scanner.journal_source = lambda: journal
        scanner.refresh()
        return scanner

    def upstream_moves(self, n, branch="master", files=None):
        """n nowych commitów na zdalnej gałęzi i `fetch` w projekcie (lokalnie, bez sieci)."""
        git(self.other, "fetch", "-q", "origin")
        git(self.other, "checkout", "-q", "-B", branch, f"origin/{branch}")
        for i in range(n):
            name = (files or [])[i] if files and i < len(files) else f"up-{branch.replace('/', '-')}-{i}-{time.monotonic_ns()}.txt"
            commit(self.other, name)
        git(self.other, "push", "-q", "origin", f"HEAD:{branch}")
        git(self.proj, "fetch", "-q", "origin")
        return self.rev(f"refs/remotes/origin/{branch}")

    def rev(self, ref, cwd=None):
        return git(cwd or self.proj, "rev-parse", ref).strip()

    def tree(self, cwd):
        """HEAD i pełny status — „niezmienione HEAD i drzewo”."""
        return self.rev("HEAD", cwd), git(cwd, "status", "--porcelain=v1", "--untracked-files=all")

    def assert_refused(self, result, reason, cwd, before):
        self.assertEqual((result["ok"], result["reason"]), (False, reason), result)
        self.assertTrue(result["message"])
        self.assertEqual(self.tree(cwd), before, "odmowa nie może zmienić HEAD ani drzewa")


class PullResultTest(PullTestCase):
    def test_main_branch_in_main_directory_fast_forwards_to_upstream(self):
        target = self.upstream_moves(2)
        rec = Hooked()
        result = self.scanner(rec).pull(self.proj)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.rev("HEAD"), target)
        self.assertEqual(git(self.proj, "status", "--porcelain"), "")
        self.assertEqual((result["branch"], result["upstream"], result["commits"], result["to"]),
                         ("master", "origin/master", 2, target))
        self.assertIn("zaciągnięto 2 commity z origin/master", result["message"])
        merges = [c for c in rec.calls if "merge" in c]
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0][-4:], ("--ff-only", "--no-autostash", "--no-stat", target))

    def test_linked_worktree_fast_forwards_its_branch(self):
        target = self.upstream_moves(1, "feat/sync")
        result = self.scanner().pull(self.wt)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.rev("HEAD", self.wt), target)
        self.assertEqual(self.rev("refs/heads/master"), self.rev("refs/remotes/origin/master"), "inna gałąź bez zmian")
        self.assertEqual(result["message"], "zaciągnięto 1 commit z origin/feat/sync (fast-forward)")

    def test_only_read_commands_and_one_ff_only_merge(self):
        self.upstream_moves(1)
        rec = Hooked()
        self.assertTrue(self.scanner(rec).pull(self.proj)["ok"])
        pulled = rec.commands()[rec.commands().index("symbolic-ref"):]
        self.assertTrue(set(pulled) <= READ_ONLY | {"merge"}, pulled)
        flat = " ".join(a for c in rec.calls for a in c)
        for banned in ("reset", "rebase", "stash", "push", "prune", "--force", "pull", "fetch", "checkout"):
            self.assertNotIn(f" {banned} ", f" {flat} ")
        self.assertIn("merge.autoStash=false", flat)


class PullRefusalTest(PullTestCase):
    def test_no_upstream(self):
        local = self.worktree(self.proj, "feat/local")
        before = self.tree(local)
        self.assert_refused(self.scanner().pull(local), "no-upstream", local, before)

    def test_detached_head_is_no_upstream(self):
        det = os.path.join(self.tmp, "det")
        git(self.proj, "worktree", "add", "-q", "--detach", det)
        before = self.tree(det)
        self.assert_refused(self.scanner().pull(det), "no-upstream", det, before)

    def test_dirty(self):
        self.upstream_moves(1)
        with open(os.path.join(self.proj, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("lokalna zmiana\n")
        before = self.tree(self.proj)
        self.assert_refused(self.scanner().pull(self.proj), "dirty", self.proj, before)

    def test_untracked_file_is_dirty(self):
        self.upstream_moves(1)
        with open(os.path.join(self.proj, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        before = self.tree(self.proj)
        self.assert_refused(self.scanner().pull(self.proj), "dirty", self.proj, before)

    def test_agent_and_card_place(self):
        self.upstream_moves(1, "feat/sync")
        before = self.tree(self.wt)
        scanner = self.scanner()
        agent = scanner.pull(self.wt, agents=[os.path.join(self.wt, "src")])
        self.assert_refused(agent, "agent", self.wt, before)
        self.assertIn("agent", agent["message"])
        card = scanner.pull(self.wt, cards=[self.wt])
        self.assert_refused(card, "agent", self.wt, before)
        self.assertIn("karty", card["message"])

    def test_diverged(self):
        self.upstream_moves(1)
        commit(self.proj, "local.txt")
        before = self.tree(self.proj)
        result = self.scanner().pull(self.proj)
        self.assert_refused(result, "diverged", self.proj, before)
        self.assertEqual((result["ahead"], result["behind"]), (1, 1))
        self.assertIn("nie scala", result["message"])

    def test_up_to_date(self):
        before = self.tree(self.proj)
        self.assert_refused(self.scanner().pull(self.proj), "up-to-date", self.proj, before)

    def test_ahead_only_is_up_to_date(self):
        commit(self.proj, "local.txt")
        before = self.tree(self.proj)
        self.assert_refused(self.scanner().pull(self.proj), "up-to-date", self.proj, before)

    def test_git_operation_in_progress(self):
        self.upstream_moves(1, "feat/sync")
        gitdir = git(self.wt, "rev-parse", "--absolute-git-dir").strip()
        for marker in ("MERGE_HEAD", "index.lock", "rebase-merge"):
            path = os.path.join(gitdir, marker)
            if marker == "rebase-merge":
                os.mkdir(path)
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.rev("HEAD", self.wt) + "\n" if marker == "MERGE_HEAD" else "")
            before = self.rev("HEAD", self.wt)
            result = self.scanner().pull(self.wt)
            self.assertEqual((result["ok"], result["reason"]), (False, "git-operation"), marker)
            self.assertEqual(self.rev("HEAD", self.wt), before)
            (os.rmdir if marker == "rebase-merge" else os.remove)(path)

    def test_locked_when_card_merge_holds_repository_lock(self):
        self.upstream_moves(1)
        scanner = self.scanner()
        held, release = threading.Event(), threading.Event()

        def card_merge():
            with lock_for(self.proj):
                held.set()
                release.wait(WAIT)

        thread = threading.Thread(target=card_merge, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(WAIT))
        before = self.tree(self.proj)
        with mock.patch.object(pull, "LOCK_WAIT", 0.3):
            result = scanner.pull(self.proj)
        self.assert_refused(result, "locked", self.proj, before)

    def test_boundary_refused_without_git(self):
        self.upstream_moves(1)
        rec = Hooked()
        scanner = self.scanner(rec)
        rec.calls.clear()
        before = self.tree(self.proj)
        with mock.patch.object(pull, "boundary_active", return_value=True):
            result = scanner.pull(self.proj)
        self.assert_refused(result, "boundary", self.proj, before)
        self.assertEqual(rec.calls, [])


class CardStepTest(PullTestCase):
    """`card-step` z prawdziwego dziennika kart (Journal.cards/state — istniejące odczyty)."""

    def journal(self):
        journal = Journal(os.path.join(self.tmp, "board"), mirror=os.path.join(self.tmp, "mirror"), clock=lambda: 1000)
        journal.append("K-7", "card.started", baseRef="refs/heads/master", baseCommit=self.rev("master"))
        return journal

    def test_ticket_branch_in_flow_is_refused(self):
        journal = self.journal()
        journal.append("K-7", "intent", action="ticket-branch", key="PT-1", repo=self.proj, branch="feat/sync",
                       worktree=self.wt, ticket="PT-1")
        journal.append("K-7", "result", action="ticket-branch", key="PT-1", ok=True, branch="feat/sync",
                       worktree=self.wt, ticket="PT-1")
        self.upstream_moves(1, "feat/sync")
        scanner = self.scanner(journal=journal)
        before = self.tree(self.wt)
        result = scanner.pull(self.wt)
        self.assert_refused(result, "card-step", self.wt, before)
        self.assertIn("zarządza Tide", result["message"])
        self.assertIn("K-7", result["message"])
        listed = {w["path"]: w for w in scanner.snapshot([])}
        self.assertTrue(listed[self.wt]["flowBranch"])
        self.assertFalse(listed[self.proj]["flowBranch"])

    def test_card_branch_intent_without_result_is_refused(self):
        journal = self.journal()
        journal.append("K-7", "intent", action="card-branch", key="card", repo=self.proj, branch="feat/sync",
                       worktree=self.wt)
        self.upstream_moves(1, "feat/sync")
        before = self.tree(self.wt)
        self.assert_refused(self.scanner(journal=journal).pull(self.wt), "card-step", self.wt, before)

    def test_finished_card_no_longer_manages_branch(self):
        journal = self.journal()
        journal.append("K-7", "intent", action="ticket-branch", key="PT-1", repo=self.proj, branch="feat/sync")
        journal.append("K-7", "card.finished")
        target = self.upstream_moves(1, "feat/sync")
        self.assertTrue(self.scanner(journal=journal).pull(self.wt)["ok"])
        self.assertEqual(self.rev("HEAD", self.wt), target)

    def test_same_branch_name_in_other_repository_does_not_count(self):
        journal = self.journal()
        journal.append("K-7", "intent", action="ticket-branch", key="PT-1", repo=os.path.join(self.tmp, "elsewhere"),
                       branch="feat/sync")
        self.upstream_moves(1, "feat/sync")
        self.assertTrue(self.scanner(journal=journal).pull(self.wt)["ok"])

    def test_unreadable_journal_is_refused(self):
        broken = SimpleNamespace(cards=lambda: ["K-1"], state=mock.Mock(side_effect=OSError("brak dostępu")))
        self.upstream_moves(1)
        scanner = self.scanner()
        scanner.journal_source = lambda: broken
        before = self.tree(self.proj)
        self.assert_refused(scanner.pull(self.proj), "card-step", self.proj, before)


class PathTest(PullTestCase):
    def test_path_outside_worktrees_list_is_bad_request_without_git(self):
        stranger = self.repo("stranger")
        outside = os.path.join(self.tmp, "outside")
        shutil.copytree(self.proj, outside, symlinks=True)
        link = os.path.join(self.tmp, "link-to-outside")
        os.symlink(outside, link)
        rec = Hooked()
        scanner = WorktreeScanner(os.path.join(self.root, "proj"), git=rec)
        scanner.refresh()
        rec.calls.clear()
        for path in (stranger, outside, link, os.path.join(self.proj, "..", "stranger"),
                     os.path.join(self.wt, "..", "..", "..", "outside"), "", "relative/path", self.tmp, "\0", None, 7):
            result = scanner.pull(path)
            self.assertEqual((result["ok"], result["reason"]), (False, "bad-request"), path)
        self.assertEqual(rec.calls, [])

    def test_symlink_and_dotdot_to_listed_worktree_use_listed_path(self):
        link = os.path.join(self.tmp, "link-to-wt")
        os.symlink(self.wt, link)
        target = self.upstream_moves(1, "feat/sync")
        result = self.scanner().pull(link)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path"], self.wt)
        self.assertEqual(self.rev("HEAD", self.wt), target)
        self.upstream_moves(1)
        dotted = os.path.join(self.proj, "..", "proj")
        self.assertEqual(self.scanner().pull(dotted)["path"], self.proj)


class FreshStateTest(PullTestCase):
    def test_worktree_dirtied_after_listing_is_refused(self):
        self.upstream_moves(1)
        scanner = self.scanner()
        self.assertEqual({w["path"]: w for w in scanner.snapshot([])}[self.proj]["dirty"], 0, "lista: czysty")
        with open(os.path.join(self.proj, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("zmiana po odczycie listy\n")
        before = self.tree(self.proj)
        self.assert_refused(scanner.pull(self.proj), "dirty", self.proj, before)

    def test_state_is_read_under_lock_right_before_merge(self):
        """Zmiana w katalogu w chwili, gdy blokada jest już wzięta (pierwsze polecenie gita pod nią): odczyt stanu
        idzie po niej, więc odmowa `dirty` i bez `merge`."""
        self.upstream_moves(1)
        rec = Hooked()
        scanner = self.scanner(rec)
        held = []

        def dirty_now():
            # Z innego wątku: blokada (RLock) jest już wzięta przez pull.
            probe = threading.Thread(target=lambda: held.append(lock_for(self.proj).acquire(blocking=False)))
            probe.start()
            probe.join(WAIT)
            with open(os.path.join(self.proj, "README.md"), "a", encoding="utf-8") as fh:
                fh.write("zmiana pod blokadą\n")
        rec.hooks["symbolic-ref"] = dirty_now
        before_head = self.rev("HEAD")
        result = scanner.pull(self.proj)
        self.assertEqual(held, [False], "zmiana nastąpiła, gdy pull trzymał blokadę")
        self.assertEqual((result["ok"], result["reason"]), (False, "dirty"), result)
        self.assertEqual(self.rev("HEAD"), before_head)
        self.assertNotIn("merge", rec.commands())


class FilterTest(PullTestCase):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.other, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write("*.bin filter=fake\n")
        git(self.other, "add", ".gitattributes")
        git(self.other, "commit", "-q", "-m", "attrs")
        git(self.other, "push", "-q", "origin", "HEAD:master")
        git(self.proj, "pull", "-q", "--ff-only")
        git(self.proj, "config", "filter.fake.smudge", "cat")
        git(self.proj, "config", "filter.fake.clean", "cat")

    def test_change_through_smudge_filter_is_refused(self):
        self.upstream_moves(1, files=["data.bin"])
        before = self.tree(self.proj)
        result = self.scanner().pull(self.proj)
        self.assert_refused(result, "git-operation", self.proj, before)
        self.assertIn("fake", result["message"])

    def test_change_of_gitattributes_with_driver_is_refused(self):
        with open(os.path.join(self.other, ".gitattributes"), "a", encoding="utf-8") as fh:
            fh.write("*.dat filter=fake\n")
        git(self.other, "commit", "-q", "-am", "more attrs")
        git(self.other, "push", "-q", "origin", "HEAD:master")
        git(self.proj, "fetch", "-q", "origin")
        before = self.tree(self.proj)
        self.assert_refused(self.scanner().pull(self.proj), "git-operation", self.proj, before)

    def test_change_outside_filtered_files_is_pulled(self):
        target = self.upstream_moves(1, files=["plain.txt"])
        self.assertTrue(self.scanner().pull(self.proj)["ok"])
        self.assertEqual(self.rev("HEAD"), target)


class LockReleaseTest(PullTestCase):
    def test_timeout_releases_lock_and_reports_state(self):
        slow = os.path.join(self.tmp, "slowgit.sh")
        real = shutil.which("git")
        with open(slow, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\ncase " $* " in *" merge "*) sleep 30;; esac\nexec {real} "$@"\n')
        os.chmod(slow, os.stat(slow).st_mode | stat.S_IXUSR)
        self.upstream_moves(1)
        scanner = self.scanner(Git(executable=slow))
        before = self.rev("HEAD")
        started = time.monotonic()
        with mock.patch.object(pull, "PULL_TIMEOUT", 1.0):
            result = scanner.pull(self.proj)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual((result["ok"], result["reason"]), (False, "timeout"), result)
        self.assertIn("limit czasu", result["message"])
        self.assertIn("HEAD bez zmian", result["message"])
        self.assertEqual(self.rev("HEAD"), before)
        lock = lock_for(self.proj)
        self.assertTrue(lock.acquire(blocking=False), "blokada zwolniona po przekroczeniu czasu")
        lock.release()

    def test_exception_releases_lock(self):
        self.upstream_moves(1)
        scanner = self.scanner()
        with mock.patch.object(pull, "_pull_locked", side_effect=RuntimeError("awaria")):
            with self.assertRaises(RuntimeError):
                scanner.pull(self.proj)
        lock = lock_for(self.proj)
        self.assertTrue(lock.acquire(blocking=False), "blokada zwolniona po wyjątku")
        lock.release()


class ConcurrencyTest(PullTestCase):
    """Pull i scalanie karty w tym samym repozytorium: jedna blokada, przeplot wymuszony zdarzeniami."""

    def test_pull_waits_for_card_merge_and_does_not_see_its_intermediate_state(self):
        target = self.upstream_moves(1)
        scanner = self.scanner()
        held, waiting, release, done = (threading.Event() for _ in range(4))
        scratch = os.path.join(self.proj, "scalanie-w-toku.tmp")
        results = []

        def card_merge():
            with lock_for(self.proj):
                with open(scratch, "w", encoding="utf-8") as fh:  # stan pośredni: katalog „brudny”
                    fh.write("x\n")
                held.set()
                release.wait(WAIT)
                os.remove(scratch)  # koniec operacji: katalog znów czysty

        real_acquire = pull._acquire

        def acquire(lock, deadline, cancel):
            waiting.set()
            return real_acquire(lock, deadline, cancel)

        def puller():
            results.append(scanner.pull(self.proj))
            done.set()

        merger = threading.Thread(target=card_merge, daemon=True)
        merger.start()
        self.assertTrue(held.wait(WAIT))
        with mock.patch.object(pull, "_acquire", acquire):
            thread = threading.Thread(target=puller, daemon=True)
            thread.start()
            self.assertTrue(waiting.wait(WAIT), "pull doszedł do blokady")
            self.assertFalse(done.is_set(), "pull nie może skończyć, póki scalanie trzyma blokadę")
            release.set()
            self.assertTrue(done.wait(WAIT))
        merger.join(WAIT)
        thread.join(WAIT)
        self.assertTrue(results[0]["ok"], results)
        self.assertEqual(self.rev("HEAD"), target)

    def test_card_merge_waits_for_pull_and_sees_final_head(self):
        target = self.upstream_moves(1)
        before = self.rev("HEAD")
        rec = Hooked()
        scanner = self.scanner(rec)
        in_merge, go, merged_seen = threading.Event(), threading.Event(), []
        attempt, card_done = threading.Event(), threading.Event()

        def hold_in_merge():
            in_merge.set()
            go.wait(WAIT)
        rec.hooks["merge"] = hold_in_merge
        results = []
        puller = threading.Thread(target=lambda: results.append(scanner.pull(self.proj)), daemon=True)
        puller.start()
        self.assertTrue(in_merge.wait(WAIT))
        self.assertFalse(lock_for(self.proj).acquire(blocking=False), "pull trzyma blokadę na czas merge")
        self.assertEqual(self.rev("HEAD"), before)

        def card_merge():
            attempt.set()
            with lock_for(self.proj):
                merged_seen.append(self.rev("HEAD"))
            card_done.set()

        card = threading.Thread(target=card_merge, daemon=True)
        card.start()
        self.assertTrue(attempt.wait(WAIT))
        self.assertFalse(card_done.is_set(), "scalanie karty czeka na blokadę")
        go.set()
        puller.join(WAIT)
        self.assertTrue(card_done.wait(WAIT))
        card.join(WAIT)
        self.assertTrue(results[0]["ok"], results)
        self.assertEqual(merged_seen, [target], "scalanie karty widzi stan po pull, nie w trakcie")


class FetchWithoutEngineLockTest(PullTestCase):
    """M1 (review K-36): „Pobierz” nie trzyma `lock_for` przez transfer sieciowy; jedno pobieranie na repozytorium."""

    def test_engine_lock_is_free_while_fetch_transfers(self):
        commit(self.other, "new.txt")
        git(self.other, "push", "-q", "origin", "HEAD:master")
        rec = Hooked()
        scanner = self.scanner(rec)
        in_fetch, go, results = threading.Event(), threading.Event(), []

        def hold_in_fetch():
            in_fetch.set()
            go.wait(WAIT)
        rec.hooks["fetch"] = hold_in_fetch
        thread = threading.Thread(target=lambda: results.append(scanner.fetch(self.proj)), daemon=True)
        thread.start()
        self.assertTrue(in_fetch.wait(WAIT))
        lock = lock_for(self.proj)
        self.assertTrue(lock.acquire(blocking=False), "silnik nie czeka na fetch")
        lock.release()
        second = scanner.fetch(self.proj)
        self.assertEqual((second["ok"], second["reason"]), (False, "busy"), "jedno pobieranie na repozytorium")
        go.set()
        thread.join(WAIT)
        self.assertTrue(results[0]["ok"], results)

    def test_fetch_proceeds_while_engine_holds_repository_lock(self):
        scanner = self.scanner()
        with lock_for(self.proj):
            result = scanner.fetch(self.proj)
        self.assertTrue(result["ok"], result)


class PullMonitorTest(unittest.TestCase):
    def test_main_thread_does_not_wait_and_second_request_is_locked(self):
        from tide_daemon.worktree_monitor import WorktreeMonitor
        from .privatebus import app
        app()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        real = os.path.join(tmp, "real")
        os.mkdir(real)
        link = os.path.join(tmp, "link")
        os.symlink(real, link)
        entered, release, seen = threading.Event(), threading.Event(), []

        def slow(path, agents, cards):
            seen.append((path, agents, cards))
            entered.set()
            release.wait(WAIT)
            return {"ok": True, "path": path, "message": "zaciągnięto"}
        monitor = WorktreeMonitor(SimpleNamespace(pull=slow, stop=lambda: None), lambda: ["/agent/cwd", "/card/place"],
                                  card_places_source=lambda: ["/card/place"])
        monitor.refresh = lambda: None
        self.addCleanup(monitor._pull_pool.shutdown, wait=True)
        self.addCleanup(release.set)
        replies = []
        monitor.pull(real, lambda data: replies.append(json.loads(data)))
        self.assertEqual(replies, [], "wątek główny wrócił od razu, bez wyniku")
        self.assertTrue(entered.wait(WAIT))
        self.assertEqual(seen[0], (real, ["/agent/cwd", "/card/place"], ["/card/place"]))
        monitor.pull(link, lambda data: replies.append(json.loads(data)))
        self.assertEqual([(r["ok"], r["reason"]) for r in replies], [(False, "locked")])
        monitor.pull("", lambda data: replies.append(json.loads(data)))
        self.assertEqual(replies[-1]["reason"], "bad-request")
        release.set()
        deadline = time.monotonic() + WAIT
        while len(replies) < 3 and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(.01)
        self.assertTrue(replies[-1]["ok"], replies)
        self.assertFalse(monitor._pulling)


class FlowBranchesTest(unittest.TestCase):
    def test_reads_card_and_ticket_branches_of_running_cards_only(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        journal = Journal(os.path.join(tmp, "board"), mirror=os.path.join(tmp, "mirror"), clock=lambda: 1000)
        journal.append("K-1", "card.started", baseRef="refs/heads/master", baseCommit="a" * 40)
        journal.append("K-1", "intent", action="card-branch", key="card", repo="/r/p", branch="card/k-1-x")
        journal.append("K-1", "result", action="card-branch", key="card", ok=True, branch="card/k-1-x")
        journal.append("K-1", "intent", action="ticket-branch", key="PT-9", repo="/r/p", branch="ticket/pt-9-y")
        journal.append("K-2", "card.started", baseRef="refs/heads/master", baseCommit="a" * 40)
        journal.append("K-2", "intent", action="ticket-branch", key="PT-10", repo="/r/p", branch="ticket/pt-10-z")
        journal.append("K-2", "card.finished")
        journal.append("K-3", "intent", action="ticket-branch", key="PT-11", repo="/r/p", branch="ticket/pt-11-q")
        flows = pull.flow_branches(journal)
        self.assertEqual(flows, {(os.path.realpath("/r/p"), "card/k-1-x"): "K-1",
                                 (os.path.realpath("/r/p"), "ticket/pt-9-y"): "K-1"})
        self.assertEqual(pull.flow_card(flows, "/r/p", "ticket/pt-9-y"), "K-1")
        self.assertEqual(pull.flow_card(flows, "/r/other", "ticket/pt-9-y"), "")
        self.assertEqual(pull.flow_card({("", "x"): "K-5"}, "/any", "x"), "K-5", "wpis bez repozytorium pasuje zawsze")
        self.assertEqual(pull.flow_branches(None), {})


if __name__ == "__main__":
    unittest.main()
