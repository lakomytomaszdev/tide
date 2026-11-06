# Testy podczas pracy nad ticketem

W trakcie implementacji uruchamiaj zestawy dobrane do zmiany. Przed oddaniem ticketu i po
poprawkach uruchom `make test-changed BASE=<punkt startu ticketu na gałęzi karty>`.
Używaj przypiętego SHA z kartki kroku, nie przesuwającego się wierzchołka gałęzi karty ani
domyślnego `master`. Zapisz polecenie i wynik w notce. Recenzent sprawdza, że ten przebieg
przeszedł dla oddawanych zmian; nie żąda osobnego pełnego `make test`.

Pełny `make test` uruchom raz przy domknięciu karty (krok `close`), na gałęzi karty po scaleniu
ticketów, przed podsumowaniem. Nieudany przebieg oznacza problem domknięcia: wynik `error`
z opisem niepowodzeń zamiast `done`, bez scalania do głównej. Brak zgody lub konieczność wykonania
poza piaskownicą obsłuż zwykłym kontraktem `denied`/`outside`; po wznowieniu wykorzystaj wynik
przebiegu. Powtarzaj udany pełny przebieg dopiero po nowych zmianach.

Ta zasada zastępuje wymóg pełnego `make test` przed oddaniem ticketu zapisany w kartkach
istniejących planów, w tym K-37, K-38 i K-55. Nie przepisujemy tych planów.

```sh
make test-cardflow           # test_cardflow*.py oraz testy basesuggestions i hostingcli
make test-daemon             # wszystkie testy usługi
make test-board              # okno Tablicy (apps/board/tests)
make test-changed BASE=<SHA-startu-ticketu>
make test                   # raz przy domknięciu karty, na gałęzi karty
make test JOBS=4             # ograniczenie liczby jednoczesnych procesów
make test JOBS=1             # diagnostyka bez równoległości
```

Nazwy wszystkich zestawów wypisuje `make help`. Ich jedynym rejestrem jest
[`tools/test_suites.py`](../tools/test_suites.py): `SUITES` definiuje cele `test-<nazwa>`,
`CHANGE_MAP` wiąże ścieżki z zestawami, `IMPORT_ROOTS` określa kod analizowany przez AST,
`SERVICE_CONTRACT_MODULES` wymienia mały kontrakt procesu usługi, a `SERIAL_MODULES`
oznacza wyjątki od równoległości.
Każdy katalog `TEST_DIRS` musi mieć zestaw; pilnuje tego test rejestru. `make test` nadal bierze
wszystkie katalogi `TEST_DIRS` z plików `mk/*.mk`, także nowo dopisane komponenty.

`make test-changed` porównuje drzewo robocze z merge-base `BASE` i `HEAD` (domyślnie `master`).
Uwzględnia commity gałęzi, indeks, zmiany niezapisane w commicie i pliki nieśledzone poza ignorowanymi.
Usunięcia i przeniesienia kodu lub danych obejmują także starą ścieżkę i wybierają pełny przebieg.

Dla `daemon/tide_daemon/**/*.py` selektor czyta AST kodu usługi i wszystkich katalogów testów,
bez importowania ani wykonywania tych plików. Śledzi `import`, `from ... import ...` (także
względne, aliasy i importy wewnątrz funkcji) oraz stałe nazwy w `importlib.import_module` i
`__import__`, także przez alias modułu lub funkcji (`import importlib as lib`,
`from importlib import import_module as load`, odpowiedniki z `builtins`). Rozpoznaje
zarówno `tide_daemon`, jak i `daemon.tide_daemon`. Wybiera test
importujący zmieniony moduł bezpośrednio albo przez kod pośredni i pomocniki testów,
również poza `daemon/tests`. Zmieniony samodzielny moduł testów wybiera sam siebie.
Brak rozpoznanych importów do zmienionego źródła oznacza dawny zestaw katalogowy.

Graf zachowuje wszystkich znanych pośrednich odbiorców, także przez duże agregatory
i fixture'y (np. `engine`, `FlowInProcess`). Nie odcina łańcuchów według głębokości ani
liczby zależności/odbiorców: poprzednie progi mogły pomijać znane testy zależne.
Zbiór odwiedzonych modułów kończy cykle i ogranicza koszt przejścia do rozmiaru grafu.
Lokalna zmiana bez wielu odbiorców nadal wybiera mały zestaw; wspólny moduł może wybrać szeroki.

**Świadome ograniczenie cardflow (decyzja koordynatora, 06.11.2026, PT-258/K-68):**
dla zmian `daemon/tide_daemon/cardflow/*` dobór pozostaje szeroki. Audyt 31 modułów
wykazał, że każdy prowadzi do wspólnego `engine.py` (albo sam jest silnikiem).
Przykładowe wybory: `basesuggestions.py` — 79 modułów, `hostingcli.py` — 80,
`engine.py` — 78, `state.py` — 130, wobec 75 modułów zestawu `cardflow`.
Zachowujemy graf modułów i wszystkich znanych odbiorców. Redukcja dla lokalnego cardflow
jest jawnie odroczona ze względu na ograniczenie pracy w toku; nie jest wynikiem tego ticketu.
Zawężenie działa dla pozostałych komponentów oraz jawnie przypisanych narzędzi użytkowych.

