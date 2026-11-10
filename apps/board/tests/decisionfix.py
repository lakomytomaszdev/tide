"""Scenariusz F makiety K-35 (docs/mockups/card-decision-and-full-view.html, gałąź docs/card-decision-ui-mockup):
dwa tickety tej samej karty pytają naraz, z opcjami z result.json — #3 (Sonnet) o 11:31, #2 (Opus) o 11:38.

Decyzje nie są wpisane ręcznie: dziennik karty z publicznymi zdarzeniami przechodzi przez `fold` i `build_decisions`
usługi (PT-123), więc okno dostaje dokładnie kontrakt `derived.flow.decisions[]`. `scenario_f(data)` wkłada je
do karty K-8 (kolumna „Czeka na mnie”) w kopii danych podglądu z boardfix.preview_data.

Pełna karta (PT-128): `full_c(data)` i `full_d(data)` wkładają do K-8 całe `derived` policzone przez usługę
(`fold` + `derived`) z dzienników scenariuszy C (plan do zatwierdzenia) i D (praca w toku, fala 2 z 4).
"""
import json
import os
import sys

from . import REPO

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.board.chain import describe  # noqa: E402
from tide_daemon.cardflow.decisions import build_decisions  # noqa: E402
from tide_daemon.cardflow.state import derived, fold  # noqa: E402

CARD = "K-8"

Q3 = ("Czy mogę uruchomić narzędzie do zbierania wiadomości poza piaskownicą? "
      "Potrzebuję odczytu ~/.claude/projects i ~/.codex/sessions.")
Q3_OPTIONS = [
    {"title": "Tylko dzienniki Claude", "effect": "Bez ~/.codex/sessions; pomiar obejmie jednego dostawcę."},
    {"title": "Tak, uruchom poza piaskownicą", "recommended": True,
     "effect": "Narzędzie przeczyta dzienniki raz; wynik zostaje w katalogu kroku, nie w repozytorium."},
    {"title": "Nie, zbuduj zestaw z testów",
     "effect": "Bez prywatnych danych; pomiar będzie mniejszy i oznaczony jako szacunkowy."},
]
Q2 = ("Przyczyna to wyścig między zapisem pytania a liczeniem kolumny. "
      "Naprawić w usłudze czy tylko dopisać test, który to pokazuje?")
Q2_OPTIONS = [
    {"title": "Napraw z testem", "recommended": True,
     "effect": "Opus zmieni liczenie kolumny i doda test regresji; review zrobi Sonnet."},
    {"title": "Tylko test i dowody",
     "effect": "#2 skończy się testem odtwarzającym błąd; naprawa trafi do nowego ticketu."},
]


