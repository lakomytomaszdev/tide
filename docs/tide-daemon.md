# tide-daemon

Usługa użytkownika systemd (Python 3, PyQt6), która czyta dzienniki sesji Claude Code i Codexa, wyprowadza stan każdej sesji i wystawia go przez D-Bus. Nie zmienia konfiguracji Claude Code ani Codexa — tylko czyta ich pliki. Drugi obszar to worktrees repozytoriów z katalogu projektów: ich stan i bezpieczne sprzątanie scalonych (opis niżej, [Worktrees](#worktrees)). Trzeci to wtyczka KRunnera z sesjami z 30 dni, projektami i worktrees ([KRunner](#krunner)).

## Kontrakt D-Bus

| Element | Wartość |
| --- | --- |
| Magistrala | sesji użytkownika |
| Nazwa usługi | `org.tide.Daemon` |
| Obiekt | `/Agents` |
| Interfejs | `org.tide.Daemon.Agents` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Sessions()` | `→ s` | Lista sesji jako JSON (tablica obiektów, opis niżej) |
| `ActivateSession(s id)` | `→ b` | Przechodzi do okna sesji. `false`, gdy okna nie da się ustalić |
| `ActivateLongestWaiting()` | `→ b` | Przechodzi do okna sesji, która czeka najdłużej (skrót Meta+A). `false` i nic się nie dzieje, gdy nic nie czeka |
| `Changed` | sygnał bez argumentów | Lista sesji się zmieniła — pobierz `Sessions()` ponownie |

Kolejność listy: najpierw czekające (najdłużej czekająca na górze), potem pracujące, bezczynne i zakończone (od najświeższej aktywności).

Pola sesji:

| Pole | Typ | Opis |
| --- | --- | --- |
| `id` | string | `claude:<id-sesji>` albo `codex:<id-sesji>` — argument `ActivateSession` |
| `tool` | string | `claude` albo `codex` |
| `sessionId` | string | identyfikator sesji w narzędziu |
| `project` | string | nazwa repozytorium (dla worktree — repozytorium głównego) albo katalogu |
| `branch` | string | bieżąca gałąź z `.git/HEAD`, zapasowo z dziennika |
| `worktree` | string | nazwa katalogu worktree albo pusty napis |
| `cwd` | string | katalog roboczy sesji |
| `title` | string | krótki tytuł rozmowy (tylko Claude Code: `custom-title` nadany przez użytkownika, inaczej `ai-title`) |
| `state` | string | `working`, `waiting`, `idle`, `ended` |
| `reason` | string | dla `waiting`: `reply` (koniec tury), `approval` (zgoda na narzędzie), `question` (pytanie do użytkownika) albo wartość `waitingFor` zgłoszona przez Claude Code; inaczej pusty |
| `since`, `sinceTs` | ISO 8601 / sekundy epoki | od kiedy trwa bieżący stan (dla `waiting` — od kiedy czeka) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / sekundy epoki | ostatni wpis agenta w dzienniku |
| `lastAction` | string | ostatnia czynność, np. `Edit · main.py`, `shell · make test` |
| `pid` | int | proces agenta albo 0 |
| `canActivate` | bool | czy `ActivateSession` ma dokąd przejść |
| `terminal` | string | nazwa okna docelowego, np. `Konsole`, `Codex` |

Z QML (moduł `org.kde.plasma.workspace.dbus`) korzysta z tego `widgets/lib/DaemonSource.qml`.

## Stany i progi

Pomijamy sesje, w których dziennik nie ma wpisu od ponad **24 h**.

| Stan | Kiedy |
| --- | --- |
| `working` | tura trwa: model myśli, pisze albo działa narzędzie |
| `waiting` | tura skończona (czeka na odpowiedź), narzędzie czeka na zgodę albo agent zadał pytanie |
| `idle` | czeka dłużej niż **30 min**; także tura bez żadnego wpisu przez 30 min i bez śladu działającego procesu |
| `ended` | proces sesji już nie działa |

Skąd to wiemy:

- **Claude Code** sam zapisuje stan działającego procesu w `~/.claude/sessions/<pid>.json` (`status`: `busy`, `waiting`, `idle`, plus `waitingFor`). To główne źródło — obejmuje także czekanie na zgodę. Gdy pliku nie ma (starsze wersje), stan wynika z dziennika `~/.claude/projects/<katalog>/<sesja>.jsonl`: `stop_reason: end_turn` albo `system/turn_duration` kończy turę, `tool_use` bez `tool_result` to wywołanie narzędzia w toku.
- **Codex** — `~/.codex/sessions/RRRR/MM/DD/rollout-*.jsonl`: `task_started` zaczyna turę, `task_complete` i `turn_aborted` ją kończą, `function_call`/`custom_tool_call` bez `*_output` to narzędzie w toku. Wątki podagentów i recenzenta zgód (`source.subagent`) nie są osobnymi sesjami.
- **Narzędzie w toku.** Przez **10 s** od wywołania sesja „pracuje”. Potem pracuje dalej tylko wtedy, gdy proces agenta uruchomił coś po wywołaniu (np. polecenie powłoki), narzędzie jest z natury długie (podagent, czekanie na podagenta) albo Codex ma `approval_policy: never`. W pozostałych przypadkach uznajemy, że czeka na zgodę. Długie wywołanie MCP bez własnego procesu może więc chwilowo wyglądać jak czekanie na zgodę.
- **Proces sesji.** Claude Code: `pid` z pliku `sessions/<pid>.json`. Codex: proces `codex`, który trzyma otwarty plik dziennika (w aplikacji Codex Desktop to `codex app-server`; wątek, którego aplikacja nie trzyma otwartego, jest pokazywany jako zakończony). Zapasowo: proces narzędzia z tym samym katalogiem roboczym.

## Odświeżanie

- `QFileSystemWatcher` na plikach sesji z ostatniej doby i na katalogach, w których pojawiają się nowe sesje; zmiana pliku → doczytanie tylko dopisanych wpisów po 250 ms,
- co 5 s przeliczenie stanów bez czytania dzienników (progi czasowe, procesy z `/proc`, ok. 15 ms),
- co 30 s przegląd katalogów (np. wznowiona stara sesja Codexa w katalogu sprzed tygodnia).

Przy pierwszym odczycie dużego pliku czytamy tylko pierwszą linię (metadane) i ostatni 1 MB.

## Przejście do okna

Best-effort. Z procesu agenta idziemy w górę drzewa procesów do znanego terminala (Konsole, kitty, Alacritty, foot, WezTerm, Ghostty…) albo aplikacji (Codex Desktop, VS Code). Dla Konsole najpierw przełączamy kartę (`org.kde.konsole.Window.setCurrentSession` z numerem z `KONSOLE_DBUS_SESSION`), potem skrypt KWin ładowany przez `org.kde.KWin /Scripting` (`loadScript`, `start`) aktywuje okno należące do tego procesu. Gdy terminala nie da się ustalić (np. tmux, ssh), sesja nie jest klikalna.

## Skok do czekającego agenta

**Skrót Meta+A.** Skrypt KWin `tide-agent-jump` (`kwin/agent-jump`, instalacja `make install-agent-jump`) rejestruje skrót przez `registerShortcut` i woła `ActivateLongestWaiting` na `/Agents`. Usługa odświeża stan i procesy, wybiera czekającą sesję z najwcześniejszym `sinceTs` spośród tych, do których okna da się przejść (czekająca w tmux czy przez ssh jest pomijana), i aktywuje okno tak jak `ActivateSession` (Konsole: najpierw karta). Gdy nic nie czeka — zwraca `false` bez żadnego komunikatu. Jeśli usługa nie działa, D-Bus uruchomi ją przy pierwszym naciśnięciu.

Dlaczego skrypt KWin, a nie rejestracja w KGlobalAccel prosto z usługi: na Waylandzie w Plasmie 6 globalne skróty obsługuje KWin, a `registerShortcut` to udokumentowane, stabilne API skryptów. Protokół D-Bus `org.kde.kglobalaccel` jest wewnętrzny (`doRegister`, `setShortcutKeys` ze strukturami klawiszy `a(ai)`, które zmieniły się między KF5 i KF6, `setInactive`, osobny obiekt `/component/<nazwa>` z sygnałem) — łatwo go zepsuć aktualizacją Frameworks i trudno poprawnie zbudować z PyQt. Skrót w skrypcie KWin nie znika przy restarcie usługi, widać go w Ustawieniach systemowych → Skróty → KWin („Tide: przejdź do agenta, który czeka najdłużej”) i można go tam zmienić. Przełączanie okien i tak idzie przez skrypty KWin.

**Kolizja Meta+A.** KDE nie nada domyślnego klawisza, który ma już inna akcja. Instalator sprawdza `kglobalshortcutsrc` i wypisuje kolizje (np. „Przełączaj między aktywnościami” w plasmashell), niczego w nich nie zmieniając — trzeba wtedy zwolnić Meta+A w Ustawieniach systemowych i przypisać go akcji Tide.

**Powiadomienia.** Gdy sesja wchodzi w stan `waiting`, usługa wysyła przez `org.freedesktop.Notifications` powiadomienie „Agent czeka: <projekt>” z powodem (np. „Czeka na zgodę: shell · make test”), narzędziem i gałęzią oraz przyciskiem „Przejdź” (także kliknięcie w powiadomienie), który aktywuje okno tej sesji (`ActionInvoked` → `ActivateSession`). Sesja bez ustalonego okna dostaje powiadomienie bez przycisku.

- Ta sama sesja nie dostaje drugiego powiadomienia, dopóki nie przestanie czekać (pracuje, bezczynna, zakończona albo zniknęła z listy) i nie zacznie znowu.
- Gdy sesja przestaje czekać, jej powiadomienie jest zamykane (`CloseNotification`), żeby nie wisiało nieaktualne.
- Sesje czekające już w chwili startu usługi nie dostają powiadomień — restart usługi nie zasypuje ekranu.
- `--no-notifications` wyłącza powiadomienia.

Logika (wybór sesji, kiedy powiadomić, tekst) jest w `tide_daemon/attention.py` bez Qt, wiązanie z magistralą w `notifications.py`.

## Stan agenta na karcie Konsole

Karta Konsole, w której działa sesja, pokazuje jej stan (decyzja 29 — koloru paska tytułu dekoracja Tide nie umie przełączać per okno):

| Stan sesji | Tytuł karty | Kolor karty |
| --- | --- | --- |
| `working` | `◐ ` przed tytułem | `pastel[semantic.working]` (teal) |
| `waiting` | `◆ ` przed tytułem | `pastel[semantic.waiting]` (brzoskwinia) |
| `idle`, `ended`, brak sesji | jak przed oznaczeniem | jak przed oznaczeniem |

- Kartę wskazują `KONSOLE_DBUS_SERVICE` i `KONSOLE_DBUS_SESSION` ze środowiska procesu agenta (jak przy przejściu do okna). Gdy w jednej karcie są dwie sesje, „czeka” wygrywa. Inne terminale i aplikacje nie mają API kart — zostają bez znacznika; okna bez agenta nie są ruszane.
- Zmiana idzie przez `org.kde.konsole.Session`: prefiks trafia do obu formatów tytułu (`setTabTitleFormat` 0 i 1), więc przetrwa odświeżenia Konsoli, a wyświetlany tytuł jest ustawiany od razu (`setTitle(1, …)`, potem formaty), bo Konsole przelicza go dopiero po aktywności w terminalu. Kolor: `setTabColor`. Bieżąca karta przekazuje tytuł oknu, więc znacznik widać też na pasku tytułu, w pasku zadań i przełączniku okien.
- Oryginalne formaty i kolor są w `$XDG_STATE_HOME/tide/konsole-tabs.json` (plik znika, gdy nic nie jest oznaczone). Karta wraca do nich, gdy stan mija, przy zamknięciu usługi i po jej restarcie. Jeśli użytkownik w międzyczasie zmienił tytuł albo kolor karty, jego zmiana zostaje.
- Konsolę pytamy tylko przy zmianie stanu karty, wywołaniami z limitem 1 s. `--no-tab-marks` (albo `--no-processes`) wyłącza znacznik.

Logika jest w `tide_daemon/tabmarks.py` bez Qt, zaplecze D-Bus w `konsole.py`. Kolory pochodzą z palety: `palette.json` obok zainstalowanej usługi, w repozytorium `theme/palette/mist.json`, albo `$TIDE_PALETTE`.

## Worktrees

| Element | Wartość |
| --- | --- |
| Obiekt | `/Worktrees` |
| Interfejs | `org.tide.Daemon.Worktrees` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `List()` | `→ s` | Lista worktrees jako JSON (pola niżej) |
| `Remove(as paths)` | `→ s` | Sprzątanie wskazanych worktrees; JSON z wynikiem dla każdej ścieżki, w kolejności wejścia. Odpowiedź przychodzi po zakończeniu (praca w tle) |
| `Refresh()` | `→` | Odświeżenie na żądanie; wynik przychodzi sygnałem `Changed` |
| `Open(s path, s app)` | `→ b` | Otwiera worktree: `app` = `terminal` (`konsole --workdir`) albo `rider`. Tylko ścieżki z listy |
| `Launchers()` | `→ s` | JSON `{"terminal": bool, "rider": bool, "window": bool}` — które przyciski pokazać; `window`: czy okno Worktrees jest zainstalowane |
| `ShowWindow()` | `→ b` | Otwiera okno „Tide Worktrees” (drugie uruchomienie tylko je pokazuje). `false`, gdy okno nie jest zainstalowane |
| `Prune(s repoPath)` | `→ s` | `git worktree prune` w znanym repozytorium, które ma worktrees bez katalogu. JSON: `repoPath`, `ok`, `pruned` (ścieżki), `message`. Odpowiedź po zakończeniu |
| `Changed` | sygnał | Lista się zmieniła — pobierz `List()` ponownie |

Kolejność listy: według nazwy repozytorium, w repozytorium najpierw główny katalog, potem od ostatnio zmienianych.

Pola worktree:

| Pole | Typ | Opis |
| --- | --- | --- |
| `repo` | string | nazwa repozytorium (katalog główny, dla bare bez `.git`) |
| `repoPath` | string | katalog repozytorium, w którym działa git |
| `path`, `name` | string | ścieżka worktree i nazwa jego katalogu |
| `branch` | string | gałąź albo pusty napis przy odłączonym HEAD |
| `detached`, `head` | bool, string | odłączony HEAD i skrót commita |
| `isMain` | bool | główny katalog repozytorium — nigdy nie jest usuwany |
| `hasAgent` | bool | pracuje w nim agent (katalog roboczy żywej sesji z obszaru Agents albo procesu `claude`/`codex`; przy zagnieżdżonych worktrees liczy się najgłębszy) |
| `dirty` | int | liczba pozycji w `git status --porcelain` (nieśledzony katalog to jedna pozycja); `-1`, gdy statusu nie dało się odczytać (traktowane jak zmiany) |
| `mainBranch` | string | lokalna gałąź główna: `master`, `main` albo `develop` (ta, na której stoi główny katalog, inaczej pierwsza istniejąca w tej kolejności); pusty, gdy żadnej nie ma |
| `merged` | bool | czubek worktree jest przodkiem lokalnej gałęzi głównej (`unmerged == 0`) |
| `unmerged` | int / null | `git rev-list --count <główna>..HEAD`; `null` bez gałęzi głównej |
| `lastChange`, `lastChangeTs` | ISO 8601 / sekundy epoki | najnowsze z: czas ostatniego commita HEAD, mtime plików wskazanych przez `git status` |
| `size` | int | bajty na dysku jak `du -s` (bez zagnieżdżonych worktrees); `-1`, póki nie policzone |
| `locked`, `missing` | bool | `git worktree lock` / katalogu już nie ma (`prunable`) |
| `removable`, `blocker` | bool, string | czy sprzątanie go usunie; jeśli nie — pierwszy powód (niżej) |
| `group` | string | grupa w oknie Worktrees: `active`, `clean`, `decide`, `main`, `missing` (reguły niżej) |
| `title`, `tag` | string | czytelny tytuł z nazwy gałęzi i wyciszony znacznik z prefiksu: `codex/summary-tabs-03-version-axis` → `summary-tabs 03 · version axis` i `codex`. Odłączony HEAD: `odłączony HEAD <skrót>` |
| `subject` | string | temat ostatniego commita HEAD (do 120 znaków) |
| `tool` | string | `claude`, `codex` albo pusty: z powiązanej sesji, inaczej z położenia (`~/.codex/worktrees`, `.claude/worktrees`) albo prefiksu gałęzi |
| `sessionId`, `sessionTitle`, `sessionLastActivityTs` | string, string, sekundy epoki | najświeższa sesja Claude Code albo Codexa z ostatnich 30 dni, której katalog roboczy leży w worktree (puste / 0, gdy brak) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / sekundy epoki | ostatnia aktywność: najnowsze z `lastChangeTs` i ostatniego wpisu powiązanej sesji |

Grupy (`group`, pierwsza pasująca reguła):

| Grupa | Kiedy | W oknie |
| --- | --- | --- |
| `missing` | katalogu już nie ma | linia z `git worktree prune` dla repozytorium |
| `main` | główny katalog repozytorium albo worktree na gałęzi głównej (`protected`) | zwinięta sekcja „Gałęzie główne” (same główne katalogi pomijane) |
| `active` | pracuje agent albo ostatnia aktywność krótsza niż 24 h | „W pracy”, tylko otwieranie |
| `clean` | `removable`: scalony z lokalną gałęzią główną, czysty, bez agenta, nie zablokowany | „Do sprzątnięcia”: pola wyboru, usuwanie zaznaczonych |
| `decide` | pozostałe: niescalone commity, zmiany, blokada, brak gałęzi głównej | „Do decyzji”, z powodem, tylko otwieranie |

Wynik `Remove` dla każdej ścieżki: `path`, `removed`, `reason` (pusty albo powód), `message` (po polsku), `branch`, `branchDeleted`, `branchMessage`, `freed` (bajty z ostatniego pomiaru).

Powody pominięcia (w tej kolejności): `main`, `missing`, `locked`, `agent`, `dirty`, `no-main` (brak lokalnej gałęzi głównej), `unmerged`, a przy samym usuwaniu także `unknown` (ścieżka spoza listy), `git` (git odmówił — komunikat w `message`) i `error` (wyjątek w usłudze).

Zasady sprzątania:

- **Bez sieci.** Żadnego `fetch`/`pull` — „scalony” znaczy scalony z *lokalną* gałęzią główną. Gałąź wypchnięta i scalona tylko na serwerze liczy się jako niescalona.
- **Bez wymuszania.** Tylko `git worktree remove <ścieżka>` bez `--force`; git sam odmówi usunięcia worktree ze zmianami albo plikami nieśledzonymi (pliki ignorowane, np. `node_modules`, znikają razem z katalogiem).
- **Świeży stan.** Przed usunięciem repozytorium jest czytane na nowo (status, scalenie, agenci z chwili wywołania), więc decyzja nie opiera się na liście sprzed minuty.
- **Gałąź tylko bezpiecznie.** Po usunięciu worktree gałąź znika wyłącznie przez `git branch -d` (nigdy `-D`). Git porównuje wtedy z HEAD głównego katalogu albo z gałęzią śledzoną; gdy odmówi, gałąź zostaje, a wynik mówi dlaczego.

Skąd lista:

- repozytoria: katalogi z `.git` (albo repozytoria bare) w katalogu projektów (`--workspace`, `$TIDE_WORKSPACE`, domyślnie `~/DevWorkspace`), do 3 poziomów w głąb (`--worktree-depth`), z pominięciem katalogów ukrytych i zależności (`node_modules`, `vendor`, `bin`, `obj`, `target`, `build`, `dist`…); wewnątrz repozytorium nie szukamy dalej,
- worktrees: `git worktree list --porcelain` każdego repozytorium — także te, które leżą poza katalogiem projektów (np. `~/.codex/worktrees`),
- git działa z `GIT_OPTIONAL_LOCKS=0` (status nie zakłada `index.lock`), `GIT_NO_LAZY_FETCH=1` i `GIT_TERMINAL_PROMPT=0`.

Powiązanie z sesjami (`worktrees/sessions.py`, best effort, tylko odczyt): pliki z ostatnich 30 dni — Claude Code `<claude>/projects/<katalog>/<sesja>.jsonl` (nazwa katalogu to ścieżka z każdym znakiem spoza `[A-Za-z0-9]` zamienionym na `-`, więc nieodwracalna — katalog bierzemy z pola `cwd` wpisów) i Codex `<codex>/sessions/**/rollout-*.jsonl` (`session_meta.payload.cwd`, `turn_context.payload.cwd`; wątki podagentów pomijane). Tytuł: Claude — `custom-title`, `ai-title`, `summary`, inaczej pierwsze polecenie użytkownika; Codex — `thread_name` z `<codex>/session_index.jsonl`, inaczej pierwsze polecenie; najwyżej 80 znaków. Z pliku czytamy tylko początek i koniec (po 192 KB), a wynik trzymamy do zmiany rozmiaru albo mtime (pierwszy przegląd ok. 0,2 s, kolejne kilka ms). W pamięci są tylko metadane (narzędzie, identyfikator, tytuł, katalogi, czas), nic z rozmów nie trafia do dziennika usługi. Sesja uruchomiona w głównym katalogu, która zakłada worktree poleceniem powłoki, nie zostanie powiązana — liczy się katalog roboczy.

Okno „Tide Worktrees” (`apps/worktrees`): `ShowWindow()` uruchamia `/usr/bin/python3 -s $XDG_DATA_HOME/tide/worktrees-window/tide-worktrees.py` (albo `$TIDE_WORKTREES_WINDOW`) przez `systemd-run --user --scope`. Okno rejestruje nazwę `org.tide.WorktreesWindow` (obiekt `/Window`, metoda `Raise()`), więc kolejne uruchomienie tylko pokazuje już otwarte.

Odświeżanie: co **60 s** w tle (jeden wątek roboczy: odczyt, potem rozmiary), na żądanie przez `Refresh()` (okno woła je przy starcie i przyciskiem „Odśwież”) i po sprzątaniu. Rozmiary są w pamięci podręcznej i przeliczane najwyżej co **30 min**. Zmiana sesji agentów przelicza tylko flagę `hasAgent`, bez gita. Wątek główny (D-Bus) nigdy nie czeka na gita ani na dysk.

Otwieranie: Konsole przez `konsole --workdir`, Rider przez skrypt z JetBrains Toolbox (`~/.local/share/JetBrains/Toolbox/scripts/rider`), `rider` z `PATH` albo `$TIDE_RIDER`; bez Ridera przycisk znika. Programy startują przez `systemd-run --user --scope`, żeby restart usługi nie zamykał otwartych okien.

## KRunner

Wtyczka KRunnera: po wpisaniu nazwy projektu, gałęzi albo fragmentu tytułu rozmowy wznawia sesję Claude Code albo Codexa w jej katalogu, a po nazwie gałęzi otwiera worktree.

| Element | Wartość |
| --- | --- |
| Nazwa usługi | `org.tide.Daemon.KRunner` (osobne połączenie tej samej usługi, niżej dlaczego) |
| Obiekt | `/KRunner` |
| Interfejs | `org.kde.krunner1` (API „DBus” KRunnera 6) |
| Plik wtyczki | `$XDG_DATA_HOME/krunner/dbusplugins/tide.desktop` (`make install-krunner`) |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Match(s query)` | `→ a(sssida{sv})` | Dopasowania: id, tekst, ikona, ważność kategorii (70/50/30 — całe pole, początek, dalej), trafność 0..1, właściwości `subtext`, `category`, `actions` |
| `Actions()` | `→ a(sss)` | Akcje dodatkowe: `terminal` („Otwórz katalog w terminalu”) i `rider` („Otwórz w Riderze”), tylko dostępne |
| `Run(s matchId, s actionId)` | `→` | Uruchomienie; pusty `actionId` to akcja domyślna |

Co pokazuje:

| Rodzaj | Tekst / podpis | Uruchom |
| --- | --- | --- |
| Sesja (`session:<narzędzie>:<id>`) | `Codex · LogViewer · <tytuł>` / `<gałąź> · 3 dni temu` | okno sesji, jeśli jeszcze działa (jak `ActivateSession`); inaczej nowe okno Konsoli w katalogu sesji z `claude --resume <id>` albo `codex resume <id>`, a po wyjściu z agenta zostaje powłoka |
| Projekt (`project:<ścieżka>`) | nazwa repozytorium / `Otwórz projekt · ~/DevWorkspace/…` | Konsole w katalogu; akcja `rider` |
| Worktree (`worktree:<ścieżka>`) | gałąź / `Worktree · <repozytorium> · <ścieżka>` | Konsole w katalogu; akcja `rider` |

- Zapytanie musi mieć co najmniej 3 znaki. Każde słowo musi trafić w któreś pole (sesja: projekt, gałąź, nazwa katalogu, tytuł, narzędzie; projekt: nazwa; worktree: gałąź, katalog, repozytorium), bez wielkości liter i polskich znaków. Sesje są dodatkowo ważone świeżością.
- `ai <słowa>` pokazuje tylko sesje (do 20); zwykłe zapytanie — do 8 sesji, 5 projektów i 6 worktrees.
- Sesje, których katalogu już nie ma (np. usunięty worktree), nie są pokazywane — nie da się ich wznowić tam, gdzie były. `Run` przyjmuje tylko id z bieżącego indeksu i ścieżki z bieżącej listy worktrees.
- Ikony z motywu: `utilities-terminal` (Claude Code), `openai` (Codex), `folder-git` (projekt), `vcs-branch` (worktree), `jetbrains-rider` (akcja).

**Indeks sesji** (`krunner/index.py`): sesje z ostatnich **30 dni** (czas modyfikacji pliku jako wstępny filtr, potem ostatni wpis w dzienniku). Dla każdej sesji tylko: id, narzędzie, katalog, projekt, gałąź (z dziennika, zapasowo bieżąca), tytuł skrócony do 100 znaków i czas ostatniej aktywności. Tytuł:

- Claude Code: `custom-title` (nadany przez użytkownika), inaczej ostatni `ai-title`, inaczej pierwsza wiadomość użytkownika (bez wstawek poleceń i wpisów `isMeta`),
- Codex: `thread_name` z `~/.codex/session_index.jsonl`, inaczej `threads.title` z bazy aplikacji `~/.codex/state_<n>.sqlite` (otwieranej tylko do odczytu, `mode=ro`; inny schemat albo błąd — pomijamy), inaczej pierwsza wiadomość użytkownika (`user_message`, `UserMessage` aplikacji albo wiadomość `user` bez kontekstu środowiska i `AGENTS.md`). Wątki podagentów są pomijane.

Indeks powstaje leniwie: pierwsze `Match` po starcie usługi czeka na zbudowanie (u nas ok. 2 s przy 0,6 GB dzienników Claude i 1,9 GB Codexa), w wątku roboczym, bez blokowania D-Bus. Później odświeża się w tle najwyżej co 10 s i tylko przy zapytaniach — czyta wyłącznie pliki ze zmienionym rozmiarem albo czasem modyfikacji, od miejsca, w którym skończył (te same czytniki co obszar Agents: `ClaudeLog`, `CodexLog`, `JsonlTail`). Przy pierwszym odczycie dużego pliku: początek (256 KB, pierwsza wiadomość) i ostatni 1 MB (najnowszy tytuł, gałąź). Wynik zapytania wraca w kilka milisekund.

**Prywatność.** Indeks żyje wyłącznie w pamięci usługi: nie ma pliku pamięci podręcznej, nic nie wychodzi poza komputer, a z rozmów zostaje tylko skrócony tytuł. Testy używają wyłącznie syntetycznych dzienników (`daemon/tests/fixtures/history`).

**Dlaczego osobna nazwa i dbus-python.** KRunner oczekuje odpowiedzi `a(sssida{sv})` — tablicy struktur. PyQt6 nie potrafi jej zbudować: `QDBusArgument.beginArray` wymaga typu zarejestrowanego w C++ (`qDBusRegisterMetaType`), a próba obejścia przerywa proces w libdbus. Obiekt `/KRunner` obsługuje więc dbus-python (`python3-dbus`, z pętlą GLib z `python3-gi`), który ma pełne typowanie. Qt na Linuksie działa na tej samej domyślnej pętli GLib (`QEventDispatcherGlib`), więc obie biblioteki obsługuje jeden wątek główny; przy `QT_NO_GLIB` pętlę GLib popycha timer co 50 ms. dbus-python ma własne połączenie z magistralą, a nazwa należy do jednego połączenia — stąd `org.tide.Daemon.KRunner` obok `org.tide.Daemon`. Plik aktywacji D-Bus tej nazwy uruchamia tę samą jednostkę systemd, więc KRunner zapytany przed startem usługi ją uruchomi. Bez `python3-dbus` albo `python3-gi` usługa działa dalej, tylko bez wtyczki (`--no-krunner` wyłącza ją jawnie).

**Instalacja.** `make install-daemon` (kod i plik aktywacji `org.tide.Daemon.KRunner.service`) oraz `make install-krunner` (plik `.desktop`; oba są też w `make install`). KRunner czyta wtyczki D-Bus przy starcie, więc instalator i dezinstalator proszą działający KRunner o zamknięcie (`qdbus6 org.kde.krunner /MainApplication quit`) — wraca sam przy następnym Alt+Spacja. Wyszukiwanie w menu Plasmy (Kickoff) ma własną instancję i zobaczy zmianę po ponownym zalogowaniu albo `systemctl --user restart plasma-plasmashell`. `make uninstall-krunner` usuwa plik wtyczki, więc znika ona z KRunnera. Szybkie sprawdzenie: `qdbus6 org.kde.krunner /App querySingleRunner tide plasma` otwiera KRunnera z wynikami tylko tej wtyczki.

## Uruchamianie

```bash
make install-daemon          # kopia do $XDG_DATA_HOME/tide/daemon, usługa systemd, start
make uninstall-daemon        # zatrzymanie i usunięcie
journalctl --user -u tide-daemon -f

# Podgląd bez D-Bus (jednorazowy JSON):
/usr/bin/python3 daemon/tide-daemon.py --print
/usr/bin/python3 daemon/tide-daemon.py --print-worktrees      # tylko odczyt, niczego nie usuwa
# Opcje diagnostyczne: --claude-dir, --codex-dir, --workspace, --worktree-depth, --no-processes,
#                      --no-notifications, --no-krunner, --no-tab-marks,
#                      --now 2026-10-26T12:00:00Z
```

Pliki instalacji: `$XDG_DATA_HOME/tide/daemon/` (z kopią palety `palette.json`), `$XDG_CONFIG_HOME/systemd/user/tide-daemon.service`, `$XDG_DATA_HOME/dbus-1/services/org.tide.Daemon.service` (start na żądanie, gdy widżet zapyta, a usługa nie działa) i `org.tide.Daemon.KRunner.service` (to samo dla KRunnera). Usługa używa `/usr/bin/python3` z systemowym PyQt6 — `python3` z pyenv go nie ma.

## Testy

`make test` (unittest, `/usr/bin/python3`):

- `daemon/tests/test_agents_core.py` — rdzeń na syntetycznych dziennikach z `daemon/tests/fixtures/` (zamrożony zegar, procesy podawane jawnie),
- `daemon/tests/test_dbus_contract.py` — usługa na prywatnej magistrali (własny `dbus-daemon` z konfiguracją bez katalogów usług, `privatebus.py`): te same dane co z rdzenia, oraz zmiana stanu w ciągu 3 s po dopisaniu wpisu; bez `dbus-daemon` test jest pomijany,
- `daemon/tests/test_attention.py` — wybór najdłużej czekającej sesji i powiadomienia bez powtórzeń na fixtures; `Notifier` na prywatnej magistrali z atrapą serwera powiadomień (`fake_notifications.py`: Notify, przycisk „Przejdź”, zamknięcie); cała usługa: nowa czekająca sesja → powiadomienie,
- `daemon/tests/test_tabmarks.py` — stan sesji → znacznik karty (z tabelą procesów), oznaczanie, zmiana stanu, przywracanie, zmiana tytułu przez użytkownika, zamknięta karta, restart z plikiem stanu i bez niego — na atrapie Konsole w pamięci; zaplecze D-Bus na atrapie Konsole na prywatnej magistrali (`fake_konsole.py`),
- `kwin/tests/` — skrypt KWin wykonany w `QJSEngine` z atrapami `registerShortcut`/`callDBus`, wykrywanie kolizji skrótu, instalacja i odinstalowanie z atrapami `kpackagetool6`, `kwriteconfig6`, `dbus-send`,
- `daemon/tests/test_worktrees_core.py` — rdzeń worktrees na prawdziwych, tymczasowych repozytoriach (`gitrepos.py`: scalony, ze zmianami, z niescalonymi commitami, z agentem, odłączony, bez gałęzi głównej, główny); git owinięty skryptem zapisującym wywołania, żeby potwierdzić brak `--force`, `-D` i operacji sieciowych,
  Do tego grupy (`active`, `clean`, `decide`, `main`, `missing`) przy zamrożonym zegarze, usuwanie tylko wybranych ścieżek i `prune` wyłącznie dla brakujących katalogów,
- `daemon/tests/test_worktrees_sessions.py` — tytuły z nazw gałęzi i powiązanie z sesjami na syntetycznych dziennikach (najświeższa i najgłębsza, rodzaje tytułów, podagenci, pliki starsze niż 30 dni, duże pliki, pamięć podręczna),
- `daemon/tests/test_worktrees_dbus.py` — `List()`, `Remove()`, `Refresh()`, `Prune()` i `ShowWindow()` przez prywatną magistralę; agent symulowany sesją Claude Code w worktree,
- `daemon/tests/test_krunner_index.py` — indeks historii na syntetycznych dziennikach (`history.py`, `fixtures/history`): tytuły obu narzędzi (także z bazy stanu Codexa zbudowanej w teście), pierwsza wiadomość z początku dużego pliku, odcięcie po 30 dniach, odczyt tylko zmienionych plików,
- `daemon/tests/test_krunner_core.py` — dopasowanie i ranking, `ai `, teksty po polsku, polecenia `Run` (wznowienie Claude/Codex w Konsoli, przejście do okna żywej sesji, projekt, worktree, Rider) z zapisem zamiast uruchamiania; leniwe budowanie indeksu,
- `daemon/tests/test_krunner_dbus.py` — `Match`, `Actions`, `Run` przez prywatną magistralę z sygnaturami KRunnera 6; programy to atrapy (`fakebin.py`: `systemd-run`, `konsole`, `rider`, `claude`, `codex` tylko zapisują wywołanie),
- `apps/worktrees/tests/` — okno: QML na przykładowych danych (`qml6`, offscreen), launcher z `--data --grab`, backend okna na prywatnej magistrali z prawdziwą usługą (Remove tylko dla zaznaczonych), drugie uruchomienie, instalacja z atrapami `update-desktop-database` i `kbuildsycoca6`,
- `daemon/tests/test_install.py`, `daemon/tests/test_krunner_install.py`, `widgets/tests/` — instalacja i odinstalowanie na tymczasowym HOME z atrapami `systemctl`, `kpackagetool6` i `qdbus6`.

Fixtures są pisane ręcznie i naśladują tylko strukturę wpisów — nie zawierają prawdziwych rozmów.