Gdy rozpoznanego wywołania importera dynamicznego nie da się rozstrzygnąć (np. nazwa
w zmiennej, import względny lub `__import__` z `fromlist`), jego moduł traktowany jest
jako potencjalny odbiorca każdego zmienionego źródła usługi. Do wyboru trafiają ten moduł
testowy i wszystkie testy pośrednio importujące taki moduł. Poszerzenie następuje również,
gdy źródło ma zwykły test statyczny; powód zawiera ścieżkę i linię niepewnego importu.
Nie wykonujemy kodu w celu ustalenia wartości zmiennych.

Każda zmiana kodu lub danych w `daemon/` dodatkowo wybiera `test_dbus_contract.py` (start procesu i /Agents)
oraz `test_service_smoke.py` (ticket → review → domknięcie przez /Board). Dzięki temu
uruchomienie usługi w osobnym procesie ma pokrycie mimo braku krawędzi importu.
To analiza statyczna: dowolne przekazywanie funkcji importera jako wartości lub refleksja
mogą wykraczać poza rozpoznawane wywołania; zależności danych i wszystkie scenariusze D-Bus
nie wynikają z grafu importów. Pełny test przy domknięciu pozostaje wymagany.

Zmiana wspólnych danych lub pomocników w katalogach testów, `__init__.py` oraz modułu testów
importowanego przez inne testy wybiera pełny przebieg. `SHARED_TEST_MODULES` dodatkowo
oznacza fixture'y `FlowCase` i `FlowInProcess`. Błąd odczytu/składni podczas analizy AST
również przełącza na pełny zestaw. Brak zadeklarowanego modułu kontraktowego jest błędem.
Dokumenty Markdown (`*.md`) w katalogu głównym, `docs/` i komponentach nie wybierają testów
kodu, także przy usunięciu dokumentu. W mieszanym diffie dobór wynika z pozostałych plików.
W `docs/` pomijane są również `.rst`, `.txt` i ilustracje (`.png`, `.jpg`, `.jpeg`, `.svg`, `.gif`, `.webp`).
Wyjątek nie obejmuje Markdown w `tools/`, `mk/`, katalogach testów ani `fixtures/`: wspólne fixture'y
mogą zawierać dokumenty używane jako dane. Kod i konfiguracja w `docs/` nie są pomijane.
Narzędzia użytkowe z `FILE_TEST_MODULES` w `tools/test_suites.py` mają jawne kontrakty:
`board_compat_check.py`, `question_corpus.py`, `next_id.py`, `check_plans.py`, `sessions_preview.py`.
Wybierają wyłącznie zadeklarowane moduły testów; brak takiego modułu jest błędem konfiguracji.
Pozostałe `tools/` (w tym cały uruchamiacz, rejestr i benchmarki) zachowują pełny przebieg.
Ścieżka spoza mapy (np. `Makefile`, `mk/`, nieznane `tools/` lub wspólny moduł motywów)
uruchamia pełny przebieg. Pozostałe komponenty zachowują mapę katalogową: najdłuższy
prefiks wygrywa, np. `apps/board/` wybiera `board`. Ich testy często ładują kod po ścieżce
albo badają QML, więc nie zawężamy ich na podstawie niekompletnych importów.

Selektor wypisuje liczbę i każdy wybrany moduł wraz ze zmienionym plikiem oraz powodem
(łańcuch AST, kontrakt, mapa katalogowa albo pełny przebieg). Powody zapisuje też w polu
`selection` pliku `summary.json`. `tools/test_runner.py --changed --base <SHA> --list`
pokazuje ten sam dobór bez uruchamiania testów. Brak zmian kończy się sukcesem bez testów;
nieistniejąca baza kończy się błędem z prośbą o `BASE`, bez cichego pomijania kontroli.
Gdy mapa zmian wybierze pełny zestaw w tickecie, wykonaj ten przebieg; nie uruchamiaj dodatkowego
`make test` tylko po to, by oddać ticket. Domknięcie nadal sprawdza zintegrowaną gałąź karty.

Pomiar obu selektorów: `python3 tools/test_changed_benchmark.py --base <SHA-startu-ticketu>`.
Procedura porównuje diffy pięciu ostatnich commitów bez merge z tej bazy. Opcja
`--history-ref <SHA>` wybiera inny punkt historii **tylko do pomiarów**: pozwala uwzględnić
np. dokumentacyjną kartę K-71 powstałą po starcie ticketu, bez zmiany jego bazy ani checkout.
Następnie uruchamia publiczne `make test-changed BASE=<SHA-startu-ticketu>`.
Testy obu selektorów wykonuje na jednym bieżącym drzewie kodu; identyczne zestawy benchmarku
mierzy raz. Publiczna walidacja pozostaje niezależnym przebiegiem. Podaje liczbę
modułów i rzeczywisty czas wykonania, a osobno czas selekcji AST. `--output <katalog>` zachowuje
logi, `comparison.json` i tabelę `report.md`, w tym SHA HEAD i jego drzewa, bazę, polecenie
oraz odcisk drzewa roboczego; wznowienie w tym samym katalogu wykorzystuje
ukończone pomiary, o ile drzewo kodu, JOBS, baza i zakres historii się nie zmieniły. To pełna procedura testowa wymagająca
środowiska, w którym działają prywatne gniazda i piaskownice testów.
Nieudana walidacja nie kasuje zebranego porównania, ale pozostaje zapisana w raporcie i powoduje
niezerowy wynik całej procedury. Gotowa tabela czasów nie oznacza wtedy udanego `test-changed`.