def journal() -> list[dict]:
    """Dziennik karty: fala 2 z #2 (Opus) i #3 (Sonnet); #3 pyta pierwszy, #2 po nim."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    def step(name, ticket, executor, model, ts):
        add("step.started", ts, step=name, ticket=ticket, kind="ticket", executor=executor, provider="anthropic",
            model=model, attempt=0, parentStep="", stepDir=f"/wt/{name}.step", worktree=f"/wt/{name}",
            branch=f"ticket/{ticket.strip('#')}")

    add("card.started", "09:05:00", entry="now", executor="Sonnet", program="claude", provider="anthropic")
    step("K-8-ticket-2-0", "#2", "Opus", "opus", "10:05:00")
    step("K-8-ticket-3-0", "#3", "Sonnet", "sonnet", "10:05:10")
    add("step.finished", "11:31:12", step="K-8-ticket-3-0", ticket="#3", result="question", question=Q3,
        options=Q3_OPTIONS)
    add("step.finished", "11:38:40", step="K-8-ticket-2-0", ticket="#2", result="question", question=Q2,
        options=Q2_OPTIONS)
    return items


def decisions(events: list[dict] | None = None) -> list[dict]:
    """Decyzje policzone przez usługę z dziennika (domyślnie scenariusza F)."""
    return build_decisions(fold(journal() if events is None else events))


def without_options(events: list[dict]) -> list[dict]:
    """Ten sam dziennik w starym formacie pytania (bez opcji w result.json)."""
    return [{k: v for k, v in e.items() if k != "options"} for e in events]


# --- Scenariusz C: plan do zatwierdzenia (PT-127) -------------------------------------------------

FINDINGS_C = [
    {"severity": "major", "tickets": ["#5", "#7", "#8"],
     "text": "#5, #7 i #8 zmieniają okno zlecenia i panel karty w tej samej fali. Przy scalaniu do gałęzi karty "
             "grozi konflikt — daj #7 i #8 bloker #5 albo przenieś je do fali 4."},
    {"severity": "major", "tickets": ["#3"],
     "text": "#3 poprosi o zgodę na odczyt dzienników sesji poza piaskownicą, a kartka nie mówi, co zrobić "
             "przy odmowie. Dopisz wariant „bez dzienników” z mniejszym pomiarem."},
    {"severity": "nit", "tickets": ["PT-42"],
     "text": "PT-42: uwagi mówią o roli classify, ale kryteria nie mają testu kolejki z pamięcią na wiadomość."},
]


def journal_c(findings=None, verdict="REQUEST_CHANGES") -> list[dict]:
    """Dziennik karty: Opus zaproponował 9 ticketów, Sonnet prosi o zmiany (2 ważne, 1 drobna)."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    add("card.started", "09:05:00", entry="plan", executor="Opus", program="claude", provider="anthropic")
    add("plan.proposed", "14:58:00", planner="Opus",
        rows=[{"workId": f"#{n}", "title": f"Ticket {n}", "blockers": [] if n == 1 else ["#1"]} for n in range(1, 10)])
    add("plan.reviewed", "23:40:00", reviewer="Sonnet", verdict=verdict,
        findings=FINDINGS_C if findings is None else findings, corrected=False)
    return items


def decisions_c(events: list[dict] | None = None) -> list[dict]:
    """Decyzja zatwierdzenia planu policzona przez usługę z dziennika scenariusza C."""
    return build_decisions(fold(journal_c() if events is None else events))


def scenario_c(data: dict, items: list[dict] | None = None) -> dict:
    """Kopia danych podglądu z decyzją „Plan do zatwierdzenia” w karcie K-8 (kolumna „Czeka na mnie”)."""
    return scenario_f(data, decisions_c() if items is None else items)


# --- Scenariusz B: błąd Tide (PT-125) -------------------------------------------------------------

