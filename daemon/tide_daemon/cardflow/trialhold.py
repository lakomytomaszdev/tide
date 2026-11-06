"""Zachowanie historycznych prób i nowe przekazanie K-38 bez edycji kartek/WIP."""
import json
import os
import stat

from ..box import trialgate
from ..tasks import stepstop


def held(step):
    return trialgate.blocked_event(step)


def inventory(engine, card, st):
    events = engine.journal.read(card)
    items = []
    known = dict(st.steps)
    for (action, name), intent in st.intents.items():
        if action == "step-start" and name not in known:
            known[name] = {**intent, "step": name}
    for name, step in known.items():
        if not held(step):
            continue
        try:
            if step.get("mode") == "interactive":
                from ..tasks import launch
                state = stepstop.unit_state(step.get("consoleUnit") or "")
                if launch.process_alive(step.get("processFile") or "") is not False:
                    state = "unknown" if state == "inactive" else state
            else:
                state = engine._unit_state(step) if step.get("unit") else "unknown"
                proc = engine.procs.get(name)
                if proc is not None and proc.poll() is None:
                    state = "unknown" if state == "inactive" else state
                # Sam brak scope nie rozstrzyga okna Popen(systemd-run) → step.started.
                # Zakończenie klienta też nie dowodzi zakończenia uruchamianej jednostki.
                unresolved = ("step-start", name) in st.intents and ("step-start", name) not in st.results
                if state == "inactive" and (name not in st.steps or unresolved):
                    state = "unknown"
        except Exception:
            state = "unknown"
        items.append({"step": name, "ticket": step.get("ticket"), "state": state,
                      "unit": step.get("unit") or "", "worktree": step.get("worktree") or "",
                      "stepDir": step.get("stepDir") or "", "branch": step.get("branch") or "",
                      "wip": (st.finished.get(name) or {}).get("wip") or {}, "kind": "step"})
        if not any(e.get("type") == "k38.handoff" and e.get("step") == name for e in events):
            engine.journal.append(card, "k38.handoff", step=name, ticket=step.get("ticket"), version=1,
                                  instruction=trialgate.MESSAGE, preserved=True,
                                  excluded=step.get("ticket") in trialgate.EXCLUDED)
    protected = {i["step"] for i in items}
    for event in events:
        if event.get("type") != "outside.started":
            continue
        if event.get("step") not in protected and not trialgate.blocked(event.get("ticket") or ""):
            continue
        try:
            state = stepstop.unit_state(event["unit"]) if event.get("unit") else "unknown"
        except Exception:
            state = "unknown"
        items.append({"step": event.get("step"), "ticket": event.get("ticket"), "kind": "outside",
                      "state": state, "unit": event.get("unit") or "", "log": event.get("log") or ""})
    previous = next((e for e in reversed(events) if e.get("type") == "k38.inventory"), {})
    if items and previous.get("items") != items:
        engine.journal.append(card, "k38.inventory", version=1, items=items,
                              instruction="Zachowano procesy i WIP. Nie cofnięto wcześniejszych skutków.")
    public_safe = public_inventory(engine) if card == "K-38" else True
    return public_safe and all(i["state"] == "inactive" for i in items)


def public_inventory(engine):
    """Dawne CLI nie miało kroku w dzienniku karty. Nie sprzątamy jego rezerwacji.

    Bez trwałej tożsamości procesu nie potwierdzamy końca samym brakiem klienta
    CLI. Aktywne/osierocone demo wymaga osobnego ustalenia stanu przez hosta.
    """
    root = os.path.join(engine.journal.root, "projects")
    items = []
    try:
        slugs = sorted(os.listdir(root))
    except FileNotFoundError:
        slugs = []
        items.append({"state": "unknown", "path": root})
    except OSError:
        slugs = []
        items.append({"state": "unknown", "path": root})
    for slug in slugs:
        path = os.path.join(root, slug, "box", "journal.jsonl")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if os.path.exists(os.path.dirname(path)) or os.path.exists(os.path.join(root, slug, "box.yaml")):
                items.append({"state": "unknown", "path": path})
            continue
        except OSError:
            items.append({"state": "unknown", "path": path})
            continue
        try:
            with os.fdopen(fd, encoding="utf-8") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("dziennik nie jest zwykłym plikiem")
                content = source.read(4 * 1024 * 1024 + 1)
            if len(content) > 4 * 1024 * 1024:
                raise ValueError("dziennik przekracza limit odczytu")
            active = {}
            for line in content.splitlines():
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("nieznane zdarzenie")
                name = event.get("step") or ""
                if not (str(name).startswith("demo-") or held({"step": name})):
                    continue
                key = (event.get("box") or "", name)
                if event.get("type") == "box.use":
                    active[key] = {"project": slug, "box": key[0], "step": name, "state": "unknown"}
                elif event.get("type") == "box.release":
                    active.pop(key, None)
            items.extend(active.values())
        except (OSError, ValueError, UnicodeError):
            items.append({"state": "unknown", "path": path})
    events = engine.journal.read("K-38")
    previous = next((e for e in reversed(events) if e.get("type") == "k38.public-inventory"), {})
    if items != previous.get("items", []):
        engine.journal.append("K-38", "k38.public-inventory", version=1, items=items)
    return not items


def view(engine, card, out):
    """Hostowe UI nie proponuje już starych zgód; historia decyzji zostaje."""
    if not out:
        return
    for decision in out["flow"]["decisions"]:
        if trialgate.blocked(decision.get("ticket") or ""):
            decision.update(why=trialgate.MESSAGE, options=[], freeText=False, reply=None)
    events = engine.journal.read(card)
    consumed = {e.get("step") for e in events if e.get("type") == "k38.diagnostic-consumed"}
    for event in events:
        if event.get("type") != "k38.diagnostic-requested" or event.get("step") in consumed:
            continue
        name = event["step"]
        out["flow"]["decisions"].append({
            "id": "step:" + name, "step": name, "stepKind": "diagnostic", "ticket": "PT-273",
            "executor": "", "kind": "permission", "happened": "Usługa zarejestrowała diagnostykę PT-273.",
            "why": "Jednorazowy cel usługi, bez repozytorium użytkownika. Zgoda dotyczy tylko tej wersji próby.",
            "options": [{"id": "run", "title": "Uruchom tę diagnostykę raz", "effect": "Usługa wykona zarejestrowaną procedurę.",
                         "recommended": False, "choice": "run", "method": "ResolveCardStep",
                         "params": {"step": name, "choice": "run"}}],
            "freeText": False, "reply": None, "fault": "", "findings": [], "technical": event["binding"]})
