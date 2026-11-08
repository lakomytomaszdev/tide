"""Czysty model tożsamości procesów z całej historii, bez I/O i stanu usługi.

Kolejne zdarzenie może dodać wiedzę, ochronę lub błąd, nigdy ich usunąć.
Stan tożsamości nie potwierdza końca procesu: to osobny dowód hostowy.
"""
from dataclasses import dataclass, field

from .. import trialpolicy
from ..box import profile as bp, state as bs
from ..coordination.numbering import ticket_key

CARD_STEP_KINDS = frozenset({"planning", "plan-fix", "plan-import", "plan-review",
                             "plan-amend", "plan-revise", "close", "interactive"})
TICKET_STEP_KINDS = frozenset({"ticket", "fix", "review", "conflict"})
CONSOLE_EVENTS = ("step.entered", "step.console_opened", "step.console_closed")
IDENTITY_FIELDS = ("ticket", "kind", "branch", "worktree", "stepDir", "runner", "box", "boxSlug",
                   "consoleUnit", "processFile")
START_FIELDS = IDENTITY_FIELDS + ("unit",)
RESOURCE_FIELDS = ("runner", "box", "boxSlug")
CONSOLE_FIELDS = ("consoleUnit", "processFile")
# (dowody własne zdarzenia, dowody dopuszczające dziedziczenie). Każde
# uruchomienie osobnego procesu musi zapisać swój komplet, a potwierdzenie
# otwarcia/zamknięcia może odwołać się do wcześniejszego kompletu Konsoli.
# unit kroku pozostaje opcjonalne/puste przed przejściem do Konsoli.
REQUIRED_PROCESS_TEXT = {
    "outside.started": (("unit",), ()),
    "step.entered": (CONSOLE_FIELDS, ()),
    "step.console_opened": ((), CONSOLE_FIELDS),
    "step.console_closed": ((), CONSOLE_FIELDS),
}
# Akcje metadanych i Gita nie są śladami procesu kroku. Nowy dyskryminator
# wymaga jawnej klasyfikacji; brak lub literówka nie dowodzi braku procesu.
NON_PROCESS_ACTIONS = frozenset({"reserve", "card-branch", "ticket-branch", "review-branch",
                                 "step-reopen", "plan-commit", "cleanup", "merge",
                                 "youtrack-create", "youtrack-link", "youtrack-note"})
# Wyłącznie dokładnie rozpoznane metadane można pominąć. Nowy typ zdarzenia
# wymaga klasyfikacji tutaj, także gdy nie zawiera żadnych pól procesu.
NON_PROCESS_EVENTS = frozenset({
    "card.started", "card.inputs", "card.paused", "card.resumed", "card.finished",
    "card.closed", "card.main-merged", "card.moved", "card.stalled",
    "plan.checked", "plan.imported", "plan.proposed", "plan.reviewer-selected",
    "plan.approval_requested", "plan.returned", "plan.revision-discarded",
    "plan.revision-failed", "plan.revision-ready", "plan.revision-approved",
    "plan.reviewed", "plan.ticket-edited", "plan.approved", "plan.finished",
    "ticket.assigned", "ticket.rejected", "ticket.imported", "reviewer.reassigned",
    "review.verdict", "review.question", "review.decision", "review.reviewer-note",
    "step.waiting", "step.retry", "step.transfer_requested", "step.wip", "step.limit",
    "step.notice", "step.activity", "step.reported", "step.cleanup",
    "flow.error", "flow.notice", "permission.rechecked", "refs.guard",
    "box.wait", "box.rebuild-requested", "box.ports", "box.risk", "packs.removed",
    "k38.handoff", "k38.inventory", "k38.public-inventory",
    "k38.diagnostic-requested", "k38.diagnostic-consumed", "youtrack.reset", "youtrack.retry",
})
PROCESS_EVENTS = frozenset({"intent", "result", "step.started", "step.finished", "step.decision",
                            "outside.started", "outside.finished", "outside.ticket-consent",
                            *CONSOLE_EVENTS})


@dataclass
class ProcessIdentity:
    name: str
    state: str = "ok"
    protected: bool = False
    data: dict = field(default_factory=dict)
    outside: list = field(default_factory=list)
    has_start: bool = False
    projects: set = field(default_factory=set)
    outside_unit: str | None = None

    @property
    def held(self):
        return self.protected or self.state != "ok"

    def worsen(self, state):
        rank = {"ok": 0, "unknown": 1, "inconsistent": 2}
        if rank[state] > rank[self.state]:
            self.state = state


