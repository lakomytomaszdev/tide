"""Wejście planującego: pełne szablony specyfikacji i pionowych ticketów, bez publikacji."""
import json

HEADER = "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |"
HEADER_PROJECTS = HEADER + " Projekt |"


def _multi(projects) -> bool:
    return len(projects or ()) > 1


def projects_section(projects) -> str:
    """Projekty zlecenia (K-33 #4) w poleceniu planowania: repozytoria, źródło numerów i zasady kolumny Projekt.
    Pusty napis, gdy zlecenie ma jeden projekt."""
    if not _multi(projects):
        return ""
    lines = ["Projekty zlecenia (jeden wspólny plan; każdy ticket należy do jednego projektu):"]
    for p in projects:
        role = ("projekt karty — tu pracujesz i piszesz" if p.get("main") else
                "repozytorium tylko do odczytu — nie zmieniaj w nim plików")
        source = p.get("source") or (f"YouTrack {p.get('prefix')}" if p.get("tracker") == "youtrack"
                                     else f"lokalny {p.get('prefix')}")
        lines.append(f"- {p['slug']} — {p.get('name') or p['slug']}: {p.get('repo') or ''} ({role}); "
                     f"prefiks {p.get('prefix') or '(nieustalony)'}, tracker {p.get('tracker') or 'local'}, "
                     f"numery: {source}")
    lines += ["Poznaj kod i zasady każdego z tych repozytoriów (AGENTS.md/CLAUDE.md, CONTEXT.md, ADR).",
              "Plan ma kolumnę Projekt (standard 1.3) ze slugiem projektu z tej listy; pusta komórka = projekt "
              "karty. Nie używaj projektów spoza listy. Numery robocze #n są wspólne dla całego planu, a blokery "
              "#n mogą wskazywać ticket z innego projektu (bez cykli). Nie wpisuj gotowych ID ani prefiksów — "
              "numery w każdym projekcie nada Tide po zatwierdzeniu."]
    return "\n".join(lines) + "\n"


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
           tracker="local", tracker_url="", tracker_project="", projects=()):
    executors = [{"name": e.name, "provider": e.provider, "program": e.program, "model": e.model,
                  "classes": list(e.classes)} for e in catalog.executors]
    multi = _multi(projects)
    header = HEADER_PROJECTS if multi else HEADER
    sample = ("| #1 | <zachowanie> | todo | | <wykonawca> | <uzasadnienie> | standard | <recenzent> | | <projekt> |"
              if multi else "| #1 | <zachowanie> | todo | | <wykonawca> | <uzasadnienie> | standard | <recenzent> | |")
    return f"""Krok planowania Lekkiego karty {card.id}.
Pracuj bez pytań. Rozsądne założenia zapisz w specyfikacji. Tylko przygotuj propozycję, nie wykonuj ticketów.
Nie publikuj do GitHub Issues ani do trackera. To nadpisuje kroki publikacji skilli to-spec i to-tickets,
także lokalne .scratch: wszystkie wyniki zapisz wyłącznie do katalogu wyjściowego {out_dir}.
Nie rezerwuj numerów, nie zakładaj zgłoszeń ani nie zmieniaj planów projektu. Nie rób push ani merge.

Karta: {card.id} — {card.title}
Projekt: {project.name}
{projects_section(projects)}Opis karty:
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
Zapisz propozycję standardu {'1.3' if multi else '1.2'} jako {out_dir}/plan.md:
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
{header}
{'| --- ' * (header.count('|') - 1)}|
{sample}
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


def full_prompt(*, card, project, catalog, preferred, assignment_rules, prefix, out_dir, tool,
                images=(), tracker="local", tracker_url="", tracker_project="", projects=()):
    """Pełny proces w jednej rozmowie; artefakty pozostają w katalogu kroku do zatwierdzenia."""
    executors = [{"name": e.name, "provider": e.provider, "program": e.program, "model": e.model,
                  "classes": list(e.classes)} for e in catalog.executors]
    multi = _multi(projects)
    header = HEADER_PROJECTS if multi else HEADER
    sample = ("| #1 | <działające zachowanie> | todo | | <wykonawca> | <jednozdaniowe uzasadnienie> | standard | "
              "<recenzent> | | <projekt> |" if multi else
              "| #1 | <działające zachowanie> | todo | | <wykonawca> | <jednozdaniowe uzasadnienie> | standard | "
              "<recenzent> | |")
    discovery = ("Użyj skilli grill-with-docs oraz domain-modeling. Prowadź dociekliwą rozmowę z użytkownikiem: "
                 "pytaj o cel, przypadki brzegowe, proces i decyzje. W trakcie rozmowy aktualizuj słownik "
                 "domenowy i uzasadnienia decyzji projektu w artefaktach katalogu wyjściowego, "
                 "bez zmieniania repozytorium projektu.") if tool == "claude" else (
                 "Przeprowadź dociekliwy grilling z dokumentacją: pytaj użytkownika o cel, przypadki brzegowe, "
                 "proces i decyzje, aż wymagania będą jednoznaczne. Na bieżąco buduj słownik domenowy i "
                 "uzasadnienia decyzji projektu, zapisując je w artefaktach katalogu wyjściowego. "
                 "Nie zmieniaj repozytorium projektu.")
    return f"""Pełne planowanie karty {card.id} w jednej rozmowie w Konsoli.
