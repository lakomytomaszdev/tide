"""Pakiet zgód: sekcja „Środowisko karty” w planie do zatwierdzenia (PT-172, karta K-38).

Cała walidacja YAML-a i klasyfikacja ryzyka są tutaj i w `box/profile.py`; panel karty (QML) tylko pokazuje wynik
(`derived.flow.plan.environment`) i odsyła tekst YAML. Moduł:
- `model(...)` — sekcja do panelu: stan pudełka (`clean` z datą / do zbudowania / do przebudowy z różnicą), pozycje
  z klasą ryzyka, ostrzeżenia i jedno pytanie planu dla projektu bez profilu i bez wzorca,
- `prepare(...)` + `approve(...)` — `ApproveCardEnvironment(card, yaml, onlyPlan)`: walidacja schematem i zgodą na
  pozycje czerwone przed jakimkolwiek zapisem, zapis profilu projektu i karty do Tablicy, zdarzenie `box.approved
  source=ui`, `boxWait`, start budowy/przebudowy w tle,
- `status(...)` — `BoxStatus(slug)`,
- `network_domain`, `add_card_domain` — wynik kroku `denied` z `command: "network: <domena>"` (pytanie o domenę).

Niczego nie zapisuje do `.tide/box.yaml` repozytorium. Tablicę commituje Tide (zapis profilu idzie wyłącznie tędy).
Opis: docs/tide-daemon.md (Pakiet zgód: środowisko karty).
"""
import os
import threading
import time

from ..box import builder as box_builder
from ..box import k3
from ..box import profile as bp
from ..box import settings as box_settings
from ..box import state as bs
from ..box import templates
from ..box import yamlmini
from ..box import packs
from ..tasks import stack
from . import boxwait
from . import environment

QUESTION = "Czego potrzebuje środowisko?"
QUESTION_HINT = ("Projekt nie ma profilu środowiska ani rozpoznanego wzorca. Odpowiedz w planie: pakiety apt, "
                 "polecenia budowy (setup) i weryfikacji (verify), domeny sieci.")
CONSENT_KEY = "accepted_red"        # klucz zgody w profilu karty; UI dopisuje go z modelu, nie zna listy kluczy
PER_CARD = {"on": "box: per-card", "off": "box: per-project"}

TOKENS = {bp.GREEN: "positive", bp.YELLOW: "waiting", bp.RED: "negative"}
RISK_LABELS = {bp.GREEN: "niskie ryzyko", bp.YELLOW: "średnie ryzyko", bp.RED: "wysokie ryzyko — wymaga zgody"}
GROUP_LABELS = {"network": "Sieć", "docker": "Docker i obrazy", "ports": "Porty", "packs": "Paczki danych",
                "resources": "Zasoby", "per-card": "Pudełko osobne dla karty"}


class EnvError(Exception):
    """Odrzucone środowisko: `errors` to komunikaty dla użytkownika, `items` — pozycje do ponownego pokazania."""

    def __init__(self, errors, items=None):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        self.items = items or []
        super().__init__("; ".join(self.errors))


# ------------------------------------------------------------ pytanie o domenę (wynik kroku denied)

def network_domain(command) -> str:
    """Domena z `command: "network: <domena>"` (wynik kroku `denied` po odmowie proxy); pusty napis, gdy to nie ono."""
    text = str(command or "").strip()
    if not text[:8].lower() == "network:":
        return ""
    host = bp.normalize_host(text[8:])
    return host if host and bp._HOST.match(host) else ""


def domain_risk(domain: str) -> str:
    return bp.classify(domain, templates.known_domains())


def add_card_domain(slug: str, card: str, domain: str) -> str:
    """Dopisuje domenę do profilu karty (po walidacji, tylko nie-czerwoną); zwraca ścieżkę zapisanego profilu."""
    host = bp.normalize_host(domain)
    if domain_risk(host) == bp.RED:
        raise bp.ProfileError([f"domena {host!r} jest czerwona — wymaga jawnej zgody w planie, nie w trakcie kroków"])
    current = bp.load_card(slug, card) or {"tide-box": bp.SCHEMA_VERSION}
    net = dict(current.get("network") or {})
    if host not in (net.get("domains") or []):
        net["domains"] = [*(net.get("domains") or []), host]
    current["network"] = net
    return bp.save_card(slug, card, current)


# ------------------------------------------------------------ pozycje i model sekcji

