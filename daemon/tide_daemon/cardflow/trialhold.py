"""Zachowanie historycznych prób i nowe przekazanie K-38 bez edycji kartek/WIP."""
import json
import os
import stat

from ..box import state as bs, trialgate
from ..tasks import stepstop
from .state import fold


def held(step):
    return trialgate.blocked_event(step)


def preserves_project(st):
    """Także hostowy zamiar bez step.started mógł już utworzyć proces."""
    return (any(held(step) for step in st.steps.values())
            or any(held({**intent, "step": name}) for (action, name), intent in st.intents.items()
                   if action == "step-start"))


def _outside_evidence(event):
    return (event["type"] == "outside.started"
            or (event["type"] == "step.finished" and event.get("outside"))
            or (event["type"] == "step.decision" and event.get("choice") in ("run", "run-always", "run-ticket")))


def read_process_history(journal, card):
    """Wspólna kontrola historii przed wykonaniem, inwentaryzacją i ochroną zasobów."""
    events = journal.read_complete(card)
    _validate_outside_identity(events)
    return events


def _validate_outside_identity(events):
    """Start zachowuje tożsamość, a outside wskazuje wcześniejszy krok lub zamiar.

    Sam ticket/nazwa outside nie zastępuje brakującej historii startu. Ticket
    bywa pominięty w starym outside, ale podany musi zgadzać się z jego krokiem.
    """
    known = {}
    for event in events:
        if (event["type"] == "step.started"
                or (event["type"] == "intent" and event.get("action") == "step-start")):
            name = event["step"] if event["type"] == "step.started" else event["key"]
            previous = known.get(name)
            if previous is not None:
                if previous.get("ticket", "") != event.get("ticket", ""):
                    raise ValueError("sprzeczna tożsamość ticketu w historii startu")
                for field in ("kind", "branch", "worktree", "stepDir", "runner"):
                    if field in previous and field in event and previous[field] != event[field]:
                        raise ValueError("sprzeczna tożsamość kroku w historii startu: " + field)
            # Pominięte opcjonalne pole nie kasuje wcześniejszej wiedzy. Dzięki temu
            # kolejny zapis nie ukryje sprzeczności przez pośredni, skrócony wpis.
            known[name] = {**(previous or {}), **event}
        elif _outside_evidence(event):
            name = event.get("step")
            if not isinstance(name, str) or not name.strip() or name not in known:
                raise ValueError("niepełna lub osierocona tożsamość outside")
            if "ticket" in event and (not isinstance(event["ticket"], str)
                                      or event["ticket"] != known[name].get("ticket", "")):
                raise ValueError("tożsamość outside nie zgadza się z krokiem")


def inventory(engine, card, st):
    try:
        events = read_process_history(engine.journal, card)
        # Stan z tolerancyjnego odczytu UI lub wcześniejszego ticka nie jest dowodem.
        st = fold(events)
    except (OSError, ValueError, TypeError, KeyError):
        # Nie dopisujemy przez tolerancyjny parser do uszkodzonej historii.
        # Jej zachowany błąd oznacza unknown również po restarcie usługi.
        return False
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
            # result i step.started zapisywano po Popen, bez potwierdzenia scope.
            # Także odtworzony step.started, dawne done i brak klienta po restarcie
            # nie dowodzą końca uruchamiania. Nie mamy chronionego dowodu hosta.
            if state == "inactive":
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
    outside = [e for e in events if e.get("type") == "outside.started"]
    recorded = {e.get("step") for e in outside}
    for event in events:
        # Stare outside nie miało intent przed Popen. Oczekująca prośba lub zgoda
        # bez outside.started może oznaczać awarię tuż po utworzeniu klienta.
        name = event.get("step")
        if _outside_evidence(event) and name not in recorded:
            outside.append({"step": name, "ticket": event.get("ticket")})
            recorded.add(name)
    for event in outside:
        if event.get("step") not in protected and not held(event):
            continue
        try:
            state = stepstop.unit_state(event["unit"]) if event.get("unit") else "unknown"
            # outside.started nie potwierdza utworzenia scope przez asynchroniczny systemd-run.
            # Po restarcie brak jednostki nie rozstrzyga, czy klient dopiero ją utworzy.
            # Dawne outside.finished i plik exit-code też nie są chronionym dowodem hosta.
            if state == "inactive":
                state = "unknown"
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
    items = public_processes(engine.journal.root)
    try:
        # Publiczne CLI mogło nigdy nie mieć karty. Nie tworzymy jej przy skanie.
        if "K-38" in engine.journal.cards_complete():
            with engine.journal.lock:
                events = read_process_history(engine.journal, "K-38")
                previous = next((e for e in reversed(events) if e.get("type") == "k38.public-inventory"), {})
                if items != previous.get("items", []):
                    engine.journal.append_complete("K-38", "k38.public-inventory", version=1, items=items)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return not items


