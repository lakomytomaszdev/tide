"""Jedna wersjonowana zgoda plasma-tide przez istniejące ApproveCardEnvironment.

YAML jest propozycją profilu i listą id kopii, nigdy manifestem ani uprawnieniem.
Usługa wyprowadza repozytoria, montowania, opisy kopii i odcisk ponownie.
"""
from ..box import exposure as ex, profile as bp, yamlmini
from . import boxwait

REQUEST = "project-exposure"
ACTION = "exposure-action"
EXPECTED = "exposure-expected"
ACCEPTED = "exposure-accepted"
CONTROL = (REQUEST, ACTION, EXPECTED, ACCEPTED)


def service(engine, card_id):
    _card, project = engine._project(card_id)
    board = engine.store.load()
    registry = {slug: [p.repo] for slug, p in board.projects.items() if p.repo}
    return ex.ExposureService(project.slug, [project.repo], registry)


def _profiles(engine, card_id, st, raw):
    from . import envconsent
    slug, repo = envconsent._project_state(engine, card_id)
    # Czytamy surowy profil przed walidacją, żeby nowsze pola nie zniknęły
    # przy aktualizacji przez stary schemat. Nie nadpisujemy nieczytelnego profilu.
    def read(path, scope):
        with open(path, encoding="utf-8") as fh:
            text = fh.read(bp._MAX_FILE + 1)
        if len(text) > bp._MAX_FILE:
            raise ex.ExposureError("Profil przekracza limit; odmowa aktywacji bez nadpisania danych")
        return ex.profile_contract(yamlmini.loads(text), scope)

    try:
        project = read(bp.project_path(slug), "project")
    except FileNotFoundError:
        _source, proposal, _items, _note = envconsent.environment._proposal(repo, slug,
                                                                          envconsent.stack.detect(repo))
        project = ex.profile_contract(proposal, "project")
    try:
        read(bp.card_path(slug, card_id), "card")
    except FileNotFoundError:
        pass
    card = ex.profile_contract(raw, "card")
    # Nie sumujemy zgód zapisanych kart. Ta jedna propozycja jest cała pokazywana
    # i obejmowana nową zgodą, w tym jej pozycje czerwone.
    red = bp.red_items(card, envconsent.templates.known_domains())
    merged = bp.merge(project, {**card, "accepted_red": red}, envconsent.templates.known_domains()).profile
    return project, card, merged


def _request(raw):
    request = raw.get(REQUEST)
    if (not isinstance(request, dict) or set(request) - {"version", "snapshotIds"} or
            type(request.get("version")) is not int or request["version"] != ex.VERSION):
        raise ex.ExposureError("Wymagana nowa zgoda projektu: project-exposure.version: 1 i snapshotIds; "
                               "dawna zgoda per karta nie nadaje tych praw.")
    return request


def view(candidate):
    manifest = candidate["manifest"]
    lines = [f"Projekt: {manifest['project']}; wersja zakresu zgody: {manifest['consentVersion']}",
             "Repozytoria: " + ", ".join(manifest["repositories"])]
    lines += [f"{m['source']} → {m['target']} ({m['mode']})" for m in manifest["mounts"]]
    for entry in manifest["inputs"]:
        copy = entry["snapshot"]
        lines += [f"PackSnapshot {copy['id']} v{copy['version']}: {len(copy['files'])} plików, {copy['size']} B; "
                  + copy["scanner"]["scanner"]]
        lines += [f"{f['path']} ({f['size']} B; sha256 {f['sha256']})" for f in copy["files"]]
    lines += [f"{p}: {mode['note']}" for p, mode in manifest["credentials"].items()]
    return {"expected": candidate["expected"], "scope": ex.SCOPE, "details": "\n".join(lines),
            "manifest": manifest}


