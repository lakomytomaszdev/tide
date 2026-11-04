"""Opiekun karty (K-37, PT-143): krok w tle rodzaju `caretaker`, który przy problemie karty sprawdza go zamiast
użytkownika i odpowiada jedną akcją z rejestru. Bez Qt i bez dysku: wszystko liczy się ze stanu dziennika
(`FlowState`) i z tego, co poda silnik (plik wyniku już odczytany, katalog wyjściowy).

Publiczny interfejs (silnik i widoki wołają tylko te funkcje):
- `problems(st)` — otwarte problemy karty z wyzwalaczy `TRIGGERS`; jeden łańcuch kroków ma naraz najwyżej jeden
  problem — kolejny wyzwalacz na tym łańcuchu dołącza do niego (`triggers[]`),
- `problem_id(...)` — jedyna definicja id problemu, bez `seq` dziennika,
- `owned(st, id)` — jedyne liczenie działań opiekuna przy problemie (od ostatniego zamknięcia),
- `brief(problem, st, ...)` — kartka kroku opiekuna (pierwsza linia „Jesteś opiekunem karty …”),
- `parse_result(data, problem, st)` — walidacja pliku wyniku opiekuna; granica uprawnień jest tu, nie w kartce.

Rejestry (kolejne tickety tylko dopisują wpisy, bez edycji wspólnych gałęzi):
- `TRIGGERS` — funkcje `(st) -> [kandydat]`, kandydat `{trigger, step?, ticket?, stage?, cause?, decision?}`,
- `ACTIONS` — `nazwa → Action(kinds, params, apply, description)`; `kinds` to wyzwalacze, przy których akcja jest
  dozwolona (`"*"` — przy każdym), `apply(port, problem, params)` wykonuje akcję przez port silnika,
- `HISTORY_LABELS` — akcja → tekst po ludzku do historii karty,
- `BRIEF_SECTIONS` — wyzwalacz → funkcja `(problem, st) -> str` z przesłanką w kartce.

Id problemu: wyzwalacz z krokiem — `<wyzwalacz>:<korzeń łańcucha parentStep>` (`error:K-37-PT-7-1`); powtórzenie
przyczyny — `repeat:<ticket>:<przyczyna>`; bez kroku — `<wyzwalacz>:<etap>` (`flow-error:<etap>`, `stalled:card`);
czekająca decyzja — `waiting:<id decyzji>`.

Zdarzenia dziennika:
- `caretaker.started` — `{problem, key, n, step, target, root, ticket, trigger, watermark}`: zamiar uruchomienia
  kroku opiekuna; `key` = `caretaker:<problem>:<n>` (n — kolejny krok opiekuna przy tym problemie, od 1),
  `step` — id kroku opiekuna, `target` — krok problemu, `watermark` — ostatni `seq` dziennika w chwili startu.
  Start kroku idzie istniejącą parą „zamiar → wynik” `step-start` i zdarzeniem `step.started` z `kind: caretaker`.
- `caretaker.escalated` — `{problem, step, target, ticket, action, summary, recommendation, optionId, failed,
  error, code}`: problem wraca do użytkownika (akcja `escalate` albo nieudane działanie, `failed: true`),
- `caretaker.action` — `{problem, step, target, ticket, action, params, ok, error}`: wykonana akcja inna niż oddanie
  decyzji (schemat na potrzeby kolejnych ticketów; ten ticket go nie zapisuje).

Bez rekursji: krok `caretaker` nie trafia do `st.steps`, więc żaden wyzwalacz go nie widzi (i wyzwalacz `error`
sprawdza to jeszcze raz wprost).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import faults
from .labels import kind_label, step_name

KIND = "caretaker"
ACTOR = "caretaker"
BRIEF_FIRST_LINE = "Jesteś opiekunem karty"
STARTED, ESCALATED, ACTION = "caretaker.started", "caretaker.escalated", "caretaker.action"
LOCAL_ONLY_NOTE = "opiekun niedostępny dla pracy tylko lokalnej"
CLASS_RANK = {"lekki": 0, "standard": 1, "mocny": 2}

# Długości pól wyniku (US 19): instrukcja/odpowiedź/uwaga do 4000 znaków, powód 1–600, streszczenie do 1200.
TEXT_MAX, REASON_MAX, SUMMARY_MAX = 4000, 600, 1200
OPTION_ID_MAX = 60

BAD_ACTION, BAD_RESULT = "bad-caretaker-action", "bad-caretaker-result"
QUESTION, OTHER_RESULT, NOT_STARTED, STOPPED = ("caretaker-question", "caretaker-result", "caretaker-not-started",
                                                 "caretaker-stopped")

TICKET_KINDS = ("ticket", "fix", "review", "conflict")


# --- rejestr akcji -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    """Pole wyniku akcji: tekst od `minimum` do `maximum` znaków; `choices(problem)` — dozwolone wartości."""
    required: bool
    minimum: int
    maximum: int
    hint: str
    choices: object = None


@dataclass(frozen=True)
class Action:
    kinds: tuple
    params: dict = field(default_factory=dict)
    apply: object = None
    description: str = ""


def _option_ids(problem: dict) -> list[str]:
    return list(problem.get("options") or [])


def _escalate(port, problem: dict, params: dict) -> None:
    port.escalate(problem, params)


ACTIONS: dict[str, Action] = {
    "escalate": Action(
        kinds=("*",),
        params={
            "summary": Param(True, 1, SUMMARY_MAX, "co sprawdziłeś i co znalazłeś, dla użytkownika"),
            "recommendation": Param(True, 1, REASON_MAX, "co polecasz użytkownikowi i dlaczego"),
            "optionId": Param(False, 1, OPTION_ID_MAX, "id możliwości z decyzji, którą polecasz", _option_ids),
        },
        apply=_escalate,
        description="oddaj decyzję użytkownikowi z krótkim streszczeniem i rekomendacją"),
}

HISTORY_LABELS = {"escalate": "oddaje decyzję"}


# --- wyzwalacze ----------------------------------------------------------------------------------

def _error_trigger(st) -> list[dict]:
    """Wyzwalacz 1: błąd kroku ticketu czekający na decyzję — po automatycznym ponowieniu i ewentualnym przejściu
    na mocniejszego wykonawcę (automaty silnika działają wcześniej w tym samym przebiegu). Bez wygasłego logowania,
    limitu dostawcy i winy użytkownika: tam zostaje dzisiejsza decyzja."""
    latest: dict = {}
    for name, item in st.steps.items():
        latest[item.get("ticket") or ""] = name
    found = []
    for name in latest.values():
        step, done = st.steps.get(name) or {}, st.finished.get(name) or {}
        if step.get("kind") == KIND or step.get("kind") not in TICKET_KINDS or not step.get("ticket"):
            continue
        if done.get("result") != "error" or int(step.get("attempt") or 0) < 1:
            continue
        if name in st.step_decisions or name in st.superseded or step.get("ticket") in st.rejected:
            continue
        if any(other.get("parentStep") == name for other in st.steps.values()):
            continue
        reason = done.get("reason") or ""
        found_fault = faults.of_finished(done)
        if reason in ("login", "limit") or done.get("faultCause") == "limit" or \
                found_fault["cause"] == "limit" or found_fault["fault"] == "user":
            continue
        found.append({"trigger": "error", "step": name, "ticket": step.get("ticket") or "",
                      "decision": f"step:{name}"})
    return found


TRIGGERS = [_error_trigger]


def _error_section(problem: dict, st) -> str:
    """Przesłanka: opis problemu z decyzji i powód zakończenia kroku; dziennik kroku, plan, notki i poprzednie
    próby są w plikach kontekstu (`caretakerctx`, PT-153)."""
    name = problem.get("step") or ""
    step, done = st.steps.get(name) or {}, st.finished.get(name) or {}
    decision = _decision_of(st, name) or {}
    lines = ["## Problem: błąd kroku", "",
             f"Krok: {name} ({step_name(step.get('kind'), step.get('ticket'))}), wykonawca: "
             f"{step.get('executor') or '?'}, próba {int(step.get('attempt') or 0) + 1}.",
             f"Co się stało: {decision.get('happened') or 'Krok zakończył się błędem.'}",
             f"Dlaczego to problem: {decision.get('why') or ''}",
             f"Powód zakończenia kroku: {done.get('reason') or '(brak)'}"]
    found = faults.of_finished(done)
    lines.append(f"Czyja wina według dziennika kroku: {faults.FAULT_LABELS.get(found['fault'], found['fault'])}.")
    return "\n".join(lines)


BRIEF_SECTIONS = {"error": _error_section}


# --- problemy i liczenie działań -----------------------------------------------------------------

def chain_root(st, name: str) -> str:
    """Pierwszy krok łańcucha `parentStep` (wszystkie rodzaje kroków); pętla w dzienniku nie zawiesza liczenia."""
    seen = {name}
    while True:
        parent = (st.steps.get(name) or {}).get("parentStep") or ""
        if not parent or parent in seen or parent not in st.steps:
            return name
        seen.add(parent)
        name = parent


def problem_id(trigger: str, st=None, *, step: str = "", ticket: str = "", cause: str = "", stage: str = "",
               decision: str = "") -> str:
    """Jedyna definicja id problemu (bez `seq`): kolejne próby jednego łańcucha kroków mają to samo id."""
    if trigger == "repeat":
        return f"repeat:{ticket}:{cause}"
    if trigger == "waiting":
        return f"waiting:{decision}"
    if step:
        return f"{trigger}:{chain_root(st, step) if st is not None else step}"
    return f"{trigger}:{stage}"


def problems(st) -> list[dict]:
    """Otwarte problemy karty w kolejności wyzwalaczy z `TRIGGERS`; kandydaci na jednym łańcuchu łączą się w jeden
    problem, a jego id to id problemu, który na tym łańcuchu już pracuje (jeśli jest), inaczej pierwszego kandydata."""
    if st.started is None or st.card_finished:
        return []
    grouped: dict[str, dict] = {}
    for trigger in TRIGGERS:
        for candidate in trigger(st) or []:
            step = candidate.get("step") or ""
            root = chain_root(st, step) if step else ""
            ident = problem_id(candidate["trigger"], st, step=step, ticket=candidate.get("ticket") or "",
                               cause=candidate.get("cause") or "", stage=candidate.get("stage") or "",
                               decision=candidate.get("decision") or "")
            group = f"chain:{root}" if root else ident
            if group in grouped:
                grouped[group]["triggers"].append(candidate["trigger"])
                known = _known_id(st, root)
                if known:
                    grouped[group]["id"] = known
                continue
            grouped[group] = {"id": _known_id(st, root) or ident, "trigger": candidate["trigger"],
                              "triggers": [candidate["trigger"]], "step": step, "root": root,
                              "ticket": candidate.get("ticket") or "", "stage": candidate.get("stage") or "",
                              "decision": candidate.get("decision") or "",
                              "options": [o["id"] for o in (_decision_of(st, step) or {}).get("options") or []]}
    return list(grouped.values())


def _known_id(st, root: str) -> str:
    """Id problemu już zapisanego dla łańcucha (pierwszy krok opiekuna na nim) — dołączenie do otwartego problemu."""
    if not root:
        return ""
    return next((run.get("problem") or "" for run in st.caretaker_runs.values() if run.get("root") == root), "")


def owned(st, ident: str) -> dict:
    """Jedyne liczenie działań opiekuna przy problemie: kroki opiekuna z tym id od ostatniego zamknięcia problemu
    (decyzja użytkownika bez pola `caretaker` albo rozwiązanie — zwykły krok zakończony `done`). Dla problemu
    z krokiem zakres to jego łańcuch, bez kroku — wskazany ticket, a bez ticketu — cała karta.

    `count` — działania od zamknięcia, `runs` — te kroki, `next` — numer następnego kroku przy tym id (do klucza
    `caretaker:<problem>:<n>`, nigdy się nie powtarza), `open` — krok opiekuna bez wyniku, `handed` — ostatnie
    działanie oddało problem użytkownikowi."""
    runs_all = [run for run in st.caretaker_runs.values() if run.get("problem") == ident]
    roots = {run.get("root") for run in runs_all if run.get("root")}
    tickets = {run.get("ticket") for run in runs_all if run.get("ticket")}

    def in_scope(name: str) -> bool:
        # `st.steps` nie zawiera kroków opiekuna: jego wynik `done` nigdy nie rozwiązuje problemu.
        if name not in st.steps or not runs_all:
            return False
        if roots:
            return chain_root(st, name) in roots
        return not tickets or st.steps[name].get("ticket") in tickets

    closed = 0
    for name, decision in st.step_decisions.items():
        if decision.get("auto") or decision.get("automatic") or "caretaker" in decision:
            continue
        if in_scope(name):
            closed = max(closed, int(decision.get("seq") or 0))
    for name, done in st.finished.items():
        if done.get("result") == "done" and in_scope(name):
            closed = max(closed, int(done.get("seq") or 0))
    runs = [run for run in runs_all if int(run.get("seq") or 0) > closed]
    open_run = next((run for run in runs_all if run.get("step") not in st.caretaker_outcomes), None)
    last = st.caretaker_outcomes.get(runs[-1].get("step")) if runs else None
    return {"count": len(runs), "runs": runs, "next": 1 + max([int(r.get("n") or 0) for r in runs_all], default=0),
            "open": open_run, "handed": bool(last and last.get("type") == ESCALATED), "closedSeq": closed}


def local_only(st) -> bool:
    started = st.started or {}
    return started.get("preferredProvider") == "local" or (
        not started.get("preferredProvider") and started.get("provider") == "local")


def unavailable(st, column: str) -> str:
    """Dlaczego opiekun nie ruszy przy tej karcie (pusty napis — ruszy). Do #9 opiekun jest włączony na stałe."""
    if st.started is None or st.card_finished:
        return "karta zakończona"
    if column in ("backlog", "done", "archived"):
        return f"karta w kolumnie {column}"
    if st.paused:
        return "karta wstrzymana"
    if local_only(st):
        return LOCAL_ONLY_NOTE
    return ""


