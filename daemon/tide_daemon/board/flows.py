"""Szablony poleceń trybów uruchomienia (pełny, lekki, tylko spec) i ich wypełnianie.

Szablon to `flows/<tryb>.md` w tablicy, a dla jednego projektu można go nadpisać plikiem
`flows/<projekt>/<tryb>.md`. Frontmatter szablonu (opcjonalny) nie trafia do polecenia.
Zastępujemy wyłącznie znane pola w klamrach (PLACEHOLDERS) — inne klamry, np. przykład JSON
w szablonie, zostają bez zmian, a wartości pól (np. opis karty) nie są już szablonem.

Pola: card_id, title, description, project, repo, branch (w trybie spec — „bez gałęzi”), tool
(claude albo codex — do `coordinator:` w planie), docs_dir (gdzie agent pisze plany i specyfikacje:
docs/agents w katalogu roboczym albo katalog w tablicy), docs_note (jedno zdanie, kto commituje te
pliki), spec (istniejąca specyfikacja karty albo „brak”), spec_file (proponowana ścieżka specyfikacji
— nazwa zaczyna się od ID karty, po niej usługa rozpoznaje gotową specyfikację), check (`auto` albo
`manual` — „Test ręczny po zakończeniu” z okna uruchomienia), check_label (to samo słowami) i finish
(jak skończyć pracę: po review AI od razu albo z listą „Co przetestować ręcznie i jak” — finish_text),
spec_commit (tryb spec: czy i jak commitować samą specyfikację — spec_commit_text).

Treści trybów prowadzą agenta przez nasz przepływ pracy: grilling (skill `grilling` w Claude Code,
w Codeksie to samo opisane słowami), specyfikacja, plan według standardu planu 1.1 z kluczem
`card:`, tickety z numerami według zasad projektu, TDD (skill `tdd`), review i pytania z ID ticketu.

Migracja: nowa tablica dostaje SEED_FLOWS, a tablica założona wcześniej ma w `flows/` dawne zaczyny.
Plik zastępujemy tylko wtedy, gdy jego treść jest bajt w bajt równa jednemu ze znanych dawnych
zaczynów (PREVIOUS_SEEDS: skróty SHA-256) — szablonu zmienionego przez użytkownika nie ruszamy.
"""
import hashlib
import re

from .frontmatter import Document, FrontmatterError
from .model import MODES

PLACEHOLDERS = ("card_id", "title", "description", "project", "repo", "docs_dir", "branch", "tool",
                "docs_note", "spec", "spec_file", "check", "check_label", "finish", "spec_commit")
CHECKS = ("auto", "manual")
_FIELD = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

STANDARD = "~/DevWorkspace/plasma-tide/docs/agent-plan-standard.md"
TOOLS_MAKE = "make -C ~/DevWorkspace/plasma-tide"

_COMMON = """Karta {card_id}: {title}
Projekt: {project}
Repozytorium: {repo}
Gałąź: {branch}
Plany i specyfikacje: {docs_dir}

Opis karty:

{description}
"""

_SPEC_FRONTMATTER = """   ---
   card: {card_id}
   status: szkic
   ---"""

_TICKETS = f"""Prefiks i numery ticketów bierz z zasad projektu: CLAUDE.md albo AGENTS.md w repozytorium,
   sekcja „Plan pracy agentów (Tide)” (tracker, prefiks, sposób nadawania numerów). Nowy numer lokalny
   bierzesz wyłącznie z `{TOOLS_MAKE} next-id REPO={{repo}} TITLE="…"`. Jeśli projekt nie ma takich zasad:
   `tracker: local`, własny krótki prefiks wielkimi literami zapisany w planie (`ticket-prefix`), numery
   przez `{TOOLS_MAKE} next-id REPO={{repo}} PREFIX=<prefiks> TITLE="…"`. Przed nadaniem numerów uruchom
   `{TOOLS_MAKE} check-plans` — kod wyjścia 1 to kolizja: zatrzymaj się i zapytaj mnie."""

_PLAN = f"""Zakładasz go według standardu Tide ({STANDARD}, wersja 1.1)
   w `{{docs_dir}}/plans/RRRR-MM-DD-<slug>.md`: frontmatter z `tide-plan: 1`, `card: {{card_id}}`,
   `coordinator: {{tool}}`, `status: active`, `updated` i tabela
   `| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |`. Klucz `card` łączy plan z kartą na tablicy Tide."""