def _item(ident, group, risk, *, scope, enabled, locked=False, note="", label=None, kind="") -> dict:
    needs = risk == bp.RED and not locked
    return {"id": ident, "label": label or ident, "group": group, "groupLabel": GROUP_LABELS.get(group, group),
            "kind": kind or group, "risk": risk, "riskLabel": RISK_LABELS[risk], "token": TOKENS[risk],
            "scope": scope, "enabled": bool(enabled), "needsConsent": needs, "locked": locked, "note": note}


def docker_note() -> str:
    """Opis ryzyka K1 z propozycji (szablon docker) i dopisek o K3 według `box/k3.py`."""
    try:
        notes = [n.get("note", "") for n in templates.load("docker").get("image_notes", []) if "K1" in n.get("note", "")]
    except (templates.TemplateError, OSError, KeyError):
        notes = []
    off = [p for p in sorted(k3.K3_PROGRAMS) if not k3.k3_active(p)]
    k3_text = f"K3 nieaktywne ({', '.join(off)})" if off else "K3 aktywne"
    return " ".join([templates.DOCKER_RISK_NOTE, *notes[:1], f"{k3_text}.", k3.K1_NOTE if off else ""]).strip()


def _profile_items(profile: dict, known, scope: str, accepted=(), locked_red=False) -> list:
    out = []
    for ident, group, risk in bp.display_items(profile, known):
        note = docker_note() if ident == "docker" else ""
        consented = ident in accepted or locked_red
        out.append(_item(ident, group, risk, scope=scope, enabled=(risk != bp.RED or consented),
                         locked=locked_red and risk == bp.RED, note=note,
                         label={"docker": "Docker w pudełku", "ports": "Pula portów", "resources": "Zasoby pudełka",
                                "box:per-card": "Pudełko osobne dla tej karty"}.get(ident, ident)))
        if group == "packs":
            spec = next(p for p in profile["packs"]
                        if ident == "pack:" + (p["name"] if isinstance(p, dict) else p))
            report = packs.describe(spec)
            out[-1]["pack"] = report
            out[-1]["blocked"] = report["blocked"]
            outcome = "zablokowana" if report["blocked"] else "brak trafień"
            out[-1]["note"] = (f"{report['count']} plików, {report['size']} B; skaner: {outcome}. " + packs.FILTER_NOTE + "\n" +
                               "\n".join(report["paths"]) + "\n" +
                               "\n".join(h["path"] + ": " + h["rule"] for h in report["hits"]) +
                               report.get("error", ""))
            if report["blocked"]:
                out[-1]["enabled"] = False
    return out


def _template_items(items: list, accepted=()) -> list:
    """Pozycje propozycji: sieć/obrazy wymagają zgody; lokalne źródła są informacją bez udostępnienia."""
    out = []
    for entry in items:
        risk = entry.get("risk") if entry.get("risk") in (bp.GREEN, bp.YELLOW, bp.RED) else bp.YELLOW
        if entry.get("kind") == "local-feed":
            # Informacja o nieudostępnionym źródle plikowym, nigdy przełącznik domeny.
            out.append(_item(f"local-feed:{entry['value']}", "packs", bp.RED, scope="projekt",
                             enabled=False, locked=True, note=entry.get("description") or "",
                             label=entry["value"], kind="local-feed"))
            continue
        group = "docker" if entry.get("kind") == "image" else "network"
        ident = f"image:{entry['value']}" if group == "docker" else entry["value"]
        out.append(_item(ident, group, risk, scope="projekt", enabled=risk != bp.RED or ident in accepted,
                         note=entry.get("description") or "", label=entry["value"], kind="template"))
    return out


def _project_state(engine, card_id: str):
    item, project = engine._project(card_id)
    return project.slug, project.repo or ""


def _stamp(profile: dict) -> str:
    """Hash przepisu liczony tak samo jak `Builder.stamp` (wersja agentów z `box_builder.agents_version`)."""
    try:
        settings = box_settings.load()
    except Exception:
        settings = None
    return bp.recipe_stamp(profile, box_builder.templates_version(), box_builder.agents_version(settings))["hash"]


def _journal_box(slug: str):
    """(zdarzenie `box.ready` pudełka projektu, lista `box.approved`) z dziennika pudełka."""
    name = bs.box_name(slug)
    ready = None
    for event in bs.events(slug, name):
        if event["type"] == "box.ready" and not event.get("copy_of"):
            ready = event
        elif event["type"] == "box.destroy":
            ready = None
    approved = [e for e in bs.events(slug) if e["type"] == "box.approved"]
    return ready, approved