def executor_name(catalog, st) -> str:
    """Wykonawca opiekuna do #9: najmocniejsza klasa preferowanego dostawcy karty (w trybie mieszanym — dostawcy
    wykonawcy karty)."""
    started = st.started or {}
    provider = started.get("preferredProvider") or ""
    if provider in ("", "mixed"):
        provider = started.get("provider") or ""
    best, rank = "", -1
    for executor in getattr(catalog, "executors", ()) or ():
        if executor.provider != provider or not executor.program or not executor.model:
            continue
        mine = max((CLASS_RANK.get(c, -1) for c in executor.classes), default=-1)
        if mine > rank:
            best, rank = executor.name, mine
    return best


# --- kartka --------------------------------------------------------------------------------------

def brief(problem: dict, st, *, card: str, out_dir: str, worktree: str, context: list | None = None,
          context_dir: str = "") -> str:
    """Kartka kroku opiekuna: opis problemu (zamaskowany `faults.mask`), pliki kontekstu (PT-153, `context` —
    `[{path, title, trimmed}]` z `caretakerctx.build`), dostępne akcje z parametrami, zakazy i ścieżka wyniku.
    Sekcja problemu pochodzi z `BRIEF_SECTIONS` według wyzwalacza."""
    section = BRIEF_SECTIONS.get(problem.get("trigger") or "")
    described = section(problem, st) if section else f"## Problem\n\nProblem {problem.get('id')}."
    files = []
    if context:
        files = ["## Kontekst", "",
                 f"Tide przygotował pliki kontekstu w {context_dir or '(katalog kontekstu)'} (tylko do odczytu; "
                 "sekrety zamaskowane jako •••, razem najwyżej 400 kB). Przeczytaj je, zanim wybierzesz akcję:", ""]
        files += [f"- {item['path']} — {item['title']}" + (" (przycięty do limitu)" if item.get("trimmed") else "")
                  for item in context]
        files.append("")
    actions = allowed_actions(problem)
    listed = []
    for name, action in actions.items():
        listed.append(f"- `{name}` — {action.description}. Pola:")
        for param, spec in action.params.items():
            need = "wymagane" if spec.required else "opcjonalne"
            limit = f"od {spec.minimum} do {spec.maximum} znaków"
            choices = spec.choices(problem) if spec.choices else None
            extra = f"; jedna z: {', '.join(choices)}" if choices else ""
            listed.append(f"  - `{param}` ({need}, {limit}{extra}) — {spec.hint}")
    example = {"result": "done", "action": "escalate", "summary": "…", "recommendation": "…"}
    if problem.get("options"):
        example["optionId"] = problem["options"][0]
    import json
    text = "\n".join([
        f"{BRIEF_FIRST_LINE} {card} w Tide. Tide zatrzymał się na problemie i oddał go Tobie, zanim zapyta "
        "użytkownika. Sprawdź problem i odpowiedz dokładnie jedną akcją z listy poniżej.",
        "",
        f"Id problemu: {problem.get('id')}",
        f"Worktree kroku (wyłącznie do odczytu): {worktree}",
        "",
        faults.mask(described),
        "",
        *files,     # `collect` maskuje tytuły i nadaje sekretnej nazwie alias istniejącego pliku kontekstu
        "## Dostępne akcje",
        "",
        *listed,
        "",
        "## Zakazy",
        "",
        "- Nie zmieniaj plików w worktree ani w repozytorium i nie uruchamiaj poleceń: krok opiekuna nie ma "
        "dozwolonych poleceń, a zapis ma tylko do własnego katalogu wyjściowego.",
        "- Nie commituj, nie rób push ani scalenia; nie zatwierdzaj planu, nie udzielaj zgód na polecenia, nie "
        "uruchamiaj niczego poza piaskownicą i nie odrzucaj ticketu. Takich akcji nie ma — akcja spoza listy jest "
        "nieudanym działaniem i problem od razu wraca do użytkownika.",
        "- Nie zadawaj pytań: gdy potrzebna jest decyzja użytkownika, użyj `escalate`.",
        "",
        "## Wynik",
        "",
        f"Plik wyniku: {out_dir}/result.json",
        "",
        "Zapisz go jako ostatnią czynność, w formacie JSON, np.:",
        json.dumps(example, ensure_ascii=False),
        "Bez pliku wyniku albo z wynikiem innym niż `done` krok opiekuna jest nieudany, a problem wraca do "
        "użytkownika.",
        "",
    ])
    return text


