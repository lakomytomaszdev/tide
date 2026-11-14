"""PT-480 — jeden manifest ścieżek kroku (`agentcmd.path_manifest`) jako jedyne źródło granicy piaskownicy kroku.

- argumenty piaskownicy Claude Code (`allowWrite`, `--add-dir`, `denyWrite`, `denyRead`) i Codexa (`--cd`,
  `writable_roots`, wykluczenia `/tmp`) dają te same zbiory zapisu, zakazu zapisu i ukrycia co manifest,
- zawartość: `out` innych kroków bez zapisu, `out` wcześniejszego kroku ticketu i wykonawcy tylko do odczytu,
  Tablica, dziennik, `in`, konfiguracja agentów bez zapisu, haki i konfiguracja gita tylko do odczytu,
  `.git/worktrees` innych kroków ukryte,
- dowiązanie na każdym poziomie ścieżek → manifest nieustalony z powodem,
- podmiana katalogu po złożeniu kroku, a przed startem → odmowa startu, bez zapisu przez dowiązanie,
- `git worktree prune` i `git gc` odrzucane w każdym kroku.
Prawdziwy program agenta ani systemd-run nie startują.
"""
import json
import os
import shutil

from tide_daemon.board import commands
from tide_daemon.tasks import agentcmd
from . import gitrepos
from .test_agentcmd import BackgroundBase


def claude_sets(test, bg):
    """Zbiory ścieżek z argumentów piaskownicy Claude Code: zapis (katalog roboczy, `allowWrite`, `--add-dir` bez
    `denyWrite`, katalog tymczasowy), zakaz zapisu (`denyWrite`) i ukrycie (`denyRead` poza `~`)."""
    fs = test.settings(bg)["sandbox"]["filesystem"]
    deny = fs.get("denyWrite", [])
    adds = [bg.argv[i + 1] for i, arg in enumerate(bg.argv) if arg == "--add-dir"]
    rw = {bg.workdir or bg.cwd, *fs["allowWrite"], bg.env["CLAUDE_CODE_TMPDIR"],
          *(d for d in adds if not any(agentcmd._within(d, x) for x in deny))}
    return rw, set(deny), set(fs["denyRead"]) - {"~"}


def codex_sets(bg):
    """Zbiory z argv Codexa: `--cd`, `writable_roots` i domyślne `/tmp`/`$TMPDIR`, jeśli nie wykluczone."""
    roots = next(json.loads(a.split("=", 1)[1]) for a in bg.argv if a.startswith("sandbox_workspace_write.writable_roots="))
    rw = {bg.argv[bg.argv.index("--cd") + 1], *roots}
    if "sandbox_workspace_write.exclude_slash_tmp=true" not in bg.argv:
        rw.add("/tmp")
    if "sandbox_workspace_write.exclude_tmpdir_env_var=true" not in bg.argv and bg.env.get("TMPDIR"):
        rw.add(bg.env["TMPDIR"])
    return rw


class ManifestBase(BackgroundBase):
    def setUp(self):
        super().setUp()
        # Drugi krok tej samej karty: własny worktree, wpis `.git/worktrees/pt-2` i wynik review w `out`.
        self.other = os.path.join(self.home, "proj-wt", "pt-2")
        gitrepos.git(self.repo, "worktree", "add", "-q", "-b", "feat/pt-2", self.other)
        self.other_out = os.path.join(self.other + ".step", "out")
        os.makedirs(self.other_out)
        with open(os.path.join(self.other_out, "result.json"), "w", encoding="utf-8") as fh:
            fh.write('{"result": "done", "verdict": "APPROVE"}\n')
        # Wcześniejszy krok tego ticketu (wznowienie) i krok wykonawcy (dla recenzenta).
        self.prev_out = os.path.join(self.home, "proj-wt", "pt-1.resume-1.step", "out")
        self.author_out = os.path.join(self.home, "proj-wt", "pt-1.author.step", "out")
        for folder in (self.prev_out, self.author_out):
            os.makedirs(folder)
        self.git = os.path.join(self.repo, ".git")

    def base(self, **extra):
        return dict(self.step().base_env, **extra)


