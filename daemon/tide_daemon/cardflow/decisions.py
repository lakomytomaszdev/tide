"""Decyzje karty dla okna decyzji i pełnej karty: `build_decisions(st)` → `derived.flow.decisions[]` (K-35, PT-123).

Lista mówi po ludzku, co się stało, dlaczego potrzebna jest decyzja i co da każda możliwość. Liczy się wyłącznie ze
stanu dziennika (`FlowState`), bez Qt i bez dysku. To jedyny kontrakt decyzji: stare pola `derived.flow` (`action`,
`question`, `command`, `canAllow`, `error`, `limit`, `fault`…) usunął PT-135.

Decyzja:
- `id` — stały identyfikator (`step:<krok>`, `plan-approval:<seq>`, `review:<ticket>:<runda>`…),
- `step`, `stepKind`, `ticket`, `executor` — krok, którego dotyczy (puste dla decyzji samej karty),
- `kind` — rodzaj z `KINDS`; makieta K-35: A1/A2 → `permission`, B → `error` z `fault="tide"`, C → `plan-approval`,
  F → `question`; limit dostawcy (E) nie jest decyzją (D3),
- `happened`, `why` — zdania dla użytkownika, nigdy puste (bez danych: zdanie ogólne dla rodzaju); dla błędu
  „co się stało” i „czyja wina”,
- `options[]` — możliwości; polecona jest najwyżej jedna i zawsze pierwsza, żadna nie jest wybrana domyślnie (D10),
- `freeText` — czy okno pokazuje pole własnej odpowiedzi; `reply` mówi, dokąd ją wysłać
  (`{method, params, field}`: tekst trafia do `params[field]`), albo jest None. Błąd z `fault` `tide` albo `unknown`
  ma tu „Opisz problem i ponów” (`ReportStepFault`, pole `description`) z `preview` (`PreviewStepFault` — podgląd
  karty przed zapisem), `label`, `hint` i `effect` do pola,
- `fault` — dla błędów kroku z dziennika kroku (faults.py): `tide`, `model`, `user`, `provider`, `unknown`;
  dla YouTracka `tracker`, dla review planu bez recenzenta `provider`; pusty napis dla pozostałych rodzajów,
- `findings[]` — tylko `plan-approval`: uwagi recenzenta `{id, severity, text, tickets, selected}` (`selected`: domyślne
  zaznaczenie — `blocker` i `major`, nie `nit`); dla pozostałych rodzajów pusta lista,
- `technical{}` — szczegóły do zwiniętego bloku „Szczegóły techniczne”.

Zgoda na polecenie (`permission`, PT-124) ma dodatkowo:
- `commands[]` — polecenie kroku rozłożone na pojedyncze polecenia `{text, readOnly, stepDir, forbidden, reason,
  canAllow, preselected}` (`board.chain.describe`, liczone przez usługę przy wyniku kroku; `forbidden` wyłącznie
  z `is_forbidden` i reguł projektu, odczyt własnego `<krok>/in` i `<krok>/out` nie jest na liście),
- przy łańcuchu możliwość „Zezwól na wybrane i wznów” z `params.allow = []` — okno wpisuje tam indeksy
  zaznaczonych poleceń (domyślnie zaznaczone `preselected`; to zaznaczenie poleceń, nie możliwości — D10),
- `otherTitle`, `otherHint` — podpis i podpowiedź pola własnej odpowiedzi („Zrób zamiast tego…”), która idzie
  przez `reply` do `params.instead`.

Możliwość: `{id, title, effect, recommended, choice, params, method}`. `method` to metoda /Board na D-Bus,
a `params` — gotowe żądanie bez numeru karty: dla `ResolveCardStep` (StepResolve) JSON z `choice`, `step` i ewentualnie
`answer`; dla `ResolveCardReview` sam `choice`. Pole o pustej wartości (`executor: ""`) uzupełnia okno wyborem
użytkownika.

Kolejność: najpierw decyzje karty (plan, YouTrack, kroki bez ticketu), potem tickety; w grupie najstarsza pierwsza.
"""
from __future__ import annotations

import re

from . import caretaker
from . import faults
from . import baseproposal
from . import findings as review_findings
from .labels import kind_label
from .state import PLAN_WORK_KINDS, REASON_LABELS, FlowState, base_of

# Limit dostawcy nie jest decyzją (D3, PT-125): Tide czeka na odnowienie albo przenosi krok według ustawień.
KINDS = ("question", "permission", "error", "transfer", "stopped", "review",
         "plan-approval", "plan-revision", "plan-revision-failed", "plan-fix-failed", "plan-reviewer", "tracker",
         "caretaker")

RESOLVE_STEP, RESOLVE_REVIEW = "ResolveCardStep", "ResolveCardReview"
APPROVE_PLAN, EDIT_PLAN, ENTER_STEP = "ApproveCardPlan", "EditCardPlan", "EnterStep"
REPORT_FAULT, PREVIEW_FAULT = "ReportStepFault", "PreviewStepFault"

# Zdania, gdy dziennik nie daje szczegółów (US 2) — każdy rodzaj ma swoje, nigdy pusty napis.
GENERIC = {
    "caretaker": ("Opiekun sprawdził problem, który zatrzymał kartę.",
                  "Opiekun oddał decyzję Tobie. Wybierz, co zrobić z problemem."),
    "question": ("Krok zadał pytanie i czeka na odpowiedź.",
                 "Bez odpowiedzi krok nie pójdzie dalej."),
    "permission": ("Krok poprosił o zgodę na polecenie spoza listy dozwolonych.",
                   "Tide nie uruchomi tego polecenia bez Twojej decyzji, więc krok stoi."),
    "error": ("Krok zakończył się błędem, także po automatycznym ponowieniu.",
              "Nie wiadomo, czyja to wina — dziennik kroku tego nie rozstrzyga. Zdecyduj, co dalej."),
    "transfer": ("Krok zatrzymano, żeby przekazać go innemu dostawcy.",
                 "Przekazanie czeka na wybór wykonawcy."),
    "stopped": ("Rozmowa z krokiem w Konsoli została zamknięta przed zapisaniem wyniku.",
                "Krok nie skończył pracy — wróć do rozmowy, żeby ją dokończyć."),
    "review": ("Review nadal wskazuje ważne uwagi po kolejnych rundach poprawek.",
               "Tide nie ponawia poprawek bez końca — zdecyduj, czy próbować dalej."),
    "plan-approval": ("Plan jest gotowy i przeszedł review.",
                      "Implementacja wystartuje dopiero po Twoim zatwierdzeniu."),
    "plan-revision": ("Planujący poprawił plan po uwagach.",
                      "Zmieniony plan zastąpi obecny dopiero po Twoim zatwierdzeniu różnicy."),
    "plan-revision-failed": ("Poprawka planu nie przeszła sprawdzenia formatu.",
                             "Tej poprawki nie da się użyć — odeślij plan ponownie albo ją odrzuć."),
    "plan-fix-failed": ("Poprawki formatu planu nadal nie przechodzą sprawdzenia.",
                        "Bez poprawnego planu karta nie pójdzie dalej — ponów poprawę z instrukcją."),
    "plan-reviewer": ("Review planu nie może wystartować.",
                      "Bez review plan nie trafi do zatwierdzenia."),
    "tracker": ("Operacja w YouTracku nie powiodła się.",
                "Karta czeka, aż przyczyna zostanie usunięta i ponowisz operację."),
}

