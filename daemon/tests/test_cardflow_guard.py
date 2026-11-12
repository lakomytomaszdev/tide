"""Strażnik kroku opiekuna (K-37, PT-158): stan worktree, HEAD i gałęzi sprzed kroku porównany ze stanem po kroku
(`cardflow/guard.py`) i zapis Tide w trakcie kroku opiekuna, który nie jest zapisem kroku (`FlowEngine._guard_*`).
Prawdziwe repozytorium git z worktree ticketu i gałęzią karty, bez usługi."""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from tide_daemon.cardflow import guard
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.gitops import FlowGit
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks.agentcmd import resolve_git_dir

from .gitrepos import commit, git

CARD = "K-1"


class GuardRepo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-guard-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = os.path.join(self.root, "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        commit(self.repo, "README.md", "projekt\n")
        commit(self.repo, ".gitignore", "build/\n")
        git(self.repo, "branch", "card/k-1")
        self.wt = os.path.join(self.root, "proj-worktrees", "pt-1")
        git(self.repo, "worktree", "add", "-q", "-b", "ticket/pt-1", self.wt, "card/k-1")
        with open(os.path.join(self.wt, "brudny.txt"), "w", encoding="utf-8") as fh:
            fh.write("zmiana kroku ticketu sprzed opiekuna\n")
        self.out = self.wt + ".caretaker-K-1-care-1.step/out"
        os.makedirs(self.out)
        # Worktree karty (gałąź karty) obok worktree ticketu — przy problemie ticketu też pod strażą (review M2).
        self.card_wt = os.path.join(self.root, "proj-worktrees", "k-1")
        git(self.repo, "worktree", "add", "-q", self.card_wt, "card/k-1")
        self.git = FlowGit()
        self.git_dir = resolve_git_dir(self.repo, self.wt)
        self.watched = [(self.wt, self.git_dir), (self.card_wt, resolve_git_dir(self.repo, self.card_wt))]

    def snap(self):
        return guard.snapshot(self.git, repo=self.repo, worktrees=self.watched,
                              branches=("ticket/pt-1", "card/k-1"))


class SnapshotTest(GuardRepo):
    def test_writing_only_the_result_file_and_reading_pass(self):
        before = self.snap()
        with open(os.path.join(self.out, "result.json"), "w", encoding="utf-8") as fh:
            fh.write('{"result": "done"}')
        git(self.wt, "status", "--porcelain")         # odczyt gitem odświeża indeks w `.git`, nie w worktree
        git(self.wt, "log", "--oneline")
        with open(os.path.join(self.wt, "README.md"), encoding="utf-8") as fh:
            fh.read()
        self.assertEqual(guard.changes(before, self.snap()), [])

    def test_tracked_untracked_and_ignored_files_are_detected(self):
        for name, mode in (("README.md", "a"), ("nowy.txt", "w"), ("build/out.o", "w"), ("brudny.txt", "a")):
            with self.subTest(name=name):
                before = self.snap()
                os.makedirs(os.path.dirname(os.path.join(self.wt, name)), exist_ok=True)
                with open(os.path.join(self.wt, name), mode, encoding="utf-8") as fh:
                    fh.write("opiekun pisze\n")
                found = guard.changes(before, self.snap())
                self.assertEqual(len(found), 1, found)
                self.assertIn(name.split("/")[0], found[0])

    def test_rewrite_with_restored_mtime_and_same_size_is_detected(self):
        path = os.path.join(self.wt, "README.md")
        info = os.stat(path)
        before = self.snap()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("PROJEKT\n")                      # ta sama długość
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(os.stat(path).st_size, info.st_size)
        self.assertTrue(guard.changes(before, self.snap()))

    def test_deleting_and_chmod_are_detected(self):
        before = self.snap()
        os.unlink(os.path.join(self.wt, "brudny.txt"))
        self.assertTrue(guard.changes(before, self.snap()))
        before = self.snap()
        os.chmod(os.path.join(self.wt, "README.md"), 0o755)
        self.assertTrue(guard.changes(before, self.snap()))

    def test_commit_moves_the_step_branch_and_card_branch_is_watched(self):
        before = self.snap()
        git(self.wt, "commit", "-q", "--allow-empty", "-m", "opiekun commituje")
        self.assertEqual(guard.changes(before, self.snap()), ["przesunięta gałąź ticket/pt-1"])
        before = self.snap()
        git(self.repo, "update-ref", "refs/heads/card/k-1", "ticket/pt-1")
        self.assertEqual(guard.changes(before, self.snap()), ["przesunięta gałąź card/k-1"])
        before = self.snap()
        git(self.wt, "checkout", "-q", "--detach")
        self.assertEqual(guard.changes(before, self.snap()), [f"przesunięty HEAD worktree {self.wt}"])

    def test_card_worktree_is_watched_for_a_ticket_problem(self):
        """Review M2: plik śledzony i nowy nieśledzony w worktree karty (bez commita) przy opiekunie ticketu."""
        for name, mode in (("README.md", "a"), ("nowy-w-karcie.txt", "w")):
            with self.subTest(name=name):
                before = self.snap()
                with open(os.path.join(self.card_wt, name), mode, encoding="utf-8") as fh:
                    fh.write("opiekun pisze w karcie\n")
                self.assertEqual(guard.changes(before, self.snap()),
                                 [f"zmienione pliki w worktree {self.card_wt}: {name}"])
        before = self.snap()
        git(self.card_wt, "checkout", "-q", "--detach")
        self.assertEqual(guard.changes(before, self.snap()), [f"przesunięty HEAD worktree {self.card_wt}"])

    def test_the_same_worktree_is_watched_once(self):
        snap = guard.snapshot(self.git, repo=self.repo, worktrees=[(self.wt, self.git_dir), (self.wt + "/", "")],
                              branches=("card/k-1",))
        self.assertEqual([w["worktree"] for w in snap["worktrees"]], [self.wt])

    def test_missing_worktree(self):
        before = self.snap()
        shutil.rmtree(self.wt)
        self.assertIn(f"worktree {self.wt} zniknął", guard.changes(before, self.snap()))

    def cc_writes(self, *, existing=True, kind="dir"):
        """`.claude` w worktree jak po wcześniejszej sesji Claude Code; `existing` — `.cc-writes` już jest."""
        claude = os.path.join(self.wt, ".claude")
        os.makedirs(claude, exist_ok=True)
        with open(os.path.join(claude, "settings.local.json"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        path = os.path.join(claude, ".cc-writes")
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)
        if existing:
            self.write_cc(path, kind, "przed krokiem\n")
        return path

    @staticmethod
    def write_cc(path, kind, text):
        if kind == "dir":
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "zapis"), "a", encoding="utf-8") as fh:
                fh.write(text)
        else:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text)

    def test_claude_code_writes_in_cc_writes_are_not_a_change(self):
        """PT-317: `.claude/.cc-writes` (plik albo katalog) w katalogu głównym worktree zapisuje Claude Code, nie
        model — powstanie, zapis i usunięcie w trakcie kroku nie są zmianą. Pada na bazie (zmiana `.claude`)."""
        for kind in ("dir", "file"):
            for existing in (True, False):
                with self.subTest(kind=kind, existing=existing):
                    path = self.cc_writes(existing=existing, kind=kind)
                    before = self.snap()
                    self.write_cc(path, kind, "zapis w trakcie kroku\n")
                    self.assertEqual(guard.changes(before, self.snap()), [])
                    if kind == "dir":
                        shutil.rmtree(path)
                    else:
                        os.unlink(path)
                    self.assertEqual(guard.changes(before, self.snap()), [])

    def test_claude_dir_created_with_cc_writes_only_is_not_a_change(self):
        """Review PT-317 MAJOR-1: świeży worktree bez `.claude` (ticket autorstwa Codex, opiekun Claude) — pierwszy
        zapis `.cc-writes` tworzy katalog `.claude`; to nie jest zmiana. Nowy `.claude` z innym plikiem nadal jest."""
        claude = os.path.join(self.wt, ".claude")
        for kind in ("dir", "file"):
            with self.subTest(kind=kind):
                shutil.rmtree(claude, ignore_errors=True)
                before = self.snap()
                os.makedirs(claude)
                self.write_cc(os.path.join(claude, ".cc-writes"), kind, "pierwszy zapis\n")
                self.assertEqual(guard.changes(before, self.snap()), [])
                shutil.rmtree(claude)
                self.assertEqual(guard.changes(before, self.snap()), [])
        for other in ("x", "settings.local.json"):
            with self.subTest(other=other):
                shutil.rmtree(claude, ignore_errors=True)
                before = self.snap()
                os.makedirs(claude)
                self.write_cc(os.path.join(claude, ".cc-writes"), "dir", "zapis\n")
                with open(os.path.join(claude, other), "w", encoding="utf-8") as fh:
                    fh.write("model pisze\n")
                self.assertEqual(guard.changes(before, self.snap()),
                                 [f"zmienione pliki w worktree {self.wt}: .claude"])
        shutil.rmtree(claude)

    def test_hard_link_cc_writes_to_a_worktree_file_is_a_change(self):
        """Review PT-317 NIT-2: dowiązanie twarde `.claude/.cc-writes` → plik worktree zmienia `ctime` (`nlink`) celu,
        a zapis przez nie — rekord celu; oba widać w rekordzie celu."""
        claude = os.path.join(self.wt, ".claude")
        os.makedirs(claude)
        target, link = os.path.join(self.wt, "README.md"), os.path.join(claude, ".cc-writes")
        before = self.snap()
        os.link(target, link)
        self.assertEqual(guard.changes(before, self.snap()), [f"zmienione pliki w worktree {self.wt}: README.md"])
        os.unlink(link)
        os.link(target, link)
        before = self.snap()
        with open(link, "a", encoding="utf-8") as fh:
            fh.write("zapis przez dowiązanie twarde\n")
        self.assertEqual(guard.changes(before, self.snap()), [f"zmienione pliki w worktree {self.wt}: README.md"])

    def test_other_changes_in_claude_dir_are_still_a_change(self):
        """PT-317: wyjątek to dokładnie jedna ścieżka — inny plik w `.claude`, nowy plik obok `.cc-writes`, usunięcie,
        zmiana nazwy, `.cc-writes` jako dowiązanie i `.cc-writes` poza katalogiem głównym nadal są zmianą."""
        outside = os.path.join(self.root, "poza-cc")
        os.makedirs(outside)
        sub = os.path.join(self.wt, "pod", ".claude")

        def edit_settings(path):
            with open(os.path.join(os.path.dirname(path), "settings.local.json"), "a", encoding="utf-8") as fh:
                fh.write('{"x": 1}\n')

        def new_next_to(path):
            self.write_cc(path, "dir", "zapis\n")
            with open(os.path.join(os.path.dirname(path), "obok.txt"), "w", encoding="utf-8") as fh:
                fh.write("model pisze obok\n")

        def new_inside_claude_subdir(path):
            os.makedirs(os.path.join(os.path.dirname(path), "skills"), exist_ok=True)
            with open(os.path.join(os.path.dirname(path), "skills", "x.md"), "w", encoding="utf-8") as fh:
                fh.write("x\n")

        def delete_settings(path):
            os.unlink(os.path.join(os.path.dirname(path), "settings.local.json"))

        def rename_into_cc(path):
            os.rename(os.path.join(os.path.dirname(path), "settings.local.json"), os.path.join(path, "ukryty"))

        def move_worktree_file_into_cc(path):
            os.rename(os.path.join(self.wt, "brudny.txt"), os.path.join(path, "brudny.txt"))

        def symlink_cc(path):
            shutil.rmtree(path)
            os.symlink(outside, path)

        def nested_cc(path):
            os.makedirs(os.path.join(sub, ".cc-writes"), exist_ok=True)

        cases = {"settings": (edit_settings, ".claude"), "obok": (new_next_to, ".claude"),
                 "podkatalog": (new_inside_claude_subdir, ".claude"), "usuniety": (delete_settings, ".claude"),
                 "do-cc": (rename_into_cc, ".claude"), "plik-do-cc": (move_worktree_file_into_cc, "brudny.txt"),
                 "dowiazanie": (symlink_cc, ".claude"), "zagniezdzony": (nested_cc, "pod")}
        for case, (change, top) in cases.items():
            with self.subTest(case=case):
                shutil.rmtree(os.path.join(self.wt, ".claude"), ignore_errors=True)
                shutil.rmtree(os.path.join(self.wt, "pod"), ignore_errors=True)    # NIT-1: bez zależności od kolejności
                if not os.path.exists(os.path.join(self.wt, "brudny.txt")):
                    with open(os.path.join(self.wt, "brudny.txt"), "w", encoding="utf-8") as fh:
                        fh.write("x\n")
                os.makedirs(sub, exist_ok=True)
                path = self.cc_writes()
                before = self.snap()
                change(path)
                found = guard.changes(before, self.snap())
                self.assertEqual(len(found), 1, found)
                self.assertIn(top, found[0])

    def test_writes_through_a_cc_writes_symlink_are_seen_at_the_target(self):
        """PT-317: dowiązanie `.claude/.cc-writes` do innego miejsca worktree nie ukrywa zapisu — cel ma własny
        rekord (a samo dowiązanie nie jest wyjątkiem)."""
        claude = os.path.join(self.wt, ".claude")
        os.makedirs(claude)
        os.symlink(os.path.join(self.wt, "README.md"), os.path.join(claude, ".cc-writes"))
        before = self.snap()
        with open(os.path.join(claude, ".cc-writes"), "a", encoding="utf-8") as fh:
            fh.write("zapis przez dowiązanie\n")
        self.assertEqual(guard.changes(before, self.snap()), [f"zmienione pliki w worktree {self.wt}: README.md"])

    def changed(self, change):
        before = self.snap()
        change()
        return guard.changes(before, self.snap())

    def test_cc_writes_symlink_scope(self):
        """PT-331: dowiązanie `.claude/.cc-writes` nie ukrywa zapisu — strażnik nie podąża za dowiązaniem; zmiana
        treści celu w worktree, zmiana celu dowiązania i samo pojawienie się dowiązania (do pliku albo katalogu,
        w worktree albo poza nim) dają zmianę (`caretaker-wrote`)."""
        claude, outside = os.path.join(self.wt, ".claude"), os.path.join(self.root, "poza-cc")
        link = os.path.join(claude, ".cc-writes")
        inner = os.path.join(self.wt, "wewn")
        os.makedirs(outside)
        os.makedirs(inner)
        with open(os.path.join(outside, "plik"), "w", encoding="utf-8") as fh:
            fh.write("poza\n")

        def fresh(target=None):
            shutil.rmtree(claude, ignore_errors=True)
            os.makedirs(claude)
            if target:
                os.symlink(target, link)

        cases = {
            # Krok opiekuna zmienia treść pliku worktree wskazanego przez dowiązanie — rekord celu.
            "tresc-celu-pliku": (os.path.join(self.wt, "README.md"),
                                 lambda: self.append(link), "README.md"),
            # Nowy plik w katalogu worktree wskazanym przez dowiązanie — rekord katalogu celu.
            "tresc-celu-katalogu": (inner, lambda: self.append(os.path.join(link, "nowy")), "wewn"),
            # Przestawienie dowiązania na inny cel — rekord dowiązania ma cel.
            "zmiana-celu": (os.path.join(self.wt, "README.md"),
                            lambda: (os.unlink(link), os.symlink(outside, link)), ".claude"),
            # Dowiązanie utworzone w trakcie kroku: do katalogu poza worktree, do pliku poza worktree, do worktree.
            "nowe-do-katalogu-poza": (None, lambda: os.symlink(outside, link), ".claude"),
            "nowe-do-pliku-poza": (None, lambda: os.symlink(os.path.join(outside, "plik"), link), ".claude"),
            "nowe-do-worktree": (None, lambda: os.symlink(inner, link), ".claude"),
        }
        for case, (target, change, top) in cases.items():
            with self.subTest(case=case):
                fresh(target)
                self.assertEqual(self.changed(change), [f"zmienione pliki w worktree {self.wt}: {top}"])
        for kind in ("dir", "file"):
            with self.subTest(case="wyjatek-podmieniony-na-dowiazanie", kind=kind):
                fresh()
                self.write_cc(link, kind, "przed krokiem\n")       # `.cc-writes` (wyjątek) przed krokiem

                def swap():
                    if kind == "dir":
                        shutil.rmtree(link)
                    else:
                        os.unlink(link)
                    os.symlink(outside, link)

                self.assertEqual(self.changed(swap), [f"zmienione pliki w worktree {self.wt}: .claude"])
        # Dowiązanie do katalogu poza worktree nie jest przeglądane, ale też nie jest ukrytym wyjątkiem:
        # zapis po drugiej stronie dowiązania nie należy do worktree (jak każde dowiązanie).
        fresh(outside)
        self.assertEqual(self.changed(lambda: self.append(os.path.join(outside, "plik"))), [])
        shutil.rmtree(claude)

    def append(self, path, text="zapis\n"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def test_cc_writes_directory_is_skipped_with_its_content(self):
        """PT-331: `.claude/.cc-writes` jako katalog — nowe pliki, podkatalogi i usunięcia w środku nie są zmianą."""
        path = self.cc_writes(kind="dir")

        def inside():
            os.makedirs(os.path.join(path, "a", "b"))
            self.append(os.path.join(path, "a", "b", "nowy"))
            self.append(os.path.join(path, "drugi"))
            os.unlink(os.path.join(path, "zapis"))

        self.assertEqual(self.changed(inside), [])

    def test_other_claude_entries_next_to_a_cc_writes_change_are_a_change(self):
        """PT-331: przy jednoczesnej zmianie `.cc-writes` każdy inny wpis w `.claude` — hak, skill, ustawienia,
        nazwa zaczynająca się od `.cc-writes` — nadal jest zmianą (dokładna ścieżka, bez dopasowania prefiksu)."""
        claude = os.path.join(self.wt, ".claude")
        others = (os.path.join("hooks", "x"), os.path.join("skills", "s", "SKILL.md"), "settings.local.json",
                  ".cc-writes2", ".cc-writes.bak", ".cc-writesx")
        for kind in ("dir", "file"):
            for other in others:
                with self.subTest(kind=kind, other=other):
                    path = self.cc_writes(kind=kind)
                    for extra in ("hooks", "skills"):
                        shutil.rmtree(os.path.join(claude, extra), ignore_errors=True)
                    for extra in (".cc-writes2", ".cc-writes.bak", ".cc-writesx"):
                        if os.path.lexists(os.path.join(claude, extra)):
                            os.unlink(os.path.join(claude, extra))

                    def change():
                        self.write_cc(path, kind, "zapis w trakcie kroku\n")
                        target = os.path.join(claude, other)
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        self.append(target)

                    self.assertEqual(self.changed(change), [f"zmienione pliki w worktree {self.wt}: .claude"])

    def test_cc_writes_outside_the_root_claude_dir_is_a_change(self):
        """PT-331: `.cc-writes` poza `.claude` katalogu głównego — w katalogu głównym bez `.claude`, w podkatalogu
        worktree (`sub/.claude/.cc-writes`) i w `.claude/sub/` — nie jest pomijany (plik i katalog)."""
        places = {"korzen": (".cc-writes", ".cc-writes"), "sub-claude": ("sub/.claude/.cc-writes", "sub"),
                  "claude-sub": (".claude/sub/.cc-writes", ".claude"), "sub": ("sub/.cc-writes", "sub")}
        for kind in ("dir", "file"):
            for place, (rel, top) in places.items():
                with self.subTest(kind=kind, place=place):
                    for name in (".claude", "sub", ".cc-writes"):
                        target = os.path.join(self.wt, name)
                        if os.path.isdir(target):
                            shutil.rmtree(target)
                        elif os.path.lexists(target):
                            os.unlink(target)
                    path = os.path.join(self.wt, rel)
                    os.makedirs(os.path.dirname(path), exist_ok=True)

                    def change():
                        self.write_cc(path, kind, "zapis\n")

                    self.assertEqual(self.changed(change), [f"zmienione pliki w worktree {self.wt}: {top}"])

    def test_symlinks_are_not_followed(self):
        outside = os.path.join(self.root, "poza")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.wt, "link"))
        before = self.snap()
        with open(os.path.join(outside, "x"), "w", encoding="utf-8") as fh:
            fh.write("poza worktree\n")
        self.assertEqual(guard.changes(before, self.snap()), [])


