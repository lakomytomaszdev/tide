"""Czysty model tożsamości procesów z całej historii, bez I/O i stanu usługi.

Kolejne zdarzenie może dodać wiedzę, ochronę lub błąd, nigdy ich usunąć.
Stan tożsamości nie potwierdza końca procesu: to osobny dowód hostowy.

PT-288: jedna deklaratywna tabela (`EVENTS`, `ACTIONS`, `BOX_EVENTS`) opisuje każde
rozpoznane zdarzenie dziennika karty i pudełka: jego rolę dowodową albo uzasadnienie
braku tej roli, a dla zdarzeń procesu — rolę, typ, niepustość, poprzednika, domenę
tożsamości i regułę zgodności każdego pola. Reduktor i walidatory (dziennik karty,
dziennik pudełka) czytają wyłącznie tę tabelę; konsumenci nie mają własnych reguł pól.
"""
from dataclasses import dataclass, field, replace
import re
from types import MappingProxyType

from .. import trialpolicy
from ..box import profile as bp, state as bs
from ..coordination.numbering import ticket_key
from . import commandruns

CARD_STEP_KINDS = frozenset({"planning", "plan-fix", "plan-import", "plan-review",
                             "plan-amend", "plan-revise", "close", "interactive",
                             "caretaker", "caretaker-chat"})
TICKET_STEP_KINDS = frozenset({"ticket", "fix", "review", "conflict"})
# Pseudokrok próby testów po scaleniu (PT-329): proces outside ticketu bez kroku agenta (`merge.check-step`).
MERGE_CHECK_KINDS = frozenset({"merge-check"})
OUTSIDE_CHOICES = frozenset({"run", "run-always", "run-ticket"})


# ------------------------------------------------------------------ model

@dataclass(frozen=True)
class Field:
    """Rola pola w dowodzie tożsamości.

    role:
      name           — nazwa procesu (`step`/`key`), niepusty tekst; brak/błąd = osobna tożsamość unknown,
      discriminator  — rozstrzyga klasyfikację zdarzenia (`action`, `choice`, `outside`),
      identity       — opcjonalne pole tożsamości procesu; brak dziedziczy, jawna wartość musi być
                       kanoniczna i zgodna z wcześniejszą wartością w tej samej domenie,
      conditional    — wymagane warunkowo (zasób pudełka przy runner=box, ticket kroku ticketu),
      own            — wymagany własny dowód tego zdarzenia (osobny proces),
      inherited      — wymagany dowód, którego brak dziedziczy wyłącznie kompletny wcześniejszy
                       `predecessor` tego samego procesu; jawna wartość musi być z nim zgodna,
      context        — kontekst karty (projekt),
      generation     — numer próby procesu (`attempt`); inna wartość w kolejnym starcie tej samej
                       nazwy rozpoczyna nową generację: dowody poprzedników starszej generacji
                       nie są dziedziczone (PT-288 review 1, M2),
      neutral        — dane operacyjne/metadane bez roli dowodowej (uzasadnienie w `note`).
    type: str | bool | int | any.  nonempty: wymagany tekst niepusty po strip() i bez otaczających znaków białych.
    domain: step | console | outside | resource | card.
    rule: match | ticket | kind | resource | slug | box (nazwa pudełka projektu dziennika pudełka).
    source: writer (zapis w kodzie usługi), historical (starszy format, sonda lub ślad jawnie wstrzyknięty) albo
            reader (rozpoznawane przed zapisem — krok „rozszerz” widoku przebiegów `commandruns`, K-109 PT-481;
            zapis włączy PT-479/PT-485 i zmieni źródło na writer).
    """
    role: str
    type: str = "any"
    nonempty: bool = False
    predecessor: str = ""
    domain: str = ""
    rule: str = ""
    source: str = "writer"
    note: str = ""


@dataclass(frozen=True)
class Event:
    """Rola zdarzenia: context, step-start, step-result, step-trace, console-enter, console-ack,
    outside-start, outside-finish, outside-trace, action (zamiar/wynik według ACTIONS),
    box-trace albo metadata. Zdarzenie metadanych ma uzasadnienie. `dynamic=True` oznacza
    dodatkowy ładunek dynamiczny, którego reduktor nie czyta; pola jawne zapisu nadal muszą
    być sklasyfikowane w `fields` (M4 review 1).

    `start` — wymagany poprzednik startu kroku dla śladu procesu:
      domain — start tego samego procesu w bieżącej domenie (projekt, zlecenie, generacja),
      process — dowolny wcześniejszy start tego procesu (legalne historyczne zakończenie
                dawnego procesu po zleceniu innego projektu; ochrona obejmuje oba projekty)."""
    role: str
    fields: object = None
    reason: str = ""
    source: str = "writer"
    dynamic: bool = False
    start: str = "domain"


def _neutral(*names, note="dane operacyjne bez roli dowodowej", source="writer"):
    return {name: Field("neutral", note=note, source=source) for name in names}


def _metadata(reason, *names, source="writer"):
    return Event("metadata", MappingProxyType(_neutral(*names, note=reason, source=source)), reason, source)


def _dynamic(reason, *names, source="writer"):
    """Metadane z ładunkiem dynamicznym; `names` — pola jawne zapisane obok niego."""
    return Event("metadata", MappingProxyType(_neutral(*names, note=reason, source=source)), reason, source, True)


NAME_STEP = Field("name", "str", True, domain="step", note="nazwa procesu kroku; źródło ochrony")
NAME_KEY = Field("name", "str", True, domain="step", note="nazwa procesu kroku w zamiarze/wyniku; źródło ochrony")
TICKET = Field("identity", "str", domain="step", rule="ticket",
               note="dokładny klucz numeracji; pusty tylko dla kroku karty; źródło ochrony")
TICKET_START = Field("conditional", "str", domain="step", rule="ticket",
                     note="wymagany dla rodzaju kroku ticketu; pusty legalny dla kroku karty; źródło ochrony")
KIND = Field("identity", "str", domain="step", rule="kind", note="rozpoznany rodzaj kroku")
BRANCH = Field("identity", "str", domain="step", rule="match", note="gałąź procesu; źródło ochrony")
WORKTREE = Field("identity", "str", domain="step", rule="match")
STEP_DIR = Field("identity", "str", domain="step", rule="match")
STEP_UNIT = Field("identity", "str", domain="step", rule="match",
                  note="jednostka kroku; pusta przed przejściem do Konsoli (runda 8)")
RUNNER = Field("conditional", "str", domain="resource", rule="resource", note="host albo box")
BOX = Field("conditional", "str", domain="resource", rule="resource",
            note="wymagany przy runner=box; nazwa zgodna z box_name/card_box_name")
BOX_SLUG = Field("conditional", "str", domain="resource", rule="slug", note="wymagany przy runner=box; projekt")
CONSOLE_UNIT = Field("identity", "str", True, domain="console", rule="match",
                     note="jednostka Konsoli zapisana przy starcie rozmowy opiekuna")
