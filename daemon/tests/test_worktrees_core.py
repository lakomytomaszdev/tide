"""Rdzeń worktrees na prawdziwych, tymczasowych repozytoriach (gitrepos.py).

Sprawdzamy to, co widać przez List() i Remove(): stany, flagi, powody pominięcia, a przy
sprzątaniu — co naprawdę zniknęło z dysku i z gałęzi. Git jest owinięty skryptem, który
zapisuje każde wywołanie, żeby potwierdzić brak --force, -D i operacji sieciowych.
"""
import json
import os
import shutil
import stat
import tempfile
import unittest

from . import gitrepos
from tide_daemon.processes import ProcessInfo, ProcessTable
from tide_daemon.worktrees import Launchers, WorktreeScanner, agent_cwds, find_repositories, scan
from tide_daemon.worktrees.git import Git, parse_status, pick_main_branch
from tide_daemon.worktrees.disk import disk_usage

LOGGING_GIT = """#!/bin/bash
printf '%s\\n' "$*" >> "{log}"
exec git "$@"
"""


def by_path(items):
    return {w["path"]: w for w in items}


@unittest.skipUnless(shutil.which("git"), "brak gita")
class WorktreeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wt-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "work")
        self.p = gitrepos.build(self.root)
        self.log = os.path.join(self.tmp, "git.log")
        wrapper = os.path.join(self.tmp, "git-logged")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(LOGGING_GIT.format(log=self.log))
        os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IXUSR)
        self.scanner = WorktreeScanner(self.root, git=Git(wrapper))
        self.agent = [os.path.join(self.p["agent"], "src")]

    def listed(self, cwds=None):
        self.scanner.refresh()
        self.scanner.measure_stale()
        return by_path(self.scanner.snapshot(self.agent if cwds is None else cwds))

    def git_calls(self) -> list[str]:
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read().splitlines()
        except OSError:
            return []


class DiscoveryTest(WorktreeTestCase):
    def test_finds_repositories_but_not_dependencies_or_hidden(self):
        self.assertEqual(find_repositories(self.root), [self.p["alpha"], self.p["beta"]])

    def test_depth_limit(self):
        self.assertEqual(find_repositories(self.root, max_depth=1), [self.p["alpha"]])

    def test_lists_every_worktree_of_every_repository(self):
        items = self.listed()
        self.assertEqual(set(items), {self.p[k] for k in ("alpha", "merged", "dirty", "unmerged", "agent",
                                                         "detached", "beta", "topic")})
        order = [w["path"] for w in self.scanner.snapshot()]
        self.assertEqual(order[0], self.p["alpha"], "główny katalog pierwszy w grupie repozytorium")
        self.assertEqual(order.index(self.p["beta"]), 6)


