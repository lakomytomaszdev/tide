"""Przykładowa tablica do testów i podglądu widoku Tablica (jak na mocku z 27.10.2026).

build_snapshot(tmp) zakłada w katalogu tymczasowym prawdziwą tablicę modułem usługi (board/store.py,
z osobną konfiguracją gita — nigdy ~/DevWorkspace/tide-board) i zwraca jej migawkę. Z derived=True
część kart dostaje stan z koordynatora w kształcie, którego widok oczekuje od PT-5 (W toku, Czeka na
mnie, Do sprawdzenia) — dziś usługa zawsze daje derived: null, więc to tylko podgląd projektu karty.

preview_data(tmp) dokłada `cardDocs` — odpowiedzi /Board CardDocs policzone modułem usługi
(board/docs.py) z planu i specyfikacji zapisanych w tymczasowych repozytoriach i tablicy: K-5 ma plan
w repozytorium plasma-tide (`card: K-5`) i specyfikację z sekcją „Co przetestować ręcznie”, K-14 —
samą specyfikację w `docs/mobileapp/specs` tablicy.
"""
import json
import os
import subprocess
import sys

from . import REPO

sys.path.insert(0, os.path.join(REPO, "daemon"))
from tide_daemon.board.docs import CardDocs  # noqa: E402
from tide_daemon.board.store import BoardStore  # noqa: E402

NOW = 1793099040  # 2026-10-27 12:04 czasu w Warszawie
PALETTE = os.path.join(REPO, "theme", "palette", "mist.json")


def git_env(tmp: str) -> dict:
    """Tymczasowa konfiguracja gita (autor, gałąź) — bez ~/.gitconfig użytkownika."""
    os.makedirs(tmp, exist_ok=True)
    config = os.path.join(tmp, "gitconfig")
    with open(config, "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
    return {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"}


def colors() -> dict:
    with open(PALETTE, encoding="utf-8") as fh:
        return dict(json.load(fh)["pastel"])


def make_repo(path: str, env: dict, agents_docs: bool = False) -> str:
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "-C", path, "init", "-q"], check=True, env=env)
    if agents_docs:
        os.makedirs(os.path.join(path, "docs", "agents"), exist_ok=True)
    return path


# (kolumna, projekt, tytuł, tagi karty, notatka, youtrack, opis)
CARDS = [
    ("done", "plasma-tide", "Ekran logowania SDDM", "", "", "", ""),
    ("done", "plasma-tide", "Raport poranny: „Na daily”", "", "", "", ""),
    ("done", "plasma-tide", "Opisy kart w Tide Sesje", "", "", "", ""),
    ("done", "LogViewer", "Unikalne filtry w wyszukiwaniu", "", "", "", ""),
    ("todo", "plasma-tide", "Tablica pracy z backlogiem i tagami", "", "", "", ""),
    ("todo", "agent-team", "Biblioteka kazusów: zatwierdzanie bazowego UPN", "", "", "", ""),
    ("todo", "LogViewer", "Hotfix: unikalne wyszukiwanie fraz", "pilne", "", "TOM-142",
     "Wyszukiwanie zwraca tę samą frazę kilka razy."),
    ("todo", "LogViewer", "Wartość w logu: warunki na kolumnach przebiegu", "", "", "", ""),
    ("todo", "plasma-tide", "Własne okno i kafelek Dockera", "", "przeniesiona, agent jeszcze nie ruszył", "", ""),
    ("backlog", "CookApp", "Import przepisu z linku, zdjęcie jako zapas", "", "", "", ""),
    ("todo", "webservice", "Limit zapytań na klucz API", "", "bez agenta, robię sam", "", ""),
    ("backlog", "TimeTracker", "Miesięczne zestawienie godzin do faktury", "pilne", "", "", ""),
    ("backlog", "LogViewer", "Eksport podsumowania przebiegu do PDF", "", "", "LOG-131",
     "Podsumowanie przebiegu (czas, błędy, wykres) jako PDF do maila."),
    ("backlog", "mobileapp", "Historia diagnoz pojazdu z filtrem po VIN", "", "✓ specyfikacja gotowa", "", ""),
    ("backlog", "plasma-tide", "Tryb skupienia: wyciszone powiadomienia i przygaszony dock", "pomysł", "", "", ""),
]
PROJECTS = {  # projekt → (tagi, ai-docs w repozytorium)
    "plasma-tide": ("własne", True), "agent-team": ("własne", True), "LogViewer": ("Firma", False),
    "mobileapp": ("Firma", False), "webservice": ("Firma", False), "TimeTracker": ("własne", False),
    "CookApp": ("własne", False),
}
# Stan z koordynatora (PT-5) do podglądu: id karty → derived.
DERIVED = {
    "K-5": {"column": "progress", "done": 3, "total": 10, "tool": "claude", "note": "PT-2: okno tablicy w trakcie",
            "session": "claude:5f0c"},
    "K-6": {"column": "progress", "done": 8, "total": 13, "tool": "codex",
            "note": "LA-035 po review OK, LA-036 w trakcie", "session": "codex:019a"},
    "K-8": {"column": "waiting", "done": 5, "total": 9, "tool": "claude",
            "note": "TOM-146: czy warunek ma działać też na archiwalnych logach?", "session": "claude:7a21"},
    "K-7": {"column": "review", "done": 4, "total": 4, "tool": "claude",
            "note": "✓ review OK · 4 commity, gałąź niescalona", "branch": "hotfix/tom-142-unique"},
}