PROCESS_FILE = Field("identity", "str", True, domain="console", rule="match",
                     note="plik procesu Konsoli zapisany przy starcie rozmowy opiekuna")


def _historical(spec, note="jawny ślad starszego formatu porównywany z kompletem procesu"):
    return Field(spec.role, spec.type, spec.nonempty, spec.predecessor, spec.domain, spec.rule, "historical",
                 spec.note or note)


# Pola tożsamości kroku, które każdy ślad procesu może jawnie powtórzyć (brak dziedziczy).
TRACE_IDENTITY = MappingProxyType({
    "ticket": TICKET, "kind": KIND, "branch": BRANCH, "worktree": WORKTREE, "stepDir": STEP_DIR,
    "runner": RUNNER, "box": BOX, "boxSlug": BOX_SLUG,
    "consoleUnit": CONSOLE_UNIT, "processFile": PROCESS_FILE,
})


def _trace(own=None, writer=(), neutral=(), extra=None):
    """Pola śladu procesu: nazwa, jawne pola tożsamości (writer albo historical) i neutralne."""
    fields = {"step": NAME_STEP}
    for name, spec in TRACE_IDENTITY.items():
        fields[name] = spec if name in writer else _historical(spec)
    fields.update(_neutral(*neutral))
    fields.update(extra or {})
    return MappingProxyType(fields)


ATTEMPT = Field("generation", "int", domain="step",
                note="numer próby; inna wartość tej samej nazwy to nowa generacja procesu bez dziedziczenia "
                     "poprzedników; brak nie zmienia generacji")
START_IDENTITY = {"ticket": TICKET_START, "kind": KIND, "branch": BRANCH, "worktree": WORKTREE,
                  "stepDir": STEP_DIR, "runner": RUNNER, "box": BOX, "boxSlug": BOX_SLUG,
                  "unit": STEP_UNIT}
START_NEUTRAL = ("baseCommit", "baseRef", "baseVersion", "boxAccess", "boxIp", "boxPortOwner",
                 "closeFile", "consoleClosed", "contractCommands", "consoleOpened", "deliveredNoteIds", "effort", "enteredAt",
                 "executor", "executorReason", "gitDir", "local", "mode", "model", "parentStep", "program", "provider",
                 "readDirsProfile", "repo", "reviewTip", "round", "routeKind", "routeTokenFile", "routeUrl",
                 "session", "taskFile", "testPolicy", "testPolicyCard", "toolingKind")
CONSOLE_INHERITED = Field(
    "inherited", "str", True, predecessor="step.entered|step.started", domain="console", rule="match",
    source="historical",
    note="brak dziedziczy z kompletnej Konsoli tego kroku: step.entered albo step.started rozmowy opiekuna "
         "z własnym kompletem (odzyskanie przed wejściem); jawna wartość musi być zgodna")
OUTSIDE_UNIT_OWN = Field("own", "str", True, domain="outside", rule="match",
                         note="jednostka osobnego procesu outside; nie dziedziczy z kroku")
OUTSIDE_UNIT_INHERITED = Field(
    "inherited", "str", True, predecessor="outside-start", domain="outside", rule="match", source="historical",
    note="brak dziedziczy z kompletnego startu przebiegu (rola outside-start: outside.started albo "
         "command.started); jawna wartość musi być zgodna (runda 10)")


def _reader(event):
    """Zdarzenie rodziny `command.*` rozpoznawane przed zapisem (PT-481): ta sama rola i te same reguły pól co
    odpowiednik `outside.*`, źródło `reader` dla zdarzenia i każdego pola (żadne miejsce w kodzie go jeszcze nie
    zapisuje)."""
    fields = MappingProxyType({name: replace(spec, source="reader") for name, spec in event.fields.items()})
    return Event(event.role, fields, event.reason or "przebieg polecenia (protokół jednego wykonania, ADR-1)",
                 "reader", event.dynamic, event.start)


def _historical_event(event):
    """Zdarzenie, którego kod już nie zapisuje, a historia dzienników nadal je zawiera (źródło `historical` dla
    zdarzenia i każdego pola; role i reguły pól bez zmian)."""
    fields = MappingProxyType({name: replace(spec, source="historical") for name, spec in event.fields.items()})
    return Event(event.role, fields, event.reason, "historical", event.dynamic, event.start)


def _reader_fields(event, names):
    """Zdarzenie zapisywane w kodzie, którego pola `names` są dopiero rozpoznawane przed zapisem (źródło `reader`)."""
    fields = MappingProxyType({name: replace(spec, source="reader") if name in names else spec
                               for name, spec in event.fields.items()})
    return Event(event.role, fields, event.reason, event.source, event.dynamic, event.start)


COMMAND_NEUTRAL = commandruns.COMMAND_ONLY
COMMAND_LATER = ("recovered", "reason")     # pola odzyskiwania zamiaru bez startu (PT-485)

