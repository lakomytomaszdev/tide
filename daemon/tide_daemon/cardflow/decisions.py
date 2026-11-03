"""Decyzje karty dla okna decyzji i pełnej karty: `build_decisions(st)` → `derived.flow.decisions[]` (K-35, PT-123).

Lista mówi po ludzku, co się stało, dlaczego potrzebna jest decyzja i co da każda możliwość. Liczy się wyłącznie ze
stanu dziennika (`FlowState`), bez Qt i bez dysku. Stare pola `derived` (`action`, `question`, `canAllow`…) zostają
bez zmian; decyzje stoją obok nich, aż konsumenci przejdą na nowy kontrakt.

Decyzja:
- `id` — stały identyfikator (`step:<krok>`, `plan-approval:<seq>`, `review:<ticket>:<runda>`…),
- `step`, `stepKind`, `ticket`, `executor` — krok, którego dotyczy (puste dla decyzji samej karty),
- `kind` — rodzaj z `KINDS`; makieta K-35: A1/A2 → `permission`, B → `error` z `fault="tide"`, C → `plan-approval`,
  E → `limit`, F → `question`,
- `happened`, `why` — zdania dla użytkownika, nigdy puste (bez danych: zdanie ogólne dla rodzaju),
- `options[]` — możliwości; polecona jest najwyżej jedna i zawsze pierwsza, żadna nie jest wybrana domyślnie (D10),
- `freeText` — czy okno pokazuje pole własnej odpowiedzi; `reply` mówi, dokąd ją wysłać
  (`{method, params, field}`: tekst trafia do `params[field]`), albo jest None,
- `fault` — dla błędów: `tide`, `step`, `provider`, `tracker`; pusty napis dla pozostałych rodzajów,
- `technical{}` — szczegóły do zwiniętego bloku „Szczegóły techniczne”.

Możliwość: `{id, title, effect, recommended, choice, params, method}`. `method` to metoda /Board na D-Bus,
a `params` — gotowe żądanie bez numeru karty: dla `ResolveCardStep` (StepResolve) JSON z `choice`, `step` i ewentualnie
`answer`; dla `ResolveCardReview` sam `choice`. Pole o pustej wartości (`executor: ""`) uzupełnia okno wyborem
użytkownika.

Kolejność: najpierw decyzje karty (plan, YouTrack, kroki bez ticketu), potem tickety; w grupie najstarsza pierwsza.
"""
from __future__ import annotations

from .labels import kind_label
from .state import REASON_LABELS, FlowState

KINDS = ("question", "permission", "error", "limit", "transfer", "stopped", "review",
         "plan-approval", "plan-revision", "plan-revision-failed", "plan-reviewer", "tracker")

RESOLVE_STEP, RESOLVE_REVIEW = "ResolveCardStep", "ResolveCardReview"
APPROVE_PLAN, EDIT_PLAN, ENTER_STEP = "ApproveCardPlan", "EditCardPlan", "EnterStep"

# Zdania, gdy dziennik nie daje szczegółów (US 2) — każdy rodzaj ma swoje, nigdy pusty napis.
GENERIC = {
    "question": ("Krok zadał pytanie i czeka na odpowiedź.",
                 "Bez odpowiedzi krok nie pójdzie dalej."),
    "permission": ("Krok poprosił o zgodę na polecenie spoza listy dozwolonych.",
                   "Tide nie uruchomi tego polecenia bez Twojej decyzji, więc krok stoi."),
    "error": ("Krok zakończył się błędem, także po automatycznym ponowieniu.",
              "Tide nie ponawia kroku w nieskończoność — zdecyduj, co dalej."),
    "limit": ("Skończył się limit dostawcy i krok się zatrzymał.",
              "Tide wznowi krok po odnowieniu limitu; możesz też przenieść go do innego dostawcy."),
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
    "plan-reviewer": ("Review planu nie może wystartować.",
                      "Bez review plan nie trafi do zatwierdzenia."),
    "tracker": ("Operacja w YouTracku nie powiodła się.",
                "Karta czeka, aż przyczyna zostanie usunięta i ponowisz operację."),
}

PROVIDERS = {"anthropic": "Claude", "openai": "Codex", "local": "modelu lokalnego"}
TIDE_REASONS = {"start", "lost"}
PROVIDER_REASONS = {"limit", "local-unavailable"}
PLAN_KINDS = ("planning", "plan-fix", "plan-review", "plan-revise", "plan-amend")


def build_decisions(st: FlowState) -> list[dict]:
    """Decyzje, na które karta czeka teraz; pusta lista — nic nie czeka na użytkownika."""
    if st.started is None or st.card_finished:
        return []
    found: list[tuple[int, int, dict]] = []

    def add(group: int, seq, decision: dict) -> None:
        found.append((group, int(seq or 0), decision))

    for name in _latest_steps(st):
        made = _step_decision(st, name)
        if made:
            step = st.steps.get(name) or {}
            add(0 if not step.get("ticket") else 1, (st.finished.get(name) or {}).get("seq"), made)
    for (ticket, round_no), question in st.review_questions.items():
        if (ticket, round_no) in st.review_decisions or ticket in st.rejected:
            continue
        add(1, question.get("seq"), _review(st, ticket, round_no, question))
    plan = _plan_decision(st)
    if plan:
        add(0, plan.pop("_seq", 0), plan)
    for failure in st.tracker_failures():
        if failure.get("action") == "youtrack-note" or st.running():
            continue
        add(0, failure.get("seq"), _tracker(failure))
    found.sort(key=lambda item: (item[0], item[1]))
    return [decision for _group, _seq, decision in found]