def build_board(tmp: str) -> tuple[BoardStore, dict]:
    """Prawdziwa tablica w tmp/board z projektami, tagami i kartami z mocka."""
    env = dict(os.environ, **git_env(tmp))
    old = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ.update(git_env(tmp))
    try:
        ws = os.path.join(tmp, "ws")
        clock = iter(range(NOW - 4000, NOW + 1, 40))
        store = BoardStore(os.path.join(tmp, "board"), clock=lambda: next(clock, NOW), colors=colors())
        store.upsert_tag({"name": "pilne", "color": "rose", "description": "Na już."})
        store.upsert_tag({"name": "pomysł", "color": "butter"})
        repos = {}
        for name, (tags, agents) in PROJECTS.items():
            repos[name] = make_repo(os.path.join(ws, name), env, agents)
            store.upsert_project({"name": name, "repo": repos[name], "tags": tags})
        make_repo(os.path.join(ws, "legal-agents"), env)  # kandydat „dodaj jako projekt”
        slugs = {p["name"]: p["slug"] for p in store.snapshot()["projects"]}
        for column, project, title, tags, note, youtrack, description in CARDS:
            store.create_card({"title": title, "project": slugs[project], "tags": tags, "column": column,
                               "note": note, "youtrack": youtrack, "description": description})
        return store, repos
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


PLAN_K5 = """---
tide-plan: 1
title: Tablica pracy z backlogiem i tagami
tracker: local
tracker-url:
tracker-project:
ticket-prefix: PT
coordinator: claude
card: K-5
status: active
updated: 2026-10-27T11:48:00+01:00
---

# Tablica pracy z backlogiem i tagami

## Cel

Jeden widok na pracę ze wszystkich projektów: karta to rzecz do zrobienia, a kolumny W toku, Czeka na mnie
i Do sprawdzenia wynikają z tego, co **naprawdę** robią agenci. Specyfikacja: `specs/k-5-tablica.md`.

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| PT-1 | Format repozytorium tablicy i zapis kart z commitem | gotowe | feat/pt-1-board-store | code-reviewer | review OK, scalone |
| PT-2 | Widok Tablica w oknie Tide: kolumny i przeciąganie | gotowe | feat/pt-2-board-view | code-reviewer | APPROVE po jednej rundzie poprawek |
| PT-3 | Stan karty z pracy agentów (W toku, Czeka, Do sprawdzenia) | review | feat/pt-3-derive | code-reviewer | czeka na ponowne review |
| PT-4 | Uruchomienie agenta z karty | w-toku | feat/pt-4-launch | implementer | okno „Co ma zrobić agent?” gotowe, brakuje testów |
| PT-5 | Plan i specyfikacja w panelu karty | czeka | | | czy panel ma zajmować całe okno? |
| PT-6 | Klucz `card` w standardzie planu | todo | | | |

## Decyzje

1. Pliki kart to Markdown z płaskim frontmatterem — da się je czytać i poprawiać ręcznie.
2. Usługa commituje każdą zmianę; **nigdy** nie wypycha repozytorium tablicy.
3. Kolumny wyliczane nie przyjmują kart — ręczna kolumna to wyłącznie decyzja użytkownika.

## Notatki

Szczegóły formatu: [docs/tide-daemon.md](https://example.com/tide/docs/tide-daemon.md). Zrzut z mocka
![mock tablicy](https://example.com/mock.png) został w zgłoszeniu.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.CardDocs K-5
```
"""

