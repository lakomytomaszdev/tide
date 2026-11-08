"""PT-170: rzeczywiste refy, kopie .git i współbieżne końce kroków bez Incusa."""
import concurrent.futures
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest import mock

from tide_daemon.box import refguard
from tide_daemon.box import runner as br
from tide_daemon.cardflow import boxstep
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.cardflow.gitops import FlowGit, lock_for
from tide_daemon.gitcommand import HOST_OPTIONS
from tide_daemon.tasks import agentcmd, stepstop
from tide_daemon.worktrees.git import Git

from . import gitrepos
from .test_box_runner import BoxRunnerCase


class GuardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tide-refguard-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = str(self.root / "repo")
        os.mkdir(self.repo)
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")})
        patch.start()
        self.addCleanup(patch.stop)
        gitrepos.git(self.repo, "init", "-q", "-b", "master")
        gitrepos.commit(self.repo, "initial")
        self.original = gitrepos.git(self.repo, "rev-parse", "master").strip()
        self.worktrees = {}
        for name in ("one", "two"):
            wt = str(self.root / "repo-worktrees" / name)
            gitrepos.git(self.repo, "worktree", "add", "-q", "-b", f"ticket/{name}", wt)
            self.worktrees[name] = wt
        self.common = Path(self.repo) / ".git"

    def begin(self, name="one", card="K-38", **kw):
        wt = self.worktrees[name]
        admin = gitrepos.git(wt, "rev-parse", "--absolute-git-dir").strip()
        refguard.begin(self.repo, card, name, admin, branch="ticket/" + name, worktree=wt, **kw)

    def finish(self, name="one", card="K-38"):
        return refguard.finish(self.repo, card, name, branch="ticket/" + name)

    def commit(self, name):
        gitrepos.commit(self.worktrees[name], name)
        return gitrepos.git(self.repo, "rev-parse", "ticket/" + name).strip()

    def test_deleted_own_ref_is_restored_without_reverting_parallel_ticket(self):
        self.begin()
        self.begin("two")
        tip = self.commit("two")
        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/ticket/one"])
        self.assertFalse(result["errors"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/one").strip(), self.original)
        self.assertEqual(self.finish("two"), {})
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/two").strip(), tip)

    def test_foreign_movement_is_reported_without_any_ref_write_and_own_commit_survives(self):
        self.begin()
        tip = self.commit("one")
        gitrepos.git(self.repo, "update-ref", "refs/heads/master", tip)
        with mock.patch.object(Git, "run", autospec=True, side_effect=Git.run) as run:
            result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/master"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "master").strip(), tip)
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/one").strip(), tip)
        updates = [c.args[2:] for c in run.call_args_list if "update-ref" in c.args]
        self.assertEqual(updates, [])
        self.assertEqual(self.finish(), result)  # trwały wynik, także po utracie pamięci procesu

    def test_added_branch_tag_and_deleted_foreign_ref_are_only_reported(self):
        gitrepos.git(self.repo, "branch", "existing")
        self.begin()
        gitrepos.git(self.repo, "branch", "tmp-evil")
        gitrepos.git(self.repo, "tag", "evil")
        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/existing")
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/existing", "refs/heads/tmp-evil", "refs/tags/evil"])
        refs = gitrepos.git(self.repo, "for-each-ref", "--format=%(refname)")
        self.assertNotIn("refs/heads/existing", refs)
        self.assertIn("refs/heads/tmp-evil", refs)
        self.assertIn("refs/tags/evil", refs)

    def test_ref_cas_does_not_overwrite_a_change_after_comparison(self):
        self.begin()
        self.commit("one")
        good = self.commit("two")
        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
        real_run = Git.run

        def raced(git, cwd, *args, **kw):
            if "update-ref" in args and "refs/heads/ticket/one" in args:
                gitrepos.git(self.repo, "update-ref", "refs/heads/ticket/one", good)
            return real_run(git, cwd, *args, **kw)

        with mock.patch.object(Git, "run", raced):
            result = self.finish()
        self.assertTrue(result["errors"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/one").strip(), good)

    def test_two_overlapping_steps_finish_together_without_reverting_either_ticket(self):
        self.begin()
        self.begin("two")
        tips = {name: self.commit(name) for name in self.worktrees}
        barrier = threading.Barrier(2)

        def end(name):
            barrier.wait(timeout=5)
            return self.finish(name)

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(end, ("one", "two")))
        self.assertEqual(results, [{}, {}])
        for name, tip in tips.items():
            self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/" + name).strip(), tip)

    def test_untrusted_head_cannot_grant_master_to_either_step(self):
        self.begin()
        gitrepos.git(self.worktrees["two"], "symbolic-ref", "HEAD", "refs/heads/master")
        with self.assertRaises(agentcmd.StepError):
            self.begin("two")
        peers = refguard._active(self.repo)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0][1]["allowed"], ["refs/heads/ticket/one"])
        # Nawet zapis przez podmieniony HEAD odrzuconego sąsiada nie dostaje wyjątku.
        gitrepos.commit(self.worktrees["two"], "attack")
        tip = gitrepos.git(self.repo, "rev-parse", "master").strip()
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/master"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "master").strip(), tip)

    def test_bad_admin_entry_does_not_skip_independent_repairs_and_can_be_retried(self):
        config = (self.common / "config").read_bytes()
        pointer = Path(self.worktrees["one"]) / ".git"
        original_pointer = pointer.read_bytes()
        self.begin()
        tip = self.commit("one")
        gitrepos.git(self.repo, "update-ref", "refs/heads/master", tip)
        gitrepos.git(self.worktrees["one"], "symbolic-ref", "HEAD", "refs/heads/master")
        (self.common / "hooks/new-hook").write_text("obcy hak")
        (self.common / "config").write_text("uszkodzona konfiguracja [\n")
        (self.common / "info/attributes").write_text("obce atrybuty")
        pointer.write_text("obcy wskaźnik\n")
        junk = self.common / "worktrees/junk"
        junk.write_text("nie katalog")
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/master"])
        self.assertIn("hooks", result["files"])
        self.assertTrue(result["head"])
        self.assertTrue(any("worktrees/junk" in error for error in result["errors"]))
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "master").strip(), tip)
        self.assertFalse((self.common / "hooks/new-hook").exists())
        self.assertFalse((self.common / "info/attributes").exists())
        self.assertEqual((self.common / "config").read_bytes(), config)
        self.assertEqual(pointer.read_bytes(), original_pointer)
        self.assertEqual(gitrepos.git(self.worktrees["one"], "symbolic-ref", "HEAD").strip(),
                         "refs/heads/ticket/one")
        self.assertEqual(len(refguard._active(self.repo)), 1)
        junk.unlink()
        retried = self.finish()
        self.assertFalse(retried["errors"])
        self.assertEqual(retried["refs"], result["refs"])
        self.assertEqual(refguard._active(self.repo), [])
        self.assertEqual(self.finish(), retried)

    def test_new_dangling_foreign_symbolic_ref_is_only_reported(self):
        self.begin()
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/tmp-evil", "refs/heads/missing")
        result = self.finish()
        self.assertEqual(result.get("refs"), ["refs/heads/tmp-evil"])
        self.assertFalse(result["errors"])
        self.assertTrue((self.common / "refs/heads/tmp-evil").exists())

    def test_dangling_foreign_symbolic_ref_changes_are_only_reported(self):
        ref = "refs/heads/alias"
        gitrepos.git(self.repo, "symbolic-ref", ref, "refs/heads/missing")
        self.begin()
        self.assertEqual(self.finish(), {})
        for change in (("symbolic-ref", ref, "refs/heads/other-missing"), ("symbolic-ref", "--delete", ref)):
            with self.subTest(change=change):
                gitrepos.git(self.repo, "symbolic-ref", ref, "refs/heads/missing")
                self.begin()
                gitrepos.git(self.repo, *change)
                result = self.finish()
                self.assertEqual(result["refs"], [ref])
                self.assertFalse(result["errors"])
                if change[1] == "--delete":
                    self.assertFalse((self.common / ref).exists())
                else:
                    self.assertEqual(gitrepos.git(self.repo, "symbolic-ref", ref).strip(), "refs/heads/other-missing")

    def test_own_ref_replaced_by_dangling_symbolic_ref_is_restored_without_touching_target(self):
        self.begin()
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/ticket/one", "refs/heads/missing")
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/ticket/one"])
        self.assertFalse(result["errors"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/one").strip(), self.original)
        self.assertFalse((self.common / "refs/heads/missing").exists())

    def test_own_alias_to_parallel_branch_is_repaired_without_reverting_its_target(self):
        self.begin()
        self.begin("two")
        tip = self.commit("two")
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/ticket/one", "refs/heads/ticket/two")
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/ticket/one"])
        self.assertFalse(result["errors"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/one").strip(), self.original)
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/two").strip(), tip)
        self.assertEqual(self.finish("two"), {})

    def test_own_symbolic_ref_repair_checks_target_again_under_ref_lock(self):
        self.begin()
        ref = "refs/heads/ticket/one"
        gitrepos.git(self.repo, "symbolic-ref", ref, "refs/heads/missing")
        real_restore = refguard._restore_symref
        real_refs = refguard._refs

        def raced(repo, name, old, now):
            gitrepos.git(repo, "symbolic-ref", name, "refs/heads/new-missing")
            return real_restore(repo, name, old, now)

        def refs(repo):
            if (self.common / (ref + ".lock")).exists():
                # Równoległy zapis przez gita jest blokowany standardowym plikiem lock.
                self.assertFalse(Git().run(repo, "symbolic-ref", ref, "refs/heads/blocked").ok)
            return real_refs(repo)

        with mock.patch.object(refguard, "_restore_symref", side_effect=raced), \
                mock.patch.object(refguard, "_refs", side_effect=refs):
            result = self.finish()
        self.assertEqual(result["refs"], [ref])
        self.assertTrue(any("zmieniony po porównaniu" in e for e in result["errors"]))
        self.assertEqual(gitrepos.git(self.repo, "symbolic-ref", ref).strip(), "refs/heads/new-missing")
        self.assertFalse((self.common / (ref + ".lock")).exists())

    def test_single_unreadable_path_does_not_skip_other_paths_or_refs_and_retry_repairs_it(self):
        self.begin()
        tip = self.commit("one")
        gitrepos.git(self.repo, "update-ref", "refs/heads/master", tip)
        (self.common / "hooks/new-hook").write_text("obcy hak")
        (self.common / "info/attributes").write_text("obce atrybuty")
        real_node = refguard._node

        def unreadable(parent, name):
            if name == "hooks":
                raise PermissionError("brak odczytu hooks")
            return real_node(parent, name)

        with mock.patch.object(refguard, "_node", side_effect=unreadable):
            result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/master"])
        self.assertEqual(result["files"], ["info"])
        self.assertTrue(result["errors"])
        self.assertFalse((self.common / "info/attributes").exists())
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "master").strip(), tip)
        retried = self.finish()
        self.assertFalse(retried["errors"])
        self.assertEqual(retried["files"], ["hooks", "info"])
        self.assertFalse((self.common / "hooks/new-hook").exists())

    def test_later_start_is_allowed_even_after_older_step_has_committed(self):
        self.begin()
        self.commit("one")
        self.begin("two")
        self.commit("two")
        self.assertEqual(self.finish("one"), {})
        self.assertEqual(self.finish("two"), {})

    def test_snapshot_waits_for_repository_lock_before_reading_refs(self):
        entered = threading.Event()
        real_refs = refguard._refs

        def refs(repo):
            entered.set()
            return real_refs(repo)

        with mock.patch.object(refguard, "_refs", side_effect=refs), concurrent.futures.ThreadPoolExecutor(1) as pool:
            with lock_for(self.repo):
                future = pool.submit(self.begin)
                self.assertFalse(entered.wait(0.1))
                gitrepos.git(self.repo, "tag", "before-lock")
            future.result(timeout=5)
        self.assertTrue(entered.is_set())
        self.assertEqual(self.finish(), {})

    def test_foreign_card_branch_is_not_allowed(self):
        self.begin()
        tip = self.commit("two")
        self.assertEqual(self.finish()["refs"], ["refs/heads/ticket/two"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/two").strip(), tip)

    def test_other_card_waits_and_cannot_start_until_guard_is_finished(self):
        self.begin()
        self.assertEqual(refguard.other_cards(self.repo, "K-99"), ["K-38"])
        with self.assertRaises(agentcmd.StepError):
            self.begin("two", card="K-99")
        self.assertEqual(self.finish(), {})
        self.assertEqual(refguard.other_cards(self.repo, "K-99"), [])
        self.begin("two", card="K-99")
        self.commit("two")
        self.assertEqual(self.finish("two", card="K-99"), {})

    def test_incident_k55_k60_survive_finish_abort_and_fresh_process_recovery(self):
        for ending in ("finish", "abort", "restart"):
            for delete_k60 in (False, True):
                with self.subTest(ending=ending, delete_k60=delete_k60):
                    for name in ("55", "60"):
                        gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-" + name, self.original)
                    self.begin()
                    gitrepos.commit(self.worktrees["two"], f"{ending}-{delete_k60}")
                    tip = gitrepos.git(self.repo, "rev-parse", "ticket/two").strip()
                    gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-55", tip)
                    gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-60", tip)
                    if delete_k60:
                        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/card/k-60")
                    gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
                    if ending == "restart":
                        # Świeży interpreter, bez pamięci/blokad autora migawki.
                        script = ("import json, sys; from tide_daemon.box import refguard; "
                                  "print(json.dumps(refguard.finish(sys.argv[1], 'K-38', 'one', active_only=True)))")
                        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(__file__)))
                        proc = subprocess.run([sys.executable, "-c", script, self.repo], env=env,
                                              check=True, text=True, capture_output=True)
                        result = json.loads(proc.stdout)
                    elif ending == "abort":
                        engine = FlowEngine.__new__(FlowEngine)
                        engine.journal = mock.Mock()
                        engine.journal.read.return_value = []
                        for method in ("_queue_notices", "_release_local", "_release_box"):
                            setattr(engine, method, mock.Mock())
                        engine._codex_broken_mcp = mock.Mock(return_value=False)
                        engine.tmp_root = str(self.root / "tmp")
                        engine.procs, engine.watch = {}, {}
                        step = dict(step="one", runner="box", repo=self.repo, branch="ticket/one", kind="ticket")
                        engine._finish("K-38", step, forced={"result": "stopped", "reason": "abort"})
                        result = next(c.kwargs for c in engine.journal.append.call_args_list
                                      if c.args[1] == "refs.guard")
                    else:
                        result = self.finish()
                    self.assertFalse(result["errors"])
                    self.assertTrue({"refs/heads/card/k-55", "refs/heads/card/k-60",
                                     "refs/heads/ticket/one"}.issubset(result["refs"]))
                    self.assertEqual(gitrepos.git(self.repo, "rev-parse", "card/k-55").strip(), tip)
                    refs = refguard._refs(self.repo)
                    if delete_k60:
                        self.assertNotIn("refs/heads/card/k-60", refs)
                    else:
                        self.assertEqual(refs["refs/heads/card/k-60"]["oid"], tip)
                    self.assertEqual(refs["refs/heads/ticket/one"]["oid"], self.original)
                    self.assertEqual(refguard._active(self.repo), [])

    def test_restart_reservation_blocks_other_card_and_duplicate_begin(self):
        self.begin()
        before = Path(refguard._path(self.repo, "K-38", "one")).read_bytes()
        script = ("import sys; from tide_daemon.box import refguard; "
                  "refguard.begin(sys.argv[1], 'K-99', 'two', sys.argv[2], branch='ticket/two')")
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(__file__)))
        proc = subprocess.run([sys.executable, "-c", script, self.repo, str(self.common / "worktrees/two")],
                              env=env, text=True, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("trwa krok innej karty", proc.stderr)
        with self.assertRaisesRegex(agentcmd.StepError, "już aktywna"):
            self.begin()
        self.assertEqual(Path(refguard._path(self.repo, "K-38", "one")).read_bytes(), before)
        self.assertEqual(refguard.other_cards(self.repo, "K-99"), ["K-38"])

    def test_finish_cannot_change_trusted_branch_from_begin(self):
        self.begin()
        with mock.patch.object(refguard, "_restore") as restore:
            result = refguard.finish(self.repo, "K-38", "one", branch="master")
        self.assertTrue(result["errors"])
        restore.assert_not_called()
        self.assertTrue(refguard._active(self.repo))
        self.assertEqual(self.finish(), {})

    def test_unknown_persisted_state_is_not_discarded_after_restart(self):
        self.begin()
        path = Path(refguard._path(self.repo, "K-38", "one"))
        path.write_text("{przerwany zapis")
        for operation in (lambda: self.begin("two", card="K-99"),
                          lambda: refguard.other_cards(self.repo, "K-99")):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(path.read_text(), "{przerwany zapis")
        self.assertTrue(self.finish()["errors"])

    def test_recovery_does_not_follow_replaced_worktree_directory(self):
        self.begin()
        worktree = Path(self.worktrees["one"])
        moved = worktree.with_name("moved")
        worktree.rename(moved)
        victim = self.root / "foreign-worktree"
        victim.mkdir()
        pointer = victim / ".git"
        pointer.write_text("nie ruszać\n")
        worktree.symlink_to(victim, target_is_directory=True)
        self.assertTrue(self.finish()["errors"])
        self.assertEqual(pointer.read_text(), "nie ruszać\n")

    def test_host_snapshot_cannot_be_placed_inside_project_mounts(self):
        for state in (str(self.common / "state"), str(self.root / "repo-worktrees/state")):
            with self.subTest(state=state), mock.patch.dict(os.environ, XDG_STATE_HOME=state):
                with self.assertRaisesRegex(agentcmd.StepError, "stan hosta"):
                    self.begin()

    def test_common_dir_and_admin_symlinks_are_rejected_before_git(self):
        def begin():
            refguard.begin(self.repo, "K-38", "one", str(self.common / "worktrees/one"),
                           branch="ticket/one", worktree=self.worktrees["one"])

        pointer = self.common / "worktrees/one/commondir"
        pointer.write_text(str(self.root / "foreign"))
        with self.assertRaisesRegex(agentcmd.StepError, "commondir"):
            begin()
        pointer.write_text("../..\n")
        admin = self.common / "worktrees/one"
        moved = self.common / "worktrees/moved"
        admin.rename(moved)
        admin.symlink_to(moved, target_is_directory=True)
        with self.assertRaises((OSError, agentcmd.StepError)):
            begin()
        self.assertEqual(refguard._active(self.repo), [])

    def test_git_storage_redirects_are_rejected_without_touching_target(self):
        for name in ("refs/heads/outside", "logs/refs/heads/outside", "objects/outside"):
            with self.subTest(name=name):
                victim = self.root / "victim"
                victim.write_text("nie ruszać\n")
                path = self.common / name
                path.symlink_to(victim)
                with self.assertRaises(agentcmd.StepError):
                    self.begin()
                self.assertEqual(victim.read_text(), "nie ruszać\n")
                path.unlink()
        alternate = self.common / "objects/info/alternates"
        alternate.write_text(str(self.root / "outside-objects") + "\n")
        with self.assertRaisesRegex(agentcmd.StepError, "alternates"):
            self.begin()

    def test_reflog_hardlink_blocks_repair_without_writing_outside_project(self):
        self.begin()
        victim = self.root / "foreign-log"
        victim.write_text("nie ruszać\n")
        log = self.common / "logs/refs/heads/ticket/one"
        log.unlink()
        os.link(victim, log)
        (self.common / "refs/heads/ticket/one").unlink()
        self.assertTrue(self.finish()["errors"])
        self.assertEqual(victim.read_text(), "nie ruszać\n")
        self.assertTrue(refguard._active(self.repo))
        log.unlink()
        self.assertFalse(self.finish()["errors"])
        self.assertEqual(victim.read_text(), "nie ruszać\n")

    def test_project_config_includes_hooks_and_environment_do_not_control_guard_git(self):
        marker = self.root / "hook-ran"
        hook = self.common / "hooks/reference-transaction"
        hook.write_text("#!/bin/sh\necho BAD > '" + str(marker) + "'\n")
        hook.chmod(0o755)
        bad_include = self.root / "included-config"
        bad_include.write_text("invalid config [\n")
        config = self.common / "config"
        with config.open("a") as fh:
            fh.write(f"\n[include]\npath = {bad_include}\n[core]\nworktree = /nonexistent/outside\n")
        poisoned = {"GIT_DIR": "/nonexistent/foreign", "GIT_COMMON_DIR": "/nonexistent/foreign",
                    "GIT_OBJECT_DIRECTORY": "/nonexistent/foreign", "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(hook.parent)}
        # Testowa modyfikacja refu wprost: zwykły Git celowo nie czyta zatrutej konfiguracji.
        with mock.patch.dict(os.environ, poisoned):
            refguard.begin(self.repo, "K-38", "one", str(self.common / "worktrees/one"),
                           branch="ticket/one", worktree=self.worktrees["one"])
            (self.common / "refs/heads/ticket/one").unlink()
            result = self.finish()
        self.assertFalse(result["errors"])
        self.assertFalse(marker.exists())
        self.assertEqual(refguard._refs(self.repo)["refs/heads/ticket/one"]["oid"], self.original)

    def test_packed_refs_and_reading_other_card_objects_remain_supported(self):
        tip = self.commit("two")
        gitrepos.git(self.repo, "branch", "card/k-55", tip)
        gitrepos.git(self.repo, "pack-refs", "--all")
        self.begin()
        self.assertEqual(refguard._git(self.repo, "show", "card/k-55:two"), "x")
        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
        self.assertFalse(self.finish()["errors"])
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "card/k-55").strip(), tip)

    def test_sha256_repository_keeps_its_object_format_in_private_gitdir(self):
        repo = str(self.root / "sha256")
        os.mkdir(repo)
        gitrepos.git(repo, "init", "-q", "-b", "master", "--object-format=sha256")
        gitrepos.commit(repo, "initial")
        tip = gitrepos.git(repo, "rev-parse", "HEAD").strip()
        wt = repo + "-worktrees/one"
        gitrepos.git(repo, "worktree", "add", "-q", "-b", "ticket/one", wt)
        refguard.begin(repo, "K-38", "one", os.path.join(repo, ".git/worktrees/one"),
                       branch="ticket/one", worktree=wt)
        gitrepos.git(repo, "update-ref", "-d", "refs/heads/ticket/one")
        self.assertFalse(refguard.finish(repo, "K-38", "one")["errors"])
        self.assertEqual(gitrepos.git(repo, "rev-parse", "ticket/one").strip(), tip)

    def test_host_creates_a_future_card_branch_without_triggering_guard(self):
        self.begin()
        service = FlowGit()
        path = str(self.root / "repo-worktrees" / "future-card")
        service.branch_worktree(self.repo, "card/future", path, "master")
        self.assertEqual(self.finish(), {})

    def test_host_ref_transaction_updates_only_written_refs_in_guard_snapshot(self):
        """Transakcje targetprep/basegeneration nie maskują obcego zapisu ani refu verify."""
        flowgit = FlowGit()
        for ref in ("refs/heads/verified", "refs/heads/deleted", "refs/heads/moved"):
            gitrepos.git(self.repo, "update-ref", ref, self.original)
        self.begin()
        tip = self.commit("two")
        gitrepos.git(self.repo, "update-ref", "refs/heads/verified", tip)
        gitrepos.git(self.repo, "tag", "foreign")
        result = flowgit.run(self.repo, "update-ref", "--stdin", input_text=(
            f"start\noption no-deref\nverify refs/heads/verified {tip}\n"
            f"create refs/tide/merge-targets/test {self.original}\n"
            f"create refs/heads/new-target {self.original}\n"
            f"update refs/heads/moved {tip} {self.original}\n"
            f"delete refs/heads/deleted {self.original}\nprepare\ncommit\n"))
        self.assertTrue(result.ok, result.err)
        report = self.finish()
        self.assertEqual(report["refs"], ["refs/heads/ticket/two", "refs/heads/verified", "refs/tags/foreign"])
        self.assertFalse(report["errors"])

    def test_host_ref_transactions_preserve_apostrophes_and_git_c_quoting(self):
        self.begin()
        cases = (
            ((), "refs/heads/release'2026", "refs/heads/release'2026"),
            ((), "refs/heads/release'2026'", "refs/heads/release'2026'"),
            ((), r'"refs/heads/c-\047\303\263"', "refs/heads/c-'ó"),
            ((), r'"refs/heads/double-\"quote"', 'refs/heads/double-"quote'),
            (("-z",), 'refs/heads/nul\'"2026', 'refs/heads/nul\'"2026'),
        )
        for flags, wire_ref, ref in cases:
            with self.subTest(ref=ref):
                text = (f"create {wire_ref}\0{self.original}\0" if flags
                        else f"create {wire_ref} {self.original}\n")
                result = FlowGit().run(self.repo, "update-ref", "--stdin", *flags, input_text=text)
                self.assertTrue(result.ok, result.err)
                self.assertEqual(gitrepos.git(self.repo, "rev-parse", ref).strip(), self.original)
        self.assertEqual(self.finish(), {})

    def test_host_text_transaction_accepts_c_quoted_object_expression(self):
        self.begin()
        result = FlowGit().run(self.repo, "update-ref", "--stdin", input_text=(
            'create refs/heads/by-message ":/add initial"\n'))
        self.assertTrue(result.ok, result.err)
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "refs/heads/by-message").strip(), self.original)
        self.assertEqual(self.finish(), {})

    def test_host_ref_transaction_refuses_unknown_or_ambiguous_input_before_git(self):
        self.begin()
        cases = (
            ((), None),
            ((), 'create "refs/heads/bad\\x27" HEAD\n'),
            ((), 'create "refs/heads/bad\\47" HEAD\n'),
            ((), 'create "refs/heads/bad\\000suffix" HEAD\n'),
            ((), 'create "refs/heads/bad HEAD\n'),
            ((), 'create "refs/heads/ok"suffix HEAD\n'),
            ((), 'create refs/heads/ok HEAD extra\n'),
            ((), 'create refs/heads/ok\n'),
            ((), 'create  refs/heads/ok HEAD\n'),
            ((), 'option future-option\n'),
            ((), 'start extra\n'),
            ((), '"create" refs/heads/ok HEAD\n'),
            ((), 'future-command refs/heads/ok HEAD\n'),
            ((), 'create refs/heads/ok HEAD\0'),
            ((), 'create refs/heads/ok HEAD\n\n'),
            ((), 'update HEAD HEAD\n'),
            (("-z",), 'update refs/heads/ok\0HEAD\0'),
            (("-z",), 'create refs/heads/ok\0HEAD'),
            (("-z",), 'create refs/heads/ok HEAD\0'),
            (("-z",), 'start extra\0'),
        )
        for flags, text in cases:
            with self.subTest(flags=flags, text=text), mock.patch.object(Git, "run") as run:
                with self.assertRaises(agentcmd.StepError):
                    FlowGit().run(self.repo, "update-ref", "--stdin", *flags, input_text=text)
                run.assert_not_called()
        self.assertEqual(self.finish(), {})

    def test_host_nul_transaction_updates_only_written_refs(self):
        for name in ("moved", "deleted", "verified"):
            gitrepos.git(self.repo, "update-ref", "refs/heads/" + name, self.original)
        self.begin()
        tip = self.commit("two")
        gitrepos.git(self.repo, "update-ref", "refs/heads/verified", tip)
        result = FlowGit().run(self.repo, "update-ref", "--stdin", "-z", input_text=(
            f"start\0option no-deref\0verify refs/heads/verified\0{tip}\0"
            f"create refs/heads/new\0{tip}\0"
            f"update refs/heads/moved\0{tip}\0\0"
            "delete refs/heads/deleted\0\0prepare\0commit\0"))
        self.assertTrue(result.ok, result.err)
        report = self.finish()
        self.assertEqual(report["refs"], ["refs/heads/ticket/two", "refs/heads/verified"])
        self.assertFalse(report["errors"])

    def test_target_preparation_and_base_generation_are_legal_during_another_cards_step(self):
        from tide_daemon.cardflow import targetprep
        from tide_daemon.cardflow.basegeneration import BaseGeneration
        flowgit = FlowGit()
        remote = "refs/remotes/team/release"
        # Ref zdalny istnieje już przed migawką — nie jest częścią tworzenia celu.
        gitrepos.git(self.repo, "update-ref", remote, self.original)
        self.begin(card="K-99")
        op = {"key": "other-card", "repo": self.repo, "targetRef": "refs/heads/release",
              "targetCommitBefore": self.original, "targetWorktree": "/unused",
              "targetPreparation": {"createRef": True, "createWorktree": False,
                                    "remoteRef": remote, "remoteCommit": self.original}}
        targetprep.provision(flowgit, op, [], lambda *args, **kw: None)
        engine = SimpleNamespace(git=flowgit, journal=Journal(str(self.root / "board"),
                                                            mirror=str(self.root / "mirror")))
        generation = BaseGeneration(engine)
        intent = {"repo": self.repo, "key": "generation", "lockReason": "tide-base-change:test",
                  "oldBase": {}, "newBase": {}, "generation": [], "previousBaseVersion": 1, "baseVersion": 2}
        row = {"branch": "card/new-generation", "base": self.original, "oldCommit": self.original,
               "ownerRef": "refs/tide/base-owners/test", "archiveRef": "refs/tide/base-archives/test",
               "worktree": str(self.root / "repo-worktrees" / "generation")}
        generation.prepare("K-38", intent, row)
        self.assertEqual(self.finish(card="K-99"), {})
        self.begin("two", card="K-38")
        self.assertEqual(self.finish("two", card="K-38"), {})

    def test_host_merge_and_worktree_cleanup_do_not_trigger_older_guard(self):
        config = self.common / "worktrees/two/config.worktree"
        config.write_text("")
        self.begin()
        self.begin("two")
        tip = self.commit("two")
        self.assertEqual(self.finish("two"), {})
        service = FlowGit()
        with lock_for(self.repo):
            self.assertTrue(service.run(self.repo, "update-ref", "refs/heads/master", tip, self.original).ok)
            self.assertTrue(service.run(self.repo, "worktree", "remove", self.worktrees["two"]).ok)
        self.assertEqual(self.finish(), {})
        self.assertEqual(gitrepos.git(self.repo, "rev-parse", "master").strip(), tip)

    def test_foreign_symbolic_ref_target_and_new_symbolic_ref_are_only_reported(self):
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/alias", "refs/heads/master")
        self.begin()
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/alias", "refs/heads/ticket/one")
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/tmp-alias", "refs/heads/master")
        result = self.finish()
        self.assertEqual(result["refs"], ["refs/heads/alias", "refs/heads/tmp-alias"])
        self.assertFalse(result["errors"])
        self.assertEqual(gitrepos.git(self.repo, "symbolic-ref", "refs/heads/alias").strip(), "refs/heads/ticket/one")
        self.assertIn("tmp-alias", gitrepos.git(self.repo, "for-each-ref"))

    def test_symbolic_alias_to_ticket_is_not_a_violation_when_ticket_advances(self):
        gitrepos.git(self.repo, "symbolic-ref", "refs/heads/alias", "refs/heads/ticket/one")
        self.begin()
        self.commit("one")
        self.begin("two")
        self.assertEqual(self.finish(), {})
        self.assertEqual(self.finish("two"), {})

    def test_changed_worktree_pointer_is_restored_without_following_symlink(self):
        pointer = Path(self.worktrees["one"]) / ".git"
        original = pointer.read_bytes()
        victim = self.root / "pointer-victim"
        victim.write_text("nie ruszać")
        self.begin()
        pointer.unlink()
        pointer.symlink_to(victim)
        result = self.finish()
        self.assertEqual(result["files"], ["worktree/.git"])
        self.assertEqual(pointer.read_bytes(), original)
        self.assertEqual(victim.read_text(), "nie ruszać")

    def test_detached_or_switched_head_is_restored_to_ticket(self):
        for args in (("checkout", "--detach", "-q"), ("symbolic-ref", "HEAD", "refs/heads/master")):
            with self.subTest(args=args):
                self.begin()
                gitrepos.git(self.worktrees["one"], *args)
                self.assertTrue(self.finish()["head"])
                self.assertEqual(gitrepos.git(self.worktrees["one"], "symbolic-ref", "HEAD").strip(),
                                 "refs/heads/ticket/one")

    def test_protected_files_content_modes_new_files_and_worktree_configs(self):
        paths = ["hooks/pre-commit", "info/attributes", "config.worktree", "worktrees/one/config.worktree"]
        for name in paths:
            p = self.common / name
            p.write_text("oryginał\n")
            p.chmod(0o755)
        originals = {p: (self.common / p).read_bytes() for p in paths + ["config"]}
        self.begin()
        for p in paths + ["config"]:
            (self.common / p).write_text("uszkodzona konfiguracja [\n")
            (self.common / p).chmod(0o600)
        (self.common / "hooks/new-hook").write_text("nowy hak")
        (self.common / "info/new-file").write_text("nowy plik")
        (self.common / "worktrees/two/config.worktree").write_text("nowy config")
        result = self.finish()
        self.assertFalse(result["errors"])
        self.assertEqual(set(result["files"]), {"hooks", "info", "config", "config.worktree",
                                               "worktrees/one/config.worktree", "worktrees/two/config.worktree"})
        for p, content in originals.items():
            self.assertEqual((self.common / p).read_bytes(), content)
        self.assertEqual((self.common / paths[0]).stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.common / "hooks/new-hook").exists())
        self.assertFalse((self.common / "info/new-file").exists())
        self.assertFalse((self.common / "worktrees/two/config.worktree").exists())

    def test_symlink_and_hardlink_substitution_does_not_write_to_target(self):
        victim = self.root / "victim"
        victim.write_text("nie ruszać")
        self.begin()
        config = self.common / "config"
        config.unlink()
        config.symlink_to(victim)
        attributes = self.common / "info/attributes"
        os.link(victim, attributes)
        result = self.finish()
        self.assertFalse(result["errors"])
        self.assertEqual(victim.read_text(), "nie ruszać")
        self.assertFalse(config.is_symlink())
        self.assertFalse(attributes.exists())

    def test_poisoned_peer_is_not_saved_as_a_new_baseline(self):
        for kind in ("hook", "ref"):
            with self.subTest(kind=kind):
                self.begin()
                if kind == "hook":
                    (self.common / "hooks/new-hook").write_text("złośliwy hak")
                else:
                    gitrepos.git(self.repo, "tag", "evil")
                with self.assertRaises(agentcmd.StepError):
                    self.begin("two")
                self.assertTrue(self.finish())

    def test_trusted_runner_config_creation_updates_older_snapshot(self):
        self.begin()

        def prepare():
            (self.common / "worktrees/two/config.worktree").write_text("")

        self.begin("two", prepare=prepare)
        self.assertEqual(self.finish(), {})
        self.assertEqual(self.finish("two"), {})

    def test_missing_snapshot_fails_closed(self):
        self.assertTrue(self.finish()["errors"])

    def test_engine_records_violation_and_forces_error_even_for_forced_finish(self):
        self.begin()
        tip = self.commit("one")
        gitrepos.git(self.repo, "update-ref", "refs/heads/master", tip)
        engine = FlowEngine.__new__(FlowEngine)
        engine.journal = mock.Mock()
        engine.journal.read.return_value = []
        engine._queue_notices = mock.Mock()
        engine._release_local = mock.Mock()
        engine._release_box = mock.Mock()
        engine._codex_broken_mcp = mock.Mock(return_value=False)
        engine.tmp_root = str(self.root / "tmp")
        engine.procs, engine.watch = {}, {}
        step = dict(step="one", runner="box", repo=self.repo, branch="ticket/one", kind="ticket")
        engine._finish("K-38", step, forced={"result": "stopped", "reason": "timeout"})
        events = engine.journal.append.call_args_list
        guard = next(c for c in events if c.args[1] == "refs.guard")
        done = next(c for c in events if c.args[1] == "step.finished")
        self.assertEqual(guard.kwargs["refs"], ["refs/heads/master"])
        self.assertEqual((done.kwargs["result"], done.kwargs["reason"]), ("error", "refs.guard"))

    def test_finished_step_retries_original_snapshot_through_recovery_and_tick(self):
        for failure in ("read", "cas"):
            with self.subTest(failure=failure):
                self.begin()
                engine = FlowEngine.__new__(FlowEngine)
                engine.journal = Journal(str(self.root / ("board-" + failure)))
                engine.journal.append("K-38", "card.started", repo=self.repo, project="proj")
                engine.journal.append("K-38", "step.started", step="one", runner="box", repo=self.repo,
                                      branch="ticket/one", kind="ticket", ticket="PT-1",
                                      box="proj", boxSlug="proj")
                for name in ("_queue_notices", "_release_local", "_release_box", "_recover_started_steps",
                             "_tick_card", "_guard_card"):
                    setattr(engine, name, mock.Mock())
                engine._codex_broken_mcp = mock.Mock(return_value=False)
                engine.tmp_root = str(self.root / "tmp")
                engine.settings = str(self.root / "cardflow.json")
                engine.procs, engine.watch = {}, {}
                engine._local_claims, engine._warnings, engine._worktree_heads = {}, {}, {}
                engine.box_proxy = mock.Mock()
                engine.store = mock.Mock(root=str(self.root / "store"))
                st = fold(engine.journal.read("K-38"))
                self.assertEqual(st.process_identities["one"].state, "ok")
                step = st.steps["one"]
                gitrepos.commit(self.worktrees["one"], "engine-" + failure)
                gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
                hook = self.common / "hooks/new-hook"
                hook.write_text("obcy hak")
                real_node, real_run = refguard._node, Git.run

                def unreadable(parent, name):
                    if name == "hooks":
                        raise PermissionError("chwilowy brak odczytu")
                    return real_node(parent, name)

                def cas_error(git, cwd, *args, **kw):
                    if "update-ref" in args and "refs/heads/ticket/one" in args:
                        raise agentcmd.StepError("chwilowy błąd CAS")
                    return real_run(git, cwd, *args, **kw)

                patch = (mock.patch.object(refguard, "_node", side_effect=unreadable) if failure == "read"
                         else mock.patch.object(Git, "run", cas_error))
                # Odczyt ponawiamy także bez proxy; CAS sprawdza ścieżkę z pamięcią podpisu sieci.
                with mock.patch.object(boxstep, "proxy_of", return_value=None if failure == "read" else engine.box_proxy):
                    with patch:
                        engine._finish("K-38", step, forced={"result": "done"})
                        boxstep.recover_network(engine)
                    self.assertTrue(refguard._active(self.repo))
                    self.assertEqual(refguard.other_cards(self.repo, "K-99"), ["K-38"])
                    # Nowy krok nie może przyjąć obcego haka/refu jako punktu przywracania.
                    with self.assertRaises(agentcmd.StepError):
                        self.begin("two")
                    # Awaria sprzątania sieci nie może pominąć niezależnej naprawy repozytorium.
                    with mock.patch.object(boxstep, "_release_resources", side_effect=OSError("sprzątanie sieci")):
                        engine.tick()
                self.assertFalse(hook.exists())
                self.assertEqual(gitrepos.git(self.repo, "rev-parse", "ticket/one").strip(), self.original)
                self.assertEqual(refguard._active(self.repo), [])
                events = engine.journal.read("K-38")
                ended = [e for e in events if e["type"] == "step.finished"]
                self.assertEqual(len(ended), 1)
                self.assertEqual((ended[0]["result"], ended[0]["reason"]), ("error", "refs.guard"))
                repaired = [e for e in events if e["type"] == "refs.guard"][-1]
                self.assertEqual(repaired["errors"], [])
                self.assertEqual(repaired["files"], ["hooks"])
                self.assertEqual(repaired["refs"], ["refs/heads/ticket/one"])
                self.begin("two")
                self.assertEqual(self.finish("two"), {})

    def restarting_engine(self, board="board"):
        engine = FlowEngine.__new__(FlowEngine)
        engine.journal = Journal(str(self.root / board))
        engine.journal.append("K-38", "card.started", project="proj", repo=self.repo)
        engine.procs, engine.watch = {}, {}
        engine._box_network_errors = {}
        engine._unit_state = mock.Mock(return_value="inactive")
        for method in ("_queue_notices", "_release_local", "_release_box"):
            setattr(engine, method, mock.Mock())
        engine._codex_broken_mcp = mock.Mock(return_value=False)
        engine.tmp_root = str(self.root / "tmp")
        return engine

    def test_restart_checks_process_exited_before_start_ack_and_preserves_foreign_refs(self):
        for code in (2, 0):
            for delete_k60 in (False, True):
                with self.subTest(code=code, delete_k60=delete_k60):
                    for name in ("55", "60"):
                        gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-" + name, self.original)
                    self.begin()
                    engine = self.restarting_engine(f"board-{code}-{delete_k60}")
                    step_dir = self.root / f"step-{code}-{delete_k60}"
                    (step_dir / "in").mkdir(parents=True)
                    intent = engine.journal.append(
                        "K-38", "intent", action="step-start", key="one", runner="box", repo=self.repo,
                        box="proj", boxSlug="proj",
                        kind="ticket", ticket="PT-1", branch="ticket/one", worktree=self.worktrees["one"],
                        stepDir=str(step_dir))
                    gitrepos.commit(self.worktrees["two"], f"restart-{code}-{delete_k60}")
                    tip = gitrepos.git(self.repo, "rev-parse", "ticket/two").strip()
                    gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-55", tip)
                    if delete_k60:
                        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/card/k-60")
                    else:
                        gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-60", tip)
                    gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
                    # Wrapper zakończył proces, ale usługa nie zapisała jeszcze potwierdzenia startu.
                    (step_dir / "in" / stepstop.EXIT_CODE_FILE).write_text(f"{code}\n")
                    with mock.patch.object(boxstep, "proxy_of", return_value=None), \
                            mock.patch.object(refguard, "cancel", wraps=refguard.cancel) as cancel:
                        engine._reconcile("K-38", intent)
                        st = fold(engine.journal.read("K-38"))
                        self.assertTrue(st.results[("step-start", "one")]["ok"])
                        engine._poll("K-38", st.steps["one"])
                        boxstep.recover_network(engine)
                    cancel.assert_not_called()
                    refs = refguard._refs(self.repo)
                    self.assertEqual(refs["refs/heads/ticket/one"]["oid"], self.original)
                    self.assertEqual(refs["refs/heads/card/k-55"]["oid"], tip)
                    self.assertEqual(refs.get("refs/heads/card/k-60"), None if delete_k60 else refs["refs/heads/card/k-55"])
                    st = fold(engine.journal.read("K-38"))
                    self.assertEqual(st.finished["one"]["exit"], code)
                    self.assertEqual(st.finished["one"]["reason"], "refs.guard")
                    guards = [e for e in engine.journal.read("K-38") if e["type"] == "refs.guard"]
                    self.assertEqual(len(guards), 1)
                    self.assertFalse(guards[0]["errors"])
                    self.assertTrue({"refs/heads/ticket/one", "refs/heads/card/k-55",
                                     "refs/heads/card/k-60"}.issubset(guards[0]["refs"]))
                    self.assertEqual(refguard.other_cards(self.repo, "K-99"), [])

    def test_restart_without_execution_evidence_keeps_intent_and_snapshot(self):
        self.begin()
        engine = self.restarting_engine()
        intent = engine.journal.append("K-38", "intent", action="step-start", key="one", runner="box",
                                       repo=self.repo, ticket="PT-1", branch="ticket/one")
        with mock.patch.object(boxstep, "proxy_of", return_value=None), \
                mock.patch.object(refguard, "finish") as finish, mock.patch.object(refguard, "cancel") as cancel:
            for state in ("unknown", "inactive"):
                engine._unit_state.return_value = state
                engine._reconcile("K-38", intent)
                boxstep.recover_network(engine)
        self.assertNotIn(("step-start", "one"), fold(engine.journal.read("K-38")).results)
        finish.assert_not_called()
        cancel.assert_not_called()
        engine._release_box.assert_not_called()
        self.assertEqual(refguard.other_cards(self.repo, "K-99"), ["K-38"])

    def test_recovery_keeps_snapshot_of_historical_failed_start_without_execution_evidence(self):
        self.begin()
        engine = self.restarting_engine()
        step_dir = self.root / "step"
        (step_dir / "in").mkdir(parents=True)
        engine.journal.append("K-38", "intent", action="step-start", key="one", runner="box", repo=self.repo,
                              ticket="PT-1", branch="ticket/one", stepDir=str(step_dir))
        engine.journal.append("K-38", "result", action="step-start", key="one", ok=False)
        with mock.patch.object(boxstep, "proxy_of", return_value=None):
            boxstep.recover_network(engine)
        self.assertEqual(refguard.other_cards(self.repo, "K-99"), ["K-38"])
        tip = self.commit("two")
        gitrepos.git(self.repo, "update-ref", "refs/heads/card/k-55", tip)
        gitrepos.git(self.repo, "update-ref", "-d", "refs/heads/ticket/one")
        (step_dir / "in" / stepstop.EXIT_CODE_FILE).write_text("2\n")
        with mock.patch.object(boxstep, "proxy_of", return_value=mock.Mock()), \
                mock.patch.object(refguard, "cancel", wraps=refguard.cancel) as cancel:
            boxstep.recover_network(engine)
            boxstep.recover_network(engine)
        cancel.assert_not_called()
        refs = refguard._refs(self.repo)
        self.assertEqual(refs["refs/heads/ticket/one"]["oid"], self.original)
        self.assertEqual(refs["refs/heads/card/k-55"]["oid"], tip)
        self.assertEqual(refguard.other_cards(self.repo, "K-99"), [])
        guards = [e for e in engine.journal.read("K-38") if e["type"] == "refs.guard"]
        self.assertEqual(len(guards), 1)
        self.assertFalse(guards[0]["errors"])