def journal_b() -> list[dict]:
    """Scenariusz B makiety (PT-125): review planu (Haiku) dwa razy bez wyniku — piaskownica odmówiła zapisu pliku
    wyniku, Tide ponowił krok raz sam. `fault` i przyczyna jak w zakończeniu kroku liczonym z dziennika kroku."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    add("card.started", "14:00:00", entry="plan", executor="Opus", program="claude", provider="anthropic")
    add("plan.proposed", "14:58:00", planner="Opus",
        rows=[{"workId": f"#{n}", "title": f"Ticket {n}", "blockers": []} for n in range(1, 10)])
    for attempt, (start, end) in enumerate((("23:00:40", "23:01:16"), ("23:01:16", "23:02:00"))):
        name = f"{CARD}-plan-{attempt + 1}"
        add("step.started", start, step=name, ticket="", kind="plan-review", executor="Haiku", provider="anthropic",
            model="haiku", attempt=attempt, parentStep=f"{CARD}-plan-1" if attempt else "",
            stepDir=f"/wt/{name}.step", worktree="/wt/k-8", branch="card/k-8")
        add("step.finished", end, step=name, ticket="", result="error", reason="no-result", fault="tide",
            faultCause="write-denied")
    return items


def scenario_b(data: dict) -> dict:
    """Kopia danych podglądu z decyzją scenariusza B w karcie K-8 (decyzja z `build_decisions` usługi)."""
    return scenario_f(data, decisions(journal_b()))


# --- Pełna karta (PT-128): całe `derived` z usługi dla scenariuszy C i D -----------------------------

# Tickety K-33 z makiety: (id, tytuł, autor, recenzent, blokery) — 9 ticketów w 4 falach.
TICKETS_D = [
    ("#1", "Integracja K-28 i K-29 na gałęzi karty", "Sonnet", "Opus", []),
    ("#2", "Diagnoza „Do sprawdzenia” zamiast „Czeka na mnie”", "Opus", "Sonnet", ["#1"]),
    ("#3", "Zestaw prawdziwych wiadomości agentów do rozpoznawania pytań", "Sonnet", "Opus", ["#1"]),
    ("#4", "Planowanie w kilku projektach", "Opus", "Sonnet", ["#1"]),
    ("PT-42", "Rozpoznawanie pytań: drugie zdanie modelu lokalnego", "Sonnet", "Opus", ["#1", "#3"]),
    ("#5", "Zatwierdzenie planu grupy zakłada karty rodzeństwa", "Opus", "Sonnet", ["#4"]),
    ("#7", "Podpowiedź projektu w oknie zlecenia", "Sonnet", "Opus", ["#4"]),
    ("#8", "Propozycja dołączenia projektu od planującego", "Sonnet", "Opus", ["#4"]),
    ("#6", "Bloker zewnętrzny: ticket startuje po tickecie innego projektu", "Opus", "Sonnet", ["#5"]),
]


def journal_d() -> list[dict]:
    """Scenariusz D makiety („Praca w toku”): plan zatwierdzony, #1 scalony, fala 2 pracuje — #2 (Opus) pisze,
    #3 jest w review (Opus), #4 dostaje poprawki po review (Opus); nic nie czeka na użytkownika."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    def step(name, ticket, kind, executor, ts, **extra):
        add("step.started", ts, step=name, ticket=ticket, kind=kind, executor=executor, provider="anthropic",
            model=executor.lower(), attempt=0, parentStep="", stepDir=f"/wt/{name}.step", worktree=f"/wt/{ticket}",
            branch=f"ticket/{ticket.strip('#').lower()}", **extra)

    rows = [{"id": i, "workId": i, "title": t, "executor": a, "reviewer": r, "blockers": b}
            for i, t, a, r, b in TICKETS_D]
    add("card.started", "00:40:00", entry="planning", executor="Opus", program="claude", provider="anthropic")
    step("K-8-planning-1", "", "planning", "Opus", "01:19:00")
    add("step.finished", "14:58:00", step="K-8-planning-1", ticket="", result="done")
    add("plan.proposed", "14:58:00", planner="Opus", rows=[{k: v for k, v in r.items() if k != "id"} for r in rows])
    add("plan.reviewed", "23:58:00", reviewer="Sonnet", verdict="APPROVE", findings=[], corrected=False)
    add("plan.approved", "09:05:00", map={r["id"]: r["id"] for r in rows}, rows=rows)
    step("K-8-ticket-1-0", "#1", "ticket", "Sonnet", "09:06:00")
    add("step.finished", "09:50:00", step="K-8-ticket-1-0", ticket="#1", result="done")
    step("K-8-review-1-1", "#1", "review", "Opus", "09:51:00", round=1)
    add("step.finished", "10:02:00", step="K-8-review-1-1", ticket="#1", result="done")
    add("review.verdict", "10:02:00", ticket="#1", round=1, step="K-8-review-1-1", verdict="APPROVE",
        blockers=0, majors=0, nits=2)
    add("result", "10:04:00", action="merge", key="#1", ok=True)
    step("K-8-ticket-2-0", "#2", "ticket", "Opus", "10:05:00")
    step("K-8-ticket-3-0", "#3", "ticket", "Sonnet", "10:05:00")
    step("K-8-ticket-4-0", "#4", "ticket", "Opus", "10:05:00")
    add("step.finished", "11:01:00", step="K-8-ticket-3-0", ticket="#3", result="done")
    step("K-8-review-3-1", "#3", "review", "Opus", "11:02:00", round=1)
    add("step.finished", "11:05:00", step="K-8-ticket-4-0", ticket="#4", result="done")
    step("K-8-review-4-1", "#4", "review", "Sonnet", "11:06:00", round=1)
    add("step.finished", "11:13:00", step="K-8-review-4-1", ticket="#4", result="done")
    add("review.verdict", "11:13:00", ticket="#4", round=1, step="K-8-review-4-1", verdict="REQUEST_CHANGES",
        blockers=0, majors=1, nits=0)
    step("K-8-fix-4-1", "#4", "fix", "Opus", "11:14:00", round=1)
    return items


