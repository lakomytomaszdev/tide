"""Dane testu inwentarza kafelka karty (PT-505, karta K-128): `boardfix` plus karty pokrywające każdy element listy
„Kafelek karty na Tablicy” (13 punktów). Dane powstają z kopii `preview_data`, więc reszta tablicy jest ta sama
co w pozostałych testach; nic w `boardfix` się nie zmienia.

Pokrycie (punkt listy → karty):
 1  wariant zwarty w Gotowe: K-1…K-4 (K-4 ma długi tytuł na 3 linie i długi projekt),
 2  numer i projekt: każda karta,
 3  pastylki dodatkowych projektów: K-12 (dwa projekty), K-18,
 4  znacznik YouTracka we wszystkich stanach: K-13 ok ✓, K-12 pending ⟳, K-16 offline ⟳, K-7 conflict ⚠ (róża),
    K-17 error ⚠ (butter), K-19 bez stanu synchronizacji,
 5  tytuł do 4 linii i tagi z kropkami w kolorze tagu: K-20 (długi tytuł), K-12 (pilne rose), K-15 (pomysł butter),
    K-13 (tag projektu sky),
 6  znacznik fali: K-5, K-8, K-21,
 7  pasek ticketów: K-5 (każda faza i przerwa między falami), K-8 (czeka), K-21 (starsza usługa bez `phase`),
 8  postęp koordynatora: K-5, K-6, K-7, K-8,
 9  notatka każdego rodzaju: pochodna (K-5, K-8), „✓ specyfikacja gotowa” (K-22), notatka karty (K-9, K-11),
10  gałąź w Do sprawdzenia: K-7,
11  przyciski w Do sprawdzenia: K-7 („Zmiany gałęzi”, „Gotowe”),
12  „Uruchom agenta…” (K-9), „Uruchom ponownie…” (K-11), „Przejdź do agenta” (K-8 przycisk, K-5 link),
    „Otwórz w Sesjach →” (K-5, K-8),
13  stany: karta czekająca K-8, zaznaczona/fokus/najechana/przeciągana/duch — w teście.
"""
import copy
import json

from .boardfix import preview_data

EXTRA_IDS = ("K-16", "K-17", "K-18", "K-19", "K-20", "K-21", "K-22")

SYNC = {
    "ok": {"status": "ok", "issue": "LOG-131", "issueState": "Open", "via": "card", "label": "karta K-13",
           "text": "zsynchronizowane — w YouTracku „Open”", "url": "http://yt:8111/issue/LOG-131", "conflict": None},
    "pending": {"status": "pending", "issue": "LOG-150", "issueState": "Open", "via": "card", "label": "karta K-12",
                "text": "zapis do YouTracka w toku", "url": "http://yt:8111/issue/LOG-150", "conflict": None},
    "offline": {"status": "offline", "issue": "LOG-151", "issueState": "", "via": "card", "label": "karta K-16",
                "text": "YouTrack niedostępny — zapis czeka", "url": "http://yt:8111/issue/LOG-151", "conflict": None},
    "conflict": {"status": "conflict", "issue": "LOG-142", "issueState": "In Progress", "via": "card",
                 "label": "karta K-7", "url": "http://yt:8111/issue/LOG-142",
                 "text": "YouTrack: stan zmieniony ręcznie — w YouTracku „In Progress”, Tide: „Fixed”",
                 "conflict": {"youtrack": "In Progress", "tide": "Fixed"}},
    "error": {"status": "error", "issue": "LOG-152", "issueState": "Open", "via": "ticket", "label": "ticket PT-3",
              "text": "YouTrack odmówił zmiany stanu", "url": "http://yt:8111/issue/LOG-152", "conflict": None},
}

TICKETS_ALL_PHASES = [
    {"id": "PT-1", "state": "gotowe", "phase": "gotowe", "why": "scalony", "wave": 1},
    {"id": "PT-2", "state": "review", "phase": "review", "why": "review w toku", "wave": 1},
    {"id": "PT-3", "state": "w-toku", "phase": "pracuje", "why": "implementer", "wave": 2},
    {"id": "PT-4", "state": "czeka", "phase": "kolejka", "why": "w kolejce poza piaskownicą: make test", "wave": 2},
    {"id": "PT-5", "state": "czeka", "phase": "ty", "why": "pytanie o zakres", "wave": 3},
    {"id": "PT-6", "state": "todo", "phase": "todo", "why": "", "wave": 3},
    {"id": "PT-7", "state": "odrzucone", "phase": "odrzucone", "why": "poza zakresem", "wave": 3},
]


def derived(column: str, **fields) -> dict:
    return dict({"column": column}, **fields)