PROVIDERS = {"anthropic": "Claude", "openai": "Codex", "local": "modelu lokalnego"}
PLAN_KINDS = ("planning", "plan-fix", "plan-review", "plan-revise", "plan-amend")


def build_decisions(st: FlowState) -> list[dict]:
    """Decyzje, na które karta czeka teraz; pusta lista — nic nie czeka na użytkownika."""
    if st.started is None or st.card_finished:
        return []
    found: list[tuple[int, int, dict]] = []

    def add(group: int, seq, decision: dict) -> None:
        found.append((group, int(seq or 0), decision))

    if st.base_request:
        asked = st.base_request
        add(0, asked.get("seq"), _question(st, asked["decision"], {}, {
            "question": "Wybrano nową bazę w pełnej karcie. Czy zachować obecną bazę?",
            "baseProposal": asked["baseProposal"]}))
    for name in _latest_steps(st):
        if (st.steps.get(name) or {}).get("ticket") in st.rejected:
            continue        # odrzucony ticket nie czeka na nikogo (K-16/LEGAL-75, 03.11)
        if caretaker.hidden(st, name):
            continue                    # opiekun karty sprawdza ten krok — decyzja wróci z jego wynikiem (PT-143)
        made = _step_decision(st, name)
        if made:
            made = caretaker.annotate(st, made)
            step = st.steps.get(name) or {}
            add(0 if not step.get("ticket") else 1, (st.finished.get(name) or {}).get("seq"), made)
    for (ticket, round_no), question in st.review_questions.items():
        if (ticket, round_no) in st.review_decisions or ticket in st.rejected:
            continue
        add(1, question.get("seq"), caretaker.annotate(st, _review(st, ticket, round_no, question)))
    for made in _plan_fix_decisions(st):
        add(0, (st.finished.get(made["step"]) or {}).get("seq"), made)
    plan = _plan_decision(st)
    if plan and plan.get("kind") == "plan-revision-failed":
        # PT-146: nieudaną poprawkę planu sprawdza opiekun — decyzja wraca z jego wynikiem (jak decyzja kroku).
        target = caretaker.revision_step(st)
        if target and caretaker.hidden(st, target):
            plan = None
        elif target:
            seq = plan.pop("_seq", 0)
            plan = {**caretaker.annotate(st, plan, target), "_seq": seq}
    if plan:
        plan = caretaker.annotate(st, plan)
        add(0, plan.pop("_seq", 0), plan)
    for failure in st.tracker_failures():
        if failure.get("action") == "youtrack-note" or st.running():
            continue
        add(0, failure.get("seq"), _tracker(failure))
    for event in caretaker.escalations(st):
        if (event.get("problem") or "").startswith(("flow-error:", "stalled:")):
            add(0, event.get("seq"), _caretaker(st, event))
    found.sort(key=lambda item: (item[0], item[1]))
    # `findings[]` (uwagi recenzenta z wagą i ticketami) ma tylko zatwierdzenie planu; reszta — pusta lista.
    return [{**decision, "findings": decision.get("findings", [])} for _group, _seq, decision in found]


# --- kroki ---------------------------------------------------------------------------------------

def _caretaker(st, event):
    problem = event["problem"]
    if problem.startswith("flow-error:"):
        stage = problem.split(":", 1)[1]
        happened = ((st.flow_errors.get(stage) or {}).get("event") or {}).get("error")
    else:
        happened = "Karta stoi bez kroku i powodu czekania."
    options = [_option(choice, title, effect, "ResolveCardCaretaker", {"choice": choice, "problem": problem})
               for choice, title, effect in (
                   ("retry-stage", "Ponów etap", "Tide ponowi etap karty i zamknie ten problem."),
                   ("dismiss", "Zamknij", "Tide zamknie problem bez ponawiania etapu."))]
    made = {"id": f"caretaker:{problem}:{event['seq']}", "kind": "caretaker", "step": "", "stepKind": "",
            "ticket": event.get("ticket") or "", "executor": "", "happened": happened or GENERIC["caretaker"][0],
            "why": GENERIC["caretaker"][1], "options": options, "freeText": True,
            "reply": {"method": "ResolveCardCaretaker", "preview": PREVIEW_FAULT,
                      "params": {"choice": "report", "problem": problem}, "field": "description",
                      "label": "Zgłoś błąd Tide", "confirmLabel": "Zgłoś błąd",
                      "effect": "Zapisze zgłoszenie w Backlogu plasma-tide i zamknie problem bez ponawiania etapu."},
            "fault": "", "technical": {"problem": problem, "error": event.get("error") or ""}}
    made = caretaker.annotate_escalation(st, made, event)
    if not made["caretaker"]["recommendation"]:
        made["caretaker"]["recommendation"] = "Wybierz, czy ponowić etap, czy zamknąć problem."
    return made


def _latest_steps(st: FlowState) -> list[str]:
    """Ostatni krok każdego ticketu (kroki planu i domknięcia mają wspólny pusty ticket)."""
    latest: dict = {}
    for name, item in st.steps.items():
        latest[item.get("ticket") or ""] = name
    return list(latest.values())


def step_decision(st: FlowState, name: str) -> dict | None:
    """Decyzja kroku bez ukrywania przez opiekuna i bez jego pola (opis problemu do kartki opiekuna)."""
    return _step_decision(st, name)


def _step_decision(st: FlowState, name: str) -> dict | None:
    step, done = st.steps.get(name) or {}, st.finished.get(name)
    if not done or name in st.superseded or step.get("ticket") in st.rejected:
        return None
    if any(other.get("parentStep") == name for other in st.steps.values()):
        return None
    result, reason = done.get("result") or "", done.get("reason") or ""
    decided = name in st.step_decisions
    if result in ("error", "stopped") and (reason == "limit" or done.get("faultCause") == "limit"):
        # Limit nie jest decyzją (D3): Tide czeka na odnowienie sam albo przenosi krok według ustawień.
        return None
    if reason == "transfer" and result == "stopped":
        return None if decided else _transfer(st, name, step, done)
    if decided:
        return None
    if result == "question":
        return _question(st, name, step, done)
    if result == "denied" and done.get("network"):
        return _network(st, name, step, done)
    if result == "denied":
        return _permission(st, name, step, done)
    # Wygasłe logowanie Tide pyta od razu, bez automatycznego ponowienia (03.11) — decyzja też od razu.
    if result == "error" and (int(step.get("attempt") or 0) >= 1 or reason == "login" or
                              caretaker.handed_repeat(st, name)):     # PT-148: opiekun oddał ten sam powód drugi raz
        return _error(st, name, step, done)
    if result == "stopped" and reason == "interactive-closed":
        return _stopped(st, name, step, done)
    return None