def journal_move(steps: list[tuple], merged: tuple = ("#1",)) -> list[dict]:
    """Dziennik do zdania „Twój ruch” (PT-337): plan K-33 zatwierdzony, tickety `merged` scalone do gałęzi karty,
    potem kroki `steps` — krotki (nazwa, ticket, rodzaj, wykonawca, zdarzenia po starcie jako lista (typ, pola)).
    Każdy krok bez zdarzeń końcowych trwa."""
    full = journal_d()
    approved = next(i for i, e in enumerate(full) if e["type"] == "plan.approved")
    items = full[:approved + 1]

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    for ticket in merged:
        add("result", "10:04:00", action="merge", key=ticket, ok=True)
    for index, (name, ticket, kind, executor, after) in enumerate(steps):
        add("step.started", f"11:{index:02d}:00", step=name, ticket=ticket, kind=kind, executor=executor,
            provider="anthropic", model=executor.lower(), attempt=0, parentStep="", stepDir=f"/wt/{name}.step",
            worktree=f"/wt/{ticket or 'card'}", branch=f"ticket/{ticket.strip('#').lower() or 'card'}")
        for event_type, data in after:
            add(event_type, f"11:{index:02d}:30", step=name, ticket=ticket, **data)
    return items


# --- Scenariusz E (PT-130): limit Claude, #2 i #4 zatrzymane, Codex ma 91% limitu ---------------------

def journal_e() -> list[dict]:
    """Scenariusz E makiety („Limit dostawcy”): jak D, ale o 13:52 skończył się limit Claude — #2 (Opus, 6 plików WIP)
    i #4 (poprawki, Opus, 11 plików WIP) zatrzymane, odnowienie o 16:30; review #3 nie wystartowało."""
    items = [e for e in journal_d() if e.get("step") != "K-8-review-3-1"]

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    for seq, item in enumerate(items, 1):
        item["seq"] = seq
    for name, ticket, files in (("K-8-ticket-2-0", "#2", 6), ("K-8-fix-4-1", "#4", 11)):
        add("step.limit", "13:52:00", step=name, ticket=ticket, provider="anthropic", reset="16:30", window="session")
        add("step.finished", "13:52:01", step=name, ticket=ticket, result="error", reason="limit",
            wip={"stopped": True, "committed": True, "files": files})
    return items


def limits_snapshot(codex: float = 91, now: float = 1_800_000_000.0) -> dict:
    """Odczyt Limitów AI w scenariuszu E: Claude wyczerpany, Codex ma `codex`% limitu tygodnia."""
    def window(name, value):
        return {name: {"remaining": value, "readTs": now, "resetTs": now + 8400}}
    return {"available": True, "services": {"claude": window("session", 0), "chatgpt": window("weekly", codex)}}


def full_e(data: dict, codex: float = 91) -> dict:
    """Scenariusz E: limit Claude, K-8 w „W toku”; `otherProviders` i `limitTransfer` liczy silnik z odczytu limitów."""
    from types import SimpleNamespace
    from tide_daemon.cardflow.engine import FlowEngine
    from tide_daemon.models.executors import default_catalog
    state = fold(journal_e())
    computed = derived(state, {"column": "todo"})
    engine = SimpleNamespace(
        limits_source=lambda: limits_snapshot(codex), clock=lambda: 1_800_000_000.0, models_config=None,
        catalog=default_catalog,
        get_flow_settings=lambda: {"limitTransfer": {"mode": "ask", "executor": ""}, "limitThresholdPercent": 10})
    FlowEngine._limit_view(engine, state, computed)
    data = json.loads(json.dumps(data))
    for card in data["cards"]:
        if card["id"] == CARD:
            card["derived"] = json.loads(json.dumps(computed))
            card["boardColumn"] = computed["column"]
    return data