class ListTest(WorktreeTestCase):
    def setUp(self):
        super().setUp()
        self.items = self.listed()

    def test_main_worktree_is_flagged_and_never_removable(self):
        w = self.items[self.p["alpha"]]
        self.assertEqual((w["repo"], w["isMain"], w["branch"], w["mainBranch"]), ("alpha", True, "master", "master"))
        self.assertEqual((w["removable"], w["blocker"]), (False, "main"))

    def test_merged_clean_worktree_is_removable(self):
        w = self.items[self.p["merged"]]
        self.assertEqual((w["repo"], w["name"], w["branch"]), ("alpha", "merged", "feat/merged"))
        self.assertEqual((w["merged"], w["unmerged"], w["dirty"], w["hasAgent"]), (True, 0, 0, False))
        self.assertEqual((w["removable"], w["blocker"]), (True, ""))
        self.assertEqual(w["repoPath"], self.p["alpha"])

    def test_dirty_worktree_counts_changes(self):
        w = self.items[self.p["dirty"]]
        self.assertEqual((w["dirty"], w["merged"], w["removable"], w["blocker"]), (1, True, False, "dirty"))

    def test_unmerged_commits_are_counted(self):
        w = self.items[self.p["unmerged"]]
        self.assertEqual((w["merged"], w["unmerged"], w["blocker"]), (False, 2, "unmerged"))

    def test_active_agent_in_subdirectory(self):
        w = self.items[self.p["agent"]]
        self.assertEqual((w["hasAgent"], w["blocker"]), (True, "agent"))
        self.assertFalse(self.items[self.p["alpha"]]["hasAgent"], "agent liczy się tylko w najgłębszym worktree")

    def test_detached_head(self):
        w = self.items[self.p["detached"]]
        self.assertEqual((w["branch"], w["detached"], w["merged"], w["removable"]), ("", True, True, True))
        self.assertEqual(len(w["head"]), 7)

    def test_repository_without_main_branch(self):
        w = self.items[self.p["topic"]]
        self.assertEqual((w["repo"], w["mainBranch"], w["merged"], w["unmerged"]), ("beta", "", False, None))
        self.assertEqual(w["blocker"], "no-main")

    def test_last_change_is_newest_of_commit_and_changed_files(self):
        self.assertEqual(self.items[self.p["merged"]]["lastChangeTs"], gitrepos.COMMIT_TS)
        newer = gitrepos.COMMIT_TS + 3600
        os.utime(os.path.join(self.p["dirty"], "README.md"), (newer, newer))
        self.assertEqual(self.listed()[self.p["dirty"]]["lastChangeTs"], newer)

    def test_size_like_du_without_nested_worktrees(self):
        w = self.items[self.p["merged"]]
        self.assertEqual(w["size"], disk_usage(self.p["merged"]))
        self.assertGreater(w["size"], 0)
        # Worktree wewnątrz głównego katalogu nie jest wliczany do jego rozmiaru.
        nested = os.path.join(self.p["alpha"], "inner")
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "inner", nested)
        with open(os.path.join(nested, "big.bin"), "wb") as fh:
            fh.write(b"\1" * 1_000_000)
        before = self.items[self.p["alpha"]]["size"]
        self.scanner.size_ttl = 0
        after = self.listed()
        self.assertLess(after[self.p["alpha"]]["size"] - before, 500_000)
        self.assertGreater(after[nested]["size"], 1_000_000)

    def test_agent_process_without_session_counts(self):
        procs = ProcessTable([ProcessInfo(10, 1, "codex", cwd=self.p["unmerged"], tool="codex")])
        sessions = [{"cwd": self.p["merged"], "state": "ended"}, {"cwd": self.p["dirty"], "state": "idle"}]
        cwds = agent_cwds(sessions, procs)
        self.assertEqual(cwds, sorted([self.p["unmerged"], self.p["dirty"]]))
        items = by_path(self.scanner.snapshot(cwds))
        self.assertFalse(items[self.p["merged"]]["hasAgent"], "zakończona sesja nie blokuje")
        self.assertTrue(items[self.p["unmerged"]]["hasAgent"])

    def test_no_network_and_no_writes_while_listing(self):
        calls = self.git_calls()
        self.assertTrue(calls)
        for call in calls:
            self.assertNotRegex(call, r"\b(fetch|pull|push|remote|ls-remote|clone|gc)\b")
            self.assertNotRegex(call, r"^-C \S+ (worktree (remove|prune|add)|branch -|commit|checkout|reset)")

    def test_json_round_trip(self):
        self.assertEqual(json.loads(json.dumps(self.scanner.snapshot(self.agent))), self.scanner.snapshot(self.agent))


