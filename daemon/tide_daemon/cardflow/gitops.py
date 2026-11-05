"""Git przepływu karty w repozytorium projektu: gałąź i worktree karty, gałąź i worktree ticketu, scalenie ticketu.

- wszystkie operacje na jednym repozytorium idą po kolei pod jedną blokadą (`lock_for`),
- każda akcja jest idempotentna (powtórka po restarcie usługi nie robi drugiej gałęzi ani drugiego scalenia):
  gałąź, która już jest w tym samym worktree, to sukces; ticket, który już jest w gałęzi karty, to sukces,
- worktree przepływu leżą w `<repo>-worktrees/` obok repozytorium (konwencja z okna „Nowe zadanie”), nie w ukrytym
  katalogu (`.claude/worktrees` byłby dla piaskownicy Claude Code chronionym katalogiem, a Codex ze snapa nie widzi
  ukrytych katalogów domowych), katalog kroku to `<worktree>.step` obok worktree,
- scalanie na obiektach (`merge-tree`, `commit-tree`) odbywa się bez katalogu roboczego; każde polecenie gita
  dotykające worktree karty działa w piaskownicy StepGit. Najpierw materializujemy drzewo, potem przesuwamy ref;
  ponowienie sprawdza i naprawia rozjazd indeksu/worktree z gałęzią, zanim zwróci `already`.
"""
from __future__ import annotations

import os
import shutil
import re
import stat
import threading

from ..worktrees.git import MAIN_CANDIDATES, Git, pick_main_branch
from ..tasks.agentcmd import StepError, resolve_git_dir
from ..tasks.stepstop import StepGit, sandbox_available

HARDENED = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
            "-c", "submodule.recurse=false", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false",
            "-c", "gc.auto=0")
FALLBACK_IDENTITY = ("-c", "user.name=Tide", "-c", "user.email=tide@localhost")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(repo: str) -> threading.Lock:
    key = os.path.realpath(repo)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _foreign_changes(porcelain: str) -> list[str]:
    """Linie `git status --porcelain` bez nieśledzonych narzędzi agentów użytkownika (TOOLING_DIRS) — tych nie
    wnosi krok, tylko zewnętrzna synchronizacja, więc nie blokują review ani scalenia."""
    from ..tasks.agentcmd import TOOLING_DIRS
    prefixes = tuple(d.strip("/") + "/" for d in TOOLING_DIRS)
    return [line for line in porcelain.splitlines()
            if line.strip() and not (line.startswith("?? ") and line[3:].strip('"').startswith(prefixes))]


class FlowGitError(Exception):
    """Błąd gita z komunikatem po polsku."""


def worktrees_dir(repo: str) -> str:
    repo = os.path.normpath(repo)
    return repo + "-worktrees"


def worktree_for(repo: str, branch: str) -> str:
    name = branch.partition("/")[2] or branch
    return os.path.join(worktrees_dir(repo), re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "krok")