Jesteś planującym. Rozmawiaj z użytkownikiem i pokaż mu podział przed zapisem propozycji. Nie implementuj ticketów.
Karta: {card.id} — {card.title}
Projekt: {project.name}
{projects_section(projects)}Opis karty:
{card.description}
Zrzuty ekranu (przeczytaj dołączone obrazy):
{chr(10).join(images) or '(brak)'}
Preferowany dostawca: {preferred} ({'mieszany' if preferred == 'mixed' else preferred}).
Katalog wykonawców (nazwa, dostawca, program, model, klasy):
{json.dumps(executors, ensure_ascii=False)}
Zasady przydziału:
{assignment_rules}

Pracuj w tej kolejności:
1. {discovery} Poznaj zasady AGENTS.md/CLAUDE.md, CONTEXT.md i decyzje ADR. Uzgadniaj terminy
z użytkownikiem. Zapisuj ustalenia jako {out_dir}/discovery.md i w razie potrzeby {out_dir}/decisions.md.
2. Użyj to-spec po zakończeniu rozmowy o wymaganiach. Utwórz {out_dir}/spec.md z pełną specyfikacją:
Problem Statement, Solution, User Stories (każda z korzyścią i przypadkami brzegowymi), Implementation Decisions
(moduły, interfejsy, schemat i kontrakty), Testing Decisions (testy przez najwyższy użyteczny interfejs),
Out of Scope i Further Notes. Pokaż użytkownikowi nierozstrzygnięte decyzje i doprecyzuj je przed podziałem.
3. Użyj to-tickets na podstawie tej specyfikacji. Zaproponuj pionowe plasterki: każdy ticket dostarcza
działające, sprawdzalne zachowanie przez potrzebne warstwy i mieści się w jednym świeżym kontekście.
Rozważ uproszczenie kodu; szerokie migracje dziel przez expand–contract z prawdziwymi blokerami.
W rozmowie pokaż użytkownikowi każdy ticket, kryteria akceptacji, blokery i kolejność fal. Przyjmij poprawki
użytkownika i pokaż poprawiony podział. Dopiero potem zapisz propozycję.
Każdemu ticketowi przypisz klasę lekki/standard/mocny/lokalny, wykonawcę z katalogu i niezależnego recenzenta
z innym modelem zgodnie z zasadami i preferowanym dostawcą. Jednym zdaniem uzasadnij każdy przydział.
4. Zapisz {out_dir}/plan.md w standardzie {'1.3' if multi else '1.2'}:
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
{header}
{'| --- ' * (header.count('|') - 1)}|
{sample}
Numery są robocze (#1, #2); blokery wskazują tylko robocze numery z planu i nie tworzą cykli.
5. Dla każdego ticketu zapisz {out_dir}/tickets/<numer bez #>.md:
# <numer>: <tytuł>
**Co zbudować:** zachowanie dla użytkownika oraz odpowiedni fragment specyfikacji.
**Blokery:** numery i tytuły lub brak.
**Stan:** ready-for-agent
- [ ] Oddzielne, mierzalne kryteria akceptacji.
## Dla recenzenta
Konkretne ryzyka, zakres kontroli i czego nie wolno było ruszać.

Wszystkie pliki wyjściowe zapisz wyłącznie w {out_dir}. Instrukcje publikowania z wymienionych skilli
są tutaj nadpisane: nie publikuj specyfikacji ani ticketów do GitHub Issues ani żadnego trackera, także
lokalnego .scratch. Nie rezerwuj numerów i nie zmieniaj planów projektu. Nie rób push ani merge.
Po zapisaniu wszystkich artefaktów zapisz jako ostatni plik:
Plik wyniku: {out_dir}/result.json
{{"result":"done","notes":["discovery.md","spec.md","plan.md"]}}.
Jeśli potrzebujesz decyzji w trakcie rozmowy, pytaj w Konsoli. Jeżeli musisz zakończyć krok z pytaniem,
zapisz {{"result":"question","question":"treść pytania"}}; możesz dodać gotowe odpowiedzi
`"options":[{{"title":"…","effect":"skutek","recommended":true}}]` (1–4, najwyżej jedna polecona;
„Inną odpowiedź…” dokłada Tide). Dla odmowy polecenia zapisz
{{"result":"denied","command":"pełne polecenie"}}, a dla błędu {{"result":"error","reason":"powód"}}.
Zapis result.json kończy ten krok; dalsze zmiany wymagają osobnego odesłania planu do poprawy.
"""