def full_card(data: dict, events: list[dict]) -> dict:
    """Kopia danych podglądu, w której K-8 ma całe `derived` policzone przez usługę z dziennika `events`."""
    data = json.loads(json.dumps(data))
    computed = json.loads(json.dumps(derived(fold(events), {"column": "todo"})))
    for card in data["cards"]:
        if card["id"] == CARD:
            card["derived"] = computed
            card["boardColumn"] = computed["column"]
    return data


def full_c(data: dict) -> dict:
    """Scenariusz C: plan do zatwierdzenia, recenzent prosi o zmiany (K-8 w „Czeka na mnie”)."""
    return full_card(data, journal_c())


def full_d(data: dict) -> dict:
    """Scenariusz D: praca w toku, fala 2 z 4 (K-8 w „W toku”)."""
    return full_card(data, journal_d())


# Scenariusze A1 i A2 makiety (PT-124): zgoda na łańcuch i zgoda na jedno polecenie. `commands` w zdarzeniu
# `step.finished` liczy `board.chain.describe` z zakazami projektu — tak jak usługa przy wyniku kroku.
DENIED = ["make install"]
A1_STEP = "K-8-plan-review-58"
A1_DIR = "/home/user/DevWorkspace/plasma-tide-worktrees/k-33-tide-work-closing-mvp.plan-review-K-33-plan-review-58.step"
A1_COMMAND = (f"ls -la {A1_DIR}/in {A1_DIR}/out; ls docs | head -50; "
              "git log --oneline -3 -- docs/agent-plan-standard.md; "
              "git branch --list 'codex/k28*' 'claude/k-29*' 'fix/plan-ticket*'")
A2_STEP = "K-8-ticket-3-0"
A2_COMMAND = ("python3 tools/question_corpus.py --sessions ~/.claude/projects --since 14d "
              "--out /wt/K-8-ticket-3-0.step/out/corpus.jsonl")


def a1_journal(tail: str = "") -> list[dict]:
    """A1: review planu (Haiku) prosi o zgodę na łańcuch czterech poleceń i odczyt własnego katalogu kroku;
    `tail` dokleja kolejne polecenia (np. zabronione)."""
    command = A1_COMMAND + tail
    return [
        {"v": 1, "seq": 1, "ts": "2026-11-03T14:58:00+01:00", "card": CARD, "type": "card.started", "entry": "now",
         "executor": "Opus", "program": "claude", "provider": "anthropic"},
        {"v": 1, "seq": 2, "ts": "2026-11-03T14:59:00+01:00", "card": CARD, "type": "step.started", "step": A1_STEP,
         "ticket": "", "kind": "plan-review", "executor": "Haiku", "provider": "anthropic", "model": "haiku",
         "attempt": 0, "parentStep": "", "stepDir": A1_DIR, "worktree": "/wt/k-33", "branch": "card/k-33"},
        {"v": 1, "seq": 3, "ts": "2026-11-03T15:00:36+01:00", "card": CARD, "type": "step.finished", "step": A1_STEP,
         "ticket": "", "result": "denied", "command": command, "canAllow": False,
         "commands": describe(command, denied=DENIED, step_dir=A1_DIR)},
    ]


def a1_one_journal(rest: str = "git fetch origin") -> list[dict]:
    """Review PT-124 r2 MAJOR-1: łańcuch z własnym odczytem katalogu kroku i jednym poleceniem do zgody."""
    events = a1_journal()
    command = f"ls -la {A1_DIR}/in {A1_DIR}/out && {rest}"
    events[2] = {**events[2], "command": command, "canAllow": False,
                 "commands": describe(command, denied=DENIED, step_dir=A1_DIR)}
    return events