class ManifestIsTheOnlySourceTest(ManifestBase):
    def test_claude_sandbox_arguments_are_exactly_the_manifest(self):
        reads = os.path.join(self.root, "dane")
        os.makedirs(reads)
        for kw in ({}, {"context_dirs": (self.prev_out,), "read_dirs": (reads,)}, {"write_out_only": True}):
            with self.subTest(kw):
                bg = agentcmd.background(self.step(**kw))
                manifest = bg.manifest
                rw, ro, hidden = claude_sets(self, bg)
                implicit = {*manifest.areas.implicit, manifest.git_dir}
                self.assertEqual(rw, set(manifest.read_write) - implicit)
                self.assertTrue(implicit <= set(manifest.read_write))
                self.assertEqual(ro, set(manifest.read_only))
                self.assertEqual(hidden, set(manifest.hidden))
                self.assertEqual(manifest.implicit_read_only, (os.path.join(self.git, "worktrees"),))

    def test_codex_sandbox_arguments_are_exactly_the_manifest(self):
        reads = os.path.join(self.root, "dane")
        os.makedirs(reads)
        for kw in ({}, {"read_dirs": (reads,), "context_dirs": (self.prev_out,)}, {"write_out_only": True}):
            with self.subTest(kw):
                bg = agentcmd.background(self.step("codex", codex_mcp=(), **kw))
                manifest = bg.manifest
                self.assertEqual(codex_sets(bg), set(manifest.read_write))
                self.assertEqual(manifest.hidden, ())
                self.assertTrue(manifest.limits)          # Codex nie ukrywa odczytu — jawnie zapisane
                roots = codex_sets(bg) - set(manifest.tmp)    # katalog testu leży w /tmp — por. ManifestContentsTest
                for path in manifest.read_only:
                    self.assertFalse(any(agentcmd._within(path, root) for root in roots), path)

    def test_write_areas_of_contract_approval_come_from_the_same_manifest(self):
        for tool in ("claude", "codex"):
            step = self.step(tool, codex_mcp=())
            self.assertEqual(agentcmd.step_write_areas(step), agentcmd.background(step).manifest.areas)
            self.assertEqual(agentcmd.step_path_manifest(step).read_write, agentcmd.background(step).manifest.read_write)


class ManifestContentsTest(ManifestBase):
    """Zakazy sprawdzamy bez wydzielonych katalogów tymczasowych (`writable(…, tmp=False)`): katalog testu leży
    w `/tmp`, a domyślny zapis `/tmp` Codexa objąłby go w całości. Katalogi tmp kroku są osobnym polem manifestu."""

    def denied(self, m, path):
        return not m.writable(path, tmp=False)

    def manifests(self, **kw):
        for tool in ("claude", "codex"):
            yield tool, agentcmd.background(self.step(tool, codex_mcp=(), **kw)).manifest

    def test_writes_only_own_worktree_out_and_git_entry(self):
        for tool, m in self.manifests():
            with self.subTest(tool):
                out = os.path.join(self.step_dir, "out")
                for path in (self.worktree, os.path.join(self.worktree, "a.txt"), out, os.path.join(out, "note.md"),
                             self.gitdir, os.path.join(self.gitdir, "index")):
                    self.assertTrue(m.writable(path), path)
                for name in ("objects", "refs", "logs"):
                    self.assertTrue(m.writable(os.path.join(self.git, name, "x")), name)
                # Rodzic worktree (`<repo>-worktrees`) nie jest w zapisie — otworzyłby `out` innych kroków.
                self.assertTrue(self.denied(m, os.path.dirname(self.worktree)))
                self.assertNotIn(os.path.dirname(self.worktree), m.read_write)

    def test_other_steps_out_and_review_result_are_not_writable(self):
        for tool, m in self.manifests():
            with self.subTest(tool):
                for path in (self.other_out, os.path.join(self.other_out, "result.json"), self.other,
                             self.prev_out, self.author_out):
                    self.assertTrue(self.denied(m, path), path)

    def test_earlier_step_and_author_out_are_read_only(self):
        for tool, m in self.manifests(context_dirs=(self.prev_out, self.author_out)):
            with self.subTest(tool):
                for path in (self.prev_out, self.author_out):
                    self.assertIn(path, m.read_only)
                    self.assertTrue(self.denied(m, os.path.join(path, "result.json")))

    def test_board_journal_input_and_agent_config_are_outside_rw(self):
        board = os.path.join(self.home, "DevWorkspace", "tide-board")
        journal = os.path.join(self.home, ".local", "state", "tide", "cardflow", "x", "K-1.jsonl")
        for tool, m in self.manifests():
            with self.subTest(tool):
                for path in (board, os.path.join(board, "cards", "K-1.md"), journal,
                             os.path.join(self.step_dir, "in"), os.path.join(self.step_dir, "in", "prompt.txt"),
                             *(os.path.join(self.home, rel, "settings.json")
                               for rel in (".claude", ".codex", ".config", ".local/share/tide"))):
                    self.assertTrue(self.denied(m, path), path)
                self.assertEqual(m.home, self.home)
                self.assertTrue(self.denied(m, self.home))
                self.assertTrue(m.tmp)

    def test_git_hooks_and_config_are_read_only(self):
        for tool, m in self.manifests():
            with self.subTest(tool):
                for name in ("hooks", "config"):
                    self.assertIn(os.path.join(self.git, name), m.read_only)
                    self.assertTrue(self.denied(m, os.path.join(self.git, name, "pre-commit")
                                                if name == "hooks" else os.path.join(self.git, name)))

    def test_other_steps_git_entries_are_hidden_from_claude(self):
        m = agentcmd.background(self.step()).manifest
        self.assertEqual(m.hidden, (os.path.join(self.git, "worktrees", "pt-2"),))
        self.assertNotIn(self.gitdir, m.hidden)
        self.assertFalse(m.writable(os.path.join(self.git, "worktrees", "pt-2", "HEAD")))
        self.assertFalse(m.writable(os.path.join(self.git, "worktrees")))

    def test_board_covered_by_the_write_boundary_is_refused(self):
        """Tablica w `out` byłaby zapisywalna — manifest nieustalony, nie cichy zapis."""
        for tool in ("claude", "codex"):
            with self.subTest(tool):
                step = self.step(tool, codex_mcp=(), base_env=self.base(TIDE_BOARD_DIR=os.path.join(self.worktree, "b")))
                with self.assertRaisesRegex(agentcmd.ManifestError, "Tablica"):
                    agentcmd.background(step)


