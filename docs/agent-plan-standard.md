# Standard planu pracy agentów (Tide)

Wersja 1.3, 3.11.2026 (zgodna wstecz z wersjami 1, 1.1 i 1.2: dodatkowe kolumny są opcjonalne,
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
   **Nowy numer lokalny bierzesz wyłącznie z** `tide next-id <repozytorium>
   [--count N] [--title "…"]` (`--title` to tytuł wiersza w planie, w języku planu): liczy to samo pod blokadą i zapisuje rezerwację
   w `~/.local/state/tide/ticket-ids.json`, więc dwie równoległe sesje nie dostaną tego samego numeru.
   Dla prefiksu z YouTracka odmawia — numer nadaje YouTrack. Ręczne liczenie tylko wtedy, gdy narzędzia nie ma.
4. **Zanim założysz projekt w trackerze albo zmienisz prefiks**, sprawdź, czy klucz nie jest już
   używany w planach repozytorium ani w trackerze. Przy kolizji zatrzymaj się i zapytaj użytkownika.
5. Tide sprawdza to samo i pokazuje ostrzeżenie na tablicy Sesje i w raporcie: ten sam ID z różnymi
   tytułami w dwóch planach, prefiks lokalny równy kluczowi projektu w YouTracku, prefiks planu
   `youtrack` różny od `tracker-project` bez mapy migracji, a łagodnie: numer zarezerwowany dla innego
   tytułu (często ten sam ticket w innym języku) i nowy plan, który zaczyna numerację od nowa. Koordynator przed nadaniem nowego numeru
   albo założeniem projektu w trackerze uruchamia `tide check-plans`
   (kod wyjścia 1 to kolizja — zatrzymaj się i zapytaj użytkownika).
   Lokalizacje standardu i narzędzi podaje `tide info --json`; polecenia korzystają z zasobów instalacji.
   W checkoutcie odpowiednikami są `make next-id REPO=…` i `make check-plans`.

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
| `Review` | nazwa recenzenta z katalogu wykonawców; gdy pusta, Tide dobiera inny model tego samego dostawcy (przy polityce projektu `other-family` — wykonawcę innej rodziny); sprawdzenie planu stosuje politykę projektu ticketu (recenzja według ryzyka, niżej) |
| `Blokery` | ID ticketów z tego planu, oddzielone przecinkiem; przed zatwierdzeniem mogą mieć postać `#1`, `#2` |

**Wersja 1.3:** po `Blokery` może dojść opcjonalna kolumna `Projekt` (10 kolumn). Parser czyta plany o 6, 7, 8, 9
i 10 kolumnach; znaczenie kolumn 1.2 się nie zmienia, a o obecności kolumny decyduje nagłówek, nie pusta ostatnia
komórka (plan z 9 kolumnami nie staje się planem z kolumną `Projekt`).

| Kolumna 1.3 | Opis |
| --- | --- |
| `Projekt` | slug projektu Tablicy, do którego należy ticket; pusta komórka albo brak kolumny = projekt karty. Nieznany slug nie psuje odczytu Sesji i raportu, zgłasza go dopiero sprawdzanie propozycji |

Blokery `#n` w propozycji planu mogą wskazywać dowolny ticket z całego wspólnego planu karty, także z innego
projektu. W zatwierdzonym planie bloker może być ID z innej karty grupy.

Propozycja planu karty ma numery robocze `#1`, `#2` itd. oraz plik Markdown `tickets/1.md`,
`tickets/2.md` itd. obok pliku planu. Każda kartka zawiera co najmniej jeden punkt kryteriów
`- [ ] …`; osobny nagłówek „Kryteria akceptacji” jest opcjonalny, zgodnie z formatem lokalnym
`to-tickets`. Usługa sprawdza wykonawców, kartki, blokery, cykle i jedną tabelę przed
pokazaniem planu do zatwierdzenia. Numery `PREFIKS-n` powstają dopiero po „Zatwierdź plan”. Dziennik
karty zapisuje mapę `#n → ID`, a zatwierdzony plan i kartki z ID trafiają na gałąź karty. Plan w tym
worktree czytają Sesje, raport i kontrola numerów razem z innymi planami repozytorium.
Usługa aktualizuje `Stan` oraz `updated` w zatwierdzonym planie po delegowaniu, wyniku kroku,
review i scaleniu ticketu.

**Szkice zakresu następczego (`followups/`).** Pakiet propozycji obejmuje Markdowny obok planu,
`tickets/*.md` i `followups/**/*.md`. Podkatalogi szkiców zachowują swoją strukturę. Usługa zamraża
pełną treść przed przekazaniem propozycji; review otrzymuje kopię pakietu w `in/proposal/`, a poprawka
dodatkowo zaczyna z kopiami w swoim wyjściu. Późniejsza edycja wyjścia autora nie zmienia migawki.
Dotyczy to planowania w tle, Konsoli oraz propozycji wskazanej z pliku. Każda propozycja przechowuje
własny pakiet, więc poprawa nie nadpisuje materiału poprzedniej propozycji.
Kopia do odczytu zachowuje oryginalne nazwy i treści. W wyjściu poprawki plan ma nazwę `plan.md`,
a linki ze szkiców wskazują tę nazwę; przy poprawce zatwierdzonego planu kartki i linki do nich
używają już nadanych ID zamiast numerów roboczych. Szkice zachowują podkatalogi, fragmenty i parametry URL.
Istniejące kartki w wyjściu poprawki zatwierdzonego planu mają aktualną treść z planu bazowego,
w tym edycje użytkownika w panelu; pierwotna migawka w `in/proposal/` pozostaje bez zmian.

Dozwolone są zwykłe pliki UTF-8, najwyżej 512 KiB na plik i 16 MiB na cały pakiet.
Dowiązania plików i katalogów są odrzucane. Lokalne linki Markdown do szkiców (także odsyłacze
referencyjne i cele z fragmentem) muszą wskazywać istniejący Markdown w pakiecie; wyjście poza
katalog artefaktów i brak celu przerywają sprawdzanie przed publikacją niekompletnej propozycji.
Usługa nie dołącza dowolnych plików wskazanych linkami. Błąd zamrożenia daje wynik błędu kroku.

Po zatwierdzeniu szkice trafiają do commita gałęzi karty w `followups/` obok planu. Linki kartek
`../followups/…` są przeliczane względem docelowego katalogu kartek. Linki ze szkiców do planu
i kartek są przeliczane według datowanej nazwy planu i nadanych ID ticketów, także w podkatalogach,
z zachowaniem fragmentów i parametrów URL. Lokalny cel szkicu musi należeć do publikowanego kompletu:
plan, kartki ticketów planu lub `followups/`; pozostałe cele są odrzucane przed publikacją propozycji.
Kolejne commity stanu planu
zachowują szkice, tekst poza tabelą i dopisane końcówki kartek. Pierwszy udany commit zatwierdzonej
poprawki bierze tekst poza tabelą i szkice z nowej zamrożonej propozycji. Podczas oczekiwania
na decyzję commity stanu nadal korzystają z pakietu poprzednio zatwierdzonej wersji:
nie nadpisują szkiców treścią poprawki ani nie dodają jej nowych szkiców. Późniejsze aktualizacje stanu
zachowują tekst z worktree, w tym ręczne dopiski. Próg pierwszego udanego zapisu dotyczy wersji
faktycznie zatwierdzonej; odrzucenie niezatwierdzonej poprawki nie unieważnia wcześniejszego zapisu.
Szkic opisuje zakres do osobnego
zaplanowania; jego zapis nie publikuje karty i nie potwierdza dostępności opisanej funkcji.

## Przepływ karty prowadzony przez Tide (1.2)

Karta na Tablicy uruchamia planowanie albo pojedynczy ticket. Tide prowadzi dziennik karty, zakłada gałąź karty
i osobne gałęzie ticketów, uruchamia kroki oraz pokazuje decyzje w panelu karty. Planowanie Lekkie tworzy propozycję
w tle z opisu i specyfikacji karty. Pełne planowanie odbywa się obecnie w Konsoli; włączenie go do tego przepływu
jest drugim etapem. „Mam plan” przejmuje wskazany plan
bez zapisywania zmian w jego repozytorium. Plan przekazywany dla konkretnej karty ma `card: K-<numer>`; plan bez
tego pola może być podpowiedzią, a nie automatycznie wybranym planem karty.

Pełne planowanie w Konsoli prowadzi rozmowę `grill-with-docs` (pytania i zapis decyzji), potem `to-spec`
i `to-tickets`. Planowanie Lekkie w przepływie karty przygotowuje `spec.md`, `plan.md` i kartki bez
pytań do użytkownika w trakcie kroku; po niezależnym review pokazuje plan do zatwierdzenia.

**Role.** Planujący przygotowuje tabelę i kartki. Inny wykonawca sprawdza plan przed pokazaniem go użytkownikowi;
gdy znajdzie braki, planujący dostaje jedną poprawkę. Autor ticketu wykonuje pionowy wycinek w osobnym worktree,
a recenzent spełnia zasady recenzji według ryzyka (niżej) i zapisuje werdykt. Użytkownik zatwierdza plan albo różnicę po odesłaniu z uwagami;
może też zmienić wykonawcę lub kartkę ticketu, który jeszcze nie wystartował. Tickety bez blokerów mogą biec
równolegle, z limitem kroków dostawcy; bloker rusza po scaleniu poprzedników do gałęzi karty.

**Kontrakt kroku.** Tide przekazuje kartkę z identyfikatorem karty i ticketu, gałęzią, katalogiem roboczym,
ograniczeniami oraz ścieżką katalogu wyjściowego. Autor zapisuje tam notkę `note.md`, a na końcu `result.json`:
`{"result":"done","notes":["note.md"]}`, `{"result":"question","question":"…"}`,
`{"result":"denied","command":"…"}` albo `{"result":"error","reason":"…"}`. Recenzent oprócz tego
zapisuje `review.md` i wynik, np. `{"result":"done","verdict":"APPROVE","blockers":0,
"majors":0,"nits":0,"notes":["review.md"]}`; `REQUEST_CHANGES` wymaga dodatniej liczby `blockers`
lub `majors`. Brak poprawnego wyniku jest błędem
kroku. Pytanie, zgoda i ponowienie przechodzą przez dziennik karty;
wznowienie zachowuje worktree i zmiany.

**Testy ticketu i review.** Przed oddaniem ticketu i poprawek autor uruchamia
`make test-changed BASE=<punkt startu ticketu na gałęzi karty>` i zapisuje polecenie oraz wynik
w notce. Kartka przekazuje przypięty SHA z wyniku utworzenia gałęzi ticketu; późniejszy ruch
gałęzi karty nie zmienia tej bazy. Recenzent sprawdza udany wynik dla oddawanych zmian i nie żąda
osobnego pełnego `make test`. Pełny przebieg nie jest kryterium ticketu ani review. Zasada zastępuje
wymóg pełnego `make test` w kartkach istniejących planów (w tym K-37, K-38 i K-55), bez przepisywania
planów. Jeśli mapa `test-changed` wybierze pełny zestaw, autor wykonuje go bez dodatkowego `make test`.

Wyjątek dla starszej gałęzi bez celu `test-changed`: autor i recenzent uruchamiają pełny `make test`
zamiast niedostępnego celu i zapisują wynik. Szczegóły: [starsze gałęzie](testing.md#starsze-gałęzie-bez-celu-test-changed).

Zasady rozliczenia nieudanego przebiegu i odstępstwa opisuje
[dokumentacja testów](testing.md#wyjątki-od-testów). Odstępstwo nie zmienia wyniku na udany.

**Pytanie z gotowymi opcjami.** `{"result":"question","question":"…"}` może mieć dodatkowo
`"options":[{"title":"…","effect":"…","recommended":true}]`: od 1 do 4 pozycji, `title` niepusty (do 120 znaków),
`effect` — jedno zdanie o skutku wyboru (do 400 znaków, opcjonalny), `recommended` — najwyżej jedna pozycja.
Okno decyzji pokazuje opcje jako karty (polecona pierwsza, żadna nie jest wybrana domyślnie), a Tide zawsze
dokłada na końcu „Inną odpowiedź…” — agent jej nie podaje. Wybór wznawia krok; tytuł i skutek opcji trafiają do
`resume_context`. Niepoprawne lub brakujące `options` (pusty tytuł, więcej niż 4, kilka poleconych, powtórzony
tytuł, zły typ) nie są błędem kroku: okno ma wtedy samo pole odpowiedzi. Pytanie bez `options` działa jak dotąd.
Autor i recenzent nie wykonują push ani scalenia.

**Pytanie o bazę (PT-208).** Każde przekazanie kroku zawiera bieżący pełny ref i przypięty SHA bazy
albo jawne `unknown`. Sam brak pliku nie dowodzi błędnej bazy ani nie pozwala tworzyć zamiennika,
wykonywać checkout, rebase czy reset. Po sprawdzeniu dowodów krok może zapisać:

```json
{"result":"question","question":"Czy zachować bazę?","baseProposal":{"ref":"refs/heads/feature/example","evidence":"Sprawdzona historia wskazanego pliku…"}}
```

`baseProposal` jest opcjonalne. `ref` musi być pełnym lokalnie znanym refem `refs/heads/…` lub
`refs/remotes/…` (do 1024 znaków), `evidence` niepustym zwykłym tekstem (do 4000 znaków).
Usługa sprawdza kandydata w repozytorium projektu karty, bez fetch ani zmiany gita. Nieprawidłowa
propozycja pozostaje informacją przy zwykłym pytaniu; stare pytania i `options` zachowują działanie.

Poprawna propozycja wstrzymuje nowe kroki i integrację wyników tej karty. Procesy mogą dokończyć,
a ich wyniki pozostają zapisane i niescalone. Pytanie i blokada wracają po restarcie; oczekiwanie nie
trzyma blokady repozytorium i nie blokuje innych kart. `ResolveCardStep` przyjmuje `choice: answer`,
`step` oraz strukturalne `baseDecision: keep` albo `change`, niezależnie od tekstu odpowiedzi.
Zachowaj zapisuje zwykłą odpowiedź i odblokowuje kontynuację. Zmień zapisuje oczekiwanie (`waiting: true`,
`changed: false`), pozostawiając pytanie i możliwość Zachowaj. Nie wykonuje operacji git ani nie
potwierdza zmiany; podgląd dostarcza PT-201, aktywację PT-202, skutki i potwierdzenie PT-213.

**Podgląd korekty (PT-201).** `/Board PreviewCardBaseChange(id, request)` przyjmuje pełny `baseRef`
i opcjonalny `baseCommit` (dla zdalnego refu wymagany SHA), albo `step` nierozstrzygniętej propozycji;
puste `{}` wybiera pierwszą oczekującą decyzję o bazie. Walidacja jest taka sama jak przy starcie,
bez fetch i domyślnego HEAD. Odpowiedź zawiera `oldBase`, `newBase`, `baseVersion`, `branches[]`
z zapisanymi punktami startu i commitami, `processes[]`, lokalne zmiany oraz ignorowane ścieżki,
`blockedReasons[]`, `invalidations[]`, `followup` i instrukcję. Dowody są zwykłym tekstem.

Jawny wybór użytkownika w pełnej karcie wywołuje `RequestCardBaseChange(id, {baseRef, baseCommit})`.
Zapisuje wyłącznie decyzję `card.base-requested`, blokuje nowe kroki i integrację, zachowuje istniejące
pytania oraz zwraca ten sam podgląd co propozycja agenta. `decisionStep` identyfikuje decyzję;
`ResolveCardStep` z tym identyfikatorem i `baseDecision: keep` odblokowuje kartę, a `change` jedynie
otwiera podgląd. Zamknięcie/Esc nie zapisuje odpowiedzi ani nie zatrzymuje procesów.

`previewId` jest odciskiem dokładnych SHA, wersji bazy, całego dziennika/mapy, logicznej zawartości indeksów,
statusu, metadanych lokalnych danych i procesów. Oba odczyty są lekkie: bez hashowania treści plików.
Ignorowane katalogi zwijamy przez `ls-files --others --ignored --exclude-standard --directory`;
odcisk obejmuje ich stat, bez wyliczania wnętrza. Zmiana treści pliku wewnątrz takiego katalogu nie musi
zmienić identyfikatora. To odcisk kwalifikacji pustej generacji, bez prawa do kopiowania lub kasowania
lokalnych danych; PT-202 musi zachować stare worktrees wraz z ich zawartością. Limit 4096 odczytów
metadanych na worktree (także dla nieśledzonych zagnieżdżonych repozytoriów) daje odmowę
`unknown-local-state` po przekroczeniu. Odpowiedź zawiera najwyżej 20 ignorowanych ścieżek na gałąź,
`ignoredCount` liczy zwinięte ścieżki/katalogi, a `ignoredTruncated` oznacza skrócenie listy.
Panel pokazuje najwyżej 10 i liczbę pozostałych. Nie jest to liczba plików w ignorowanych katalogach.
Dowody, powody odmowy i procesy poprzednich zleceń mają `previousOrder: true`; kontrola pozostaje zachowawcza.
PT-202 musi ponownie wyliczyć podgląd pod blokadą przed operacją,
wymagać `canChange: true` i równości identyfikatora; każda wykryta różnica wymaga świeżej decyzji. Identyfikator
sam w sobie nie jest zgodą. `canExecute: true` udostępnia osobne końcowe potwierdzenie pustej korekty;
panel wysyła `choice: change-base` z dokładnym ref/SHA i `previewId`. Aktywacja unieważnia stare
zgody, sesje i domknięcie; nowy krok rusza w nowym katalogu i świeżej sesji. Nowa praca podlega
zwykłym testom i review, a karta wymaga nowego podsumowania. Historyczne dane pozostają do odczytu.
Nieznana granica lub stan procesu oznacza odmowę. Zakończony krok i domknięcie na bazowym SHA bez nowego
commita są metadanymi, natomiast każdy commit dokumentów/kodu/WIP/merge wyklucza pustą korektę.

Korekta v1 jest ograniczona do karty bez commitów wkładu i czynnych procesów, zwykle Od razu przed
pierwszą pracą. Commit planu po Lekkie/Mam plan/Pełne, WIP i praca po review (K-49) wymagają odmowy
korekty i zachowania danych. Cel pkt 4 oraz historie 35–37 pozostają częściowe. Wymagania wskazują
szkic [K-50-replay-code](agents/plans/followups/replay-code.md), zweryfikowany w PT-201 i dołączony
do zatwierdzonego planu. Sam ten klucz nie oznacza opublikowanej karty ani dostępnego replay. Zachowaj
pozwala kontynuować; alternatywą jest oczekiwanie i osobne zaplanowanie przeniesienia.

**Review planu.** Krok `plan-review` zapisuje w katalogu wyjściowym `plan-review.json`:
`{"verdict":"APPROVE"|"REQUEST_CHANGES","findings":[{"severity":"blocker"|"major"|"nit","text":"…","tickets":["#5"]}]}`.
`severity`: `blocker` — plan nie może ruszyć bez poprawy, `major` — ważna zmiana przed startem, `nit` — drobiazg;
`tickets` — numery ticketów z planu, których uwaga dotyczy (może być pusta lista). Przy `APPROVE` bez uwag
`findings` to `[]`. Stary format (jeden napis w `findings`) nadal jest przyjmowany jako jedna uwaga bez wagi.
Nieznana waga, uwaga bez tekstu albo `tickets` niebędące listą napisów to błąd formatu. To zmienia tylko wynik
review; format pliku planu i reguły zatwierdzania zostają bez zmian.

**Domknięcie.** Tide scala zaakceptowane tickety do gałęzi karty. Krok `close` przed napisaniem podsumowania
uruchamia raz pełny `make test` na tej gałęzi i zapisuje wynik w podsumowaniu. Nieudane testy obsługuje
jak inne problemy domknięcia: wynik `error` z opisem niepowodzeń zamiast `done`, bez scalenia do głównej.
Domknięcie mimo nieudanego pełnego przebiegu wymaga jawnego rozliczenia odstępstwa i decyzji użytkownika
według [zasad wyjątków od testów](testing.md#wyjątki-od-testów).
Polecenie wymagające zgody lub wykonania poza piaskownicą korzysta ze zwykłego kontraktu `denied`/`outside`;
wznowienie wykorzystuje wynik i nie powtarza udanego przebiegu bez nowych zmian.
`check: manual` dodaje listę „Co przetestować
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

## Recenzja według ryzyka

Zasady uzgodnione 6.11.2026 (K-71, PT-259), decyzja 96 w [rejestrze decyzji](decisions.md); od 10.11.2026
(decyzja 96 z 10.11, K-88/PT-330) dobór recenzenta jest **polityką projektu, nie regułą Tide**.
Stały warunek Tide to tylko nowa, osobna sesja recenzenta; autor nie recenzuje własnej pracy w tej samej sesji.

Polityka projektu Tablicy `reviewIndependence` (plik projektu: `review-independence`; opis pola
w [tide-daemon.md](tide-daemon.md#tablica)):

- `free` — **domyślna** (także projekt bez ustawienia): dowolny recenzent, również ten sam model i ta sama siła
  myślenia (np. Opus → Opus), zawsze w nowej sesji;
- `other-model` — inny model albo ten sam model z inną siłą myślenia, np. Codex mocny ↔ standard;
- `other-family` — recenzent innej rodziny (dostawca/program z katalogu, nie nazwa wykonawcy) niż autor
  i wykonawcy poprawek; opcjonalnie tylko dla klas z listy (`review-independence-classes`, np. `mocny`).

Tide egzekwuje wyłącznie ustawioną politykę: przy sprawdzaniu planu, edycji ticketu w oknie, starcie Od razu,
wyborze celu eskalacji poprawek i w bramce przed startem review. Sama zmiana siły myślenia nie oznacza zmiany
rodziny modeli. Gdy polityka wymaga recenzenta, którego nie ma (brak dostawcy, limit), karta pokazuje powód
i pyta właściciela: zmień recenzenta, poczekaj albo poluzuj politykę projektu.

Lista niżej jest **zaleceniem dla projektów z polityką `other-family`**, nie regułą Tide. W takim projekcie
recenzent **innej rodziny modeli** niż autor (np. Astra / Claude dla autora Codex) jest zalecany dla:

- granic uprawnień i bezpieczeństwa;
- operacji destrukcyjnych;
- migracji trwałego stanu;
- historycznych wznowień i ważności zgód;
- współbieżnych operacji git, blokad i odtwarzania po awarii;
- zmian doboru testów i bramek akceptacji;
- integracji zmieniających wspólny kontrakt kart.

W projekcie z taką polityką przed scaleniem sprawdza się klasę ryzyka i dobór recenzenta; automatyczny wybór
wykonawcy nie zwalnia z tego obowiązku. Wymaganie polityki nie spada automatycznie do tego samego modelu z inną
siłą myślenia ani do braku review — decyduje właściciel. W każdym projekcie review pozostaje obowiązkowe
i kończy się `APPROVE` bez BLOCKER i MAJOR.

## Poprawki koordynatora poza kartą

To wyjątkowa ścieżka dla pilnego odblokowania pracy albo usunięcia regresji. Jedna poprawka usuwa
jedną przyczynę i obejmuje najwyżej **150 zmienionych linii kodu produkcyjnego w 3 plikach**,
liczonych łącznie dla całej poprawki (dodania i usunięcia), bez dzielenia na commity w celu obejścia limitu.
Testy i dokumentacja nie zwiększają limitu kodu produkcyjnego, ale wchodzą do zakresu review.
Funkcje, zmiany kontraktów oraz wszystkie klasy ryzyka z poprzedniej sekcji idą wyłącznie przez kartę
z review **przed scaleniem**, także gdy poprawka jest pilna lub mała.

Przed dostarczeniem poprawki wymagane są test odtwarzający problem (nieudany na przypiętej bazie,
udany po poprawce) oraz testy regresji dobrane do zmiany. Wyniki podlegają zasadom wyjątków od testów.
Koordynator zapisuje wpis w [rejestrze poprawek](agents/coordinator-fixes.md):

- przyczynę, pilność i zakres oraz czas dostarczenia;
- SHA bazy i poprawki;
- dokładne polecenia, wyniki na bazie i po poprawce oraz regresję;
- dotknięte karty i sposób dostarczenia do każdej ich gałęzi (SHA scalenia lub przeniesienia);
- sposób wycofania i stan faktycznego wycofania;
- termin, recenzenta, raport i wynik niezależnego review po fakcie.

Niezależne review musi zakończyć się **w ciągu 24 godzin od dostarczenia poprawki**, a wcześniej,
jeśli ma być scalana karta polegająca na tej poprawce. Zaległe review wstrzymuje kolejne scalenia
tą ścieżką. BLOCKER lub MAJOR wykryty po fakcie oznacza zatrzymanie zależnego działania i naprawę
albo wycofanie poprawki; sposób rozliczenia trafia do rejestru. Samo zlecenie review nie jest jego wynikiem.
Ta ścieżka nie daje zgody na push ani scalenie do głównej bez wyraźnego polecenia użytkownika.

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