## Równoległość i wynik

Domyślnie liczba procesów to liczba rdzeni dostępnych procesowi minus dwa, minimum jeden.
`JOBS=N` nadpisuje ten limit. Wszystkie przebiegi naraz (np. kilka ticketów Tide) dzielą wspólną pulę
miejsc maszyny o tej samej wielkości (`$XDG_RUNTIME_DIR/tide-test-slots`, blokady `flock`): drugi
`make test` nie dokłada kolejnych procesów, tylko czeka na wolne miejsca modułów. `TIDE_TEST_SLOTS=N`
zmienia wielkość puli, `TIDE_TEST_SLOTS_DIR` jej katalog. Każdy plik `test*.py` jest osobnym procesem systemowego Pythona;
`unittest discover` używa tego samego katalogu najwyższego `.` i tego samego wzorca jak wcześniej.
Nie dzielimy klasy ani pojedynczego testu. Zachowujemy także powtórzenia klas importowanych
przez inne moduły, które uruchamiał dotychczasowy `discover`. Hook pakietu `load_tests` wymaga
osobnego dostosowania runnera; obecny runner odmawia zamiast zgubić takie testy.

Procesy mają oddzielny stan Pythona i środowisko, ale muszą izolować własne zasoby zewnętrzne:
`TemporaryDirectory`, prywatny D-Bus i port przydzielany przez system (`bind(..., 0)`). Jeśli test
wymaga wspólnego zasobu, dopisz jego ścieżkę do `SERIAL_MODULES` z konkretnym uzasadnieniem.
Takie moduły biegną pojedynczo dopiero po wszystkich równoległych. Pusta lista oznacza, że
nie stwierdzono potrzeby serializacji; nie dodawaj wyjątków wyłącznie z powodu używania Qt lub D-Bus.
Limit dotyczy jednego wywołania runnera; kilka jednoczesnych wywołań `make test` ma niezależne limity.

W PT-215 osobno oznaczono `test_cardflow_review.py` (tick domknięcia konkurujący z callbackiem
decyzji) i `test_cardflow_dbus.py` (odtworzenie pliku dziennika widoczne przed ostrzeżeniem
w migawce). Oba przeszły po 3 próby pojedyncze, lecz pod obciążeniem 14 procesów miały
odpowiednio 1/5 i 2/4 porażek. Uzasadnienia są przy wpisach `SERIAL_MODULES`; testy nadal
się wykonują i ich błędy trafiają do wspólnego raportu. `FlowInProcess` dodatkowo kończy
wątki i dostarcza ich callbacki przed restartem lub usunięciem fixture, aby praca starego
silnika nie przechodziła do następnego testu.

Logi trafiają do unikalnego `build/test-results/run-*/`: plik `.log` i `.json` na moduł oraz
`summary.json` z czasem, kodami wyjścia, licznikami i identyfikatorami wykonanych testów.
Po ukończeniu modułu widać nazwę, wynik i czas; dla nieudanego także jego log.
Runner czeka na wszystkie moduły i na końcu wypisuje pełną listę niepowodzeń, błędów,
nieoczekiwanych sukcesów i awarii procesów wraz z nazwami modułów (także błędy podtestów).
Kod wyjścia jest niezerowy, jeśli cokolwiek się nie powiodło.

`JOBS=1` zachowuje nowy podział na procesy; pomiar dawnego przebiegu musi uruchamiać całe
katalogi `discover` kolejno. Raport pomiaru PT-215 zapisuje oba sposoby i liczbę testów.

`make test-benchmark` wykonuje takie porównanie na tej samej maszynie: najpierw pełny
`discover` każdego katalogu kolejno, potem publiczne `make test`. Zapisuje czasy, kody wyjścia
i porównanie identyfikatorów testów **z krotnością powtórzeń** w `build/test-benchmark/comparison-*/`
(`comparison.json`, `report.md`, logi). Porównuje też błędy, pominięcia i oczekiwane porażki.
Przy błędzie kontynuuje pozostałe katalogi, aby porównanie
nie urywało się przedwcześnie. Wymaga tego samego środowiska co pełne testy, w tym prywatnych
gniazd D-Bus, lokalnych serwerów atrap i piaskownic uruchamianych przez testy. Nie uruchamiaj
obok innego pełnego przebiegu, bo pomiar czasu byłby zakłócony. `TEST_OUTPUT=<katalog>` pozwala
wskazać inny katalog nadrzędny wyników; każde wywołanie nadal tworzy unikalny podkatalog.
