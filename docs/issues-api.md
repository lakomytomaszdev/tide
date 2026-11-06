# Sprawy i CLI Tide — API v1

Sprawa (`S-N`) jest trwałym raportem rozmowy, niezależnym od karty (`K-N`), ticketu i specyfikacji.
PT-218 dostarcza zapis szkicu i odczyty, PT-219 edycję oraz cykl życia; magazyn i publiczny
odczyt załączników — PT-228. Utworzenie sprawy nie uruchamia modelu, nie zakłada karty ani gałęzi projektu.

## Publiczna granica

Magistrala sesji, usługa `org.tide.Daemon`, obiekt `/Issues`, interfejs `org.tide.Daemon.Issues`.
Metody zwracają jeden napis JSON. Nie przyjmują ścieżek do plików klienta ani ścieżek odczytu Tablicy.
CLI jest samodzielnym klientem tego interfejsu; nie importuje pakietu `tide_daemon`.

| Metoda | Argumenty D-Bus | Wynik przy sukcesie, poza wspólną kopertą |
| --- | --- | --- |
| `Info` | brak | `capabilities: ["issue.create", "issue.list", "issue.show", "issue.update", "issue.submit", "issue.reject", "issue.reopen"]` |
| `Create` | `s request` (obiekt JSON opisany niżej) | `issue`, `existing: bool`, `commitError: string` |
| `List` | brak | `issues: [...]` (metadane bez treści raportu), `errors: [...]` |
| `Show` | `s id` (dokładnie `S-N`, N = 1…999999999) | `issue` (metadane i treść) |
| `Update` | `s id, s request` | `issue`, `commitError: string` |
| `Submit` | `s id, s request` | `issue`, `commitError: string` |
| `Reject` | `s id, s request` | `issue`, `commitError: string` |
| `Reopen` | `s id, s request` | `issue`, `commitError: string` |

Wspólna koperta sukcesu: `{"ok":true,"apiVersion":1,...}`.
Błąd: `{"ok":false,"apiVersion":1,"error":"bad-request","message":"…"}`.
`Info` działa także z `--no-board`, wtedy lista możliwości jest pusta; zapisy i odczyty spraw
zwracają `unavailable`. Klient przed operacją sprawdza dokładnie wersję 1 i wymaganą możliwość.
Brak lub niezgodność API kończy polecenie bez zapisu, importu lokalnego i uruchamiania modelu.

### Create — wdrożone w PT-218

```json
{"report":"# Treść raportu\n", "title":"Krótki tytuł", "project":null, "requestId":"rozmowa-2026-11-06-1"}
```

Lista pól jest zamknięta: wymagane `report` i `title`, opcjonalne `project` oraz `requestId`.
Nieznane i powtórzone pola są błędem. `id`, `state`, `version`, daty, powiązanie karty, manifest
oraz wszelkie ścieżki są własnością usługi. Ich przesłanie nie importuje metadanych.

`title` to tekst mający 1–200 znaków, niepusty po `strip()`, bez znaków sterujących U+0000…001F.
Zapis zachowuje podany tytuł. `project` jest istniejącym slugiem projektu Tablicy (1–64 znaków,
małe litery ASCII, cyfry, `.`, `_`, `-`, pierwszy znak alfanumeryczny). Brak pola lub `null`
oznacza brak przypisania. Pusty slug, nieznany i usunięty projekt są błędem. Tytuł i projekt
nie są wyprowadzane z raportu. Walidacja poprzedza rezerwację numeru i publikację sprawy.

`report` jest samą treścią Markdown UTF-8, bez NUL, do **65536 bajtów** włącznie (limit dotyczy
treści po dekodowaniu JSON, nie transportu JSON). Pusty raport jest dozwolony. Początkowa linia
`---` jest zarezerwowana dla frontmatteru i odrzucana, także z BOM, CRLF albo białymi znakami
wokół separatora. Wewnętrzne linie `---` są dozwolone. Usługa nie importuje początkowych metadanych,
nie zmienia treści i generuje własny frontmatter. `Show` rozdziela metadane od oryginalnej treści.
Niepoprawne UTF-8 w pliku odrzuca CLI; niepoprawne sekwencje Unicode w JSON odrzuca również usługa.

