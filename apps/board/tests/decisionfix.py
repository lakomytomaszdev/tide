"""Scenariusz F makiety K-35 (docs/mockups/card-decision-and-full-view.html, gałąź docs/card-decision-ui-mockup):
dwa tickety tej samej karty pytają naraz, z opcjami z result.json — #3 (Sonnet) o 11:31, #2 (Opus) o 11:38.

Decyzje nie są wpisane ręcznie: dziennik karty z publicznymi zdarzeniami przechodzi przez `fold` i `build_decisions`
usługi (PT-123), więc okno dostaje dokładnie kontrakt `derived.flow.decisions[]`. `scenario_f(data)` wkłada je
do karty K-8 (kolumna „Czeka na mnie”) w kopii danych podglądu z boardfix.preview_data.
"""
import json
import os
import sys

from . import REPO

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.board.chain import describe  # noqa: E402
from tide_daemon.cardflow.decisions import build_decisions  # noqa: E402
from tide_daemon.cardflow.state import fold  # noqa: E402

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