def a2_journal() -> list[dict]:
    """A2: #3 (Sonnet) prosi o zgodę na jedno polecenie, które można dopisać do listy."""
    return [
        {"v": 1, "seq": 1, "ts": "2026-11-03T09:05:00+01:00", "card": CARD, "type": "card.started", "entry": "now",
         "executor": "Sonnet", "program": "claude", "provider": "anthropic"},
        {"v": 1, "seq": 2, "ts": "2026-11-03T10:05:00+01:00", "card": CARD, "type": "step.started", "step": A2_STEP,
         "ticket": "#3", "kind": "ticket", "executor": "Sonnet", "provider": "anthropic", "model": "sonnet",
         "attempt": 0, "parentStep": "", "stepDir": "/wt/K-8-ticket-3-0.step", "worktree": "/wt/K-8-ticket-3-0",
         "branch": "ticket/3"},
        {"v": 1, "seq": 3, "ts": "2026-11-03T11:31:12+01:00", "card": CARD, "type": "step.finished", "step": A2_STEP,
         "ticket": "#3", "result": "denied", "command": A2_COMMAND, "canAllow": True,
         "commands": describe(A2_COMMAND, denied=DENIED, step_dir="/wt/K-8-ticket-3-0.step")},
    ]


# --- Zgrupowane zgody (K-97, PT-338): 10.11, K-88 — siedem ticketów prosi o to samo polecenie poza piaskownicą ------

OUTSIDE_COMMAND = "make test-changed BASE=7fc9ea88f36d5ed3f10f0df63e9512bffc7120e5"
OUTSIDE_PATTERN = "make test-changed *"
OUTSIDE_TICKETS = tuple(f"PT-{n}" for n in range(1, 8))


def outside_step(ticket: str) -> str:
    return f"K-8-ticket-{ticket.split('-')[1]}-0"


def journal_outside(requests: list[tuple] | None = None, extra: list[dict] | None = None) -> list[dict]:
    """Dziennik karty: każdy ticket z `requests` (ticket, polecenie[, pola zdarzenia]) prosi o polecenie poza
    piaskownicą (`result: denied`, `outside: true`) — jak usługa zapisuje wynik kroku. Domyślnie siedem ticketów
    z tym samym `make test-changed`. Worktree ticketu: /wt/<ticket>. `extra` — zdarzenia dopisane na końcu."""
    if requests is None:
        requests = [(ticket, OUTSIDE_COMMAND) for ticket in OUTSIDE_TICKETS]
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-10T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    add("card.started", "09:00:00", entry="now", executor="Sonnet", program="claude", provider="anthropic")
    for index, request in enumerate(requests):
        ticket = request[0]
        add("step.started", f"10:{index:02d}:00", step=outside_step(ticket), ticket=ticket, kind="ticket",
            executor="Sonnet", provider="anthropic", model="sonnet", attempt=0, parentStep="",
            stepDir=f"/wt/{ticket}.step", worktree=f"/wt/{ticket}", branch=f"ticket/{ticket.lower()}")
    for index, request in enumerate(requests):
        ticket, command = request[0], request[1]
        fields = {"pattern": OUTSIDE_PATTERN if command.startswith("make test-changed") else "", "canAllow": True,
                  "reason": f"Testy ticketu {ticket}: pełny przebieg nie działa w piaskownicy.",
                  **(request[2] if len(request) > 2 else {})}
        add("step.finished", f"11:{index:02d}:00", step=outside_step(ticket), ticket=ticket, result="denied",
            outside=True, command=command, **fields)
    for event in extra or []:
        add(event.pop("type"), "12:00:00", **event)
    return items


# --- Odstępstwo przy błędzie domknięcia (K-97, PT-339) ------------------------------------------------------------

CLOSE_REASON = ("Pełny make test nie przeszedł: daemon/tests/test_cardflow_dbus.py i apps/board/tests/test_animations.py "
                "padły; rozliczenie w podsumowaniu.")