class EngineGuardRepo(GuardRepo):
    """Metody silnika na samym dzienniku i gicie: stan bazowy, zapis Tide w trakcie kroku i werdykt."""

    UNIT = "tide-step-K-1-care-1"

    def setUp(self):
        super().setUp()
        board = os.path.join(self.root, "board")
        os.makedirs(board)
        self.calls = []
        self.freezes = True
        self.proc = SimpleNamespace(poll=lambda: None)        # proces kroku opiekuna żyje

        def freeze(unit, frozen):
            self.calls.append((unit, frozen))
            return self.freezes

        self.engine = SimpleNamespace(git=self.git, journal=Journal(board), freeze_unit=freeze,
                                      procs={"K-1-care-1": self.proc})
        for name in ("_act", "_guard_baseline", "_guard_watched", "_guard_check", "_guard_freeze", "_guard_thaw",
                     "_guard_rebase", "_guard_verdict", "_guard_outside"):
            setattr(self.engine, name, getattr(FlowEngine, name).__get__(self.engine))
        self.engine.journal.append(CARD, "flow.started", provider="anthropic")

    def baseline(self):
        self.engine._guard_baseline(CARD, "K-1-care-1", self.repo, self.watched, ("ticket/pt-1", "card/k-1"))
        self.engine.journal.append(CARD, "step.started", step="K-1-care-1", kind="caretaker", unit=self.UNIT,
                                   worktree=self.wt)

    def tide_writes(self, work, action="merge"):
        """Prawdziwe `_act`: sprawdzenie przed działaniem Tide, zamrożenie kroku, działanie, stan na nowo."""
        self.engine._act(CARD, action, f"{action}-{len(self.calls)}", lambda: work() or {},
                         **({"kind": "ticket", "ticket": "PT-1"} if action == "step-start" else {}))

    def append(self, path, text="zapis\n"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


class EngineGuardTest(EngineGuardRepo):
    def test_tide_merge_into_the_card_branch_is_not_a_caretaker_write(self):
        self.baseline()
        frozen_during = []

        def merge():
            frozen_during.append(list(self.calls))
            commit(self.card_wt, "scalony.txt")

        self.tide_writes(merge)
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")
        causes = [e.get("cause") for e in self.engine.journal.read(CARD) if e["type"] == guard.EVENT]
        self.assertEqual(causes, ["start", "merge"])
        # Działanie Tide trwało przy zamrożonym kroku; po nim krok odmrożony.
        self.assertEqual(frozen_during, [[(self.UNIT, True)]])
        self.assertEqual(self.calls, [(self.UNIT, True), (self.UNIT, False)])

    def test_caretaker_write_during_a_tide_action_is_kept_when_the_step_cannot_be_frozen(self):
        """Review M1: baseline → sprawdzenie → opiekun dopisuje do śledzonego README.md w trakcie działania Tide
        (np. przywracanie zależności przy starcie innego kroku) → bez zamrożenia zmiana należy do kroku."""
        self.freezes = False
        self.baseline()
        self.tide_writes(lambda: self.append(os.path.join(self.wt, "README.md")), action="step-start")
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertTrue(verdict.startswith("krok opiekuna pisał poza swoim katalogiem wyjściowym"), verdict)
        self.assertIn("`step-start`", verdict)
        self.assertIn("README.md", verdict)
        self.assertEqual(self.calls, [(self.UNIT, True)], "nie zamrożony — nie odmrażany")

    def test_caretaker_write_before_the_freeze_completes_is_kept(self):
        """Review M1 rundy 2: opiekun dopisuje do śledzonego README.md tuż przed skutecznym zamrożeniem, działanie
        Tide jest puste — zapis należy do kroku, wynik odrzucony jako `caretaker-wrote`."""

        def freeze(unit, frozen):
            self.calls.append((unit, frozen))
            if frozen:
                self.append(os.path.join(self.wt, "README.md"))
            return True

        self.engine.freeze_unit = freeze
        self.baseline()
        self.tide_writes(lambda: None)
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertTrue(verdict.startswith("krok opiekuna pisał poza swoim katalogiem wyjściowym"), verdict)
        self.assertIn("README.md", verdict)
        self.assertEqual(self.calls, [(self.UNIT, True), (self.UNIT, False)])

    def test_a_failing_check_still_thaws_the_step(self):
        self.baseline()

        def broken(card, st):
            raise OSError("git")

        self.engine._guard_check = broken
        with self.assertRaises(OSError):
            self.tide_writes(lambda: None)
        self.assertEqual(self.calls, [(self.UNIT, True), (self.UNIT, False)])

    def test_caretaker_write_into_the_card_worktree_during_a_tide_action_is_kept(self):
        self.freezes = False
        self.baseline()
        self.tide_writes(lambda: self.append(os.path.join(self.card_wt, "README.md")), action="step-start")
        self.assertIn(self.card_wt, self.engine._guard_verdict(CARD, "K-1-care-1"))

    def test_a_finished_caretaker_process_needs_no_freezing(self):
        self.freezes = False
        self.proc.poll = lambda: 0
        self.baseline()
        self.tide_writes(lambda: commit(self.card_wt, "scalony.txt"))
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")

    def test_a_failing_tide_action_still_thaws_the_step(self):
        self.baseline()

        def broken():
            raise OSError("dysk")

        with self.assertRaises(OSError):
            self.tide_writes(broken)
        self.assertEqual(self.calls, [(self.UNIT, True), (self.UNIT, False)])

    def test_without_caretaker_steps_nothing_is_frozen(self):
        self.tide_writes(lambda: commit(self.card_wt, "scalony.txt"))
        self.assertEqual(self.calls, [])

    def test_caretaker_write_before_a_tide_write_is_kept(self):
        self.baseline()
        with open(os.path.join(self.wt, "opiekun.txt"), "w", encoding="utf-8") as fh:
            fh.write("zapis kroku\n")
        self.tide_writes(lambda: git(self.repo, "update-ref", "refs/heads/card/k-1", "master"))
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertTrue(verdict.startswith("krok opiekuna pisał poza swoim katalogiem wyjściowym"), verdict)
        self.assertIn("opiekun.txt", verdict)

    def test_write_after_the_tide_write_is_detected(self):
        self.baseline()
        self.tide_writes(lambda: None)
        git(self.wt, "commit", "-q", "--allow-empty", "-m", "opiekun commituje")
        self.assertIn("przesunięta gałąź ticket/pt-1", self.engine._guard_verdict(CARD, "K-1-care-1"))

    def test_without_a_baseline_the_result_is_rejected(self):
        self.assertIn("brak stanu worktree sprzed kroku", self.engine._guard_verdict(CARD, "K-1-care-9"))

    def test_finished_caretaker_steps_are_no_longer_watched(self):
        self.baseline()
        self.engine.journal.append(CARD, "step.finished", step="K-1-care-1", result="done")
        self.assertEqual(self.engine._guard_check(CARD, fold(self.engine.journal.read(CARD))), [])


class OutsideRunsTest(unittest.TestCase):
    """Czyste funkcje wyjątku (PT-372): uruchomienia z dziennika, nakładanie się na stan bazowy, dopasowanie ścieżek."""

    def test_runs_pair_start_and_finish_and_take_the_merge_check_worktree_from_its_pseudo_step(self):
        events = [
            {"seq": 1, "type": "merge.check-step", "step": "merge-check-PT-1-1", "worktree": "/w/k-1/"},
            {"seq": 2, "type": "outside.started", "step": "K-1-PT-2-1", "worktree": "/w/./pt-2"},
            {"seq": 3, "type": "outside.started", "step": "merge-check-PT-1-1", "worktree": "/inny"},
            {"seq": 4, "type": "outside.finished", "step": "K-1-PT-2-1"},
            {"seq": 5, "type": "outside.started", "step": "K-1-PT-3-1"},
        ]
        self.assertEqual(guard.runs(events), [
            {"step": "K-1-PT-2-1", "worktree": "/w/pt-2", "started": 2, "finished": 4},
            {"step": "merge-check-PT-1-1", "worktree": "/w/k-1", "started": 3, "finished": None},
            {"step": "K-1-PT-3-1", "worktree": "", "started": 5, "finished": None},
        ])

    def test_active_runs_overlap_the_baseline(self):
        found = [{"step": "a", "worktree": "/w", "started": 2, "finished": 4},     # skończone przed stanem bazowym
                 {"step": "b", "worktree": "/w", "started": 3, "finished": 5},     # skończone równo z nim
                 {"step": "c", "worktree": "/w", "started": 6, "finished": 8},     # ruszyło i skończyło się w trakcie
                 {"step": "d", "worktree": "/v", "started": 1, "finished": None},  # trwa
                 {"step": "e", "worktree": "", "started": 6, "finished": None},    # bez worktree
                 {"step": "care", "worktree": "/u", "started": 6, "finished": None}]
        self.assertEqual(guard.active({"seq": 5}, found, "care"), {"/w": ["c"], "/v": ["d"]})
        self.assertEqual(guard.active({}, found, "care"), {}, "bez seq stanu bazowego nic nie jest wyłączone")

    def test_paths_are_normalised_without_following_symlinks(self):
        root = tempfile.mkdtemp(prefix="tide-guard-where-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        wt = os.path.join(root, "pt-1")
        os.makedirs(wt)
        os.symlink(wt, os.path.join(root, "link"))
        self.assertEqual(guard.where(wt + "/"), wt)
        self.assertEqual(guard.where(os.path.join(root, ".", "pt-1", ".")), wt)
        self.assertNotEqual(guard.where(os.path.join(root, "link")), wt)
        self.assertEqual(guard.where(None), "")
        self.assertEqual(guard.where(""), "")

    def test_excuse_keeps_head_refs_and_other_worktrees(self):
        base = {"seq": 3, "repo": "/r", "branches": ["b"], "refs": {"b": "1"},
                "worktrees": [{"worktree": "/w/", "gitDir": "/g", "tree": {"a": "1"}, "head": "ref: b"},
                              {"worktree": "/v", "gitDir": "/h", "tree": {"a": "1"}, "head": "ref: c"}]}
        fresh = {"repo": "/r", "branches": ["b"], "refs": {"b": "2"},
                 "worktrees": [{"worktree": "/w/", "gitDir": "/g", "tree": {"a": "1", "build": "2"}, "head": "x"},
                               {"worktree": "/v", "gitDir": "/h", "tree": {"a": "3"}, "head": "ref: c"}]}
        rebased, skipped = guard.excuse(base, fresh, {"/w": ["K-1-PT-1-1"]})
        self.assertEqual(skipped, [{"run": "K-1-PT-1-1", "worktree": "/w/", "entries": ["build"]}])
        self.assertEqual(set(rebased), set(guard.FIELDS))
        self.assertEqual(guard.changes(rebased, fresh), [
            "przesunięty HEAD worktree /w/", "zmienione pliki w worktree /v: a", "przesunięta gałąź b"])
        gone = {**fresh, "worktrees": [{**fresh["worktrees"][0], "tree": guard.MISSING}, fresh["worktrees"][1]]}
        self.assertEqual(guard.excuse(base, gone, {"/w": ["K-1-PT-1-1"]})[1], [], "zniknięcie worktree nie jest wyłączone")


class OutsideGuardTest(EngineGuardRepo):
    """Polecenie Tide w worktree pod strażą w trakcie kroku opiekuna (PT-372): zmiany drzewa tego worktree to zapisy
    polecenia, `HEAD`, refy i inne worktree — nadal kroku."""

    RUN = "K-1-PT-1-2"

    def started(self, worktree, step=RUN):
        fields = {} if worktree is None else {"worktree": worktree}
        self.engine.journal.append(CARD, "outside.started", step=step, ticket="PT-1", command="make test-changed",
                                   unit=f"tide-outside-{step}", **fields)

    def finished(self, step=RUN):
        self.engine.journal.append(CARD, "outside.finished", step=step, ticket="PT-1", command="make test-changed",
                                   exit=0, tail="")

    def build(self, worktree, name="build"):
        os.makedirs(os.path.join(worktree, name), exist_ok=True)
        self.append(os.path.join(worktree, name, "wynik.txt"))

    def guard_events(self):
        return [e for e in self.engine.journal.read(CARD) if e["type"] == guard.EVENT]

    def test_ticket_worktree_with_a_running_outside_command_is_not_a_caretaker_write(self):
        self.baseline()
        self.started(self.wt)
        self.build(self.wt)
        self.build(self.wt, "__pycache__")
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")
        last = self.guard_events()[-1]
        self.assertEqual(last["cause"], guard.OUTSIDE)
        self.assertEqual(last["wrote"], [])
        self.assertEqual(last["skipped"], [{"run": self.RUN, "worktree": self.wt, "entries": ["__pycache__", "build"]}])
        # Stan tego worktree wzięty na nowo: kolejne sprawdzenie bez nowych zmian nic nie dopisuje.
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")
        self.assertEqual(len(self.guard_events()), 2)

    def test_merge_check_in_the_card_worktree_is_not_a_caretaker_write(self):
        self.baseline()
        self.engine.journal.append(CARD, "merge.check-step", step="merge-check-PT-1-1", ticket="PT-1",
                                   kind="merge-check", check="PT-1-1", worktree=self.card_wt + "/")
        self.started(None, step="merge-check-PT-1-1")
        self.build(self.card_wt)
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")
        skipped = self.guard_events()[-1]["skipped"]
        self.assertEqual(skipped, [{"run": "merge-check-PT-1-1", "worktree": self.card_wt, "entries": ["build"]}])

    def test_a_command_started_and_finished_during_the_caretaker_step_counts(self):
        self.baseline()
        self.started(self.wt)
        self.build(self.wt)
        self.finished()
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")

    def test_a_command_finished_before_the_baseline_does_not_count(self):
        self.started(self.wt)
        self.finished()
        self.baseline()
        self.build(self.wt)
        self.assertIn("zmienione pliki w worktree", self.engine._guard_verdict(CARD, "K-1-care-1"))

    def test_head_and_branch_changes_during_a_running_command_are_still_caretaker_writes(self):
        self.baseline()
        self.started(self.wt)
        self.build(self.wt)
        git(self.wt, "commit", "-q", "--allow-empty", "-m", "opiekun commituje")
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertIn("przesunięta gałąź ticket/pt-1", verdict)
        self.assertNotIn("zmienione pliki", verdict)
        git(self.wt, "checkout", "-q", "--detach")
        self.assertIn(f"przesunięty HEAD worktree {self.wt}", self.engine._guard_verdict(CARD, "K-1-care-1"))

    def test_check_before_a_tide_action_skips_the_tree_and_keeps_head(self):
        self.baseline()
        self.started(self.wt)
        self.build(self.wt)
        git(self.wt, "checkout", "-q", "--detach")
        self.tide_writes(lambda: None)
        causes = [(e["cause"], bool(e["wrote"])) for e in self.guard_events()]
        self.assertEqual(causes[:3], [("start", False), (guard.OUTSIDE, False), ("check", True)])
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertIn("przesunięty HEAD worktree", verdict)
        self.assertNotIn("zmienione pliki", verdict)

    def test_rebase_after_an_unfrozen_tide_action_skips_the_tree_and_keeps_head(self):
        """`_guard_rebase`: krok nie dał się zamrozić, w trakcie działania Tide polecenie w worktree ticketu pisze
        `build/`, a `HEAD` się przesuwa — drzewo nie jest zapisem kroku, `HEAD` jest."""
        self.freezes = False
        self.baseline()
        self.started(self.wt)

        def action():
            self.build(self.wt)
            git(self.wt, "checkout", "-q", "--detach")

        self.tide_writes(action, action="step-start")
        last = self.guard_events()[-1]
        self.assertEqual(last["cause"], "step-start")
        self.assertEqual(len(last["wrote"]), 1)
        self.assertIn("przesunięty HEAD worktree", last["wrote"][0])
        self.assertNotIn("zmienione pliki", last["wrote"][0])
        self.assertIn(guard.OUTSIDE, [e["cause"] for e in self.guard_events()])
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertIn("przesunięty HEAD worktree", verdict)
        self.assertNotIn("zmienione pliki", verdict)

    def test_rebase_with_only_tree_changes_of_a_running_command_is_clean(self):
        self.freezes = False
        self.baseline()
        self.started(self.wt)
        self.tide_writes(lambda: self.build(self.wt), action="step-start")
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")

    def test_other_path_forms_of_the_same_worktree_match(self):
        forms = {"końcowy /": lambda wt: wt + "/", "./": lambda wt: os.path.join(os.path.dirname(wt), ".", "pt-1")}
        for label, form in forms.items():
            with self.subTest(form=label):
                self.setUp()
                self.baseline()
                self.started(form(self.wt))
                self.build(self.wt)
                self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")

    def test_outside_started_without_a_worktree_does_not_disable_the_guard(self):
        self.baseline()
        self.started(None)
        self.build(self.wt)
        self.assertIn("zmienione pliki w worktree", self.engine._guard_verdict(CARD, "K-1-care-1"))
        self.assertNotIn(guard.OUTSIDE, [e["cause"] for e in self.guard_events()])

    def test_symlink_to_the_worktree_is_another_path(self):
        link = os.path.join(self.root, "dowiazanie")
        os.symlink(self.wt, link)
        self.baseline()
        self.started(link)
        self.build(self.wt)
        self.assertIn("zmienione pliki w worktree", self.engine._guard_verdict(CARD, "K-1-care-1"))

    def test_write_into_another_guarded_worktree_is_still_a_caretaker_write(self):
        self.baseline()
        self.started(self.wt)
        self.build(self.wt)
        self.append(os.path.join(self.card_wt, "README.md"))
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertIn(f"zmienione pliki w worktree {self.card_wt}: README.md", verdict)
        self.assertNotIn(f"worktree {self.wt}:", verdict)


if __name__ == "__main__":
    unittest.main()