EVENTS = MappingProxyType({
    # --- kontekst i procesy -------------------------------------------------------------
    "card.started": Event("context", MappingProxyType({
        "project": Field("context", "str", True, domain="card", rule="slug",
                         note="zakres projektu procesów karty; wadliwy daje unknown i nieznany zakres"),
        **_neutral("assignmentRules", "baseBranch", "baseCommit", "baseRef", "baseVersion", "cardBranch", "check",
                   "column", "effort", "entry", "executor", "executorEffort", "executorRouteKind",
                   "executorRouteTokenFile", "executorRouteUrl", "images", "mode", "model", "moveId",
                   "preferredProvider", "prefix", "program", "projects", "provider", "repo", "request", "reviewer",
                   "reviewerEffort", "reviewerModel", "reviewerProgram", "reviewerProvider", "reviewerRouteKind",
                   "reviewerRouteTokenFile", "reviewerRouteUrl", "routeKind", "routeTokenFile", "routeUrl",
                   "trackerProject", "trackerUrl", note="ustawienia karty bez roli dowodowej"),
        **_neutral("importPreparation", "orderId", "sourceEvidence",
                   note="powiązanie z przygotowaniem importu, zleceniem startu i dowodami sprawy; bez roli dowodowej"),
    }), "początek zlecenia karty; ustala kontekst projektu"),
    "step.started": Event("step-start", MappingProxyType({
        "step": NAME_STEP, **START_IDENTITY, "consoleUnit": CONSOLE_UNIT, "processFile": PROCESS_FILE,
        "attempt": ATTEMPT, **_neutral(*START_NEUTRAL),
    })),
    # PT-329: start pseudokroku próby testów po scaleniu — poprzednik jej decyzji i uruchomienia outside.
    "merge.check-step": Event("step-start", MappingProxyType({
        # Bez `runner`/`box`: próba biegnie wyłącznie na hoście (brak zasobu pudełka = host).
        "step": NAME_STEP, **{name: START_IDENTITY[name] for name in ("ticket", "kind", "branch", "worktree",
                                                                      "stepDir")},
        **_neutral("after", "baseVersion", "canAllow", "check", "command",
                   note="próba (PT-318), polecenie i wierzchołek gałęzi karty; bez roli dowodowej"),
    })),
    "step.entered": Event("console-enter", _trace(
        writer=(), neutral=("closeFile", "deliveredNoteIds", "enteredAt", "readDirsProfile", "session", "source",
                            "taskFile"),
        extra={"consoleUnit": Field("own", "str", True, domain="console", rule="match",
                                    note="każde wejście zapisuje własny komplet Konsoli"),
               "processFile": Field("own", "str", True, domain="console", rule="match",
                                    note="każde wejście zapisuje własny komplet Konsoli")})),
    "step.console_opened": Event("console-ack", _trace(
        neutral=("recovered",), extra={"consoleUnit": CONSOLE_INHERITED, "processFile": CONSOLE_INHERITED})),
    "step.console_closed": Event("console-ack", _trace(
        neutral=("cancelledNoteIds",), extra={"consoleUnit": CONSOLE_INHERITED, "processFile": CONSOLE_INHERITED})),
    "step.decision": Event("step-trace", _trace(
        writer=("ticket", "kind"),
        neutral=("allow", "answer", "auto", "automatic", "baseDecision", "caretaker", "caretakerStep", "check",
                 "command", "commands", "envAnswer", "escalated", "executor", "instead", "refusal", "resetTs", "skipped",
                 "waitCard", "waitTicket"),
        extra={"choice": Field("discriminator", "str", domain="outside",
                               note="run/run-always/run-ticket to zgoda outside; inna wartość to zwykły ślad")})),
    "step.finished": Event("step-trace", _trace(
        writer=("ticket",),
        neutral=("baseProposal", "canAllow", "command", "commands", "exclusive", "exit", "fault", "faultCause", "kept",
                 "messages", "mode", "network", "notes", "options", "outcome", "pattern", "question", "reason",
                 "result", "snapshotDir", "stalledSince", "waitFor", "wip"),
        extra={"outside": Field("discriminator", "bool", domain="outside",
                                note="prośba o polecenie outside tego kroku")}),
        start="process"),
    # K-109 PT-479 i PT-483: prośba kroku i próba testów po scaleniu zapisują już `command.*`; `outside.started`
    # i `outside.finished` zostają w historii przebiegów sprzed przełączenia (odczyt, odzyskiwanie i koniec przebiegu
    # w toku przez `commands.record_finished`, którego gałąź dawnej rodziny kończy tylko takie przebiegi).
    "outside.started": _historical_event(Event("outside-start", _trace(
        writer=("ticket", "worktree"), neutral=("command", "exitFile", "head", "log"),
        extra={"unit": OUTSIDE_UNIT_OWN,
               "rerunOf": Field("neutral", note="ponowienie padniętych modułów (prośba kroku sprzed PT-479)",
                                source="historical")}))),
    "outside.finished": Event("outside-finish", _trace(
        writer=("ticket",), neutral=("command", "exit", "tail"), extra={"unit": OUTSIDE_UNIT_INHERITED})),
    "outside.ticket-consent": Event("outside-trace", _trace(writer=("ticket",), neutral=("baseVersion", "orderSeq"))),
    # K-109 PT-481: nowa rodzina przebiegu (zamiar → start → wynik), czytana przez widok `commandruns` razem
    # z `outside.started`/`outside.finished`. Zamiar jest śladem procesu bez startu jednostki; od PT-485 zapisuje
    # go `_start_outside` przed `run_command` (jednostka i pliki próby, rezerwacja budżetu: `heavy`, `exclusive`,
    # `project`).
    commandruns.INTENT: Event("outside-trace", _trace(
        writer=("ticket", "worktree"),
        neutral=("command", "exitFile", "head", "log", "rerunOf", "unit",
                 *(name for name in COMMAND_NEUTRAL if name not in ("loadavg", "cores", *COMMAND_LATER)),
                 *commandruns.INTENT_BUDGET)),
        reason="zamiar przed startem polecenia (protokół jednego wykonania, ADR-1 pkt 1–2)"),
    # K-109 PT-479: start i wynik zapisuje prośba `outside` kroku (`_start_outside`, `commands.outside_ready`);
    # pola odzyskiwania zamiaru zapisuje `commands.outside_recover` (PT-485): `recovered` w starcie, `reason`
    # (`not-started`) w końcu. Drugie z pól w drugim zdarzeniu jest tylko rozpoznawane (źródło `reader`).
    commandruns.STARTED: _reader_fields(Event("outside-start", _trace(
        writer=("ticket", "worktree"), neutral=("command", "exitFile", "head", "log", "rerunOf", *COMMAND_NEUTRAL),
        extra={"unit": OUTSIDE_UNIT_OWN}), reason="przebieg polecenia (protokół jednego wykonania, ADR-1)"),
        ("reason",)),
    commandruns.FINISHED: _reader_fields(Event("outside-finish", _trace(
        writer=("ticket",), neutral=("command", "exit", "exitFile", "tail", *COMMAND_NEUTRAL),
        extra={"unit": replace(OUTSIDE_UNIT_INHERITED, source="writer")}),
        reason="przebieg polecenia (protokół jednego wykonania, ADR-1)"), ("recovered",)),
    "intent": Event("action"),
    "result": Event("action"),
    # --- metadane karty: zdarzenia bez roli dowodowej ------------------------------------
    "card.inputs": _metadata("obrazy wejściowe karty", "images"),
    "card.paused": _metadata("wstrzymanie przepływu przez użytkownika"),
    "card.resumed": _metadata("wznowienie przepływu przez użytkownika"),
    "card.finished": _metadata("zakończenie karty", "column", "operationId"),
    "card.closed": _metadata("podsumowanie domknięcia", "baseCommit", "baseVersion", "check", "step", "summary"),
    "card.main-merged": _metadata("dawny zapis scalenia do głównej", source="historical"),
    "card.moved": _metadata("przeniesienie karty na Tablicy", "from", "moveId", "to"),
    "card.move-ignored": _metadata("zbędny ruch okna po zdjęciu karty z Backlogu przy starcie", "moveId"),
    "card.stalled": _metadata("diagnoza zatrzymania przepływu", "reason"),
    "card.base-requested": _metadata("decyzja o bazie karty", "baseProposal", "decision"),
    "card.base-kept": _metadata("zachowanie bazy karty", "decision"),
    "card.base-changed": _metadata("aktywacja nowej bazy karty", "base", "baseVersion", "decisionStep",
                                   "generation", "operationId", "previousBaseVersion", "validationRequired"),
    "card.merged": _dynamic("wynik scalenia karty do głównej (metadane Gita)", "branch", "commit"),
    "plan.checked": _metadata("walidacja propozycji planu", "errors", "step", "valid"),
    "plan.import-prepared": _metadata("trwałe przygotowanie importu planu przed card.started", "cards",
                                      "needsCompletion", "path", "rows", "source", "specification"),
    "plan.imported": _metadata("import planu", "needsCompletion", "path", "rows", "snapshotPath"),
    "plan.proposed": _metadata("propozycja planu", "appendRequest", "appendRevision", "artifactPath", "box",
                               "coordinator", "planPath", "planner", "prefix", "projects", "reviewAuthor", "rows",
                               "session", "sourceKind", "sourceStep", "specification", "title"),
    "order.reserved": _metadata("rezerwacja zlecenia startu ze sprawy; proces startuje osobnym card.started",
                                "base", "fingerprint", "generation", "orderId"),
    "order.dispatching": _metadata("próba przekazania zlecenia startu", "attemptId", "orderId"),
    "order.delivery-error": _metadata("błąd po przyjęciu zlecenia startu", "attemptId", "error", "orderId"),
    "order.failed": _metadata("odrzucenie zlecenia startu przed card.started", "attemptId", "error",
                              "generation", "orderId"),
    "plan.reviewer-selected": _metadata("wybór recenzenta planu", "executor"),
    "plan.approval_requested": _metadata("prośba o zatwierdzenie planu", "proposal"),
    "plan.returned": _metadata("odesłanie planu", "attempt", "automatic", "baseline", "caretaker", "caretakerFrom",
                               "caretakerStep", "feedback", "userFeedback"),
    "plan.revision-discarded": _metadata("odrzucenie poprawki planu"),
    "plan.revision-failed": _metadata("błąd poprawki planu", "baseline", "errors", "failed", "feedback", "pending"),
    "plan.revision-ready": _metadata("poprawka planu gotowa", "baseline", "difference", "feedback", "pending"),
    "plan.revision-approved": _metadata("zatwierdzenie poprawki planu", "difference", "plan"),
    "plan.reviewed": _dynamic("raport review planu", "corrected", "findings", "reviewer", "verdict"),
    "plan.ticket-edited": _metadata("edycja kartki", "changes", "ticket"),
    "plan.approved": _metadata("zatwierdzenie planu", "map", "planFile", "rows"),
    "plan.finished": _metadata("statystyka planu", "merged", "rejected"),
    "ticket.assigned": _metadata("przydział wykonawcy", "executor", "executorEffort", "executorRouteKind",
                                 "executorRouteTokenFile", "executorRouteUrl", "model", "preferredProvider",
                                 "program", "provider", "reviewer", "reviewerEffort", "reviewerModel",
                                 "reviewerProgram", "reviewerProvider", "reviewerRouteKind",
                                 "reviewerRouteTokenFile", "reviewerRouteUrl", "ticket"),
    "ticket.rejected": _metadata("odrzucenie ticketu", "ticket"),
    "ticket.imported": _metadata("import stanu ticketu", "state", "ticket"),
    "reviewer.reassigned": _metadata("zmiana recenzenta", "reason", "reviewer", "reviewerEffort", "reviewerModel",
                                     "reviewerProgram", "reviewerProvider", "reviewerRouteKind",
                                     "reviewerRouteTokenFile", "reviewerRouteUrl", "sourceStep", "ticket"),
    "author.reassigned": _metadata("zmiana autora", "executor", "ticket"),
    "review.verdict": _dynamic("werdykt review (ładunek wyniku recenzenta)", "baseVersion", "reviewTip", "reviewer",
                               "round", "step", "ticket"),
    "review.question": _metadata("pytanie dotyczące review", "policy", "question", "reason", "round", "ticket"),
    "review.decision": _metadata("decyzja dotycząca review", "auto", "automatic", "choice", "round", "ticket"),
    "review.reviewer-note": _metadata("notka recenzenta", "author", "reviewer", "round", "ticket"),
    "step.waiting": _metadata("oczekiwanie przed startem (brak procesu)", "cause", "executor", "kind", "provider", "reason",
                              "ticket", "until"),
    "step.retry": _metadata("prośba o ponowienie (proces startuje osobnym step.started)", "caretaker",
                            "caretakerStep", "instruction", "reason", "step", "ticket", "user"),
    "step.transfer_requested": _metadata("prośba o przekazanie do Konsoli", "baseTip", "executor", "step", "ticket"),
    "step.wip": _metadata("migawka WIP", "step", "ticket", "wip"),
    "step.limit": _metadata("limit dostawcy", "baseTip", "provider", "remaining", "reset", "resetTs", "step",
                            "ticket", "window"),
    "step.notice": _metadata("komunikat kroku", "result", "step", "ticket"),
    "step.activity": _metadata("aktywność kroku", "step", "ticket"),
    "step.reported": _metadata("zgłoszenie usterki", "action", "fault", "project", "reported", "signature", "step",
                               "ticket"),
    "step.cleanup": _metadata("sprzątnięcie po kroku", "step", "ticket"),
    "step.base-wait": _metadata("krok czeka na decyzję o bazie", "step"),
    "step.note-added": _metadata("notka użytkownika do kroku", "noteId", "orderSeq", "requestId", "step", "text"),
    "flow.error": _metadata("błąd przepływu", "at", "error", "stage", "step", "ticket"),
    "flow.notice": _metadata("komunikat przepływu", "key", "result"),
    "flow.error-persisted": _metadata("utrwalony błąd przepływu", "error", "since", "stage", "step", "ticket"),
    "flow.error-cleared": _metadata("wyczyszczony błąd przepływu", "caretaker", "caretakerStep", "error", "stage"),
    "permission.rechecked": _metadata("ponowna kontrola uprawnień", "canAllow", "step", "ticket"),
    "refs.guard": _dynamic("kontrola refów po kroku (migawka Gita)", "step", "ticket"),
    "box.wait": _dynamic("stan oczekiwania na pudełko", "value"),
    "box.rebuild-requested": _metadata("prośba o przebudowę pudełka", "domain", "project", "step"),
    "box.ports": _metadata("rezerwacja portów przed zamiarem startu; proces zapisuje step-start",
                           "box", "boxSlug", "owner", "step"),
    "box.risk": _metadata("wpis ryzyka K1", "box", "program", "risk", "step", "text"),
    "packs.removed": _metadata("sprzątnięte paczki", "removed"),
    "k38.handoff": _metadata("przekazanie K-38 (inwentaryzacja)", "excluded", "instruction", "preserved", "step",
                             "ticket", "version"),
    "k38.inventory": _metadata("inwentaryzacja K-38", "instruction", "items", "version"),
    "k38.public-inventory": _metadata("inwentaryzacja publicznych prób", "items", "version"),
    "k38.diagnostic-requested": _metadata("rejestracja diagnostyki PT-273 (bramka hostowa)", "binding", "step"),
    "k38.diagnostic-consumed": _metadata("konsumpcja zgody diagnostyki", "step"),
    "youtrack.reset": _metadata("reset błędu YouTracka", "action", "allowWrite", "key", "rejectedResult"),
    "youtrack.retry": _metadata("ponowienie YouTracka", "action", "after", "error", "key"),
    "base-change.checkpoint": _dynamic("punkt kontrolny korekty bazy", "baseVersion", "generation", "key", "newBase",
                                       "oldBase", "previousBaseVersion", "stage"),
    "base-change.waiting": _metadata("oczekiwanie korekty bazy", "generation", "key", "newBase", "oldBase",
                                     "reason"),
    "main-merge.checkpoint": _dynamic("punkt kontrolny scalenia do głównej", "key", "stage"),
    "main-merge.waiting": _metadata("oczekiwanie scalenia do głównej", "key", "reason"),
    "merge.check-armed": _metadata("uzbrojenie bramki testów po scaleniu (PT-318); nie jest startem procesu"),
    "merge.check-requested": _metadata("próba testów po scaleniu ticketu (PT-318); nie jest startem procesu",
                                       "after", "before", "check", "command", "fallback", "mergeSeq", "reason",
                                       "retryOf", "source", "start", "ticket", "unavailable"),
    # Zapis: przebieg pod zgodą (PT-329), razem z `outside.finished` pseudokroku próby.
    "merge.check-finished": _metadata("wynik próby testów po scaleniu (zapis: przebieg pod zgodą, PT-329)",
                                      "check", "exit", "log", "ticket"),
    "merge.check-accepted": _metadata("akceptacja próby testów po scaleniu mimo porażki (PT-320); nie uruchamia "
                                      "polecenia", "after", "by", "check", "ticket"),
    "main-merge.resumed": _metadata("wznowienie scalenia do głównej", "key"),
    "main-merge.abandoned": _metadata("porzucenie scalenia do głównej", "key", "reason"),
    "stage.retry": _metadata("ponowienie etapu przez opiekuna", "caretaker", "caretakerStep", "problem", "stage",
                             "ticket"),
    "outside.ticket-consent-revoked": _dynamic("cofnięcie zgody outside; nie jest startem procesu"),
    "outside.decision-invalidated": _metadata("unieważnienie decyzji outside; nie jest startem procesu",
                                              "decisionSeq", "reason", "step", "ticket"),
    "outside.auto-skipped": _metadata("pominięta automatyczna zgoda outside (odmowa człowieka, pauza); "
                                      "nie jest zgodą ani startem procesu", "command", "reason", "step", "ticket"),
    "caretaker.started": _metadata("problem opiekuna; proces opiekuna zapisuje step.started", "decision", "key",
                                   "n", "problem", "root", "step", "target", "ticket", "trigger", "triggers",
                                   "watermark"),
    "caretaker.guard": _dynamic("migawka strażnika opiekuna", "branches", "cause", "refs", "repo", "skipped",
                                "step", "worktrees", "wrote"),
    "caretaker.escalated": _metadata("eskalacja opiekuna", "action", "code", "count", "error", "failed", "limit",
                                     "mode", "optionId", "problem", "recommendation", "step", "summary", "target",
                                     "ticket"),
    "caretaker.action": _metadata("akcja opiekuna", "action", "code", "detail", "error", "label", "mode", "ok",
                                  "params", "problem", "retried", "stale", "step", "target", "ticket"),
    "caretaker.decision": _metadata("decyzja o problemie opiekuna", "choice", "problem"),
    "caretaker.reported": _metadata("zgłoszenie opiekuna", "code", "error", "ok", "problem", "reason", "reported",
                                    "signature", "step", "target"),
    "caretaker.chat": _metadata("rozmowa z opiekunem; proces zapisuje step.started", "decision", "mode",
                                "problem", "root", "step", "target", "ticket", "trigger", "watermark"),
    "caretaker.chat-action": _dynamic("akcja z rozmowy opiekuna (kopia przebiegu)", "chat", "file", "outcome",
                                      "step"),
    "caretaker.chat-file": _metadata("plik rozmowy opiekuna", "chat", "signature", "step"),
})