def _question(st, name, step, done) -> dict:
    question = layout(done.get("question"))
    happened = f"{_who(step, True)} pyta: „{question}”" if question else ""
    raw_options = clean_options(done.get("options"))
    options = [_option(f"answer-{index + 1}", raw["title"], raw["effect"], RESOLVE_STEP,
                       {"choice": "answer", "step": name, "answer": raw["title"]}, recommended=raw["recommended"])
               for index, raw in enumerate(raw_options)]
    options += wait_ticket_options(st, name, step, done.get("question") or "")
    reply = {"method": RESOLVE_STEP, "params": {"choice": "answer", "step": name}, "field": "answer"}
    decision = _decision(st, name, step, "question", happened,
                         "Krok czeka na odpowiedź; bez niej nie pójdzie dalej." if question else "",
                         _recommended_first(options), reply, "", _step_technical(step, done))
    if question:
        decision["happened"] = happened          # z podziałem na linie (layout), nie spłaszczone przez _sentences
    decision["copyCommands"] = copy_commands(done.get("question"), *(o["effect"] for o in raw_options))
    proposal = done.get("baseProposal")
    if proposal:
        decision["happened"] += "\nDowód propozycji bazy: " + (proposal.get("evidence") or "(brak)")
        decision["technical"]["baseProposal"] = proposal
        decision["technical"]["base"] = base_of(st)
        if proposal.get("valid") is True:
            current = base_of(st).get("baseRef") or "unknown"
            decision["options"] = [
                _option("base-keep", "Zachowaj " + current,
                        "Wznów krok na obecnej bazie i odblokuj ocenę oraz integrację zachowanych wyników.",
                        RESOLVE_STEP, {"choice": "answer", "step": name, "baseDecision": "keep"}, recommended=True),
                _option("base-change", "Zmień na " + proposal["ref"],
                        "Otwórz podgląd korekty. Bez operacji git i bez potwierdzenia wykonania.",
                        RESOLVE_STEP, {"choice": "answer", "step": name, "baseDecision": "change"})]
            decision["reply"] = None
            decision["freeText"] = False
            decision["why"] = ("Decyzja o bazie blokuje nowe kroki i integrację wyników tej karty. "
                               "Biegnące procesy mogą dokończyć; ich wyniki pozostają niescalone.\n" +
                               ("Oczekiwanie na podgląd korekty; baza nie została zmieniona.\n"
                                if name in st.base_waits else "") + baseproposal.LIMITS)
        else:
            decision["why"] += "\nNieprawidłowa propozycja bazy (tylko informacja): " + proposal.get("error", "")
    return decision


MAX_STEP_OPTIONS = 4
MAX_OPTION_TITLE = 120
MAX_OPTION_EFFECT = 400