def allowed_actions(problem: dict) -> dict:
    trigger = problem.get("trigger") or ""
    return {name: action for name, action in ACTIONS.items() if "*" in action.kinds or trigger in action.kinds}


# --- wynik ---------------------------------------------------------------------------------------

def _failed(code: str, error: str, *, action: str = "", summary: str = "") -> dict:
    return {"ok": False, "code": code, "error": _text(error)[:REASON_MAX], "action": action,
            "summary": _text(summary)[:SUMMARY_MAX]}


def parse_result(data, problem: dict, st) -> dict:
    """Plik wyniku opiekuna → `{ok: True, action, params}` albo nieudane działanie `{ok: False, code, error, action,
    summary}`. `data` to odczytany JSON, None (brak pliku) albo cokolwiek innego (zły format).

    Akcja musi być w `ACTIONS` (inaczej `bad-caretaker-action` — także `approve-plan`, `allow`, `merge`, `push`,
    `reject`…) i dozwolona dla rodzaju problemu; pola mają wymagane długości. Wynik inny niż `done` jest nieudanym
    działaniem; pytanie niesie swoją treść jako streszczenie (problem wraca do użytkownika jak przy `escalate`)."""
    if data is None:
        return _failed(BAD_RESULT, "brak pliku wyniku opiekuna")
    if not isinstance(data, dict):
        return _failed(BAD_RESULT, "zły format pliku wyniku opiekuna (oczekiwano obiektu JSON)")
    result = data.get("result")
    if result != "done":
        if result == "question":
            question = _text(data.get("question"))
            return _failed(QUESTION, "opiekun zadał pytanie zamiast wybrać akcję" +
                           (f": {question}" if question else ""), summary=question)
        detail = _text(data.get("reason") or data.get("command"))
        return _failed(OTHER_RESULT, f"wynik opiekuna „{_text(result) or '?'}” zamiast „done”" +
                       (f": {detail}" if detail else ""))
    action = data.get("action")
    if not isinstance(action, str) or not action.strip():
        return _failed(BAD_RESULT, "wynik opiekuna nie ma akcji")
    action = action.strip()
    if action not in ACTIONS:
        return _failed(BAD_ACTION, f"nieznana akcja opiekuna „{action[:60]}”", action=action[:60])
    spec = ACTIONS[action]
    if action not in allowed_actions(problem):
        return _failed(BAD_ACTION, f"akcja „{action}” jest niedozwolona przy problemie {problem.get('trigger')}",
                       action=action)
    params = {}
    for name, param in spec.params.items():
        value = data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if param.required:
                return _failed(BAD_RESULT, f"akcja „{action}” wymaga pola {name}", action=action)
            continue
        if not isinstance(value, str):
            return _failed(BAD_RESULT, f"pole {name} musi być tekstem", action=action)
        value = value.strip()
        if not param.minimum <= len(value) <= param.maximum:
            return _failed(BAD_RESULT, f"pole {name}: od {param.minimum} do {param.maximum} znaków", action=action)
        if param.choices is not None and value not in param.choices(problem):
            return _failed(BAD_RESULT, f"pole {name}: nieznana wartość „{value[:60]}”", action=action)
        params[name] = value
    return {"ok": True, "action": action, "params": params}


