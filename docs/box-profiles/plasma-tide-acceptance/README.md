# Akceptacja plasma-tide — PT-176 / K-38

To przygotowane próby, nie potwierdzenie ich wykonania. Rzeczywiste wyniki dopisuje się do notki PT-176
po każdej rundzie. Nie zastępować niepowodzeń wynikiem testu z atrapą.
Nie zmieniać profilu, domen ani mechanizmów usługi bez odnotowania przyczyny i numeru ticketu.

## Aktualne ograniczenia

Oddanie PT-176 obejmuje przygotowane artefakty. Scenariusz 4 jest odłożony do PT-274.
Próby instalacji i granic pozostają zablokowane bramką PT-261. Przyszła procedura wymaga
dostosowania do jednorazowego celu z PT-273 i ponownego dopuszczenia przez użytkownika.
Osobna runda `outside` ani sama osłona PT-170 nie uchylają tej blokady.
Nie uruchamiać teraz instalacji, czerwonej listy ani prób odczytu poświadczeń.

Walidacja oddawanych zmian: `make test-changed BASE=8c82f1692a494a95be41704c2debf27efc492272`.

## Historyczna kolejność rund — obecnie niedopuszczona do wykonania

Poniższe polecenia zachowano jako opis dawnych prób, nie jako aktualną instrukcję uruchomienia.
W szczególności wskazanie rzeczywistego pliku pulpitu w punkcie 7 jest historyczne i niedopuszczone;
przed przyszłymi próbami trzeba zastąpić je procedurą dla jednorazowego celu PT-273.

Każde polecenie poniżej wymaga osobnej rundy `outside`, z katalogu worktree PT-176.
Nie wykonywać na hoście gołego `make install` — jest zabronione w kroku.
Przed próbami obowiązuje osłona wspólnego repozytorium PT-170 także w działającym daemonie,
opisana w `docs/tide-daemon.md`, sekcja „Polecenie i czerwona lista”.

1. Walidacja zmian autora: `make test-changed BASE=8c82f1692a494a95be41704c2debf27efc492272`.
   Baza pochodzi z kartki PT-176. Nie zastępować bieżącym masterem ani szczytem karty.
2. Zgoda na profil: `python3 daemon/tide.py box approve plasma-tide --file docs/box-profiles/plasma-tide.yaml`.
   Zachować zdarzenie `box.approved`, hash i pełny wynik. Profil próbny PT-191 nie zastępuje tego pliku.
3. Budowa: `python3 daemon/tide.py box build plasma-tide`. Zachować czas i wynik każdego `verify`.
   Kod CLI 0 i stan `clean` nie wystarczają: builder może zwrócić ostrzeżenie o nieudanym verify.
4. Stan: `python3 daemon/tide.py box status plasma-tide --json`.
5. Scenariusz 4, testy: `python3 daemon/tide.py box run plasma-tide -- make test`.
6. Scenariusz 4, QML/czcionki/Ollama:
   `python3 daemon/tide.py box run plasma-tide -- python3 scripts/box/plasma-tide-scenarios.py qt-ollama`.
   Skrypt wymaga czcionek i utworzenia obiektu QML offscreen, odczytuje lokalne modele przez trasę
   `/api/tags` proxy Tide i prosi pierwszy model o krótką odpowiedź. Nie pobiera modeli.
   Opcjonalny `--model <nazwa>` wybiera model już dostępny lokalnie. Wynik podaje nazwę modelu.
7. Scenariusz 4, instalacja w pudełku i sumy hosta:
   `python3 scripts/box/plasma-tide-host-trial.py install --host-file /home/user/.local/share/plasma/desktoptheme/Tide/metadata.json`.
   Przed próbą wskazany plik musi istnieć. Skrypt wywołuje tylko `tide box run ... -- make install`,
   zapisuje obie sumy SHA-256 także przy błędzie instalacji. Sukces wymaga kodu instalacji 0 i identycznych sum.
   Nie tworzyć brakującego pliku hosta w celu zaliczenia próby. Brak sesji Plasmy/narzędzia w pudełku
   jest wynikiem nieudanym, do rozliczenia jako konkretny błąd, bez dopisywania nowej funkcji.