ACTION_START = MappingProxyType({
    "key": NAME_KEY, "action": Field("discriminator", "str", note="step-start"), **START_IDENTITY,
    "attempt": ATTEMPT, **_neutral(*(n for n in START_NEUTRAL if n not in ("gitDir", "readDirsProfile", "repo", "toolingKind",
                                                       "closeFile", "consoleClosed", "consoleOpened", "enteredAt",
                                                       "mode", "taskFile"))),
})
ACTION_RESULT = MappingProxyType({
    "key": NAME_KEY, "action": Field("discriminator", "str", note="step-start"),
    "runner": RUNNER, "box": BOX, "boxSlug": BOX_SLUG, "unit": STEP_UNIT,
    **{name: _historical(spec) for name, spec in START_IDENTITY.items()
       if name not in ("runner", "box", "boxSlug", "unit")},
    **_neutral("boxAccess", "boxIp", "boxPortOwner", "error", "gitDir", "ok", "readDirsProfile"),
})


@dataclass(frozen=True)
class Action:
    role: str
    intent: object = None
    result: object = None
    reason: str = ""
    source: str = "writer"
    dynamic: frozenset = frozenset()   # strony (intent/result) z dodatkowym ładunkiem dynamicznym


def _meta_action(reason, intent=(), result=(), dynamic=(), source="writer"):
    """Akcja metadanych: reduktor jej nie czyta, ale pola jawne zamiaru/wyniku są sklasyfikowane."""
    def table(names):
        return MappingProxyType(_neutral("action", "key", *names, note=reason, source=source))
    return Action("metadata", table(intent), table(result), reason, source, frozenset(dynamic))