# --- widoki: decyzja, derived, historia ----------------------------------------------------------

def hidden(st, name: str) -> bool:
    """Decyzja tego kroku jest ukryta, dopóki pracuje nad nim opiekun."""
    return name in st.caretaking


def _escalations(st):
    """Aktualne eskalacje według wspólnego cyklu życia problemu w `owned()`, także bez kroku-sprawcy."""
    for ident, event in st.caretaker_escalations.items():
        own = owned(st, ident)
        if own["handed"] and own["runs"][-1].get("step") == event.get("step"):
            yield event


def annotate(st, decision: dict) -> dict:
    """Decyzja wracająca od opiekuna dostaje `caretaker{summary, recommendation, tried[], optionId, failed}`;
    możliwość z `optionId` jest polecona i pierwsza, a żadna nie jest wybrana (D10)."""
    escalated = next((event for event in _escalations(st)
                      if decision.get("step") and event.get("target") == decision["step"]), None)
    if not escalated:
        return decision
    run = st.caretaker_runs.get(escalated.get("step") or "") or {}
    tried = []
    for item in owned(st, run.get("problem") or escalated.get("problem") or "")["runs"]:
        outcome = st.caretaker_outcomes.get(item.get("step") or "") or {}
        if outcome.get("type") == ESCALATED and not outcome.get("failed"):
            continue
        tried.append({"step": item.get("step") or "", "action": outcome.get("action") or "",
                      "ok": not outcome.get("failed") and outcome.get("ok", True) is not False,
                      "text": history_text(outcome)})
    failed = bool(escalated.get("failed"))
    summary = escalated.get("summary") or (
        f"Opiekun nie poradził sobie z problemem: {escalated.get('error')}" if failed else "")
    option = "" if failed else escalated.get("optionId") or ""
    made = dict(decision)
    made["caretaker"] = {"summary": summary, "recommendation": escalated.get("recommendation") or "",
                         "tried": tried, "optionId": option, "failed": failed}
    options = made.get("options") or []
    if option and any(o.get("id") == option for o in options):
        chosen = [{**o, "recommended": True} for o in options if o.get("id") == option]
        made["options"] = chosen + [{**o, "recommended": False} for o in options if o.get("id") != option]
    return made