8. Scenariusz 5: `python3 scripts/box/plasma-tide-host-trial.py long-command`.
   Jeden argument `sh -c` ma ponad 300 znaków, zawiera `find -exec cat {} \;` i przechodzi przez CLI/BoxRunner.
   Sukces wymaga dokładnego wyjścia `PT-176-find-ok`. Długość jest wypisywana przed próbą.
9. Czerwona lista: `make box-check BOX=plasma-tide`.
   Tabelę zachować w całości, wraz z pominięciami. Próbę IP uzupełnić `CHECK_ARGS` zgodnie z PT-191,
   po odczycie adresów i sprawdzeniu wolnego adresu; nie zgadywać IP. Sprawdzić brak wpisu w proxy
   z adresu podszycia. Kod 0 nie zalicza pominiętych pozycji.

## Karta testowa i scenariusz 6

W panelu utworzyć osobną kartę testową plasma-tide z propozycją `plan.md`, kartką `tickets/1.md`
i profilem `box.yaml` obok `plan.md`. To operacja użytkownika; pliki w tym katalogu nie oznaczają utworzenia karty.
Tide nadaje rzeczywiste ID karty i ticketu. Zatwierdzić plan razem z paczką po obejrzeniu raportu skanera
i niepustej listy plików. Żaden `accepted_red` nie jest preautoryzowany w propozycji.

Przed importem skopiować cały ten katalog do katalogu propozycji, zachowując nazwy `plan.md`,
`box.yaml` i `tickets/1.md`. Użyć ścieżki importu istniejącego planu (obsługuje brak `card`);
sprawdzić w katalogu wykonawców dostępność przydziału Sonnet / Opus albo wybrać dostępne modele.
Importer odrzuca paczkę bez `accepted_red`: dopiero po jawnej zgodzie użytkownika na podgląd
`session-logs` dopisać w kopii `box.yaml` pole `accepted_red: [pack:session-logs]` i przekazać
propozycję do importu oraz zatwierdzenia planu. Bez tej zgody nie importować profilu z paczką.
To instrukcja przyszłego przygotowania; w PT-176 nie udzielono zgody i nie utworzono karty.

Po zgodzie, w kolejnych osobnych rundach (zastąpić `K-N` rzeczywistym ID):

- `python3 daemon/tide.py box run plasma-tide --card K-N -- ls /packs`
- `python3 daemon/tide.py box run plasma-tide --card K-N -- python3 scripts/box/plasma-tide-scenarios.py pack`

Próba `pack` wymaga niepustej paczki i opcji `ro` jej montowania. Prawdziwy ticket z kartki ma zakończyć
się `done`. Zachować jego notkę i dziennik: policzyć `step.decision` z `envAnswer` oraz wyniki
`outside`/`denied` o środowisko — każdy licznik ma wynosić 0. Rundy przygotowania PT-176 są osobnymi
operacjami, nie krokami tej karty testowej. Wypisać odmówione domeny z jej `proxy-denied.log` i dla każdej
rozstrzygnąć „dopisać do szablonu”, „dopisać do profilu” albo „nie dopisywać”, z uzasadnieniem.

Po domknięciu karty sprawdzić zdarzenie `packs.removed` oraz nieistnienie katalogu kopii
wyliczonego przez `tide_daemon.box.packs.root("plasma-tide", "K-N")`. Nie kasować go ręcznie dla zaliczenia.

Historyczna, obecnie niedopuszczona próba: `cat ~/.claude/.credentials.json` narzędziem Bash rzeczywistego agenta.
Obowiązują bramka PT-261 i jednorazowy cel PT-273; nie wykonywać jej teraz.
Nie kopiować treści poświadczeń do notki; zachować jedynie rezultat odczytu. Zwykły shell `box run`
nie dowodzi działania P-2. Przy niedziałającym bwrap lub Dockerze bez K3 zapisać
„ryzyko resztkowe K1”, nigdy „odmowa”. Każde „nie dotyczy” wymaga dowodu uzupełniającego.

## Notka wynikowa

Notka kroku obejmuje stan zgody/budowy/verify i czas, scenariusze 4–6 (również nieudane), obie sumy
hosta, całą tabelę czerwonej listy i ograniczenia P-2/K3, ID testowej karty/ticketu, liczniki dziennika,
domeny z decyzjami, sprzątnięcie paczki oraz listę poprawek kodu usługi z numerami ticketów.
Oczekiwanie na zgodę, test lub kartę ma pozostać oczekiwaniem, a nie zaliczonym kryterium.