def inventory_data(tmp: str) -> dict:
    data = copy.deepcopy(preview_data(tmp))
    cards = {c["id"]: c for c in data["cards"]}

    def new_card(card_id: str, **fields) -> dict:
        card = copy.deepcopy(cards["K-15"])
        card.update(id=card_id, **fields)
        data["cards"].append(card)
        cards[card_id] = card
        return card

    # 1 — zwarty wariant: długi tytuł (3 linie) i długa nazwa projektu.
    cards["K-4"]["title"] = ("Unikalne filtry w wyszukiwaniu logów z wielu przebiegów naraz, z zapamiętaniem "
                             "ostatnio użytych wartości w każdej kolumnie i szybkim czyszczeniem filtrów")
    cards["K-4"]["projectName"] = "LogViewer"

    # 3, 4 — dodatkowe projekty i znaczniki YouTracka.
    cards["K-12"]["extraProjects"] = [
        {"slug": "mobileapp", "name": "mobileapp", "repo": "/r/mobileapp", "repoMissing": False},
        {"slug": "cookapp", "name": "CookApp", "repo": "/r/cookapp", "repoMissing": False}]
    cards["K-12"]["youtrack"], cards["K-12"]["youtrackSync"] = "LOG-150", SYNC["pending"]
    cards["K-13"]["youtrackSync"] = SYNC["ok"]
    cards["K-7"]["youtrack"], cards["K-7"]["youtrackSync"] = "LOG-142", SYNC["conflict"]
    new_card("K-16", title="Zapis stanu w YouTracku czeka na sieć", youtrack="LOG-151", youtrackSync=SYNC["offline"])
    new_card("K-17", title="YouTrack odrzucił zmianę stanu", youtrack="LOG-152", youtrackSync=SYNC["error"])
    new_card("K-18", title="Karta z dodatkowym projektem bez znacznika", extraProjects=[
        {"slug": "webservice", "name": "webservice", "repo": "/r/ws", "repoMissing": False}])
    new_card("K-19", title="Znacznik YouTracka bez stanu synchronizacji", youtrack="LOG-153", youtrackSync=None)

    # 5 — tytuł do 4 linii.
    new_card("K-20", title="Bardzo długi tytuł karty, który nie mieści się w czterech liniach kafelka, bo opisuje "
                           "całą zmianę wraz z jej uzasadnieniem, listą plików, ryzykiem regresji, sposobem "
                           "sprawdzenia i wszystkimi szczegółami, które ktoś wkleił do pola tytułu zamiast opisu")

    # 6, 7, 8, 9, 12 — koordynator: każda faza ticketu, przerwa między falami, link do agenta i do Sesji.
    cards["K-5"]["derived"] = derived(
        "progress", done=3, total=10, tool="claude", note="PT-3: okno tablicy w trakcie", session="claude:5f0c",
        coordinator="claude:coord-5", tickets=copy.deepcopy(TICKETS_ALL_PHASES),
        flow={"entry": "plan", "waveLabel": "fala 2 z 3", "paused": False, "timeline": [], "steps": []})
    cards["K-5"]["boardColumn"] = "progress"
    cards["K-6"]["derived"] = derived("progress", done=8, total=13, tool="codex", session="codex:019a",
                                      note="LA-035 po review OK, LA-036 w trakcie")
    cards["K-6"]["boardColumn"] = "progress"
    cards["K-8"]["derived"] = derived(
        "waiting", done=5, total=9, tool="claude", session="claude:7a21", coordinator="claude:coord-8",
        note="TOM-146: czy warunek ma działać też na archiwalnych logach?",
        tickets=[{"id": "PT-1", "state": "gotowe", "phase": "gotowe", "why": "scalony", "wave": 1},
                 {"id": "PT-2", "state": "czeka", "phase": "ty", "why": "pytanie o archiwalne logi", "wave": 1}],
        flow={"entry": "plan", "waveLabel": "fala 1 z 1", "paused": False, "timeline": [], "steps": []})
    cards["K-8"]["boardColumn"] = "waiting"
    new_card("K-21", title="Starsza usługa: ticket bez fazy", boardColumn="progress", column="todo",
             derived=derived("progress", done=1, total=3, tool="claude", session="claude:old1",
                             note="fala 1 z 1",
                             tickets=[{"id": "PT-8", "state": "w-toku", "wave": 1},
                                      {"id": "PT-9", "state": "gotowe", "wave": 1},
                                      {"id": "PT-10", "state": "czeka", "wave": 1}],
                             flow={"entry": "plan", "waveLabel": "fala 1 z 1", "paused": False, "timeline": [],
                                   "steps": []}))

    # 9 — notatka „✓ specyfikacja gotowa” z `spec`, notatka karty w Do zrobienia (wyciszona).
    new_card("K-22", title="Specyfikacja gotowa bez własnej notatki", note="", spec="docs/agents/specs/k-22.md")
    cards["K-9"]["note"] = "przeniesiona, agent jeszcze nie ruszył"

    # 10, 11 — Do sprawdzenia: gałąź i przyciski; 12 — Do zrobienia: uruchomienie i ponowne uruchomienie.
    cards["K-7"]["derived"] = derived(
        "review", done=4, total=4, tool="claude", session="claude:rev7",
        note="✓ review OK · 4 commity, gałąź niescalona", branch="hotfix/tom-142-unique",
        flow={"entry": "plan", "cardBranch": "card/k-7-hotfix", "paused": False, "timeline": [], "steps": []})
    cards["K-7"]["boardColumn"] = "review"
    cards["K-11"]["launched"] = "2026-10-27T11:00:00+01:00"
    cards["K-11"]["note"] = "bez agenta, robię sam"
    return json.loads(json.dumps(data))