GIT_RESULT = ("base", "branch", "error", "existing", "ok", "ticket", "worktree")
# Wynik YouTracka: pola stałe i znane klucze ładunku `data` (zgłoszenie, tytuł, mapa numerów) obok zakresu
# dynamicznego odpowiedzi API (M5 review 2: klucze przypisań zapisywane obok zakresu dynamicznego).
TRACKER_RESULT = ("error", "map", "ok", "permanent", "retryWriteSafe", "ticket", "title")

ACTIONS = MappingProxyType({
    "step-start": Action("step-start", ACTION_START, ACTION_RESULT, "zamiar i wynik startu procesu kroku"),
    "reserve": _meta_action("rezerwacja numeru ticketu", ("minimumNumber", "payload", "prefix", "repo", "title",
                                                         "youtrack"),
                            TRACKER_RESULT, ("result",)),
    "card-branch": _meta_action("gałąź karty (Git)", ("base", "baseBranch", "baseRef", "branch", "repo", "worktree"),
                                GIT_RESULT),
    "ticket-branch": _meta_action("gałąź ticketu (Git)", ("base", "branch", "repo", "ticket", "worktree"), GIT_RESULT),
    "review-branch": _meta_action("gałąź review (Git)", (), GIT_RESULT),
    "step-reopen": _meta_action("ponowne otwarcie worktree kroku (Git)", ("base", "branch", "repo", "worktree"),
                                GIT_RESULT),
    "plan-commit": _meta_action("commit planu (Git)", ("branch", "repo", "worktree"),
                                 ("already", "commit", "error", "ok", "skipped"), ("result",)),
    "cleanup": _meta_action("usunięcie worktree (Git)", ("branch", "repo", "worktree"),
                            ("already", "error", "ok", "preserved", "reason", "removed")),
    "merge": _meta_action("scalenie ticketu do karty (Git)", ("branch", "cardTip", "expectedTip", "into"),
                          ("commit", "error", "ok", "resolved"), ("result",)),
    "main-merge": _meta_action("scalenie karty do głównej (Git)", ("baseVersion", "operationId", "previewToken", "repo",
                                                                  "requestSeq", "sourceWorktree"),
                               ("commit", "ok"), ("intent", "result")),
    "change-base": _meta_action("korekta bazy karty", ("baseVersion", "beforeSeq", "decisionStep", "generation",
                                                      "lockReason", "newBase", "oldBase", "operationId", "previewId",
                                                      "previousBaseVersion", "repo", "requestSeq"),
                                ("baseVersion", "generation", "ok", "operationId")),
    "caretaker-action": _meta_action("akcja opiekuna na planie/kroku; proces opiekuna ma step.started",
                                     ("caretakerAction", "params", "problem"), ("error", "ok"), ("result",)),
    "youtrack-create": _meta_action("dawne założenie zgłoszenia YouTracka", source="historical"),
    "youtrack-link": _meta_action("powiązanie zgłoszeń YouTracka", ("payload", "youtrack"), TRACKER_RESULT,
                                  ("result",)),
    "youtrack-note": _meta_action("komentarz YouTracka", ("payload", "youtrack"), TRACKER_RESULT, ("result",)),
})