`requestId`, jeżeli podane, jest niepustym tekstem 1–200 znaków bez U+0000…001F. Zakres klucza
to całe repozytorium Tablicy. Ten sam klucz i identyczne `report`, `title`, `project` odtwarzają
pierwotną odpowiedź `issue`, również po restarcie (nawet po usunięciu projektu). Brak projektu i
`null` to ten sam ładunek; treści i tytułu nie normalizujemy. `existing` informuje o powtórzeniu.
Inny ładunek pod tym samym kluczem daje `conflict`, bez nowej sprawy. Bez klucza każde wywołanie
jest nową intencją. Po utracie odpowiedzi klient powinien ponowić ten sam klucz i ładunek.

### Update — wdrożone w PT-219

```json
{"expectedVersion":1,"report":"Poprawiona treść","title":"Nowy tytuł","project":null}
```

Dokładnie dozwolone pola: wymagane `expectedVersion` i opcjonalne `report`, `title`, `project`.
Co najmniej jedno z tych trzech pól musi być obecne. Pominięte pole pozostaje bez zmiany.
`null` czyści **wyłącznie projekt**; `report:null` i `title:null` są błędami. Reguły treści,
tytułu i istnienia projektu są identyczne jak dla create. Nieobsługiwane/powtórzone pola są
błędem danych. `expectedVersion` musi być dodatnią liczbą całkowitą (bez bool i ułamka) i jest
warunkiem zgodności z obecną wersją, a nie żądaniem ustawienia `version`. Niezgodność oznacza
`version-conflict`, bez zapisu. Odpowiedź błędu zawiera aktualną wersję jako dodatnią liczbę
całkowitą `currentVersion`, np.
`{"ok":false,"apiVersion":1,"error":"version-conflict","message":"Wersja sprawy uległa zmianie.","currentVersion":2}`.
CLI zwraca kod wyjścia 4 i zachowuje `currentVersion` w wyniku `--json`.
Skuteczna zmiana zwiększa wersję o jeden; `id` i `created` pozostają
bez zmiany, `updated` ustala usługa. Samo ponowne przesłanie tych samych wartości nie zmienia
wersji. Usługa sprawdza warunek także przy takim żądaniu. Nie jest to idempotencja `requestId`.
Zmieniać można `draft` i `ready`; rzeczywista zmiana `ready` wraca do `draft` i wymaga ponownego
submit. Żądanie identycznych wartości zachowuje także stan `ready`. `rejected` wymaga najpierw
reopen, a `accepted` i `archived` odmawiają zmian. Niepoprawny stan daje `bad-request`.

Polecenie:

```text
tide issue update S-N --expected-version N [--report <plik>] [--title <tekst>] [--project <slug> | --clear-project] [--json]
```

`--project` i `--clear-project` wzajemnie się wykluczają. Brak obu nie zmienia projektu.
Projekt jest sprawdzany, gdy pole `project` jest podane i nie jest `null`, także przy żądaniu
identycznych wartości. Pominięcie projektu zachowuje przypisanie, również gdy projekt usunięto;
przed submit należy je poprawić albo jawnie wyczyścić.

### Submit, Reject, Reopen — wdrożone w PT-219

Wszystkie trzy operacje przyjmują wyłącznie `{"expectedVersion":N}`. Wersja musi być dodatnią
liczbą całkowitą. Nieznane i powtórzone pola, bool, ułamek i brak wersji dają `bad-request`.
Obowiązuje ta sama kontrola wersji i odpowiedź `version-conflict` z `currentVersion` co dla update.
Warunek wersji jest sprawdzany przed przejściem stanu; każda skuteczna operacja zwiększa `version`
raz i aktualizuje `updated`, zachowując `id`, `created`, tytuł, projekt, raport i manifest.

| Operacja | Stan wejściowy | Stan po sukcesie |
| --- | --- | --- |
| `Submit` | `draft` | `ready` |
| `Reject` | `draft`, `ready` | `rejected` |
| `Reopen` | `rejected` | `draft` |

Inne przejścia dają `bad-request` bez zapisu. Powtórny submit na `ready` nie jest nową zmianą
ani ponowieniem z kluczem; zwraca odmowę. Przyjęcie sprawy dostarczy PT-221. `accepted` i `archived`
odmawiają wszystkich powyższych operacji.