def valid_ticket(value):
    """Dokładny klucz numeracji; rozpoznawalny wariant nie jest innym ticketem."""
    return isinstance(value, str) and bool(value) and ticket_key(value) == value


def complete_process_evidence(event, previous):
    """Wymagane dowody i jawne pola Konsoli są niepustym tekstem."""
    kind = event.get("type")
    own, inherited = REQUIRED_PROCESS_TEXT.get(kind, ((), ())) if isinstance(kind, str) else ((), ())
    current = {**previous, **event}
    values = [event.get(key) for key in own]
    values.extend(current.get(key) for key in inherited)
    values.extend(event[key] for key in CONSOLE_FIELDS if key in event)
    return all(isinstance(value, str) and bool(value.strip()) for value in values)


def start_error(event):
    """Struktura starego startu; opcjonalne pola nie kasują wcześniejszej wiedzy."""
    name = "key" if event.get("type") == "intent" else "step"
    if not isinstance(event.get(name), str) or not event[name].strip():
        return "niepełna tożsamość procesu w dzienniku karty"
    if any(key in event and not isinstance(event[key], str) for key in START_FIELDS):
        return "nieznana tożsamość procesu w dzienniku karty"
    kind = event.get("kind")
    if "kind" in event and (not isinstance(kind, str) or kind not in CARD_STEP_KINDS | TICKET_STEP_KINDS):
        return "nieznany rodzaj kroku w dzienniku karty"
    if not (event.get("ticket") or "").strip() and kind not in CARD_STEP_KINDS:
        return "brak ticketu procesu w dzienniku karty"
    if event.get("ticket", "") != "" and not valid_ticket(event["ticket"]):
        return "niekanoniczny ticket procesu w dzienniku karty"
    return ""


def outside_evidence(event):
    return (str(event.get("type") or "").startswith("outside.")
            or (event.get("type") == "step.finished" and event.get("outside"))
            or (event.get("type") == "step.decision"
                and event.get("choice") in ("run", "run-always", "run-ticket")))


def valid_project(value):
    """Projekt musi być dokładnym slugiem; nie naprawiamy kontekstu historii."""
    try:
        bp._check_slug(value, "projekt")
        return value == value.strip()
    except (ValueError, TypeError):
        return False


def resource_state(data, card):
    """Czysta kontrola tego samego powiązania, które zapisuje Placement.entry."""
    runner = data.get("runner", "host")
    has_box = any(key in data for key in ("box", "boxSlug"))
    if runner not in ("host", "box"):
        return "unknown"
    if runner == "host":
        return "inconsistent" if has_box else "ok"
    if any(not isinstance(data.get(key), str) or not data[key] for key in RESOURCE_FIELDS):
        return "unknown"
    slug = data["boxSlug"]
    if not valid_project(slug):
        return "unknown"
    try:
        if data["box"] == bs.box_name(slug):
            return "ok"
        if not card:
            return "unknown"
        return "ok" if data["box"] == bs.card_box_name(slug, card) else "inconsistent"
    except (ValueError, TypeError):
        return "unknown"