class SymlinkLevelTest(ManifestBase):
    def swap_for_link(self, path):
        real = path + "-real"
        os.rename(path, real)
        os.symlink(real, path)
        return real

    def assert_refused(self, what):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool, level=what):
                with self.assertRaises(agentcmd.ManifestError) as caught:
                    agentcmd.background(self.step(tool, codex_mcp=()))
                self.assertIn(what, str(caught.exception))
                self.assertIn("dowiązaniem", str(caught.exception))

    def test_worktree_link(self):
        self.swap_for_link(self.worktree)
        self.assert_refused("worktree")

    def test_out_link(self):
        os.makedirs(self.step_dir)
        target = os.path.join(self.root, "cel")
        os.makedirs(target)
        os.symlink(target, os.path.join(self.step_dir, "out"))
        self.assert_refused("out")

    def test_input_link(self):
        os.makedirs(self.step_dir)
        os.symlink(self.root, os.path.join(self.step_dir, "in"))
        self.assert_refused("in")

    def test_step_dir_link(self):
        os.makedirs(self.step_dir)
        self.swap_for_link(self.step_dir)
        self.assert_refused("katalog kroku")

    def test_git_worktree_entry_link(self):
        # Cel poza `.git/worktrees`: inaczej repozytorium znałoby worktree dwukrotnie (odmowa resolve_git_dir).
        real = os.path.join(self.git, "wt-real")
        os.rename(self.gitdir, real)
        os.symlink(real, self.gitdir)
        self.assert_refused(".git/worktrees/<wt>")

    def test_git_worktrees_link(self):
        self.swap_for_link(os.path.join(self.git, "worktrees"))
        self.assert_refused(".git/worktrees")

    def test_common_git_link(self):
        self.swap_for_link(self.git)
        self.assert_refused(".git")

    def test_worktree_parent_link(self):
        self.swap_for_link(os.path.dirname(self.worktree))
        self.assert_refused("katalog worktree kroków")

    def test_codex_git_root_link(self):
        self.swap_for_link(os.path.join(self.git, "logs"))
        with self.assertRaisesRegex(agentcmd.ManifestError, r"\.git/logs jest dowiązaniem"):
            agentcmd.background(self.step("codex", codex_mcp=()))


