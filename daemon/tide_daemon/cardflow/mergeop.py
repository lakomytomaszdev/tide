"""Przypięte przygotowanie i wykonanie scalenia. Dziennik i blokadę repozytorium prowadzi silnik.

Obiekty próbnego drzewa powstają jak w podglądzie. Zamiar zawiera treść i SHA przyszłego commita,
zanim zapiszemy ten commit, indeks, pliki lub ref. Recovery akceptuje tylko zapisane drzewa i lokalną
pracę sprzed zamiaru; nie próbuje zgadywać, czy obce zmiany wolno nadpisać.
"""
from __future__ import annotations

import hashlib
import os
import stat

from . import gitops


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

    def index(self):
        return self.checked(self.runner.run("write-tree")).strip()

    def local_state(self):
        """Odcisk pracy względem indeksu: ten sam przed i po poprawnej materializacji.

        Uwzględnia również treść plików nieśledzonych i ignorowanych, a nie tylko ich nazwy. Hashowanie
        bez filtrów odbywa się w StepGit, nie zapisuje obiektów i nie uruchamia kodu repozytorium.
        """
        patch = self.checked(self.runner.run("diff", "--no-ext-diff", "--no-textconv", "--binary", "--no-color",
                                             "--ignore-submodules=none", "--"))
        names = self.checked(self.runner.run("ls-files", "--others", "-z")).split("\0")
        pending = [n.rstrip("/") for n in names if n]
        hashes, entries, regular = [], [], []
        while pending:
            name = pending.pop()
            path = os.path.join(self.path, name)
            mode = os.lstat(path).st_mode
            entries.append((name, str(mode), os.readlink(path) if stat.S_ISLNK(mode) else ""))
            if stat.S_ISDIR(mode):
                # Git zwraca nieśledzone zagnieżdżone repo jako jeden katalog z końcowym ukośnikiem.
                pending.extend(os.path.join(name, child) for child in os.listdir(path))
            elif stat.S_ISREG(mode):
                regular.append(name)
        regular.sort()
        for start in range(0, len(regular), 100):
            hashes.append(self.checked(self.runner.run("hash-object", "--no-filters", "--", *regular[start:start + 100])))
        metadata = "\0".join(value for entry in sorted(entries) for value in entry)
        return hashlib.sha256((patch + "\0" + metadata + "\0" + "".join(hashes)).encode()).hexdigest()

    def clean_files(self, before, after):
        dirty = set(self.checked(self.runner.run("diff-files", "--name-only", "-z", "--")).split("\0")) - {""}
        changed = set(self.checked(self.git.run(self.repo, "diff-tree", "-r", "--name-only", "-z", "--no-renames",
                                                before, after)).split("\0")) - {""}
        overlap = sorted(dirty & changed)
        if overlap:
            raise gitops.FlowGitError("worktree karty ma lokalne zmiany w scalanych plikach: " + ", ".join(overlap[:5]))
        # read-tree może zastąpić ignorowany plik. Sama próba -n ani diff-files go nie chronią.
        untracked = {p.rstrip("/") for p in self.checked(self.runner.run("ls-files", "--others", "-z")).split("\0") if p}
        parents = {"/".join(p.split("/")[:i]) for p in changed for i in range(1, len(p.split("/")) + 1)}
        collisions = sorted(p for p in untracked if p in parents or any(
            "/".join(p.split("/")[:i]) in changed for i in range(1, len(p.split("/")) + 1)))
        if collisions:
            raise gitops.FlowGitError("nieśledzone lub ignorowane pliki kolidują ze scaleniem: " + ", ".join(collisions[:5]))

    def prepare(self, source_ref: str, source: str, message: str) -> dict:
        before = self.git.sha(self.repo, self.ref)
        if not before or not source:
            raise gitops.FlowGitError("brak gałęzi głównej albo gałęzi karty")
        prepared = self.git.prepare_merge_tree(self.objects.run, before, source)
        if prepared["reason"]:
            raise gitops.FlowGitError(prepared["message"])
        tree = prepared["resultTree"]
        if self.index() != self.tree(before):
            raise gitops.FlowGitError("indeks worktree karty nie odpowiada gałęzi — lokalne zmiany staged")
        self.clean_files(before, tree)
        self.checked(self.runner.run("read-tree", "-n", "-m", "-u", before, tree))
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
                "localState": self.local_state(), "message": message}

    def execute(self, op: dict, checkpoint) -> dict:
        before, commit = op["targetCommitBefore"], op["resultCommit"]
        current = self.git.sha(self.repo, self.ref)
        if current not in (before, commit):
            raise gitops.FlowGitError("gałąź celu zmieniła się poza zapisaną operacją — scalenie czeka")
        if self.git.sha(self.repo, op["sourceRef"]) != op["sourceCommit"]:
            raise gitops.FlowGitError("gałąź karty zmieniła się po przygotowaniu operacji — scalenie czeka")
        old_tree, index = self.tree(before), self.index()
        if index not in (old_tree, op["resultTree"]):
            raise gitops.FlowGitError("obcy indeks celu — scalenie czeka bez nadpisywania")
        if self.local_state() != op["localState"]:
            raise gitops.FlowGitError("nowe lokalne zmiany w worktree celu — scalenie czeka bez nadpisywania")
        if op["commitBody"]:
            made = self.checked(self.git.run(self.repo, "hash-object", "-t", "commit", "-w", "--stdin",
                                              input_text=op["commitBody"])).strip()
            if made != commit or self.tree(commit) != op["resultTree"]:
                raise gitops.FlowGitError("obiekt wyniku nie odpowiada zapisanemu zamiarowi")
        if index != op["resultTree"]:
            self.clean_files(before, commit)
            # read-tree odmawia również przy kolizji z nieśledzonym plikiem; bez resetu i bez sprzątania.
            self.checked(self.runner.run("read-tree", "-m", "-u", before, commit))
        if self.index() != op["resultTree"] or self.local_state() != op["localState"]:
            raise gitops.FlowGitError("pliki lub indeks zmieniły się podczas materializacji — scalenie czeka")
        checkpoint("materialized")
        if current != commit:
            moved = self.git.run(self.repo, "update-ref", "-m", op["message"], self.ref, commit, current)
            if not moved.ok:
                raise gitops.FlowGitError("nie udało się przesunąć zapisanego refu — scalenie czeka bez cofania plików")
        if self.git.sha(self.repo, self.ref) != commit:
            raise gitops.FlowGitError("gałąź celu zmieniła się poza zapisaną operacją — scalenie czeka")
        checkpoint("ref-moved")
        return {key: op[key] for key in ("targetRef", "targetBranch", "sourceCommit", "targetCommitBefore",
                                        "resultCommit", "baseVersion", "operationId", "already")}