def box_state(slug: str, profile: dict | None, ids: list) -> dict:
    """Stan pudełka projektu bez pytania Incusa: `build` (do zbudowania), `clean` (z datą) albo `rebuild` z różnicą pozycji
    względem profilu zatwierdzonego przy hashu migawki `clean`."""
    ready, approved = _journal_box(slug)
    if ready is None:
        return {"state": "build", "label": "do zbudowania", "date": "", "hash": "", "diff": {"added": [], "removed": []}}
    clean_hash = ready.get("hash") or ""
    date = str(ready.get("ts") or "")[:10]
    current = _stamp(profile) if profile is not None else ""
    if not current or current == clean_hash:
        return {"state": "clean", "label": f"clean {date}".strip(), "date": date, "hash": clean_hash,
                "diff": {"added": [], "removed": []}}
    base = next((e for e in reversed(approved) if e.get("recipe") == clean_hash), None)
    old = list(base.get("items") or []) if base else []
    return {"state": "rebuild", "label": "do przebudowy", "date": date, "hash": clean_hash,
            "diff": {"added": [i for i in ids if i not in old], "removed": [i for i in old if i not in ids]},
            "baseline": bool(base)}


def _approved_project(slug: str):
    try:
        return bp.load_project(slug), ""
    except bp.ProfileError as exc:
        return None, f"zatwierdzony profil jest nieczytelny: {exc}"


def _project_profile(slug: str, repo: str):
    """(profil projektu, nowy?, pozycje szablonów, czy pusty projekt, uwagi). Nowy = jeszcze niezatwierdzony."""
    approved, problem = _approved_project(slug)
    notes = [problem] if problem else []
    if approved is not None:
        return approved, False, [], False, notes
    report = stack.detect(repo) if repo and os.path.isdir(repo) else stack.StackReport()
    source, proposal, tmpl_items, note = environment._proposal(repo or "", slug, report)
    if note:
        notes.append(note)
    try:
        profile = bp.validate(proposal, "project")
    except (bp.ProfileError, ValueError) as exc:
        notes.append(f"propozycja profilu projektu jest niepoprawna: {exc}")
        profile = {"tide-box": bp.SCHEMA_VERSION, "project": slug}
    empty = source == environment.SOURCE_TEMPLATES and not report.stacks
    return profile, True, tmpl_items, empty, notes


def _card_proposal(engine, card_id: str, st, slug: str) -> dict:
    """Rozszerzenie profilu karty: zapisane (po zatwierdzeniu) albo propozycja z `out/box.yaml` planującego."""
    try:
        saved = bp.load_card(slug, card_id)
    except bp.ProfileError:
        saved = None
    if saved is not None:
        return saved
    return dict((st.plan_proposed or {}).get("box") or {})


def yaml_base(card: dict) -> str:
    """Tekst YAML do pola „Zmień”: profil karty bez kluczy ustawianych przełącznikami (`box`, zgody)."""
    return bp.dump({k: v for k, v in card.items() if k not in ("box", CONSENT_KEY)})


def model(engine, card_id: str, st) -> dict:
    """Sekcja „Środowisko karty” dla panelu planu."""
    slug, repo = _project_state(engine, card_id)
    from ..box import exposure
    if slug == exposure.PROJECT:
        from . import projectconsent
        return projectconsent.model(engine, card_id, st)
    project, new, tmpl_items, empty, notes = _project_profile(slug, repo)
    card = _card_proposal(engine, card_id, st, slug)
    known = templates.known_domains()
    accepted = {bp.normalize_host(a) if not (a == "network.full" or a.startswith("pack:")) else a
                for a in card.get(CONSENT_KEY, [])}
    items = _profile_items(project, known, "projekt", accepted, locked_red=not new)
    items += _template_items(tmpl_items, accepted)
    items += [i for i in _profile_items(card, known, "karta", accepted) if i["id"] != "box:per-card"]
    per_card = (card.get("box") or project.get("box") or "per-project") == "per-card"
    items.append(_item("box:per-card", "per-card", bp.GREEN, scope="karta", enabled=per_card,
                       label="Pudełko osobne dla tej karty",
                       note="Domyślnie jak w profilu projektu; przełącznik zmienia tylko tę kartę."))
    items[-1]["toggle"] = dict(PER_CARD)
    ids = [i["id"] for i in items if i["enabled"]]
    state = box_state(slug, project if not new else None, ids)
    if state["state"] == "build" and new:
        state["label"] = "do zbudowania"
    out = {"slug": slug, "card": card_id, "project": project.get("project", slug), "state": state["state"],
           "stateLabel": state["label"], "cleanDate": state["date"], "diff": state["diff"], "newProject": new,
           "items": items, "yaml": yaml_base(card), "acceptKey": CONSENT_KEY, "notes": notes,
           "question": QUESTION if empty else "", "questionHint": QUESTION_HINT if empty else "",
           "boxWait": boxwait.card_box_wait(st), "boxWaitLabel": boxwait.label(boxwait.card_box_wait(st)),
           "approved": bool(st.plan_approved) and not boxwait.card_box_wait(st) == boxwait.ENVIRONMENT}
    return out