# --- kroki ---------------------------------------------------------------------------------------

def _latest_steps(st: FlowState) -> list[str]:
    """Ostatni krok każdego ticketu (kroki planu i domknięcia mają wspólny pusty ticket)."""
    latest: dict = {}
    for name, item in st.steps.items():
        latest[item.get("ticket") or ""] = name
    return list(latest.values())


def _step_decision(st: FlowState, name: str) -> dict | None:
    step, done = st.steps.get(name) or {}, st.finished.get(name)
    if not done or name in st.superseded or step.get("ticket") in st.rejected:
        return None
    if any(other.get("parentStep") == name for other in st.steps.values()):
        return None
    result, reason = done.get("result") or "", done.get("reason") or ""
    decided = name in st.step_decisions
    if reason == "limit" and result in ("error", "stopped"):
        return None if decided else _limit(st, name, step, done)
    if reason == "transfer" and result == "stopped":
        return None if decided else _transfer(st, name, step, done)
    if decided:
        return None
    if result == "question":
        return _question(st, name, step, done)
    if result == "denied":
        return _permission(st, name, step, done)
    if result == "error" and int(step.get("attempt") or 0) >= 1:
        return _error(st, name, step, done)
    if result == "stopped" and reason == "interactive-closed":
        return _stopped(st, name, step, done)
    return None


def _question(st, name, step, done) -> dict:
    question = _text(done.get("question"))
    happened = f"{_who(step, True)} pyta: „{question}”" if question else ""
    options = [_option(f"answer-{index + 1}", raw["title"], raw["effect"], RESOLVE_STEP,
                       {"choice": "answer", "step": name, "answer": raw["title"]}, recommended=raw["recommended"])
               for index, raw in enumerate(clean_options(done.get("options")))]
    reply = {"method": RESOLVE_STEP, "params": {"choice": "answer", "step": name}, "field": "answer"}
    return _decision(st, name, step, "question", happened,
                     "Krok czeka na odpowiedź; bez niej nie pójdzie dalej." if question else "",
                     _recommended_first(options), reply, "", _step_technical(step, done))


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


def _permission(st, name, step, done) -> dict:
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


def _error(st, name, step, done) -> dict:
    reason = done.get("reason") or ""
    label = REASON_LABELS.get(reason, reason)
    fault = "tide" if reason in TIDE_REASONS else "provider" if reason in PROVIDER_REASONS else "step"
    attempts = _attempts(st, name)
    happened = f"{_who(step, True)} zakończył się błędem: {label}" if label else ""
    if happened and attempts > 1:
        happened += f" (próba {attempts})"
    why = {"tide": "To błąd Tide, nie pracy kroku. Zmiany kroku zostają w jego worktree.",
           "provider": "Wykonawca był niedostępny; zmiany kroku zostają w jego worktree.",
           "step": "Tide ponowił krok raz sam; zmiany zostają w worktree, dalej decydujesz Ty."}[fault] if label else ""
    options = [
        _option("retry", "Ponów krok", "Ten sam wykonawca zacznie od zmian zapisanych w worktree kroku.",
                RESOLVE_STEP, {"choice": "retry", "step": name}, recommended=True),
        _option("change-executor", "Ponów innym wykonawcą…",
                "Wybierz model; nowy wykonawca zacznie od zmian zapisanych w worktree kroku.",
                RESOLVE_STEP, {"choice": "change-executor", "step": name, "executor": ""}),
    ]
    technical = _step_technical(step, done)
    technical.update(attempts=attempts, stalledSince=done.get("stalledSince") or "")
    return _decision(st, name, step, "error", happened, why, options, None, fault, technical)


def _limit(st, name, step, done) -> dict:
    limit = st.limits.get(name) or {}
    provider = PROVIDERS.get(step.get("provider") or "", step.get("provider") or "")
    reset = _text(limit.get("reset"))
    happened = ""
    if provider:
        happened = f"Skończył się limit {provider}" + (f" (odnowi się o {reset})" if reset else "") + \
            f"; {_who(step, False)} zatrzymał się w trakcie."
    why = (f"Tide wznowi krok sam o {reset}; możesz też przenieść go do innego dostawcy teraz."
           if reset else "")
    options = [
        _option("wait-limit", "Poczekaj na limit",
                "Tide wznowi krok od miejsca, w którym stanął, gdy limit się odnowi.",
                RESOLVE_STEP, {"choice": "wait-limit", "step": name}, recommended=True),
        _option("transfer", "Przenieś do innego dostawcy…",
                "Wybrany wykonawca dokończy krok od commita WIP w tym samym worktree.",
                RESOLVE_STEP, {"choice": "transfer", "step": name, "executor": ""}),
    ]
    technical = _step_technical(step, done)
    wip = (st.wip_retries.get(name) or {}).get("wip") or done.get("wip") or {}
    technical.update(limitReset=reset, limitWindow=_text(limit.get("window")),
                     wipCommitted=bool(wip.get("committed")), wipFiles=wip.get("files") or 0,
                     wipError=_text(wip.get("error")))
    return _decision(st, name, step, "limit", happened, why, options, None, "provider", technical)


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
        _option("enter", "Wróć do rozmowy", "Krok otworzy się w Konsoli w tej samej sesji.",
                ENTER_STEP, {"step": name}, recommended=True),
    ]
    return _decision(st, name, step, "stopped", happened, "", options, None, "", _step_technical(step, done))