def journal_close_error(kind: str = "close", ticket: str = "", attempts: int = 2) -> list[dict]:
    """Krok `kind` (domyślnie domknięcie karty) kończy się błędem `attempts` razy; Tide ponawia go sam po pierwszym
    błędzie (`step.retry`). Przy `attempts=1` to pierwszy błąd przed automatycznym ponowieniem."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-10T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    add("card.started", "09:00:00", entry="now", executor="Opus", program="claude", provider="anthropic")
    root = f"K-8-{kind}-1"
    parent = ""
    for attempt in range(attempts):
        name = root if attempt == 0 else f"{root}-{attempt}"
        add("step.started", f"1{attempt}:00:00", step=name, ticket=ticket, kind=kind, executor="Opus",
            provider="anthropic", model="opus", attempt=attempt, parentStep=parent, stepDir=f"/wt/{name}.step",
            worktree="/wt/k-8", branch="card/k-8")
        add("step.finished", f"1{attempt}:30:00", step=name, ticket=ticket, result="error", reason=CLOSE_REASON)
        if attempt + 1 < attempts:
            add("step.retry", f"1{attempt}:30:01", step=name, ticket=ticket)
        parent = name
    return items


def scenario_f(data: dict, items: list[dict] | None = None) -> dict:
    """Kopia danych podglądu z decyzjami scenariusza F w karcie K-8 (albo z podanymi `items`)."""
    data = json.loads(json.dumps(data))
    for card in data["cards"]:
        if card["id"] == CARD:
            derived = dict(card.get("derived") or {})
            derived["column"] = "waiting"
            derived["flow"] = {"ticket": "#3", "decisions": decisions() if items is None else items}
            card["derived"] = derived
            card["boardColumn"] = "waiting"
    return data


# --- Opiekun karty (K-37, PT-157): decyzja błędu wraca od opiekuna z polem `caretaker` ---------------------

CARE_TARGET = "K-8-ticket-3-1"
CARE_SUMMARY = "Test pada na tym samym: brakuje dostępu do ~/.codex/sessions."
CARE_RECOMMENDATION = "Zmień wykonawcę."


def journal_caretaker(with_tried: bool = True) -> list[dict]:
    """Ticket #3 pada dwa razy; opiekun (pierwszy krok nieudany, drugi oddaje decyzję) zostawia ją z rekomendacją
    „change-executor”. Pole `caretaker` liczy usługa (`caretaker.annotate`), tak jak w dzienniku karty."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    def step(name, attempt, parent, ts, kind="ticket"):
        add("step.started", ts, step=name, ticket="#3", kind=kind, executor="Sonnet", provider="anthropic",
            model="sonnet", attempt=attempt, parentStep=parent, stepDir=f"/wt/{name}.step", worktree="/wt/3",
            branch="ticket/3")

    def care(n, name, ts):
        add("caretaker.started", ts, problem="error:K-8-ticket-3-0", key=f"caretaker:error:K-8-ticket-3-0:{n}", n=n,
            step=name, target=CARE_TARGET, root="K-8-ticket-3-0", ticket="#3", trigger="error", triggers=["error"],
            watermark=len(items))
        step(name, 0, "", ts, kind="caretaker")
        add("step.finished", ts, step=name, ticket="#3", result="done", reason="")

    add("card.started", "09:05:00", entry="now", executor="Sonnet", program="claude", provider="anthropic")
    step("K-8-ticket-3-0", 0, "", "10:05:00")
    add("step.finished", "10:20:00", step="K-8-ticket-3-0", ticket="#3", result="error", reason="awaria testowa")
    add("step.retry", "10:20:01", step="K-8-ticket-3-0", ticket="#3")
    step(CARE_TARGET, 1, "K-8-ticket-3-0", "10:21:00")
    add("step.finished", "10:40:00", step=CARE_TARGET, ticket="#3", result="error", reason="awaria testowa")
    if with_tried:
        care(1, "K-8-care-1", "10:41:00")
        add("caretaker.escalated", "10:45:01", problem="error:K-8-ticket-3-0", step="K-8-care-1", target=CARE_TARGET,
            ticket="#3", action="merge", summary="", recommendation="", optionId="", failed=True,
            error="nieznana akcja opiekuna „merge”", code="bad-caretaker-action")
    name = "K-8-care-2" if with_tried else "K-8-care-1"
    care(2 if with_tried else 1, name, "10:50:00")
    add("caretaker.escalated", "10:55:01", problem="error:K-8-ticket-3-0", step=name, target=CARE_TARGET, ticket="#3",
        action="escalate", summary=CARE_SUMMARY, recommendation=CARE_RECOMMENDATION, optionId="change-executor",
        failed=False, error="", code="")
    return items