def unavailable_note(st, step: str) -> str:
    """Dopisek do decyzji, gdy problem tego kroku byłby dla opiekuna, ale opiekun przy tej karcie nie działa."""
    if not local_only(st):
        return ""
    # Same wyzwalacze, nie `problems()`: problem liczy możliwości z decyzji, a decyzja woła tę funkcję.
    return LOCAL_ONLY_NOTE if any(candidate.get("step") == step for trigger in TRIGGERS
                                  for candidate in trigger(st) or []) else ""


def working(st) -> list[dict]:
    """Kroki opiekuna bez wyniku: `{problem, step, target, ticket, label, startedTs}`."""
    out = []
    for name, run in st.caretaker_runs.items():
        if name in st.caretaker_outcomes:
            continue
        target = st.steps.get(run.get("target") or "") or {}
        started = st.caretakers.get(name) or {}
        out.append({"problem": run.get("problem") or "", "step": name, "target": run.get("target") or "",
                    "ticket": run.get("ticket") or "",
                    "label": "opiekun sprawdza " + (step_name(target.get("kind"), target.get("ticket")) if target
                                                     else run.get("ticket") or "kartę"),
                    "startedTs": _epoch(started.get("ts") or run.get("ts"))})
    return out


def view(st) -> dict:
    """`derived.flow.caretaker`: `enabled`, `working[]` (jak `working`), `escalated[]` — problemy oddane
    użytkownikowi, które nadal są otwarte według `owned()`: `{problem, step, target, ticket, summary,
    recommendation, optionId, failed, error}`."""
    escalated = []
    for event in _escalations(st):
        escalated.append({key: event.get(key) or "" for key in
                          ("problem", "step", "target", "ticket", "summary", "recommendation", "optionId", "error")}
                         | {"failed": bool(event.get("failed"))})
    return {"enabled": not local_only(st), "working": working(st), "escalated": escalated}


