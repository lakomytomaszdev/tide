"""Plan i własność brakującego celu. Odczyt nie tworzy zasobów; wykonanie ma zapisany zamiar."""
from __future__ import annotations

import hashlib
import os

from . import gitops


def ref_exists(git, repo, ref):
    # Także ref symboliczny albo nieprowadzący do commita zajmuje nazwę.
    return git.run(repo, "show-ref", "--verify", "--quiet", ref).ok or git.run(
        repo, "symbolic-ref", "--quiet", ref).ok


def name_conflict(git, repo, ref):
    listed = git.run(repo, "for-each-ref", "--format=%(refname)", "refs/heads/")
    if not listed.ok:
        raise gitops.FlowGitError("nie można sprawdzić dostępności lokalnej nazwy")
    return any(name == ref or name.startswith(ref + "/") or ref.startswith(name + "/")
               for name in listed.out.splitlines())


def remote_choices(git, repo, base):
    ref = base.get("baseRef", "") if base.get("status") == "known" else ""
    if not ref.startswith("refs/remotes/"):
        return {"defaultTargetRef": ref, "trackingTargets": [], "createTarget": None}
    listed = git.run(repo, "for-each-ref", "--format=%(refname)%00%(upstream)%00%(symref)", "refs/heads/")
    if not listed.ok:
        raise gitops.FlowGitError("nie można odczytać gałęzi śledzących")
    candidates = [name for name, upstream, symbolic in (line.split("\0") for line in listed.out.splitlines())
                  if upstream == ref and not symbolic]
    proposal = None
    if not candidates and git.sha(repo, ref) and not git.run(repo, "symbolic-ref", "--quiet", ref).ok:
        name = ref[len("refs/remotes/"):]
        remotes = git.run(repo, "remote")
        remote = next((r for r in sorted(remotes.out.splitlines(), key=len, reverse=True)
                       if name.startswith(r + "/")), name.partition("/")[0])
        branch = name[len(remote) + 1:]
        local = "refs/heads/" + branch
        if git.run(repo, "check-ref-format", local).ok:
            occupied = ref_exists(git, repo, local) or name_conflict(git, repo, local)
            proposal = {"ref": local, "name": branch, "kind": "create", "remoteRef": ref,
                        "commit": git.sha(repo, ref), "selectable": not occupied,
                        "reason": "Nazwa jest zajęta. Wybierz inną nazwę lub istniejący lokalny cel." if occupied else "",
                        "label": f"Utwórz lokalną {branch} od {name}"}
    return {"defaultTargetRef": candidates[0] if len(candidates) == 1 else "",
            "trackingTargets": candidates, "createTarget": proposal}


def plan(git, repo, ref, remote_ref=""):
    exists = ref_exists(git, repo, ref)
    if not exists and remote_ref and git.run(repo, "symbolic-ref", "--quiet", remote_ref).ok:
        raise gitops.FlowGitError("zdalny punkt startu stał się refem symbolicznym — wybierz konkretną gałąź")
    commit = git.sha(repo, ref) if exists else git.sha(repo, remote_ref) if remote_ref else ""
    if not commit or (exists and git.run(repo, "symbolic-ref", "--quiet", ref).ok):
        raise gitops.FlowGitError("cel nie istnieje lub nie jest lokalną gałęzią; brak zdalnego punktu startu")
    checkouts = git.branch_checkouts(repo)
    path = checkouts.get(ref, "")
    own = not path
    if own:
        digest = hashlib.sha256((git.repository_key(repo) + "\0" + ref).encode()).hexdigest()[:24]
        path = os.path.join(gitops.worktrees_dir(repo), "merge-target-" + digest)
    return {"createRef": not exists, "createWorktree": own, "path": path,
            "nameConflict": not exists and name_conflict(git, repo, ref),
            "pathOccupied": own and os.path.lexists(path),
            "parentSymlink": own and os.path.realpath(os.path.dirname(path)) != os.path.abspath(os.path.dirname(path)),
            "startCommit": commit, "remoteRef": remote_ref if not exists else "",
            "remoteCommit": commit if not exists else ""}


