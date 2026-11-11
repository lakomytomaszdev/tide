"""Zachowanie historycznych prób i nowe przekazanie K-38 bez edycji kartek/WIP."""
import json
import os
import stat

from ..box import trialgate
from ..tasks import stepstop
from .state import fold
from .processidentity import box_event_error, process_identities


def held(step):
    return step.get("_identityHeld") is True or trialgate.blocked_event(step)


def preserves_project(st, project=None):
    """Także hostowy zamiar bez step.started mógł już utworzyć proces."""
    return any(identity.held and (project is None or "" in identity.projects or project in identity.projects)
               for identity in st.process_identities.values())


def read_process_history(journal, card, phase=None):
    """Wspólna kontrola historii przed wykonaniem, inwentaryzacją i ochroną zasobów.

    `phase` (K-98 #3, `HistoryPhase` ticku): wynik tej kontroli — historia albo błąd — jest zapamiętany w fazie
    i zwracany ponownie bez kolejnego odczytu, dopóki ten proces nie zapisze historii karty."""
    if phase is not None:
        cached = phase.cached(card)
        if cached is not None:
            return cached
        version = phase.version(card)
    try:
        events = journal.read_complete(card)
        identities = process_identities(events)
        if any(identity.state != "ok" for identity in identities.values()):
            raise ValueError("nieznana lub niespójna tożsamość procesu w historii karty")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if phase is not None:
            phase.remember(card, version, error=exc)
        raise
    if phase is not None:
        phase.remember(card, version, events=events, identities=identities)
    return events


class HistoryPhase:
    """Jedna niezmienna faza odczytu historii procesów w ticku (K-98 #3, PT-344).

    Rozpoznanie niepełnej historii po restarcie, odtworzenie `step.started` (`_recover_started_steps`) i uzgodnienie
    sieci pudełek (`boxstep.recover_network`) dzielą jeden pełny ścisły odczyt historii karty z kontrolą tożsamości
    (`read_process_history(…, phase)`) zamiast czytać ją każde osobno. Faza żyje tylko w jednym ticku na workerze
    mutacji i ginie razem z nim; nie zastępuje odczytu przed skutkiem:

    - zapis tego procesu do historii karty (licznik `Journal.generation`) unieważnia jej odczyt — następne użycie
      czyta historię ponownie (osobny odczyt po zapisie),
    - każda mutacja oparta na fazie najpierw robi świeży pełny odczyt i porównuje go z odczytem fazy
      (`Journal.unchanged` pod blokadą zapisu, `HistoryPhase.unchanged`); zmiana albo ucięcie dziennika lub kopii
      w międzyczasie (także spoza procesu) daje odmowę bez skutku.

    Błąd odczytu albo nieznana tożsamość jest zapamiętany na tę wersję historii i rzucany przy każdym użyciu —
    unknown nie znika w obrębie fazy. Dziennik bez licznika zapisów (atrapa) — bez pamięci, zawsze świeży odczyt."""

    def __init__(self, journal):
        self.journal = journal
        self._read: dict = {}     # karta → (wersja, zdarzenia, tożsamości, błąd)

    def version(self, card):
        generation = getattr(self.journal, "generation", None)
        return generation(card) if generation is not None else None

    def remember(self, card, version, events=None, identities=None, error=None) -> None:
        if version is not None:
            self._read[card] = (version, tuple(events) if events is not None else None, identities, error)

    def cached(self, card):
        """Historia z odczytu fazy (kopia listy) albo None, gdy trzeba czytać; zapamiętany błąd jest rzucany."""
        hit = self._read.get(card)
        if hit is None or hit[0] != self.version(card):
            return None
        if hit[3] is not None:
            raise hit[3]
        return list(hit[1])

    def identities(self, card):
        """Tożsamości procesów z bieżącego odczytu fazy tej karty (tylko do odczytu) albo None, gdy go nie ma."""
        hit = self._read.get(card)
        if hit is None or hit[0] != self.version(card) or hit[3] is not None:
            return None
        return hit[2]

    def unchanged(self, card, events) -> bool:
        """Świeży pełny odczyt przed mutacją: True, gdy historia karty jest dokładnie `events` (odczytem decyzji)."""
        try:
            return self.journal.read_complete(card) == list(events)
        except (OSError, ValueError, TypeError, KeyError):
            return False


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
    for name, identity in st.process_identities.items():
        if not identity.held:
            continue
        step = {**identity.data, **st.steps.get(name, {}), **st.caretakers.get(name, {})}
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
    outside = []
    for identity in st.process_identities.values():
        if identity.held and identity.outside:
            started = [e for e in identity.outside if e.get("type") == "outside.started"]
            outside.extend(started or [{"step": identity.name, "ticket": identity.data.get("ticket")}])
    for event in outside:
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


def _validate_box_event(event, slug):
    """Tożsamość zdarzenia musi być znana przed pominięciem niechronionej próby.

    Reguły pól pochodzą ze wspólnego modelu (`processidentity.BOX_EVENTS`): kanoniczne pola
    i instancja pudełka projektu `slug` dziennika."""
    error = box_event_error(event, slug)
    if error:
        raise ValueError(error)


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
                _validate_box_event(event, slug)
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