SPEC_K5 = """---
card: K-5
status: gotowa
updated: 2026-10-27T09:30:00+01:00
---

# K-5: Tablica pracy z backlogiem i tagami

## Po co

Tablica to główny punkt pracy: backlog ze wszystkich projektów i stan agentów bez przełączania okien.

## Wymagania

- Karta ma tytuł, projekt, tagi (tagi projektu dziedziczone), opis i notatkę.
- Przeciąganie między Backlogiem, Do zrobienia i Gotowe; kolumny wyliczane tylko z pracy agentów.
- Zapis karty to osobny commit w lokalnym repozytorium tablicy, bez remote.

## Co przetestować ręcznie i jak

1. Przeciągnij kartę z Backlogu do Do zrobienia — pojawia się okno „Co ma zrobić agent?”.
2. Zmień tytuł w panelu i zapisz Ctrl+Enter — w `git log` tablicy jest commit „K-5: update title”.
3. Zamknij usługę i edytuj plik karty ręcznie — po starcie widać commit „sync external changes”.

## Poza zakresem

Import z YouTracka (PT-7) i synchronizacja w drugą stronę.
"""

SPEC_K14 = """---
card: K-14
status: szkic
---

# Historia diagnoz pojazdu z filtrem po VIN

## Wymagania

- Lista diagnoz jednego pojazdu, najnowsze na górze, filtr po fragmencie VIN.
- Bez sieci: dane z lokalnej bazy aplikacji.
"""


def card_docs(tmp: str, snap: dict) -> dict:
    """Odpowiedzi CardDocs dla K-5 i K-14 z plików zapisanych w repozytorium plasma-tide i w tablicy."""
    ws, board = os.path.join(tmp, "ws"), os.path.join(tmp, "board")
    files = {os.path.join(ws, "plasma-tide", "docs", "agents", "plans", "2026-10-27-tablica.md"): PLAN_K5,
             os.path.join(ws, "plasma-tide", "docs", "agents", "specs", "k-5-tablica.md"): SPEC_K5,
             os.path.join(board, "docs", "mobileapp", "specs", "k-14-historia-diagnoz.md"): SPEC_K14}
    for path, text in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    reader = CardDocs(board)
    projects = {p["slug"]: p for p in snap["projects"]}
    out = {}
    for card in snap["cards"]:
        if card["id"] in ("K-5", "K-14"):
            out[card["id"]] = reader.read(card, projects.get(card["project"]))
    return out


def build_snapshot(tmp: str, derived: bool = False) -> dict:
    store, _repos = build_board(tmp)
    snap = store.snapshot(repos=[os.path.join(tmp, "ws", "legal-agents")])
    if derived:
        for card in snap["cards"]:
            if card["id"] in DERIVED:
                card["derived"] = dict(DERIVED[card["id"]])
                card["boardColumn"] = card["derived"]["column"]
    return snap


def preview_data(tmp: str, derived: bool = True) -> dict:
    """Dane do --data: migawka jak u użytkownika (~/DevWorkspace/tide-board, zapis 12:04), z „now”
    i „home” — powtarzalny zrzut niezależny od zegara i katalogu tymczasowego."""
    snap = build_snapshot(tmp, derived=derived)
    snap["cardDocs"] = card_docs(tmp, snap)
    home = "/home/user"
    real = os.path.join(tmp, "")

    def fake(path):
        return path.replace(os.path.join(real, "board"), home + "/DevWorkspace/tide-board") \
                   .replace(os.path.join(real, "ws"), home + "/DevWorkspace")

    snap = json.loads(fake(json.dumps(snap, ensure_ascii=False)))
    snap["lastCommit"]["ts"] = NOW
    snap["now"] = NOW + 30
    snap["home"] = home
    return snap


def empty_snapshot(tmp: str) -> dict:
    """Migawka katalogu, w którym tablicy jeszcze nie ma (przed pierwszą kartą)."""
    store = BoardStore(os.path.join(tmp, "nowa-tablica"), colors=colors())
    return store.snapshot(repos=[make_repo(os.path.join(tmp, "ws2", "plasma-tide"), dict(os.environ, **git_env(tmp)))])


if __name__ == "__main__":  # podgląd: python3 -m apps.board.tests.boardfix KATALOG [--derived]
    print(json.dumps(preview_data(sys.argv[1], "--derived" in sys.argv), ensure_ascii=False, indent=1))
