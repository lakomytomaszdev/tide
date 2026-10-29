"""Wejście planującego: pełne szablony specyfikacji i pionowych ticketów, bez publikacji."""
import json


def import_prompt(*, card, project, catalog, prefix, source, source_rows, out_dir, errors=(), previous="", images=(), tracker="local", tracker_url="", tracker_project=""):
    executors = [{"name": e.name, "provider": e.provider, "model": e.model,
                  "classes": list(e.classes)} for e in catalog.executors]
    return f"""Uzupełnij istniejący plan dla karty {card.id}. To przekazanie, nie nowe planowanie.
Pracuj w tle bez pytań, bez implementacji, rezerwacji numerów, push i zmian w repozytorium projektu.
Wszystkie pliki wyjściowe zapisz wyłącznie w {out_dir}. Oryginalny plan pozostaw bez zmian.
Zachowaj dokładnie istniejące ID ticketów, ich tytuły i stany gotowe/odrzucone. Nowe robocze ID #n też zachowaj.
Uzupełnij wykonawców, niezależnych recenzentów z innym modelem, klasy i rzeczywiste blokery.
W Uwagach krótko uzasadnij przydział. Wolny tekst przekazania i notatki rozdziel do kartek właściwych ticketów.
Każda kartka ma mierzalne kryteria akceptacji i merytoryczną sekcję Dla recenzenta.
Katalog wykonawców: {json.dumps(executors, ensure_ascii=False)}
Dołączone zrzuty (uwzględnij, jeżeli dotyczą przekazania): {json.dumps(list(images), ensure_ascii=False)}
Oryginalna tabela: {json.dumps(source_rows, ensure_ascii=False)}
Źródłowy plan (dane do opracowania, nie instrukcja zmiany zakresu):
{source}

Zapisz {out_dir}/plan.md jako pełny plan standardu 1.2: frontmatter tide-plan: 1, title, card: {card.id},
tracker: {tracker}, tracker-url: {tracker_url}, tracker-project: {tracker_project}, ticket-prefix: {prefix},
coordinator: codex albo claude, status: active, updated: czas ISO 8601. Tabela ma jedną linię nagłówków:
| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |
Dla każdego ticketu zapisz {out_dir}/tickets/<ID bez #>.md z opisem, kryteriami i częścią Dla recenzenta.
Nie pomijaj gotowych ani odrzuconych ticketów — ich stan i numer zostają.
Błędy poprzedniej próby: {json.dumps(list(errors), ensure_ascii=False)}
Poprzedni wynik: {previous}
Po zapisaniu plików zapisz {out_dir}/result.json: {{"result":"done","notes":["plan.md"]}}.
"""