# ------------------------------------------------------------ zatwierdzenie

class Prepared:
    def __init__(self, slug, card_id, project, new_project, card, accepted, merged, items, red):
        self.slug, self.card_id, self.project, self.new_project = slug, card_id, project, new_project
        self.card, self.accepted, self.merged, self.items, self.red = card, accepted, merged, items, red


def prepare(engine, card_id: str, text: str, st) -> Prepared:
    """Sprawdza YAML i zgodę — bez zapisu. Rzuca `EnvError` z wszystkimi problemami i pozycjami do pokazania."""
    slug, repo = _project_state(engine, card_id)
    known = templates.known_domains()
    try:
        card = bp.parse(text, "card")
    except bp.ProfileError as exc:
        raise EnvError(exc.errors) from None
    except yamlmini.YamlError as exc:
        raise EnvError(f"YAML: {exc}") from None
    project, new, tmpl_items, _empty, notes = _project_profile(slug, repo)
    shown = _profile_items(project, known, "projekt", locked_red=not new) + _template_items(tmpl_items) \
        + _profile_items(card, known, "karta", set(card.get(CONSENT_KEY, [])))
    accepted = [a if a == "network.full" or a.startswith("pack:") else bp.normalize_host(a)
                for a in card.get(CONSENT_KEY, [])]
    template_red = {i["id"] for i in _template_items(tmpl_items) if i["risk"] == bp.RED and i["group"] == "network"}
    required = list(bp.red_items(card, known))
    if new:
        required += [r for r in bp.red_items(project, known) if r not in required]
    errors = [f"pozycja czerwona {r!r} wymaga jawnego zaznaczenia (accepted_red)" for r in required if r not in accepted]
    errors += [f"accepted_red: pozycji {a!r} nie ma w profilu albo nie jest czerwona" for a in accepted
               if a not in required and a not in template_red]
    errors += [f"{i['id']}: paczka zablokowana; {i['note']}" for i in shown if i.get("blocked")]
    if errors:
        raise EnvError(errors, shown)
    card = dict(card)
    net = dict(card.get("network") or {})
    for ident in accepted:
        if ident in template_red and ident not in (net.get("domains") or []):
            net["domains"] = [*(net.get("domains") or []), ident]   # czerwona pozycja szablonu: zgoda dopisuje ją do karty
    if net:
        card["network"] = net
    try:
        for_merge = {**card, CONSENT_KEY: [a for a in accepted if a in bp.red_items(card, known)]}
        merged = bp.merge(project, for_merge, known)
    except bp.ProfileError as exc:
        raise EnvError(exc.errors, shown) from None
    ids = [i for i, _g, _r in bp.display_items(merged.profile, known)]
    red = [a for a in accepted]
    return Prepared(slug, card_id, project, new, card, accepted, merged.profile, ids, red)


def approve(engine, card_id: str, text: str, only_plan: bool) -> dict:
    """`ApproveCardEnvironment(card, yaml, onlyPlan)`; wynik JSON-owy dla D-Bus."""
    from .engine import FlowError, fold
    from ..board.model import card_num
    try:
        card_id = f"K-{card_num(card_id)}"
    except Exception as exc:
        raise FlowError("bad-card", str(exc)) from None
    st = fold(engine.journal.read(card_id))
    if st.started is None or st.card_finished:
        raise FlowError("no-card", f"{card_id}: karta nie ma aktywnego przepływu")
    from ..box import exposure
    if _project_state(engine, card_id)[0] == exposure.PROJECT:
        from . import projectconsent
        if only_plan:
            plan = engine.approve_plan(card_id) if st.plan_proposed and not st.plan_approved else {}
            boxwait.set_box_wait(engine.journal, card_id, boxwait.ENVIRONMENT, source="ui")
            return {"ok": True, "id": card_id, "onlyPlan": True, "boxWait": boxwait.ENVIRONMENT, "plan": plan}
        return projectconsent.approve(engine, card_id, text, st)
    try:
        prepared = prepare(engine, card_id, text, st)
    except EnvError as exc:
        # Błąd walidacji: bez zapisu i bez budowy; pozycje wracają do panelu (nowe czerwone do zaznaczenia).
        return {"ok": False, "id": card_id, "error": "invalid-environment", "message": "; ".join(exc.errors),
                "errors": exc.errors, "items": exc.items}
    plan = {}
    if st.plan_proposed and not st.plan_approved:
        plan = engine.approve_plan(card_id)          # rzuca przy review-pending: nic jeszcze nie zapisano
    if only_plan:
        boxwait.set_box_wait(engine.journal, card_id, boxwait.ENVIRONMENT, source="ui")
        return {"ok": True, "id": card_id, "onlyPlan": True, "boxWait": boxwait.ENVIRONMENT, "plan": plan}
    return _commit(engine, prepared, plan)


