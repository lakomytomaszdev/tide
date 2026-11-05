"""Dowody pustej korekty bazy. Wyłącznie lokalne odczyty, bez checkpointów i replay."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from . import baseproposal
from .gitops import FlowGitError
from .mergepreview import checkout_snapshot, file_stamp
from .state import base_of

_RELATIVE_FOLLOWUP = "docs/agents/plans/followups/replay-code.md"
FOLLOWUP = next((p for p in (Path(__file__).resolve().parents[3] / _RELATIVE_FOLLOWUP,
                            Path(__file__).resolve().parents[2] / _RELATIVE_FOLLOWUP) if p.is_file()),
                Path(__file__).resolve().parents[3] / _RELATIVE_FOLLOWUP)
INVALIDATIONS = ["review i domknięcie", "podglądy i zgody na scalenie", "stary kontekst sesji i wznowień",
                 "przypięte cele i mapy generacji"]


def followup():
    """Nie budujemy linku, jeżeli instalacja nie zawiera zatwierdzonego artefaktu."""
    present = FOLLOWUP.is_file()
    return {"key": "K-50-replay-code", "available": present, "path": str(FOLLOWUP) if present else "",
            "message": ("Zapisany szkic K-50-replay-code, poza v1; bez opublikowanej karty na Tablicy."
                        if present else "Szkic K-50-replay-code nie jest dostępny w tej instalacji; "
                        "wymaga odzyskania zatwierdzonego artefaktu. Nie ma odnośnika ani opublikowanej karty.")}


def candidate(git, repo, request):
    if not isinstance(request, dict) or set(request) - {"baseRef", "baseCommit", "step"}:
        raise ValueError("podgląd przyjmuje baseRef, baseCommit i opcjonalny krok propozycji")
    if not isinstance(request.get("baseRef"), str):
        raise ValueError("wybierz pełny lokalnie znany ref bazy")
    return git.resolve_base(repo, request["baseRef"], request.get("baseCommit"))


def local_snapshot(git, repo, path):
    snapshot = checkout_snapshot(git.run, path)
    if not snapshot.get("exists"):
        raise OSError("brak worktree")
    # Obejmujemy także pliki ignorowane oraz tracked z assume-unchanged/skip-worktree.
    files = git.run(path, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    ignored = git.run(path, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    if not files.ok or not ignored.ok:
        raise OSError("nie można odczytać lokalnych plików")
    paths = sorted(set((files.out + ignored.out).split("\0")) - {""})
    root = os.path.realpath(path)

    def local_stamp(rel):
        full = os.path.join(path, rel)
        if os.path.commonpath((root, os.path.realpath(os.path.dirname(full)))) != root:
            raise OSError("plik poza worktree")
        stamp = file_stamp(full)
        if stamp and stat.S_ISDIR(stamp[0]):
            # Zagnieżdżone nieśledzone repozytorium git opisuje jedną ścieżką katalogu.
            with os.scandir(full) as entries:
                names = sorted(entry.name for entry in entries)
            return [stamp[0], {name: local_stamp(os.path.join(rel, name)) for name in names}]
        return stamp

    snapshot.update(allFiles={rel: local_stamp(rel) for rel in paths},
                    ignored=sorted(set(ignored.out.split("\0")) - {""}),
                    repository=git.repository_key(path))
    if snapshot["repository"] != git.repository_key(repo):
        raise OSError("worktree należy do innego repozytorium")
    return snapshot


def preview(git, repo, card, events, st, new, processes):
    reasons, branches, snapshots = [], [], {}

    def block(reason, message, **data):
        reasons.append({"reason": reason, "message": message, **data})

    old = base_of(st)
    if old["status"] != "known":
        block("unknown-base", "Dziennik nie dowodzi starej bazy. Nie odgadujemy jej z HEAD ani merge-base.")
    if st.base_version < 1:
        block("unknown-version", "Historyczna wersja bazy 0 nie uprawnia do korekty.")
    if not st.started or st.card_finished or st.main_merged:
        block("inactive-order", "Brak otwartego zlecenia do korekty.")
    if any(p["active"] for p in processes):
        block("active-process", "Krok jest czynny albo jego zakończenie nie jest potwierdzone. "
              "Użyj istniejącej akcji stop i ponów podgląd po potwierdzonym zakończeniu. "
              "Stop z commitem WIP nadal oznacza pracę i odmowę.")
    if st.open_intents():
        block("pending-action", "Dziennik zawiera niedokończoną operację; granice nie są dowiedzione.")

    # Nie wyznaczamy zakresu z nazw, dat, reflogu ani wiadomości commitów.
    creations = [e for e in events if e.get("type") == "result" and e.get("ok") and
                 e.get("action") in ("card-branch", "ticket-branch", "review-branch", "step-reopen")]
    mapped = {(e.get("branch"), e.get("worktree")) for e in creations}
    unproven = []
    for step in st.steps.values():
        pair = (step.get("branch"), step.get("worktree"))
        if pair not in mapped:
            mapped.add(pair)
            unproven.append({"branch": pair[0], "worktree": pair[1], "ticket": step.get("ticket"),
                             "action": "unproven-step", "existing": True})
    for e in creations + unproven:
        branch, path = e.get("branch") or "", e.get("worktree") or ""
        if not branch or not path:
            block("unknown-boundary", "Brak zapisanej gałęzi/worktree w wyniku utworzenia.")
            continue
        key = (e.get("action"), e.get("key"))
        before = events[:events.index(e)] if e in events else []
        intent = next((i for i in reversed(before) if i.get("type") == "intent" and
                       (i.get("action"), i.get("key")) == key), {})
        start = e.get("base") or ""
        ref = "refs/heads/" + branch
        row = {"branch": branch, "ref": ref, "worktree": path, "ticket": e.get("ticket") or "",
               "startCommit": start, "startRef": intent.get("base") or "", "commit": "", "commits": []}
        branches.append(row)
        valid = (e.get("existing") is False and bool(intent) and
                 re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", start) and
                 intent.get("branch") == branch and intent.get("worktree") == path)
        if not valid:
            block("unknown-boundary", "Dziennik nie dowodzi punktu startu gałęzi " + branch + ".", branch=branch)
        tip = git.sha(repo, ref)
        row["commit"] = tip
        if not tip:
            block("changed-ref", "Zapisany ref zniknął: " + ref, branch=branch)
        symbolic = git.run(repo, "symbolic-ref", "--quiet", ref)
        if symbolic.code != 1:
            block("changed-ref", "Zapisany ref nie jest bezpośrednią gałęzią: " + ref, branch=branch)
        if valid and tip:
            # Wyłącznie sprawdzenie znanej granicy z dziennika; nie szukamy wspólnej bazy do zgadywania zakresu.
            ancestor = git.run(repo, "merge-base", "--is-ancestor", start, tip)
            if not ancestor.ok:
                block("changed-ref", "Ref nie zawiera dowiedzionego punktu startu: " + ref, branch=branch)
            else:
                commits = git.run(repo, "rev-list", "--parents", start + ".." + tip)
                if not commits.ok:
                    block("unknown-boundary", "Nie można odczytać grafu: " + ref, branch=branch)
                else:
                    row["commits"] = [{"sha": line.split()[0], "parents": line.split()[1:]}
                                      for line in commits.out.splitlines()]
                    if row["commits"]:
                        block("contribution", "Commit wkładu na " + ref + "; także dokumenty planu, kod i WIP "
                              "wykluczają korektę v1.", branch=branch, commits=row["commits"])
                    if any(len(c["parents"]) > 1 for c in row["commits"]):
                        block("merge", "Graf zawiera merge; v1 nie przenosi grafu ani obcych merge.", branch=branch)
            if old["status"] == "known" and start != old["baseCommit"]:
                block("nonempty-start", "Punkt startu gałęzi różni się od przypiętej bazy karty.", branch=branch)
        try:
            snapshot = local_snapshot(git, repo, path)
            snapshots[path] = snapshot
            row["localChanges"] = snapshot["changes"]
            row["ignored"] = snapshot["ignored"]
            if snapshot["branch"] != ref or snapshot["head"] != tip:
                block("changed-ref", "Checkout nie odpowiada zapisanej gałęzi: " + ref, branch=branch)
            if any(snapshot["changes"].values()):
                block("local-changes", "Worktree ma staged/unstaged/untracked: " + path, branch=branch)
            if any(entry and (entry[0].islower() or entry[0] == "S")
                   for entry in snapshot["indexEntries"].split("\0")):
                block("unknown-local-state", "Indeks ukrywa stan plików przez assume-unchanged/skip-worktree.",
                      branch=branch)
            if any(snapshot["operations"].values()):
                block("git-operation", "W worktree trwa operacja gita albo indeks jest zablokowany.", branch=branch)
        except (OSError, FlowGitError) as exc:
            block("unknown-local-state", "Nie można dowieść stanu worktree " + path + ": " + str(exc), branch=branch)

    if not any(e.get("action") == "card-branch" for e in creations):
        block("unknown-boundary", "Brak dowiedzionego utworzenia gałęzi karty.")
    # Dziennik może dowodzić pracy również po zewnętrznym cofnięciu/usunięciu refu.
    for e in events:
        sha = (e.get("commit") if e.get("type") == "result" and e.get("ok") and
               e.get("action") in ("plan-commit", "merge") else "")
        wip = e.get("wip") or {}
        sha = sha or (wip.get("commit") if wip.get("committed") else "")
        if sha and sha != old.get("baseCommit"):
            block("recorded-contribution", "Dziennik zachowuje commit wkładu " + sha + ".", commit=sha)
        if wip.get("files") and not wip.get("committed"):
            block("local-changes", "Dziennik wskazuje niezapisane pliki WIP.")
    worktrees = git.run(repo, "worktree", "list", "--porcelain")
    if not worktrees.ok:
        block("unknown-map", "Nie można odczytać mapy worktrees.")
    old_ref_sha = git.sha(repo, old["baseRef"]) if old["status"] == "known" else ""
    old_symbolic = (git.run(repo, "symbolic-ref", "--quiet", old["baseRef"]).code
                    if old["status"] == "known" else 1)
    if old["status"] == "known" and (old_ref_sha != old["baseCommit"] or old_symbolic != 1):
        block("changed-base-ref", "Stary ref zmienił się lub zniknął; przypięta baza pozostaje zachowana.")
    sketch = followup()
    result = {"ok": True, "id": card, "oldBase": old, "oldRefCommit": old_ref_sha, "newBase": new,
              "baseVersion": st.base_version, "branches": branches, "processes": processes,
              "blockedReasons": reasons, "canChange": not reasons, "canExecute": False,
              "invalidations": INVALIDATIONS, "scope": baseproposal.LIMITS, "followup": sketch,
              "instruction": "Zachowaj bazę, aby kontynuować, albo pozostaw kartę w oczekiwaniu i zaplanuj "
              "przeniesienie z zapisanego szkicu K-50-replay-code. Bez ręcznego reset/rebase zarządzanych "
              "gałęzi i bez usuwania katalogów. Nowe zlecenie na poprawnej bazie jest osobne, "
              "bez automatycznego kopiowania pracy.",
              "ignoredPolicy": "Ignorowane dane pozostają w starym worktree; bez przenoszenia i kasowania.",
              "executionMessage": "Potwierdzenie wykonania jest niedostępne do PT-213. "
              "PT-202 dostarczy operację backendu wyłącznie dla pustej generacji."}
    payload = {"events": events, "preview": result, "snapshots": snapshots, "worktrees": worktrees.out}
    result["previewId"] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return result