def decisions_caretaker(with_tried: bool = True) -> list[dict]:
    """Decyzje policzone przez usługę z dziennika opiekuna."""
    return build_decisions(fold(journal_caretaker(with_tried)))


def journal_caretaker_card(trigger="flow-error", limit=False) -> list[dict]:
    """PT-154: eskalacja problemu bez własnej decyzji kroku."""
    items = [journal_caretaker(False)[0]]

    def add(kind, /, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": "2026-11-03T11:00:00+01:00",
                      "card": CARD, "type": kind, **data})

    problem = "flow-error:advance" if trigger == "flow-error" else "stalled:card"
    if trigger == "flow-error":
        add("flow.error", stage="advance", error="Nie udało się przygotować etapu karty.")
        add("flow.error-persisted", stage="advance", error="Nie udało się przygotować etapu karty.")
    else:
        add("card.stalled", reason="brak kroku i powodu czekania")
    add("caretaker.started", problem=problem, step="K-8-care-1", target="", trigger=trigger,
        decision="", n=1, watermark=len(items))
    add("step.started", step="K-8-care-1", kind="caretaker", executor="Sonnet")
    add("step.finished", step="K-8-care-1", result="done")
    add("caretaker.escalated", problem=problem, step="K-8-care-1", target="", summary=CARE_SUMMARY,
        recommendation=CARE_RECOMMENDATION, optionId="", failed=False)
    if limit:
        add("caretaker.escalated", problem=problem, step="K-8-care-1", target="", summary="Limit działań.",
            limit=True, failed=True, count=1)
    return items


# --- Testy po scaleniu (K-88, PT-320): problem bramki z wyjściami „Ponów próbę” i „Akceptuj mimo porażki” -------

MERGE_TICKET = "PT-2"
MERGE_CHECK = "PT-2:1"
MERGE_AFTER = "c0ffee" + "1" * 34


def journal_merge_check(problem: str = "merge-check-failed") -> list[dict]:
    """Bramka zamknięta próbą `PT-2:1` na wierzchołku `MERGE_AFTER`: porażka (wynik 1), odmowa zgody albo brak
    polecenia. Decyzję liczy usługa (`build_decisions`) — okno dostaje ją tak, jak z dziennika karty."""
    items = []

    def add(kind, /, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": "2026-11-10T12:00:00+01:00", "card": CARD,
                      "type": kind, **data})

    add("card.started", entry="plan", executor="Sonnet", program="claude", provider="anthropic")
    add("merge.check-armed")
    request = {"ticket": MERGE_TICKET, "check": MERGE_CHECK, "start": "a" * 40, "before": "b" * 40,
               "after": MERGE_AFTER, "mergeSeq": 2, "retryOf": ""}
    if problem == "merge-check-unavailable":
        add("merge.check-requested", **request, unavailable=True)
        return items
    command = "make test-changed BASE=" + "a" * 40
    add("merge.check-requested", **request, command=command, source="makefile")
    if problem == "merge-check-denied":
        add("step.decision", step="merge-check-PT-2-1", ticket=MERGE_TICKET, kind="merge-check", check=MERGE_CHECK,
            choice="deny", answer="", command=command, executor="")
    else:
        add("merge.check-finished", ticket=MERGE_TICKET, check=MERGE_CHECK, exit=1, log="/tmp/przebieg.log")
    return items