def process_identities(events):
    """step → ok/unknown/inconsistent, z monotoniczną ochroną każdego śladu.

    Niepełny wpis pozostaje unknown nawet po późniejszym uzupełnieniu. Outside
    wymaga wcześniejszego startu/zamiaru kroku, a zakończenie outside — wcześniejszego
    startu jego osobnego procesu. Brak nazwy ma osobną tożsamość unknown,
    zatem nie znika przez filtrowanie ani przez kolizję z poprawnym krokiem.
    """
    identities = {}
    project = ""
    card = ""
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            identities[None, index] = ProcessIdentity("", state="unknown")
            continue
        kind = event.get("type")
        if kind == "card.started":
            value = event.get("project")
            project = value if valid_project(value) else ""
            card = event.get("card", "")
        uncertain_type = not isinstance(kind, str) or kind not in PROCESS_EVENTS | NON_PROCESS_EVENTS
        action = event.get("action")
        uncertain_action = kind in ("intent", "result") and (
            not isinstance(action, str) or (action != "step-start" and action not in NON_PROCESS_ACTIONS))
        start = uncertain_type or kind == "step.started" or (
            kind == "intent" and (action == "step-start" or uncertain_action))
        result = kind == "result" and (action == "step-start" or uncertain_action)
        outside = outside_evidence(event)
        if not (start or result or outside or kind in CONSOLE_EVENTS or kind in ("step.decision", "step.finished")):
            continue
        name = event.get("key") if kind in ("intent", "result") else event.get("step")
        if uncertain_type and "step" not in event:
            name = event.get("key")
        valid_name = isinstance(name, str) and bool(name.strip())
        key = name if valid_name else (None, index)
        identity = identities.setdefault(key, ProcessIdentity(name if valid_name else ""))
        if uncertain_action or uncertain_type:
            # Przechowujemy cały ślad bez naprawiania dyskryminatorów. Nawet
            # późniejszy poprawny start/wynik nie rozstrzyga nieznanej operacji.
            identity.worsen("unknown")
        identity.projects.add(project)
        if not project:
            # Wadliwy kontekst nie wyznacza zakresu nawet dla procesu hostowego.
            # Późniejsze zlecenie nie usuwa ani błędu, ani nieznanego projektu.
            identity.worsen("unknown")
        # Start deklarujący zasób musi być kompletny sam w sobie. Zdarzenia
        # uzupełniające mogą dziedziczyć brakujące pola z wcześniejszego startu.
        resource = event if start and any(k in event for k in RESOURCE_FIELDS) else {
            **identity.data, **{k: event[k] for k in RESOURCE_FIELDS if k in event}}
        state = resource_state(resource, event.get("card", identity.data.get("card", card)))
        if state != "ok":
            identity.worsen(state)
            # Nie zgadujemy projektu z wadliwego slugu ani z nazwy instancji.
            # Nieznany zakres nigdy nie może zostać później zawężony.
            identity.projects.add("")
        # Ticket karty może pracować w innym projekcie. Zbieramy każdy jawny
        # zasób przed scaleniem pól, również z późniejszego sprzecznego wpisu.
        if "boxSlug" in event:
            slug = event["boxSlug"]
            if isinstance(slug, str) and slug.strip():
                identity.projects.add(slug)
            else:
                identity.projects.add("")
                identity.worsen("unknown")
        identity.protected |= trialpolicy.blocked_event(event)
        # Każdy jawny ślad ID podlega tej samej kontroli, także wynik i decyzja.
        # Pusty ticket pozostaje legalny dla kroku karty; kolizję z ticketem
        # wcześniejszego startu wykrywa niżej scalanie tożsamości.
        if "ticket" in event and event["ticket"] != "" and not valid_ticket(event["ticket"]):
            identity.worsen("unknown")
        if not valid_name:
            identity.worsen("unknown")
        if not complete_process_evidence(event, identity.data):
            identity.worsen("unknown")
        if start:
            if start_error(event):
                identity.worsen("unknown")
            # Brak ticketu bywa legalny dla kroku karty, ale nie dla ponownego
            # zapisu tożsamości, która wcześniej nazywała konkretny ticket.
            if identity.has_start and identity.data.get("ticket", "") != event.get("ticket", ""):
                identity.worsen("inconsistent")
            for field_name in START_FIELDS:
                if field_name not in event:
                    continue
                if field_name in identity.data and identity.data[field_name] != event[field_name]:
                    identity.worsen("inconsistent")
                else:
                    identity.data[field_name] = event[field_name]
            identity.has_start = True
            # Dane operacyjne nie są źródłem klasyfikacji ochrony.
            for field_name, value in event.items():
                if field_name not in START_FIELDS:
                    identity.data[field_name] = value
            identity.data["step"] = identity.name
        else:
            if not identity.has_start:
                identity.worsen("unknown")
            for field_name in START_FIELDS if result else IDENTITY_FIELDS:
                if field_name not in event:
                    continue
                value = event[field_name]
                if not isinstance(value, str):
                    identity.worsen("unknown")
                elif field_name in identity.data and identity.data[field_name] != value:
                    identity.worsen("inconsistent")
                elif field_name == "ticket" and value != identity.data.get("ticket", ""):
                    identity.worsen("inconsistent")
                else:
                    identity.data[field_name] = value
            if outside:
                if kind == "outside.finished" and identity.outside_unit is None:
                    # Tylko wcześniejszy outside.started ustala tę jednostkę.
                    # Start kroku ani unit zakończenia nie dowodzą startu outside;
                    # późniejsze uzupełnienie nie może usunąć wykrytej niepewności.
                    identity.worsen("unknown")
                if kind == "outside.started":
                    # Outside uruchamia osobny proces: jego jednostka musi być
                    # zapisana w tym starcie, nie odziedziczona z kroku.
                    unit = event.get("unit")
                    if isinstance(unit, str) and unit.strip():
                        if identity.outside_unit is not None and identity.outside_unit != unit:
                            identity.worsen("inconsistent")
                        else:
                            identity.outside_unit = unit
                identity.outside.append(dict(event))
    return identities