class RemoveTest(WorktreeTestCase):
    def setUp(self):
        super().setUp()
        self.listed()
        open(self.log, "w").close()

    def test_removes_only_safe_worktrees_and_reports_each(self):
        ask = [self.p[k] for k in ("merged", "dirty", "unmerged", "agent", "alpha", "detached", "topic")]
        ask.append(os.path.join(self.root, "nie-ma"))
        results = self.scanner.remove(ask, self.agent)
        self.assertEqual([r["path"] for r in results], ask)
        got = {os.path.basename(r["path"]): (r["removed"], r["reason"]) for r in results}
        self.assertEqual(got, {
            "merged": (True, ""),
            "dirty": (False, "dirty"),
            "unmerged": (False, "unmerged"),
            "agent": (False, "agent"),
            "alpha": (False, "main"),
            "detached": (True, ""),
            "topic": (False, "no-main"),
            "nie-ma": (False, "unknown"),
        })
        for r in results:
            self.assertTrue(r["message"], r)

        # Na dysku: zniknęły tylko dwa bezpieczne katalogi.
        self.assertFalse(os.path.exists(self.p["merged"]))
        self.assertFalse(os.path.exists(self.p["detached"]))
        for kept in ("dirty", "unmerged", "agent", "alpha", "topic"):
            self.assertTrue(os.path.isdir(self.p[kept]), kept)
        with open(os.path.join(self.p["dirty"], "README.md"), encoding="utf-8") as fh:
            self.assertIn("zmiana", fh.read())

        # Gałąź scalona usunięta bezpiecznie, pozostałe zostają.
        merged = results[0]
        self.assertEqual((merged["branch"], merged["branchDeleted"]), ("feat/merged", True))
        self.assertGreater(merged["freed"], 0)
        self.assertEqual(sorted(gitrepos.branches(self.p["alpha"])),
                         ["feat/agent", "feat/dirty", "feat/unmerged", "master"])
        self.assertNotIn(self.p["merged"], by_path(self.scanner.snapshot()))

    def test_never_forces(self):
        self.scanner.remove([self.p[k] for k in ("merged", "dirty", "unmerged", "detached")], self.agent)
        destructive = [c for c in self.git_calls() if " worktree remove" in c or " branch " in c]
        self.assertEqual(sorted(destructive), sorted([
            f"-C {self.p['alpha']} worktree remove {self.p['merged']}",
            f"-C {self.p['alpha']} branch -d feat/merged",
            f"-C {self.p['alpha']} worktree remove {self.p['detached']}",
        ]))
        for call in self.git_calls():
            self.assertNotRegex(call, r"(--force|\s-f\b|\s-D\b|\s-ff?\b)")

    def test_decision_uses_fresh_state(self):
        # Między odświeżeniem a sprzątaniem w scalonym worktree pojawił się nowy plik.
        with open(os.path.join(self.p["merged"], "nowy.txt"), "w", encoding="utf-8") as fh:
            fh.write("praca w toku\n")
        result = self.scanner.remove([self.p["merged"]], [])[0]
        self.assertEqual((result["removed"], result["reason"]), (False, "dirty"))
        self.assertTrue(os.path.exists(os.path.join(self.p["merged"], "nowy.txt")))

    def test_branch_kept_when_git_refuses_safe_delete(self):
        # Główny katalog stoi na innej gałęzi niż master: git branch -d porównuje z HEAD i odmawia.
        gitrepos.git(self.p["alpha"], "checkout", "-q", "-b", "side", "master~1")
        self.listed()
        result = self.scanner.remove([self.p["merged"]], [])[0]
        self.assertTrue(result["removed"])
        self.assertFalse(result["branchDeleted"])
        self.assertIn("gałąź zostaje", result["branchMessage"])
        self.assertIn("feat/merged", gitrepos.branches(self.p["alpha"]))

    def test_locked_worktree_is_skipped(self):
        gitrepos.git(self.p["alpha"], "worktree", "lock", self.p["merged"])
        self.listed()
        result = self.scanner.remove([self.p["merged"]], [])[0]
        self.assertEqual((result["removed"], result["reason"]), (False, "locked"))
        self.assertTrue(os.path.isdir(self.p["merged"]))

    def test_one_shot_scan_matches_scanner(self):
        self.assertEqual(scan(self.root, self.agent), self.scanner.snapshot(self.agent))


