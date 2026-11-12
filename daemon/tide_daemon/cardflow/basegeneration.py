"""PT-202: przygotowanie pustej generacji, jedna aktywacja i zachowawcze recovery.

Zamiar jest zgodą na dokładny odcisk, a nie na dzisiejsze HEAD. Nie ma rollbacku,
przenoszenia plików ani sprzątania częściowego wyniku. Wszystkie zasoby są w dzienniku.
"""
from __future__ import annotations

import os

from . import basechange
from .gitops import FlowGitError, lock_for, worktree_for
from .state import fold

ACTION = "change-base"
WAITING = "Przygotowanie generacji czeka na odzyskanie zasobów."


def intent_of(st):
    return next((i for i in reversed(list(st.intents.values())) if i.get("action") == ACTION and
                 i.get("requestSeq") == (st.started or {}).get("seq")), None)


class BaseGeneration:
    def __init__(self, engine):
        self.engine = engine
        self.git = engine.git
        self.journal = engine.journal

    def resolve(self, card, request):
        from .engine import FlowError
        if (set(request) - {"choice", "baseRef", "baseCommit", "previewId", "step"} or
                not isinstance(request.get("previewId"), str) or not request["previewId"] or
                not isinstance(request.get("baseRef"), str)):
            raise FlowError("bad-request", "change-base wymaga pełnego baseRef i previewId podglądu")
        repo = self.engine._repo(card)
        with lock_for(repo), self.journal.lock:
            st = fold(self.journal.read(card))
            intent = intent_of(st)
            if intent:
                if (intent["previewId"] != request["previewId"] or intent["newBase"]["baseRef"] != request["baseRef"] or
                        ("baseCommit" in request and request["baseCommit"] != intent["newBase"]["baseCommit"])):
                    raise FlowError("stale-preview", "Karta ma już inny zamiar korekty; zachowano jego zasoby.")
                self.resume(card, intent)
                return self.response(card, intent)
            try:
                preview = self.engine.preview_card_base_change(card, {
                    k: v for k, v in request.items() if k in ("baseRef", "baseCommit", "step")})
            except FlowError as exc:
                raise FlowError("stale-preview", str(exc)) from None
            if not preview["canChange"] or preview["previewId"] != request["previewId"]:
                raise FlowError("stale-preview", "Stan zmienił się albo nie jest pusty. Ponów podgląd; bez aktywacji.")
            events = self.journal.read(card)
            key = card.lower() + "-" + preview["previewId"][:24]
            version = st.base_version + 1
            generation = []
            seen = {}
            for (action, result_key), res in st.results.items():
                if action not in ("card-branch", "ticket-branch", "review-branch", "step-reopen") or not res.get("ok"):
                    continue
                pair = (res["branch"], res["worktree"])
                if pair not in seen:
                    n = len(seen)
                    prefix = "card" if action == "card-branch" else "ticket"
                    ident = card if prefix == "card" else res.get("ticket") or card
                    branch = f"{prefix}/{ident.lower()}-generation-{version}-{key}-{n}"
                    path = worktree_for(repo, branch)
                    if os.path.lexists(path) or self.git.sha(repo, "refs/heads/" + branch):
                        raise FlowError("stale-preview", "Kolizja zasobu generacji: " + branch)
                    snap = basechange.local_snapshot(self.git, repo, res["worktree"])
                    seen[pair] = {"branch": branch, "worktree": path,
                                  "ownerRef": f"refs/tide/base-generations/{key}/new-{n}",
                                  "archiveRef": f"refs/tide/base-generations/{key}/old-{n}",
                                  "ignored": snap["ignored"]}
                generation.append({**seen[pair], "action": action, "key": result_key,
                                   "ticket": res.get("ticket") or "", "base": preview["newBase"]["baseCommit"],
                                   "oldBranch": pair[0], "oldWorktree": pair[1], "oldCommit": res["base"]})
            # Wykaz ignorowanych danych nie może otworzyć okna między podglądem a zamiarem.
            check = self.engine.preview_card_base_change(card, {
                k: v for k, v in request.items() if k in ("baseRef", "baseCommit", "step")})
            if check["previewId"] != preview["previewId"] or events != self.journal.read(card):
                raise FlowError("stale-preview", "Stan zmienił się przed zapisem zamiaru.")
            intent = self.journal.append(card, "intent", action=ACTION, key=key, operationId=key,
                                         requestSeq=st.started["seq"], beforeSeq=st.last_seq, repo=repo,
                                         previewId=preview["previewId"], decisionStep=preview["decisionStep"],
                                         oldBase=preview["oldBase"], newBase=preview["newBase"],
                                         previousBaseVersion=st.base_version, baseVersion=version,
                                         generation=generation, lockReason="Tide base-generation " + key)
            self.resume(card, intent)
            return self.response(card, intent)

    def response(self, card, intent):
        st = fold(self.journal.read(card))
        changed = st.base_change.get("operationId") == intent["key"]
        return {"ok": True, "id": card, "operationId": intent["key"], "changed": changed,
                "waiting": not changed or st.base_validation_required,
                "baseVersion": st.base_version, "generation": intent["generation"],
                "reason": "Wymagane nowe domknięcie karty." if changed else st.base_generation_wait.get("reason", WAITING)}

    def checkpoint(self, card, intent, stage, **data):
        if any(e.get("type") == "base-change.checkpoint" and e.get("key") == intent["key"] and
               e.get("stage") == stage and e.get("resource") == data.get("resource")
               for e in self.journal.read(card)):
            return
        self.journal.append(card, "base-change.checkpoint", key=intent["key"], stage=stage,
                            oldBase=intent["oldBase"], newBase=intent["newBase"],
                            previousBaseVersion=intent["previousBaseVersion"], baseVersion=intent["baseVersion"],
                            generation=intent["generation"], **data)

    def qualify(self, card, intent):
        events = self.journal.read(card)
        original = [e for e in events if e["seq"] <= intent["beforeSeq"]]
        # Inne decyzje/praca po zamiarze nie są objęte pierwotną zgodą.
        if any(e["seq"] > intent["seq"] and not (
                e.get("key") == intent["key"] and e.get("type") in
                ("base-change.checkpoint", "base-change.waiting")) for e in events):
            raise FlowGitError("Dziennik zmienił się po zamiarze korekty.")
        st = fold(original)
        new = self.git.resolve_base(intent["repo"], intent["newBase"]["baseRef"], intent["newBase"]["baseCommit"])
        current = basechange.preview(self.git, intent["repo"], card, original, st, new,
                                     self.engine._base_change_processes(st), operation=intent)
        if not current["canChange"] or current["previewId"] != intent["previewId"]:
            raise FlowGitError("stale-preview: stare/nowe SHA, mapa, procesy lub lokalne dane zmieniły się po zgodzie.")

    def protect(self, intent, row):
        listed = self.git.run(intent["repo"], "worktree", "list", "--porcelain")
        if not listed.ok:
            raise FlowGitError("Nie można odczytać blokad worktrees.")
        block = next((b for b in listed.out.split("\n\n") if
                      b.splitlines() and b.splitlines()[0] == "worktree " + row["oldWorktree"]), "")
        if not block:
            raise FlowGitError("Zniknął zachowywany katalog: " + row["oldWorktree"])
        if not any(line == "locked" or line.startswith("locked ") for line in block.splitlines()):
            self.run(intent, "worktree", "lock", "--reason", intent["lockReason"], row["oldWorktree"])

    def run(self, intent, *args, **kwargs):
        result = self.git.run(intent["repo"], *args, **kwargs)
        if not result.ok:
            raise FlowGitError("Przygotowanie generacji: " + result.err.strip())
        return result

    def prepare(self, card, intent, row):
        repo, sha = intent["repo"], row["base"]
        ref = "refs/heads/" + row["branch"]
        if any(self.git.run(repo, "symbolic-ref", "--quiet", name).code != 1
               for name in (ref, row["ownerRef"], row["archiveRef"])):
            raise FlowGitError("Obcy symboliczny ref generacji: " + ref)
        owner = self.git.sha(repo, row["ownerRef"])
        if not owner:
            if self.git.sha(repo, ref) or os.path.lexists(row["worktree"]) or self.git.sha(repo, row["archiveRef"]):
                raise FlowGitError("Obcy ref lub katalog generacji: " + row["branch"])
            # Ref właściciela i gałąź powstają w tej samej transakcji CAS. Recovery nie adoptuje cudzego refu.
            self.run(intent, "update-ref", "--no-deref", "--stdin", input_text=(
                f"start\ncreate {row['ownerRef']} {sha}\ncreate {ref} {sha}\n"
                f"create {row['archiveRef']} {row['oldCommit']}\nprepare\ncommit\n"))
        if (self.git.sha(repo, row["ownerRef"]) != sha or self.git.sha(repo, ref) != sha or
                self.git.sha(repo, row["archiveRef"]) != row["oldCommit"] or
                self.git.run(repo, "symbolic-ref", "--quiet", ref).code != 1):
            raise FlowGitError("Ref generacji zmienił właściciela albo SHA: " + ref)
        if not os.path.lexists(row["worktree"]):
            self.run(intent, "worktree", "add", "--lock", "--reason", intent["lockReason"],
                     row["worktree"], row["branch"], timeout=600)
        self.verify(intent, row)
        self.checkpoint(card, intent, "resource-prepared", resource=ref)

    def verify(self, intent, row):
        repo, sha, ref = intent["repo"], row["base"], "refs/heads/" + row["branch"]
        listed = self.run(intent, "worktree", "list", "--porcelain")
        block = next((b for b in listed.out.split("\n\n") if b.splitlines() and
                      b.splitlines()[0] == "worktree " + row["worktree"]), "")
        if ("branch " + ref not in block.splitlines() or
                "locked " + intent["lockReason"] not in block.splitlines() or
                any(self.git.run(repo, "symbolic-ref", "--quiet", r).code != 1 for r in
                    (ref, row["ownerRef"], row["archiveRef"])) or
                self.git.sha(repo, ref) != sha or self.git.sha(repo, row["ownerRef"]) != sha or
                self.git.sha(repo, row["archiveRef"]) != row["oldCommit"]):
            raise FlowGitError("Niepewna własność albo mapa nowego worktree: " + row["worktree"])
        snapshot = basechange.local_snapshot(self.git, repo, row["worktree"])
        if any(entry and (entry[0].islower() or entry[0] == "S")
               for entry in snapshot["indexEntries"].split("\0")):
            raise FlowGitError("Indeks nowego worktree ukrywa stan plików przez "
                               "assume-unchanged/skip-worktree: " + row["worktree"])
        if (snapshot["branch"] != ref or snapshot["head"] != sha or any(snapshot["changes"].values()) or
                any(snapshot["operations"].values()) or snapshot["ignored"]):
            raise FlowGitError("Niepewny lub zmieniony nowy worktree: " + row["worktree"])

    def resume(self, card, intent):
        # Wywołujący zawsze trzyma blokadę repo. Awaria po aktywacji ponawia tylko projekcję.
        st = fold(self.journal.read(card))
        if st.base_change.get("operationId") == intent["key"]:
            self.finish(card, intent)
            return
        try:
            self.qualify(card, intent)
            for row in intent["generation"]:
                self.protect(intent, row)
                self.prepare(card, intent, row)
            self.checkpoint(card, intent, "prepared")
            self.qualify(card, intent)
            for row in intent["generation"]:
                self.verify(intent, row)
            # Cała mapa została sprawdzona; tylko ten zapis wybiera generację i wersję.
            self.journal.append(card, "card.base-changed", operationId=intent["key"],
                                previousBaseVersion=intent["previousBaseVersion"], baseVersion=intent["baseVersion"],
                                base=intent["newBase"], generation=intent["generation"],
                                validationRequired=True, decisionStep=intent["decisionStep"])
            if fold(self.journal.read(card)).base_change.get("operationId") != intent["key"]:
                raise FlowGitError("Dziennik nie przyjął pełnej mapy generacji; zachowano starą mapę.")
        except (FlowGitError, OSError) as exc:
            st = fold(self.journal.read(card))
            if st.base_change.get("operationId") == intent["key"]:
                # Kopia dziennika mogła utrwalić aktywację przed błędem projekcji pliku tablicy.
                # Nie zapisujemy oczekiwania na przygotowanie już aktywnej generacji.
                self.finish(card, intent)
                return
            reason = str(exc)
            if st.base_generation_wait.get("reason") != reason:
                self.journal.append(card, "base-change.waiting", key=intent["key"], reason=reason,
                                    generation=intent["generation"], oldBase=intent["oldBase"], newBase=intent["newBase"])
            return
        self.finish(card, intent)

    def finish(self, card, intent):
        st = fold(self.journal.read(card))
        if (ACTION, intent["key"]) not in st.results:
            self.journal.append(card, "result", action=ACTION, key=intent["key"], ok=True,
                                operationId=intent["key"], baseVersion=intent["baseVersion"], generation=intent["generation"])
        self.engine._project_base(card)

    def recover(self, card):
        # K-105 PT-415: projekcja z pamięci `Journal.state` (ten sam `fold` odczytu) zamiast złożenia co tick.
        intent = intent_of(self.journal.state(card))
        if intent:
            with lock_for(intent["repo"]), self.journal.lock:
                self.resume(card, intent)