# Koperta każdego zdarzenia dziennika pudełka: pola kanoniczne, bez przycinania (M1 review 1).
BOX_TS = Field("identity", "str", True, note="znacznik koperty; kanoniczny niepusty tekst")
BOX_INSTANCE = Field("identity", "str", True, domain="resource", rule="box",
                     note="instancja pudełka projektu dziennika: box_name(slug) albo card_box_name(slug, karta)")
BOX_STEP = Field("name", "str", True, domain="step",
                 note="krok w pudełku; źródło ochrony; niekanoniczna nazwa nie jest przycinana ani pomijana")
BOX_TRACE = MappingProxyType({"step": BOX_STEP, "box": BOX_INSTANCE, "ts": BOX_TS})
BOX_ENVELOPE_FIELDS = MappingProxyType({"box": BOX_INSTANCE, "ts": BOX_TS})

BOX_EVENTS = MappingProxyType({
    "box.use": Event("box-trace", BOX_TRACE, "wejście kroku do pudełka"),
    "box.release": Event("box-trace", BOX_TRACE, "wyjście kroku z pudełka"),
    "box.build": _metadata("budowa pudełka", "duration", "error", "hash", "phase", "result", "steps"),
    "box.ready": _metadata("pudełko gotowe", "card", "copy_of", "duration", "failed_images", "hash", "reason",
                           "warning"),
    "box.verify": _metadata("weryfikacja pudełka", "duration", "failed", "result", "sourceCommit", "source_commit",
                            "source_revision"),
    "box.reset": _metadata("reset pudełka", "duration", "error", "reason", "removed", "result"),
    "box.stop": _metadata("zatrzymanie pudełka", "duration", "reason"),
    "box.destroy": _metadata("usunięcie pudełka"),
    "box.approved": _metadata("zatwierdzenie przepisu pudełka", "accepted_red", "card", "items", "path", "recipe",
                              "source"),
})

CARD_ENVELOPE = frozenset({"v", "seq", "ts", "card", "type"})
BOX_ENVELOPE = frozenset({"v", "ts", "type"})

PROCESS_ROLES = frozenset({"step-start", "step-trace", "console-enter", "console-ack", "outside-start",
                           "outside-finish", "outside-trace", "action"})


# ------------------------------------------------------------------ pochodne modelu

def _model_names():
    process = frozenset(k for k, e in EVENTS.items() if e.role in PROCESS_ROLES)
    metadata = frozenset(k for k, e in EVENTS.items() if e.role in ("metadata", "context"))
    actions = frozenset(k for k, a in ACTIONS.items() if a.role == "metadata")
    return process, metadata, actions


PROCESS_EVENTS, NON_PROCESS_EVENTS, NON_PROCESS_ACTIONS = _model_names()
START_FIELDS = tuple(k for k, f in EVENTS["step.started"].fields.items()
                     if f.role in ("identity", "conditional"))
IDENTITY_FIELDS = tuple(TRACE_IDENTITY)
RESOURCE_FIELDS = tuple(k for k, f in START_IDENTITY.items() if f.domain == "resource")
CONSOLE_FIELDS = tuple(k for k, f in EVENTS["step.entered"].fields.items() if f.role == "own")
CONSOLE_EVENTS = tuple(k for k, e in EVENTS.items() if e.role in ("console-enter", "console-ack"))


def event_fields(event):
    """Specyfikacja pól zdarzenia procesu albo None (metadane, nieznany typ/akcja)."""
    kind = event.get("type")
    if not isinstance(kind, str) or kind not in EVENTS:
        return None
    spec = EVENTS[kind]
    if spec.role == "action":
        action = ACTIONS.get(event.get("action")) if isinstance(event.get("action"), str) else None
        if action is None or action.role != "step-start":
            return None
        return action.intent if kind == "intent" else action.result
    return spec.fields if spec.role in PROCESS_ROLES else None


def canonical_text(value, nonempty):
    """Tekst bez otaczających znaków białych; przycięcie nie naprawia identyfikatora."""
    return isinstance(value, str) and value == value.strip() and (bool(value) or not nonempty)


# ------------------------------------------------------------------ stan tożsamości

@dataclass
class ProcessIdentity:
    name: str
    state: str = "ok"
    protected: bool = False
    data: dict = field(default_factory=dict)
    outside: list = field(default_factory=list)
    has_start: bool = False
    projects: set = field(default_factory=set)
    # Dowody poprzedników są związane z domeną (projekt, zlecenie, próba), w której powstały:
    # dziedziczy wyłącznie zdarzenie z tej samej domeny (M2 review 1).
    outside_units: dict = field(default_factory=dict)   # domena → jednostka outside.started
    outside_runs: dict = field(default_factory=dict)    # domena → {"started": zdarzenie, "finished": …}
    consoles: dict = field(default_factory=dict)        # domena → komplet Konsoli tego kroku
    attempt: object = 0                                # numer bieżącej próby
    # Generacja rośnie przy każdej zmianie próby, także przy powrocie do starego numeru:
    # domena nie wraca do zachowanych dowodów starszej próby (M3 review 2).
    generation: int = 0
    attempts: set = field(default_factory=set)         # numery prób już rozpoczętych
    start_domains: set = field(default_factory=set)    # pełne domeny startów kroku
    current_domain: tuple | None = None               # domena bieżącego śladu

    @property
    def outside_unit(self):
        """Jednostka outside ostatniej domeny (diagnostyka; reduktor czyta outside_units)."""
        return next(reversed(self.outside_units.values()), None)

    def _outside_roles(self):
        return [EVENTS[e["type"]].role if e.get("type") in EVENTS else "" for e in self.outside]

    @property
    def has_outside_start(self):
        """Jakikolwiek ślad startu outside tego procesu (także bez kompletu dowodu)."""
        return "outside-start" in self._outside_roles()

    @property
    def has_outside_finish(self):
        return "outside-finish" in self._outside_roles()

    @property
    def has_outside_request(self):
        """Prośba kroku o polecenie outside (`step.finished.outside`)."""
        return any(e.get("type") == "step.finished" and e.get("outside") for e in self.outside)

    @property
    def current_outside(self):
        """Uruchomienie outside bieżącej domeny procesu: {"started", "finished"} albo None.

        Konsumenci (gotowość, zajętość, plik wyjścia) czytają wyłącznie ten wynik: start
        i zakończenie innej próby lub domeny nie są uruchomieniem bieżącego procesu (M4 review 2)."""
        return self.outside_runs.get(self.current_domain)

    @property
    def console(self):
        return next(reversed(self.consoles.values()), None)

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


