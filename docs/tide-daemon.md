# tide-daemon

Usługa użytkownika systemd (Python 3, PyQt6), która czyta dzienniki sesji Claude Code i Codexa, wyprowadza stan każdej sesji i wystawia go przez D-Bus. Nie zmienia konfiguracji Claude Code ani Codexa — tylko czyta ich pliki. Drugi obszar to worktrees repozytoriów z katalogu projektów: ich stan i bezpieczne sprzątanie scalonych (opis niżej, [Worktrees](#worktrees)). Trzeci to wtyczka KRunnera z sesjami z 30 dni, projektami i worktrees ([KRunner](#krunner)). Czwarty to bezczynność użytkownika dla tapety z drzemiącym kotem ([Bezczynność](#bezczynność)). Piąty to raport poranny: raz na dobę strona HTML na każdy projekt z ruchem ([Raport poranny](#raport-poranny)). Szósty to historia Limitów AI, prognoza ich wyczerpania i powiadomienia o progach ([Limity AI](#limity-ai)).

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
| `ActivateSession(s id)` | `→ b` | Przechodzi do sesji: okno (Konsole — także karta) albo wątek w Codex Desktop. `false`, gdy nie ma dokąd przejść |
| `ActivateLongestWaiting()` | `→ b` | Przechodzi do okna sesji, która czeka najdłużej (skrót Meta+A). `false` i nic się nie dzieje, gdy nic nie czeka. Sesje `done` nie są brane pod uwagę |
| `Changed` | sygnał bez argumentów | Lista sesji się zmieniła — pobierz `Sessions()` ponownie |

Kolejność listy: najpierw czekające (najdłużej czekająca na górze), potem pracujące, skończone, bezczynne i zakończone (od najświeższej aktywności).

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
| `state` | string | `working`, `waiting`, `done`, `idle`, `ended` |
| `reason` | string | `waiting`: `approval` (zgoda na narzędzie), `question` (pytanie do użytkownika) albo wartość `waitingFor` zgłoszona przez Claude Code. `working`: `background` (tura skończona, ale działa zadanie w tle, do którego agent wróci) albo pusty. `done` i `idle`: `scheduled` (ostatnią turę zaczęło zaplanowane zadanie Codex Desktop) albo pusty. `ended`: pusty |
| `since`, `sinceTs` | ISO 8601 / sekundy epoki | od kiedy trwa bieżący stan (dla `waiting` — od kiedy czeka, dla `done` — od końca tury) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / sekundy epoki | ostatni wpis agenta w dzienniku |
| `lastAction` | string | ostatnia czynność, np. `Edit · main.py`, `shell · make test` |
| `pid` | int | proces agenta albo 0 |
| `canActivate` | bool | czy `ActivateSession` ma dokąd przejść. Sesja z Codex Desktop: zawsze, gdy aplikacja jest zainstalowana (także bez procesu i po zakończeniu — rozmowę da się otworzyć) |
| `terminal` | string | nazwa okna docelowego, np. `Konsole`, `Codex` |
| `newTask` | bool | tylko `true` i tylko przez 10 min: sesję uruchomiło przed chwilą okno „Nowe zadanie” (to samo narzędzie i katalog, sesji nie było przed startem). Widżet pokazuje przy niej znacznik „nowe”. W pozostałych sesjach pola nie ma |
| `activation` | string | jak `ActivateSession` przejdzie do sesji: `deeplink` (Codex Desktop — wątek `codex://threads/<id>` w aplikacji, potem jej okno), `window` (okno terminala albo aplikacji, Konsole z przełączeniem karty) albo pusty |
| `subagents` | int | ilu podagentów Codexa zwinięto do tej sesji i jeszcze działa (stan inny niż `ended`) |
| `subagent` | bool | sesja to podagent Codexa, którego rodzica nie znamy (np. wątek główny sprzed doby) — pokazywany osobno z dopiskiem „podagent” |
| `agentNickname` | string | przydomek podagenta (`agent_nickname`) albo pusty |

Z QML (moduł `org.kde.plasma.workspace.dbus`) korzysta z tego `widgets/lib/DaemonSource.qml`.

Zgodność wstecz: usługa sprzed stanu `done` zgłaszała koniec tury jako `waiting` z `reason: reply`. Widżet (`Theme.sessionState`) i linia na ekranie blokady traktują taki wpis jak `done`, więc po aktualizacji widżetów przed restartem usługi skończone sesje nie świecą brzoskwinią.

## Stany i progi

Pomijamy sesje, w których dziennik nie ma wpisu od ponad **24 h**.

| Stan | Kiedy | Widżet |
| --- | --- | --- |
| `working` | tura trwa: model myśli, pisze albo działa narzędzie | „pracuje”, teal |
| `waiting` | agent **stoi i nie ruszy bez użytkownika**: narzędzie czeka na zgodę, agent zadał pytanie (`AskUserQuestion`, `ExitPlanMode`, `request_user_input`) albo Claude Code zgłosił status `waiting` | „czeka na ciebie”, brzoskwinia; powiadomienie, Meta+A, „◆ ” na karcie Konsole, licznik na ekranie blokady |
| `done` | tura skończyła się zwyczajnie (Claude Code: status `idle`, `end_turn`; Codex: `task_complete`) albo została przerwana, w ciągu ostatnich **30 min** | „skończył”, spokojny błękit (`semantic.done`), puste kółko; bez powiadomienia, bez Meta+A, bez znacznika karty |
| `idle` | `done` albo `waiting` bez ruchu dłużej niż **30 min**; także tura bez żadnego wpisu przez 30 min i bez śladu działającego procesu | „bezczynna”, przygaszona |
| `ended` | proces sesji już nie działa | „zakończona”, przygaszona |

Dlaczego koniec tury nie jest „czekaniem”: agent, który odpowiedział, niczego nie blokuje — może na odpowiedź nikt nie czekać (wątek prowadzony przez zaplanowane zadanie Codex Desktop sam dostanie następną wiadomość), a powiadomienie, brzoskwinia i Meta+A przy każdej skończonej turze uczyły ignorować „czeka”. Teraz „czeka” znaczy zawsze to samo: bez ciebie agent dalej nie pójdzie.

**Meta+A tylko do czekających.** Rozważany zapasowy cel (najświeższa sesja `done`, gdy nikt nie czeka) odrzucony: skrót, który czasem prowadzi do skończonej rozmowy, przestaje znaczyć „ktoś cię potrzebuje”, a do skończonych sesji prowadzą lista w widżecie (przycisk „Przejdź”) i KRunner.

Skąd to wiemy:

- **Claude Code** sam zapisuje stan działającego procesu w `~/.claude/sessions/<pid>.json` (`status`: `busy`, `waiting`, `idle`, `shell`, plus `waitingFor`). To główne źródło — obejmuje także czekanie na zgodę: `waiting` → `waiting` z powodem z `waitingFor`, `idle` → `done`, `busy` → `working`, `shell` → `working` z powodem `background`. Gdy pliku nie ma (starsze wersje), stan wynika z dziennika `~/.claude/projects/<katalog>/<sesja>.jsonl`: `stop_reason: end_turn` albo `system/turn_duration` kończy turę (`done`), `tool_use` bez `tool_result` to wywołanie narzędzia w toku, a `AskUserQuestion`/`ExitPlanMode` bez wyniku — pytanie (`waiting`, `question`).
- **Codex** — `~/.codex/sessions/RRRR/MM/DD/rollout-*.jsonl`: `task_started` zaczyna turę, `task_complete` i `turn_aborted` ją kończą (`done`), `function_call`/`custom_tool_call` bez `*_output` to narzędzie w toku, a `request_user_input` bez wyniku — pytanie (`waiting`, `question`, także przy `approval_policy: never`). Wątki podagentów i recenzenta zgód (`source.subagent`) nie są osobnymi sesjami (niżej, „Podagenci Codexa”).
- **Podagenci Codexa.** Podagent uruchomiony przez agenta (`source.subagent.thread_spawn`, z `parent_thread_id`; `payload.session_id` to id wątku głównego) zwija się do najwyższego znanego przodka: przodek dostaje licznik `subagents` (działający podagenci) i stan najpilniejszego z grupy — `waiting` > `working` > `done` > `idle` > `ended`, przy remisie zostaje przodek — razem z jego powodem, `since` i `lastAction`; `lastActivity` to najnowsza w grupie. Czekający podagent daje więc czekający wątek główny: powiadomienie, Meta+A i znacznik karty Konsole dotyczą wątku głównego, a przejście prowadzi do niego. Podagent, którego rodzica nie znamy (rodzic starszy niż doba), zostaje osobną pozycją z `subagent: true`. Podagent żyje tylko wtedy, gdy proces trzyma jego dziennik (Codex Desktop i CLI trzymają otwarte dzienniki aktywnych podagentów) — bez dopasowania po katalogu roboczym. Recenzent zgód (`subagent.other = "guardian"`) i inne rodzaje bez rodzica są pomijane w całości.
- **Zaplanowane zadania Codex Desktop.** Automatyzacja (`~/.codex/automations/<id>/automation.toml`, `kind = "heartbeat"`, `rrule`, `target_thread_id`) co jakiś czas sama wysyła wiadomość do wskazanego wątku. Taką turę poznajemy po `event_msg/task_started` z `turn_attribution.turn_trigger = "automation_heartbeat_scheduled"` (nowsze wersje; wpisana ręcznie ma `composer`) albo po wiadomości użytkownika (`response_item` `message`, `role: user`) z `internal_chat_message_metadata_passthrough.content_item_kinds` zawierającym `user.heartbeat` (wpisana ręcznie: `user.text`). Po końcu takiej tury sesja jest `done` z powodem `scheduled` (widżet dopisuje „zaplanowane”), a następna tura zaczęta przez użytkownika zdejmuje ten powód. Pytanie albo zgoda w trakcie tury z automatyzacji to nadal `waiting` — agent naprawdę stoi. Format jest nieudokumentowany: gdy znaczniki znikną, tura z automatyzacji będzie zwykłym `done`, a nie `waiting`.
- **Narzędzie w toku.** Przez **10 s** od wywołania sesja „pracuje”. Potem pracuje dalej tylko wtedy, gdy proces agenta uruchomił coś po wywołaniu (np. polecenie powłoki), narzędzie jest z natury długie (podagent, czekanie na podagenta) albo Codex ma `approval_policy: never`. W pozostałych przypadkach uznajemy, że czeka na zgodę. Długie wywołanie MCP bez własnego procesu może więc chwilowo wyglądać jak czekanie na zgodę.
- **Proces sesji.** Claude Code: `pid` z pliku `sessions/<pid>.json`. Codex: proces `codex`, który trzyma otwarty plik dziennika (w aplikacji Codex Desktop to `codex app-server`; wątek, którego aplikacja nie trzyma otwartego, jest pokazywany jako zakończony). Zapasowo: proces narzędzia z tym samym katalogiem roboczym.

## Odświeżanie

- `QFileSystemWatcher` na plikach sesji z ostatniej doby i na katalogach, w których pojawiają się nowe sesje; zmiana pliku → doczytanie tylko dopisanych wpisów po 250 ms,
- co 5 s przeliczenie stanów bez czytania dzienników (progi czasowe, procesy z `/proc`, ok. 15 ms),
- co 30 s przegląd katalogów (np. wznowiona stara sesja Codexa w katalogu sprzed tygodnia).

Przy pierwszym odczycie dużego pliku czytamy tylko pierwszą linię (metadane) i ostatni 1 MB.

## Przejście do okna

Best-effort. Z procesu agenta idziemy w górę drzewa procesów do znanego terminala (Konsole, kitty, Alacritty, foot, WezTerm, Ghostty…) albo aplikacji (Codex Desktop, VS Code). Dla Konsole najpierw przełączamy kartę (`org.kde.konsole.Window.setCurrentSession` z numerem z `KONSOLE_DBUS_SESSION`), potem skrypt KWin ładowany przez `org.kde.KWin /Scripting` (`loadScript`, `start`) aktywuje okno należące do tego procesu. Gdy terminala nie da się ustalić (np. tmux, ssh), sesja nie jest klikalna.

**Codex Desktop (`activation: deeplink`).** Samo podniesienie okna aplikacji pokazywało ostatnio oglądaną rozmowę, nie tę z listy. Sesja z aplikacji (`originator` z „desktop”, np. `Codex Desktop`, albo okno `ChatGPT` w przodkach procesu) otwiera więc swój wątek: usługa przekazuje aplikacji `codex://threads/<id>`, a potem podnosi jej okno skryptem KWin (okno z przodków procesu sesji, a bez procesu — główny proces `ChatGPT`, czyli ten, którego rodzic nie jest `ChatGPT`; gdy aplikacja nie działa, sama pokaże nowe okno). `<id>` to wątek główny: podagent nie ma w aplikacji własnej rozmowy („Nie znaleziono konwersacji”), więc idziemy po `parent_thread_id` przez znane sesje (dowolna głębokość, z ochroną przed pętlą); gdy rodzic najwyższego znanego wątku jest nieznany, bierzemy jego `payload.session_id` (id wątku głównego według Codexa), a bez niego — samego rodzica. Do odnośnika trafia tylko UUID.

- Program bierzemy z linii `Exec` pliku `.desktop` obsługującego `x-scheme-handler/codex`: najpierw `chatgpt.desktop` z katalogów XDG (`$XDG_DATA_HOME`, potem `$XDG_DATA_DIRS`), zapasowo dowolny plik z tym typem w `MimeType` (pomijając `Hidden=true`). Odnośnik zastępuje `%U`/`%u`/`%F`/`%f`, inne kody pól znikają, bez kodu — idzie na koniec; uruchomienie listą argumentów, bez powłoki. Dziś: `chatgpt codex://threads/<id>` (`/usr/bin/chatgpt` → `/usr/lib/chatgpt/codex-launcher`), który działająca instancja przełącza na ten wątek. `$TIDE_CODEX_DESKTOP` wskazuje plik `.desktop` wprost (pusty = bez aplikacji; tak uruchamiają usługę testy).
- Start przez `systemd-run --user --scope` (jak Konsole z worktrees), żeby restart usługi nie zamknął aplikacji uruchomionej przez nas.
- Usługa systemd może nie mieć zmiennych ekranu (zależy od tego, czy sesja zaimportowała środowisko). Gdy brakuje `WAYLAND_DISPLAY` i `DISPLAY`, brakujące zmienne graficzne (`WAYLAND_DISPLAY`, `DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, `XDG_CURRENT_DESKTOP`, `XDG_SESSION_TYPE`…) dobieramy z `systemctl --user show-environment`; reszty środowiska nie ruszamy.
- Bez zainstalowanej aplikacji (nie ma pliku albo programu z `Exec`) sesje z Codex Desktop zachowują się jak dotąd: klikalne tylko z ustalonym oknem, które samo się podnosi.
- Codex z wiersza poleceń (`codex_cli_rs`) w Konsoli bez zmian — przejście do karty terminala.

## Skok do czekającego agenta

**Skrót Meta+A.** Skrypt KWin `tide-agent-jump` (`kwin/agent-jump`, instalacja `make install-agent-jump`) rejestruje skrót przez `registerShortcut` i woła `ActivateLongestWaiting` na `/Agents`. Usługa odświeża stan i procesy, wybiera czekającą sesję z najwcześniejszym `sinceTs` spośród tych, do których okna da się przejść (czekająca w tmux czy przez ssh jest pomijana), i aktywuje okno tak jak `ActivateSession` (Konsole: najpierw karta). Gdy nic nie czeka — zwraca `false` bez żadnego komunikatu; sesje `done` nie są zapasowym celem (wyżej, „Meta+A tylko do czekających”). Jeśli usługa nie działa, D-Bus uruchomi ją przy pierwszym naciśnięciu.

Dlaczego skrypt KWin, a nie rejestracja w KGlobalAccel prosto z usługi: na Waylandzie w Plasmie 6 globalne skróty obsługuje KWin, a `registerShortcut` to udokumentowane, stabilne API skryptów. Protokół D-Bus `org.kde.kglobalaccel` jest wewnętrzny (`doRegister`, `setShortcutKeys` ze strukturami klawiszy `a(ai)`, które zmieniły się między KF5 i KF6, `setInactive`, osobny obiekt `/component/<nazwa>` z sygnałem) — łatwo go zepsuć aktualizacją Frameworks i trudno poprawnie zbudować z PyQt. Skrót w skrypcie KWin nie znika przy restarcie usługi, widać go w Ustawieniach systemowych → Skróty → KWin („Tide: przejdź do agenta, który czeka najdłużej”) i można go tam zmienić. Przełączanie okien i tak idzie przez skrypty KWin.

**Kolizja Meta+A.** KDE nie nada domyślnego klawisza, który ma już inna akcja. Instalator sprawdza `kglobalshortcutsrc` i wypisuje kolizje (np. „Przełączaj między aktywnościami” w plasmashell), niczego w nich nie zmieniając — trzeba wtedy zwolnić Meta+A w Ustawieniach systemowych i przypisać go akcji Tide.

**Powiadomienia.** Tylko wejście w stan `waiting` — koniec tury (`done`) nie powiadamia. Gdy sesja wchodzi w stan `waiting`, usługa wysyła przez `org.freedesktop.Notifications` powiadomienie „Agent czeka: <projekt>” z powodem (np. „Czeka na zgodę: shell · make test”), narzędziem i gałęzią oraz przyciskiem „Przejdź” (także kliknięcie w powiadomienie), który aktywuje okno tej sesji (`ActionInvoked` → `ActivateSession`). Sesja bez ustalonego okna dostaje powiadomienie bez przycisku.

- Ta sama sesja nie dostaje drugiego powiadomienia, dopóki nie przestanie czekać (pracuje, skończyła, bezczynna, zakończona albo zniknęła z listy) i nie zacznie znowu.
- Gdy sesja przestaje czekać, jej powiadomienie jest zamykane (`CloseNotification`), żeby nie wisiało nieaktualne.
- Sesje czekające już w chwili startu usługi nie dostają powiadomień — restart usługi nie zasypuje ekranu.
- `--no-notifications` wyłącza powiadomienia.

Logika (wybór sesji, kiedy powiadomić, tekst) jest w `tide_daemon/attention.py` bez Qt, wiązanie z magistralą w `notifications.py`.

## Stan agenta na karcie Konsole

Karta Konsole, w której działa sesja, pokazuje jej stan (decyzja 29 — koloru paska tytułu dekoracja Tide nie umie przełączać per okno):

| Stan sesji | Tytuł karty | Kolor karty |
| --- | --- | --- |
| `working` | `● projekt · gałąź` | `pastel[semantic.working]` (teal) |
| `waiting` | `◆ projekt · gałąź · czeka` | `pastel[semantic.waiting]` (brzoskwinia) |
| `done` | `○ projekt · gałąź` | bez zmian (puste kółko, informacja — #26) |
| `idle`, `ended`, brak sesji | jak przed oznaczeniem | jak przed oznaczeniem |

Projekt i gałąź pochodzą z sesji (`project`, `branch`); sesja bez projektu dostaje sam znak przed dotychczasowym tytułem. Konsole rysuje kolor karty jako pasek pod tytułem; arkusz stylów paska kart Tide (`make install-konsole`, decyzja 42) układa pigułkę tak, żeby pasek wypadał pod tekstem. Pojedynczej karcie nie da się nadać innego koloru tekstu ani tła, więc stan niosą kształt znaku, słowo „czeka” i pasek.

- Kartę wskazują `KONSOLE_DBUS_SERVICE` i `KONSOLE_DBUS_SESSION` ze środowiska procesu agenta (jak przy przejściu do okna). Gdy w jednej karcie są dwie sesje, „czeka” wygrywa. Inne terminale i aplikacje nie mają API kart — zostają bez znacznika; okna bez agenta nie są ruszane.
- Zmiana idzie przez `org.kde.konsole.Session`: prefiks trafia do obu formatów tytułu (`setTabTitleFormat` 0 i 1), więc przetrwa odświeżenia Konsoli, a wyświetlany tytuł jest ustawiany od razu (`setTitle(1, …)`, potem formaty), bo Konsole przelicza go dopiero po aktywności w terminalu. Kolor: `setTabColor`. Bieżąca karta przekazuje tytuł oknu, więc znacznik widać też na pasku tytułu, w pasku zadań i przełączniku okien.
- Oryginalne formaty i kolor są w `$XDG_STATE_HOME/tide/konsole-tabs.json` (plik znika, gdy nic nie jest oznaczone). Karta wraca do nich, gdy stan mija, przy zamknięciu usługi i po jej restarcie. Jeśli użytkownik w międzyczasie zmienił tytuł albo kolor karty, jego zmiana zostaje.
- Konsolę pytamy tylko przy zmianie stanu albo etykiety karty, wywołaniami z limitem 1 s. `--no-tab-marks` (albo `--no-processes`) wyłącza znacznik.

Logika jest w `tide_daemon/tabmarks.py` bez Qt, zaplecze D-Bus w `konsole.py`. Kolory pochodzą z palety: `palette.json` obok zainstalowanej usługi, w repozytorium `theme/palette/mist.json`, albo `$TIDE_PALETTE`.

## Stan agentów dla promptu powłoki

Prompt Tide (`make install-terminal`) pokazuje chip „claude pracuje” dla katalogu powłoki. Żeby nie pytać usługi przy każdym znaku zachęty, usługa przy każdej zmianie listy sesji zapisuje `$XDG_CACHE_HOME/tide/agents-by-cwd.tsv`: jeden wiersz `<stan>\t<narzędzie>\t<katalog>` na katalog (czeka > pracuje > skończył; bezczynne i zakończone pominięte). Zapis atomowy i tylko przy zmianie treści; przy zamknięciu usługi plik znika. Kod: `tide_daemon/cwdstate.py`.

## Worktrees

| Element | Wartość |
| --- | --- |
| Obiekt | `/Worktrees` |
| Interfejs | `org.tide.Daemon.Worktrees` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `List()` | `→ s` | Lista worktrees jako JSON (pola niżej) |
| `Remove(as paths)` | `→ s` | Sprzątanie wskazanych worktrees; JSON z wynikiem dla każdej ścieżki, w kolejności wejścia. Odpowiedź przychodzi po zakończeniu (praca w tle) |
| `RemoveForced(as paths, b deleteUnmergedBranches, s confirmed)` | `→ s` | Usuwanie po potwierdzeniu „Usuń mimo to” w oknie: także worktrees ze zmianami, niescalone i bez gałęzi głównej (`git worktree remove --force`). Gałąź znika przez `-d` tylko scalona; niescalona zostaje, chyba że `deleteUnmergedBranches` (wtedy `-D`). Twarde blokady (niżej) obowiązują bez wyjątku. `confirmed`: JSON `{ścieżka: {dirty, unmerged, unpushed, branch}}` ze stanem, który widział użytkownik — gdy świeży stan jest gorszy, odmowa `changed`; pusty napis = bez porównania; niepoprawny JSON = odmowa `bad-request` dla wszystkich ścieżek. Wynik jak przy `Remove` |
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
| `unpushed` | int | commity tylko w tym worktree: osiągalne z HEAD, a z żadnej innej lokalnej gałęzi ani gałęzi zdalnej (`rev-list --count HEAD --not --exclude=<gałąź> --branches --remotes`, stan z ostatniego fetch, bez sieci). To one przepadają po `branch -D` albo z odłączonym HEAD. Liczone tylko dla niescalonych (inaczej `0`); `-1`, gdy nie dało się policzyć |
| `lastChange`, `lastChangeTs` | ISO 8601 / sekundy epoki | najnowsze z: czas ostatniego commita HEAD, mtime plików wskazanych przez `git status` |
| `size` | int | bajty na dysku jak `du -s` (bez zagnieżdżonych worktrees); `-1`, póki nie policzone |
| `locked`, `missing` | bool | `git worktree lock` / katalogu już nie ma (`prunable`) |
| `removable`, `blocker` | bool, string | czy sprzątanie go usunie; jeśli nie — pierwszy powód (niżej) |
| `forceRemovable`, `forceBlocker` | bool, string | czy `RemoveForced` go usunie; jeśli nie — twarda blokada: `main`, `protected`, `missing`, `locked` albo `agent` |
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
| `active` | pracuje agent albo ostatnia aktywność krótsza niż 24 h | „W pracy”: otwieranie; bez agenta także pole wyboru (usuwanie wymuszone, jeśli nie jest `removable`) |
| `clean` | `removable`: scalony z lokalną gałęzią główną, czysty, bez agenta, nie zablokowany | „Do sprzątnięcia”: pola wyboru, „Zaznacz wszystkie”, usuwanie zaznaczonych |
| `decide` | pozostałe: niescalone commity, zmiany, blokada, brak gałęzi głównej | „Do decyzji”, z powodem; otwieranie i pole wyboru (usuwanie wymuszone), zablokowane bez pola |

Wynik `Remove` i `RemoveForced` dla każdej ścieżki: `path`, `removed`, `status` (`removed` — worktree i jego gałąź, jeśli była, zniknęły; `branch-kept` — worktree usunięty, gałąź została, powód w `branchMessage`; `refused` — nic nie usunięto, powód w `reason` i `message`), `reason` (pusty albo powód), `message` (po polsku), `branch`, `branchDeleted`, `branchMessage`, `freed` (bajty z ostatniego pomiaru), `forced` (czy to `RemoveForced`).

Powody pominięcia (w tej kolejności): `main`, `protected`, `missing`, `locked`, `agent`, `dirty`, `no-main` (brak lokalnej gałęzi głównej), `unmerged`, a przy samym usuwaniu także `unknown` (ścieżka spoza listy), `git` (git odmówił — komunikat w `message`), `error` (wyjątek w usłudze), przy `RemoveForced` także `changed` (stan gorszy niż w potwierdzeniu) i `bad-request` (niepoprawne `confirmed`). `RemoveForced` pomija tylko twarde blokady: `main`, `protected`, `missing`, `locked`, `agent`.

Zasady sprzątania (`Remove`):

- **Bez sieci.** Żadnego `fetch`/`pull` — „scalony” znaczy scalony z *lokalną* gałęzią główną. Gałąź wypchnięta i scalona tylko na serwerze liczy się jako niescalona.
- **Bez wymuszania.** Tylko `git worktree remove <ścieżka>` bez `--force`; git sam odmówi usunięcia worktree ze zmianami albo plikami nieśledzonymi (pliki ignorowane, np. `node_modules`, znikają razem z katalogiem).
- **Świeży stan.** Przed usunięciem repozytorium jest czytane na nowo (status, scalenie, agenci z chwili wywołania), więc decyzja nie opiera się na liście sprzed minuty.
- **Gałąź tylko bezpiecznie.** Po usunięciu worktree gałąź znika wyłącznie przez `git branch -d` (nigdy `-D`). Git porównuje wtedy z HEAD głównego katalogu albo z gałęzią śledzoną; gdy odmówi, gałąź zostaje, a wynik mówi dlaczego.

Usuwanie wymuszone (`RemoveForced`, decyzja 41):

- **Tylko po potwierdzeniu.** Okno woła je wyłącznie po mocniejszym potwierdzeniu, które wymienia dla każdego worktree, co przepadnie, i ma czerwony przycisk „Usuń mimo to”.
- **Twarde blokady bez wyjątków.** Główny katalog repozytorium, worktree na gałęzi głównej (`master`, `main`, `develop` albo lokalna główna), brakujący katalog, zablokowany (`git worktree lock`) i ten, w którym pracuje agent — odmowa niezależnie od flag. Gałęzi głównych nie kasuje ani `-d`, ani `-D` (druga linia obrony po nazwie gałęzi).
- **Jedno `--force`.** `git worktree remove --force <ścieżka>`: zmiany i pliki nieśledzone idą razem z katalogiem; blokady (`-f -f`) nie zdejmujemy.
- **Gałąź.** Scalona z lokalną gałęzią główną → `git branch -d`; niescalona albo bez gałęzi głównej → zostaje (`status: branch-kept`, `branchMessage` np. „gałąź feat/x zostaje (2 commity poza master)”), chyba że `deleteUnmergedBranches` → `git branch -D`.
- **Świeży stan i potwierdzenie.** Repozytorium jest czytane na nowo jak przy sprzątaniu; jeśli od potwierdzenia przybyło zmian, niescalonych commitów albo commitów tylko tutaj, albo zmieniła się gałąź — odmowa `changed`, nic nie jest usuwane.

Skąd lista:

- repozytoria: katalogi z `.git` (albo repozytoria bare) w katalogu projektów (`--workspace`, `$TIDE_WORKSPACE`, domyślnie `~/DevWorkspace`), do 3 poziomów w głąb (`--worktree-depth`), z pominięciem katalogów ukrytych i zależności (`node_modules`, `vendor`, `bin`, `obj`, `target`, `build`, `dist`…); wewnątrz repozytorium nie szukamy dalej,
- worktrees: `git worktree list --porcelain` każdego repozytorium — także te, które leżą poza katalogiem projektów (np. `~/.codex/worktrees`),
- git działa z `GIT_OPTIONAL_LOCKS=0` (status nie zakłada `index.lock`), `GIT_NO_LAZY_FETCH=1` i `GIT_TERMINAL_PROMPT=0`.

Powiązanie z sesjami (`worktrees/sessions.py`, best effort, tylko odczyt): pliki z ostatnich 30 dni — Claude Code `<claude>/projects/<katalog>/<sesja>.jsonl` (nazwa katalogu to ścieżka z każdym znakiem spoza `[A-Za-z0-9]` zamienionym na `-`, więc nieodwracalna — katalog bierzemy z pola `cwd` wpisów) i Codex `<codex>/sessions/**/rollout-*.jsonl` (`session_meta.payload.cwd`, `turn_context.payload.cwd`; wątki podagentów pomijane). Tytuł: Claude — `custom-title`, `ai-title`, `summary`, inaczej pierwsze polecenie użytkownika; Codex — `thread_name` z `<codex>/session_index.jsonl`, inaczej pierwsze polecenie; najwyżej 80 znaków. Z pliku czytamy tylko początek i koniec (po 192 KB), a wynik trzymamy do zmiany rozmiaru albo mtime (pierwszy przegląd ok. 0,2 s, kolejne kilka ms). W pamięci są tylko metadane (narzędzie, identyfikator, tytuł, katalogi, czas), nic z rozmów nie trafia do dziennika usługi. Sesja uruchomiona w głównym katalogu, która zakłada worktree poleceniem powłoki, nie zostanie powiązana — liczy się katalog roboczy.

Okno „Tide Worktrees” (`apps/worktrees`): `ShowWindow()` uruchamia `/usr/bin/python3 -s $XDG_DATA_HOME/tide/worktrees-window/tide-worktrees.py` (albo `$TIDE_WORKTREES_WINDOW`) przez `systemd-run --user --scope`. Okno rejestruje nazwę `org.tide.WorktreesWindow` (obiekt `/Window`, metoda `Raise()`), więc kolejne uruchomienie tylko pokazuje już otwarte.

Odświeżanie: co **60 s** w tle (jeden wątek roboczy: odczyt, potem rozmiary), na żądanie przez `Refresh()` (okno woła je przy starcie i przyciskiem „Odśwież”) i po sprzątaniu. Rozmiary są w pamięci podręcznej i przeliczane najwyżej co **30 min**. Zmiana sesji agentów przelicza tylko flagę `hasAgent`, bez gita. Wątek główny (D-Bus) nigdy nie czeka na gita ani na dysk.

Otwieranie: Konsole przez `konsole --workdir`, Rider przez skrypt z JetBrains Toolbox (`~/.local/share/JetBrains/Toolbox/scripts/rider`), `rider` z `PATH` albo `$TIDE_RIDER`; bez Ridera przycisk znika. Programy startują przez `systemd-run --user --scope`, żeby restart usługi nie zamykał otwartych okien.

## Nowe zadanie

Okno „Tide: nowe zadanie” (`apps/newtask`, skrót Meta+Alt+A, kafelek „＋ Zadanie” w docku, „＋ Nowe zadanie” w widżecie agentów) zbiera repozytorium, narzędzie (Claude Code albo Codex), polecenie, zrzuty ekranu i decyzję „Nowy worktree”, a usługa zakłada worktree i otwiera kartę Konsoli z agentem. Uzasadnienia: decyzja 47 w `docs/decisions.md`.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Tasks` |
| Interfejs | `org.tide.Daemon.Tasks` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Repos()` | `→ s` | Repozytoria z katalogu projektów jako JSON, od ostatnio aktywnych (najświeższa aktywność któregokolwiek worktree: zmiany, commity, sesje agentów). Z listy obszaru Worktrees; tuż po starcie usługi — jednorazowy przegląd w tle (odpowiedź po zakończeniu) |
| `Defaults()` | `→ s` | JSON: zapamiętany wybór `repoPath`, `tool`, `newWorktree`, dostępne programy `tools` (`{"claude": bool, "codex": bool}`), `terminal` (czy jest Konsole), `imagesDir`, `promptLimit`, `imageLimit` |
| `SuggestBranch(s repoPath, s tool, s prompt)` | `→ s` | JSON `{ok, branch, path, base, slug}` albo `{ok: false, error, message}`: gałąź z polecenia i katalog worktree; zajęta nazwa dostaje `-2`, `-3`… |
| `Start(s request)` | `→ s` | Prośba `{repoPath, tool, prompt, newWorktree, branch, images}`, wynik `{ok, error, message, cwd, branch, worktree, base, title, repoPath, tool, mode}` (`mode`: `tab` albo `window`). Odpowiedź przychodzi po starcie agenta albo po wycofaniu |
| `ShowDialog()` | `→ b` | Otwiera okno „Tide: nowe zadanie” (drugie wywołanie tylko je pokazuje). `false`, gdy okno nie jest zainstalowane |

Pola repozytorium: `name`, `path`, `branch` (gałąź głównego katalogu), `mainBranch` (lokalna master/main/develop jak w Worktrees), `dirty`, `lastActivityTs`, `worktreeDir` (gdzie powstanie nowy worktree).

**Nazwa gałęzi** (`worktrees/naming.py`, `branch_from_prompt`): `<narzędzie>/<opis>` — pierwsze sześć znaczących słów polecenia, bez polskich znaków i słów funkcyjnych, najwyżej 48 znaków („Popraw liczenie VIN-ów w wyszukiwarce” → `codex/popraw-liczenie-vin-ow-wyszukiwarce`). Ta sama konwencja co gałęzie agentów, które okno Worktrees już zna: znacznik narzędzia z prefiksu, narzędzie zgadywane najpierw z prefiksu (`guess_tool`). W oknie nazwę można zmienić.

**Worktree.** Katalog: `<repo>-worktrees/<nazwa>`, jeśli obok repozytorium jest już taki katalog, inaczej `<repo>/.claude/worktrees/<nazwa>` (konwencja `claude --worktree`, ta sama dla obu narzędzi); nazwa katalogu to gałąź bez prefiksu narzędzia. Gdy git nie ignoruje `.claude/worktrees`, usługa dopisuje `/.claude/worktrees/` do lokalnego `.git/info/exclude` (nie do śledzonego `.gitignore`), żeby główny katalog nie zrobił się „brudny”. Polecenie: `git worktree add -b <gałąź> <katalog> <commit lokalnej gałęzi głównej>` — gałąź główna nie jest zmieniana ani przełączana, bez `fetch` (stan lokalny, jak w całym obszarze Worktrees). Wyłączony „Nowy worktree” — agent startuje w głównym katalogu, na tym, co tam jest.

**Odmowy** (pole `error`, po polsku w `message`; nic nie zostaje utworzone): `bad-request`, `bad-tool`, `missing-cli` (programu nie ma w PATH usługi ani w `~/.local/bin`), `no-terminal`, `unknown-repo` (tylko repozytoria z katalogu projektów, bez bare), `empty-prompt`, `one-word` (jedno słowo mogłoby zostać wzięte za podpolecenie, np. `claude doctor`), `prompt-too-long` (32 000 znaków), `bad-prompt` (NUL), `bad-image`, `no-base` (brak master/main/develop), `bad-branch` (`git check-ref-format --branch`), `protected-branch` (master, main, develop, gałąź główna, HEAD), `branch-exists`, `path-exists`, `dirty-base` (główny katalog stoi na gałęzi głównej i ma niezacommitowane zmiany — nowy worktree by ich nie zawierał), `git`, `launch` (Konsola nie uruchomiła zadania — worktree i gałąź wycofane).

**Polecenie agenta** (`tasks/core.py`, `agent_argv`) — lista argumentów, nigdy napis dla powłoki; polecenie to jeden element, po `--`:

- Claude Code: `claude [--add-dir <katalog zrzutów>] -- "<polecenie>[\n\nZałączone zrzuty ekranu (przeczytaj je narzędziem Read):\n<ścieżki>]"` — Claude Code nie ma opcji na obrazy, więc ścieżki idą na koniec polecenia, a `--add-dir` pozwala je przeczytać bez pytania o zgodę,
- Codex: `codex --image <plik> … -- "<polecenie>"` (`-i/--image` dołącza obrazy do pierwszej wiadomości).

Program to bezwzględna ścieżka z PATH usługi (bez rozwiązywania dowiązań: `/snap/bin/codex` wskazuje na `/usr/bin/snap`, który rozpoznaje program po nazwie).

**Karta Konsoli** (`tasks/terminal.py`, `tasks/launch.py`). Konsole 26.08 uruchamia każde okno jako osobny proces (`org.kde.konsole-<pid>`), więc `konsole --new-tab` otwiera nowe okno, a `runCommand`/`sendText` są domyślnie zablokowane („API DBus zagrażające bezpieczeństwu”; sprawdzone na izolowanej magistrali). Dlatego:

1. usługa zapisuje plik zadania (`argv`, katalog, tytuł) w `$XDG_RUNTIME_DIR/tide/tasks/<czas>-<los>.task` (katalog 0700, plik 0600),
2. nowa karta: okno Konsoli, w którym ostatnio otwarto kartę (najmłodszy proces potomny procesu `konsole`, z jego `KONSOLE_DBUS_SERVICE` i `KONSOLE_DBUS_WINDOW`), `org.kde.konsole.Window.newSession("Tide: zadanie agenta", katalog)` — profil, który instaluje `make install-new-task` (`TideTask.profile`, `Command=` launch.py usługi, wygląd po domyślnym profilu z `konsolerc`); tytuł karty „projekt · gałąź” przez `setTitle`/`setTabTitleFormat` (jak znacznik stanu), potem przejście do okna,
3. bez profilu, bez Konsoli na magistrali albo gdy Konsole odmówi — nowe okno: `konsole --workdir <katalog> -p tabtitle=<tytuł> -e python3 launch.py --task <plik>` (przez `systemd-run --user --scope`),
4. `launch.py` w karcie przejmuje plik zadania zmianą nazwy na `.claimed` (bez `--task`: najstarsze zadanie dla katalogu karty), usuwa go i uruchamia `/bin/sh -c '"$0" "$@"; exec "${SHELL:-/bin/sh}"' <argv agenta>` — po zakończeniu agenta zostaje zwykła powłoka,
5. usługa co 100 ms sprawdza przejęcie; po 15 s (`TIDE_TASK_CLAIM_TIMEOUT`) anuluje plik zmianą nazwy na `.cancelled` — jeśli launcher nie był szybszy, worktree jest usuwany (`git worktree remove`, przy odmowie `--force` — to świeży katalog bez pracy agenta), a gałąź `git branch -D` tylko wtedy, gdy nadal wskazuje commit, od którego wyszła. Atomowa zmiana nazwy gwarantuje, że wycofane zadanie nie wystartuje później.

Udany start zapamiętuje wybór (`$XDG_STATE_HOME/tide/new-task.json`: repozytorium, narzędzie, „Nowy worktree”), odświeża listę worktrees, a nowa sesja przez 10 min ma w `/Agents` pole `newTask`. Zrzuty ekranu zapisuje okno w `$XDG_DATA_HOME/tide/tasks/<czas>-<los>/` (`imagesDir`); `Start` przyjmuje tylko pliki obrazów z tego katalogu (najwyżej 10). Okno zamknięte bez startu usuwa swój katalog; po starcie zostaje dla agenta.

Wątek główny (D-Bus) nie czeka na gita: `Repos`, `SuggestBranch` i `Start` pracują w jednym wątku roboczym i odpowiadają z opóźnieniem. `--no-tasks` wyłącza obiekt.

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
| Sesja (`session:<narzędzie>:<id>`) | `Codex · LogViewer · <tytuł>` / `<gałąź> · 3 dni temu` | okno sesji, jeśli jeszcze działa (jak `ActivateSession`); wątek z Codex Desktop — otwarty w aplikacji (`codex://threads/<id>`, podpowiedź „otwórz w Codex”); inaczej nowe okno Konsoli w katalogu sesji z `claude --resume <id>` albo `codex resume <id>`, a po wyjściu z agenta zostaje powłoka |
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

## Bezczynność

Tapeta „Tide: kot” (`org.tide.cat`) zamyka kotu oczy, gdy nikt nie używa komputera. Wie o tym od usługi:

| Element | Wartość |
| --- | --- |
| Obiekt | `/Idle` |
| Interfejs | `org.tide.Daemon.Idle` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Idle` | właściwość `b` | od progu nie było klawiatury, myszy ani dotyku; `false`, gdy źródło nie działa |
| `Available` | właściwość `b` | czy kompozytor podaje bezczynność |
| `ThresholdMs` | właściwość `i` | próg w ms (`wallpaper.dozeAfterS` z palety, domyślnie 15 s) |
| `IsIdle()` | `→ b` | to samo co `Idle` jednym wywołaniem (QML) |
| `State()` | `→ s` | JSON `{available, idle, thresholdMs, idleForMs, source}` — diagnostyka |
| `IdleChanged` | sygnał `(b idle)` | bezczynność się zmieniła |

**Źródło.** Usługa łączy się z kompozytorem Wayland (KWin) i prosi o powiadomienie protokołu `ext_idle_notifier_v1` z progiem z palety: w wersji 2 `get_input_idle_notification` (liczy się tylko wejście użytkownika, blokady bezczynności — np. odtwarzany film — nie przeszkadzają), zapasowo wersja 1 i starszy `org_kde_kwin_idle`. Odlicza kompozytor, a usługa dostaje tylko dwa zdarzenia: „bezczynny” po progu i „wrócił” przy pierwszym wejściu — bez odpytywania i bez zużycia procesora w spoczynku. Klient protokołu to czysty Python (`tide_daemon/wayland_idle.py`), stan i połączenie z pętlą Qt — `tide_daemon/idle.py`. Gniazdo kompozytora jak w libwayland: `$WAYLAND_DISPLAY` w `$XDG_RUNTIME_DIR` (Plasma eksportuje obie zmienne do środowiska usług systemd użytkownika). Gdy kompozytora nie ma albo połączenie się zerwie (restart KWina), `Idle` = `false`, `Available` = `false`, a usługa próbuje ponownie co 2 s, potem coraz rzadziej, do 60 s; komunikaty idą do dziennika tylko przy zmianie.

Próg czyta usługa z kopii palety obok siebie (`palette.json`), więc po zmianie `wallpaper.dozeAfterS` trzeba `make install-daemon`. Opcje: `--idle-after SEKUNDY` (nadpisuje próg), `--no-idle` (bez obiektu `/Idle`). Uzasadnienie i odrzucone źródła: decyzja 39 w `docs/decisions.md`.

```bash
qdbus6 org.tide.Daemon /Idle org.tide.Daemon.Idle.State
dbus-monitor "type='signal',interface='org.tide.Daemon.Idle'"   # IdleChanged przy drzemce i przebudzeniu
```

## Raport poranny

Raz na dobę usługa składa raport z tego, co działo się od poprzedniego: jedna strona HTML na projekt z ruchem i indeks z kartami projektów. Wszystko lokalnie — git, dzienniki agentów i Limity AI z `127.0.0.1`, bez żadnego modelu i bez sieci. Uzasadnienie: decyzja 42 w `docs/decisions.md`.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Reports` |
| Interfejs | `org.tide.Daemon.Reports` |
| Pliki | `$XDG_DATA_HOME/tide/reports/RRRR-MM-DD/index.html` i `<projekt>.html` (katalog 0700, pliki 0600) |
| Stan | `$XDG_STATE_HOME/tide/report-state.json` (doba, okres i doba powiadomienia ostatniego raportu) |
| Ustawienia | `$XDG_CONFIG_HOME/tide/report.json` (opcjonalny): `{"at": "07:00", "dayStart": "04:00", "keepDays": 30, "enabled": true}` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Generate()` | `→ s` | Składa raport bieżącej doby teraz (ten sam początek okresu co poranny, nowy koniec) i zwraca ścieżkę `index.html`; pusty napis przy błędzie. Bez powiadomienia. Odpowiedź po zakończeniu (praca w tle) |
| `Latest()` | `→ s` | `index.html` najnowszego raportu albo pusty napis |
| `Ready` | sygnał `(s path)` | Powstał nowy raport |

```bash
qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Generate   # raport teraz
xdg-open "$(qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Latest)"
```

**Kiedy.** Doba raportu zaczyna się o `dayStart` (04:00) — praca po północy należy jeszcze do poprzedniego dnia. Raport doby powstaje raz: przy pierwszym logowaniu w tej dobie (start usługi; sprawdzenie po 2 min, żeby nie spowalniać logowania) albo o `at` (07:00), jeśli użytkownik jest już zalogowany. Zegar ścienny sprawdzamy co minutę, więc wybudzenie po uśpieniu też łapie zaległy raport. Logowanie o 05:30 daje raport od razu, o 01:00 — nic (doba ma już raport), a o 07:00 przychodzi następny. `--report-at GG:MM` nadpisuje `at`, `--no-report` albo `"enabled": false` wyłącza termin (`Generate()` działa dalej).

**Okres.** Od końca poprzedniego raportu do teraz, najwyżej 7 dni; pierwszy raport — ostatnia doba. `Generate()` w tej samej dobie zachowuje początek okresu, więc nie gubi niczego z porannego raportu.

**Powiadomienie.** Po raporcie z terminu, najwyżej jedno na dobę (doba w pliku stanu, więc restart usługi go nie powtórzy): „Raport poranny gotowy — 3 projekty”, w treści liczba commitów, sesji i czekających, przycisk „Otwórz” (także kliknięcie) → `xdg-open index.html` przez `systemd-run --user --scope`. `--no-notifications` je wyłącza.

**Retencja.** Po każdym raporcie znikają katalogi `RRRR-MM-DD` starsze niż `keepDays` (30) dób. Usuwamy tylko katalogi o takiej nazwie, nie dowiązania.

**Co jest w raporcie.**

- Indeks: liczby (czeka na ciebie, projekty z ruchem, commity z liniami, sesje, przebiegi zaplanowanych zadań), lista „Czeka na ciebie” z odnośnikami do sesji, karty projektów z ruchem (od czekających i najświeższych), zwinięta lista projektów bez ruchu z tym, co w nich zostało (worktrees do sprzątnięcia, zmiany), migawka Limitów AI (poziomy kolorami jak w widżecie) i miejsce na prognozę (`reports/limits.py: forecast` — na razie nic nie zwraca), odnośnik do poprzedniego raportu.
- Strona projektu: sesje agentów z okresu (czeka / pracuje / skończył / przerwana) z tytułem, gałęzią, worktree, czasem trwania, ostatnią aktywnością i **ostatnią wiadomością agenta** z dziennika (to ona mówi, co zrobił; wycinek do granicy akapitu, całość pod „Cała wiadomość”), przyciskiem „Wznów” (kopiuje `cd … && claude --resume …`); dla wątków Codexa z zaplanowanym zadaniem — nazwa i rytm zadania (`automation.toml`) oraz przebiegi z okresu z wynikami. Commity z okresu po gałęziach (gałąź główna, gałęzie z commitami poza główną, odłączone HEAD-y worktrees; każdy commit raz) z autorem, godziną, tematem, liczbą plików i linii oraz znacznikiem „agent: Claude/Codex”, gdy wskazuje na to stopka Co-Authored-By albo adres autora, gałąź/worktree agenta albo commit powstał w czasie sesji agenta w tym katalogu. Worktrees do sprzątnięcia, worktrees z pracą tylko tutaj (commity bez innej gałęzi i serwera, zmiany) i niezacommitowane zmiany w głównym katalogu.
- Projekt to repozytorium z katalogu projektów razem z worktrees (także tymi w `~/.codex/worktrees`); sesja spoza repozytoriów tworzy projekt po nazwie katalogu. Własną stronę dostaje tylko projekt z commitem albo sesją w okresie.

**Czytanie dzienników.** Pliki zmienione od początku okresu; stan, tytuł i ostatnia wiadomość z ogona pliku (2 MB) przez te same parsery co obszar Agents, pierwszy wpis i pierwsze polecenie z początku (256 KB). Przebiegi zaplanowanych zadań: od miejsca w pliku, w którym zaczyna się okres (wyszukiwanie binarne po znacznikach czasu), JSON parsowany tylko w liniach z początkiem albo końcem tury. Stan sesji z ostatniej doby bierzemy z obszaru Agents; starsze — z dziennika (skończona tura to „skończył”). Worktrees z obszaru Worktrees (z rozmiarami), a przed jego pierwszym odczytem raport przegląda repozytoria sam.

**Strony.** Samodzielne pliki: CSS z palety (`palette.json` obok usługi, w repozytorium `theme/palette/mist.json`), Geist i Geist Mono z systemu (`@font-face` z `local()`, zapasowo czcionka systemowa), bez sieci i zewnętrznych plików, ciemny Mist, układ od wąskiego okna (jedna kolumna, 16 px marginesu) do 2560 px (dwie kolumny na stronie projektu). Jedyny skrypt to „Kopiuj”, a `Content-Security-Policy` dopuszcza tylko jego skrót SHA-256. Każdy tekst z gita i dzienników jest escapowany (także w atrybutach). Zamiast schematu `tide://` są ścieżki z „Kopiuj”, polecenia do skopiowania (Konsole, Rider, wznowienie sesji) i zwykły link `file://` do katalogu.

**KRunner.** „raport” (albo „rap…”, „raport poranny”) pokazuje najnowszy raport; Enter otwiera go w przeglądarce.

## Limity AI

Widżet Limity AI czyta pozostałe limity prosto z usługi QuotaApp (decyzja 21). Usługa Tide dokłada do tego pamięć: zapisuje odczyty, z nich liczy tempo i prognozę wyczerpania, a przy progach wysyła powiadomienia. Widżet pokazuje prognozę, gdy usługa działa; bez niej wszystko zostaje jak dotąd.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Limits` |
| Interfejs | `org.tide.Daemon.Limits` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Forecast()` | `→ s` | JSON `{available, error, services}` (opis niżej) |
| `Changed` | sygnał bez argumentów | prognoza albo dostępność QuotaApp się zmieniła — pobierz `Forecast()` ponownie |

`available` mówi, czy ostatnie `GET /status` się udało, `error` to krótki powód po polsku (bez tokenu), a `services` to `{"claude": {"session": {…}, "weekly": {…}}, "chatgpt": {"weekly": {…}}}` — tylko okna, które mają historię. Prognoza jednego okna:

| Pole | Typ | Opis |
| --- | --- | --- |
| `state` | string | `runs-out` (wyczerpie się przed resetem), `lasts` (starczy do resetu), `exhausted` (100%), `unknown` (brak prognozy) |
| `reason` | string | dla `unknown`: `noData`, `tooLittleData`, `noReset`, `resetPassed`, `stale` |
| `text` | string | „wyczerpie się ok. 17:40”, „starczy do resetu” albo pusty |
| `short` | string | „koniec ok. 17:40”, „starczy do resetu” albo pusty — do wąskich wierszy |
| `whenText` | string | sama chwila wyczerpania: „17:40”, „jutro 09:30”, „czwartek 11:00” |
| `used`, `remaining` | int | zużyty i pozostały procent z ostatniego odczytu |
| `readTs`, `resetTs`, `exhaustTs`, `windowStartTs` | int albo null | ostatni odczyt, reset okna, prognozowane wyczerpanie, pierwszy odczyt okna (epoch) |
| `marginS` | int albo null | ile sekund przed resetem limit się skończy (tylko `runs-out`) |
| `ratePerHour` | float albo null | tempo w punktach procentowych na godzinę |
| `points` | int | ile odczytów weszło do prognozy |

**Odczyty.** Co `limits.pollS` (120 s) usługa robi `GET /status` w lokalnym API QuotaApp. To tylko odczyt pamięci tamtej usługi — strony czyta ona sama co 5 min (i wcale przy pełnym ekranie), więc nie dokładamy żadnego wczytania strony ani `POST /refresh`. Nowy odczyt poznajemy po zmianie `lastRead`, a ten sam odczyt nie trafia do historii drugi raz. Port i token są w `~/.config/limity-ai-widget/config.json` (albo `$LIMITY_CONFIG`) — usługa czyta je przy każdym zapytaniu, tylko w pamięci; token nie trafia do dziennika, historii, stanu ani odpowiedzi D-Bus. Zapytanie idzie w wątku roboczym z limitem 5 s, więc wiszące API nie blokuje D-Bus.

**Historia.** `$XDG_STATE_HOME/tide/limits-history.jsonl`: jedna linia na odczyt jednego okna, same liczby — `{"t":1793016060,"s":"claude","w":"session","u":62,"r":1793030400}` (czas odczytu, usługa, okno, zużyty procent, chwila resetu). Etykietę resetu z QuotaApp („poniedziałek 18:30”, „~czwartek 11:51” — liczoną od chwili odczytu, w strefie Europe/Warsaw jak w tamtej usłudze) zamieniamy na najbliższą taką chwilę od odczytu. Plik obejmuje `limits.historyDays` (14) dni: starsze linie wypadają przy starcie i najwyżej raz na godzinę (przepisanie przez plik tymczasowy). Doba to ok. 1150 linii, 14 dni — ok. 1 MB.

**Okna.** Granic okien nie zapisujemy — wynikają z odczytów, więc po restarcie są takie same. Nowe okno zaczyna się, gdy: minął reset poprzedniego odczytu, reset przesunął się dalej niż o `resetJumpMin` (sesja 30 min, tydzień 6 h — „~” drga o minutę), zużycie spadło o więcej niż 3 punkty albo przerwa między odczytami jest dłuższa niż całe okno. Prognoza i powiadomienia biorą wyłącznie bieżące okno.

**Prognoza.** Tempo to nachylenie prostej (najmniejsze kwadraty) przez odczyty bieżącego okna z ostatnich `lookbackMin` — sesja 90 min, tydzień doba. Oś czasu to „czas pracy”: idąc od najnowszego odczytu wstecz, płaski odcinek (zużycie się nie zmienia) liczy się najwyżej jako `idleGapMin`:

- **Sesja (15 min).** Przerwa na obiad nie rozcieńcza tempa: po 80 min przerwy prognoza mówi „przy twoim tempie pracy”, a nie „średnio z ostatnich 90 min”. Krótkie plateau między kolejnymi procentami (strona podaje całe procenty) liczy się w całości, dopóki tempo przekracza ok. 4 punkty/h — wolniejsze i tak nie wyczerpie 5-godzinnego okna.
- **Tydzień (0 = bez skracania).** Noce i przerwy to rytm tygodnia: tempo z ostatniej doby razem z nocą przewiduje kolejne dni lepiej niż tempo z samych godzin pracy, które zakładałoby pracę bez przerwy.

Od ostatniego odczytu: wyczerpanie = czas odczytu + (100 − zużycie) / tempo, zaokrąglone do `roundMin` (sesja 5 min, tydzień 30 min). Przed resetem → `runs-out`, „wyczerpie się ok. 17:40”; po resecie albo przy zerowym tempie → `lasts`, „starczy do resetu”. Bez prognozy (pusty tekst): mniej niż 3 odczyty albo krócej niż `minSpanMin` (sesja 15 min, tydzień 3 h), nieznany reset (np. ChatGPT bez okna sesji), reset już minął albo ostatni odczyt jest starszy niż `staleMin` (sesja 30 min, tydzień 6 h — np. odczyt wstrzymany przy pełnym ekranie).

**Powiadomienia.** Sprawdzane tylko przy nowym odczycie, w stylu „Agent czeka” (aplikacja „Tide”, ikona `view-statistics`, bez przycisku); nowe powiadomienie tego samego okna zastępuje poprzednie.

| Kiedy | Tytuł | Treść |
| --- | --- | --- |
| zużycie przekroczyło próg z `alertUsedPercent` (80, 95) | „Claude: zużyto 82% limitu sesji” | „Zostało 18% · reset 18:30” |
| prognoza: wyczerpanie więcej niż `alertRunoutMarginMin` (30 min) przed resetem | „Claude: limit sesji skończy się przed resetem” | „Przy obecnym tempie wyczerpie się ok. 17:40, a reset dopiero 18:30 · zostało 38%” |

- Każdy próg i wyczerpanie — raz na okno limitu. Skok przez kilka progów naraz (70 → 96%) daje jedno powiadomienie o najwyższym. Próg i wyczerpanie w tym samym odczycie idą razem (treść progu dostaje linię „Przy obecnym tempie …”).
- Co już poszło, jest w `$XDG_STATE_HOME/tide/limits-alerts.json` (okno rozpoznajemy po pierwszym odczycie albo po chwili resetu w tolerancji `resetJumpMin`), więc restart usługi niczego nie powtarza. Pierwszy start z limitem już powyżej progu daje jedno powiadomienie — potem już nie.
- Odczyt z okna, którego reset już minął (np. stary `lastRead` po wstrzymaniu odczytów), nie powiadamia.
- `--no-notifications` wyłącza także te powiadomienia.

**Progi i parametry** są w palecie, sekcja `limits` w `theme/palette/mist.json` (usługa czyta kopię `palette.json` obok siebie, więc po zmianie: `make install-daemon`). Brak sekcji albo klucza = wartości domyślne z `tide_daemon/limits/config.py` (te same co w palecie).

| Klucz | Domyślnie | Znaczenie |
| --- | --- | --- |
| `pollS` | 120 | odstęp `GET /status` w sekundach |
| `historyDays` | 14 | ile dni historii trzymać |
| `alertUsedPercent` | `[80, 95]` | progi zużycia dla powiadomień |
| `alertRunoutMarginMin` | 30 | powiadom, gdy limit skończy się co najmniej tyle minut przed resetem |
| `session.*`, `weekly.*` | | `lengthMin` (300 / 10080), `lookbackMin` (90 / 1440), `minSpanMin` (15 / 180), `idleGapMin` (15 / 0), `staleMin` (30 / 360), `resetJumpMin` (30 / 360), `roundMin` (5 / 30) |

Kolor prognozy „przed resetem” w widżecie to `semantic.runout` (brzoskwinia).

Logika bez Qt jest w `tide_daemon/limits/` (`readings.py`, `history.py`, `forecast.py`, `alerts.py`, `tracker.py`, `backend.py`, `config.py`), odpytywanie w `limits_monitor.py`, powiadomienia w `notifications.py` (`LimitsNotifier`). Uzasadnienie: decyzja 46 w `docs/decisions.md`.

```bash
qdbus6 org.tide.Daemon /Limits org.tide.Daemon.Limits.Forecast
/usr/bin/python3 daemon/tide-daemon.py --print-limits   # historia + jeden GET /status, niczego nie zapisuje
```

## Uruchamianie

```bash
make install-daemon          # kopia do $XDG_DATA_HOME/tide/daemon, usługa systemd, start
make uninstall-daemon        # zatrzymanie i usunięcie
journalctl --user -u tide-daemon -f

# Podgląd bez D-Bus (jednorazowy JSON):
/usr/bin/python3 daemon/tide-daemon.py --print
/usr/bin/python3 daemon/tide-daemon.py --print-worktrees      # tylko odczyt, niczego nie usuwa
# Jednorazowy raport poranny (bez D-Bus, powiadomienia i pliku stanu) do wskazanego katalogu:
/usr/bin/python3 daemon/tide-daemon.py --report --report-days 1 --report-dir /tmp/raport
# Opcje diagnostyczne: --claude-dir, --codex-dir, --workspace, --worktree-depth, --no-processes,
#                      --no-notifications, --no-krunner, --no-tab-marks,
#                      --now 2026-10-26T12:00:00Z, --idle-after 30, --no-idle,
#                      --report-dir, --report-at 07:00, --no-report, --report-delay 120,
#                      --limits-poll 120, --no-limits, --print-limits, --no-tasks
```

Pliki instalacji: `$XDG_DATA_HOME/tide/daemon/` (z kopią palety `palette.json`, z której raport poranny bierze kolory i czcionki), `$XDG_CONFIG_HOME/systemd/user/tide-daemon.service`, `$XDG_DATA_HOME/dbus-1/services/org.tide.Daemon.service` (start na żądanie, gdy widżet zapyta, a usługa nie działa) i `org.tide.Daemon.KRunner.service` (to samo dla KRunnera). Usługa używa `/usr/bin/python3` z systemowym PyQt6 — `python3` z pyenv go nie ma.

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
- `daemon/tests/test_tasks_core.py` — nowe zadanie bez D-Bus na prawdziwych, tymczasowych repozytoriach: argv obu narzędzi (cudzysłowy, `$(…)`, nowe linie, emoji, myślnik na początku, zrzuty) sprawdzone tak, jak dociera do atrapy agenta przez `launch.py`, nazwa gałęzi z polecenia, worktree od gałęzi głównej (master nietknięty, wpis w `info/exclude`), katalog `<repo>-worktrees`, odmowy bez żadnych zmian (gałąź główna, istniejąca, zła nazwa, zmiany na master, brak gałęzi głównej, brak programu, repozytorium spoza katalogu projektów, zrzut spoza katalogu zadań, jedno słowo), wycofanie (gałąź z nowymi commitami zostaje), przejęcie i anulowanie pliku zadania, wybór okna Konsoli i tryb karta/okno na atrapach, znacznik `newTask`,
- `daemon/tests/test_tasks_dbus.py` — `/Tasks` przez prywatną magistralę z prawdziwą usługą i atrapami `konsole` (uruchamia polecenie z `-e`), `systemd-run`, `claude` i `codex` (zapisują argv): `Repos`, `Defaults`, `SuggestBranch`, `Start` z dokładnym poleceniem w agencie, wycofanie, gdy Konsola nie uruchomi zadania, odmowy i praca w głównym katalogu,
- `daemon/tests/test_worktrees_dbus.py` — `List()`, `Remove()`, `Refresh()`, `Prune()` i `ShowWindow()` przez prywatną magistralę; agent symulowany sesją Claude Code w worktree,
- `daemon/tests/test_krunner_index.py` — indeks historii na syntetycznych dziennikach (`history.py`, `fixtures/history`): tytuły obu narzędzi (także z bazy stanu Codexa zbudowanej w teście), pierwsza wiadomość z początku dużego pliku, odcięcie po 30 dniach, odczyt tylko zmienionych plików,
- `daemon/tests/test_codex_desktop.py` — Codex Desktop: odnośnik tylko z UUID, linia `Exec` (kody pól, cudzysłowy, brak programu), szukanie pliku `.desktop` i `$TIDE_CODEX_DESKTOP`, polecenie jako lista argumentów w `systemd-run --scope`, zmienne ekranu z `systemctl --user show-environment`; uruchomienie z atrapą `chatgpt` (`fakebin.py`) — nigdy prawdziwej aplikacji; wątek główny podagenta (kilka poziomów, nieznany rodzic, pętla), zwijanie podagentów i stan najpilniejszy, podagent bez rodzica, `AgentMonitor.activate` i Meta+A (otwarcie wątku głównego, podniesienie okna `ChatGPT`) z atrapą aplikacji,
- `daemon/tests/test_krunner_core.py` — dopasowanie i ranking, `ai `, teksty po polsku, polecenia `Run` (wznowienie Claude/Codex w Konsoli, przejście do okna żywej sesji, wątek Codex Desktop w aplikacji, projekt, worktree, Rider) z zapisem zamiast uruchamiania; leniwe budowanie indeksu,
- `daemon/tests/test_krunner_dbus.py` — `Match`, `Actions`, `Run` przez prywatną magistralę z sygnaturami KRunnera 6; programy to atrapy (`fakebin.py`: `systemd-run`, `konsole`, `rider`, `claude`, `codex` tylko zapisują wywołanie),
- `apps/newtask/tests/` — okno nowego zadania: logika formularza w `qml6`, zrzuty okna z `--data --grab`, zrzuty ekranu (przeciągnięte, ze schowka, atrapa Spectacle, sprzątanie), backend na prywatnej magistrali z prawdziwą usługą, drugie uruchomienie, instalacja z profilem Konsoli; `kwin/tests/test_new_task_shortcut.py` — skrót Meta+Alt+A,
- `apps/worktrees/tests/` — okno: QML na przykładowych danych (`qml6`, offscreen), launcher z `--data --grab`, backend okna na prywatnej magistrali z prawdziwą usługą (Remove tylko dla zaznaczonych), drugie uruchomienie, instalacja z atrapami `update-desktop-database` i `kbuildsycoca6`,
- `daemon/tests/test_idle.py` — bezczynność na atrapie kompozytora Wayland (`fake_wayland.py`: prawdziwe gniazdo i format wiadomości, zamrożony zegar): próg, wejście liczy czas od nowa, wybór protokołu (v2, v1, `org_kde_kwin_idle`), brak protokołu, błąd protokołu, zerwane połączenie i ponowne połączenie; `IdleState` na zamrożonym zegarze; obiekt `/Idle` usługi na prywatnej magistrali (właściwości, `IsIdle`, sygnał `IdleChanged`, próg z palety, brak kompozytora, `--no-idle`). Usługi w testach dostają nieistniejące gniazdo Wayland, więc nigdy nie łączą się z kompozytorem pulpitu,
- `daemon/tests/test_report_schedule.py` — termin raportu (pierwsze logowanie w dobie, 07:00, praca po północy, zmiana czasu, wyłączenie), okres (pierwszy, od poprzedniego, najwyżej 7 dni, ponowne wygenerowanie), jedno powiadomienie na dobę, ustawienia z pliku i opcji, stan, retencja 30 dni (tylko katalogi raportów, bez dowiązań),
- `daemon/tests/test_report_generate.py` — raport z syntetycznego świata (`reportfix.py`: repozytoria z datowanymi commitami, dzienniki Claude Code i Codexa, zaplanowane zadanie, podagent, sesje sprzed okresu) przy zamrożonym zegarze: projekty z ruchem i bez, commity po gałęziach, znacznik agenta, ostatnie wiadomości, przebiegi zadań, worktrees, pliki i uprawnienia, podmiana katalogu, escapowanie (skrypt w temacie commita, tytule sesji, wiadomości agenta i nazwie pliku), CSP, brak zasobów z sieci; odczyt Limitów AI z atrapy API (token tylko w nagłówku, nigdy w błędzie),
- `daemon/tests/test_report_dbus.py` — cała usługa na prywatnej magistrali: raport przy starcie, jedno powiadomienie „Raport poranny gotowy” z „Otwórz” (atrapy `systemd-run`, `xdg-open`), `Generate()`, `Latest()`, brak powtórki po restarcie, wyłączony termin,
- `daemon/tests/test_limits_core.py` — Limity AI bez Qt: odczyty z `/status` i etykiety resetu (także przy zmianie czasu), okna i historia (dopisywanie, restart, 14 dni, zepsute linie), prognoza na syntetycznych seriach (równe tempo, zrywami, przerwa, długi postój, przejście przez reset, tydzień z nocami, za mało danych, nieaktualne dane), powiadomienia raz na okno i po restarcie, konfiguracja z palety, klient API na atrapie QuotaApp (token tylko w nagłówku, nigdy w błędach), `--print-limits` bez zapisu,
- `daemon/tests/test_limits_dbus.py` — obiekt `/Limits` na prywatnej magistrali z atrapą API QuotaApp (HTTP w wątku testu) i atrapą serwera powiadomień: `Forecast()`, sygnał `Changed`, powiadomienia, historia w `$XDG_STATE_HOME`, brak tokenu w plikach, restart bez powtórek, usługa niedostępna, `--no-limits`. Usługi w testach dostają nieistniejący `LIMITY_CONFIG`, więc nigdy nie pytają prawdziwej QuotaApp,
- `daemon/tests/test_install.py`, `daemon/tests/test_krunner_install.py`, `widgets/tests/` — instalacja i odinstalowanie na tymczasowym HOME z atrapami `systemctl`, `kpackagetool6` i `qdbus6`.

Fixtures są pisane ręcznie i naśladują tylko strukturę wpisów — nie zawierają prawdziwych rozmów.