class ToctouTest(ManifestBase):
    def fake_run(self):
        """systemd-run, który zostawia ślad, gdy cokolwiek wystartuje."""
        path = os.path.join(self.root, "fake-systemd-run")
        self.marker = os.path.join(self.root, "started")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\ntouch {self.marker}\n')
        os.chmod(path, 0o755)
        return path

    def test_out_swapped_for_link_after_prepare_refuses_start(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool):
                shutil.rmtree(self.step_dir, True)
                bg = agentcmd.background(self.step(tool, codex_mcp=()))
                bg.prepare()
                out = os.path.join(self.step_dir, "out")
                victim = os.path.join(self.root, "victim-" + tool)
                os.rename(out, victim)
                shutil.rmtree(victim)
                os.makedirs(victim)
                os.symlink(victim, out)
                with self.assertRaisesRegex(agentcmd.ManifestError, "out"):
                    bg.start(systemd_run=self.fake_run())
                self.assertFalse(os.path.exists(self.marker))
                self.assertEqual(os.listdir(victim), [])         # ani step.log, ani nic innego przez dowiązanie

    def test_missing_out_created_as_link_before_prepare_writes_nothing(self):
        bg = agentcmd.background(self.step())
        victim = os.path.join(self.root, "victim")
        os.makedirs(victim)
        os.makedirs(self.step_dir)
        os.symlink(victim, os.path.join(self.step_dir, "out"))
        with self.assertRaisesRegex(agentcmd.ManifestError, "out"):
            bg.prepare()
        self.assertEqual(os.listdir(victim), [])

    def test_worktree_replaced_by_another_directory_refuses_start(self):
        """Ten sam napis ścieżki, inny i-węzeł (np. podmiana przez rename) — też odmowa, nie tylko dowiązanie."""
        bg = agentcmd.background(self.step("codex", codex_mcp=()))
        bg.prepare()
        os.rename(self.worktree, self.worktree + "-old")
        os.makedirs(self.worktree)
        with self.assertRaisesRegex(agentcmd.ManifestError, "worktree podmieniony"):
            bg.start(systemd_run=self.fake_run())
        self.assertFalse(os.path.exists(self.marker))

    def test_git_entry_swapped_after_build_refuses_start(self):
        bg = agentcmd.background(self.step())
        bg.prepare()
        real = os.path.join(self.git, "wt-real")
        os.rename(self.gitdir, real)
        os.symlink(real, self.gitdir)
        with self.assertRaises(agentcmd.ManifestError):
            bg.start(systemd_run=self.fake_run())
        self.assertFalse(os.path.exists(self.marker))

    def test_unchanged_paths_start_normally(self):
        bg = agentcmd.background(self.step())
        bg.argv = ["/bin/true"]
        bg.prepare()
        proc = bg.start(systemd_run=self.fake_run())
        proc.wait(timeout=10)
        self.assertTrue(os.path.exists(self.marker))


class ForbiddenGitMaintenanceTest(ManifestBase):
    COMMANDS = ("git gc", "git gc --prune=now", "git gc --aggressive", "git worktree prune", "git worktree prune -v",
                "git -C /x worktree prune", "git -c gc.auto=1 gc", "LC_ALL=C git gc", "git --no-pager gc")

    def test_classified_as_forbidden_in_every_project(self):
        for command in self.COMMANDS:
            with self.subTest(command):
                self.assertTrue(commands.is_forbidden(command, []))
                self.assertIn(commands.forbidding_rule(command, []), commands.STEP_DENIED)
                self.assertFalse(commands.outside_allowed(command, []))
                self.assertIsNone(commands.sandbox_grant(command, []))
        for command in ("git worktree list", "git worktree add ../x", "git status", "git log --oneline"):
            self.assertFalse(commands.is_forbidden(command, []), command)

    def test_claude_step_bans_them_whatever_the_project_list(self):
        allowed, bans = commands.claude_tools(["git worktree *", "git *"], [])
        for rule in ("Bash(git gc)", "Bash(git gc *)", "Bash(git worktree prune)", "Bash(git worktree prune *)"):
            self.assertIn(rule, bans)

    def test_step_git_never_runs_automatic_gc(self):
        env = agentcmd.background(self.step()).env
        pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(pairs["gc.auto"], "0")
        self.assertEqual(pairs["maintenance.auto"], "false")