def valid_project(value):
    """Projekt musi być dokładnym slugiem; nie naprawiamy kontekstu historii."""
    try:
        bp._check_slug(value, "projekt")
        return value == value.strip()
    except (ValueError, TypeError):
        return False


def start_error(event):
    """Struktura startu według modelu; opcjonalne pola nie kasują wcześniejszej wiedzy."""
    name = "key" if event.get("type") in ("intent", "result") else "step"
    if not isinstance(event.get(name), str) or not event[name].strip():
        return "niepełna tożsamość procesu w dzienniku karty"
    if any(key in event and not isinstance(event[key], str) for key in START_FIELDS):
        return "nieznana tożsamość procesu w dzienniku karty"
    kind = event.get("kind")
    if "kind" in event and (not isinstance(kind, str) or
                            kind not in CARD_STEP_KINDS | TICKET_STEP_KINDS | MERGE_CHECK_KINDS):
        return "nieznany rodzaj kroku w dzienniku karty"
    if not (event.get("ticket") or "").strip() and kind not in CARD_STEP_KINDS:
        return "brak ticketu procesu w dzienniku karty"
    if event.get("ticket", "") != "" and not valid_ticket(event["ticket"]):
        return "niekanoniczny ticket procesu w dzienniku karty"
    return ""


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


def outside_evidence(event):
    """Ślad osobnego procesu outside według dyskryminatorów modelu."""
    kind = event.get("type")
    role = EVENTS[kind].role if isinstance(kind, str) and kind in EVENTS else ""
    if role in ("outside-start", "outside-finish", "outside-trace"):
        return True
    if kind == "step.finished":
        return bool(event.get("outside"))
    if kind == "step.decision":
        return isinstance(event.get("choice"), str) and event["choice"] in OUTSIDE_CHOICES
    # Nieznany typ rodziny przebiegu (`outside.*`, `command.*`) nie jest metadanymi: zachowujemy go jako ślad outside.
    return bool(commandruns.family(kind)) and kind not in EVENTS


def evidence_errors(event, fields, identity, domain=None):
    """Błędy dowodu według ról pól; (unknown?, inconsistent?) bez modyfikacji stanu.

    `domain` — (projekt, zlecenie, próba) zdarzenia; poprzednik musi pochodzić z tej samej domeny."""
    unknown = inconsistent = False
    for name, spec in fields.items():
        present = name in event
        value = event.get(name)
        if spec.role == "discriminator":
            if present and spec.type == "bool" and not isinstance(value, bool):
                unknown = True
            elif present and spec.type == "str" and not isinstance(value, str):
                unknown = True
            continue
        if spec.role == "own":
            if not canonical_text(value, True):
                unknown = True
            continue
        if spec.role == "generation":
            if present and (type(value) is not int or value < 0):
                unknown = True
            continue
        if spec.role == "inherited":
            previous = _predecessor_value(identity, spec, name, domain)
            if present:
                if not canonical_text(value, True):
                    unknown = True
                elif previous is not None and previous != value:
                    inconsistent = True
            if previous is None:
                # Brak kompletnego poprzednika tego samego procesu; także jawna wartość
                # zdarzenia zależnego go nie zastępuje.
                unknown = True
            continue
        if spec.role in ("identity", "conditional") and present and spec.type == "str":
            if not isinstance(value, str):
                unknown = True
            elif spec.rule == "ticket":
                if value != "" and not valid_ticket(value):
                    unknown = True
            elif not canonical_text(value, spec.nonempty):
                unknown = True
    return unknown, inconsistent


def _predecessor_value(identity, spec, name, domain):
    if spec.predecessor == "step.entered|step.started":
        return (identity.consoles.get(domain) or {}).get(name)
    if spec.predecessor == "outside-start":
        return identity.outside_units.get(domain)
    return None


