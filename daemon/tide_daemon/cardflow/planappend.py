"""Ręczne dopisanie jednego ticketu: propozycja bez uruchamiania planującego."""
import hashlib
import json
import os
import tempfile

from . import baseproposal, execution, plans


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def fingerprint(st):
    """Postęp kroków nie zmienia treści planu; edycja i nowe zatwierdzenie zmieniają odcisk."""
    return digest({"order": (st.started or {}).get("seq"), "baseVersion": st.base_version,
                   "plan": st.plan_approved}) if st.plan_approved else ""


def available(st):
    return bool(st.plan_approved and not st.card_finished and not st.closed and
                st.moved_to not in ("done", "archived") and not st.plan_revision and
                all(r.get("project") in (None, "", (st.started or {}).get("project"))
                    for r in st.plan_approved["rows"]) and
                not baseproposal.blocked(st) and
                not any(s.get("kind") in ("close", "plan-review", "plan-amend", "plan-revise")
                        and s["step"] not in st.finished for s in st.steps.values()))


def append(engine, card, st, request):
    from .engine import FlowError
    key = request.get("requestKey")
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise FlowError("bad-request", "podaj klucz żądania (do 200 znaków)")
    request_hash = digest(request)
    for event in engine.journal.read(card):
        saved = event.get("appendRequest") or {}
        if event.get("seq", 0) > (st.started or {}).get("seq", 0) and saved.get("key") == key:
            if saved.get("hash") != request_hash:
                raise FlowError("request-conflict", "klucz żądania został użyty dla innej treści")
            return {"ok": True, "id": card, "workId": saved["workId"], "already": True}
    if not st.plan_approved or not st.plan_proposed or st.card_finished or st.closed or st.moved_to in ("done", "archived"):
        raise FlowError("no-plan", "dopisanie wymaga zatwierdzonego planu niezakończonej karty")
    if st.plan_revision:
        raise FlowError("revision-active", "zaczekaj na zatwierdzenie albo odrzucenie różnicy")
    if baseproposal.blocked(st):
        raise FlowError("base-decision-pending", "najpierw rozstrzygnij korektę bazy")
    item, project = engine._project(card)
    if (item.column in ("done", "archived") or
            item.project != (st.started or {}).get("project") or
            os.path.realpath(project.repo) != os.path.realpath((st.started or {}).get("repo") or "")):
        raise FlowError("foreign-project", "projekt albo stan karty zmienił się od rozpoczęcia zlecenia")
    if request.get("project", item.project) != item.project or any(
            r.get("project") not in (None, "", item.project) for r in st.plan_approved["rows"]):
        raise FlowError("foreign-project", "dopisanie obejmuje jeden ticket w projekcie karty")
    if not available(st):
        raise FlowError("revision-active", "trwa poprawa, review albo domknięcie planu")
    if request.get("planFingerprint") != fingerprint(st):
        raise FlowError("stale-plan", "plan się zmienił — odczytaj go ponownie przed dopisaniem")
    if set(request) - {"action", "requestKey", "planFingerprint", "project", "title", "body", "class",
                       "executor", "reviewer", "blockers"}:
        raise FlowError("bad-request", "dopisanie przyjmuje tylko pola jednego nowego ticketu")
    for field in ("title", "body", "class", "executor", "reviewer"):
        value = request.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > plans.plan_mod.MAX_BYTES:
            raise FlowError("bad-request", f"wypełnij pole {field} (do 512 KB)")
    title = request["title"].strip()
    if len(title) > 500 or any(c in title for c in "\n\r|"):
        raise FlowError("bad-request", "tytuł ma mieć jeden wiersz, do 500 znaków, bez znaku |")
    blockers = request.get("blockers")
    if not isinstance(blockers, list) or any(not isinstance(b, str) for b in blockers):
        raise FlowError("bad-request", "blokery muszą być listą numerów ticketów")
    # Stare wiersze są tylko wejściem wspólnego sprawdzania formatu. Po review i zatwierdzeniu
    # wracają oryginalne obiekty, także z pierwotnymi workId i metadanymi.
    baseline = st.plan_approved["rows"]
    used = set(st.plan_approved.get("map", {})) | {r["workId"] for r in baseline}
    number = max((int(w[1:]) for w in used if plans.WORK_ID.fullmatch(w)), default=0) + 1
    work_id = f"#{number}"
    rows = [{**r, "workId": r["id"]} for r in baseline]
    rows.append({"workId": work_id, "title": title, "body": request["body"], "class": request["class"],
                 "executor": request["executor"], "reviewer": request["reviewer"], "blockers": blockers})
    cfg = engine.models_config() if engine.models_config else None
    catalog = cfg.catalog if cfg else engine.catalog()
    proposal = {k: v for k, v in st.plan_proposed.items() if k != "appendRevision"}
    # plans.read jest jedynym walidatorem przydziałów, kartek i grafu. Plik tymczasowy nie
    # stanowi publikacji propozycji ani rezerwacji ID; niepowodzenie nie zmienia dziennika.
    with tempfile.TemporaryDirectory(prefix="plan-append-", dir=engine.tmp_root) as folder:
        os.makedirs(os.path.join(folder, "tickets"))
        path = os.path.join(folder, "plan.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"---\ntide-plan: 1\ncard: {card}\ntracker: local\nticket-prefix: {proposal['prefix']}\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
            for row in rows:
                cells = [row["workId"], row["title"], "todo", "", row["executor"], "", row["class"],
                         row["reviewer"], ", ".join(row["blockers"])]
                if row is rows[-1] and any(any(c in cell for c in "\r\n|") for cell in cells):
                    raise FlowError("bad-request", "pola tabeli muszą mieć jeden wiersz bez znaku |")
                cells = [cell.replace("|", "\\|").replace("\r", " ").replace("\n", " ") for cell in cells]
                fh.write("| " + " | ".join(cells) + " |\n")
                with open(os.path.join(folder, "tickets", row["workId"].lstrip("#") + ".md"),
                          "w", encoding="utf-8") as ticket:
                    ticket.write(row.get("body") or "")
        checked = plans.read(path, card, catalog, allowed_roots=[folder],
                             existing_ids=[r["id"] for r in baseline])
    if checked.errors:
        return {"ok": False, "id": card, "error": "invalid-plan", "errors": checked.errors,
                "message": "; ".join(checked.errors)}
    new = checked.rows[-1]
    new["body"] = request["body"]
    # Sprawdzamy dostępność recenzenta przed trwałym zapisem, bez rozwiązywania planującego.
    try:
        execution.resolve(new["reviewer"], catalog, config=cfg, probe=engine.model_probe, which=engine.which)
    except execution.ExecutionError as error:
        raise FlowError(error.code, str(error)) from None
    delta = {"added": [work_id], "removed": [], "changed": []}
    engine.journal.append(card, "plan.proposed", title=proposal.get("title", ""), prefix=proposal["prefix"],
                          coordinator=proposal.get("coordinator", "codex"), session="", rows=rows[:-1] + [new],
                          planner=proposal.get("planner") or new["executor"], reviewAuthor=new["executor"],
                          sourceKind="append", sourceStep="", planPath="",
                          specification=proposal.get("specification") or item.description,
                          appendRequest={"key": key, "hash": request_hash, "workId": work_id},
                          appendRevision={"pending": True, "kind": "append", "baseline": rows[:-1],
                                          "feedback": "Ticket dopisany przez użytkownika", "difference": delta,
                                          "previousProposal": proposal, "previousReview": st.plan_reviewed,
                                          "reviewer": new["reviewer"]})
    return {"ok": True, "id": card, "workId": work_id}