class RunnerGuardTest(BoxRunnerCase):
    def demo(self, runner, *options):
        start = runner.start

        def start_in_temp(step, **kw):
            return start(replace(step, tmp_root=os.path.join(self.tmp, "tmp")), **kw)

        with mock.patch.object(br, "BoxRunner", return_value=runner), \
                mock.patch.object(runner, "start", side_effect=start_in_temp), \
                mock.patch("tide_daemon.box.settings.load", return_value=self.settings), \
                mock.patch("tide_daemon.box.trialgate.require_public"), \
                contextlib.redirect_stdout(io.StringIO()):
            # Historyczny adapter refguard działa tu tylko na własnym repozytorium
            # i atrapie Incusa. Publiczną odmowę sprawdzamy bez tej podmiany poniżej.
            return br._demo(["proj", "--worktree", self.worktree, "--branch", "feat/pt-1", *options])

    def test_public_demo_and_retry_refuse_before_runner_or_refguard(self):
        for options in (("--", "true"), ("--finish-guard", "demo-20261106120000-abcd1234")):
            with self.subTest(options=options), \
                    mock.patch.object(br, "BoxRunner") as runner, \
                    mock.patch.object(refguard, "begin") as begin, \
                    mock.patch.object(refguard, "finish") as finish, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(br._demo(["proj", "--worktree", self.worktree,
                                           "--branch", "feat/pt-1", *options]), 3)
                runner.assert_not_called()
                begin.assert_not_called()
                finish.assert_not_called()
        self.assertFalse(os.path.exists(refguard._root(self.repo)))

    def test_clean_demo_releases_guard(self):
        runner = self.runner()
        runner.unit_state = mock.Mock(return_value="inactive")
        runner._popen = mock.Mock(return_value=mock.Mock(wait=mock.Mock(return_value=0)))
        self.assertEqual(self.demo(runner, "--", "true"), 0)
        self.assertEqual(refguard._active(self.repo), [])

    def test_demo_reports_foreign_ref_without_restoring_and_returns_error(self):
        runner = self.runner()
        runner.unit_state = mock.Mock(return_value="inactive")

        def ended():
            gitrepos.git(self.repo, "tag", "evil")
            return 0

        runner._popen = mock.Mock(return_value=mock.Mock(wait=mock.Mock(side_effect=ended)))
        self.assertEqual(self.demo(runner, "--", "true"), 1)
        self.assertIn("refs/tags/evil", gitrepos.git(self.repo, "for-each-ref", "--format=%(refname)"))
        self.assertEqual(refguard._active(self.repo), [])

    def test_demo_waits_for_confirmed_unit_end_before_checking_guard(self):
        for state in ("active", "unknown"):
            with self.subTest(state=state):
                runner = self.runner()
                runner.unit_state = mock.Mock(return_value=state)
                runner._popen = mock.Mock(return_value=mock.Mock(wait=mock.Mock(return_value=0)))
                with mock.patch.object(refguard, "finish", wraps=refguard.finish) as finish:
                    self.assertEqual(self.demo(runner, "--", "true"), 1)
                    saved = refguard._active(self.repo)[0][1]
                    self.assertEqual(self.demo(runner, "--finish-guard", saved["step"]), 1)
                    finish.assert_not_called()
                    runner.unit_state.return_value = "inactive"
                    self.assertEqual(self.demo(runner, "--finish-guard", saved["step"]), 0)
                self.assertEqual(refguard._active(self.repo), [])
                runner._popen.assert_called_once()

    def test_demo_retries_failed_repair_without_running_command_again(self):
        runner = self.runner()
        runner.unit_state = mock.Mock(return_value="inactive")
        hook = Path(self.repo) / ".git/hooks/new-hook"

        def ended():
            hook.write_text("obcy hak")
            return 0

        real_node = refguard._node

        def unreadable(parent, name):
            if name == "hooks" and hook.exists():
                raise PermissionError("chwilowy brak odczytu")
            return real_node(parent, name)

        runner._popen = mock.Mock(return_value=mock.Mock(wait=mock.Mock(side_effect=ended)))
        with mock.patch.object(refguard, "_node", side_effect=unreadable):
            self.assertEqual(self.demo(runner, "--", "true"), 1)
        saved = refguard._active(self.repo)[0][1]
        self.assertTrue(hook.exists())
        self.assertEqual(self.demo(runner, "--finish-guard", saved["step"]), 1)
        self.assertFalse(hook.exists())
        self.assertEqual(refguard._active(self.repo), [])
        runner._popen.assert_called_once()

    def test_switched_head_is_rejected_before_prepare_or_launch(self):
        gitrepos.git(self.worktree, "symbolic-ref", "HEAD", "refs/heads/master")
        runner = self.runner(card="K-38")
        with mock.patch.object(runner, "ensure_devices") as prepare:
            with self.assertRaisesRegex(agentcmd.StepError, "HEAD"):
                runner.start(self.step())
        prepare.assert_not_called()
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(refguard._active(self.repo), [])

    def test_missing_trusted_branch_fails_closed(self):
        with self.assertRaisesRegex(agentcmd.StepError, "zaufanej gałęzi"):
            self.runner().start(self.step(branch=""))
        self.assertEqual(self.popen_calls, [])

    def test_snapshot_exists_before_process_launch(self):
        def launch(*args, **kw):
            self.assertTrue(list(refguard._active(self.repo)))
            return mock.Mock()

        self.runner(card="K-38", popen=launch).start(self.step())
        gitrepos.git(self.repo, "tag", "evil")
        self.assertEqual(refguard.finish(self.repo, "K-38", "s1")["refs"], ["refs/tags/evil"])

    def test_failed_launch_unregisters_snapshot(self):
        with self.assertRaises(OSError):
            self.runner(card="K-38", popen=mock.Mock(side_effect=OSError("start"))).start(self.step())
        self.assertEqual(list(refguard._active(self.repo)), [])


class HostGitTest(unittest.TestCase):
    def test_shared_argv_protects_plain_flow_and_step_git(self):
        from tide_daemon.cardflow.gitops import FlowGit
        results = []

        def run(argv, **kw):
            results.append(argv)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=run):
            Git().run("/repo", "status")
            FlowGit().run("/repo", "update-ref", "refs/heads/a", "abc")
            git = stepstop.StepGit("/repo", "/repo/.git/worktrees/a", "/worktree", "/step")
            with mock.patch.object(git, "sandbox", return_value=[]):
                git.run("commit", "-m", "test")
        self.assertEqual(len(results), 3)
        for argv in results:
            self.assertEqual(tuple(argv[1:5]), HOST_OPTIONS)