def model(engine, card_id, st):
    from . import envconsent
    slug, _repo = envconsent._project_state(engine, card_id)
    card = envconsent._card_proposal(engine, card_id, st, slug)
    # To tylko propozycja: historyczny accepted_red nie zaznacza nowej zgody.
    card = {k: v for k, v in card.items() if k != "accepted_red"}
    card.setdefault("tide-box", bp.SCHEMA_VERSION)
    raw = {**card, REQUEST: {"version": ex.VERSION, "snapshotIds": []}}
    current = ex.current(slug)
    if current:
        # Manifest jest źródłem v1; historyczne YAML-e nie zostały przy jego
        # zatwierdzeniu przepisane. Kolejna karta widzi ten sam zatwierdzony zakres.
        card = current["manifest"]["profile"]
        raw = {**card, REQUEST: {"version": ex.VERSION, "snapshotIds": []}}
        raw[REQUEST]["snapshotIds"] = [i["snapshot"]["id"] for i in current["manifest"]["inputs"]]
    out = {"slug": slug, "items": [], "notes": [], "yaml": bp.dump(raw), "acceptKey": "accepted_red",
           "diff": {"added": [], "removed": []}, "state": "consent", "stateLabel": "zgoda projektu",
           "boxWait": boxwait.card_box_wait(st), "boxWaitLabel": boxwait.label(boxwait.card_box_wait(st)),
           "projectExposure": True, "exposure": {"scope": ex.SCOPE, "details": "", "expected": ""},
           "exposureControls": {"action": ACTION, "expected": EXPECTED, "accepted": ACCEPTED},
           "approvedVersion": current["version"] if current else "", "approved": bool(current), "question": ""}
    try:
        _project, _card, merged = _profiles(engine, card_id, st, card)
        out["exposure"] = view(service(engine, card_id).preview(merged, raw[REQUEST]["snapshotIds"]))
    except (ex.ExposureError, OSError, bp.ProfileError, yamlmini.YamlError, ValueError) as exc:
        out["notes"] = [str(exc)]
    return out


def approve(engine, card_id, text, st):
    try:
        raw = yamlmini.loads(text)
        if not isinstance(raw, dict):
            raise ex.ExposureError("Środowisko musi być mapą YAML")
        request = _request(raw)
        action = raw.get(ACTION, "approve")
        svc = service(engine, card_id)
        if action == "revoke":
            message = ex.revoke(svc.project, raw.get(EXPECTED))
            boxwait.set_box_wait(engine.journal, card_id, boxwait.ENVIRONMENT, source="project-consent")
            return {"ok": True, "id": card_id, "message": message, "revoked": True}
        _project, _card, merged = _profiles(engine, card_id, st, {k: v for k, v in raw.items() if k not in CONTROL})
        if action == "preview":
            return {"ok": True, "id": card_id, "preview": True,
                    "exposure": view(svc.preview(merged, request.get("snapshotIds")))}
        if action != "approve":
            raise ex.ExposureError("Nieznana czynność zgody projektu")
        candidate = svc.preview(merged, request.get("snapshotIds"))
        if raw.get(ACCEPTED) is not True or raw.get(EXPECTED) != candidate["expected"]:
            raise ex.ExposureError("Wymagana świadoma zgoda projektu i dokładny aktualny podgląd zakresu")
        # Przed zapisem zgody sprawdzamy review planu. Nie zapisujemy profili ani
        # nie uruchamiamy budowy na podstawie YAML z pudełka.
        if st.plan_proposed and not st.plan_approved:
            engine.approve_plan(card_id)
        record = svc.approve(merged, request.get("snapshotIds"), expected=raw.get(EXPECTED),
                             accepted=raw.get(ACCEPTED))
        boxwait.set_box_wait(engine.journal, card_id, boxwait.ENVIRONMENT, source="project-consent")
        return {"ok": True, "id": card_id, "onlyPlan": False, "boxWait": boxwait.ENVIRONMENT,
                "consentVersion": record["version"], "message": ex.PENDING,
                "exposure": view(svc.preview(merged, request["snapshotIds"]))}
    except (ex.ExposureError, OSError, bp.ProfileError, yamlmini.YamlError, ValueError) as exc:
        return {"ok": False, "id": card_id, "error": "project-exposure-wait", "message": str(exc),
                "boxWait": boxwait.ENVIRONMENT}
