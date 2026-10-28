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
from tide_daemon.worktrees.git import (HEAD_RESTARTS, Git, parse_status, pick_main_branch, reflog_has_own_commit,
                                      worktree_gitdir)
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

    def test_reflog_own_commit_evidence(self):
        tmp = tempfile.mkdtemp(prefix="tide-reflog-")
        self.addCleanup(shutil.rmtree, tmp)
        z = "0" * 40

        def log(name, *messages):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"{z} {'1' * 40} t <t@t> 1790000000 +0000\n")  # wpis bez komunikatu
                for message in messages:
                    fh.write(f"{z} {'1' * 40} t <t@t> 1790000000 +0000\t{message}\n")
            return path

        self.assertIsNone(reflog_has_own_commit(os.path.join(tmp, "nie-ma")))
        self.assertFalse(reflog_has_own_commit(log("a", "branch: Created from master")))
        self.assertFalse(reflog_has_own_commit(log("b", "branch: Created from HEAD", "merge master: Fast-forward",
                                                   "pull: Fast-forward", "reset: moving to master")))
        for message in ("commit: x", "commit (amend): x", "commit (merge): x", "cherry-pick: x", "revert: x",
                        "rebase (pick): x", "rebase -i (pick): x"):
            self.assertTrue(reflog_has_own_commit(log("c", "branch: Created from master", message)), message)
        self.assertFalse(reflog_has_own_commit(log("d", "commit: stara praca", "branch: Reset to master")))
        self.assertTrue(reflog_has_own_commit(log("e", "commit: x", "branch: Reset to master", "commit: y")))
        self.assertFalse(reflog_has_own_commit(log("f", "commit: x", "checkout: moving from a to b"), HEAD_RESTARTS))
        self.assertTrue(reflog_has_own_commit(log("g", "reset: moving to HEAD", "commit: x"), HEAD_RESTARTS))
        self.assertFalse(reflog_has_own_commit(log("h", "branch: Created from master", "commit: wip",
                                                   "reset: moving to master")))
        self.assertFalse(reflog_has_own_commit(log("i", "reset: moving to HEAD", "commit: x",
                                                   "reset: moving to HEAD~1"), HEAD_RESTARTS))

    def test_worktree_gitdir_from_dotgit_file(self):
        tmp = tempfile.mkdtemp(prefix="tide-gitdir-")
        self.addCleanup(shutil.rmtree, tmp)
        self.assertEqual(worktree_gitdir(tmp), "")
        with open(os.path.join(tmp, ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: ../repo/.git/worktrees/x\n")
        self.assertEqual(worktree_gitdir(tmp), os.path.normpath(os.path.join(tmp, "../repo/.git/worktrees/x")))

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
        self.assertEqual(found.available(), {"terminal": False, "rider": True, "window": False})
        self.assertEqual(found.command("rider", "/x"), [rider, "/x"])
        self.assertEqual(found.command("terminal", "/x"), [])
        missing = Launchers(env={"HOME": os.path.join(tmp, "nikt"), "PATH": ""}, path="")
        self.assertEqual(missing.available()["rider"], False)

    def test_window_launcher_found_in_data_home(self):
        tmp = tempfile.mkdtemp(prefix="tide-launch-")
        self.addCleanup(shutil.rmtree, tmp)
        env = {"HOME": tmp, "XDG_DATA_HOME": os.path.join(tmp, "data"), "PATH": ""}
        launchers = Launchers(env=env, path="")
        self.assertFalse(launchers.available()["window"], "bez instalacji okna nie ma przycisku")
        self.assertFalse(launchers.show_window())
        script = os.path.join(tmp, "data/tide/worktrees-window/tide-worktrees.py")
        os.makedirs(os.path.dirname(script))
        open(script, "w").close()
        self.assertTrue(launchers.available()["window"], "okno zainstalowane po starcie usługi też się liczy")
        self.assertEqual(launchers.command("window", ""), ["/usr/bin/python3", "-s", script])
        self.assertFalse(launchers.open("window", tmp), "Open() nie uruchamia okna")

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
        self.assertEqual((w["removable"], w["blocker"], w["group"]), (False, "protected", "main"))

    def test_remove_keeps_main_branch_worktree_and_branch(self):
        import subprocess
        self.scanner.refresh()
        [result] = self.scanner.remove([self.master_wt])
        self.assertEqual((result["removed"], result["reason"]), (False, "protected"))
        self.assertTrue(os.path.isdir(self.master_wt))
        branches = subprocess.run(["git", "branch", "--list", "master"], cwd=self.repo,
                                  capture_output=True, text=True, check=True).stdout
        self.assertIn("master", branches)

    def test_forced_removal_never_touches_main_branch_worktree_or_main_directory(self):
        import subprocess
        with open(os.path.join(self.master_wt, "a.txt"), "a") as fh:
            fh.write("zmiana na master\n")  # nawet ze zmianami nie staje się kandydatem
        self.scanner.refresh()
        items = by_path(self.scanner.snapshot([]))
        self.assertEqual((items[self.master_wt]["forceRemovable"], items[self.master_wt]["forceBlocker"]),
                         (False, "protected"))
        self.assertEqual(items[self.repo]["forceBlocker"], "main")
        results = self.scanner.remove([self.master_wt, self.repo], [], force=True, delete_unmerged=True,
                                      confirmed={self.master_wt: {"dirty": 1, "unmerged": 0, "branch": "master"}})
        self.assertEqual([(r["removed"], r["reason"], r["status"]) for r in results],
                         [(False, "protected", "refused"), (False, "main", "refused")])
        with open(os.path.join(self.master_wt, "a.txt")) as fh:
            self.assertIn("zmiana na master", fh.read())
        branches = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=self.repo,
                                  capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(branches), ["feat/summary", "master"])


class GroupingTest(WorktreeTestCase):
    """Grupy okna Worktrees na tych samych repozytoriach: w pracy, do sprzątnięcia, do decyzji,
    gałęzie główne, brakujące katalogi. Zegar zamrożony 10 dni po commitach z fixtures."""

    NOW = gitrepos.COMMIT_TS + 10 * 86400

    def setUp(self):
        super().setUp()
        # Zmiana w „dirty” ma czas commitów — liczy się jak stara (inaczej byłaby „w pracy”).
        os.utime(os.path.join(self.p["dirty"], "README.md"), (gitrepos.COMMIT_TS, gitrepos.COMMIT_TS))

    def groups(self, cwds=None):
        self.scanner.refresh()
        return {os.path.relpath(w["path"], self.root): w
                for w in self.scanner.snapshot(self.agent if cwds is None else cwds, now=self.NOW)}

    def test_groups_of_every_state(self):
        got = {name: w["group"] for name, w in self.groups().items()}
        self.assertEqual(got, {
            "alpha": "main",                    # główny katalog
            "alpha-wt/merged": "clean",         # scalony, czysty, bez agenta
            "alpha-wt/detached": "clean",       # odłączony na commicie z master
            "alpha-wt/dirty": "decide",         # zmiany, stoi od 10 dni
            "alpha-wt/unmerged": "decide",      # niescalone commity
            "alpha-wt/agent": "active",         # pracuje agent
            "group/beta": "main",
            "group/beta-wt/topic": "decide",    # brak gałęzi głównej
        })

    def test_recent_change_means_active_even_when_unmerged(self):
        recent = self.NOW - 3600
        os.utime(os.path.join(self.p["dirty"], "README.md"), (recent, recent))
        w = self.groups()["alpha-wt/dirty"]
        self.assertEqual((w["group"], w["lastActivityTs"], w["removable"]), ("active", recent, False))

    def test_clean_group_is_exactly_the_removable_idle_ones(self):
        for w in self.groups().values():
            if w["group"] == "clean":
                self.assertTrue(w["removable"], w["path"])
            if w["removable"]:
                self.assertIn(w["group"], ("clean", "active"), w["path"])

    def test_missing_directory_has_its_own_group(self):
        gone = os.path.join(self.root, "alpha-wt", "gone")
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "feat/gone", gone)
        shutil.rmtree(gone)
        w = self.groups()["alpha-wt/gone"]
        self.assertEqual((w["group"], w["missing"], w["removable"]), ("missing", True, False))

    def test_title_subject_and_tool(self):
        items = self.groups()
        merged = items["alpha-wt/merged"]
        self.assertEqual((merged["tag"], merged["title"], merged["subject"]), ("feat", "merged", "add merged.txt"))
        self.assertEqual((merged["tool"], merged["sessionTitle"], merged["sessionId"]), ("", "", ""))
        self.assertEqual(merged["lastActivityTs"], gitrepos.COMMIT_TS)
        detached = items["alpha-wt/detached"]
        self.assertEqual(detached["title"], f"odłączony HEAD {detached['head']}")
        self.assertEqual(items["alpha"]["subject"], "merge feat/merged")