class HelpersTest(unittest.TestCase):
    def test_main_branch_choice(self):
        self.assertEqual(pick_main_branch({"master", "develop"}, "develop"), "develop")
        self.assertEqual(pick_main_branch({"master", "develop"}, "feature"), "master")
        self.assertEqual(pick_main_branch({"main", "develop"}, ""), "main")
        self.assertEqual(pick_main_branch({"trunk"}, "trunk"), "")

    def test_status_parsing_with_renames(self):
        text = " M a.txt\0R  new.txt\0old.txt\0?? dir/\0"
        self.assertEqual(parse_status(text), ["a.txt", "new.txt", "dir/"])

    def test_launchers_find_rider_in_toolbox_scripts(self):
        tmp = tempfile.mkdtemp(prefix="tide-launch-")
        self.addCleanup(shutil.rmtree, tmp)
        scripts = os.path.join(tmp, ".local/share/JetBrains/Toolbox/scripts")
        os.makedirs(scripts)
        rider = os.path.join(scripts, "rider")
        with open(rider, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(rider, 0o755)
        found = Launchers(env={"HOME": tmp, "PATH": ""}, path="")
        self.assertEqual(found.available(), {"terminal": False, "rider": True})
        self.assertEqual(found.command("rider", "/x"), [rider, "/x"])
        self.assertEqual(found.command("terminal", "/x"), [])
        missing = Launchers(env={"HOME": os.path.join(tmp, "nikt"), "PATH": ""}, path="")
        self.assertEqual(missing.available()["rider"], False)

    def test_terminal_command_uses_workdir_in_own_scope(self):
        launchers = Launchers(env={"HOME": "/nonexistent", "PATH": "/usr/bin:/bin"})
        if not launchers.terminal:
            self.skipTest("brak konsole")
        argv = launchers.command("terminal", "/srv/x")
        self.assertEqual(argv[-3:], [launchers.terminal, "--workdir", "/srv/x"])
        if launchers.scope:
            self.assertEqual(argv[:4], [launchers.scope, "--user", "--scope", "--collect"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("git"), "brak gita")
class ProtectedBranchTest(unittest.TestCase):
    """Przypadek z LogViewer: główny katalog stoi na gałęzi funkcji, a master jest
    wybrany w osobnym worktree. Master jest „scalony” sam ze sobą, ale nie wolno ani usunąć
    jego worktree, ani skasować gałęzi."""

    def setUp(self):
        import subprocess
        self.tmp = tempfile.mkdtemp(prefix="tide-wt-protected-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "work")
        self.repo = os.path.join(self.root, "app")
        self.master_wt = os.path.join(self.root, "app-worktrees", "master-local")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

        def git(*args, cwd=None):
            subprocess.run(["git", *args], cwd=cwd or self.repo, env=env, check=True, capture_output=True)

        os.makedirs(self.repo)
        git("init", "-q", "-b", "master")
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("a")
        git("add", "a.txt")
        git("commit", "-q", "-m", "init")
        git("checkout", "-q", "-b", "feat/summary")
        git("commit", "-q", "--allow-empty", "-m", "feature")
        git("worktree", "add", "-q", self.master_wt, "master")
        self.git = git
        self.scanner = WorktreeScanner(self.root)

    def test_main_branch_worktree_is_protected(self):
        self.scanner.refresh()
        w = by_path(self.scanner.snapshot([]))[self.master_wt]
        self.assertEqual(w["branch"], "master")
        self.assertEqual((w["removable"], w["blocker"]), (False, "protected"))

    def test_remove_keeps_main_branch_worktree_and_branch(self):
        import subprocess
        self.scanner.refresh()
        [result] = self.scanner.remove([self.master_wt])
        self.assertEqual((result["removed"], result["reason"]), (False, "protected"))
        self.assertTrue(os.path.isdir(self.master_wt))
        branches = subprocess.run(["git", "branch", "--list", "master"], cwd=self.repo,
                                  capture_output=True, text=True, check=True).stdout
        self.assertIn("master", branches)