SEED_FLOWS = {
    "full": """---
tide-flow: 2
mode: full
---
""" + _COMMON + """
Tryb „Pełny flow”: grilling → specyfikacja → plan z ticketami → implementacja przez TDD → review.
Jesteś koordynatorem tej karty: dzielisz pracę na tickety, zlecasz je podagentom i prowadzisz plan.

Dokumenty AI: {docs_note}
Istniejąca specyfikacja karty: {spec}

1. Grilling. Zanim cokolwiek zaplanujesz, przepytaj mnie o wymagania. Użyj skilla `grilling`, jeśli go
   masz (Claude Code). Jeśli nie (Codex) — zrób to samo słowami: pytaj po kolei, po jedno–trzy pytania
   naraz, aż znasz cel, zakres, przypadki brzegowe, kryteria akceptacji i to, czego NIE robimy. Nie
   zgaduj. Gdy specyfikacja już jest, przeczytaj ją i pytaj tylko o luki.
2. Specyfikacja. Zapisz ją po polsku w `{spec_file}` z frontmatterem:
""" + _SPEC_FRONTMATTER + """
   Treść: cel, zakres, poza zakresem, kryteria akceptacji, decyzje z grillingu, ryzyka. Pokaż mi ją;
   po mojej akceptacji zmień `status: szkic` na `status: gotowa`.
3. Plan. """ + _PLAN + """
   """ + _TICKETS + """
4. Implementacja ticket po tickecie przez TDD: najpierw test, który nie przechodzi, potem kod, potem
   porządki (skill `tdd`, jeśli go masz). Praca idzie na gałęzi {branch}; gałąź podagenta zawiera ID
   ticketu małymi literami.
5. Delegowanie i review według standardu: opis podagenta zaczyna się od ID ticketu
   (`<PREFIKS>-7: implement …`, `<PREFIKS>-7: review`); każdy ticket przechodzi review z liczbami
   BLOCKER / MAJOR / NIT i werdyktem, a po BLOCKER albo MAJOR — poprawki i ponowne sprawdzenie.
   Tabelę planu i `updated` aktualizuj przy każdym delegowaniu, wyniku, review i pytaniu do mnie.
6. Każde pytanie do mnie zaczynaj od ID ticketu (`<PREFIKS>-7: …?`) — Tide pokaże je na tablicy.
7. Review AI jest obowiązkowe: żadnego ticketu ani całej pracy nie kończysz bez review z werdyktem
   APPROVE (bez BLOCKER i MAJOR).
8. Zakończenie (sprawdzenie: {check_label}): {finish}
""",
    "light": """---
tide-flow: 2
mode: light
---
""" + _COMMON + """
Tryb „Lekki”: krótka specyfikacja, plan z 1–3 ticketami, praca i review. Pytaj tylko przy realnej
wątpliwości — resztę rozstrzygnij sam i zapisz w specyfikacji.

Dokumenty AI: {docs_note}
Istniejąca specyfikacja karty: {spec}

1. Krótka specyfikacja (kilka akapitów: cel, zakres, kryteria akceptacji, decyzje) w `{spec_file}`
   z frontmatterem:
""" + _SPEC_FRONTMATTER + """
   Gdy jest kompletna, zmień `status: szkic` na `status: gotowa`.
2. Plan z 1–3 ticketami. """ + _PLAN + """
   """ + _TICKETS + """
3. Implementacja przez TDD (skill `tdd`, jeśli go masz) na gałęzi {branch}; gałąź podagenta zawiera
   ID ticketu małymi literami, a jego opis zaczyna się od ID ticketu.
4. Review AI jest obowiązkowe: każdy ticket (przy małej zmianie wystarczy jedno review całości)
   z liczbami BLOCKER / MAJOR / NIT i werdyktem; poprawki po BLOCKER albo MAJOR, kończysz dopiero
   po APPROVE. Tabelę planu i `updated` aktualizuj na bieżąco, pytania do mnie zaczynaj od ID ticketu.
5. Zakończenie (sprawdzenie: {check_label}): {finish}
""",
    "spec": """---
tide-flow: 2
mode: spec
---
""" + _COMMON + """
Tryb „Tylko specyfikacja”: dopytujesz i piszesz specyfikację — nic więcej. Bez zmian w kodzie, bez
gałęzi i worktree, bez planu i ticketów.

Dokumenty AI: {docs_note}
Commit: {spec_commit}
Istniejąca specyfikacja karty: {spec}

1. Rozejrzyj się w kodzie i dokumentacji projektu tylko do odczytu, żeby pytać konkretnie.
2. Grilling: użyj skilla `grilling`, jeśli go masz (Claude Code). Jeśli nie (Codex) — przepytaj mnie
   słowami: po jedno–trzy pytania naraz, aż znasz cel, zakres, przypadki brzegowe, kryteria akceptacji
   i to, czego NIE robimy. Nie zgaduj.
3. Zapisz specyfikację po polsku w `{spec_file}` z frontmatterem:
""" + _SPEC_FRONTMATTER + """
   Treść: cel, zakres, poza zakresem, kryteria akceptacji, decyzje z rozmowy, otwarte pytania, ryzyka
   i propozycja podziału na kroki (bez numerów ticketów). Sprawdzenie pracy po wdrożeniu:
   {check_label} — przy teście ręcznym dopisz sekcję „Co przetestować ręcznie i jak” (kroki
   i oczekiwany wynik), przy `auto` napisz, co sprawdzą testy automatyczne i review AI.
   Zanim pokażesz mi specyfikację, zleć jej review AI (podagent: luki, sprzeczności, niesprawdzalne
   kryteria) i popraw, co znajdzie.
4. Gdy zaakceptuję specyfikację, zmień `status: szkic` na `status: gotowa` — wtedy Tide oznaczy kartę
   „specyfikacja gotowa” i odłoży ją do Backlogu.
5. Napisz mi, że specyfikacja jest gotowa i gdzie leży. Nie implementuj.
""",
}