class MergedWorkGroupTest(WorktreeTestCase):
    """Scalona praca idzie „Do sprzątnięcia” od razu, a nie po dobie (PT-30). Zegar: godzina po
    commitach z fixtures, więc każdy worktree mieści się w oknie ACTIVE_WINDOW."""

    NOW = gitrepos.COMMIT_TS + 3600

    def groups(self, cwds=None):
        self.scanner.refresh()
        return {os.path.relpath(w["path"], self.root): w
                for w in self.scanner.snapshot(self.agent if cwds is None else cwds, now=self.NOW)}

    def add(self, name, *args, start=()):
        path = os.path.join(self.root, "alpha-wt", name)
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", *args, path, *start)
        return path

    def test_merged_with_own_commits_is_clean_right_away(self):
        got = {name: w["group"] for name, w in self.groups().items()}
        self.assertEqual(got, {
            "alpha": "main",
            "alpha-wt/merged": "clean",         # własny commit, scalony, czysty — od razu
            "alpha-wt/detached": "active",      # bez własnych commitów — okno doby
            "alpha-wt/dirty": "active",         # zmiany — jak dotąd
            "alpha-wt/unmerged": "active",      # niescalone — jak dotąd
            "alpha-wt/agent": "active",         # agent — zawsze w pracy
            "group/beta": "main",
            "group/beta-wt/topic": "active",    # brak gałęzi głównej — jak dotąd
        })

    def test_agent_keeps_merged_work_active(self):
        w = self.groups(cwds=[self.p["merged"]])["alpha-wt/merged"]
        self.assertEqual((w["group"], w["hasAgent"]), ("active", True))

    def test_fresh_empty_branch_keeps_the_window(self):
        fresh = self.add("fresh", "-b", "feat/fresh")
        w = self.groups()["alpha-wt/fresh"]
        self.assertEqual((w["group"], w["merged"], w["removable"]), ("active", True, True))
        old = gitrepos.COMMIT_TS + 10 * 86400
        self.scanner.refresh()
        late = by_path(self.scanner.snapshot(self.agent, now=old + 86400))[fresh]
        self.assertEqual(late["group"], "clean", "po dobie świeży i pusty też idzie do sprzątnięcia")

    def test_fast_forward_to_main_is_not_own_work(self):
        fresh = self.add("ff", "-b", "feat/ff")
        gitrepos.commit(self.p["alpha"], "later.txt")
        gitrepos.git(fresh, "merge", "-q", "--ff-only", "master")
        self.assertEqual(self.groups()["alpha-wt/ff"]["group"], "active")

    def test_commit_merged_by_fast_forward_counts(self):
        work = self.add("ffwork", "-b", "feat/ffwork")
        gitrepos.commit(work, "w.txt")
        gitrepos.git(self.p["alpha"], "merge", "-q", "--ff-only", "feat/ffwork")
        w = self.groups()["alpha-wt/ffwork"]
        self.assertEqual((w["merged"], w["group"]), (True, "clean"))

    def test_detached_head_with_merged_commit_is_clean(self):
        work = self.add("det2", "--detach", start=("master",))
        gitrepos.commit(work, "d.txt")
        sha = gitrepos.git(work, "rev-parse", "HEAD").strip()
        gitrepos.git(self.p["alpha"], "merge", "-q", "--ff-only", sha)
        self.assertEqual(self.groups()["alpha-wt/det2"]["group"], "clean")

    def test_branch_recreated_without_new_commits_keeps_the_window(self):
        gitrepos.git(self.p["merged"], "checkout", "-q", "-B", "feat/merged", "master")
        self.assertEqual(self.groups()["alpha-wt/merged"]["group"], "active")

    def test_reset_after_own_commit_keeps_the_window(self):
        # Created from / commit: wip / reset: moving to master — commitów już nie ma w gałęzi.
        work = self.add("wip", "-b", "feat/wip")
        gitrepos.commit(work, "wip.txt")
        gitrepos.git(work, "reset", "-q", "--hard", "master")
        w = self.groups()["alpha-wt/wip"]
        self.assertEqual((w["merged"], w["removable"], w["group"]), (True, True, "active"))

    def test_detached_reset_after_commit_keeps_the_window(self):
        work = self.add("det3", "--detach", start=("master",))
        gitrepos.commit(work, "d3.txt")
        gitrepos.git(work, "reset", "-q", "--hard", "HEAD~1")
        self.assertEqual(self.groups()["alpha-wt/det3"]["group"], "active")

    def test_without_reflog_falls_back_to_the_window(self):
        os.remove(os.path.join(self.p["alpha"], ".git", "logs", "refs", "heads", "feat", "merged"))
        self.assertEqual(self.groups()["alpha-wt/merged"]["group"], "active")
        self.scanner.refresh()
        late = by_path(self.scanner.snapshot(self.agent, now=gitrepos.COMMIT_TS + 2 * 86400))[self.p["merged"]]
        self.assertEqual(late["group"], "clean")

    def test_clean_group_stays_a_subset_of_removable(self):
        items = self.groups()
        clean = sorted(name for name, w in items.items() if w["group"] == "clean")
        self.assertEqual(clean, ["alpha-wt/merged"])
        self.assertTrue(all(items[name]["removable"] for name in clean))
        # Kafelek i nagłówek liczą „w pracy” z pola group (bez głównych katalogów) — merged już nie.
        self.assertEqual(sum(1 for w in items.values() if not w["isMain"] and w["group"] == "active"), 5)

    def test_listing_reads_reflog_without_extra_git_calls(self):
        self.groups()
        self.assertFalse([c for c in self.git_calls() if "reflog" in c or c.startswith("log")])


