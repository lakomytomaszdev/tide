"""Czysty model tożsamości procesów z całej historii, bez I/O i stanu usługi.

Kolejne zdarzenie może dodać wiedzę, ochronę lub błąd, nigdy ich usunąć.
Stan tożsamości nie potwierdza końca procesu: to osobny dowód hostowy.

PT-288: jedna deklaratywna tabela (`EVENTS`, `ACTIONS`, `BOX_EVENTS`) opisuje każde
rozpoznane zdarzenie dziennika karty i pudełka: jego rolę dowodową albo uzasadnienie
braku tej roli, a dla zdarzeń procesu — rolę, typ, niepustość, poprzednika, domenę
tożsamości i regułę zgodności każdego pola. Reduktor i walidatory (dziennik karty,
dziennik pudełka) czytają wyłącznie tę tabelę; konsumenci nie mają własnych reguł pól.
"""
from dataclasses import dataclass, field
from types import MappingProxyType

from .. import trialpolicy
from ..box import profile as bp, state as bs
from ..coordination.numbering import ticket_key

CARD_STEP_KINDS = frozenset({"planning", "plan-fix", "plan-import", "plan-review",
                             "plan-amend", "plan-revise", "close", "interactive",
                             "caretaker", "caretaker-chat"})
TICKET_STEP_KINDS = frozenset({"ticket", "fix", "review", "conflict"})
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
      neutral        — dane operacyjne/metadane bez roli dowodowej (uzasadnienie w `note`).
    type: str | bool | any.  nonempty: wymagany tekst niepusty po strip() i bez otaczających znaków białych.
    domain: step | console | outside | resource | card.  rule: match | ticket | kind | resource | slug.
    source: writer (zapis w kodzie usługi) albo historical (starszy format, sonda lub ślad jawnie wstrzyknięty).
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
    box-trace albo metadata. Zdarzenie metadanych ma uzasadnienie; `fields=None` oznacza
    ładunek dynamiczny, którego reduktor nie czyta."""
    role: str
    fields: object = None
    reason: str = ""
    source: str = "writer"


def _neutral(*names, note="dane operacyjne bez roli dowodowej", source="writer"):
    return {name: Field("neutral", note=note, source=source) for name in names}


def _metadata(reason, *names, source="writer"):
    return Event("metadata", MappingProxyType(_neutral(*names, note=reason, source=source)), reason, source)


def _dynamic(reason, source="writer"):
    return Event("metadata", None, reason, source)


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


START_IDENTITY = {"ticket": TICKET_START, "kind": KIND, "branch": BRANCH, "worktree": WORKTREE,
                  "stepDir": STEP_DIR, "runner": RUNNER, "box": BOX, "boxSlug": BOX_SLUG,
                  "unit": STEP_UNIT}
START_NEUTRAL = ("attempt", "baseCommit", "baseRef", "baseVersion", "boxAccess", "boxIp", "boxPortOwner",
                 "closeFile", "consoleClosed", "consoleOpened", "deliveredNoteIds", "effort", "enteredAt",
                 "executor", "gitDir", "local", "mode", "model", "parentStep", "program", "provider",
                 "readDirsProfile", "repo", "reviewTip", "round", "routeKind", "routeTokenFile", "routeUrl",
                 "session", "taskFile", "toolingKind")
CONSOLE_INHERITED = Field(
    "inherited", "str", True, predecessor="step.entered|step.started", domain="console", rule="match",
    source="historical",
    note="brak dziedziczy z kompletnej Konsoli tego kroku: step.entered albo step.started rozmowy opiekuna "
         "z własnym kompletem (odzyskanie przed wejściem); jawna wartość musi być zgodna")
OUTSIDE_UNIT_OWN = Field("own", "str", True, domain="outside", rule="match",
                         note="jednostka osobnego procesu outside; nie dziedziczy z kroku")
OUTSIDE_UNIT_INHERITED = Field(
    "inherited", "str", True, predecessor="outside.started", domain="outside", rule="match", source="historical",
    note="brak dziedziczy z kompletnego outside.started; jawna wartość musi być zgodna (runda 10)")

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
    }), "początek zlecenia karty; ustala kontekst projektu"),
    "step.started": Event("step-start", MappingProxyType({
        "step": NAME_STEP, **START_IDENTITY, "consoleUnit": CONSOLE_UNIT, "processFile": PROCESS_FILE,
        **_neutral(*START_NEUTRAL),
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
        neutral=("allow", "answer", "auto", "automatic", "baseDecision", "caretaker", "caretakerStep", "command",
                 "commands", "envAnswer", "escalated", "executor", "instead", "refusal", "resetTs", "skipped",
                 "waitCard", "waitTicket"),
        extra={"choice": Field("discriminator", "str", domain="outside",
                               note="run/run-always/run-ticket to zgoda outside; inna wartość to zwykły ślad")})),
    "step.finished": Event("step-trace", _trace(
        writer=("ticket",),
        neutral=("baseProposal", "canAllow", "command", "commands", "exit", "fault", "faultCause", "kept",
                 "messages", "mode", "network", "notes", "options", "outcome", "pattern", "question", "reason",
                 "result", "snapshotDir", "stalledSince", "waitFor", "wip"),
        extra={"outside": Field("discriminator", "bool", domain="outside",
                                note="prośba o polecenie outside tego kroku")})),
    "outside.started": Event("outside-start", _trace(
        writer=("ticket", "worktree"), neutral=("command", "exitFile", "head", "log", "rerunOf"),
        extra={"unit": OUTSIDE_UNIT_OWN})),
    "outside.finished": Event("outside-finish", _trace(
        writer=("ticket",), neutral=("command", "exit", "tail"), extra={"unit": OUTSIDE_UNIT_INHERITED})),
    "outside.ticket-consent": Event("outside-trace", _trace(writer=("ticket",), neutral=("baseVersion", "orderSeq"))),
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
    "card.stalled": _metadata("diagnoza zatrzymania przepływu", "reason"),
    "card.base-requested": _metadata("decyzja o bazie karty", "baseProposal", "decision"),
    "card.base-kept": _metadata("zachowanie bazy karty", "decision"),
    "card.base-changed": _metadata("aktywacja nowej bazy karty", "base", "baseVersion", "decisionStep",
                                   "generation", "operationId", "previousBaseVersion", "validationRequired"),
    "card.merged": _dynamic("wynik scalenia karty do głównej (metadane Gita)"),
    "plan.checked": _metadata("walidacja propozycji planu", "errors", "step", "valid"),
    "plan.imported": _metadata("import planu", "needsCompletion", "path", "rows"),
    "plan.proposed": _metadata("propozycja planu", "appendRequest", "appendRevision", "artifactPath", "box",
                               "coordinator", "planPath", "planner", "prefix", "projects", "reviewAuthor", "rows",
                               "session", "sourceKind", "sourceStep", "specification", "title"),
    "plan.reviewer-selected": _metadata("wybór recenzenta planu", "executor"),
    "plan.approval_requested": _metadata("prośba o zatwierdzenie planu", "proposal"),
    "plan.returned": _metadata("odesłanie planu", "attempt", "automatic", "baseline", "caretaker", "caretakerFrom",
                               "caretakerStep", "feedback", "userFeedback"),
    "plan.revision-discarded": _metadata("odrzucenie poprawki planu"),
    "plan.revision-failed": _metadata("błąd poprawki planu", "baseline", "errors", "failed", "feedback", "pending"),
    "plan.revision-ready": _metadata("poprawka planu gotowa", "baseline", "difference", "feedback", "pending"),
    "plan.revision-approved": _metadata("zatwierdzenie poprawki planu", "difference", "plan"),
    "plan.reviewed": _dynamic("raport review planu"),
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
    "review.verdict": _dynamic("werdykt review (ładunek wyniku recenzenta)"),
    "review.question": _metadata("pytanie po review", "question", "round", "ticket"),
    "review.decision": _metadata("decyzja po review", "auto", "choice", "round", "ticket"),
    "review.reviewer-note": _metadata("notka recenzenta", "author", "reviewer", "round", "ticket"),
    "step.waiting": _metadata("oczekiwanie przed startem (brak procesu)", "cause", "kind", "provider", "reason",
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
    "refs.guard": _dynamic("kontrola refów po kroku (migawka Gita)"),
    "box.wait": _dynamic("stan oczekiwania na pudełko"),
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
    "base-change.checkpoint": _dynamic("punkt kontrolny korekty bazy"),
    "base-change.waiting": _metadata("oczekiwanie korekty bazy", "generation", "key", "newBase", "oldBase",
                                     "reason"),
    "main-merge.checkpoint": _dynamic("punkt kontrolny scalenia do głównej"),
    "main-merge.waiting": _metadata("oczekiwanie scalenia do głównej", "key", "reason"),
    "main-merge.resumed": _metadata("wznowienie scalenia do głównej", "key"),
    "main-merge.abandoned": _metadata("porzucenie scalenia do głównej", "key", "reason"),
    "stage.retry": _metadata("ponowienie etapu przez opiekuna", "caretaker", "caretakerStep", "problem", "stage",
                             "ticket"),
    "outside.ticket-consent-revoked": _dynamic("cofnięcie zgody outside; nie jest startem procesu"),
    "outside.decision-invalidated": _metadata("unieważnienie decyzji outside; nie jest startem procesu",
                                              "decisionSeq", "step", "ticket"),
    "caretaker.started": _metadata("problem opiekuna; proces opiekuna zapisuje step.started", "decision", "key",
                                   "n", "problem", "root", "step", "target", "ticket", "trigger", "triggers",
                                   "watermark"),
    "caretaker.guard": _dynamic("migawka strażnika opiekuna"),
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
    "caretaker.chat-action": _dynamic("akcja z rozmowy opiekuna (kopia przebiegu)"),
    "caretaker.chat-file": _metadata("plik rozmowy opiekuna", "chat", "signature", "step"),
})

ACTION_START = MappingProxyType({
    "key": NAME_KEY, "action": Field("discriminator", "str", note="step-start"), **START_IDENTITY,
    **_neutral(*(n for n in START_NEUTRAL if n not in ("gitDir", "readDirsProfile", "repo", "toolingKind",
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


def _meta_action(reason, source="writer"):
    return Action("metadata", reason=reason, source=source)


ACTIONS = MappingProxyType({
    "step-start": Action("step-start", ACTION_START, ACTION_RESULT, "zamiar i wynik startu procesu kroku"),
    "reserve": _meta_action("rezerwacja numeru ticketu"),
    "card-branch": _meta_action("gałąź karty (Git)"),
    "ticket-branch": _meta_action("gałąź ticketu (Git)"),
    "review-branch": _meta_action("gałąź review (Git)"),
    "step-reopen": _meta_action("ponowne otwarcie worktree kroku (Git)"),
    "plan-commit": _meta_action("commit planu (Git)"),
    "cleanup": _meta_action("usunięcie worktree (Git)"),
    "merge": _meta_action("scalenie ticketu do karty (Git)"),
    "main-merge": _meta_action("scalenie karty do głównej (Git)"),
    "change-base": _meta_action("korekta bazy karty"),
    "caretaker-action": _meta_action("akcja opiekuna na planie/kroku; proces opiekuna ma step.started"),
    "youtrack-create": _meta_action("dawne założenie zgłoszenia YouTracka", source="historical"),
    "youtrack-link": _meta_action("powiązanie zgłoszeń YouTracka"),
    "youtrack-note": _meta_action("komentarz YouTracka"),
})

BOX_EVENTS = MappingProxyType({
    "box.use": Event("box-trace", MappingProxyType({
        "step": Field("name", "str", True, domain="step", note="krok w pudełku; źródło ochrony"),
        "box": Field("identity", "str", True, domain="resource", note="instancja pudełka"),
        "ts": Field("identity", "str", True, note="znacznik koperty"),
    }), "wejście kroku do pudełka"),
    "box.release": Event("box-trace", MappingProxyType({
        "step": Field("name", "str", True, domain="step", note="krok w pudełku; źródło ochrony"),
        "box": Field("identity", "str", True, domain="resource", note="instancja pudełka"),
        "ts": Field("identity", "str", True, note="znacznik koperty"),
    }), "wyjście kroku z pudełka"),
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
    outside_unit: str | None = None
    console: dict | None = None   # komplet ostatniego poprawnego step.entered tego kroku

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
    if "kind" in event and (not isinstance(kind, str) or kind not in CARD_STEP_KINDS | TICKET_STEP_KINDS):
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
    # Nieznany typ outside.* nie jest metadanymi: zachowujemy go jako ślad outside.
    return isinstance(kind, str) and kind.startswith("outside.") and kind not in EVENTS


def evidence_errors(event, fields, identity):
    """Błędy dowodu według ról pól; (unknown?, inconsistent?) bez modyfikacji stanu."""
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
        if spec.role == "inherited":
            previous = _predecessor_value(identity, spec, name)
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


def _predecessor_value(identity, spec, name):
    if spec.predecessor == "step.entered|step.started":
        return (identity.console or {}).get(name)
    if spec.predecessor == "outside.started":
        return identity.outside_unit
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
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            identities[None, index] = ProcessIdentity("", state="unknown")
            continue
        kind = event.get("type")
        if kind == "card.started":
            value = event.get("project")
            project = value if valid_project(value) else ""
            card = event.get("card", "")
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
        unknown, inconsistent = evidence_errors(event, fields or {}, identity)
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
            if not unknown and all(canonical_text(event.get(k), True) for k in CONSOLE_FIELDS):
                # Start rozmowy opiekuna zapisuje własny komplet Konsoli tego kroku.
                identity.console = {k: event[k] for k in CONSOLE_FIELDS}
            # Dane operacyjne nie są źródłem klasyfikacji ochrony.
            for field_name, value in event.items():
                if field_name not in START_FIELDS:
                    identity.data[field_name] = value
            identity.data["step"] = identity.name
            continue
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
        if role == "console-enter" and not unknown and identity.has_start:
            # Tylko kompletne wejście tego kroku jest poprzednikiem otwarcia/zamknięcia.
            identity.console = {k: event[k] for k in CONSOLE_FIELDS}
        if outside:
            if role == "outside-start":
                # Outside uruchamia osobny proces: jego jednostka musi być
                # zapisana w tym starcie, nie odziedziczona z kroku.
                unit = event.get("unit")
                if canonical_text(unit, True):
                    if identity.outside_unit is not None and identity.outside_unit != unit:
                        identity.worsen("inconsistent")
                    else:
                        identity.outside_unit = unit
            identity.outside.append(dict(event))
    return identities


def box_event_error(event):
    """Walidacja zdarzenia dziennika pudełka według BOX_EVENTS (koperta i pola śladu)."""
    if (not isinstance(event, dict) or type(event.get("v")) is not int
            or event["v"] != bs.SCHEMA or not isinstance(event.get("type"), str)
            or event["type"] not in BOX_EVENTS):
        return "nieznany format zdarzenia pudełka"
    spec = BOX_EVENTS[event["type"]]
    required = ["ts", "box"] + [k for k, f in (spec.fields or {}).items() if f.nonempty and k not in ("ts", "box")]
    if any(not isinstance(event.get(key), str) or not event[key].strip() for key in required):
        return "niepełna tożsamość zdarzenia pudełka"
    return ""