def provision(git, op, events, checkpoint):
    """Własność refu jest atomowa z jego utworzeniem; katalog ma utrwaloną tożsamość inode."""
    resource = op.get("targetPreparation")
    if not resource:
        return
    repo, ref, before, path = op["repo"], op["targetRef"], op["targetCommitBefore"], op["targetWorktree"]
    owner = "refs/tide/merge-targets/" + op["key"]
    checked = gitops_checked
    if git.run(repo, "symbolic-ref", "--quiet", ref).ok or git.run(repo, "symbolic-ref", "--quiet", owner).ok:
        raise gitops.FlowGitError("ref celu lub dowód własności stał się symboliczny — oczekiwanie")
    checkouts = git.branch_checkouts(repo)
    if resource["createWorktree"] and ref in checkouts and os.path.abspath(checkouts[ref]) != os.path.abspath(path):
        raise gitops.FlowGitError("cel ma nowy checkout — oczekiwanie bez przełączania")
    if resource["createRef"]:
        if git.sha(repo, owner) != before:
            if ref_exists(git, repo, ref) or ref_exists(git, repo, owner):
                raise gitops.FlowGitError("obcy ref celu — oczekiwanie bez nadpisania")
            if git.sha(repo, resource["remoteRef"]) != resource["remoteCommit"]:
                raise gitops.FlowGitError("zdalny punkt startu zmienił się — oczekiwanie")
            # Jedna transakcja refów: po awarii sam zgodny SHA nie wystarcza do przejęcia cudzego refu.
            checked(git.run(repo, "update-ref", "--stdin", input_text=(
                f"start\noption no-deref\nverify {resource['remoteRef']} {resource['remoteCommit']}\n"
                f"create {owner} {before}\ncreate {ref} {before}\nprepare\ncommit\n")))
        if git.sha(repo, ref) != before:
            raise gitops.FlowGitError("utworzony ref celu zmienił commit — oczekiwanie")
        checkpoint("target-ref-created")
    elif git.sha(repo, ref) != before:
        raise gitops.FlowGitError("ref celu zmienił commit — oczekiwanie")
    if not resource["createWorktree"]:
        return
    checkouts = git.branch_checkouts(repo)
    if ref in checkouts and os.path.abspath(checkouts[ref]) != os.path.abspath(path):
        raise gitops.FlowGitError("cel ma nowy checkout — oczekiwanie bez przełączania")
    directory = next((e for e in events if e.get("stage") == "target-directory" and e.get("key") == op["key"]), None)
    if directory is None:
        if os.path.lexists(path):
            raise gitops.FlowGitError("katalog celu jest zajęty — oczekiwanie bez nadpisania")
        parent = os.path.dirname(path)
        if os.path.realpath(parent) != os.path.abspath(parent):
            raise gitops.FlowGitError("katalog worktrees jest dowiązaniem — oczekiwanie")
        os.makedirs(parent, exist_ok=True)
        os.mkdir(path)
        stamp = os.lstat(path)
        directory = {"device": stamp.st_dev, "inode": stamp.st_ino}
        checkpoint("target-directory", **directory)
    stamp = os.lstat(path)
    if os.path.islink(path) or (stamp.st_dev, stamp.st_ino) != (directory["device"], directory["inode"]):
        raise gitops.FlowGitError("katalog celu zmienił właściciela — oczekiwanie")
    if os.path.realpath(os.path.dirname(path)) != os.path.abspath(os.path.dirname(path)):
        raise gitops.FlowGitError("katalog worktrees jest dowiązaniem — oczekiwanie")
    reason = "tide-merge:" + op["key"]
    if ref not in checkouts:
        if os.listdir(path):
            raise gitops.FlowGitError("katalog celu zawiera obce pliki — oczekiwanie")
        checked(git.run(repo, "worktree", "add", "--no-checkout", "--lock", "--reason", reason, "--",
                        path, ref[len("refs/heads/"):], timeout=600))
    listed = checked(git.run(repo, "worktree", "list", "--porcelain", "-z"))
    records = [dict(line.split(" ", 1) for line in block.split("\0") if " " in line)
               for block in listed.split("\0\0")]
    if not any(w.get("branch") == ref and w.get("worktree") == path and w.get("locked") == reason for w in records):
        raise gitops.FlowGitError("worktree nie należy do zapisanej operacji — oczekiwanie")
    from .mergeop import MergeWorktree
    worktree = MergeWorktree(git, repo, op["targetBranch"], path)
    index = checked(git.run(path, "rev-parse", "--path-format=absolute", "--git-path", "index")).strip()
    if not os.path.exists(index):
        if os.listdir(path) != [".git"]:
            raise gitops.FlowGitError("niezainicjowany worktree zawiera obce pliki — oczekiwanie")
        # Filtry checkoutu działają wyłącznie we wspólnej piaskownicy wykonawcy.
        checked(worktree.runner.run("read-tree", "-m", "-u", before))
    checkpoint("target-worktree-created")


def gitops_checked(result):
    if not result.ok:
        raise gitops.FlowGitError("nie można przygotować celu: " + (result.err or "błąd gita"))
    return result.out


def release(git, op):
    """Po ukończeniu gita nie trzymamy blokady własnego worktree w narzędziach użytkownika."""
    if not op.get("targetPreparation", {}).get("createWorktree"):
        return
    listed = gitops_checked(git.run(op["repo"], "worktree", "list", "--porcelain", "-z"))
    records = [dict(line.split(" ", 1) for line in block.split("\0") if " " in line)
               for block in listed.split("\0\0")]
    if any(w.get("worktree") == op["targetWorktree"] and w.get("locked") == "tide-merge:" + op["key"]
           for w in records):
        gitops_checked(git.run(op["repo"], "worktree", "unlock", op["targetWorktree"]))