class NestedWorktreeTest(WorktreeTestCase):
    """Worktree z innym worktree w ignorowanym katalogu (np. .claude/worktrees/) nie jest do usunięcia:
    `git worktree remove` bez --force kończy się sukcesem i kasuje zagnieżdżony razem z jego plikami."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.p["alpha"], ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
            fh.write(".claude/\n")
        self.outer = os.path.join(self.root, "alpha-wt", "outer")
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "feat/outer", self.outer)
        self.inner = os.path.join(self.outer, ".claude", "worktrees", "inner")
        self.precious = os.path.join(self.inner, "niezacommitowane.txt")

    def add_inner(self):
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "feat/inner", self.inner)
        with open(self.precious, "w", encoding="utf-8") as fh:
            fh.write("praca agenta\n")

    def test_outer_is_blocked_even_when_clean_and_merged(self):
        self.add_inner()
        w = self.listed()[self.outer]
        self.assertEqual((w["dirty"], w["merged"]), (0, True), "na oko czysty i scalony")
        self.assertEqual((w["removable"], w["blocker"], w["forceRemovable"], w["forceBlocker"]),
                         (False, "nested", False, "nested"))
        self.assertNotEqual(w["group"], "clean")

    def test_remove_refuses_and_keeps_nested_files(self):
        self.add_inner()
        self.listed()
        [res] = self.scanner.remove([self.outer], self.agent)
        self.assertEqual((res["status"], res["reason"]), ("refused", "nested"))
        self.assertIn("zawiera inne worktrees", res["message"])
        [forced] = self.scanner.remove([self.outer], self.agent, force=True, delete_unmerged=True)
        self.assertEqual((forced["status"], forced["reason"]), ("refused", "nested"))
        self.assertTrue(os.path.isfile(self.precious))
        self.assertFalse([c for c in self.git_calls() if "worktree remove" in c], "git worktree remove nie padło")

    def test_remove_rereads_nesting_after_stale_listing(self):
        self.assertEqual(self.listed()[self.outer]["blocker"], "")  # lista sprzed założenia zagnieżdżonego
        self.add_inner()
        [res] = self.scanner.remove([self.outer], self.agent)
        self.assertEqual(res["reason"], "nested")
        self.assertTrue(os.path.isfile(self.precious))

    def test_agent_in_nested_does_not_unblock_outer(self):
        self.add_inner()
        items = self.listed(cwds=[self.inner])
        self.assertEqual((items[self.inner]["hasAgent"], items[self.outer]["hasAgent"]), (True, False))
        self.assertEqual(items[self.outer]["forceBlocker"], "nested")
        [res] = self.scanner.remove([self.outer], [self.inner], force=True)
        self.assertEqual(res["reason"], "nested")
        self.assertTrue(os.path.isdir(self.inner))

    def test_nested_worktree_of_another_repository_blocks_too(self):
        other = os.path.join(self.outer, ".claude", "beta-copy")
        gitrepos.git(self.p["beta"], "worktree", "add", "-q", "-b", "feat/elsewhere", other)
        self.assertEqual(self.listed()[self.outer]["blocker"], "nested")
        [res] = self.scanner.remove([self.outer], self.agent)
        self.assertEqual(res["reason"], "nested")
        self.assertTrue(os.path.isdir(other))


class RemoveSubsetTest(WorktreeTestCase):
    def test_removes_only_the_selected_paths(self):
        self.listed()
        removable = sorted(w["path"] for w in self.scanner.snapshot(self.agent) if w["removable"])
        self.assertEqual(removable, sorted([self.p["merged"], self.p["detached"]]))
        results = self.scanner.remove([self.p["detached"]], self.agent)
        self.assertEqual([(r["path"], r["removed"]) for r in results], [(self.p["detached"], True)])
        self.assertFalse(os.path.exists(self.p["detached"]))
        self.assertTrue(os.path.isdir(self.p["merged"]), "niezaznaczony scalony worktree zostaje")
        self.assertIn("feat/merged", gitrepos.branches(self.p["alpha"]))
        left = by_path(self.scanner.snapshot(self.agent))
        self.assertTrue(left[self.p["merged"]]["removable"])
        removals = [c for c in self.git_calls() if " worktree remove " in c]
        self.assertEqual(removals, [f"-C {self.p['alpha']} worktree remove {self.p['detached']}"])


class PruneTest(WorktreeTestCase):
    def test_prune_only_drops_missing_entries(self):
        gone = os.path.join(self.root, "alpha-wt", "gone")
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "feat/gone", gone)
        shutil.rmtree(gone)
        self.listed()
        result = self.scanner.prune(self.p["alpha"])
        self.assertEqual((result["ok"], result["pruned"]), (True, [gone]))
        paths = set(by_path(self.scanner.snapshot(self.agent)))
        self.assertNotIn(gone, paths)
        for kept in ("merged", "dirty", "unmerged", "agent", "detached"):
            self.assertIn(self.p[kept], paths)
            self.assertTrue(os.path.isdir(self.p[kept]))
        self.assertIn("feat/gone", gitrepos.branches(self.p["alpha"]), "prune nie kasuje gałęzi")

    def test_prune_does_nothing_without_missing_worktrees(self):
        self.listed()
        open(self.log, "w").close()
        result = self.scanner.prune(self.p["alpha"])
        self.assertEqual((result["ok"], result["pruned"]), (True, []))
        self.assertFalse([c for c in self.git_calls() if "prune" in c])

    def test_prune_refuses_unknown_repository(self):
        self.listed()
        result = self.scanner.prune(os.path.join(self.root, "obce"))
        self.assertFalse(result["ok"])


class ForceFieldsTest(WorktreeTestCase):
    """Pola dla usuwania wymuszonego w List(): które worktrees da się usunąć „mimo to” i ile
    commitów przepadłoby razem z gałęzią."""

    def test_force_removable_only_without_hard_blockers(self):
        items = self.listed()
        got = {os.path.relpath(p, self.root): (w["forceRemovable"], w["forceBlocker"]) for p, w in items.items()}
        self.assertEqual(got, {
            "alpha": (False, "main"),
            "alpha-wt/merged": (True, ""),
            "alpha-wt/detached": (True, ""),
            "alpha-wt/dirty": (True, ""),
            "alpha-wt/unmerged": (True, ""),
            "alpha-wt/agent": (False, "agent"),
            "group/beta": (False, "main"),
            "group/beta-wt/topic": (True, ""),
        })

    def test_unpushed_counts_commits_that_exist_only_here(self):
        items = self.listed()
        self.assertEqual(items[self.p["unmerged"]]["unpushed"], 2, "bez serwera oba commity są tylko tutaj")
        self.assertEqual(items[self.p["merged"]]["unpushed"], 0)
        self.assertEqual(items[self.p["alpha"]]["unpushed"], 0, "główny katalog nie jest liczony")
        # Pierwszy z dwóch commitów trafia na „serwer” (lokalne repozytorium bare, bez sieci).
        origin = os.path.join(self.tmp, "origin.git")
        gitrepos.git(self.tmp, "init", "-q", "--bare", origin)
        gitrepos.git(self.p["alpha"], "remote", "add", "origin", origin)
        gitrepos.git(self.p["alpha"], "push", "-q", "origin", "feat/unmerged~1:refs/heads/feat/unmerged")
        self.assertEqual(self.listed()[self.p["unmerged"]]["unpushed"], 1)
        # Commit, który jest też w innej lokalnej gałęzi, nie przepada razem z tą.
        gitrepos.git(self.p["alpha"], "branch", "kopia", "feat/unmerged")
        self.assertEqual(self.listed()[self.p["unmerged"]]["unpushed"], 0)


class ForceRemoveTest(WorktreeTestCase):
    """Usuwanie wymuszone w rdzeniu: --force dla worktree, gałąź -d gdy scalona, -D tylko na
    prośbę, twarde blokady bez wyjątków, świeży stan porównany ze stanem z potwierdzenia."""

    def setUp(self):
        super().setUp()
        self.listed()
        open(self.log, "w").close()

    def forced(self, keys, delete_unmerged=False, confirmed=None, cwds=None):
        return self.scanner.remove([self.p[k] for k in keys], self.agent if cwds is None else cwds, force=True,
                                   delete_unmerged=delete_unmerged, confirmed=confirmed)

    def destructive_calls(self):
        return [c for c in self.git_calls() if " worktree remove" in c or " branch " in c]

    def test_dirty_unmerged_and_no_main_are_removed_but_unmerged_branches_stay(self):
        results = self.forced(["dirty", "unmerged", "topic", "detached"])
        got = {os.path.basename(r["path"]): (r["removed"], r["status"], r["branchDeleted"]) for r in results}
        self.assertEqual(got, {
            "dirty": (True, "removed", True),        # scalona gałąź znika przez -d
            "unmerged": (True, "branch-kept", False),
            "topic": (True, "branch-kept", False),   # brak gałęzi głównej — nie wiadomo, co scalone
            "detached": (True, "removed", False),    # bez gałęzi
        })
        for r in results:
            self.assertTrue(r["forced"])
            self.assertFalse(os.path.exists(r["path"]), r["path"])
        by_name = {os.path.basename(r["path"]): r for r in results}
        self.assertEqual(by_name["dirty"]["message"], "usunięty razem ze zmianami")
        self.assertEqual(by_name["unmerged"]["branchMessage"], "gałąź feat/unmerged zostaje (2 commity poza master)")
        self.assertIn("gałąź topic zostaje", by_name["topic"]["branchMessage"])
        self.assertEqual(sorted(gitrepos.branches(self.p["alpha"])),
                         ["feat/agent", "feat/merged", "feat/unmerged", "master"])
        self.assertIn("topic", gitrepos.branches(self.p["beta"]))
        self.assertEqual(sorted(self.destructive_calls()), sorted([
            f"-C {self.p['alpha']} worktree remove --force {self.p['dirty']}",
            f"-C {self.p['alpha']} branch -d feat/dirty",
            f"-C {self.p['alpha']} worktree remove --force {self.p['unmerged']}",
            f"-C {self.p['beta']} worktree remove --force {self.p['topic']}",
            f"-C {self.p['alpha']} worktree remove --force {self.p['detached']}",
        ]))

    def test_unmerged_branch_deleted_only_with_explicit_flag(self):
        [result] = self.forced(["unmerged"], delete_unmerged=True)
        self.assertEqual((result["removed"], result["branchDeleted"], result["status"]), (True, True, "removed"))
        self.assertNotIn("feat/unmerged", gitrepos.branches(self.p["alpha"]))
        self.assertIn(f"-C {self.p['alpha']} branch -D feat/unmerged", self.git_calls())

    def test_safe_remove_ignores_delete_flag(self):
        [result] = self.scanner.remove([self.p["unmerged"]], self.agent, delete_unmerged=True)
        self.assertEqual((result["removed"], result["reason"], result["status"]), (False, "unmerged", "refused"))
        self.assertTrue(os.path.isdir(self.p["unmerged"]))
        for call in self.git_calls():
            self.assertNotRegex(call, r"(--force|\s-D\b)")

    def test_main_worktree_and_running_agent_refused_even_forced(self):
        results = self.forced(["alpha", "beta", "agent"], delete_unmerged=True)
        self.assertEqual([(r["removed"], r["reason"], r["status"]) for r in results],
                         [(False, "main", "refused"), (False, "main", "refused"), (False, "agent", "refused")])
        self.assertIn("zamknij sesję", results[2]["message"])
        for key in ("alpha", "beta", "agent"):
            self.assertTrue(os.path.isdir(self.p[key]))
        self.assertEqual(self.destructive_calls(), [])

    def test_protected_branch_worktree_refused_even_forced_with_changes(self):
        # develop w osobnym worktree, ze zmianą i własnym commitem — gdyby nie ochrona, byłby
        # zwykłym kandydatem do „usuń mimo to” (i -D na develop).
        develop = os.path.join(self.root, "alpha-wt", "develop")
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "develop", develop)
        gitrepos.commit(develop, "d.txt")
        with open(os.path.join(develop, "d.txt"), "a", encoding="utf-8") as fh:
            fh.write("zmiana\n")
        w = self.listed()[develop]
        self.assertEqual((w["forceRemovable"], w["forceBlocker"], w["group"]), (False, "protected", "main"))
        open(self.log, "w").close()
        [result] = self.scanner.remove([develop], [], force=True, delete_unmerged=True,
                                       confirmed={develop: {"dirty": 1, "unmerged": 1, "branch": "develop"}})
        self.assertEqual((result["removed"], result["reason"], result["status"]), (False, "protected", "refused"))
        self.assertTrue(os.path.isfile(os.path.join(develop, "d.txt")))
        self.assertIn("develop", gitrepos.branches(self.p["alpha"]))
        self.assertEqual(self.destructive_calls(), [])

    def test_locked_and_missing_refused_even_forced(self):
        gitrepos.git(self.p["alpha"], "worktree", "lock", self.p["dirty"])
        gone = os.path.join(self.root, "alpha-wt", "gone")
        gitrepos.git(self.p["alpha"], "worktree", "add", "-q", "-b", "feat/gone", gone)
        shutil.rmtree(gone)
        self.listed()
        open(self.log, "w").close()
        results = self.scanner.remove([self.p["dirty"], gone], [], force=True, delete_unmerged=True)
        self.assertEqual([(r["removed"], r["reason"]) for r in results], [(False, "locked"), (False, "missing")])
        self.assertIn("git worktree unlock", results[0]["message"])
        self.assertTrue(os.path.isdir(self.p["dirty"]))
        self.assertIn("feat/gone", gitrepos.branches(self.p["alpha"]))
        self.assertEqual(self.destructive_calls(), [])

    def test_refused_when_state_got_worse_since_confirmation(self):
        seen = {"dirty": 1, "unmerged": 0, "unpushed": 0, "branch": "feat/dirty"}
        with open(os.path.join(self.p["dirty"], "nowy.txt"), "w", encoding="utf-8") as fh:
            fh.write("praca, której nie było w potwierdzeniu\n")
        [result] = self.forced(["dirty"], confirmed={self.p["dirty"]: seen})
        self.assertEqual((result["removed"], result["reason"]), (False, "changed"))
        self.assertIn("było 1, jest 2", result["message"])
        self.assertTrue(os.path.isfile(os.path.join(self.p["dirty"], "nowy.txt")))

        gitrepos.commit(self.p["unmerged"], "u3.txt")
        [result] = self.forced(["unmerged"], confirmed={self.p["unmerged"]: {"dirty": 0, "unmerged": 2}})
        self.assertEqual(result["reason"], "changed")
        [result] = self.forced(["unmerged"], confirmed={self.p["unmerged"]: {"branch": "feat/inna"}})
        self.assertEqual(result["reason"], "changed")
        self.assertTrue(os.path.isdir(self.p["unmerged"]))
        self.assertFalse([c for c in self.git_calls() if " worktree remove" in c])

    def test_same_or_better_state_than_confirmed_is_removed(self):
        confirmed = {self.p["dirty"]: {"dirty": 1, "unmerged": 0, "unpushed": 0, "branch": "feat/dirty"},
                     self.p["unmerged"]: {"dirty": 3, "unmerged": 2, "unpushed": 2, "branch": "feat/unmerged"}}
        results = self.forced(["dirty", "unmerged"], confirmed=confirmed)
        self.assertEqual([r["removed"] for r in results], [True, True])

    def test_force_never_doubles_or_deletes_main_branches(self):
        self.forced(["dirty", "unmerged", "topic", "alpha", "agent"], delete_unmerged=True)
        self.assertTrue(self.destructive_calls())
        for call in self.git_calls():
            self.assertNotRegex(call, r"--force --force|-f -f|\s-ff\b")
            self.assertNotRegex(call, r"branch -[dD] (master|main|develop)\b")


class StatusFieldTest(WorktreeTestCase):
    def test_safe_remove_reports_status(self):
        self.listed()
        results = self.scanner.remove([self.p["merged"], self.p["dirty"]], self.agent)
        self.assertEqual([(r["status"], r["forced"]) for r in results], [("removed", False), ("refused", False)])