# --- review, plan, YouTrack ----------------------------------------------------------------------

def _review(st, ticket, round_no, question) -> dict:
    verdict = st.verdicts.get((ticket, round_no)) or {}
    step = st.steps.get(verdict.get("step") or "") or next(
        (s for s in reversed(list(st.steps.values())) if s.get("ticket") == ticket and s.get("kind") == "review"), {})
    happened = (f"Review {ticket} po {round_no} rundach nadal wskazuje ważne uwagi."
                if ticket and round_no else "")
    options = [
        _option("retry", "Ponów poprawki", "Autor dostanie kolejną rundę poprawek, a recenzent sprawdzi je znowu.",
                RESOLVE_REVIEW, {"choice": "retry"}, recommended=True),
        _option("reject", "Odrzuć ticket", "Ticket nie trafi na gałąź karty; jego gałąź zostaje do wglądu.",
                RESOLVE_REVIEW, {"choice": "reject"}),
    ]
    technical = {"ticket": ticket, "round": round_no, "question": _text(question.get("question")),
                 **{key: verdict.get(key) for key in ("verdict", "blockers", "majors", "nits") if key in verdict}}
    return {"id": f"review:{ticket}:{round_no}", "step": step.get("step") or "", "stepKind": "review",
            "ticket": ticket, "executor": step.get("executor") or "", "kind": "review",
            **_sentences("review", happened, ""), "options": options, "freeText": False, "reply": None,
            "fault": "", "technical": technical}


def _plan_decision(st: FlowState) -> dict | None:
    if (st.started or {}).get("entry") not in ("plan", "planning") or not st.plan_proposed:
        return None
    proposal, review, revision = st.plan_proposed, st.plan_reviewed or {}, st.plan_revision or {}
    rows = proposal.get("rows") or []
    planner = proposal.get("planner") or ""
    technical = {"planner": planner, "reviewer": review.get("reviewer") or "",
                 "verdict": review.get("verdict") or "", "findings": _text(review.get("findings")),
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
        return {"_seq": max(int(revision.get("seq") or 0), int(review.get("seq") or 0)),
                "id": f"plan-revision:{revision.get('seq') or 0}", **base, "kind": "plan-revision",
                **_sentences("plan-revision", "", ""),
                "options": [
                    _option("approve", "Zatwierdź różnicę planu", "Zmieniony plan zastąpi obecny; tickety ruszą według nowych fal.",
                            APPROVE_PLAN, {}, recommended=True),
                    _option("return", "Odeślij z uwagami recenzenta",
                            "Planujący poprawi plan jeszcze raz w tle.",
                            EDIT_PLAN, {"action": "return", "feedback": technical["findings"]}),
                ],
                "freeText": True, "reply": {"method": EDIT_PLAN, "params": {"action": "return"}, "field": "feedback"},
                "technical": technical}
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
    changes = review.get("verdict") == "REQUEST_CHANGES"
    reviewer = review.get("reviewer") or ""
    happened = ""
    if rows:
        happened = f"Plan ma {_count(len(rows), 'ticket', 'tickety', 'ticketów')}"
        if reviewer:
            happened += f"; recenzent ({reviewer}) " + ("prosi o zmiany." if changes else "go zatwierdził.")
        else:
            happened += "."
    approve = _option("approve", "Zatwierdź plan i rozpocznij implementację",
                      "Mimo uwag recenzenta pierwsza fala ticketów wystartuje od razu." if changes else
                      "Pierwsza fala ticketów wystartuje od razu.", APPROVE_PLAN, {}, recommended=not changes)
    feedback = technical["findings"]
    send_back = _option("return", "Odeślij plan z uwagami recenzenta",
                        "Planujący poprawi plan w tle, a Ty wrócisz tu do zatwierdzenia.",
                        EDIT_PLAN, {"action": "return", "feedback": feedback}, recommended=changes and bool(feedback))
    options = [approve, send_back] if feedback else [approve]
    return {"_seq": review.get("seq"), "id": f"plan-approval:{proposal.get('seq') or 0}", **base,
            "kind": "plan-approval", **_sentences("plan-approval", happened, ""),
            "options": _recommended_first(options), "freeText": True,
            "reply": {"method": EDIT_PLAN, "params": {"action": "return"}, "field": "feedback"},
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