def _validate_box_event(event):
    """Tożsamość zdarzenia musi być znana przed pominięciem niechronionej próby."""
    if (not isinstance(event, dict) or type(event.get("v")) is not int
            or event["v"] != bs.SCHEMA or event.get("type") not in bs.EVENTS):
        raise ValueError("nieznany format zdarzenia pudełka")
    fields = ("ts", "box", "step") if event["type"] in ("box.use", "box.release") else ("ts", "box")
    if any(not isinstance(event.get(key), str) or not event[key].strip() for key in fields):
        raise ValueError("niepełna tożsamość zdarzenia pudełka")


def public_processes(board_root):
    """Odczyt śladów CLI i dzienników pudełek bez zmiany historii kart."""
    root = os.path.join(board_root, "projects")
    items = _public_process_inventory()
    try:
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            raise ValueError("rejestr projektów nie jest katalogiem")
        slugs = sorted(os.listdir(root))
    except (OSError, ValueError):
        slugs = []
        items.append({"state": "unknown", "path": root})
    for slug in slugs:
        project = os.path.join(root, slug)
        path = project
        try:
            mode = os.lstat(project).st_mode
            if stat.S_ISREG(mode) and slug.endswith(".md"):
                continue  # metadane BoardStore, nie katalog rejestrów pudełka
            if not stat.S_ISDIR(mode):
                raise ValueError("rejestr projektu nie jest katalogiem")
            path = os.path.join(project, "box")
            try:
                mode = os.lstat(path).st_mode
            except FileNotFoundError:
                if os.path.lexists(os.path.join(project, "box.yaml")):
                    items.append({"state": "unknown", "path": path})
                continue
            if not stat.S_ISDIR(mode):
                raise ValueError("rejestr pudełka nie jest katalogiem")
        except (OSError, ValueError):
            items.append({"state": "unknown", "path": path})
            continue
        path = os.path.join(project, "box", "journal.jsonl")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
            if content and not content.endswith("\n"):
                raise ValueError("urwany wpis dziennika pudełka")
            active = {}
            for line in content.splitlines():
                event = json.loads(line)
                _validate_box_event(event)
                if event["type"] not in ("box.use", "box.release"):
                    continue
                name = event["step"]
                if not (name.startswith("demo-") or held({"step": name})):
                    continue
                key = (event["box"], name)
                if event.get("type") == "box.use":
                    active[key] = {"project": slug, "box": key[0], "step": name, "state": "unknown"}
                elif event.get("type") == "box.release":
                    active.pop(key, None)
            items.extend(active.values())
        except (OSError, ValueError, UnicodeError):
            items.append({"state": "unknown", "path": path})
    return items


def _public_process_inventory():
    """Trwałe ślady bezpośredniego runner --demo, także bez projektu w Tablicy.

    Sama wolna blokada życia nie dowodzi końca jednostki w pudełku. Do czasu
    chronionego rozstrzygnięcia hostowego każdy taki ślad pozostaje unknown.
    Nie bierzemy blokad PortPool ani nie wywołujemy recover_demos/refguard.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    root = os.path.join(base, "tide", "box")
    items = []

    def entries(path):
        try:
            if not stat.S_ISDIR(os.lstat(path).st_mode):
                raise ValueError("rejestr nie jest katalogiem")
            return sorted(os.listdir(path))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            items.append({"state": "unknown", "path": path})
            return []

    for slug in entries(root):
        project = os.path.join(root, slug)
        # Sprawdzamy również katalog pośredni: nie podążamy za dowiązaniem.
        if "ports" not in entries(project):
            continue
        folder = os.path.join(project, "ports")
        for name in entries(folder):
            path = os.path.join(folder, name)
            if ".demo-" in name:
                items.append({"project": slug, "state": "unknown", "path": path, "kind": "demo-guard"})
                continue
            try:
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    raise ValueError("rejestr nie jest zwykłym plikiem")
                if name.endswith(".lock"):
                    continue  # zwykła blokada transakcji puli nie identyfikuje próby
                if not name.endswith(".json"):
                    raise ValueError("nieznany plik rejestru")
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, encoding="utf-8") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError("rejestr nie jest zwykłym plikiem")
                    content = source.read(4 * 1024 * 1024 + 1)
                if len(content) > 4 * 1024 * 1024:
                    raise ValueError("rejestr przekracza limit odczytu")
                leases = json.loads(content)
                if not isinstance(leases, dict):
                    raise ValueError("nieznany format rejestru")
                for owner in leases:
                    if owner.startswith("demo-") or held({"step": owner}):
                        items.append({"project": slug, "box": name[:-5], "step": owner,
                                      "state": "unknown", "path": path, "kind": "port-lease"})
            except (OSError, ValueError, UnicodeError):
                items.append({"state": "unknown", "path": path})
    return items


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
