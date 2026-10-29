"""Review propozycji i zmiany ticketów zapisane w dzienniku karty."""
import json
import os

from . import execution, plans
from .state import fold, ticket_of


class PlanEditing:
    @staticmethod
    def _plan_spec(path):
        candidate = os.path.join(os.path.dirname(path), "spec.md")
        try:
            if os.path.islink(candidate) or os.path.getsize(candidate) > plans.plan_mod.MAX_BYTES:
                return ""
            with open(candidate, encoding="utf-8") as fh:
                return fh.read(plans.plan_mod.MAX_BYTES)
        except (OSError, UnicodeError):
            return ""

    def _advance_plan_review(self, card, st):
        proposal = st.plan_proposed
        if not proposal or st.plan_reviewed or (st.plan_approved and not (st.plan_revision or {}).get("pending")) or st.paused:
            return False
        if proposal.get("sourceKind") == "plan-revise":
            source = st.steps.get(proposal.get("sourceStep")) or {}
            reviewer = next((s for s in reversed(list(st.steps.values())) if s.get("kind") == "plan-review" and s["seq"] < source.get("seq", 0)), {})
            self._record_plan_review(card, verdict="REQUEST_CHANGES", reviewer=reviewer.get("executor", ""),
                                findings=source.get("reviewTip") or "", corrected=True)
            return True
        steps = [s for s in st.steps.values() if s.get("kind") in ("plan-review", "plan-revise")
                 and s.get("seq", 0) > proposal["seq"]]
        if not steps:
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg else self.catalog()
            author = catalog.get(proposal.get("planner") or proposal["rows"][0]["executor"])
            options = [e for e in catalog.executors if e.name != author.name and e.model.lower() != author.model.lower()
                       and (e.provider == "local") == (author.provider == "local")]
            if not options:
                from .engine import FlowError
                raise FlowError("no-plan-reviewer", "brak niezależnego recenzenta planu")
            selected = execution.resolve(options[0].name, catalog, config=cfg, probe=self.model_probe, which=self.which)
            self._plan_step(card, st, selected, "plan-review", self._review_prompt(card, proposal))
            return True
        step = steps[-1]
        done = st.finished.get(step["step"])
        if not done:
            return True
        if done.get("result") != "done":
            self._plan_step_decision(card, st, step, done)
            return True
        if step["kind"] == "plan-revise":
            reply = self.propose_plan(card, os.path.join(step["stepDir"], "out", "plan.md"), continuation=True, step=step["step"])
            if reply["ok"]:
                self._record_plan_review(card, verdict="REQUEST_CHANGES", reviewer=steps[0]["executor"],
                                    findings=step.get("reviewTip") or "", corrected=True)
            else:
                self._record_plan_review(card, verdict="REQUEST_CHANGES", reviewer=steps[0]["executor"],
                                    findings="Poprawa nie przeszła sprawdzenia formatu: " + "; ".join(reply["errors"]), corrected=False)
            return True
        try:
            with open(os.path.join(step["stepDir"], "out", "plan-review.json"), encoding="utf-8") as fh:
                report = json.loads(fh.read(plans.plan_mod.MAX_BYTES))
            if report.get("verdict") not in ("APPROVE", "REQUEST_CHANGES") or not isinstance(report.get("findings"), str):
                raise ValueError("niepoprawny wynik review planu")
        except (OSError, ValueError, AttributeError):
            from .engine import FlowError
            raise FlowError("bad-plan-review", "recenzent nie zapisał poprawnego plan-review.json") from None
        if report["verdict"] == "REQUEST_CHANGES":
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg else self.catalog()
            selected = execution.resolve(proposal["planner"], catalog, config=cfg, probe=self.model_probe, which=self.which)
            self._plan_step(card, st, selected, "plan-revise", self._revision_prompt(card, proposal, report["findings"]), report["findings"])
        else:
            self._record_plan_review(card, reviewer=step["executor"], verdict=report["verdict"], findings=report["findings"], corrected=False)
        return True

    def _record_plan_review(self, card, **report):
        st = fold(self.journal.read(card))
        revision = st.plan_revision or {}
        if revision.get("pending"):
            difference = self._difference(revision["baseline"], st.plan_proposed["rows"])
            if difference != revision.get("difference"):
                self.journal.append(card, "plan.revision-ready", pending=True, baseline=revision["baseline"],
                                    feedback=revision["feedback"], difference=difference)
        self.journal.append(card, "plan.reviewed", **report)

    def _plan_step_decision(self, card, st, step, done):
        result = done.get("result")
        if result not in ("question", "denied", "error"):
            return
        decision = st.step_decisions.get(step["step"])
        if decision:
            self._resume_step(card, st, step, decision)
        elif result == "error" and int(step.get("attempt") or 0) == 0:
            if step["step"] not in st.step_retries:
                self.journal.append(card, "step.retry", step=step["step"], ticket="", reason=done.get("reason") or "")
            self._resume_step(card, fold(self.journal.read(card)), step, {"choice": "auto"}, attempt=1)

    def _plan_step(self, card, st, selected, kind, text, findings=""):
        item, project = self._project(card)
        info = ticket_of(st)
        branch = info["cardBranch"] or f"card/{card.lower()}-plan"
        from .gitops import worktree_for
        worktree = info["cardWorktree"] or worktree_for(project.repo, branch)
        made = self._act(card, "card-branch", branch,
                         lambda: self.git.branch_worktree(project.repo, branch, worktree, self.git.main_branch(project.repo)),
                         repo=project.repo, branch=branch, worktree=worktree, base=self.git.main_branch(project.repo))
        generation = (st.plan_revision or {}).get("seq") if kind == "plan-amend" else st.plan_proposed["seq"]
        step_id = f"{card}-{kind}-{generation}"
        out = worktree + f".{kind}-{step_id}.step/out"
        text += f"\nKatalog wyjściowy: {out}\nPlik wyniku: {out}/result.json\n"
        text += 'Po zapisaniu plików zapisz {"result":"done","notes":[]}.\n'
        self._launch(item, project, selected, "", made, made, step_id, [], kind=kind, prompt_text=text, review_tip=findings)

    def _review_prompt(self, card, proposal):
        item, _ = self._project(card)
        return ("Review propozycji planu. Niczego nie implementuj ani nie zmieniaj w repozytorium.\n"
                "Sprawdź luki względem specyfikacji, mierzalność kryteriów, rozmiar i pionowość ticketów: "
                "każdy daje działające zachowanie i mieści się w jednym świeżym kontekście (to-tickets). "
                "Sprawdź blokery i przydziały. Każda kartka musi mieć merytoryczną część Dla recenzenta, "
                "opisującą ryzyka i zakres kontroli. Sam nagłówek nie wystarcza.\n"
                f"Opis karty: {item.description}\nSpecyfikacja:\n{proposal.get('specification', 'Sprawdź opis karty i kartki przekazania.')}\n"
                f"Propozycja i kartki:\n{json.dumps(proposal['rows'], ensure_ascii=False)}\n"
                'Zapisz w katalogu wyjściowym plan-review.json: {"verdict":"APPROVE" albo "REQUEST_CHANGES","findings":"konkretne uwagi lub uzasadnienie"}.')

    def _revision_prompt(self, card, proposal, findings):
        return (f"Popraw propozycję planu karty {card}. To jedyna runda poprawy po review.\n"
                f"Specyfikacja: {proposal.get('specification', 'Sprawdź opis karty i kartki przekazania.')}\nUwagi: {findings}\n"
                f"Poprzednia propozycja: {json.dumps(proposal, ensure_ascii=False)}\n"
                "Zapisz pełny plan.md standardu 1.2 i tickets/<numer bez #>.md w katalogu wyjściowym. "
                "Zachowaj card, prefiks i numery robocze. Nie implementuj ticketów ani nie publikuj.")

    def edit_plan(self, card, request):
        from .engine import FlowError
        st = fold(self.journal.read(card))
        proposal = st.plan_proposed
        if not proposal or not st.plan_reviewed or st.card_finished:
            raise FlowError("no-plan", "brak planu do zmiany")
        if request.get("action") == "enter":
            step = next((s for s in reversed(list(st.steps.values())) if s.get("executor") == proposal["planner"]), None)
            if not step:
                raise FlowError("no-planner-session", "brak sesji planującego")
            return self.enter(card, step["step"])
        if request.get("action") == "discard-revision":
            if not (st.plan_revision or {}).get("failed"):
                raise FlowError("no-failed-revision", "nie ma błędnej poprawki do odrzucenia")
            self.journal.append(card, "plan.revision-discarded")
            return {"ok": True, "id": card}
        if request.get("action") == "return":
            feedback = request.get("feedback")
            if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 32000:
                raise FlowError("bad-feedback", "wpisz uwagi do planu (do 32000 znaków)")
            if st.plan_revision and not st.plan_revision.get("failed"):
                raise FlowError("revision-active", "poprawa planu już trwa")
            baseline = (st.plan_approved or proposal)["rows"]
            # Przy zatwierdzonym planie poprawa posługuje się już nadanymi ID.
            baseline = [{**r, "workId": r.get("id") or r["workId"]} for r in baseline]
            self.journal.append(card, "plan.returned", feedback=feedback, baseline=baseline)
            self._advance_user_revision(card, fold(self.journal.read(card)))
            return {"ok": True, "id": card}
        if request.get("action") != "edit":
            raise FlowError("bad-request", "nieznana zmiana planu")
        if st.plan_revision:
            raise FlowError("revision-active", "zaczekaj na zatwierdzenie różnicy przed kolejną edycją")
        plan = st.plan_approved or proposal
        ident = str(request.get("ticket") or "")
        row = next((r for r in plan["rows"] if (r.get("id") or r["workId"]) == ident), None)
        if row is None:
            raise FlowError("no-ticket", "ticket nie należy do planu")
        if ident in st.assignments or any(s.get("ticket") == ident for s in st.steps.values()) or any(
                a in ("ticket-branch", "step-start") and i.get("ticket") == ident for (a, _), i in st.intents.items()):
            raise FlowError("ticket-started", "ticket już ruszył — nie można zmienić kartki ani przydziału")
        changes = {key: request[key] for key in ("executor", "reviewer", "class", "body") if key in request}
        candidate = {**row, **changes}
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg else self.catalog()
        author, reviewer = catalog.get(candidate["executor"]), catalog.get(candidate["reviewer"])
        if not author or not reviewer or author.model.lower() == reviewer.model.lower() or (author.provider == "local") != (reviewer.provider == "local"):
            raise FlowError("bad-assignment", "wybierz znanych wykonawców z różnymi modelami w tym samym zakresie lokalnym")
        if candidate["class"] not in ("lekki", "standard", "mocny", "lokalny") or (candidate["class"] == "lokalny" and author.provider != "local"):
            raise FlowError("bad-class", "nieprawidłowa klasa")
        if not isinstance(candidate.get("body"), str) or len(candidate["body"].encode()) > plans.plan_mod.MAX_BYTES or not plans.CRITERION.search(candidate["body"]):
            raise FlowError("bad-plan-card", "kartka wymaga kryteriów i nie może przekraczać 512 KB")
        if changes and any(row.get(k) != v for k, v in changes.items()):
            self.journal.append(card, "plan.ticket-edited", ticket=ident, changes=changes)
            self._sync_plan_cards(card, fold(self.journal.read(card)))
        return {"ok": True, "id": card}

    def _sync_plan_cards(self, card, st):
        plan = st.plan_approved or st.plan_proposed or {}
        for row in plan.get("rows", []):
            if "body" not in row:
                continue
            ident = (row.get("id") or row["workId"]).lstrip("#")
            rel = os.path.join("plan", "tickets", ident + ".md")
            path = os.path.join(self.store.root, self.journal.rel_dir(card), rel)
            data = row["body"].encode("utf-8")
            try:
                with open(path, "rb") as fh:
                    if fh.read(plans.plan_mod.MAX_BYTES + 1) == data:
                        continue
            except OSError:
                pass
            self.journal.write_file(card, rel, data)

    @staticmethod
    def _difference(old, new):
        before = {(r.get("id") or r["workId"]): r for r in old}
        after = {(r.get("id") or r["workId"]): r for r in new}
        keys = ("title", "executor", "reviewer", "class", "blockers", "body")
        return {"added": [k for k in after if k not in before], "removed": [k for k in before if k not in after],
                "changed": [{"ticket": k, "fields": [f for f in keys if before[k].get(f) != after[k].get(f)]}
                            for k in after if k in before and any(before[k].get(f) != after[k].get(f) for f in keys)]}

    def _advance_user_revision(self, card, st):
        revision = st.plan_revision
        if not revision or revision.get("pending") or revision.get("failed"):
            return
        steps = [s for s in st.steps.values() if s.get("kind") == "plan-amend" and s["seq"] > revision["seq"]]
        if not steps:
            cfg = self.models_config() if self.models_config else None
            catalog = cfg.catalog if cfg else self.catalog()
            selected = execution.resolve(st.plan_proposed["planner"], catalog, config=cfg, probe=self.model_probe, which=self.which)
            self._plan_step(card, st, selected, "plan-amend", self._revision_prompt(card, {**st.plan_proposed, "rows": revision["baseline"]}, revision["feedback"]))
            return
        step = steps[-1]
        done = st.finished.get(step["step"]) or {}
        if done.get("result") != "done":
            self._plan_step_decision(card, st, step, done)
            return
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg else self.catalog()
        path = os.path.join(step["stepDir"], "out", "plan.md")
        existing = [r["id"] for r in (st.plan_approved or {}).get("rows", [])]
        proposal = plans.read(path, card, catalog, allowed_roots=[os.path.dirname(path)], existing_ids=existing)
        rows = [{**r, "body": proposal.cards.get(r["workId"], "")} for r in proposal.rows]
        delta = self._difference(revision["baseline"], rows)
        started = {s.get("ticket") for s in st.steps.values() if s.get("ticket")}
        invalid = list(proposal.errors)
        if set(delta["removed"]) & started or any(c["ticket"] in started for c in delta["changed"]):
            invalid.append("zmieniono ticket, który już ruszył")
        if invalid:
            if (st.plan_checked or {}).get("step") != step["step"]:
                self.journal.append(card, "plan.checked", step=step["step"], valid=False, errors=invalid)
                self.journal.append(card, "plan.revision-failed", failed=True, pending=False, errors=invalid,
                                    baseline=revision["baseline"], feedback=revision["feedback"])
            return
        reply = ({"ok": True} if (st.plan_proposed or {}).get("sourceStep") == step["step"] else
                 self.propose_plan(card, path, continuation=True, step=step["step"]))
        if reply["ok"]:
            self.journal.append(card, "plan.revision-ready", pending=True, difference=delta, baseline=revision["baseline"],
                                feedback=revision["feedback"])
            # Wynik niezależnego review nadal towarzyszy poprawianemu planowi; nowa propozycja jest oceniana ponownie.

    def _approve_revision(self, card, st):
        from .engine import FlowError
        if not st.plan_reviewed:
            raise FlowError("review-pending", "różnica czeka na review planu")
        if not st.plan_approved:
            self.journal.append(card, "plan.revision-approved", plan={})
            return self.approve_plan(card)
        current = st.plan_approved
        proposal = st.plan_proposed
        delta = self._difference(current["rows"], proposal["rows"])
        started = {s.get("ticket") for s in st.steps.values() if s.get("ticket")}
        if set(delta["removed"]) & started or any(c["ticket"] in started for c in delta["changed"]):
            raise FlowError("ticket-started", "różnica zmienia ticket, który już ruszył")
        mapping = {}
        for row in proposal["rows"]:
            ident = row["workId"]
            if ident.startswith("#"):
                key = f"plan:{proposal['seq']}:{ident}"
                reserved = self._act(card, "reserve", key,
                                     lambda r=row, k=key: self._reserve(self._repo(card), r["title"], f"{card}:{k}", proposal["prefix"]),
                                     retry_failed=True, repo=self._repo(card), title=row["title"], prefix=proposal["prefix"])
                mapping[ident] = reserved["ticket"]
            else:
                mapping[ident] = ident
        rows = [{**r, "id": mapping[r["workId"]], "blockers": [mapping[b] for b in r["blockers"]],
                 "cardPath": os.path.join(self.store.root, self.journal.rel_dir(card), "plan", "tickets", mapping[r["workId"]] + ".md")}
                for r in proposal["rows"]]
        waves = [[mapping[i] for i in wave] for wave in proposal["waves"]]
        self.journal.append(card, "plan.revision-approved", difference=delta,
                            plan={"rows": rows, "waves": waves, "map": {**current.get("map", {}), **mapping}})
        return {"ok": True, "id": card, "difference": delta}