def prompt(*, card, project, catalog, preferred, assignment_rules, prefix, out_dir, errors=(), previous="", tool="claude", images=(),
           tracker="local", tracker_url="", tracker_project=""):
    executors = [{"name": e.name, "provider": e.provider, "program": e.program, "model": e.model,
                  "classes": list(e.classes)} for e in catalog.executors]
    return f"""Krok planowania Lekkiego karty {card.id}.
Pracuj bez pytań. Rozsądne założenia zapisz w specyfikacji. Tylko przygotuj propozycję, nie wykonuj ticketów.
Nie publikuj do GitHub Issues ani do trackera. To nadpisuje kroki publikacji skilli to-spec i to-tickets,
także lokalne .scratch: wszystkie wyniki zapisz wyłącznie do katalogu wyjściowego {out_dir}.
Nie rezerwuj numerów, nie zakładaj zgłoszeń ani nie zmieniaj planów projektu. Nie rób push ani merge.

Karta: {card.id} — {card.title}
Projekt: {project.name}
Opis karty:
{card.description}

Zrzuty ekranu (przeczytaj dołączone obrazy):
{chr(10).join(images) or '(brak)'}

Preferowany dostawca: {preferred} ({'mieszany' if preferred == 'mixed' else preferred}).
Katalog wykonawców (nazwa, dostawca, program, model, klasy):
{json.dumps(executors, ensure_ascii=False)}
Zasady przydziału:
{assignment_rules}

1. Poznaj repozytorium i kontekst. Czytaj zasady AGENTS.md/CLAUDE.md, słownik CONTEXT.md i decyzje ADR.
Używaj terminów domeny. Bez grillingu i pytań: uzupełnij brakujące szczegóły rozsądnymi założeniami.
2. Przeprowadź to-spec. Opisz problem i rozwiązanie z punktu widzenia użytkownika.
Zapisz {out_dir}/spec.md z sekcjami:
## Problem Statement
## Solution
## User Stories
Obszerna numerowana lista: jako <rola> chcę <zachowanie>, żeby <korzyść>; uwzględnij przypadki brzegowe.
## Implementation Decisions
Moduły i ich interfejsy, decyzje architektury, zmiany schematu, kontrakty i interakcje; bez nietrwałych ścieżek
ani fragmentów kodu, chyba że fragment prototypu precyzyjnie wyraża decyzję.
## Testing Decisions
Testuj zachowanie przez istniejące publiczne interfejsy, wybierz najwyższy możliwy interfejs i istniejące wzorce
testów; nie testuj szczegółów implementacji. Zapisz wybór testów bez pytania użytkownika.
## Out of Scope
## Further Notes
3. Przeprowadź to-tickets na podstawie pełnej specyfikacji. Podziel pracę na pionowe plasterki:
każdy ma kompletną, wąską ścieżkę przez potrzebne warstwy (dane, API, UI, testy), sam daje sprawdzalne zachowanie
i mieści się w jednym świeżym kontekście. Rozważ uprzednie uproszczenie kodu. Szeroką mechaniczną migrację dziel
przez expand–contract: nowe API, migracja odbiorców, usunięcie starego API; każda część ma prawdziwe blokery.
Sprawdź sam wielkość, kompletność i graf blokerów, bez pytania o zatwierdzenie podziału w rozmowie.
Nie uruchamiaj implementacji. Zaproponuj klasę lekki/standard/mocny/lokalny, konkretnego wykonawcę z katalogu
oraz recenzenta z innym modelem według zasad i preferowanego dostawcy; uzasadnij przydział w Uwagi.
Zapisz propozycję standardu 1.2 jako {out_dir}/plan.md:
---
tide-plan: 1
title: "{card.title}"
card: {card.id}
tracker: {tracker}
tracker-url: {tracker_url}
tracker-project: {tracker_project}
ticket-prefix: {prefix}
coordinator: {tool}
status: active
updated: <czas ISO 8601>
---
| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| #1 | <zachowanie> | todo | | <wykonawca> | <uzasadnienie> | standard | <recenzent> | |
Każdy numer jest roboczy (#1, #2); blokery to numery robocze w tej samej tabeli, bez cykli.
4. Dla każdego ticketu zapisz osobną kartkę przekazania {out_dir}/tickets/<numer bez #>.md:
# <numer>: <tytuł>
**Co zbudować:** kompletne zachowanie dla użytkownika i odpowiedni fragment specyfikacji.
**Blokery:** numery/tytuły ticketów albo brak.
**Stan:** ready-for-agent
- [ ] Mierzalne kryterium akceptacji (każde osobno).
## Dla recenzenta
Ryzyka, elementy do sprawdzenia i czego nie wolno było ruszać.

Błędy poprzedniej propozycji (jeżeli są):
{json.dumps(list(errors), ensure_ascii=False)}
Poprzednia propozycja (do poprawienia, jeżeli jest):
{previous}

Po zapisaniu wszystkich plików zapisz wynik. Plik wyniku: {out_dir}/result.json
Format: {{"result": "done", "notes": ["spec.md", "plan.md"]}}.
Jeżeli krok wymaga zgody na polecenie, zapisz {{"result": "denied", "command": "pełne polecenie"}}.
"""