# Dawne zaczyny z flows/ (PT-1) — skróty SHA-256 treści zapisanej przy zakładaniu tablicy.
PREVIOUS_SEEDS = {
    "full": {"50e7022ef834891b46ef499318146a3ee322b0ce11015f83474ffdae54666e7a"},
    "light": {"6476221fe489d549b497b19cb77bba3c72590e904448e7b13eec2354228f4913"},
    "spec": {"7e01493f02a65ba87bc4bc944c85d3170452a1be96ebf8d745e95b548ddb15e3"},
}


CHECK_LABELS = {"auto": "auto — wystarczy review AI, bez testu ręcznego",
                "manual": "manual — po review AI test ręczny użytkownika"}


def finish_text(check: str, docs_dir: str) -> str:
    """Jak agent kończy pracę (pole {finish}): przy `manual` lista „Co przetestować ręcznie i jak”
    w planie i informacja, że praca czeka na test ręczny; przy `auto` — koniec po review AI."""
    if check == "manual":
        return ("po review AI z werdyktem APPROVE dopisz w planie (w {docs}/plans/, sekcja pod tabelą) krótką, "
                "ludzką listę „Co przetestować ręcznie i jak”: kroki, które mam wykonać, i oczekiwany wynik każdego. "
                "Tickety `gotowe`, plan `status: done`. Nie scalaj do gałęzi głównej i nie wypychaj. W ostatniej "
                "wiadomości napisz, że praca czeka na mój test ręczny, i wskaż listę.").format(docs=docs_dir)
    return ("po review AI z werdyktem APPROVE tickety `gotowe`, plan `status: done`; scal do gałęzi głównej tylko "
            "wtedy, gdy zasady projektu (CLAUDE.md albo AGENTS.md) na to pozwalają — inaczej zostaw gałąź do "
            "scalenia. Bez push, dopóki o to nie poproszę. W ostatniej wiadomości napisz krótko, co się zmieniło "
            "i gdzie.")


def spec_commit_text(in_board: bool, card_id: str, spec_file: str) -> str:
    """Tryb spec: w tablicy nic nie commitujemy (robi to usługa); w repozytorium projektu — wyłącznie plik
    specyfikacji na bieżącej gałęzi, żeby późniejszy start z nowym worktree nie trafił na „dirty-base”."""
    if in_board:
        return "niczego nie commitujesz — plik specyfikacji w tablicy commituje usługa Tide."
    return (f"gdy specyfikacja ma `status: gotowa`, zacommituj wyłącznie jej plik na bieżącej gałęzi repozytorium: "
            f"`git add {spec_file}` i `git commit -m \"docs(spec): {card_id} <short English title>\"` — temat po "
            "angielsku, w konwencji conventional commits, bez żadnych podpisów i wzmianek o AI ani agencie (bez "
            "Co-Authored-By). Nic poza tym plikiem, bez push.")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def outdated(mode: str, text: str | None) -> bool:
    """Czy plik szablonu to nietknięty dawny zaczyn (wolno go zastąpić nową treścią)."""
    return text is not None and digest(text) in PREVIOUS_SEEDS.get(mode, ())


def body_of(text: str) -> str:
    """Treść szablonu bez frontmattera (plik bez frontmattera to sama treść)."""
    try:
        return Document.parse(text).body.lstrip("\n")
    except FrontmatterError:
        return text


def render(template: str, values: dict) -> str:
    """Wypełnia pola z PLACEHOLDERS; brakująca wartość to pusty napis."""
    return _FIELD.sub(lambda m: str(values.get(m.group(1)) or ""), body_of(template))


assert set(SEED_FLOWS) == set(MODES)
assert not any(outdated(mode, text) for mode, text in SEED_FLOWS.items())
