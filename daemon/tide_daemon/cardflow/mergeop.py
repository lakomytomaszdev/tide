"""Przypięte przygotowanie i wykonanie scalenia. Dziennik i blokadę repozytorium prowadzi silnik.

Obiekty próbnego drzewa powstają jak w podglądzie. Zamiar zawiera treść i SHA przyszłego commita,
zanim zapiszemy ten commit, indeks, pliki lub ref. Recovery chroni lokalną pracę w scalanych ścieżkach;
po przesunięciu refu pozostaje wyłącznie zapis wyniku i projekcji.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager

from . import gitops
from .mergepreview import checkout_snapshot, assess_local


class MergeWorktree:
    def __init__(self, flow_git, repo: str, branch: str, path: str):
        self.git, self.repo, self.branch, self.path = flow_git, repo, branch, path
        self.ref = f"refs/heads/{branch}"
        listed = flow_git.run(repo, "worktree", "list", "--porcelain", "-z")
        records = [dict(line.split(" ", 1) for line in block.split("\0") if " " in line)
                   for block in listed.out.split("\0\0")]
        if not listed.ok or not any(w.get("branch") == self.ref and
                                   os.path.realpath(w.get("worktree", "")) == os.path.realpath(path)
                                   for w in records):
            raise gitops.FlowGitError("worktree celu nie jest przypisany do zapisanej gałęzi")
        if not gitops.sandbox_available():
            raise gitops.FlowGitError("piaskownica gita karty jest niedostępna")
        try:
            git_dir = (os.path.join(os.path.realpath(repo), ".git") if os.path.realpath(path) == os.path.realpath(repo)
                       else gitops.resolve_git_dir(repo, path))
        except gitops.StepError as exc:
            raise gitops.FlowGitError(f"nie można ustalić katalogu gita celu: {exc}") from exc
        self.runner = gitops.StepGit(repo, git_dir, path, path + ".step")
        self.objects = gitops.StepGit(repo, git_dir, path, path + ".step", objects_only=True)
        if self.checked(self.runner.run("symbolic-ref", "--quiet", "HEAD")).strip() != self.ref:
            raise gitops.FlowGitError("worktree celu zmienił gałąź")

    @staticmethod
    def checked(result):
        if not result.ok:
            raise gitops.FlowGitError("nie można bezpiecznie wykonać scalenia: " +
                                      ((result.err or "").splitlines() or ["błąd gita"])[0])
        return result.out

    def tree(self, commit):
        return self.checked(self.git.run(self.repo, "rev-parse", "--verify", f"{commit}^{{tree}}")).strip()

    @contextmanager
    def private_index(self):
        index = self.checked(self.git.run(self.path, "rev-parse", "--path-format=absolute", "--git-path", "index")).strip()
        try:
            with tempfile.TemporaryDirectory(prefix="tide-merge-index-") as folder:
                shutil.copyfile(index, os.path.join(folder, "index"))
                yield lambda *args: self.objects.run(*args, index_dir=folder)
        except OSError as exc:
            raise gitops.FlowGitError(f"nie można przygotować prywatnej kopii indeksu: {exc}") from exc

    def index(self):
        with self.private_index() as run:
            return self.checked(run("write-tree")).strip()

    def changed_paths(self, before, after):
        return set(self.checked(self.git.run(self.repo, "diff-tree", "-r", "--name-only", "-z", "--no-renames",
                                             before, after)).split("\0")) - {""}

    def clean_files(self, before, after):
        # Przywrócenie treści po kolizji zmienia statystyki pliku. Jak w FlowGit.merge odświeżamy je,
        # zanim diff-files uzna samą zmianę mtime/ctime za lokalną pracę; treść indeksu pozostaje bez zmian.
        with self.private_index() as run:
            run("update-index", "--refresh")
            dirty = set(self.checked(run("diff-files", "--name-only", "-z", "--")).split("\0")) - {""}
        changed = self.changed_paths(before, after)
        overlap = sorted(dirty & changed)
        if overlap:
            raise gitops.FlowGitError("worktree karty ma lokalne zmiany w scalanych plikach: " + ", ".join(overlap[:5]))
        # read-tree może zastąpić ignorowany plik. Sama próba -n ani diff-files go nie chronią.
        # Tylko scalane ścieżki i ich rodzice: pathspec obejmuje plik o tej nazwie i wszystko pod katalogiem,
        # więc duże ignorowane katalogi spoza scalenia (node_modules, build) nie są wyliczane.
        parents = {"/".join(p.split("/")[:i]) for p in changed for i in range(1, len(p.split("/")) + 1)}
        specs, untracked = sorted(f":(literal){p}" for p in parents), set()
        for start in range(0, len(specs), 500):
            listed = self.checked(self.runner.run("ls-files", "--others", "-z", "--", *specs[start:start + 500]))
            untracked |= {p.rstrip("/") for p in listed.split("\0") if p}
        collisions = sorted(p for p in untracked if p in parents or any(
            "/".join(p.split("/")[:i]) in changed for i in range(1, len(p.split("/")) + 1)))
        if collisions:
            raise gitops.FlowGitError("nieśledzone lub ignorowane pliki kolidują ze scaleniem: " + ", ".join(collisions[:5]))

    def prepare(self, source_ref: str, source: str, message: str, *, abandoned: dict | None = None) -> dict:
        before = self.git.sha(self.repo, self.ref)
        if not before or not source:
            raise gitops.FlowGitError("brak gałęzi głównej albo gałęzi karty")
        prepared = self.git.prepare_merge_tree(self.objects.run, before, source)
        if prepared["reason"]:
            raise gitops.FlowGitError(prepared["message"])
        tree = prepared["resultTree"]
        index, old_tree = self.index(), self.tree(before)
        # Porzucona materializacja jest własną pracą tej operacji, wyłącznie przy dokładnie zgodnym
        # indeksie i nieprzesuniętym refie. Nowy zamiar zapisze ją jako punkt wyjścia, bez cofania plików.
        inherited = (abandoned and abandoned["targetRef"] == self.ref and
                     abandoned["targetCommitBefore"] == before and
                     index == abandoned.get("resultIndexTree", abandoned["resultTree"]))
        # Dwa drzewa zmieniają tylko zakres scalenia. Poza nim git zachowuje istniejące wpisy indeksu.
        # Przy odziedziczonej materializacji punktem wyjścia jest poprzedni wynik commita.
        base = abandoned["resultTree"] if inherited else old_tree
        snapshot = checkout_snapshot(self.git.run, self.path)
        if inherited:
            # Własne pliki porzuconego wyniku nie są pracą użytkownika. Staged względem tego wyniku
            # nadal są jego pracą i muszą blokować kolizję z nową rewizją, nawet przy identycznym blobie.
            snapshot["changes"]["staged"] = [{"status": "M ", "paths": [p]}
                                               for p in self.changed_paths(base, index)]
        local = assess_local(snapshot, [], list(self.changed_paths(before, tree) | self.changed_paths(base, tree)))
        if local["localCollisionCount"]:
            raise gitops.FlowGitError("lokalne zmiany staged, unstaged lub untracked kolidują ze scaleniem")
        with self.private_index() as run:
            self.checked(run("read-tree", "-m", base, tree))
            result_index = self.checked(run("write-tree")).strip()
        self.clean_files(before, tree)
        self.clean_files(index, result_index)
        with self.private_index() as run:
            self.checked(run("read-tree", "-n", "-m", "-u", index, result_index))
        already = self.git.run(self.repo, "merge-base", "--is-ancestor", source, before).ok
        body, commit = "", before
        if not already:
            identities = []
            for name in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
                result = self.git.run(self.repo, "var", name)
                if not result.ok:
                    result = self.git.run(self.repo, *gitops.FALLBACK_IDENTITY, "var", name)
                identities.append(self.checked(result).strip())
            body = (f"tree {tree}\nparent {before}\nparent {source}\nauthor {identities[0]}\n"
                    f"committer {identities[1]}\n\n{message}\n")
            commit = self.checked(self.git.run(self.repo, "hash-object", "-t", "commit", "--stdin", input_text=body)).strip()
        return {"targetRef": self.ref, "targetBranch": self.branch, "targetWorktree": self.path,
                "sourceRef": source_ref, "sourceCommit": source, "targetCommitBefore": before,
                "resultCommit": commit, "resultTree": tree, "commitBody": body, "already": already,
                "materializationBaseTree": index, "resultIndexTree": result_index, "message": message}

    @staticmethod
    def result(op: dict) -> dict:
        return {key: op[key] for key in ("targetRef", "targetBranch", "sourceCommit", "targetCommitBefore",
                                        "resultCommit", "baseVersion", "operationId", "already")}

    def execute(self, op: dict, checkpoint) -> dict:
        before, commit = op["targetCommitBefore"], op["resultCommit"]
        current = self.git.sha(self.repo, self.ref)
        if current not in (before, commit):
            raise gitops.FlowGitError("gałąź celu zmieniła się poza zapisaną operacją — scalenie czeka")
        if current == commit:
            if self.tree(commit) != op["resultTree"]:
                raise gitops.FlowGitError("obiekt wyniku nie odpowiada zapisanemu zamiarowi")
            checkpoint("materialized")
            checkpoint("ref-moved")
            return self.result(op)
        if self.git.sha(self.repo, op["sourceRef"]) != op["sourceCommit"]:
            raise gitops.FlowGitError("gałąź karty zmieniła się po przygotowaniu operacji — scalenie czeka")
        old_tree, index = op.get("materializationBaseTree") or self.tree(before), self.index()
        result_index = op.get("resultIndexTree", op["resultTree"])
        if index not in (old_tree, result_index):
            raise gitops.FlowGitError("obcy indeks celu — scalenie czeka bez nadpisywania")
        self.clean_files(before, op["resultTree"])
        self.clean_files(old_tree, result_index)
        if op["commitBody"]:
            made = self.checked(self.git.run(self.repo, "hash-object", "-t", "commit", "-w", "--stdin",
                                              input_text=op["commitBody"])).strip()
            if made != commit or self.tree(commit) != op["resultTree"]:
                raise gitops.FlowGitError("obiekt wyniku nie odpowiada zapisanemu zamiarowi")
        if index != result_index:
            # read-tree odmawia również przy kolizji z nieśledzonym plikiem; bez resetu i bez sprzątania.
            self.checked(self.runner.run("read-tree", "-m", "-u", old_tree, result_index))
        if self.index() != result_index:
            raise gitops.FlowGitError("pliki lub indeks zmieniły się podczas materializacji — scalenie czeka")
        self.clean_files(before, op["resultTree"])
        self.clean_files(old_tree, result_index)
        checkpoint("materialized")
        if current != commit:
            moved = self.git.run(self.repo, "update-ref", "-m", op["message"], self.ref, commit, current)
            if not moved.ok:
                raise gitops.FlowGitError("nie udało się przesunąć zapisanego refu — scalenie czeka bez cofania plików")
        if self.git.sha(self.repo, self.ref) != commit:
            raise gitops.FlowGitError("gałąź celu zmieniła się poza zapisaną operacją — scalenie czeka")
        checkpoint("ref-moved")
        return self.result(op)