class FlowGit:
    def __init__(self, git: Git | None = None):
        self.git = git or Git(timeout=120)

    def run(self, cwd: str, *args, timeout=None):
        return self.git.run(cwd, *HARDENED, *args, timeout=timeout)

    def main_branch(self, repo: str) -> str:
        # Gałąź bazowa wskazana w repozytorium (`git config tide.mainBranch <gałąź>`) — dla projektów, których praca
        # toczy się na innej gałęzi niż master/main (04.11, BO: narzędzie hud-timeline jest tylko na feature/vision-frames).
        chosen = self.run(repo, "config", "--get", "tide.mainBranch")
        pinned = chosen.out.strip() if chosen.ok else ""
        if pinned and self.sha(repo, f"refs/heads/{pinned}"):
            return pinned
        refs = [f"refs/heads/{name}" for name in MAIN_CANDIDATES]
        listed = self.run(repo, "for-each-ref", "--format=%(refname:short)", *refs)
        current = self.run(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        existing = {line.strip() for line in listed.out.splitlines() if line.strip()} if listed.ok else set()
        return pick_main_branch(existing, current.out.strip() if current.ok else "")

    def sha(self, repo: str, ref: str) -> str:
        res = self.run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return res.out.strip() if res.ok else ""

    def prepare_merge_tree(self, run, target: str, source: str) -> dict:
        """Przygotowanie na przypiętych SHA, wspólne dla odczytu i wykonania. Bez indeksu i refów.

        `run` pochodzi ze StepGit; sterowniki merge z konfiguracji repo też podlegają piaskownicy.
        Wyjście -z/--name-only zachowuje ścieżki z tabulatorami i nowymi liniami.
        """
        help_result = run("merge-tree", "-h")
        help_text = help_result.out + help_result.err
        if not all(flag in help_text for flag in ("--write-tree", "--name-only", "messages", "-z")):
            reason = "git-capability" if help_result.code in (0, 129) else "git-error"
            return {"reason": reason, "message": "Git nie udostępnia wymaganego merge-tree."
                    if reason == "git-capability" else "Nie można uruchomić git merge-tree.",
                    "resultTree": "", "conflicts": []}
        base = run("merge-base", target, source)
        if not base.ok:
            return {"reason": "unrelated-history" if base.code == 1 else "git-error",
                    "message": "Gałęzie nie mają wspólnej historii." if base.code == 1
                    else "Nie można odczytać wspólnej historii.", "resultTree": "", "conflicts": []}
        tree = run("merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", target, source)
        parts = tree.out.split("\0")
        if tree.code not in (0, 1) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]):
            return {"reason": "git-error", "message": "Nie udało się przygotować drzewa scalenia.",
                    "resultTree": "", "conflicts": []}
        return {"resultTree": parts[0], "conflicts": [p for p in parts[1:] if p],
                "reason": "conflict" if tree.code == 1 else "",
                "message": "Konflikty w drzewie scalenia." if tree.code == 1 else ""}

    def preview_merge(self, repo: str, source_branch: str) -> dict:
        """Pełny odczyt celu z tej samej polityki co MergeCardToMain; zapis tylko obiektów próbnego drzewa."""
        with lock_for(repo):
            target_branch = self.main_branch(repo)
            source_ref, target_ref = f"refs/heads/{source_branch}", f"refs/heads/{target_branch}"
            source, target = self.sha(repo, source_ref), self.sha(repo, target_ref)
            if not target_branch or not source_branch or not source or not target:
                raise FlowGitError("brak gałęzi głównej albo gałęzi karty")
            listed = self.run(repo, "worktree", "list", "--porcelain", "-z")
            # Rekordy NUL zachowują pełne ścieżki worktree bez cytowania i dzielenia po nowych liniach.
            if not listed.ok:
                raise FlowGitError("nie można odczytać worktree celu")
            worktrees = [dict(line.split(" ", 1) for line in block.split("\0") if " " in line)
                         for block in listed.out.split("\0\0")]
            path = next((w.get("worktree", "") for w in worktrees if w.get("branch") == target_ref), "")
            out = {"sourceRef": source_ref, "sourceBranch": source_branch, "sourceCommit": source,
                   "targetRef": target_ref, "targetBranch": target_branch, "targetCommit": target,
                   "targetWorktree": path or os.path.join(worktrees_dir(repo), f"{target_branch}-main"),
                   "targetWorktreeExists": bool(path), "commits": [], "commitCount": 0,
                   "files": [], "fileCount": 0, "conflicts": [], "conflictCount": 0,
                   "canMerge": False, "readyToMerge": False, "localCollisions": "unassessed",
                   "warnings": ["Lokalne kolizje nie zostały ocenione. Podgląd nie jest zgodą na scalenie."],
                   "resultTree": "", "reason": "", "message": ""}
            commits = self.run(repo, "log", "--no-color", "--no-show-signature", "--format=%H%x00%s", "-z",
                               f"{target}..{source}", "--")
            if not commits.ok:
                out.update(reason="git-error", message="Nie można odczytać commitów scalenia.")
                return out
            fields = commits.out.split("\0")
            out["commits"] = [{"sha": fields[i], "subject": fields[i + 1]}
                              for i in range(0, len(fields) - 1, 2)]
            out["commitCount"] = len(out["commits"])
            if not sandbox_available():
                out.update(reason="no-sandbox", message="Piaskownica gita jest niedostępna.")
                return out
            git_dir = self.run(repo, "rev-parse", "--absolute-git-dir")
            if not git_dir.ok:
                out.update(reason="git-error", message="Nie można ustalić katalogu gita.")
                return out
            runner = StepGit(repo, git_dir.out.strip(), repo, repo + ".step", objects_only=True)
            out.update(self.prepare_merge_tree(runner.run, target, source))
            out["conflictCount"] = len(out["conflicts"])
            if not out["resultTree"]:
                return out
            diff = self.run(repo, "diff-tree", "--no-commit-id", "--no-ext-diff", "--no-textconv", "--no-color",
                            "--ignore-submodules=none", "-r",
                            "--name-status", "-z", "-M", "-l0", target, out["resultTree"], "--")
            if not diff.ok:
                out.update(reason="git-error", message="Nie można odczytać plików drzewa scalenia.")
                return out
            fields = diff.out.split("\0")
            i = 0
            while i < len(fields) - 1:
                status, old_path = fields[i:i + 2]
                i += 2
                new_path = old_path
                if status.startswith(("R", "C")):
                    new_path = fields[i]
                    i += 1
                out["files"].append({"status": status, "oldPath": old_path if status[0] != "A" else "",
                                     "newPath": new_path if status[0] != "D" else ""})
            out["fileCount"] = len(out["files"])
            out["canMerge"] = not out["reason"]
            return out

    def resolve_base(self, repo: str, ref=None, expected=None) -> dict:
        """Lokalna baza startu; wywołujący trzyma lock_for aż do zapisu card.started."""
        if ref is not None and (not isinstance(ref, str) or not ref.startswith("refs/heads/")):
            raise FlowGitError("baza musi być pełnym lokalnym refem refs/heads/…")
        if ref is None:
            branch = self.main_branch(repo)
            if not branch:
                raise FlowGitError("nie ma gałęzi głównej — wybierz lokalną bazę")
            ref = f"refs/heads/{branch}"
        if not self.run(repo, "check-ref-format", ref).ok:
            raise FlowGitError("niepoprawny lokalny ref bazy")
        commit = self.sha(repo, ref)
        if not commit:
            raise FlowGitError("lokalna gałąź bazy nie istnieje")
        if expected is not None and (not isinstance(expected, str) or expected != commit):
            raise FlowGitError("baza zmieniła się — odśwież wybór i uruchom kartę ponownie")
        return {"baseRef": ref, "baseBranch": ref[len("refs/heads/"):], "baseCommit": commit}

    def branch_worktree(self, repo: str, branch: str, path: str, base: str) -> dict:
        """Gałąź `branch` od `base` w worktree `path`; już jest (ta gałąź w tym worktree) — bez zmian."""
        with lock_for(repo):
            listed = self.run(repo, "worktree", "list", "--porcelain")
            for block in listed.out.split("\n\n") if listed.ok else []:
                lines = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
                if os.path.realpath(lines.get("worktree", "")) == os.path.realpath(path):
                    if lines.get("branch") == f"refs/heads/{branch}":
                        return {"branch": branch, "worktree": path, "base": self.sha(repo, branch), "existing": True}
                    raise FlowGitError(f"w {path} jest już inny worktree")
            if os.path.exists(path):
                raise FlowGitError(f"katalog {path} już istnieje")
            check = self.run(repo, "check-ref-format", "--branch", branch)
            if not check.ok:
                raise FlowGitError(f"niepoprawna nazwa gałęzi {branch}")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if self.sha(repo, f"refs/heads/{branch}"):
                added = self.run(repo, "worktree", "add", path, branch, timeout=600)
            else:
                start = self.sha(repo, base)
                if not start:
                    raise FlowGitError(f"nie ma gałęzi {base}")
                added = self.run(repo, "worktree", "add", "--no-track", "-b", branch, path, start, timeout=600)
            if not added.ok:
                raise FlowGitError("git worktree add: " + (added.err.splitlines() or ["błąd"])[0])
            return {"branch": branch, "worktree": path, "base": self.sha(repo, branch), "existing": False}

    def diff(self, repo: str, card_branch: str, ticket_branch: str) -> str:
        """Diff obiektów bez wywołań zewnętrznego diffa ani textconv."""
        result = self.run(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary",
                          f"refs/heads/{card_branch}", f"refs/heads/{ticket_branch}", "--")
        if not result.ok:
            raise FlowGitError("nie można odczytać diffu ticketu: " + (result.err.splitlines() or ["błąd"])[0])
        if len(result.out) > 300_000:
            raise FlowGitError("diff ticketu przekracza 300 000 znaków — podziel pracę na mniejszy ticket")
        return result.out

    def review_diff(self, repo: str, card_branch: str, ticket_branch: str) -> str:
        """Diff do review: pełny, gdy mieści się w limicie; większy (np. ticket scalający gałęzie, K-33/PT-108,
        3,5 mln znaków) — podsumowanie `--stat` i polecenia, którymi recenzent przegląda go po ścieżkach."""
        try:
            return self.diff(repo, card_branch, ticket_branch)
        except FlowGitError as exc:
            if "przekracza" not in str(exc):
                raise
        stat = self.run(repo, "diff", "--no-ext-diff", "--no-textconv", "--stat=200,150",
                        f"refs/heads/{card_branch}", f"refs/heads/{ticket_branch}", "--")
        if not stat.ok:
            raise FlowGitError("nie można odczytać podsumowania diffu ticketu: " + (stat.err.splitlines() or ["błąd"])[0])
        lines = stat.out.splitlines()
        shown = lines if len(lines) <= 400 else lines[:399] + [f"… ({len(lines) - 400} plików pominięto) …", lines[-1]]
        merges = self.run(repo, "log", "--merges", "--format=%h %s", f"refs/heads/{card_branch}..refs/heads/{ticket_branch}")
        return ("Diff jest za duży, żeby go wkleić (ponad 300 000 znaków). Poniżej podsumowanie; przeglądaj zmiany "
                "poleceniami tylko do odczytu, ścieżka po ścieżce, np.\n"
                f"  git diff refs/heads/{card_branch} refs/heads/{ticket_branch} -- <ścieżka>\n"
                f"  git log --oneline refs/heads/{card_branch}..refs/heads/{ticket_branch}\n"
                "Skup się na tym, co ticket zmienił sam (rozwiązania konfliktów, nowe pliki, testy), a nie na treści "
                "scalanych gałęzi, które przeszły już własne review.\n\n"
                + ("Commity scalające:\n" + merges.out.strip() + "\n\n" if merges.ok and merges.out.strip() else "")
                + "\n".join(shown) + "\n")

    def merged_tip(self, repo: str, card_branch: str, ticket_branch: str, worktree: str,
                   expected_tip: str, card_tip: str) -> str:
        """Potwierdza rozwiązanie konfliktu: oba zatwierdzone tipy są przodkami czystej gałęzi karty."""
        with lock_for(repo):
            if not expected_tip or self.sha(repo, f"refs/heads/{ticket_branch}") != expected_tip:
                raise FlowGitError("gałąź ticketu zmieniła się po review")
            tip = self.sha(repo, f"refs/heads/{card_branch}")
            if not card_tip or not tip or not self.run(repo, "merge-base", "--is-ancestor", expected_tip, tip).ok:
                raise FlowGitError("rozwiązanie konfliktu nie scala zatwierdzonego commita ticketu")
            if not self.run(repo, "merge-base", "--is-ancestor", card_tip, tip).ok:
                raise FlowGitError("rozwiązanie konfliktu utraciło wcześniejsze zmiany karty")
            listed = self.run(repo, "worktree", "list", "--porcelain")
            if not listed.ok or not any(
                os.path.realpath(block.get("worktree", "")) == os.path.realpath(worktree)
                and block.get("branch") == f"refs/heads/{card_branch}"
                for block in (dict(line.split(" ", 1) for line in part.splitlines() if " " in line)
                              for part in listed.out.split("\n\n"))
            ):
                raise FlowGitError("worktree karty nie należy do jej gałęzi")
            if not sandbox_available():
                raise FlowGitError("piaskownica gita karty jest niedostępna")
            try:
                git_dir = resolve_git_dir(repo, worktree)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita karty: {exc}") from exc
            status = StepGit(repo, git_dir, worktree, worktree + ".step").run(
                "status", "--porcelain", "--untracked-files=all")
            if not status.ok or status.out.strip():
                raise FlowGitError("worktree karty ma lokalne zmiany po rozwiązywaniu konfliktu")
            return tip

    def clean_tip(self, repo: str, branch: str, path: str, *, untracked: bool = True) -> str:
        """Przed i po review potwierdza, że recenzent nie zmienił gałęzi ani plików ticketu. `untracked=False` (po
        review): liczą się tylko śledzone pliki — nieśledzony wynik pomiaru recenzenta nie zmienia tego, co trafi do
        scalenia (04.11, K-43/BO-9: review odrzucane 9 razy przez `.k43-review-tune.json`). Przed review nieśledzone
        pliki autora dalej odsyłają krok do sprzątnięcia (K-35/PT-132)."""
        with lock_for(repo):
            tip = self.sha(repo, f"refs/heads/{branch}")
            if not tip or not sandbox_available():
                raise FlowGitError("nie można bezpiecznie sprawdzić worktree ticketu")
            try:
                git_dir = resolve_git_dir(repo, path)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree ticketu: {exc}") from exc
            status = StepGit(repo, git_dir, path, path + ".step").run(
                "status", "--porcelain", "--untracked-files=all" if untracked else "--untracked-files=no")
            if not status.ok or _foreign_changes(status.out):
                raise FlowGitError("worktree ticketu ma lokalne zmiany")
            return tip

    def untracked(self, repo: str, path: str) -> list[str]:
        """Nieśledzone pliki worktree (ścieżki względne), bez narzędzi agentów użytkownika; pusta lista przy błędzie."""
        try:
            git_dir = resolve_git_dir(repo, path)
        except StepError:
            return []
        status = StepGit(repo, git_dir, path, path + ".step").run("status", "--porcelain", "-z",
                                                                  "--untracked-files=all")
        if not status.ok:
            return []
        from ..tasks.agentcmd import TOOLING_DIRS
        prefixes = tuple(d.strip("/") + "/" for d in TOOLING_DIRS)
        return sorted(item[3:] for item in status.out.split("\0")
                      if item.startswith("?? ") and not item[3:].startswith(prefixes))

    def commit_plan(self, repo: str, branch: str, worktree: str, files: dict[str, bytes]) -> dict:
        """Zapisuje migawkę zatwierdzonego planu na gałęzi karty bez podążania za dowiązaniami."""
        with lock_for(repo):
            if not sandbox_available():
                raise FlowGitError("piaskownica gita karty jest niedostępna")
            listed = self.run(repo, "worktree", "list", "--porcelain")
            if not listed.ok or not any(
                os.path.realpath(part.get("worktree", "")) == os.path.realpath(worktree)
                and part.get("branch") == f"refs/heads/{branch}"
                for part in (dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
                             for block in listed.out.split("\n\n"))
            ):
                raise FlowGitError("worktree karty nie należy do gałęzi planu")
            try:
                git_dir = resolve_git_dir(repo, worktree)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree karty: {exc}") from exc
            work_git = StepGit(repo, git_dir, worktree, worktree + ".step")
            status = work_git.run("status", "--porcelain", "--untracked-files=all")
            allowed = set(files)
            if not status.ok or any(line[3:] not in allowed for line in status.out.splitlines() if len(line) >= 4):
                raise FlowGitError("worktree karty ma obce lokalne zmiany")
            for path, content in files.items():
                try:
                    _write_plan_file(worktree, path, content)
                except OSError as exc:
                    raise FlowGitError(f"nie można zapisać planu w worktree karty: {exc}") from exc
            staged = work_git.run("add", "--", *files)
            if not staged.ok:
                raise FlowGitError("nie można dodać planu do indeksu karty: " + staged.err)
            changed = work_git.run("diff", "--cached", "--quiet", "--", *files)
            if changed.code == 0:
                return {"commit": self.sha(repo, branch), "already": True}
            if changed.code != 1:
                raise FlowGitError("nie można sprawdzić zmian planu w indeksie karty")
            made = work_git.run("commit", "-m", "docs(plan): update card ticket plan", "--", *files)
            if not made.ok:
                raise FlowGitError("nie można zapisać planu na gałęzi karty: " + made.err)
            return {"commit": self.sha(repo, branch), "already": False}

    def remove_worktree(self, repo: str, branch: str, path: str) -> dict:
        """Sprząta worktree po APPROVE pod tą samą blokadą co zakładanie i scalanie."""
        with lock_for(repo):
            listed = self.run(repo, "worktree", "list", "--porcelain")
            if not listed.ok:
                raise FlowGitError("nie można odczytać listy worktree")
            found = next((dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
                          for block in listed.out.split("\n\n")
                          if any(line == f"worktree {os.path.realpath(path)}" for line in block.splitlines())), None)
            if found is None:
                return {"removed": True, "already": True}
            if found.get("branch") != f"refs/heads/{branch}":
                raise FlowGitError("worktree należy do innej gałęzi — nie usuwam")
            if not sandbox_available():
                raise FlowGitError("piaskownica gita ticketu jest niedostępna")
            try:
                git_dir = resolve_git_dir(repo, path)
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree ticketu: {exc}") from exc
            git = StepGit(repo, git_dir, path, path + ".step")
            tracked = git.run("status", "--porcelain", "--untracked-files=no")
            if not tracked.ok or tracked.out.strip():
                raise FlowGitError("worktree ma lokalne zmiany — nie usuwam")
            # Nieśledzone pliki po scaleniu (dane wejściowe, wyniki pomiarów) nie przepadają: idą do katalogu kroku
            # ticketu `pozostawione-po-scaleniu/`, a worktree jest usuwany (04.11, K-43/BO-9: karta stała
            # z flow.error „worktree ma lokalne zmiany — nie usuwam” przez kopię zestawu klatek).
            status = git.run("status", "--porcelain", "-z", "--untracked-files=all")
            if not status.ok:
                raise FlowGitError("worktree ma lokalne zmiany — nie usuwam")
            from ..tasks.agentcmd import TOOLING_DIRS
            prefixes = tuple(d.strip("/") + "/" for d in TOOLING_DIRS)
            archive = os.path.realpath(os.path.join(path + ".step", "pozostawione-po-scaleniu"))
            root = os.path.realpath(path)
            for item in status.out.split("\0"):
                rel = item[3:] if item.startswith("?? ") else ""
                if not rel or rel.startswith(prefixes):
                    continue
                source = os.path.realpath(os.path.join(root, rel))
                target = os.path.realpath(os.path.join(archive, rel))
                if source.startswith(root + os.sep) and target.startswith(archive + os.sep) and os.path.lexists(source):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.move(source, target)
            # Zostały wyłącznie nieśledzone narzędzia agentów użytkownika (kopie z zewnętrznej synchronizacji,
            # K-15/LEGAL-60) — git bez --force nie usunie worktree z nieśledzonymi plikami.
            force = ["--force"] if status.out.strip() else []
            removed = self.run(repo, "worktree", "remove", *force, path, timeout=600)
            if not removed.ok:
                raise FlowGitError("nie udało się usunąć worktree: " + (removed.err.splitlines() or ["błąd"])[0])
            return {"removed": True, "already": False}

    def merge(self, repo: str, card_branch: str, ticket_branch: str, card_worktree: str, message: str,
              *, expected_tip: str) -> dict:
        """Scala dokładnie zatwierdzony commit; sukces oznacza zgodność refu, indeksu i plików worktree."""
        with lock_for(repo):
            old = self.sha(repo, f"refs/heads/{card_branch}")
            tip = self.sha(repo, f"refs/heads/{ticket_branch}")
            if not old or not tip:
                raise FlowGitError(f"nie ma gałęzi {card_branch if not old else ticket_branch}")
            if not expected_tip or tip != expected_tip:
                raise FlowGitError("gałąź ticketu zmieniła się po zatwierdzeniu review")
            if not card_worktree or not os.path.isdir(card_worktree):
                raise FlowGitError("nie ma worktree karty")
            listed = self.run(repo, "worktree", "list", "--porcelain")
            if not listed.ok or not any(
                os.path.realpath(block.get("worktree", "")) == os.path.realpath(card_worktree)
                and block.get("branch") == f"refs/heads/{card_branch}"
                for block in (dict(line.split(" ", 1) for line in part.splitlines() if " " in line)
                              for part in listed.out.split("\n\n"))
            ):
                raise FlowGitError("worktree karty nie jest przypisany do jej gałęzi")
            if not sandbox_available():
                raise FlowGitError("piaskownica gita karty jest niedostępna")
            try:
                git_dir = (os.path.join(os.path.realpath(repo), ".git")
                           if os.path.realpath(card_worktree) == os.path.realpath(repo)
                           else resolve_git_dir(repo, card_worktree))
            except StepError as exc:
                raise FlowGitError(f"nie można ustalić katalogu gita worktree karty: {exc}") from exc
            work_git = StepGit(repo, git_dir, card_worktree, card_worktree + ".step")

            def tree_of(commit: str) -> str:
                result = self.run(repo, "rev-parse", "--verify", f"{commit}^{{tree}}")
                if not result.ok:
                    raise FlowGitError("nie można odczytać drzewa commita karty")
                return result.out.strip()

            def index_tree() -> str:
                result = work_git.run("write-tree")
                if not result.ok:
                    raise FlowGitError("nie można odczytać indeksu worktree karty: " + result.err)
                return result.out.strip()

            def dirty_files() -> set[str]:
                work_git.run("update-index", "--refresh")
                result = work_git.run("diff-files", "--name-only", "-z", "--")
                if not result.ok:
                    raise FlowGitError("worktree karty ma lokalne zmiany albo błąd gita: " + result.err)
                return {name for name in result.out.split("\0") if name}

            def changed(source: str, target: str) -> set[str]:
                result = self.run(repo, "diff-tree", "-r", "--name-only", "-z", "--no-renames", source, target)
                if not result.ok:
                    raise FlowGitError("nie można odczytać zmian scalenia")
                return {name for name in result.out.split("\0") if name}

            def clean_files(source: str, target: str) -> None:
                # Niezacommitowane zmiany użytkownika w plikach, których scalenie nie dotyka, zostają nietknięte —
                # jak przy zwykłym `git merge` (04.11, K-43: główny katalog BO z pracą w bo-catalog blokował scalenie
                # karty z tools/hud-timeline). Zmieniony plik, który scalenie zmienia, dalej blokuje.
                overlap = sorted(dirty_files() & changed(source, target))
                if overlap:
                    raise FlowGitError("worktree karty ma lokalne zmiany w scalanych plikach: " + ", ".join(overlap[:5]))

            def sync(source: str, target: str) -> None:
                clean_files(source, target)
                result = work_git.run("read-tree", "-m", "-u", source, target)
                if not result.ok:
                    raise FlowGitError("worktree karty nie przyjął scalenia (lokalne zmiany?): "
                                       + (result.err.splitlines() or ["błąd"])[0])
                if index_tree() != tree_of(target):
                    raise FlowGitError("indeks worktree karty nie odpowiada scaleniu")

            current_tree = tree_of(old)
            staged_tree = index_tree()
            if self.run(repo, "merge-base", "--is-ancestor", tip, old).ok:
                if staged_tree != current_tree:
                    parent = self.run(repo, "rev-parse", "--verify", "--quiet", f"{old}^1")
                    if not parent.ok or staged_tree != tree_of(parent.out.strip()):
                        raise FlowGitError("indeks worktree karty nie odpowiada gałęzi ani poprzedniemu scaleniu")
                    sync(parent.out.strip(), old)
                return {"commit": old, "already": True}
            prepared = self.prepare_merge_tree(work_git.run, old, tip)
            if prepared["reason"]:
                raise FlowGitError(prepared["message"])
            target_tree = prepared["resultTree"]
            if staged_tree not in (current_tree, target_tree):
                raise FlowGitError("indeks worktree karty nie odpowiada gałęzi ani przygotowanemu scaleniu")
            args = ("commit-tree", target_tree, "-p", old, "-p", tip, "-m", message)
            made = self.run(repo, *args)
            if not made.ok:
                made = self.run(repo, *FALLBACK_IDENTITY, *args)
            if not made.ok:
                raise FlowGitError("git commit-tree: " + (made.err.splitlines() or ["błąd"])[0])
            commit = made.out.strip()
            if staged_tree == current_tree:
                sync(old, commit)
            else:
                clean_files(old, commit)    # indeks już przygotowany (przerwane scalenie) — pliki muszą się zgadzać
            moved = self.run(repo, "update-ref", "-m", message, f"refs/heads/{card_branch}", commit, old)
            if not moved.ok:
                latest = self.sha(repo, f"refs/heads/{card_branch}")
                if latest and index_tree() == target_tree:
                    # Ref pozostał stary (np. błąd blokady): przed zapisaniem wyniku
                    # ok:false przywróć stare pliki i indeks. Gdy wygrał obcy git,
                    # wyrównaj worktree do jego refu. Obie operacje są w piaskownicy.
                    sync(commit, latest)
                if latest == old:
                    raise FlowGitError("nie udało się przesunąć gałęzi karty")
                raise FlowGitError("gałąź karty zmieniła się w trakcie scalania")
            return {"commit": commit, "already": False}


def _write_plan_file(worktree: str, rel: str, content: bytes) -> None:
    """Otwiera każdy składnik ścieżki z O_NOFOLLOW; repo nie może przekierować zapisu dowiązaniem."""
    parts = rel.split("/")
    if not parts or any(not part or part in (".", "..") for part in parts) or not isinstance(content, bytes):
        raise OSError("niepoprawna ścieżka planu")
    folder = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=folder)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = child
        target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                         0o644, dir_fd=folder)
        try:
            if not stat.S_ISREG(os.fstat(target).st_mode):
                raise OSError("plik planu nie jest zwykłym plikiem")
            view = memoryview(content)
            while view:
                written = os.write(target, view)
                if written <= 0:
                    raise OSError("niepełny zapis planu")
                view = view[written:]
            os.fsync(target)
        finally:
            os.close(target)
    finally:
        os.close(folder)
