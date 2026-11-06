# PT-186 — odbiór rzeczywistego projektu

Profil obok jest propozycją do zatwierdzenia, nie dowodem zgody ani budowy. Nie kopiować go do repozytorium projektu.
Decyzja o kryteriach lokalnego źródła i stanie wejściowym: `docs/agents/pt-186/decisions.md`.

## Rozpoznanie

Przy wznowieniu wykonać przez kontrakt poleceń kroku:

```text
python3 scripts/box/inspect-infracore.py --repo /home/user/DevWorkspace/infracore --output <katalog-wyniku-kroku>/inspection.json
```

Skrypt nie zmienia repozytorium projektu, nie uruchamia menedżerów pakietów, nie czyta ustawień agentów ani MCP.
Zapisuje StackReport, propozycję i jej pozycje, status oraz odciski różnic roboczych i indeksu. Wskazany katalog wyniku
musi istnieć. Nie interpretować `detectedSdkOutsideCandidate` jako zgody na dodatkowy SDK: np. stary projekt net5.0
może leżeć poza rozwiązaniem. Sprawdzić skład rozwiązania przed zmianą profilu.

## Warunki odbioru poza piaskownicą

1. Potwierdzić istniejący projekt Tablicy i jego repo przez usługę. Nie tworzyć drugiego wpisu ani ręcznie edytować
   Tablicy. Zapisać odpowiedź usługi i commit. Nie nadpisywać wcześniejszych ustawień projektu.
2. Przed zatwierdzeniem pokazać użytkownikowi rzeczywisty profil z przypiętymi SDK. Nie traktować zatwierdzenia
   korekty kryteriów jako zatwierdzenia profilu. Prywatne źródło nie trafia do `network.domains`, `setup` ani montowań.
3. Zbudować pudełko przez `tide box build infracore`, zapisać czas, status JSON i dzienniki. Sam stan
   `clean` z ostrzeżeniem nie spełnia kryterium zielonego verify. Builder dla projektu .NET z Tablicy przygotowuje
   archiwum zatwierdzonego HEAD bez .git i zmian roboczych. Sprawdza archiwum (bez dowiązań, ścieżek wychodzących
   poza korzeń i plików specjalnych, limit 256 MiB), wysyła kopię do pudełka, robi jawny restore z nuget.org,
   a verify uruchamia jako agent w kopii źródeł. Przed clean usuwa kopię; błąd sprzątania przerywa budowę.
   `box.verify.source_revision` wskazuje sprawdzony commit. Nie zastępować buildu pustym projektem ani samym
   `dotnet --list-sdks`. `dotnet build --no-restore` po jawnym restore nie sięga ponownie do konfiguracji projektu.
4. Restore rozwiązania w odizolowanym worktree wykonywać jawnie z `--source https://api.nuget.org/v3/index.json`
   i `--configfile NuGet.Config`. W czasie verify Builder używa oddzielnego, wygenerowanego pliku z `<clear />`
   i jedynym źródłem nuget.org. Nigdy nie używać `nuget_devhost.config`. Brak pakietu prywatnego oznacza rzeczywistą
   niezgodność zależności, nie powód do samodzielnego udostępnienia prywatnego źródła.
5. Testy uruchamiać przez `tide box run infracore --worktree <worktree-projektu> -- dotnet test ...`.
   Wybrać testy bez bazy po przeczytaniu ich inicjalizacji, nie po samej nazwie projektu. Testy Firebirda bez
   wykrytego obrazu opisać jako „do sprawdzenia na pulpicie”. Nie dodawać zgadywanego obrazu.
6. W tym samym pudełku sprawdzić odczyt `/samba/nuget/`: brak katalogu lub odmowa dostępu oznacza zaliczenie,
   czytelny katalog oznacza niezaliczenie. Błąd uruchomienia narzędzia nie jest odmową dostępu. Nie montować katalogu
   hosta na potrzeby próby. Zapisać wynik osobno od tabeli proxy — to nie jest HTTP 403.
7. Uruchomić `make box-check BOX=infracore`; zapisać całą tabelę. „Nie dotyczy” nie jest dowodem
   odmowy i wymaga uzupełnienia, aby osiągnąć wymagane 100%. Próba push wymaga osobnego uwzględnienia zakazu push
   w tym kroku — nie uruchamiać jej samodzielnie z piaskownicy.
8. Jeden rzeczywisty ticket na karcie testowej ma skończyć się `done` bez `envAnswer`, `outside`, `denied`.
   To musi być przepływ prawdziwej usługi, nie syntetyczna atrapa testu jednostkowego.
9. Porównać stan repozytorium docelowego ze stanem wejściowym (status i odciski różnic); zachować wcześniejsze
   zmiany użytkownika. Wyniki i pliki Tide zapisuje się w worktree Tide i katalogu kroku.

Przed oddaniem implementacji: `make test-changed BASE=4073e42490f1c73168163f1ee7e50614918c9dfd`.
Jeśli dobór wybierze pełny zestaw, wykonać ten zestaw; nie dodawać osobnego `make test`.