Submit ponownie sprawdza tytuł, treść UTF-8 i istniejący manifest. Raport nie może być pusty ani
złożony wyłącznie z białych znaków. Projekt, jeśli przypisany, musi nadal istnieć. Manifest musi
mieć dokładnie postać `{"version":1,"attachments":[]}` (liczba całkowita, bez bool i powtórzonych
pól). Błąd danych na dysku, brak manifestu albo nieobsługiwany manifest daje `broken-file`.
Obsługę i weryfikację niepustego manifestu dostarczy PT-228.

Usługa nie interpretuje treści raportu i nie przechowuje listy oczekiwanych, niedostarczonych dowodów.
Sam opis braku pliku przy pustym manifeście nie blokuje jawnego submit. Pozostawienie `draft` po
niedostępnym dowodzie jest obowiązkiem skilla PT-224; użytkownik może świadomie zrezygnować z obrazu,
poprawić raport i wykonać submit. Nie ma dodatkowego pola ani operacji rozstrzygania braków.

```text
tide issue submit S-N --expected-version N [--json]
tide issue reject S-N --expected-version N [--json]
tide issue reopen S-N --expected-version N [--json]
```

## Pliki i trwałość

Stara Tablica nie wymaga migracji. Przy pierwszym zapisie usługa dodaje:

```text
issues/S-1/report.md
issues/S-1/attachments.json
```

Frontmatter `report.md` jest płaski: wartości są skalarami JSON (poprawnymi także w YAML),
klucze to dokładnie `tide-issue: 1`, `id`, `title`, `project` (tekst/null), `state` (początkowo
`"draft"`), `version` (początkowo 1), `created`, `updated` (ISO 8601 UTC), `card` (początkowo null).
Za zamykającą linią `---` jest dokładnie treść raportu. Początkowy manifest to
`{"version":1,"attachments":[]}`. Niepusty manifest wymaga PT-228.

Update i przejścia stanu używają tej samej blokady `.git/tide-issues.lock` co create. Odczyt,
porównanie `expectedVersion` i atomowa podmiana `report.md` odbywają się pod blokadą. Raport i całe
metadane wersji są jednym plikiem, synchronizowanym przez fsync i publikowanym przez rename;
manifest pozostaje bez zmiany. Przegrana równoległa mutacja nie zapisuje żadnego pola.
Cykl nie rezerwuje kolejnego S-N, K-N ani PT-N. Replay create z `requestId` nadal zwraca pierwotną
odpowiedź, ale nie cofa późniejszych zmian na dysku; aktualną wersję odczytuje się przez Show.

Obiekt `issue` ma powyższe pola metadanych poza `tide-issue`, ponadto `report`, `attachments:[]`
i `paths:{report:"issues/S-N/report.md",manifest:"issues/S-N/attachments.json"}`. Są to samodzielne
ścieżki względem Tablicy, niezależne od projektu i karty; API nie służy do odczytu dowolnej ścieżki.
Odczyt odrzuca dowiązania, pliki specjalne, nadmierny rozmiar, niezgodne metadane i złe UTF-8.
`List` zwraca poprawne sprawy oraz osobne błędy uszkodzonych spraw; `Show` uszkodzonej sprawy
daje `broken-file`, nie blokując pozostałych.

Numery S-N są osobne od K-N i PT-N. Lokalny rejestr `.git/tide-issues.json`, chroniony `flock`
(`.git/tide-issues.lock`), zawiera licznik (`format: 2`, `next`). Trwałe potwierdzenia `requestId`
są osobnymi plikami `.git/tide-issue-requests/<sha256-klucza>.json`, zawierającymi skrót ładunku
(`fingerprint`), pierwotny wynik (`issue`) i znacznik publikacji (`published`). Odczyt pojedynczego
klucza nie ładuje całej historii. Limit pliku potwierdzenia uwzględnia maksymalny raport po
ucieczce JSON; zapis sprawdza tę samą granicę co odczyt, bez limitu łącznego rozmiaru historii.
Licznik jest
zapisywany atomowo i synchronizowany na dysk przed publikacją. Nie cofa go usunięcie sprawy ani
revert. Brak rejestru powoduje odbudowę licznika z plików, historii gita i zachowanych potwierdzeń;
uszkodzony rejestr wstrzymuje create. Rejestru i potwierdzeń nie należy usuwać: są lokalnym stanem
usługi i muszą być objęte kopią zapasową `.git`, jeżeli potrzebne jest ich przeniesienie na inny komputer.