def clean_options(raw) -> list[dict]:
    """`options[]` z pliku wyniku pytania kroku → `[{title, effect, recommended}]`; niepoprawny zestaw → `[]`.

    Zestaw jest poprawny, gdy ma od 1 do 4 pozycji, każda to obiekt z niepustym `title` (do 120 znaków),
    opcjonalnym tekstowym `effect` (do 400) i opcjonalnym logicznym `recommended`, a polecona jest najwyżej jedna.
    Cokolwiek innego daje pusty zestaw: okno pokazuje wtedy samo pole odpowiedzi, a krok nie kończy się błędem (D8)."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEP_OPTIONS:
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        title, effect, recommended = item.get("title"), item.get("effect", ""), item.get("recommended", False)
        if not isinstance(title, str) or not _text(title) or len(_text(title)) > MAX_OPTION_TITLE or \
                not isinstance(effect, str) or len(_text(effect)) > MAX_OPTION_EFFECT or \
                not isinstance(recommended, bool):
            return []
        result.append({"title": _text(title), "effect": _text(effect), "recommended": recommended})
    if sum(o["recommended"] for o in result) > 1 or len({o["title"] for o in result}) != len(result):
        return []
    return result


def _network(st, name, step, done) -> dict:
    """Odmowa proxy (`command: "network: <domena>"`, PT-172): pytanie o domenę. Pierwsza możliwość dopisuje domenę do
    profilu karty, zleca przebudowę i wznawia krok; druga wznawia krok z informacją o odmowie. Domena czerwona
    (adres wewnętrzny, zdalne repozytorium) nie ma pierwszej możliwości — o niej decyduje się w planie."""
    from ..box import profile as box_profile
    from ..box import templates
    domain = _text(done.get("network"))
    red = box_profile.classify(domain, templates.known_domains()) == box_profile.RED
    happened = f"{_who(step, True)} nie dostał połączenia z domeną {domain}: proxy pudełka ją odrzuciło." if domain else ""
    skip = _option("deny", "Nie, wznów bez", "Krok wznowi się z informacją o odmowie; domena nie trafi do profilu.",
                   RESOLVE_STEP, {"choice": "deny", "step": name})
    if red:
        why = ("Ta domena wymaga jawnej zgody w planie (adres wewnętrzny albo zdalne repozytorium), więc nie dopiszę "
               "jej w trakcie kroków.")
        options = [{**skip, "recommended": True}]
    else:
        why = "Dopisanie jej do profilu karty wymaga przebudowy środowiska; krok wznowi się po zleceniu przebudowy."
        options = [
            _option("box-allow", "Dopisz do profilu karty i przebuduj",
                    f"Domena {domain} trafi do profilu karty, Tide zleci przebudowę i wznowi krok.",
                    RESOLVE_STEP, {"choice": "box-allow", "step": name}),
            skip,
        ]   # bez polecanej: to decyzja o tym, dokąd pudełko może się łączyć
    technical = {**_step_technical(step, done), "domain": domain}
    made = _decision(st, name, step, "permission", happened, why, options, None, "", technical)
    made["commands"] = []
    return made


OTHER_PERMISSION = ("Zrób zamiast tego…", "Napisz, co krok ma zrobić zamiast tego polecenia.")


def _permission(st, name, step, done) -> dict:
    if done.get("outside"):
        return _outside(st, name, step, done)
    commands = [_command(item) for item in done.get("commands") or [] if isinstance(item, dict)]
    command = _text(done.get("command"))
    if len(commands) > 1 or commands and commands[0]["text"] != str(done.get("command") or "").strip():
        made = _chain_permission(st, name, step, done, commands)
    else:
        made = _single_permission(st, name, step, done)
    made["commands"] = commands
    made["otherTitle"], made["otherHint"] = OTHER_PERMISSION
    if command and made["reply"]:
        made["reply"] = {**made["reply"], "field": "instead"}
    return made


SHORT_COMMAND = 70   # dłuższe polecenie nie trafia do nagłówka decyzji


def _outside(st, name, step, done) -> dict:
    """Prośba o uruchomienie poza piaskownicą (03.11): Tide wykonuje polecenie w worktree ticketu na koncie
    użytkownika i oddaje krokowi kod wyjścia; „zawsze w projekcie” zapamiętuje rodzinę polecenia (`pattern`)."""
    command = _text(done.get("command"))
    pattern = _text(done.get("pattern"))
    who = _who(step, True)
    # Długie polecenie stoi tylko w polu pod spodem (04.11: nagłówek był kilkulinijkowym zlepkiem z całym `bash -c`).
    happened = ((f"{who} prosi o uruchomienie poza piaskownicą: {command}" if len(command) <= SHORT_COMMAND
                 else f"{who} prosi o uruchomienie polecenia poza piaskownicą") if command else "")
    reason = _text(done.get("reason"))
    from ..board.commands import outside_risky
    repo = ((getattr(st, "started", None) or {}).get("repo") or "")
    risk = outside_risky(command, (step.get("worktree") or "", repo, step.get("stepDir") or "")) if command else ""
    if done.get("canAllow", True) and risk:
        # Ryzykowne (wychodzi poza worktree, czyta `~`, zmienia system): tylko jednorazowo i z powodem na wierzchu.
        why = (f"Uwaga: {risk}. Tide uruchomi je na Twoim koncie, w worktree ticketu, tylko raz."
               + (f"\nPowód kroku: {reason}" if reason else ""))
        options = [
            _option("run", "Uruchom raz",
                    "Tide uruchomi to jedno polecenie i wznowi krok z jego wynikiem; następnym razem zapyta znowu.",
                    RESOLVE_STEP, {"choice": "run", "step": name}),
            _option("run-ticket", "Uruchom i zezwalaj do końca ticketu",
                    f"Tide uruchomi to polecenie i kolejne polecenia poza piaskownicą {step.get('ticket') or 'tego ticketu'} "
                    "bez pytania; zakazy projektu i łańcuchy nadal są blokowane.",
                    RESOLVE_STEP, {"choice": "run-ticket", "step": name}),
            _option("deny", "Nie uruchamiaj",
                    "Krok wznowi się bez tego polecenia.", RESOLVE_STEP, {"choice": "deny", "step": name}),
        ]   # bez polecanej: to decyzja o Twoich danych i systemie, nie o środowisku
    elif done.get("canAllow", True):
        why = ("Piaskownica kroku nie ma sieci, Dockera ani pełnych testów. Tide uruchomi polecenie w worktree "
               "ticketu na Twoim koncie i odda wynik krokowi." + (f"\nPowód kroku: {reason}" if reason else ""))
        options = [
            _option("run-always", f"Zawsze w projekcie: {pattern}" if pattern else "Uruchom i pamiętaj w projekcie",
                    "Polecenie ruszy teraz, a ta rodzina poleceń w tym projekcie będzie uruchamiana bez pytania.",
                    RESOLVE_STEP, {"choice": "run-always", "step": name}, recommended=True),
            _option("run", "Uruchom raz",
                    "Tide uruchomi to jedno polecenie i wznowi krok z jego wynikiem.",
                    RESOLVE_STEP, {"choice": "run", "step": name}),
            _option("deny", "Nie uruchamiaj",
                    "Krok wznowi się bez tego polecenia.", RESOLVE_STEP, {"choice": "deny", "step": name}),
        ]
    else:
        why = ("Tide nie uruchomi tego sam: to łańcuch poleceń, operator powłoki, niebezpieczna zmienna albo zakaz "
               "projektu. Możesz je uruchomić samodzielnie (pole niżej, „Kopiuj”) i wpisać wynik w „Zrób zamiast tego”."
               + (f"\nPowód kroku: {reason}" if reason else ""))
        options = [_option("deny", "Kontynuuj bez tego polecenia",
                           "Krok wznowi się bez niego; jeśli czegoś zabraknie, zgłosi pojedyncze polecenie.",
                           RESOLVE_STEP, {"choice": "deny", "step": name}, recommended=True)]
    reply = {"method": RESOLVE_STEP, "params": {"choice": "deny", "step": name}, "field": "instead"}
    made = _decision(st, name, step, "permission", happened, why, options, reply, "", _step_technical(step, done))
    made["why"] = "\n".join(_text(line) for line in why.split("\n") if _text(line))   # linie zostają osobno
    made["commands"] = []
    made["otherTitle"], made["otherHint"] = OTHER_PERMISSION
    return made


def _command(item: dict) -> dict:
    """Polecenie z łańcucha (PT-124) w postaci zapisanej przez usługę przy wyniku kroku (`board.chain.describe`)."""
    return {"text": str(item.get("text") or ""), "readOnly": item.get("readOnly") is True,
            "stepDir": item.get("stepDir") is True, "forbidden": item.get("forbidden") is True,
            "reason": _text(item.get("reason")), "canAllow": item.get("canAllow") is True,
            "preselected": item.get("preselected") is True and item.get("canAllow") is True}


def _chain_permission(st, name, step, done, commands) -> dict:
    """Łańcuch poleceń: zgoda na wybrane polecenia (`allow` = indeksy `commands[]`, uzupełnia okno)."""
    who = _who(step, True)
    allowable = [item for item in commands if item["canAllow"]]
    blocked = len(commands) - len(allowable)
    if len(commands) == 1:
        # Łańcuch, z którego po odjęciu własnego odczytu katalogu kroku zostało jedno polecenie: tekst na wierzchu.
        happened = f"{who} chce uruchomić polecenie spoza listy dozwolonych: {commands[0]['text']}"
    else:
        happened = (f"{who} chce uruchomić naraz {_count(len(commands), 'polecenie', 'polecenia', 'poleceń')} "
                    "spoza listy dozwolonych.")
    if allowable:
        why = ("Tide zatwierdza polecenia pojedynczo, więc łańcucha nie da się przepuścić w całości. Zaznacz te, "
               "na które zezwalasz, albo puść krok dalej bez nich.")
        if blocked:
            why += " Przy poleceniach, na które nie można zezwolić, jest powód."
        options = [
            _option("deny", "Kontynuuj bez tych poleceń",
                    "Krok wznowi się bez nich; jeśli czegoś zabraknie, poprosi o pojedyncze polecenia.",
                    RESOLVE_STEP, {"choice": "deny", "step": name}, recommended=True),
            _option("allow", "Zezwól na wybrane i wznów",
                    "Zaznaczone polecenia trafią na listę dozwolonych poleceń projektu; niezaznaczone nie zostaną "
                    "dopuszczone.", RESOLVE_STEP, {"choice": "allow", "step": name, "allow": []}),
        ]
    else:
        why = ("Na żadne z tych poleceń nie można zezwolić — powód jest przy każdym. Krok może pójść dalej bez nich "
               "albo zrobić coś innego.")
        options = [
            _option("deny", "Kontynuuj bez tych poleceń",
                    "Krok wznowi się bez nich; jeśli czegoś zabraknie, poprosi o pojedyncze polecenia.",
                    RESOLVE_STEP, {"choice": "deny", "step": name}, recommended=True),
        ]
    reply = {"method": RESOLVE_STEP, "params": {"choice": "deny", "step": name}, "field": "instead"}
    return _decision(st, name, step, "permission", happened, why, options, reply, "", _step_technical(step, done))


def _single_permission(st, name, step, done) -> dict:
    command = _text(done.get("command"))
    can_allow = bool(done.get("canAllow", True))
    who = _who(step, True)
    if command:
        happened = f"{who} chce uruchomić polecenie spoza listy dozwolonych: {command}"
    else:
        happened = ""
    if can_allow:
        why = "Tide nie uruchomi go bez Twojej zgody, więc krok stoi." if command else ""
        options = [
            _option("allow", "Zezwól i wznów",
                    "Polecenie trafi na listę dozwolonych poleceń projektu; następnym razem krok nie zapyta.",
                    RESOLVE_STEP, {"choice": "allow", "step": name}, recommended=True),
            _option("deny", "Kontynuuj bez tego polecenia",
                    "Krok wznowi się i dokończy pracę bez tego polecenia.",
                    RESOLVE_STEP, {"choice": "deny", "step": name}),
        ]
    else:
        why = ("Tego polecenia nie da się dopisać do listy dozwolonych: Tide zatwierdza tylko pojedyncze, "
               "niezabronione polecenia o rozsądnej długości." if command else "")
        options = [
            _option("deny", "Kontynuuj bez tego polecenia",
                    "Krok wznowi się bez niego; jeśli czegoś zabraknie, poprosi o pojedyncze polecenia.",
                    RESOLVE_STEP, {"choice": "deny", "step": name}, recommended=True),
        ]
    reply = {"method": RESOLVE_STEP, "params": {"choice": "deny", "step": name}, "field": "answer"}
    return _decision(st, name, step, "permission", happened, why, options, reply, "",
                     _step_technical(step, done))


BLOCKED_BY_SCOPE = re.compile(r"ponowienie (?:kroku )?nic|nie zamknie|wymagane działanie koordynatora|decyzj\w* "
                              r"(?:właściciela|koordynatora|użytkownika)|poza zakresem (?:tego )?kroku|brak dostawy|"
                              r"zakres (?:tego )?kroku nie pozwala|nie ponawiać|bloker\w* .{0,40}niespełnion", re.I)


def _error(st, name, step, done) -> dict:
    """Błąd kroku po automatycznym ponowieniu: co się stało, czyja to wina (`fault` z dziennika kroku, PT-125)
    i polecana akcja zależna od winy. Przy `unknown` okno mówi wprost, że nie wiadomo."""
    reason = done.get("reason") or ""
    label = REASON_LABELS.get(reason, reason)
    found = faults.of_finished(done)
    fault, cause = found["fault"], found["cause"]
    what = faults.cause_label(cause, REASON_LABELS) or label
    attempts = _attempts(st, name)
    who = _who(step, True)
    tries = f" (próba {attempts})" if attempts > 1 else ""
    provider = PROVIDERS.get(step.get("provider") or "", step.get("provider") or "") or "dostawcy"
    if fault == "tide":
        happened = f"{who} zakończył się bez wyniku{tries}: {what}." if what else ""
        why = "To błąd Tide, nie pracy kroku. Zmiany kroku zostają w jego worktree; zmiana modelu tu nie pomoże."
    elif fault == "provider":
        happened = f"{who} przerwał się po stronie {provider}{tries}: {what}." if what else ""
        why = (f"To błąd dostawcy ({provider}), nie pracy kroku. Zmiany kroku zostają w jego worktree."
               + (" Ponowienie u tego samego dostawcy nie pomoże, dopóki autoryzacja nie zostanie naprawiona."
                  if cause == "auth" else ""))
    elif fault == "model":
        happened = f"{who} oddał niepoprawny wynik{tries}: {what}." if what else ""
        why = "To błąd wykonawcy, nie Tide: Tide ponowił krok raz sam, a wynik dalej był niepoprawny."
    elif fault == "user":
        happened = f"{who} przerwano{tries}: {what}." if what else ""
        why = "Krok przerwano ręcznie; zmiany zostają w worktree kroku."
    else:
        happened = f"{who} zakończył się błędem{tries}: {label}." if label else ""
        why = ("Nie wiadomo, czyja to wina: dziennik kroku tego nie rozstrzyga. Zmiany kroku zostają w jego worktree; "
               "jeśli podejrzewasz Tide, opisz problem i ponów.") if label else ""
    other_recommended = fault == "model" or (fault == "provider" and cause == "auth")
    # Ten sam powód co w poprzedniej próbie (K-16/LEGAL-67, 03.11: to samo menu po raz kolejny) — samo ponowienie nic
    # nie zmieni, więc okno go nie poleca i mówi, co zadziała.
    parent = st.finished.get(step.get("parentStep") or "") or {}
    # Tylko opisowe powody wykonawcy (krótkie kody jak `exit` powtarzają się naturalnie).
    repeated = len(reason) > 40 and fault in ("unknown", "model") and \
        " ".join(str(parent.get("reason") or "").split())[:120] == " ".join(reason.split())[:120]
    # Agent sam pisze, że ponowienie nie pomoże albo że potrzebna jest decyzja / praca poza krokiem (04.11, K-41/KAMP-13:
    # „wymagane działanie koordynatora”, a okno polecało „Ponów krok”).
    blocked = fault in ("unknown", "model") and bool(BLOCKED_BY_SCOPE.search(reason))
    repeated = repeated or blocked
    if blocked:
        why = ("Wykonawca zgłasza, że ponowienie nic nie da: brakuje czegoś spoza tego kroku (inne repozytorium, "
               "decyzja o zakresie, praca, której krok nie może wykonać). Wybierz: odrzuć ticket i przenieś pracę gdzie "
               "indziej, albo „Ponów z instrukcją” — napisz, jak ma ograniczyć zakres.")
    elif repeated:
        why = ("Ten sam powód co w poprzedniej próbie — samo ponowienie nic nie zmieni. Napisz wykonawcy, co ma zrobić "
               "inaczej („Ponów z instrukcją”), albo zmień wykonawcę.")
    options = [
        _option("retry", "Ponów krok",
                "Ten sam wykonawca spróbuje jeszcze raz od zmian zapisanych w worktree kroku." if fault == "tide" else
                "Ten sam wykonawca zacznie od zmian zapisanych w worktree kroku.",
                RESOLVE_STEP, {"choice": "retry", "step": name}, recommended=not other_recommended and not repeated),
        _option("change-executor", "Ponów innym wykonawcą…",
                "Pomoże tylko wtedy, gdy przyczyną był model — tu nie był." if fault == "tide" else
                "Wybierz model; nowy wykonawca zacznie od zmian zapisanych w worktree kroku.",
                RESOLVE_STEP, {"choice": "change-executor", "step": name, "executor": ""},
                recommended=other_recommended),
        *([_option("reject", "Odrzuć ticket",
                   "Ticket wypada z karty (zmiany zostają na jego gałęzi); tickety, które na niego czekały, ruszą dalej.",
                   RESOLVE_STEP, {"choice": "reject", "step": name})] if step.get("ticket") else []),
    ]
    waits = wait_ticket_options(st, name, step, reason)
    if waits and blocked:
        # Wykonawca pisze, że brakuje pracy innego ticketu tej karty (06.11, K-38/PT-191 → PT-171): czekanie, nie ponowienie.
        options = [{**o, "recommended": False} for o in options]
        waits[0]["recommended"] = True
        why = ("Wykonawca zgłasza, że brakuje pracy innego ticketu tej karty. „Czekaj na …” wstrzyma ten krok bez "
               "pytań; Tide wznowi go sam po scaleniu tamtego ticketu do gałęzi karty.")
    options = options + waits
    # „Ponów z instrukcją” (03.11, K-16/LEGAL-67): agent kręcił się na tym samym powodzie, a użytkownik nie miał jak
    # mu powiedzieć, co zrobić inaczej. Tekst trafia do wznowionego kroku jako polecenie użytkownika.
    reply = {"method": RESOLVE_STEP, "params": {"choice": "retry", "step": name}, "field": "answer",
             "label": "Ponów z instrukcją", "hint": "Np. „B1 jest odroczone do nowego ticketu — zakończ krok z notką”.",
             "effect": "Ten sam wykonawca wznowi krok z Twoją instrukcją."}
    if fault == "tide":
        # „Opisz problem i ponów”: opis trafia do karty z błędem w Backlogu plasma-tide (z podglądem), krok rusza znowu.
        reply = {"method": REPORT_FAULT, "params": {"step": name}, "field": "description",
                 "preview": PREVIEW_FAULT, "label": "Opisz problem i ponów",
                 "hint": "Co zauważyłeś? Np. „po aktualizacji Tide to samo przy review ticketów”.",
                 "effect": f"Opis trafi do karty z błędem w Backlogu {faults.REPORT_PROJECT} razem z wycinkiem "
                           "dziennika kroku (bez sekretów); krok ruszy ponownie."}
    note = caretaker.unavailable_note(st, name)
    if note:
        why = (why or GENERIC["error"][1]) + f" ({note[:1].upper()}{note[1:]}.)"
    technical = _step_technical(step, done)
    technical.update(attempts=attempts, stalledSince=done.get("stalledSince") or "", fault=fault,
                     faultCause=cause, faultLabel=faults.FAULT_LABELS.get(fault, fault),
                     signature=faults.signature(step.get("kind") or "", fault, cause))
    return _decision(st, name, step, "error", happened, why, options, reply, fault, technical)


def _transfer(st, name, step, done) -> dict:
    happened = f"{_who(step, True)} zatrzymano, żeby przekazać go innemu dostawcy." if step.get("kind") else ""
    options = [
        _option("transfer", "Przekaż wybranemu wykonawcy…",
                "Wykonawca innego dostawcy dokończy krok od commita WIP w tym samym worktree.",
                RESOLVE_STEP, {"choice": "transfer", "step": name, "executor": ""}, recommended=True),
    ]
    return _decision(st, name, step, "transfer", happened, "", options, None, "", _step_technical(step, done))


def _stopped(st, name, step, done) -> dict:
    happened = (f"{_who(step, True)}: rozmowa w Konsoli została zamknięta przed zapisaniem wyniku."
                if step.get("kind") else "")
    options = [
        _option("background", "Wznów w tle", "Krok wróci do pracy w tle w tej samej sesji, od miejsca przerwania.",
                RESOLVE_STEP, {"choice": "background", "step": name}, recommended=True),
        _option("enter", "Wróć do rozmowy", "Krok otworzy się w Konsoli w tej samej sesji.",
                ENTER_STEP, {"step": name}),
    ]
    return _decision(st, name, step, "stopped", happened, "", options, None, "", _step_technical(step, done))


# --- review, plan, YouTrack ----------------------------------------------------------------------

def _review(st, ticket, round_no, question) -> dict:
    verdict = st.verdicts.get((ticket, round_no)) or {}
    step = st.steps.get(verdict.get("step") or "") or next(
        (s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and s.get("kind") == "review"), {})
    happened = (f"Review {ticket} po {round_no} rundach nadal wskazuje ważne uwagi."
                if ticket and round_no else "")
    target = {"ticket": ticket, "round": round_no}      # PT-316: okno odpowiada na to pytanie, nie na „ostatnie”
    options = [
        _option("retry", "Ponów poprawki", "Autor dostanie kolejną rundę poprawek, a recenzent sprawdzi je znowu.",
                RESOLVE_REVIEW, {"choice": "retry", **target}, recommended=True),
        # Przywrócone po K-35 (04.11): usługa przyjmuje „accept”, a okno decyzji nie miało tej opcji. Dla uwag, które
        # są środowiskowe albo drobne — ticket trafia na gałąź karty mimo werdyktu recenzenta.
        _option("accept", "Scal mimo uwag", "Ticket trafi na gałąź karty mimo uwag recenzenta; użyj, gdy uwagi dotyczą "
                "środowiska albo są drobne.", RESOLVE_REVIEW, {"choice": "accept", **target}),
        _option("reject", "Odrzuć ticket", "Ticket nie trafi na gałąź karty; jego gałąź zostaje do wglądu.",
                RESOLVE_REVIEW, {"choice": "reject", **target}),
    ]
    technical = {"ticket": ticket, "round": round_no, "question": _text(question.get("question")),
                 **{key: verdict.get(key) for key in ("verdict", "blockers", "majors", "nits") if key in verdict}}
    return {"id": f"review:{ticket}:{round_no}", "step": step.get("step") or "", "stepKind": "review",
            "ticket": ticket, "executor": step.get("executor") or "", "kind": "review",
            **_sentences("review", happened, ""), "options": options, "freeText": False, "reply": None,
            "fault": "", "technical": technical}


def _plan_fix_decisions(st: FlowState) -> list[dict]:
    """Druga nieudana poprawa formatu planu (PT-146) nie ma decyzji z samego stanu kroku (`done`, bez propozycji).
    Decyzja powstaje, gdy opiekun oddał problem (`caretaker.escalated`, także po limicie działań) albo nie działa
    przy tej karcie; opcja „Ponów z instrukcją” to `ResolveCardStep retry` na kroku `plan-fix`."""
    found = []
    for name in caretaker.plan_fix_waiting(st):
        if caretaker.hidden(st, name):
            continue
        step = st.steps.get(name) or {}
        errors = caretaker.plan_fix_errors(st, {"step": name, "decision": ""})
        base = {"id": f"step:{name}", "step": name, "stepKind": step.get("kind") or "", "ticket": "",
                "executor": step.get("executor") or "", "kind": "plan-fix-failed",
                **_sentences("plan-fix-failed", "", ""),
                "options": [_option("retry", "Ponów poprawę z instrukcją…",
                                    "Planujący poprawi plan jeszcze raz z Twoją instrukcją i błędami formatu.",
                                    RESOLVE_STEP, {"choice": "retry", "step": name}, recommended=True)],
                "freeText": True, "fault": "model",
                "reply": {"method": RESOLVE_STEP, "params": {"choice": "retry", "step": name}, "field": "answer",
                          "label": "Ponów z instrukcją", "effect": "Ten sam planujący poprawi plan z Twoją instrukcją."},
                "technical": {**_step_technical(step, st.finished.get(name) or {}), "errors": errors}}
        made = caretaker.annotate(st, base, name)
        if "caretaker" in made or caretaker.local_only(st):
            found.append(made)
    return found


def _plan_decision(st: FlowState) -> dict | None:
    if ((st.started or {}).get("entry") not in ("plan", "planning") and
            (st.plan_revision or {}).get("kind") != "append") or not st.plan_proposed:
        return None
    if any(s.get("kind") in PLAN_WORK_KINDS for s in st.running()):
        # Poprawa albo review planu trwa w tle — decyzja przyjdzie, gdy się skończą (K-38, 03.11).
        return None
    proposal, review, revision = st.plan_proposed, st.plan_reviewed or {}, st.plan_revision or {}
    rows = proposal.get("rows") or []
    planner = proposal.get("planner") or ""
    listed = review_findings.lenient(review.get("findings"))
    technical = {"planner": planner, "reviewer": review.get("reviewer") or "",
                 "verdict": review.get("verdict") or "", "findings": review_findings.render(listed),
                 "tickets": len(rows), "errors": list((st.plan_checked or {}).get("errors") or [])}
    base = {"step": "", "stepKind": "", "ticket": "", "executor": planner, "fault": ""}
    if revision.get("failed"):
        technical["errors"] = list(revision.get("errors") or technical["errors"])
        return {"_seq": revision.get("seq"), "id": f"plan-revision-failed:{revision.get('seq') or 0}", **base,
                "kind": "plan-revision-failed",
                **_sentences("plan-revision-failed", "", ""),
                "options": [
                    _option("return", "Odeślij plan ponownie z tymi samymi uwagami",
                            "Planujący dostanie uwagi jeszcze raz i poprawi plan w tle.",
                            EDIT_PLAN, {"action": "return", "feedback": _text(revision.get("feedback"))},
                            recommended=bool(_text(revision.get("feedback")))),
                    _option("discard-revision", "Odrzuć poprawkę",
                            "Zostaje plan sprzed poprawki; możesz go zatwierdzić albo odesłać z nowymi uwagami.",
                            EDIT_PLAN, {"action": "discard-revision"}),
                ],
                "freeText": True, "reply": {"method": EDIT_PLAN, "params": {"action": "return"}, "field": "feedback"},
                "technical": technical}
    if revision.get("pending") and st.plan_reviewed:
        technical["difference"] = revision.get("difference") or []
        manual = revision.get("kind") == "append"
        return {"_seq": max(int(revision.get("seq") or 0), int(review.get("seq") or 0)),
                "id": f"plan-revision:{revision.get('seq') or 0}", **base, "kind": "plan-revision",
                **_sentences("plan-revision", "", ""),
                "options": [
                    _option("approve", "Zatwierdź różnicę planu", "Zmieniony plan zastąpi obecny; tickety ruszą według nowych fal.",
                            APPROVE_PLAN, {}, recommended=True),
                    _option("discard-revision", "Odrzuć dopisanie",
                            "Dotychczasowy plan zostaje. Poprawiony ticket możesz ponownie zapisać w formularzu.",
                            EDIT_PLAN, {"action": "discard-revision"}) if manual else
                    _option("return", "Odeślij z uwagami recenzenta",
                            "Planujący poprawi plan jeszcze raz w tle.",
                            EDIT_PLAN, {"action": "return", "feedback": technical["findings"]}),
                ],
                "freeText": not manual, "reply": {} if manual else
                    {"method": EDIT_PLAN, "params": {"action": "return"}, "field": "feedback"},
                "technical": technical}
    if revision and not revision.get("pending") and not revision.get("failed"):
        # Poprawa planu po odesłaniu trwa w tle — stary plan nie wraca jako decyzja (K-40, 03.11: okno pokazywało go
        # z przyciskami przez cały czas poprawy, a kolejne kliknięcie kończyło się „poprawa planu już trwa”).
        return None
    if st.plan_approved:
        return None
    if not st.plan_reviewed:
        wait = st.plan_review_wait or st.waiting.get(("", "plan-review")) or {}
        if not wait or st.plan_reviewer or any(s.get("kind") == "plan-review" and s.get("step") not in st.finished
                                                for s in st.steps.values()):
            return None
        reason = _text(wait.get("reason"))
        technical["waitReason"] = reason
        return {"_seq": wait.get("seq"), "id": f"plan-reviewer:{proposal.get('seq') or 0}", **base,
                "kind": "plan-reviewer",
                **_sentences("plan-reviewer", f"Review planu czeka: {reason}." if reason else "",
                             "Wybierz innego recenzenta albo zaczekaj — bez review plan nie trafi do zatwierdzenia."
                             if reason else ""),
                "options": [
                    _option("review-executor", "Wybierz recenzenta planu…",
                            "Review zrobi wybrany model, inny niż planujący.",
                            EDIT_PLAN, {"action": "review-executor", "executor": ""}, recommended=True),
                ],
                "freeText": False, "reply": None, "fault": "provider", "technical": technical}
    if st.approval_requested and (st.started or {}).get("trackerProject"):
        return None
    verdict = review.get("verdict") if review.get("verdict") in ("APPROVE", "REQUEST_CHANGES") else ""
    changes = verdict == "REQUEST_CHANGES"
    counts = review_findings.counts(listed)
    technical.update({"blockers": counts["blocker"], "majors": counts["major"], "nits": counts["nit"]})
    reviewer = review.get("reviewer") or ""
    # Uwagi z review przed automatyczną poprawką planu: planujący już je uwzględnił, więc nie są prośbą o zmiany
    # tego planu (K-38, 03.11: okno pisało „recenzent prosi o zmiany” i polecało odesłanie poprawionego planu).
    corrected = bool(review.get("corrected")) and changes
    happened = ""
    if rows and corrected:
        happened = (f"Plan ma {_count(len(rows), 'ticket', 'tickety', 'ticketów')}; planujący poprawił go po uwagach "
                    f"recenzenta{f' ({reviewer})' if reviewer else ''} — uwagi niżej dotyczą wersji sprzed poprawki.")
    elif rows:
        happened = f"Plan ma {_count(len(rows), 'ticket', 'tickety', 'ticketów')}"
        if reviewer:
            happened += f"; recenzent ({reviewer}) " + (
                "prosi o zmiany." if changes else "go zatwierdził." if verdict else "nie wydał werdyktu.")
        else:
            happened += "."
    # Polecona akcja z werdyktu (US 11, D9): bez uwag — zatwierdzenie; z blokerem albo prośbą o zmiany — odesłanie;
    # przy nieznanym werdykcie żadna nie jest polecona (uwag bez werdyktu nie rozstrzygamy za recenzenta).
    if not verdict:
        recommend = ""
    elif corrected or not listed:
        recommend = "approve"
    elif counts["blocker"] or changes:
        recommend = "return"
    else:
        recommend = "approve"
    if corrected:
        effect = "Pierwsza fala ticketów wystartuje od razu; uwagi recenzenta są już naniesione w tym planie."
    elif counts["blocker"]:
        effect = (f"Mimo {_count(counts['blocker'], 'blokującej uwagi', 'blokujących uwag', 'blokujących uwag')} recenzenta "
                  "pierwsza fala ticketów wystartuje od razu, a uwagi nie zostaną poprawione.")
    elif listed and changes:
        effect = "Mimo uwag recenzenta pierwsza fala ticketów wystartuje od razu, a uwagi nie zostaną poprawione."
    else:
        effect = "Pierwsza fala ticketów wystartuje od razu."
    approve = _option("approve", "Zatwierdź plan i rozpocznij implementację", effect, APPROVE_PLAN, {},
                      recommended=recommend == "approve")
    # Okno zaznacza z góry blocker i major (nie nit) i odsyła tylko zaznaczone, razem z własnym tekstem.
    # Po automatycznej poprawce nic nie jest zaznaczone z góry — uwagi są już naniesione, odsyła się tylko wybrane.
    chosen = [] if corrected else [f for f in listed if review_findings.default_selected(f)]
    send_back = _option("return", "Odeślij uwagi do poprawy",
                        "Planujący poprawi plan w tle (dostanie zaznaczone uwagi i Twój tekst), a Ty wrócisz tu do zatwierdzenia.",
                        EDIT_PLAN, {"action": "return", "findings": chosen}, recommended=recommend == "return")
    options = [approve, send_back] if listed else [approve]
    return {"_seq": review.get("seq"), "id": f"plan-approval:{proposal.get('seq') or 0}", **base,
            "kind": "plan-approval", **_sentences("plan-approval", happened, ""),
            "options": _recommended_first(options), "freeText": True,
            "reply": {"method": EDIT_PLAN, "params": {"action": "return"}, "field": "feedback"},
            "findings": [{"id": f"f{n}", **f, "selected": review_findings.default_selected(f)}
                         for n, f in enumerate(listed, 1)],
            "technical": technical}


def _tracker(failure: dict) -> dict:
    error = _text(failure.get("error"))
    return {"id": f"tracker:{failure.get('action') or ''}:{failure.get('key') or ''}", "step": "", "stepKind": "",
            "ticket": "", "executor": "", "kind": "tracker",
            **_sentences("tracker", f"Operacja w YouTracku nie powiodła się: {error}" if error else "", ""),
            "options": [_option("retry", "Ponów", "Tide powtórzy nieudane operacje YouTracka.",
                                RESOLVE_STEP, {"choice": "retry"}, recommended=True)],
            "freeText": False, "reply": None, "fault": "tracker",
            "technical": {"action": failure.get("action") or "", "key": failure.get("key") or "", "error": error}}


# --- wspólne -------------------------------------------------------------------------------------

def _decision(st, name, step, kind, happened, why, options, reply, fault, technical) -> dict:
    return {"id": f"step:{name}", "step": name, "stepKind": step.get("kind") or "",
            "ticket": step.get("ticket") or "", "executor": step.get("executor") or "", "kind": kind,
            **_sentences(kind, happened, why), "options": _recommended_first(options),
            "freeText": reply is not None, "reply": reply, "fault": fault, "technical": technical}


def _sentences(kind: str, happened: str, why: str) -> dict:
    generic = GENERIC.get(kind, ("Karta czeka na Twoją decyzję.", "Bez niej karta nie pójdzie dalej."))
    return {"happened": _text(happened) or generic[0], "why": _text(why) or generic[1]}


def wait_ticket_options(st: FlowState, name: str, step: dict, text: str) -> list[dict]:
    """„Czekaj na PT-x” dla niescalonych ticketów planu tej karty wymienionych w treści pytania albo błędu (najwyżej 2)."""
    rows = (st.plan_approved or {}).get("rows") or []
    own = step.get("ticket") or ""
    mentioned = dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", str(text or "")))
    out = []
    for row in rows:
        ident = str(row.get("id") or "")
        if ident in mentioned and ident != own and ident not in st.rejected and \
                not (st.results.get(("merge", ident)) or {}).get("ok") and own not in (row.get("blockers") or []):
            out.append(_option(f"wait-ticket-{ident}", f"Czekaj na {ident}",
                               f"Krok stoi bez pytań; Tide wznowi go sam po scaleniu {ident} do gałęzi karty "
                               "i każe najpierw scalić gałąź karty do gałęzi ticketu.",
                               RESOLVE_STEP, {"choice": "wait-ticket", "step": name, "waitTicket": ident}))
    return out[:2]


def _option(ident, title, effect, method, params, *, recommended=False) -> dict:
    return {"id": ident, "title": title, "effect": effect, "recommended": bool(recommended),
            "choice": params.get("choice") or params.get("action") or ident, "params": dict(params),
            "method": method}


def _recommended_first(options: list[dict]) -> list[dict]:
    """Najwyżej jedna polecona możliwość, zawsze pierwsza; pozostałe w kolejności podanej."""
    first = next((o for o in options if o["recommended"]), None)
    rest = [{**o, "recommended": False} for o in options if o is not first]
    return [first, *rest] if first else rest


def _step_technical(step: dict, done: dict) -> dict:
    return {"step": step.get("step") or "", "stepKind": step.get("kind") or "", "ticket": step.get("ticket") or "",
            "executor": step.get("executor") or "", "provider": step.get("provider") or "",
            "model": step.get("model") or "", "mode": step.get("mode") or "background",
            "attempt": int(step.get("attempt") or 0), "parentStep": step.get("parentStep") or "",
            "result": done.get("result") or "", "reason": done.get("reason") or "",
            "reasonLabel": REASON_LABELS.get(done.get("reason") or "", done.get("reason") or ""),
            "question": done.get("question") or "", "command": done.get("command") or "",
            "canAllow": bool(done.get("canAllow", True)), "finishedAt": done.get("ts") or "",
            "notes": list(done.get("notes") or []), "stepDir": step.get("stepDir") or "",
            "worktree": step.get("worktree") or "", "branch": step.get("branch") or ""}


def _who(step: dict, capital: bool) -> str:
    word = kind_label(step.get("kind"))
    ticket = step.get("ticket") or ""
    text = f"{word} {ticket}" if ticket and word not in ("ticket",) else ticket or word
    if step.get("executor"):
        text += f" ({step['executor']})"
    return text[:1].upper() + text[1:] if capital else text


def _attempts(st: FlowState, name: str) -> int:
    """Numer próby kroku: długość łańcucha `parentStep` w obrębie tego samego rodzaju kroku."""
    kind = (st.steps.get(name) or {}).get("kind")
    count, seen, current = 0, set(), name
    while current and current not in seen and (st.steps.get(current) or {}).get("kind") == kind:
        count += 1
        seen.add(current)
        current = (st.steps.get(current) or {}).get("parentStep")
    return count


def _count(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return f"{n} {one}"
    return f"{n} {few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many}"


def _text(value) -> str:
    return " ".join(str(value or "").split())


# Wyliczenia wpisane w tekst pytania: „(a) …”, „a) …”, „**(b)** …”, „1) …” — przy co najmniej dwóch zaczynają linie.
ENUMERATION = re.compile(r"(?:(?<=\s)|^)(\*\*)?(\(?[a-hA-H1-9]\))(\*\*)?(?=\s)")
# Polecenie do skopiowania: tekst w `…`, który wygląda na polecenie powłoki (program i co najmniej jeden argument).
SHELL_COMMAND = re.compile(r"`([^`\n]{4,400})`")
SHELL_PROGRAMS = ("bash", "sh", "make", "cd", "sudo", "git", "npm", "npx", "python3", "python", "pip", "dotnet",
                  "docker", "incus", "systemctl", "busctl", "apt", "apt-get", "ollama", "kpackagetool6", "journalctl",
                  "LC_ALL=")


def layout(value) -> str:
    """Tekst pytania do okna decyzji: białe znaki ujednolicone, ale wyliczenia „(a) … (b) …” w osobnych liniach
    (04.11: pytania z opcjami a, b, c w jednym akapicie były nieczytelną ścianą tekstu)."""
    text = _text(value).replace("**", "")      # okno pokazuje zwykły tekst — gwiazdki Markdowna tylko przeszkadzają
    marks = list(ENUMERATION.finditer(text))
    if len(marks) < 2:
        return text
    out, last = [], 0
    for mark in marks:
        out.append(text[last:mark.start()].rstrip())
        last = mark.start()
    out.append(text[last:])
    return "\n".join(part for part in out if part)


def copy_commands(*texts) -> list[str]:
    """Polecenia powłoki z `…` w treści pytania i możliwości — okno pokazuje je w polu z przyciskiem „Kopiuj”."""
    found = []
    for text in texts:
        for match in SHELL_COMMAND.finditer(str(text or "")):
            command = match.group(1).strip()
            first = command.split()[0] if command.split() else ""
            if (" " in command and (first in SHELL_PROGRAMS or first.startswith("LC_ALL=") or first.startswith("./"))
                    and command not in found):
                found.append(command)
    return found[:4]
