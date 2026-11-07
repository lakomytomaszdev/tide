"""Czysty model tożsamości procesów z całej historii, bez I/O i stanu usługi.

Kolejne zdarzenie może dodać wiedzę, ochronę lub błąd, nigdy ich usunąć.
Stan tożsamości nie potwierdza końca procesu: to osobny dowód hostowy.
"""
from dataclasses import dataclass, field

from .. import trialpolicy
from ..box import profile as bp, state as bs

CARD_STEP_KINDS = frozenset({"planning", "plan-fix", "plan-import", "plan-review",
                             "plan-amend", "plan-revise", "close", "interactive"})
TICKET_STEP_KINDS = frozenset({"ticket", "fix", "review", "conflict"})
CONSOLE_EVENTS = ("step.entered", "step.console_opened", "step.console_closed")
IDENTITY_FIELDS = ("ticket", "kind", "branch", "worktree", "stepDir", "runner", "box", "boxSlug",
                   "consoleUnit", "processFile")
START_FIELDS = IDENTITY_FIELDS + ("unit",)
RESOURCE_FIELDS = ("runner", "box", "boxSlug")


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
    return ""


def outside_evidence(event):
    return (str(event.get("type") or "").startswith("outside.")
            or (event.get("type") == "step.finished" and event.get("outside"))
            or (event.get("type") == "step.decision"
                and event.get("choice") in ("run", "run-always", "run-ticket")))


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
    try:
        bp._check_slug(slug, "projekt")
        if slug != slug.strip():
            return "unknown"
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
    wymaga wcześniejszego startu/zamiaru. Brak nazwy ma osobną tożsamość unknown,
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
            project = value if isinstance(value, str) else ""
            card = event.get("card", "")
        start = kind == "step.started" or (kind == "intent" and event.get("action") == "step-start")
        result = kind == "result" and event.get("action") == "step-start"
        outside = outside_evidence(event)
        if not (start or result or outside or kind in CONSOLE_EVENTS or kind in ("step.decision", "step.finished")):
            continue
        name = event.get("key") if kind in ("intent", "result") else event.get("step")
        valid_name = isinstance(name, str) and bool(name.strip())
        key = name if valid_name else (None, index)
        identity = identities.setdefault(key, ProcessIdentity(name if valid_name else ""))
        identity.projects.add(project)
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
        if not valid_name:
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
                if kind == "outside.started" and "unit" in event:
                    unit = event["unit"]
                    if not isinstance(unit, str):
                        identity.worsen("unknown")
                    elif identity.outside_unit is not None and identity.outside_unit != unit:
                        identity.worsen("inconsistent")
                    else:
                        identity.outside_unit = unit
                identity.outside.append(dict(event))
    return identities