Przy pierwszym create dawny rejestr formatu 1 jest rozdzielany pod tą samą blokadą, również
gdy przekroczył dawną granicę 128 MiB. Dopiero po utrwaleniu wszystkich potwierdzeń usługa
atomowo zastępuje go licznikiem formatu 2. Przerwanie pozostawia pełny rejestr formatu 1;
ponowienie bezpiecznie powtarza migrację, zachowując także niedokończone intencje. Ten jednorazowy
odczyt starego rejestru wymaga pamięci proporcjonalnej do jego rozmiaru; dalsze create odczytują
tylko licznik i potwierdzenie wskazanego klucza.

Intencja z kluczem obejmuje pierwotny wynik i jest utrwalana przed publikacją całego katalogu
przez rename. Po awarii podczas publikacji ponowienie tego samego `requestId` dokańcza tę
intencję z tym samym numerem. Bez klucza nieukończona publikacja może pozostawić lukę w numeracji;
numer nie wraca. Rezerwacja bez publikacji nie jest poprawną sprawą. Niedokończona intencja
jest wznawiana przy ponowieniu create, a nie przez sam odczyt listy.

Wyłącznie daemon zapisuje i commituje sprawy. Korzysta z tej samej instancji `BoardRepo` co
Tablica: konflikt merge/revert/rebase daje `git-busy` bez zapisu, a błąd commita po zapisie
pozostawia sprawę i kolejkę commita. `commitError` informuje o tej sytuacji; nie oznacza
niepowodzenia mutacji. Synchronizacja Tablicy dokończy commit, także po restarcie. CLI nie
otwiera Tablicy, nie wykonuje gita i nie ma lokalnej ścieżki zapisu zastępującej usługę.

## Instalacja i użycie

Standardowa instalacja `make install` obejmuje CLI (z istniejącą kopią zapasową przed instalacją).
Sam komponent `install-cli` kopiuje `cli/tide.py` z prawem wykonania do `~/.local/bin/tide`;
`uninstall-cli` usuwa tę kopię. Potrzebny jest systemowy Python 3 i PyQt6 QtDBus, jak dla usługi.
Nie wymaga checkoutu w czasie działania ani `PYTHONPATH`. Repozytorium Tablicy wybiera usługa,
nie klient. Nie uruchamiaj instalacji na prawdziwym HOME w trakcie testu.

```text
tide info --json
tide issue create --report raport.md --title "Opis rozmowy" --request-id rozmowa-1 --json
tide issue create --report raport.md --title "Opis projektu" --project plasma-tide --json
tide issue list --json
tide issue show S-1 --json
tide issue update S-1 --expected-version 1 --title "Poprawiony tytuł" --project plasma-tide --json
tide issue submit S-1 --expected-version 2 --json
tide issue reject S-1 --expected-version 3 --json
tide issue reopen S-1 --expected-version 4 --json
tide issue update S-1 --expected-version 5 --clear-project --json
```

W trybie `--json` stdout zawiera pojedynczy obiekt JSON, również dla błędu; w zwykłym trybie
błąd trafia po polsku na stderr. `show --json` zwraca treść w `issue.report`, bez frontmatteru.
Kody zakończenia:

| Kod | Znaczenie / kody API |
| --- | --- |
| 0 | sukces, także zapis oczekujący na commit oraz częściowa lista z `errors` |
| 1 | nieoczekiwany błąd usługi (`internal`, pozostałe nieznane kody) |
| 2 | argumenty lub dane (`bad-request`), np. „Raport przekracza 64 KiB.” |
| 3 | brak usługi/magistrali, wyłączone sprawy lub niezgodne API (`unavailable`, `api-incompatible`) |
| 4 | konflikt klucza (`conflict`) albo konflikt wersji (`version-conflict`, z `currentVersion`) |
| 5 | brak sprawy (`not-found`), np. „Nie ma sprawy S-99.” |
| 6 | repozytorium zajęte operacją z konfliktami (`git-busy`) |
| 7 | błąd odczytu/zapisu albo uszkodzona sprawa (`io-error`, `broken-file`) |

Zestaw `issues` w rejestrze `tools/test_suites.py` obejmuje testy magazynu i publicznej granicy,
instalację w tymczasowym HOME/XDG oraz proces zainstalowanego CLI z prawdziwym daemonem na
prywatnym D-Bus. Weryfikuje także restart, równoległość, stare karty, niezgodne API i brak usługi.
