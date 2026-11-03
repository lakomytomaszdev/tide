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