def process_identities(events):
    """step → ok/unknown/inconsistent, z monotoniczną ochroną każdego śladu.

    Niepełny wpis pozostaje unknown nawet po późniejszym uzupełnieniu. Ślad procesu
    wymaga wcześniejszego startu/zamiaru kroku; potwierdzenie Konsoli — wcześniejszego
    kompletnego wejścia tego kroku; zakończenie outside — wcześniejszego startu jego
    osobnego procesu. Brak nazwy ma osobną tożsamość unknown, zatem nie znika przez
    filtrowanie ani przez kolizję z poprawnym krokiem.
    """
    identities = {}
    project = ""
    card = ""
    order = 0   # numer zlecenia (kolejne card.started)
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            identities[None, index] = ProcessIdentity("", state="unknown")
            continue
        kind = event.get("type")
        if kind == "card.started":
            value = event.get("project")
            project = value if valid_project(value) else ""
            card = event.get("card", "")
            order += 1
        uncertain_type = not isinstance(kind, str) or kind not in EVENTS
        action = event.get("action")
        uncertain_action = kind in ("intent", "result") and (
            not isinstance(action, str) or action not in ACTIONS)
        role = "" if uncertain_type else EVENTS[kind].role
        fields = None if uncertain_type else event_fields(event)
        start = uncertain_type or role == "step-start" or (
            kind == "intent" and (action == "step-start" or uncertain_action))
        result = kind == "result" and (action == "step-start" or uncertain_action)
        outside = outside_evidence(event)
        if not (start or result or outside or role in PROCESS_ROLES - {"action"}):
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
        if "ticket" in event and event["ticket"] != "" and not valid_ticket(event["ticket"]):
            identity.worsen("unknown")
        if not valid_name or name != name.strip():
            # Niekanoniczna nazwa nie jest przycinana do nazwy innego procesu.
            identity.worsen("unknown")
        # Role pól z modelu: własne, dziedziczone, kanoniczne pola tożsamości i dyskryminatory.
        # Nieznany typ/akcja ma fields=None i pozostaje unknown bez dalszego rozstrzygania.
        if start:
            attempt = event.get("attempt", identity.attempt)
            if type(attempt) is int and attempt >= 0 and attempt != identity.attempt:
                # Inna próba tej samej nazwy to nowa generacja: dowody poprzedników starszej
                # generacji zostają w śladzie (ochrona, stan), ale nie są dziedziczone.
                # Powrót do już rozpoczętej próby nie przywraca jej dowodów: dostaje nową
                # generację, a sam powrót jest nieznanym dowodem (M3 review 2).
                if attempt in identity.attempts:
                    identity.worsen("unknown")
                identity.attempt = attempt
                identity.generation += 1
            identity.attempts.add(identity.attempt)
        # Dziedziczone pola (Konsola, jednostka outside) czytają poprzednika wyłącznie z tej
        # samej domeny: dowód innego projektu, zlecenia lub próby nie jest poprzednikiem (M2 review 1).
        # Start kroku jest poprzednikiem tylko w pełnej domenie, w której zapisano start;
        # wyjątkiem jest legalne historyczne zakończenie (`Event.start == "process"`).
        domain = (project, order, identity.generation)
        identity.current_domain = domain
        unknown, inconsistent = evidence_errors(event, fields or {}, identity, domain)
        if unknown:
            identity.worsen("unknown")
        if inconsistent:
            identity.worsen("inconsistent")
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
            identity.start_domains.add(domain)
            if not unknown and all(canonical_text(event.get(k), True) for k in CONSOLE_FIELDS):
                # Start rozmowy opiekuna zapisuje własny komplet Konsoli tego kroku.
                identity.consoles[domain] = {k: event[k] for k in CONSOLE_FIELDS}
            # Dane operacyjne nie są źródłem klasyfikacji ochrony.
            for field_name, value in event.items():
                if field_name not in START_FIELDS:
                    identity.data[field_name] = value
            identity.data["step"] = identity.name
            continue
        scope = EVENTS[kind].start if kind in EVENTS and kind not in ("intent", "result") else "domain"
        started_here = domain in identity.start_domains or scope == "process"
        if not identity.has_start or not started_here:
            # Start innego projektu, zlecenia lub próby nie jest poprzednikiem tego śladu.
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
        if role == "console-enter" and not unknown and identity.has_start and started_here:
            # Tylko kompletne wejście tego kroku jest poprzednikiem otwarcia/zamknięcia.
            identity.consoles[domain] = {k: event[k] for k in CONSOLE_FIELDS}
        if outside:
            if role == "outside-start":
                # Outside uruchamia osobny proces: jego jednostka musi być
                # zapisana w tym starcie, nie odziedziczona z kroku.
                unit = event.get("unit")
                if canonical_text(unit, True):
                    previous = identity.outside_units.get(domain)
                    if previous is not None and previous != unit:
                        identity.worsen("inconsistent")
                    else:
                        identity.outside_units[domain] = unit
                        identity.outside_runs.setdefault(domain, {"started": dict(event), "finished": None})
            elif kind == commandruns.INTENT and not unknown:
                # K-109 PT-485: zamiar ustala jednostkę próby przed startem — start musi ją powtórzyć, a koniec
                # zamiaru rozstrzygniętego bez startu (`reason: not-started`) dziedziczy ją z zamiaru. Uruchomienia
                # (`outside_runs`) zamiar nie otwiera.
                unit = event.get("unit")
                if canonical_text(unit, True):
                    previous = identity.outside_units.get(domain)
                    if previous is not None and previous != unit:
                        identity.worsen("inconsistent")
                    else:
                        identity.outside_units[domain] = unit
            elif role == "outside-finish":
                # Zakończenie należy do uruchomienia tej samej domeny; pierwsze zakończenie wygrywa.
                run = identity.outside_runs.get(domain)
                if run is not None and run["finished"] is None:
                    run["finished"] = dict(event)
            identity.outside.append(dict(event))
    return identities


def outside_run(events, name):
    """Wspólny wynik redukcji dla konsumentów outside: (stan procesu, start, zakończenie)
    bieżącego uruchomienia outside procesu `name`. Brak procesu albo uruchomienia: (stan, None, None).
    Stan `ok` nie dowodzi zakończenia; o skutku decyduje konsument po własnej barierze."""
    identity = process_identities(events).get(name) if isinstance(name, str) and name else None
    if identity is None:
        return "unknown", None, None
    run = identity.current_outside or {}
    # Ochronę (PT-261) rozstrzyga istniejąca bariera konsumenta (`_trial_check`); tu tylko stan dowodu.
    return identity.state, run.get("started"), run.get("finished")


def outside_runs(events):
    """Procesy z rozpoczętym, niezakończonym outside (zajętość miejsca po restarcie).

    Zachowawczo: niezakończone uruchomienie dowolnej generacji (najpierw bieżącej) zajmuje miejsce,
    a proces wstrzymany (unknown/inconsistent/ochrona) z jakimkolwiek startem outside — także bez
    kompletu dowodu — zajmuje je do potwierdzenia kodu wyjścia."""
    out = {}
    for key, identity in process_identities(events).items():
        if not isinstance(key, str):
            continue
        current = identity.current_outside
        runs = [current, *identity.outside_runs.values()] if current is not None else \
            list(identity.outside_runs.values())
        open_run = next((run for run in runs if run["finished"] is None), None)
        if open_run is not None:
            out[key] = open_run["started"]
        elif identity.held and identity.has_outside_start and not identity.has_outside_finish:
            out[key] = next(e for e in identity.outside if commandruns.phase(e) == "started")
    return out


_CARD_COPY = re.compile(r"-(k-[1-9][0-9]{0,8})$")
_BOX_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def box_of_project(box, slug):
    """Instancja należy do projektu dziennika: pudełko projektu albo kopia karty (bez naprawiania nazwy)."""
    if not isinstance(box, str) or not _BOX_NAME.match(box):
        return False
    if slug is None:
        return True
    try:
        if box == bs.box_name(slug):
            return True
        copy = _CARD_COPY.search(box)
        return bool(copy) and box == bs.card_box_name(slug, copy.group(1))
    except (ValueError, TypeError):
        return False


def box_event_error(event, slug=None):
    """Walidacja zdarzenia dziennika pudełka według BOX_EVENTS (koperta i pola śladu).

    Pola wymagane muszą być kanoniczne (bez przycinania); instancja musi należeć do projektu
    `slug` dziennika. Wadliwe pole nie jest pomijane przy filtrowaniu nazw prób (M1 review 1)."""
    if (not isinstance(event, dict) or type(event.get("v")) is not int
            or event["v"] != bs.SCHEMA or not isinstance(event.get("type"), str)
            or event["type"] not in BOX_EVENTS):
        return "nieznany format zdarzenia pudełka"
    fields = {**BOX_ENVELOPE_FIELDS, **(BOX_EVENTS[event["type"]].fields or {})}
    for name, spec in fields.items():
        if spec.role == "neutral":
            continue
        if not canonical_text(event.get(name), spec.nonempty):
            return "niepełna tożsamość zdarzenia pudełka"
        if spec.rule == "box" and not box_of_project(event[name], slug):
            return "nieznana instancja w dzienniku pudełka"
    return ""