def _commit(engine, prepared: Prepared, plan: dict) -> dict:
    slug, card_id = prepared.slug, prepared.card_id
    saved = []
    if prepared.card.get("packs"):
        try:
            packs.materialize(slug, card_id, prepared.card["packs"])
        except (packs.PackError, OSError) as exc:
            return {"ok": False, "id": card_id, "error": "invalid-packs", "message": str(exc)}
    if prepared.new_project:
        saved.append(bp.save_project(slug, prepared.project))
    card_clean = dict(prepared.card)
    if prepared.accepted:
        card_clean[CONSENT_KEY] = list(prepared.accepted)
    saved.append(bp.save_card(slug, card_id, card_clean))
    name = bs.box_name(slug)
    effective = bp.load_project(slug) or prepared.project
    recipe = _stamp(effective)
    bs.record(slug, "box.approved", name, engine.clock, source="ui", card=card_id,
              accepted_red=list(prepared.accepted), items=list(prepared.items), recipe=recipe, path=saved[0])
    boxwait.set_box_wait(engine.journal, card_id, "", source="ui")
    action = _launch(engine, slug, effective, recipe)
    return {"ok": True, "id": card_id, "onlyPlan": False, "boxWait": "", "saved": saved, "acceptedRed": prepared.accepted,
            "build": action, "plan": plan}


def _launch(engine, slug: str, profile: dict, recipe: str) -> str:
    """Zleca budowę (brak pudełka) albo przebudowę (inny hash) w tle; `""`, gdy pudełko jest aktualne."""
    ready, _ = _journal_box(slug)
    if ready is not None and (ready.get("hash") or "") == recipe:
        return ""
    action = "build" if ready is None else "rebuild"
    run = engine.box_launcher or _thread_launcher
    run(slug, profile, action)
    return action


def _thread_launcher(slug: str, profile: dict, action: str) -> None:
    def work():
        try:
            box_builder.Builder(box_settings.load()).build(slug, profile)
        except Exception as exc:   # błąd budowy zapisuje builder w dzienniku pudełka (box.build, result=error)
            print(f"tide-daemon: budowa pudełka {slug}: {exc!r}", flush=True)

    threading.Thread(target=work, name=f"box-{action}-{slug}", daemon=True).start()


# ------------------------------------------------------------ BoxStatus

def status(engine, slug: str, card: str = "") -> dict:
    """`BoxStatus(slug)`: stan, hash, „filtrowanie NIC: tak/nie” i `boxWait` kart projektu."""
    from .engine import fold
    from . import boxcontrol
    card, name = boxcontrol.target(engine, slug, card)
    board = engine.store.load()
    builder = (engine.box_builder or (lambda: box_builder.Builder(box_settings.load())))()
    st = builder.status(slug, card) if card else builder.status(slug)
    out = st.to_dict()
    out["nicLabel"] = "filtrowanie NIC: " + ("tak" if st.nic_filtering else "nie")
    cards = []
    for num, item in sorted(board.cards.items()):
        if item.project != slug:
            continue
        cid = f"K-{num}"
        if engine.journal.has(cid):
            cards.append({"card": cid, "boxWait": boxwait.card_box_wait(fold(engine.journal.read(cid)))})
    out["cards"] = cards
    out["timings"] = [{k: e[k] for k in ("type", "duration", "reason") if k in e}
                      for e in bs.events(slug, name) if e.get("type") in ("box.stop", "box.reset") and "duration" in e]
    out["ok"] = True
    return out
