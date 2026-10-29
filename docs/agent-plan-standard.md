# Standard planu pracy agentów (Tide)

Wersja 1.2, 29.10.2026 (zgodna wstecz z wersjami 1 i 1.1: dodatkowe kolumny są opcjonalne,
a plany z sześcioma kolumnami działają jak dotąd). Ten dokument jest wzorcem, do którego odsyłają pliki `CLAUDE.md` i `AGENTS.md`
projektów. Plan czyta tide-daemon: tablica w oknie **Tide Sesje**, postęp koordynatora w widżecie
agentów i sekcja „Plany i tickety” w raporcie porannym (opis: [tide-daemon.md](tide-daemon.md#koordynacja),
uzasadnienie: decyzja 50 w [decisions.md](decisions.md)).

## Po co

Sesja koordynująca (Claude Code albo Codex), która dzieli pracę na tickety i zleca je podagentom,
zapisuje plan w repozytorium. Dzięki temu stan pracy jest widoczny poza rozmową: co jest do zrobienia,
co w toku, co czeka na decyzję użytkownika, co po review — także dla kolejnej sesji, która przejmie pracę.

## Plik

- Ścieżka: `docs/agents/plans/RRRR-MM-DD-<slug>.md` (data założenia planu, slug z małych liter,
  cyfr i myślników), np. `docs/agents/plans/2026-10-26-release-candidate.md`.
- Plan jest **commitowany** razem z pracą albo osobnym commitem `docs(plan): …`.
- **Projekt z dokumentami AI „w Tide”** (wersja 1.1): gdy projekt na tablicy Tide ma `ai-docs: tide`,
  plany i specyfikacje nie trafiają do repozytorium projektu, tylko do repozytorium tablicy:
  `~/DevWorkspace/tide-board/docs/<projekt>/plans/RRRR-MM-DD-<slug>.md` i `…/specs/` (inna tablica:
  `$TIDE_BOARD_DIR`). Agent dostaje ten katalog przez `--add-dir`, pisze w nim, ale **niczego tam nie
  commituje** — repozytorium tablicy commituje tide-daemon. Tide liczy takie plany do repozytorium
  projektu (`repo` w `projects/<projekt>.md` tablicy): tablica Sesje, raport poranny, kontrola numerów,
  `check-plans` i `next-id` widzą je razem z planami z `docs/agents/plans/`. Polecenie uruchomienia
  z karty podaje agentowi właściwy katalog.
- Jeden plan na jeden ciąg pracy koordynatora. Zakończony plan zostaje w repozytorium ze statusem `done`.

## Frontmatter

Płaskie pary `klucz: wartość` (YAML bez zagnieżdżeń i list), pierwsza linia pliku to `---`.

| Klucz | Wartość | Opis |
| --- | --- | --- |
| `tide-plan` | `1` | wersja standardu; plik bez niej nie jest planem Tide |
| `title` | tekst | tytuł planu, np. `Release candidate TOM-20` |
| `tracker` | `youtrack` \| `local` \| `none` | skąd są tickety (niżej) |
| `tracker-url` | adres albo pusty | dla `youtrack`, np. `http://10.20.1.159:8111` |
| `tracker-project` | klucz albo pusty | projekt w trackerze, np. `LOG`, `LEGAL`, `APP5` |
| `ticket-prefix` | wielkie litery | prefiks ID ticketów w tym planie, np. `LOG`, `APP5`, `PT` |
| `coordinator` | `claude` \| `codex` | narzędzie sesji koordynującej |
| `session` | ID sesji albo pusty | ID sesji koordynatora (Claude Code: nazwa pliku dziennika, Codex: ID wątku), jeśli znane |
| `status` | `active` \| `paused` \| `done` | stan całego planu |
| `updated` | czas ISO 8601 | chwila ostatniej zmiany planu, np. `2026-10-26T21:13:00+01:00` |
| `card` | `K-<numer>` albo brak | **opcjonalny (1.1)**: karta tablicy Tide, której służy plan, np. `K-12`; plan uruchomiony z karty zawsze go ma |

**Tracker:**

- `youtrack` — tickety żyją w YouTracku (`tracker-url`, `tracker-project`); plan wskazuje, które
  z nich są w tym ciągu pracy, a Tide dociąga z YouTracka tytuły, stany i odnośniki. Gdy projekt YouTracka
  ma na tablicy Tide klucz z włączoną synchronizacją (PT-7), Tide zapisuje w YouTracku stan ticketu
  (`w-toku` i `review` → In Progress, `czeka` → To be discussed, `gotowe` → Fixed, `odrzucone` → Won't fix) —
  tylko przy przejściach, bez nadpisywania zmian ręcznych (docs/tide-daemon.md, „Synchronizacja z YouTrackiem”).
- `local` — **nie ma zewnętrznego trackera**: ID ticketów nadaje plan (`PT-1`, `PT-2`, …), a plan
  jest źródłem stanu dla tablicy Tide i raportu porannego. `tracker-url` i `tracker-project` puste.
- `none` — praca bez śledzenia ticketów (np. jednorazowe zadanie). Tabela może być pusta albo
  zawierać zwykłe kroki; Tide pokazuje je, ale niczego nie dopasowuje do trackera.

**Karta (`card`).** Plan uruchomiony z karty tablicy Tide („Co ma zrobić agent?”) ma `card: K-12`,
a gałąź koordynatora zawiera ID karty małymi literami (`claude/k-12-okno-dockera`). Po tej parze Tide
łączy kartę z koordynatorem i jego planem: koordynator na gałęzi `…k-12…` dostaje plan z `card: K-12`
(w katalogu projektu w tablicy leżą plany wielu kart naraz), a karta — stan z planu. Plan z innym `card`
nie jest planem tego koordynatora. Klucz bez sensownej wartości (np. `card: 12`) to błąd planu.
Każda sesja pisząca przekazanie dla konkretnej karty wpisuje `card` przed zapisaniem planu.
Plan projektu bez `card` może być tylko podpowiedzią w wejściu „Mam plan”; użytkownik musi go wybrać.
Ścieżkę wybranego planu zapisuje karta w tablicy. Przejęcie kopiuje tabelę do dziennika karty
bez zmiany oryginalnego planu ani numerów istniejących ticketów.

**Specyfikacja karty** (obok planu, w `specs/`): plik `k-12-<opis>.md` z frontmatterem `card: K-12`
i `status: szkic`, zmienianym na `status: gotowa`, gdy użytkownik ją zaakceptuje. W trybie „tylko
specyfikacja” to sygnał dla Tide: karta dostaje znacznik „specyfikacja gotowa” i wraca do Backlogu.
Specyfikację w repozytorium projektu (`docs/agents/specs/`) agent w tym trybie commituje sam — tylko ten
plik, na bieżącej gałęzi, `docs(spec): K-12 <short English title>`, bez push; w tablicy commituje tide-daemon.

## Numery ticketów: jedno źródło

Jeden numer to zawsze jedno zadanie — w całym repozytorium i w trackerze. Dlatego:

1. **Jedno źródło numerów na repozytorium.** Gdy repozytorium ma projekt w YouTracku, plan ma
   `tracker: youtrack`, a `ticket-prefix` jest równy `tracker-project`: ID w planie to dokładnie klucze
   z YouTracka. Ticket najpierw zakładasz w YouTracku, dopiero potem wpisujesz go do planu. Nowe plany
   nie używają aliasów (np. `LA-035` zamiast `LEGAL-35`); stare aliasy działają tylko przez mapę
   migracji (`docs/agents/youtrack-migration/`) do czasu przejścia.
2. **Prefiks lokalny jest jeden i wolny.** Przy `tracker: local` prefiks jest jeden dla całego
   repozytorium (zapisany w CLAUDE.md/AGENTS.md) i nie może być kluczem żadnego projektu w YouTracku
   (sprawdź listę projektów — tylko odczyt).
3. **Numeracja lokalna jest ciągła w repozytorium.** Nowy numer = największy numer z tym prefiksem
   we wszystkich planach repozytorium + 1 — w `docs/agents/plans/` głównego katalogu i **każdego
   worktree** (`git worktree list`, także niezacommitowane) oraz na wszystkich gałęziach
   (`git grep -h "^ticket-prefix\\|^| PREFIKS-" $(git for-each-ref --format='%(refname)' refs/heads) -- docs/agents/plans`).
   Plan w innym worktree jest niewidoczny z głównego katalogu, a i tak zajmuje swoje numery.
   Nowy plan nie zaczyna od 1, a numerów nie używa się ponownie, także po `odrzucone`.
   Plany projektu z dokumentami „w Tide” (`tide-board/docs/<projekt>/plans/`) zajmują numery tak samo.
   **Nowy numer lokalny bierzesz wyłącznie z** `make -C ~/DevWorkspace/plasma-tide next-id REPO=<repozytorium>
   [COUNT=N] [TITLE="…"]` (równoważnie `tools/next_id.py`): liczy to samo pod blokadą i zapisuje rezerwację
   w `~/.local/state/tide/ticket-ids.json`, więc dwie równoległe sesje nie dostaną tego samego numeru.
   Dla prefiksu z YouTracka odmawia — numer nadaje YouTrack. Ręczne liczenie tylko wtedy, gdy narzędzia nie ma.
4. **Zanim założysz projekt w trackerze albo zmienisz prefiks**, sprawdź, czy klucz nie jest już
   używany w planach repozytorium ani w trackerze. Przy kolizji zatrzymaj się i zapytaj użytkownika.
5. Tide sprawdza to samo i pokazuje ostrzeżenie na tablicy Sesje i w raporcie: ten sam ID z różnymi
   tytułami w dwóch planach, prefiks lokalny równy kluczowi projektu w YouTracku, prefiks planu
   `youtrack` różny od `tracker-project` bez mapy migracji, numer zarezerwowany dla innego tytułu
   i (łagodnie) nowy plan, który zaczyna numerację od nowa. Koordynator przed nadaniem nowego numeru
   albo założeniem projektu w trackerze uruchamia `make -C ~/DevWorkspace/plasma-tide check-plans`
   (albo równoważnie `tools/check_plans.py`; kod wyjścia 1 to kolizja — zatrzymaj się i zapytaj użytkownika).

## Tabela

Pod frontmatterem jedna tabela Markdown z sześcioma wymaganymi kolumnami:

```markdown
| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |
```

| Kolumna | Opis |
| --- | --- |
| `Ticket` | ID ticketu (`PREFIKS-numer`), ten sam co w trackerze albo nadany przez plan |
| `Tytuł` | krótki tytuł; przy YouTracku może się różnić od tytułu w trackerze (wygrywa plan) |
| `Stan` | jeden z: `todo`, `w-toku`, `review`, `czeka`, `gotowe`, `odrzucone` |
| `Gałąź` | gałąź pracy (może być w `` `…` ``), z ID ticketu małymi literami |
| `Agent` | kto pracuje: typ podagenta (`implementer`, `code-reviewer`…) albo pusty |
| `Uwagi` | jedno zdanie: powód czekania, wynik review, decyzja |

**Wersja 1.2:** na końcu tabeli mogą dojść, w tej kolejności, kolumny `Klasa`, `Review`, `Blokery`.
Można użyć pierwszej, pierwszych dwóch albo wszystkich trzech. Parser Sesji i raportu czyta oba warianty;
plany sześciokolumnowe zachowują dotychczasowe znaczenie.

| Kolumna 1.2 | Opis |
| --- | --- |
| `Klasa` | `lekki`, `standard`, `mocny` albo `lokalny`; bez kolumny domyślnie `standard` przy zatwierdzaniu karty |
| `Review` | nazwa recenzenta z katalogu wykonawców; gdy pusta, Tide dobiera inny model tego samego dostawcy |
| `Blokery` | ID ticketów z tego planu, oddzielone przecinkiem; przed zatwierdzeniem mogą mieć postać `#1`, `#2` |

Propozycja planu karty ma numery robocze `#1`, `#2` itd. oraz plik Markdown `tickets/1.md`,
`tickets/2.md` itd. obok pliku planu. Każda kartka zawiera co najmniej jeden punkt kryteriów
`- [ ] …`; osobny nagłówek „Kryteria akceptacji” jest opcjonalny, zgodnie z formatem lokalnym
`to-tickets`. Usługa sprawdza wykonawców, kartki, blokery, cykle i jedną tabelę przed
pokazaniem planu do zatwierdzenia. Numery `PREFIKS-n` powstają dopiero po „Zatwierdź plan”. Dziennik
karty zapisuje mapę `#n → ID`, a zatwierdzony plan i kartki z ID trafiają na gałąź karty. Plan w tym
worktree czytają Sesje, raport i kontrola numerów razem z innymi planami repozytorium.
Usługa aktualizuje `Stan` oraz `updated` w zatwierdzonym planie po delegowaniu, wyniku kroku,
review i scaleniu ticketu.

## Przepływ karty prowadzony przez Tide (1.2)

Karta na Tablicy uruchamia planowanie albo pojedynczy ticket. Tide prowadzi dziennik karty, zakłada gałąź karty
i osobne gałęzie ticketów, uruchamia kroki oraz pokazuje decyzje w panelu karty. Planowanie Lekkie tworzy propozycję
w tle z opisu i specyfikacji karty. Pełne planowanie odbywa się obecnie w Konsoli; włączenie go do tego przepływu
jest drugim etapem. „Mam plan” przejmuje wskazany plan
bez zapisywania zmian w jego repozytorium. Plan przekazywany dla konkretnej karty ma `card: K-<numer>`; plan bez
tego pola może być podpowiedzią, a nie automatycznie wybranym planem karty.

**Role.** Planujący przygotowuje tabelę i kartki. Inny wykonawca sprawdza plan przed pokazaniem go użytkownikowi;
gdy znajdzie braki, planujący dostaje jedną poprawkę. Autor ticketu wykonuje pionowy wycinek w osobnym worktree,
a recenzent używa innego modelu i zapisuje werdykt. Użytkownik zatwierdza plan albo różnicę po odesłaniu z uwagami;
może też zmienić wykonawcę lub kartkę ticketu, który jeszcze nie wystartował. Tickety bez blokerów mogą biec
równolegle, z limitem kroków dostawcy; bloker rusza po scaleniu poprzedników do gałęzi karty.

**Kontrakt kroku.** Tide przekazuje kartkę z identyfikatorem karty i ticketu, gałęzią, katalogiem roboczym,
ograniczeniami oraz ścieżką katalogu wyjściowego. Autor zapisuje tam notkę `note.md`, a na końcu `result.json`:
`{"result":"done","notes":["note.md"]}`, `{"result":"question","question":"…"}`,
`{"result":"denied","command":"…"}` albo `{"result":"error","reason":"…"}`. Recenzent oprócz tego
zapisuje `review.md` i werdykt `APPROVE` lub `REQUEST_CHANGES` z liczbą usterek. Brak poprawnego wyniku jest błędem
kroku. Pytanie, zgoda i ponowienie przechodzą przez dziennik karty; wznowienie zachowuje worktree i zmiany.
Autor i recenzent nie wykonują push ani scalenia.

**Domknięcie.** Tide scala zaakceptowane tickety do gałęzi karty. `check: manual` dodaje listę „Co przetestować
ręcznie i jak”; `check: auto` oznacza brak tej listy. W obu przypadkach **scalenie gałęzi karty do głównej wymaga
osobnego polecenia użytkownika**. Statusy i numeracja pochodzą z dziennika oraz zatwierdzonego planu; istniejące
plany sześciokolumnowe i stare sesje koordynatora zachowują dotychczasowy odczyt. Plik planu w repozytorium projektu
nie jest poprawiany przy samym wyborze „Mam plan”.

**Stany:**

| Stan | Znaczenie | Kolumna w Tide Sesje |
| --- | --- | --- |
| `todo` | w planie, nikt nad nim nie pracuje | Do zrobienia |
| `w-toku` | implementacja albo poprawki | W toku |
| `review` | recenzent sprawdza zmiany | Review |
| `czeka` | potrzebna decyzja albo odpowiedź użytkownika | Czeka |
| `gotowe` | zrobione (po review albo zaakceptowane) | Gotowe |
| `odrzucone` | świadomie nie robimy | Gotowe (wyciszone, poza postępem) |

Tabelę i `updated` aktualizuj **przy każdym delegowaniu, wyniku podagenta, review i pytaniu do
użytkownika**. Sekcje pod tabelą (notatki, ryzyka, decyzje) to wolny tekst — Tide ich nie czyta.

## Konwencja delegowania

Tide łączy pracę podagentów z ticketami po ID, więc:

1. **Opis podagenta zaczyna się od ID ticketu**: `TOM-140: implement guard`, `TOM-140: review`,
   `TOM-140: review fixes`. Podagent Codexa ma ID w nazwie zadania (`task_name`, np. `tom140_guard`),
   w przydomku i w poleceniu.
2. **Gałąź zawiera ID małymi literami**: `tom-140-guard`, `feat/tide-7-board`, `codex/la-028-…`.
3. **Pytanie do użytkownika wymienia ID ticketu** (`TOM-141: przenieść backlog do osobnego projektu?`)
   — wtedy ticket trafia do kolumny „Czeka” z treścią pytania.
4. Recenzent ma w opisie albo typie słowo „review” (`code-reviewer`, `Review TOM-140 …`), a jego raport
   podaje liczby `BLOCKER` / `MAJOR` / `NIT` i werdykt (`APPROVE` albo `REQUEST CHANGES`).

## Zakończenie pracy (1.1)

1. **Review AI jest obowiązkowe**: ticket i cała praca kończą się dopiero po review z werdyktem
   `APPROVE` (bez BLOCKER i MAJOR).
2. **Test ręczny tylko na życzenie**: gdy uruchomienie z karty ma „Test ręczny po zakończeniu”
   (karta `check: manual`), koordynator po review dopisuje w planie, pod tabelą, sekcję
   „Co przetestować ręcznie i jak” — kroki i oczekiwany wynik każdego — nie scala gałęzi i w ostatniej
   wiadomości mówi, że praca czeka na test ręczny użytkownika. Bez testu ręcznego (`check: auto`,
   domyślnie) praca kończy się po review. W przepływie prowadzonym przez Tide gałąź główną scala
   wyłącznie użytkownik osobnym poleceniem, także przy `check: auto`.

## Jak Tide wyznacza stan

Z pracy podagentów (dzienniki koordynatora, tylko odczyt): pytanie o ticket bez odpowiedzi → `czeka`;
działa recenzent → `review`; działa inny podagent → `w-toku`; ostatnie review bez BLOCKER/MAJOR →
`gotowe`; review z BLOCKER/MAJOR → `w-toku` („do poprawy”); poprawki skończone po review → `gotowe`.
Plan wygrywa przy `odrzucone`, przy ticketach, nad którymi nikt nie pracował (`todo` i inne), i wtedy,
gdy jego `updated` jest nowsze niż ostatnia praca nad ticketem. YouTrack daje tytuły i adresy; jego
stan liczy się, gdy nie ma ani planu, ani pracy podagentów, albo gdy ticket jest tam zamknięty.

## Przykład

```markdown
---
tide-plan: 1
card: K-12
title: Release candidate TOM-20
tracker: youtrack
tracker-url: http://10.20.1.159:8111
tracker-project: LOG
ticket-prefix: TOM
coordinator: claude
session: e367f096-eb51-4f0d-9e0a-e93893d415c4
status: active
updated: 2026-10-26T21:13:00+01:00
---

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| TOM-139 | Unikalne filtry w wyszukiwaniu | gotowe | `tom-139-filters` | implementer | review OK |
| TOM-140 | Distribution guard dla kompaktowej dystrybucji | w-toku | `tom-140-guard` | implementer | po review: 1 MAJOR |
| TOM-141 | Migracja epików z Lineara | czeka | | implementer | pytanie o backlog LA-* |
| TOM-146 | Eksport raportu do CSV z filtrami | todo | | | |

## Notatki

Kolejność: najpierw TOM-140 (blokuje RC), potem TOM-146.
```

## Mapa migracji ID

Gdy repozytorium ma `docs/agents/youtrack-migration/*.csv` (kolumny `source_key` albo `source_id`
i `youtrack_id`, opcjonalnie `kind`), Tide grupuje tickety po nowym kluczu: np. `TOM-10` (Linear) →
`LOG-128` (YouTrack), `LA-001` → `LEGAL-1`. Wiersze z `kind` innym niż `ticket` są pomijane.
