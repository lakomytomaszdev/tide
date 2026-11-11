# Testy podczas pracy nad ticketem

## Zasada ogólna i kontrakt testów projektu

Zasada K-62 obowiązuje w każdej technologii: w tickecie i po poprawkach autor uruchamia testy dobrane do zmian
od przypiętego punktu startu ticketu, recenzent sprawdza ten przebieg, a pełne testy projektu są wykonywane raz
przy domknięciu karty. Polecenia nie są wpisane w Tide na stałe, tylko pochodzą z **kontraktu testów projektu**:
`tests.changed` (testy dobrane do zmian, z parametrem `{base}` — przypięty SHA startu ticketu) i `tests.full`
(pełne testy przy domknięciu) w profilu projektu Tablicy (`projects/<slug>/box.yaml`, opis:
[tide-daemon.md](tide-daemon.md#kontrakt-testów-projektu-k-69-pt-278)). `make test-changed BASE=…` i `make test`
poniżej to polecenia **plasma-tide**, czyli przykład kontraktu, a nie wymóg dla innych projektów (np. `dotnet test …`
albo `npm test` w ich kontrakcie). `CLAUDE.md` i `AGENTS.md` plasma-tide zostają przy poleceniach `make`.

Dziewięć reguł doboru i zgód (numeracja K-69, do której odsyła [standard planu](agent-plan-standard.md#przepływ-karty-prowadzony-przez-tide-12), sekcje „Testy ticketu i review” i „Domknięcie”):

1. **Kolejność źródeł.** Polecenia testów pochodzą z zatwierdzonego kontraktu, a gdy go nie ma, z wykrycia
   w `Makefile` (z `mk/*.mk`), a gdy nie ma wykrycia, z zasad projektu (`CLAUDE.md`/`AGENTS.md`).
2. **Brak pola w kontrakcie.** Brakujące pole zatwierdzonego kontraktu uzupełnia zasada ogólna (zasady projektu),
   nie wykrycie z `Makefile`.
3. **Wykrycie.** Wykrycie bada cele niezależnie: cel `test-changed` daje `changed`, cel `test` daje `full`,
   a przy braku celu `test` pełne testy wykonuje się według zasad projektu.
4. **Zatwierdzenie.** Kontrakt jest zatwierdzony tylko przez okno projektu albo przez `UpsertProject` wywołane spoza
   kroku agenta; sam zapis pliku `box.yaml` (przez krok, ręcznie, cofnięcie commita) nie jest zatwierdzeniem, a kontrakt
   niezatwierdzony — także przy kolizji magazynu odcisków z obszarem zapisu kroku — nie jest stosowany i nie daje
   automatycznych zgód
   ([zatwierdzenie](tide-daemon.md#zatwierdzenie-kontraktu-testów-pt-285),
   [ochrona](tide-daemon.md#ochrona-zatwierdzenia-przed-krokiem-agenta-pt-286)).
5. **Zgoda na polecenie.** Automatyczną zgodę dostaje tylko literalne pojedyncze polecenie zatwierdzonego kontraktu
   bez znaku `*`, z podstawionym przypiętym SHA bazy; `changed` bez znanej bazy zgody nie dostaje
   ([zgoda](tide-daemon.md#zgoda-na-polecenia-kontraktu-pt-280)).
6. **Pierwszeństwo zakazów.** Zakazy projektu (`denied-commands`) i Tide (np. `make install`) mają pierwszeństwo
   przed poleceniem kontraktu: zakazane polecenie nie dostaje zgody.
7. **Warunek `legacy`.** Bez zastosowanego (zatwierdzonego) kontraktu tryb `legacy` zachodzi, gdy gałąź ma cel `test`
   bez `test-changed`. Z zatwierdzonym kontraktem zachodzi tylko wtedy, gdy zatwierdzone `changed` jest zwykłym
   `make test-changed …` (bez opcji `make`, np. `-C`) i gałąź ma cel `test` bez `test-changed`; inne zatwierdzone
   `changed` (np. `dotnet test …`, `make -C … test-changed` albo puste) nie dają `legacy` i obowiązuje polecenie
   kontraktu albo zasada ogólna. W `legacy` pełny `make test` jest testem ticketu i poprawek, a recenzent sprawdza jego
   wynik w notce (zob. [starsze gałęzie](#starsze-gałęzie-bez-celu-test-changed)).
8. **Domknięcie.** Krok `close` raz uruchamia pełne testy (`tests.full` zatwierdzonego kontraktu, wykryte `make test`
   albo pełne testy według zasad projektu), a ich porażka kończy krok wynikiem `error`, nie `done`.
9. **Ponowienie.** Ponowienie padniętych modułów ogranicza tylko to, co się uruchamia: nie zamienia wcześniejszej
   porażki w sukces, a jej rozliczenie wymaga [wyjątków od testów](#wyjątki-od-testów)
   (zob. [ponowienie](#ponowienie-padniętych-modułów)).

Te reguły nie osłabiają zasad wyjątków od testów ani wymogu pełnego przebiegu przy domknięciu.

## Polecenia plasma-tide (przykład kontraktu)

W plasma-tide kontrakt odpowiada celom `make`: `make test-changed BASE=<SHA>` (`changed`) i `make test` (`full`).

W trakcie implementacji uruchamiaj zestawy dobrane do zmiany. Przed oddaniem ticketu i po
poprawkach uruchom `make test-changed BASE=<punkt startu ticketu na gałęzi karty>`.
Używaj przypiętego SHA z kartki kroku, nie przesuwającego się wierzchołka gałęzi karty ani
domyślnego `master`. Zapisz polecenie i wynik w notce. Recenzent sprawdza, że ten przebieg
przeszedł dla oddawanych zmian; nie żąda osobnego pełnego `make test`.

Pełny `make test` uruchom raz przy domknięciu karty (krok `close`), na gałęzi karty po scaleniu
ticketów, przed podsumowaniem. Nieudany przebieg oznacza problem domknięcia: wynik `error`
z opisem niepowodzeń zamiast `done`. Wyjątek dla domknięcia wymaga jawnego rozliczenia i decyzji
użytkownika według zasad poniżej; scalenie do głównej nadal wymaga osobnego polecenia.
Brak zgody lub konieczność wykonania
poza piaskownicą obsłuż zwykłym kontraktem `denied`/`outside`; po wznowieniu wykorzystaj wynik
przebiegu. Powtarzaj udany pełny przebieg dopiero po nowych zmianach.

Krok w tle (ticket, poprawki, domknięcie) uruchamia w piaskownicy kroku tylko testy celowane
(`make test-cardflow`, `make test-daemon`, `make test-board` albo `tools/test_runner.py --modules`).
Wymagany `make test-changed BASE=…` (a przy domknięciu pełny `make test`) zgłasza od razu jako
polecenie `outside`, bez wcześniejszej próby w piaskownicy. 08.11.2026 kroki puszczały pełny przebieg
najpierw w piaskownicy (moduły padały bez D-Bus, zapisu do /tmp i zagnieżdżonego bwrap), a potem drugi raz
poza nią — obciążenie maszyny 40–58 na 16 rdzeniach. Tekst w kartkach: `sandbox_tests`
w `daemon/tide_daemon/cardflow/handoff.py`.

Ta zasada zastępuje wymóg pełnego `make test` przed oddaniem ticketu zapisany w kartkach
istniejących planów, w tym K-37, K-38 i K-55. Nie przepisujemy tych planów.

```sh
make test-cardflow           # test_cardflow*.py oraz testy basesuggestions i hostingcli
make test-daemon             # wszystkie testy usługi
make test-board              # okno Tablicy (apps/board/tests)
make test-issues             # magazyn spraw i proces CLI na prywatnym D-Bus
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
zarówno `tide_daemon`, jak i `daemon.tide_daemon`. Graf uwzględnia istniejące
`__init__.py` wszystkich pakietów nadrzędnych importowanego modułu, także dla stałej
nazwy importu dynamicznego. Wybiera test
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

## Wyjątki od testów

### Budżety oczekiwania przy granicy hostowego Git

Granica hostowego Git (PT-264) działa tylko dla danych pudełka: przy `box.enabled`,
dla repozytorium oznaczonego `tide-boundary` i w modułach ścieżki pudełkowej. Testy
z wyłączonym pudełkiem uruchamiają więc Git jak przed PT-264 i mają pierwotne budżety
oczekiwania: 30 s dla callbacku korekty bazy (15 s dla `BaseChangeInProcessTest.call`),
10 s dla callbacku scalenia i 40/30/25 s dla zakończenia planu fal. Wcześniejsze
podniesienie do 120/60/120 s kompensowało narzut granicy przy każdym wywołaniu
(pomiar właściciela 08.11: suma czasów modułów ~4× bazy) i zostało cofnięte razem
z warunkową granicą (`docs/agents/pt-264/decisions.md`, „Granica Gita warunkowa”).
Scenariusze granicy sprawdzają moduły z włączonym pudełkiem (`boundary_on`
w `daemon/tests/test_host_git_invocation.py`); nie mierzą czasu odpowiedzi.
Krótkie okna sprawdzania współbieżności i braku działania pozostają oddzielne. Test priorytetu raportu zwalnia
pierwszą atrapę dopiero po potwierdzeniu obecności raportu w kolejce, zamiast polegać
na dwusekundowym opóźnieniu atrapy.

Test równoległego StartCard i odczytu CLI używa bariery `release-hosting`: rzeczywisty
proces atrapy CLI musi nadal czekać, gdy nadejdzie poprawna odpowiedź StartCard.
Budżet odpowiedzi wynosi 60 s, a atrapa i jej deadline 120 s; zwolnienie w `finally`
kończy CLI kontrolowanym błędem i pozwala sprawdzić ostrzeżenie. To sprawdzenie
niezależności operacji zastępuje dawny wyścig odpowiedzi z 2-sekundowym oknem przy
4-sekundowym timeout CLI. Pomiar wykazał 5,66 s lokalnego startu z chronionym Git;
stos pokazywał pracę rezerwacji, podczas gdy osobny wątek nadal czekał na CLI.
Test nie potwierdza już dawnego progu 2 s. Testy timeoutu hostingcli pozostają osobne.

Dwie próby edycji pracującego ticketu używają tej samej bariery `release-agent`,
aby naturalny koniec ośmiosekundowej atrapy nie zastąpił sprawdzanego zatrzymania.
Po zwolnieniu oczekiwanie końca planu ma pierwotny budżet 25 s. Test poświadczeń kolejnych programów czeka na domknięcie poprzedniej
karty: koniec jej ticketu nie zwalnia jeszcze blokady jednego repozytorium.

Zmiana budżetu wymaga zmierzonego uzasadnienia i review tak jak kod testu. Nie zastępuje
naprawy błędnego wyniku, nie wyłącza granic ani kontroli puli/izolacji i nie jest
odstępstwem od wcześniejszych nieudanych przebiegów. Po zmianie obowiązuje nowy
`make test-changed`; wcześniejsze porażki pozostają w notkach.

### Rozliczenie nieudanych przebiegów

Nieudany test pozostaje nieudanym wynikiem. Udana ponowna próba ani „osobno przechodzi” nie zastępują
pierwszego wyniku i nie są samodzielnym uzasadnieniem odstępstwa. Zachowaj logi obu przebiegów.
Odstępstwo w notce ticketu, a przy domknięciu również w podsumowaniu karty, zawiera:

- pierwszy wynik: polecenie, SHA, nieudane testy i odnośnik do logu;
- wynik tego samego testu na przypiętej bazie ticketu oraz na `master`, z zapisanymi SHA,
  poleceniami i warunkami porównania (bez zmiany zarządzanej gałęzi);
- obciążenie maszyny przy niepowodzeniu i porównaniu, w tym równoległe przebiegi i limit procesów;
- uzasadnienie oraz dokładny zakres odstępstwa: które niepowodzenia obejmuje, a które nadal blokują;
- właściciela poprawki i sposób śledzenia jej wykonania.

Brak danych oznacz jawnie; nie uznawaj bez dowodu, że problem występował już na bazie albo wynikał
z obciążenia. Recenzent ocenia uzasadnienie i zakres odstępstwa. Domknięcie karty z nieudanym pełnym
przebiegiem jest możliwe tylko z takim jawnym rozliczeniem i jawną decyzją użytkownika o przyjęciu
odstępstwa. Do tego czasu krok zgłasza problem domknięcia, zachowując nieudany wynik. Przyjęcie
odstępstwa nie zmienia wyniku testu na sukces i nie zastępuje osobnej zgody na scalenie do głównej.

## Bezpośrednie testy usługi i izolacja stanu

Z katalogu głównego repozytorium obsługujemy trzy bezpośrednie wejścia:

```sh
python3 -m unittest daemon.tests.test_state_isolation
python3 -m unittest discover -s daemon/tests -t . -p 'test_state_isolation.py'
python3 -m pytest -q daemon/tests/test_state_isolation.py
```

Wymagany jest interpreter z zależnościami danego testu (dla trzeciego wejścia także `pytest`).
Nazwa modułu lub wzorzec może wskazywać dowolny test usługi, także nowy `test_cardflow*.py`.
`daemon.tests.__init__` izoluje cały pakiet przed importami produktu. Uruchamianie samego pliku,
które pomija wymagane importy pakietowe, nie jest dodatkowym interfejsem testowym.

Każdy bezpośredni proces dostaje własny `tide-test-state-*` z `tempfile.mkdtemp`, nawet jeśli
zastał już `XDG_STATE_HOME` sesji. `HOME` pozostaje bez zmian. Domyślny `Journal` zapisuje kopie
w testowym `$XDG_STATE_HOME/tide/cardflow/`; nie zmieniamy produkcyjnego `mirror_root` ani nie wyłączamy kopii.
Repozytoria, tablice i świadome nadpisania środowiska testu (również `patch.dict`) muszą wskazywać
wyłącznie syntetyczne katalogi fixture. Izolacja domyślnego XDG nie zastępuje tej odpowiedzialności.

Runner i inicjalizacja pakietu używają wspólnego kontraktu `tools/test_isolation.py`:
`TIDE_TEST_ISOLATED=1` oraz `TIDE_TEST_STATE_DIR` wskazują istniejący bezwzględny katalog równy
`XDG_STATE_HOME`. Sam XDG, sam marker lub niespójny komplet nie oznaczają odziedziczonej izolacji.
Potomkowie testu, także rzeczywista usługa, dziedziczą środowisko i zapisują do tego samego katalogu.
Potomek importujący testy pożycza go bez rejestrowania usunięcia. `state_environment` służy runnerowi
do jawnego przekazania nowego testowego katalogu; jego właściciel musi go wcześniej utworzyć i później usunąć.
Świadome nadpisanie XDG w fixture wymaga również przekazania zgodnego kontraktu, jeżeli potomny
proces importujący pakiet testów ma korzystać z tego nadpisania.

Przed zastąpieniem XDG zapamiętujemy w `TIDE_TEST_ORIGINAL_STATE_ROOTS` JSON z polami
`xdg_state_home` (efektywny pierwotny XDG, z domyślnym HOME/.local/state) i
`default_tide_state_home` (pierwotny HOME/.local/state/tide). Ten sam zapis dziedziczą potomkowie.
Te korzenie wykorzystuje strażnik zapisów opisany niżej.

Przy zwykłym zakończeniu bezpośredniego procesu jego `atexit` usuwa własny katalog. Potomkowie
muszą zakończyć pracę przed rodzicem; nie usuwają pożyczonego katalogu (także po fork).
Nagłe zabicie procesu bez wykonania `atexit` może pozostawić katalog tymczasowy.
Przy uruchomieniu przez runner właścicielem pozostaje runner: jak dotychczas usuwa katalog po
zakończeniu modułu, także nieudanego, a diagnostyka pozostaje w `.log`, `.json` i `summary.json`.
Testy izolacji porównują lokalizacje i zawartość syntetycznych zapisów, nie mtime stanu użytkownika.

## Strażnik zapisów testów usługi

`tools/test_isolation.initialize()` włącza również `tools/test_guard.py`, przed importem produktu.
Dotyczy to całego pakietu `daemon.tests`, przy bezpośrednim unittest/pytest oraz przez runner,
bez dodatkowych fixture w modułach. Chronimy pierwotne `HOME/.local/state/tide` oraz
`<pierwotny XDG_STATE_HOME>/tide`. Zapamiętujemy ścieżki oryginalne i rozwiązane przez `realpath`;
sprawdzamy także dowiązania do katalogów i plików, nieistniejące jeszcze cele, ścieżki względem
`cwd`/`dirfd`, obie strony rename/link i usuwanie lub przeniesienie przodka chronionego katalogu.
Dowiązania rozwiązujemy komponent po komponencie w kontekście zgłaszającego procesu.
`/proc/self` i `/proc/thread-self` zachowują jego identyfikator procesu/wątku również wtedy,
gdy pojawią się dopiero w celu kolejnego dowiązania, np. przez `/dev/fd/N` albo własny łańcuch
aliasów. Ponowne otwarcie chronionego pliku przez legalny deskryptor O_RDONLY z O_TRUNC
jest blokowane przed wyzerowaniem, także w potomnej powłoce. Legalne aliasy poza ochroną
zachowują prawo zapisu; usunięcie samego zewnętrznego dowiązania nadal jest dozwolone.

Strażnik wymaga Linux x86-64, seccomp user notification (z obsługą CONTINUE) i dostępu przodka
do `/proc/<pid>/{mem,fd,cwd}`. Nie wymaga kompilatora, roota, zmiany praw katalogu ani montowań.
Proces wejściowy staje się nadzorcą, a jego potomek kontynuuje test. Filtr jądra obejmuje ten proces,
nowe wątki i potomków przez fork/exec, także obce programy, statyczne pliki wykonywalne oraz procesy
z wyczyszczonym środowiskiem. Nadzorca jest subreaperem: osierocony proces po double-fork/setsid
lub zatrzymaniu usługi nadal ma go za przodka, zachowuje ochronę i dostęp do legalnych celów.
Nie zmieniamy systemowego `ptrace_scope` ani uprawnień innych procesów.
`TIDE_TEST_GUARD` podaje PID nadzorcy wyłącznie diagnostycznie:
odziedziczoną ochronę potwierdza bezskutkowa próba `prctl` obsługiwana przez filtr, a nie wartość env.

Nadzorca odczytuje cele wywołań systemowych przed ich wykonaniem. Odmawia zapisywalnego
open/creat, truncate, unlink/rmdir, mkdir, rename, link/symlink i zmian metadanych w chronionych
korzeniach; kontroluje też zapisy przez deskryptory i współdzielone mapowania pliku.
W chronionych korzeniach odrzuca każde plikowe `MAP_SHARED`/`MAP_SHARED_VALIDATE`, także
z początkowym `PROT_READ` lub `PROT_NONE`: późniejsze `mprotect` lub `pkey_mprotect` mogłoby
umożliwić zapis bez kolejnego kontrolowanego wywołania plikowego. Dotyczy to również
odziedziczonego deskryptora O_RDWR. Odmowa następuje już przy `mmap`, daje EPERM i trwały
kod 125 scenariusza. To zachowawcze ograniczenie obejmuje również współdzielone mapowania
używane wyłącznie do odczytu. Zwykły odczyt, prywatne mapowania `MAP_PRIVATE` i mapowania
anonimowe pozostają dozwolone; prywatna zmiana pamięci nie zmienia pliku.
Python dostaje `PermissionError`, a np. `/bin/sh`, `rm` czy `mv` — systemowy `EPERM`.
Rozróżniamy mutację celu dowiązania od zmiany samego wpisu: unlink/rename zewnętrznego
dowiązania jest legalne, jeśli jego wpis i katalog nadrzędny leżą poza ochroną; operacja na pliku
wewnątrz dowiązanego katalogu nadal jest blokowana. Obsługujemy także `utimensat(fd, NULL, …)`
(futimens używany przez tar): oceniany jest plik deskryptora, nie adres NULL.
Mutujące `openat2` z `RESOLVE_IN_ROOT` jest zawsze odrzucane przez `EPERM` i powoduje
kod 125 scenariusza, także dla celu poza chronionymi korzeniami. Ten tryb zmienia znaczenie
`/`, `..` i absolutnych dowiązań względem `dirfd`; strażnik nie próbuje zastępować jego
semantyki zwykłym `realpath`. Odczyt przez ten tryb pozostaje dozwolony.
Nie polegamy na monkeypatchu `open`, argv potomka ani obserwowaniu zmian po fakcie.
Nadzorca nie podlega filtrowi i zachowuje informację o naruszeniu do końca całego drzewa.
Każda odmowa wypisuje na jego stderr `TIDE TEST GUARD:` i JSON z `scenario` (PID testu),
`pid` sprawcy, `operation` i `targets`. Po naruszeniu wynik całego uruchomienia to **125**,
nawet jeśli unittest/pytest wypisze sukces, produkt przechwyci wyjątek, zignoruje błąd potomka
albo wykona `os._exit(0)`. Runner zachowuje odmowy w logu modułu i traktuje jego kod jako błąd.

Awaria inicjalizacji daje `setup_error` i kod 125, bez kontynuowania niechronionego produktu.
Utrata dostępu nadzorcy do celu również oznacza odmowę i błąd scenariusza. Mechanizmy bez
obsługiwanej ścieżki (Linux AIO, io_uring, open-by-handle) dostają `ENOSYS`, aby biblioteka mogła
użyć kontrolowanych odpowiedników. Nieaktualne powiadomienie po zakończeniu procesu nie jest
naruszeniem: przed odczytem `/proc` sprawdzamy identyfikator powiadomienia w jądrze.
Nadzorca odbiera statusy osieroconych procesów. Po zakończeniu głównego procesu testu daje
pozostałym potomkom sekundę na zakończenie (nadal kontrolując ich mutacje), następnie kończy
je przez SIGKILL i odbiera ich statusy przed oddaniem wyniku. Dotyczy to wyłącznie jego drzewa,
również procesów we własnych sesjach; niezależne usługi pozostają poza tym sprzątaniem.
Sygnał HUP filtra nie zastępuje `waitpid`: po nim nadzorca odbiera wszystkie pozostałe statusy,
także gdy ostatni proces zakończył się między wcześniejszym WNOHANG a odczytem HUP.
Fixture nadal powinny sprzątać procesy podczas testu. Zewnętrzny SIGTERM/SIGINT jest przekazywany
procesowi testu, o ile ten jeszcze żyje.

To ochrona przed przypadkowym przeciekiem testów, nie piaskownica do wykonywania wrogiego kodu.
`SECCOMP_USER_NOTIF_FLAG_CONTINUE` ma wyścig między oceną ścieżki a operacją (np. celowa równoległa
podmiana dowiązania lub argumentów syscall). Nie gwarantujemy ochrony przed takimi atakami,
istniejącymi twardymi dowiązaniami poza korzeniami, zmianą przestrzeni montowań ani dowolnym
urządzeniowym ioctl. Strażnik nie chroni całego HOME; fixture nadal wskazują syntetyczne dane.
Proces usługi uruchomiony niezależnie od drzewa testu zachowuje swoje prawa. Jego legalne zmiany
nie są naruszeniem testu: nie porównujemy mtime ani migawki wspólnego katalogu użytkownika.

Test kontrolny `python3 -m unittest scripts.tests.test_state_guard` uruchamia osobne negatywne
scenariusze pod unittest i pytest (gdy jest dostępny). Oczekuje kodu 125, diagnostyki i identycznych
plików syntetycznego stanu. Oddzielnie sprawdza Python, potomka z domyślnym `Journal`, dalszego
potomka, rzeczywisty tide-daemon na prywatnej magistrali oraz `/bin/sh` z rzeczywistymi poleceniami
zapisu/usunięcia/rename. Próby `daemon/tests/guard*_probe.py` nie należą do automatycznego discover:
uruchamia je wyłącznie test kontrolny. Brak prywatnej magistrali pomija próbę pełnej usługi,
nie pozostałe dowody. Do oddania ticketu potrzebny jest przebieg w środowisku z tą magistralą.

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
wątki i opróżnia kolejkę callbacków Qt przed restartem lub usunięciem fixture. Wyniki zamkniętego
monitora są odrzucane, aby praca starego silnika nie przechodziła do następnego testu.

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

## Pętla przebiegów modułu pod obciążeniem

Niestabilny test (K-100) odtwarzamy i dowodzimy poprawki jednym poleceniem, `tools/module_loop.py`.
Powtarza badany moduł N razy (domyślnie 20); każdy przebieg to osobne wywołanie
`tools/test_runner.py --modules … --output <katalog przebiegu>` (pula miejsc maszyny obowiązuje, narzędzie
nie wywołuje unittest samo). Obciążenie robią palniki CPU w czystym Pythonie, zatrzymywane zawsze
(`finally`, SIGINT, SIGTERM; grupy procesów).

```sh
/usr/bin/python3 tools/module_loop.py daemon/tests/test_cardflow_waves.py                      # tryb palników
/usr/bin/python3 tools/module_loop.py daemon/tests/test_cardflow_waves.py --pool               # tryb puli
/usr/bin/python3 tools/module_loop.py <moduł> --pool --with <moduł…> --runs 2 --output <katalog>
```

Parametry: `--runs`, `--burners` (domyślnie ⌈1,5 × rdzenie⌉), `--threshold` (próg loadavg1, domyślnie liczba
rdzeni), `--wait` (najdłuższy czas dojścia do progu, s), `--output` (domyślnie `build/module-loop/…`).

- **Tryb palników:** w przebiegu biegnie tylko badany moduł, a maszynę obciążają palniki. Loadavg1 to średnia
  wykładnicza dochodząca do liczby procesów gotowych od dołu, dlatego palników jest 1,5× więcej niż próg.
- **Tryb puli (`--pool`):** badany moduł biegnie w jednym wywołaniu uruchamiacza razem z towarzyszami i z jego
  domyślnym `--jobs`, więc uruchamiacz dzieli moduły na równoległe i `[OSOBNO]` jak w pełnym `make test`
  (14 procesów, prywatne magistrale, porty, procesy potomne). Towarzysze: `--with <moduły…>` albo stała
  `DEFAULT_COMPANIONS` w narzędziu — 18 modułów `daemon/tests/` z prywatną magistralą D-Bus, procesami potomnymi
  i fiksturą `FlowInProcess` (w tym pozostałe moduły K-100: `test_cardflow_youtrack`, `test_cardflow_waves`,
  `test_daemon_lifecycle`, `test_cardflow_review`, `test_cardflow_dbus`), co z badanym modułem daje co najmniej
  `JOBS` modułów równoległych (narzędzie odmawia, gdy zestaw domyślny jest za mały na tej maszynie). Badany
  moduł nie jest duplikowany na liście. Pula pozostaje ciężka — nie uruchamiaj kilku naraz.
- **Kiedy tryb puli:** moduł jest `[OSOBNO]` w `SERIAL_MODULES` (raport podaje to wprost), albo porażka z pełnego
  `make test` nie odtwarza się palnikami.

Ważność przebiegu: przed pierwszym przebiegiem **i przed każdym kolejnym** narzędzie czeka, aż loadavg1 ≥ próg.
Przekroczenie czasu przed pierwszym przebiegiem to błąd bez przebiegów (kod 2); przed kolejnym przebieg rusza,
ale jest „nieważny”. Przebieg jest ważny, gdy loadavg1 na starcie ≥ próg i wszystkie palniki żyły do końca;
śmierć palnika unieważnia go. Spadek loadavg1 poniżej progu w trakcie lub na końcu (krótki moduł, przejścia
uruchamiacza) daje tylko ostrzeżenie „spadek obciążenia” z minimum z próbek.

Raport (`summary.json`, `report.md`): SHA, polecenie, tryb, badany moduł i towarzysze, `[OSOBNO]`, rdzenie,
palniki, `JOBS`, a dla przebiegu loadavg start/koniec/minimum, ważność, ostrzeżenia, kod uruchamiacza, czas
i nazwy nieudanych testów osobno dla badanego modułu i towarzyszy. Kod wyjścia 0 tylko dla N/N ważnych
przebiegów z zielonym badanym modułem; porażki towarzyszy nie zmieniają kodu, ale raport je liczy —
wykonawca rozlicza je w notce. Test narzędzia: `daemon/tests/test_module_loop.py`.

## Ponowienie padniętych modułów

Etap A oszczędzania czasu testów (06.11.2026: 31% czasu testów ticketów to były powtórki całego zestawu na
niezmienionym kodzie). Gdy przebieg ticketu padł, a autor nie zmienia żadnego pliku (podejrzenie niestabilności
albo obciążenia), nie powtarza całego `make test-changed`/`make test`. Uruchamia tylko moduły z listy
„Wszystkie nieudane testy” (albo z `summary.json`, `returncode` różny od 0), każdy osobno:

```sh
/usr/bin/python3 tools/test_runner.py --modules <ścieżki modułów>
```

`--modules` uruchamia dokładnie te moduły z tą samą pulą maszyny, osobnym katalogiem stanu każdego modułu
i raportem `summary.json` co pełny przebieg. Gałąź karty sprzed tej opcji ponawia moduły pojedynczo:
`d="$(mktemp -d)" && XDG_STATE_HOME="$d" /usr/bin/python3 -m unittest discover -t . -s <katalog> -p <plik>`.

W notce zapisuje pełny przebieg (polecenie, `git rev-parse HEAD`, wynik, raport) i ponowienie (ten sam HEAD,
czysty `git status`, lista modułów, wynik). Recenzent uznaje ponowienie tylko przy tym samym HEAD co pełny przebieg.
Każda zmiana pliku przywraca pełne polecenie zasady testów. Pełny `make test` przy domknięciu karty zostaje;
jeśli padnie, agent domknięcia także ponawia tylko padnięte moduły na tym samym HEAD.

Ponowienie ogranicza to, co się uruchamia, a nie zmienia oceny: porażka pierwszego przebiegu pozostaje wynikiem,
a udane ponowienie służy jako dowód do rozliczenia według [wyjątków od testów](#wyjątki-od-testów).
Projekty w innych technologiach stosują tę samą zasadę narzędziem testów projektu z filtrem na padnięte testy.
Tekst zasady w kartkach kroków: `rerun_failed` w `daemon/tide_daemon/cardflow/handoff.py`.

Tide wymusza tę zasadę sam (`daemon/tide_daemon/cardflow/testrerun.py`): gdy polecenie poza piaskownicą to
pełny przebieg (`make test`, `make test-changed …`), a ostatni taki przebieg w tym samym worktree padł na tym samym
HEAD przy czystym drzewie i takim samym poleceniu (cel, BASE, JOBS), Tide uruchamia zamiast niego
`tools/test_runner.py --modules` z modułami, które padły według `summary.json` tamtego przebiegu, i zapisuje
w dzienniku `rerunOf`. Brak raportu, brak listy padniętych modułów, zmiana kodu albo `TIDE_FULL_RUN=1` w poleceniu
oznaczają zwykły pełny przebieg. Dotyczy gałęzi, których uruchamiacz ma opcję `--modules`.

`TEST_OUTPUT=<katalog>` wskazuje tylko miejsce raportu: polecenie z nim nadal jest pełnym przebiegiem, przy porównaniu
przebiegów katalog się pomija, a ponowienie zapisuje raport w katalogu z bieżącego polecenia (08.11: przez
`TEST_OUTPUT` katalogu kroku ponowienie nie włączyło się w żadnym z 8 uciętych przebiegów PT-264).

Przebieg ucięty przez Tide (limit czasu polecenia poza piaskownicą albo zatrzymanie jednostki) nie ma `summary.json`.
Uruchamiacz zapisuje na starcie `modules.json` z listą modułów przebiegu, a każdy moduł zostawia raport z kodem
wyjścia procesu, więc ponowienie obejmuje moduły padnięte i te bez wyniku (w toku albo niezaczęte), a pomija tylko
zakończone kodem 0 bez porażek. Starszy uruchamiacz (bez `modules.json`): pełny przebieg, bo niezaczętych modułów
nie da się ustalić. Zwykła porażka z kodem wyjścia nadal wymaga kompletnego `summary.json`.

## Starsze gałęzie bez celu test-changed

Wyjątek dotyczy wyłącznie gałęzi, której Makefile (wraz z `mk/*.mk`) nie udostępnia jeszcze
`test-changed`: autor i recenzent uruchamiają tam pełny `make test` i zapisują polecenie oraz wynik
w notce. Tide sprawdza dostępność celu w worktree ticketu podczas tworzenia kartki przekazania
(`cardflow/handoff.py`). Nie wymaga przenoszenia infrastruktury testów z głównej gałęzi do starej
karty. Gdy cel jest dostępny, obowiązuje przypięty `BASE` i zwykłe `make test-changed`.
Ten wyjątek nie znosi pełnego testu gałęzi karty przy domknięciu.