def running_entries(st) -> list[dict]:
    """Trwające kroki opiekuna w kształcie `derived.flow.running[]` (etykieta „opiekun”, bez wejścia do rozmowy)."""
    out = []
    for name, step in st.caretakers.items():
        if name in st.finished:
            continue
        out.append({"id": name, "ticket": step.get("ticket") or "", "kind": KIND, "executor": step.get("executor") or "",
                    "mode": "background", "startedTs": _epoch(step.get("ts")),
                    "lastActivityTs": _epoch((st.activity.get(name) or step).get("ts")), "canEnter": False,
                    "kindLabel": kind_label(KIND)})
    return out


def history_text(event: dict) -> str:
    """Wpis historii karty dla zdarzenia opiekuna (pusty napis — zdarzenie bez wpisu)."""
    kind = event.get("type")
    action = event.get("action") or ""
    if kind == ESCALATED and not event.get("failed"):
        summary = _text(event.get("summary"))
        return f"Opiekun: {HISTORY_LABELS.get('escalate')}" + (f" — {summary}" if summary else "")
    if kind in (ESCALATED, ACTION) and (event.get("failed") or event.get("ok") is False):
        label = HISTORY_LABELS.get(action)
        what = f"nie udało się ({label})" if label else \
            "nie udało się wykonać działania" + (f" „{action}”" if action else "")
        error = _text(event.get("error"))
        return f"Opiekun: {what}" + (f" — {error}" if error else "")
    if kind == ACTION:
        return f"Opiekun: {HISTORY_LABELS.get(action) or action or 'działanie'}"
    return ""


def _decision_of(st, name: str) -> dict | None:
    """Decyzja błędu kroku bez ukrywania i bez pola opiekuna (opis problemu do kartki i id możliwości)."""
    if not name:
        return None
    from .decisions import step_decision
    return step_decision(st, name)


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _epoch(value) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else 0.0
    except (AttributeError, ValueError, OverflowError):
        return 0.0
