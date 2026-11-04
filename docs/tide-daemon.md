# tide-daemon

Usługa użytkownika systemd (Python 3, PyQt6), która czyta dzienniki sesji Claude Code i Codexa, wyprowadza stan każdej sesji i wystawia go przez D-Bus. Nie zmienia konfiguracji Claude Code ani Codexa — tylko czyta ich pliki. Drugi obszar to worktrees repozytoriów z katalogu projektów: ich stan i bezpieczne sprzątanie scalonych (opis niżej, [Worktrees](#worktrees)). Trzeci to wtyczka KRunnera z sesjami z 30 dni, projektami i worktrees ([KRunner](#krunner)). Czwarty to bezczynność użytkownika dla tapety z drzemiącym kotem ([Bezczynność](#bezczynność)). Piąty to raport poranny: raz na dobę strona HTML na każdy projekt z ruchem ([Raport poranny](#raport-poranny)). Szósty to historia Limitów AI, prognoza ich wyczerpania i powiadomienia o progach ([Limity AI](#limity-ai)). Siódmy to koordynacja: sesje, które zlecają pracę podagentom, jako tablica ticketów z planem i YouTrackiem ([Koordynacja](#koordynacja)). Ósmy to tablica pracy: karty „rzeczy do zrobienia” ze wszystkich projektów w lokalnym repozytorium git ([Tablica](#tablica)). Dziewiąty to kontenery Dockera: grupy compose z repozytoriami, akcje, logi i porządki zamiast Docker Desktop ([Docker](#docker)). Dziesiąty to modele lokalne: punkty dostępu do serwera modeli (Ollama, później Mac Studio), wywołanie modelu i stan serwerów ([Modele lokalne](#modele-lokalne)).

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
| `cwd` | string | katalog pracy sesji: bieżący katalog roboczy, a gdy agent wszedł (`cd`) do repozytorium tablicy Tide (np. pisze plan w `<tablica>/docs/<projekt>/`) — katalog startu sesji, bez niego repozytorium projektu `docs/<slug>/` (`board/place.py`, PT-28). Z niego też `project`, `branch` i `worktree` — sesja nie staje się „projektem” tide-board na master |
| `launchCwd` | string | katalog startu sesji (stały, także po `cd`): Codex — `cwd` z `session_meta`, Claude Code — pierwszy `cwd` wpisu zgodny z katalogiem dziennika (`projects/<zakodowany katalog>/`); pusty, gdy dziennik tego nie mówi (np. ogon dużego pliku) |
| `launchKey` | string | katalog startu zakodowany jak katalog projektu Claude Code (`/a/b.c` → `-a-b-c`): Claude Code — nazwa katalogu dziennika (zawsze), Codex — z `launchCwd`. Po nim tablica łączy kartę z sesją uruchomioną z karty, gdy `launchCwd` jest pusty |
| `startedTs` | sekundy epoki | pierwszy wpis sesji (z przeczytanej części dziennika) |
| `title` | string | krótki tytuł rozmowy (tylko Claude Code: `custom-title` nadany przez użytkownika, inaczej `ai-title`) |
| `state` | string | `working`, `waiting`, `done`, `idle`, `ended` |
| `reason` | string | `waiting`: `approval` (zgoda na narzędzie), `question` (pytanie do użytkownika), `reply` (tura skończona pytaniem w tekście, bez odpowiedzi użytkownika — PT-29) albo wartość `waitingFor` zgłoszona przez Claude Code. `working`: `background` (tura skończona, ale działa zadanie w tle, do którego agent wróci — tylko gdy tura nie skończyła się pytaniem do użytkownika i najwyżej 2 h bez wpisu w dzienniku) albo pusty. `done` i `idle`: `scheduled` (ostatnią turę zaczęło zaplanowane zadanie Codex Desktop) albo pusty. `ended`: pusty |
| `since`, `sinceTs` | ISO 8601 / sekundy epoki | od kiedy trwa bieżący stan (dla `waiting` — od kiedy czeka, dla `done` — od końca tury) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / sekundy epoki | ostatni wpis agenta w dzienniku |
| `lastAction` | string | ostatnia czynność, np. `Edit · main.py`, `shell · make test` |
| `waitingText` | string | tylko `waiting`: na co agent czeka — treść pytania (`AskUserQuestion` `input.questions[].question`, `ExitPlanMode` „Plan do zatwierdzenia: …”, `request_user_input` i pytanie asynchroniczne Codex Desktop, przy `reply` — pytanie z końca ostatniej wiadomości agenta, najwyżej 240 znaków) albo opis wywołania do zgody (jak `lastAction`, np. `Bash · Push na serwer`); pusty, gdy dziennik nie mówi, na co (sam status `waiting` z pliku Claude Code). W pozostałych stanach pusty. Raport i tablica koordynacji biorą stąd treść „czeka”, nigdy z ostatniego tekstu agenta (PT-23) |
| `pid` | int | proces agenta albo 0 |
| `canActivate` | bool | czy `ActivateSession` ma dokąd przejść. Sesja z Codex Desktop: zawsze, gdy aplikacja jest zainstalowana (także bez procesu i po zakończeniu — rozmowę da się otworzyć) |
| `terminal` | string | nazwa okna docelowego, np. `Konsole`, `Codex` |
| `newTask` | bool | tylko `true` i tylko przez 10 min: sesję uruchomiło przed chwilą okno „Nowe zadanie” (to samo narzędzie i katalog, sesji nie było przed startem). Widżet pokazuje przy niej znacznik „nowe”. W pozostałych sesjach pola nie ma |
| `activation` | string | jak `ActivateSession` przejdzie do sesji: `deeplink` (Codex Desktop — wątek `codex://threads/<id>` w aplikacji, potem jej okno), `window` (okno terminala albo aplikacji, Konsole z przełączeniem karty) albo pusty |
| `subagents` | int | ilu podagentów Codexa zwinięto do tej sesji i jeszcze działa (stan inny niż `ended`) |
| `coordination` | object | tylko przy koordynatorze (sesja z podagentami, [Koordynacja](#koordynacja)): `label`, `done`, `total`, `text` („TOM-20 · 8/13”), `working`, `waiting`, `running`. W pozostałych sesjach pola nie ma |
| `subagent` | bool | sesja to podagent Codexa, którego rodzica nie znamy (np. wątek główny sprzed doby) — pokazywany osobno z dopiskiem „podagent” |
| `agentNickname` | string | przydomek podagenta (`agent_nickname`) albo pusty |
| `model` | string | ostatni model z dziennika sesji (Codex: `turn_context.model`, Claude Code: `message.model` odpowiedzi; bez `<synthetic>`) albo pusty |
| `localModel` | bool | sesja na modelu lokalnym (`agents/localmodel.py`), lista dopuszczonych — w razie wątpliwości `false`: Codex z `session_meta.model_provider` równym `ollama`, `lmstudio`, `oss` albo z przedrostkiem `tide-` (dostawcy z okna „Nowe zadanie”); Claude Code z modelem, który nie jest Claude'em, a jest znany z bieżącego stanu `/Models` (role i modele na serwerach; w `--print` role z `models.json`) albo ma etykietę w stylu Ollamy `nazwa:tag` bez „/” (np. `qwen3-coder:30b`; `dostawca/model:free` z bramki — tylko gdy jest w `/Models`). Model Ollama Cloud (`…:cloud`, `…:120b-cloud`, `modelnames.is_cloud_model`) nigdy nie jest lokalny, także w Codexie z dostawcą `ollama` — lokalna Ollama tylko przekazuje rozmowę na ollama.com. Bedrock, Azure, bramki do chmury i nieznane nazwy bez tagu zostają bez znacznika. Widżet agentów pisze wtedy w drugiej linii „lokalny · model”, a karta Konsoli ze znacznikiem stanu ma dopisek „· lokalnie” |

Z QML (moduł `org.kde.plasma.workspace.dbus`) korzysta z tego `widgets/lib/DaemonSource.qml`.

Zgodność wstecz: usługa sprzed stanu `done` zgłaszała każdy koniec tury jako `waiting` z `reason: reply`, więc widżet, ekran blokady i plakietka Sesji traktowały taki wpis jak `done`. Od PT-29 `waiting/reply` znaczy prawdziwe pytanie bez odpowiedzi (liczone z bieżącej tury przy każdym odczycie dziennika) i wszędzie jest „czeka”; widżety i usługa instalują się razem (`make install`), więc starego znaczenia już nie ma.

## Stany i progi

Pomijamy sesje, w których dziennik nie ma wpisu od ponad **24 h**.

| Stan | Kiedy | Widżet |
| --- | --- | --- |
| `working` | tura trwa: model myśli, pisze albo działa narzędzie | „pracuje”, teal |
| `waiting` | agent **stoi i nie ruszy bez użytkownika**: narzędzie czeka na zgodę, agent zadał pytanie (`AskUserQuestion`, `ExitPlanMode`, `request_user_input`), Claude Code zgłosił status `waiting` albo tura skończyła się pytaniem w tekście, na które użytkownik jeszcze nie odpisał (`reply`, PT-29 — także po 30 min, jak każde pytanie) | „czeka na ciebie”, brzoskwinia; powiadomienie, Meta+A, „◆ ” na karcie Konsole, licznik na ekranie blokady |
| `done` | tura skończyła się zwyczajnie (Claude Code: status `idle`, `end_turn`; Codex: `task_complete`) albo została przerwana, w ciągu ostatnich **30 min** | „skończył”, spokojny błękit (`semantic.done`), puste kółko; bez powiadomienia, bez Meta+A, bez znacznika karty |
| `idle` | `done` albo `waiting` bez ruchu dłużej niż **30 min**; także tura bez żadnego wpisu przez 30 min i bez śladu działającego procesu | „bezczynna”, przygaszona |
| `ended` | proces sesji już nie działa | „zakończona”, przygaszona |

**Pytanie na końcu tury to „czeka”** (PT-29, odwraca część decyzji 36). Agent, który kończy turę pytaniem w tekście — grilling („Pytanie 10: skąd biorą się czat i alerty? …”), „Pushować?” — stoi, dopóki użytkownik nie odpisze: stan `waiting`, powód `reply`, `waitingText` z tym pytaniem, `sinceTs` — chwila pytania. Heurystyka (`agents/question.py`): ostatnia wiadomość tekstowa agenta w turze (Claude Code — ostatni wpis `assistant` z tekstem, bez wywołania narzędzia po nim; Codex — `last_agent_message` z `task_complete` albo ostatnia wiadomość `assistant`), bez bloków kodu i tabel; ostatni akapit, który nie jest listą, i jego ostatnie zdania: zdanie z „?” (po nim najwyżej jedno krótkie zdanie — inaczej to pytanie retoryczne w podsumowaniu), linia „Pytanie N: …”, „Czekam na twoją odpowiedź”, „Daj znać, czy…” albo lista pytań pod zdaniem z dwukropkiem; dopisek pod pytaniem, który prosi o słowo od użytkownika („…jeśli napiszesz „przywróć”.”, „jeśli potwierdzisz”, „napisz „…””), nie zasłania pytania z akapitu wyżej (bez niego liczy się sam dopisek), a zwykła propozycja („Jeśli chcesz, mogę też…”) pytaniem nie jest. Czekanie kasuje wiadomość użytkownika po pytaniu (bez wyników narzędzi i wstawek `<task-notification>`), przerwanie tury i wywołanie narzędzia po tekście. Kompaktowanie Claude Code (także samo, „Compacted while idle”: `system/compact_boundary`, wpis `user` ze streszczeniem — `isCompactSummary`, `isVisibleInTranscriptOnly` — załączniki, `system/informational`), wstawki `isMeta` i echo polecenia lokalnego (`<local-command-caveat>`, potem `<command-name>/model…`, `<local-command-stdout>`) to nie odpowiedź — pytanie i oczekujące `AskUserQuestion` dalej czekają; skill wywołany poleceniem (`<command-message>…`) jest zwykłą wiadomością. Gdy streszczenie i załączniki nie mieszczą się w ogonie (1 MB), pierwszy odczyt dziennika cofa się (×4, najwyżej 16 MB), aż sięgnie ostatniego wpisu agenta albo wiadomości użytkownika (`agents/jsonl.py`, `enough`). Codex przy kompaktowaniu dopisuje rekord `compacted` (z `replacement_history`), którego czytnik nie traktuje jak wiadomości — nie kasuje pytania; liczy się tylko bieżąca tura, a sesja pracująca (status `busy`) nigdy nie „czeka na odpowiedź”. Tura zaczęta przez zaplanowane zadanie Codex Desktop nie czeka nawet z pytaniem (zadanie samo wyśle następną wiadomość) — zostaje `done` z `scheduled`. Podagent (wątek Codexa z rodzicem) nigdy nie czeka na odpowiedź — jego raport czyta koordynator — a zwinięty podagent nie przenosi `reply` na przodka. Wszystko idzie za stanem: widżet („czeka”), Meta+A, powiadomienie „Agent czeka: <projekt>” z „Zadał pytanie: <pytanie>” (raz na wejście w „czeka” — kolejne odświeżenia tego samego pytania nic nie wysyłają), ekran blokady, „Czeka na mnie” w raporcie (z `waitingText`), pytanie na tablicy koordynacji i kolumna „Czeka na mnie” na tablicy z „Przejdź do agenta”.

Zwykły koniec tury bez pytania to nadal `done`: agent, który odpowiedział, niczego nie blokuje, a powiadomienie, brzoskwinia i Meta+A przy każdej skończonej turze uczyły ignorować „czeka”.

**Meta+A tylko do czekających.** Rozważany zapasowy cel (najświeższa sesja `done`, gdy nikt nie czeka) odrzucony: skrót, który czasem prowadzi do skończonej rozmowy, przestaje znaczyć „ktoś cię potrzebuje”, a do skończonych sesji prowadzą lista w widżecie (przycisk „Przejdź”) i KRunner.

Skąd to wiemy:

- **Claude Code** sam zapisuje stan działającego procesu w `~/.claude/sessions/<pid>.json` (`status`: `busy`, `waiting`, `idle`, `shell`, plus `waitingFor`). To główne źródło — obejmuje także czekanie na zgodę: `waiting` → `waiting` z powodem z `waitingFor`, `idle` → `done`, `busy` → `working`, `shell` → `working` z powodem `background`. Przy `shell` pytanie wygrywa: tura skończona pytaniem w tekście (`reply`) albo oczekujące `AskUserQuestion`/`ExitPlanMode` (`question`) to `waiting` — zadanie w tle nie odpowie za użytkownika. `shell` bez żadnego wpisu w dzienniku od `BACKGROUND_STALE` (2 h, licząc od późniejszego z: zmiany statusu i ostatniego wpisu) uznajemy za zawieszone zadanie w tle (np. pętla `until … pgrep -f`, która znajduje samą siebie) i liczymy jak zwykły koniec tury (`done`, po 30 min bez ruchu `idle`). Dowód z procesów nic tu nie mówi — pętla w tle co chwilę uruchamia `sleep`. Gdy pliku nie ma (starsze wersje), stan wynika z dziennika `~/.claude/projects/<katalog>/<sesja>.jsonl`: `stop_reason: end_turn` albo `system/turn_duration` kończy turę (`done`), `tool_use` bez `tool_result` to wywołanie narzędzia w toku, a `AskUserQuestion`/`ExitPlanMode` bez wyniku — pytanie (`waiting`, `question`).
- **Codex** — `<katalog danych>/sessions/RRRR/MM/DD/rollout-*.jsonl` w każdym katalogu danych Codexa (niżej): `task_started` zaczyna turę, `task_complete` i `turn_aborted` ją kończą (`done`), `function_call`/`custom_tool_call` bez `*_output` to narzędzie w toku, a `request_user_input` bez wyniku — pytanie (`waiting`, `question`, także przy `approval_policy: never`). Wątki podagentów i recenzenta zgód (`source.subagent`) nie są osobnymi sesjami (niżej, „Podagenci Codexa”).
- **Katalogi danych Codexa** (`codex_homes.py`, decyzja 65). Codex Desktop pisze do `~/.codex`, a Codex CLI ze snapa (`/snap/bin/codex`) do `~/snap/codex/<rewizja>` — snap ustawia mu `CODEX_HOME: $SNAP_USER_DATA` i nie daje dostępu do `~/.codex`. Usługa czyta więc `~/.codex` i `~/snap/codex/current` (bieżąca rewizja; starsze rewizje mają tylko kopie sprzed odświeżenia snapa — poza `snap revert` i procesem, który przeżył wymuszone odświeżenie i pisze dalej do starej rewizji), a gdy ma `$CODEX_HOME` — tylko ten katalog. `--codex-dir` można powtórzyć; podane zastępują domyślne. Katalogi są rozwiązywane bez dowiązań przy każdym przeglądzie (po odświeżeniu snapa `current` wskazuje nową rewizję, a proces trzyma dziennik pod ścieżką z jej numerem). To samo dotyczy KRunnera, worktrees, raportu i koordynacji: wszędzie, gdzie niżej jest `<codex>`, chodzi o każdy z tych katalogów. Kopię tego samego pliku w drugim katalogu (ta sama ścieżka względem `sessions/`; nazwa `rollout-<czas>-<wątek>[_<segment>].jsonl` niesie id wątku i segmentu) czytamy raz — nowszy plik wygrywa; to jedyne łączenie między katalogami. Nowe pliki w katalogu snapa widać tak samo szybko jak w `~/.codex` — obserwowane są katalogi `sessions/`, bieżący rok, miesiąc i dwa ostatnie dni każdego z nich. Codex ze snapa uruchomiony w katalogu, którego snap nie widzi (np. `/tmp`, ukryty katalog w `~`), zapisuje `cwd` jako `/var/lib/snapd/void`.
- **Segmenty wątku Codexa.** Codex Desktop dzieli długi wątek na kilka plików z tym samym id (`rollout-…-<wątek>.jsonl`, potem `rollout-…-<wątek>_<segment>.jsonl`, kolejne przedziały czasu). Obszar Agents pokazuje wątek jako jedną sesję ze stanem, katalogiem i aktywnością najnowszego segmentu (wcześniej każdy segment był osobną pozycją z tym samym `id`, więc lista miała np. cztery karty jednego wątku); tak samo KRunner. Raport poranny i worktrees widzą segmenty osobno, jak dotąd — raport nie gubi pracy i wyników wcześniejszych segmentów, a wątek wiąże się z worktrees wszystkich segmentów.
- **Podagenci Codexa.** Podagent uruchomiony przez agenta (`source.subagent.thread_spawn`, z `parent_thread_id`; `payload.session_id` to id wątku głównego) zwija się do najwyższego znanego przodka: przodek dostaje licznik `subagents` (działający podagenci) i stan najpilniejszego z grupy — `waiting` > `working` > `done` > `idle` > `ended`, przy remisie zostaje przodek — razem z jego powodem, `since` i `lastAction`; `lastActivity` to najnowsza w grupie. Czekający podagent daje więc czekający wątek główny: powiadomienie, Meta+A i znacznik karty Konsole dotyczą wątku głównego, a przejście prowadzi do niego. Podagent, którego rodzica nie znamy (rodzic starszy niż doba), zostaje osobną pozycją z `subagent: true`. Podagent żyje tylko wtedy, gdy proces trzyma jego dziennik (Codex Desktop i CLI trzymają otwarte dzienniki aktywnych podagentów) — bez dopasowania po katalogu roboczym. Recenzent zgód (`subagent.other = "guardian"`) i inne rodzaje bez rodzica są pomijane w całości.
- **Zaplanowane zadania Codex Desktop.** Automatyzacja (`~/.codex/automations/<id>/automation.toml`, `kind = "heartbeat"`, `rrule`, `target_thread_id`) co jakiś czas sama wysyła wiadomość do wskazanego wątku. Taką turę poznajemy po `event_msg/task_started` z `turn_attribution.turn_trigger = "automation_heartbeat_scheduled"` (nowsze wersje; wpisana ręcznie ma `composer`) albo po wiadomości użytkownika (`response_item` `message`, `role: user`) z `internal_chat_message_metadata_passthrough.content_item_kinds` zawierającym `user.heartbeat` (wpisana ręcznie: `user.text`). Po końcu takiej tury sesja jest `done` z powodem `scheduled` (widżet dopisuje „zaplanowane”), a następna tura zaczęta przez użytkownika zdejmuje ten powód. Pytanie albo zgoda w trakcie tury z automatyzacji to nadal `waiting` — agent naprawdę stoi. Format jest nieudokumentowany: gdy znaczniki znikną, tura z automatyzacji będzie zwykłym `done`, a nie `waiting`.
- **Narzędzie w toku.** Przez **10 s** od wywołania sesja „pracuje”. Potem pracuje dalej tylko wtedy, gdy proces agenta uruchomił coś po wywołaniu (np. polecenie powłoki), narzędzie jest z natury długie (podagent, czekanie na podagenta) albo Codex ma `approval_policy: never`. W pozostałych przypadkach uznajemy, że czeka na zgodę. Długie wywołanie MCP bez własnego procesu może więc chwilowo wyglądać jak czekanie na zgodę.
- **Proces sesji.** Claude Code: `pid` z pliku `sessions/<pid>.json`. Codex: proces `codex`, który trzyma otwarty plik dziennika (porównanie ścieżek bez dowiązań — u Codexa ze snapa `~/snap/codex/<rewizja>/…`; w aplikacji Codex Desktop to `codex app-server`; wątek, którego aplikacja nie trzyma otwartego, jest pokazywany jako zakończony). Zapasowo: proces narzędzia z tym samym katalogiem roboczym.

## Odświeżanie

- `QFileSystemWatcher` na plikach sesji z ostatniej doby i na katalogach, w których pojawiają się nowe sesje; zmiana pliku → doczytanie tylko dopisanych wpisów po 250 ms,
- co 5 s przeliczenie stanów bez czytania dzienników (progi czasowe, procesy z `/proc`, ok. 15 ms),
- co 30 s przegląd katalogów (np. wznowiona stara sesja Codexa w katalogu sprzed tygodnia).

Przy pierwszym odczycie dużego pliku czytamy tylko pierwszą linię (metadane) i ostatni 1 MB.

## Przejście do okna

Best-effort. Z procesu agenta idziemy w górę drzewa procesów do znanego terminala (Konsole, kitty, Alacritty, foot, WezTerm, Ghostty…) albo aplikacji (Codex Desktop, VS Code). Dla Konsole najpierw przełączamy kartę (`org.kde.konsole.Window.setCurrentSession` z numerem z `KONSOLE_DBUS_SESSION`), potem skrypt KWin ładowany przez `org.kde.KWin /Scripting` (`loadScript`, `start`) aktywuje okno należące do tego procesu. Gdy terminala nie da się ustalić (np. tmux, ssh), sesja nie jest klikalna.

**Kilka okien Konsole (PT-33, decyzja 71).** Jeden proces Konsole może mieć kilka okien (karta przeciągnięta poza okno, „Nowe okno”) — mają wtedy ten sam PID, a `KONSOLE_DBUS_WINDOW` ze środowiska agenta wskazuje okno, w którym karta powstała, nie to, w którym jest teraz.

- Okno karty: introspekcja `/Windows` usługi Konsole i to okno, którego `sessionList()` zawiera numer karty; okno ze środowiska tylko wtedy, gdy żadne okno karty nie zgłosi albo okien nie da się wyliczyć. W nim idzie `setCurrentSession`.
- Okno w KWin: bieżąca karta przekazuje oknu swój wyświetlany tytuł (`title(1)`; Qt dopisuje „ — Konsola”), więc skrypt dostaje tytuł karty i tytuły bieżących kart pozostałych okien procesu. Okno pokazujące inną kartę nigdy nie jest wybierane; okno z tytułem karty ma pierwszeństwo przed oknem o nieznanym tytule, potem decyduje bliskość procesu (jak dotąd). Białe znaki porównujemy zwinięte, jak w KWin.
- Identyczny tytuł w innym oknie procesu (np. dwa „● tide · master”): wyświetlany tytuł karty dostaje na `RESTORE_MS` (1 s) dopisek „ ⌖” (`setTitle(1, …)`, formaty tytułu od razu z powrotem), skrypt szuka okna z tym dopiskiem, a potem tytuł wraca — chyba że użytkownik zdążył go zmienić. Znacznik stanu karty (`TabMarker.hold`/`release`) w tym czasie nie rusza tej karty i po oddaniu doprowadza ją do bieżącego stanu; przy zamknięciu usługi tytuł wraca od razu.
- KWin dostaje nowy tytuł okna asynchronicznie, więc gdy w chwili startu skryptu żadne okno nie jest jednoznaczne, skrypt aktywuje najlepsze dostępne i raz poprawia wybór, gdy któreś okno procesu dostanie jednoznaczny tytuł (`captionChanged`). Ze znacznikiem żadne z okien o identycznym tytule nie jest podnoszone na ślepo — skrypt czeka na tytuł ze znacznikiem (okno o nieznanym tytule podnosi od razu, gdy identycznych nie widać). Skrypty KWin nie mają zegara, więc po `FINISH_MS` (0,6 s) usługa wczytuje skrypt kończący: jeśli aktywne okno nie jest najlepszym kandydatem, podnosi najlepsze — kliknięcie nigdy nie kończy się niczym. Skrypt jest usuwany po 2 s; zegar starszego skryptu nie usuwa nowszego, a zegary wcześniejszego przejścia do tej samej karty nie skracają znacznika nowszego.
- Zegary przywrócenia ustawiamy przed wczytaniem skryptu, więc błąd zapisu pliku skryptu nie zostawia znacznika; błędy w zegarach trafiają do dziennika, nie zatrzymują usługi, a karta zawsze wraca do znacznika stanu. Gdy dopisanie znacznika uda się tylko częściowo, tytuł i formaty karty wracają od razu. `strip_marker` zdejmuje też końcowy „ ⌖”, więc po awarii w trakcie przejścia dopisek nie trafia do zapisanego oryginału karty.

Logika jest w `tide_daemon/windows.py` (`Activator`, `kwin_script`), wywołania Konsoli w `konsole.py`.

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

Projekt i gałąź pochodzą z sesji (`project`, `branch`); sesja bez projektu dostaje sam znak przed dotychczasowym tytułem. Konsole rysuje kolor karty jako pasek pod tytułem; arkusz stylów paska kart Tide (`make install-konsole`, decyzja 48) układa pigułkę tak, żeby pasek wypadał pod tekstem. Pojedynczej karcie nie da się nadać innego koloru tekstu ani tła, więc stan niosą kształt znaku, słowo „czeka” i pasek.

- Kartę wskazują `KONSOLE_DBUS_SERVICE` i `KONSOLE_DBUS_SESSION` ze środowiska procesu agenta (jak przy przejściu do okna; okno nie jest potrzebne, więc przeniesienie karty do innego okna znacznikowi nie przeszkadza). Gdy przejście do okna chwilowo dopisuje znacznik do tytułu karty (wyżej, „Kilka okien Konsole”), znacznik stanu tej karty czeka. Gdy w jednej karcie są dwie sesje, „czeka” wygrywa. Inne terminale i aplikacje nie mają API kart — zostają bez znacznika; okna bez agenta nie są ruszane.
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
| `forceRemovable`, `forceBlocker` | bool, string | czy `RemoveForced` go usunie; jeśli nie — twarda blokada: `main`, `protected`, `missing`, `locked`, `agent` albo `nested` |
| `group` | string | grupa w oknie Worktrees: `active`, `clean`, `decide`, `main`, `missing` (reguły niżej) |
| `title`, `tag` | string | czytelny tytuł z nazwy gałęzi i wyciszony znacznik z prefiksu: `codex/summary-tabs-03-version-axis` → `summary-tabs 03 · version axis` i `codex`. Odłączony HEAD: `odłączony HEAD <skrót>` |
| `subject` | string | temat ostatniego commita HEAD (do 120 znaków) |
| `tool` | string | `claude`, `codex` albo pusty: z powiązanej sesji, inaczej z położenia (`~/.codex/worktrees`, `.claude/worktrees`) albo prefiksu gałęzi |
| `sessionId`, `sessionTitle`, `sessionLastActivityTs` | string, string, sekundy epoki | sesja Claude Code albo Codexa z ostatnich 30 dni, której katalog roboczy leży w worktree: żywa z obszaru Agents przed zakończoną, potem najświeższa (puste / 0, gdy brak) |
| `lastActivity`, `lastActivityTs` | ISO 8601 / sekundy epoki | ostatnia aktywność: najnowsze z `lastChangeTs` i ostatniego wpisu powiązanej sesji |

Grupy (`group`, pierwsza pasująca reguła):

| Grupa | Kiedy | W oknie |
| --- | --- | --- |
| `missing` | katalogu już nie ma | linia z `git worktree prune` dla repozytorium |
| `main` | główny katalog repozytorium albo worktree na gałęzi głównej (`protected`) | zwinięta sekcja „Gałęzie główne” (same główne katalogi pomijane) |
| `active` | pracuje agent | „W pracy”: otwieranie, bez pola wyboru |
| `clean` | `removable` i w gałęzi powstał własny commit (praca skończona i scalona) — od razu, bez czekania doby | „Do sprzątnięcia”: pola wyboru, „Zaznacz wszystkie”, usuwanie zaznaczonych |
| `active` | ostatnia aktywność krótsza niż 24 h | „W pracy”: otwieranie i pole wyboru (usuwanie wymuszone, jeśli nie jest `removable`) |
| `clean` | `removable`: scalony z lokalną gałęzią główną, czysty, bez agenta, nie zablokowany | „Do sprzątnięcia”: jak wyżej |
| `decide` | pozostałe: niescalone commity, zmiany, blokada, brak gałęzi głównej | „Do decyzji”, z powodem; otwieranie i pole wyboru (usuwanie wymuszone), zablokowane bez pola |

Okno 24 h zostaje dla świeżych worktrees: taki bez własnych commitów też wygląda na scalony (0 commitów poza gałęzią główną), a to dopiero początek pracy. „Własny commit” to dowód z reflogu, czytanego wprost z plików (bez dodatkowego wywołania gita, tylko dla scalonych i czystych): `<.git>/logs/refs/heads/<gałąź>` ma wpis `commit…`, `cherry-pick`, `revert` albo `rebase (pick|reword|edit)` po ostatnim „branch: Created from” / „branch: Reset to” / „reset: moving to”; odłączony HEAD — `logs/HEAD` z prywatnego katalogu worktree po ostatnim `checkout:` albo `reset: moving to`. Przewinięcie („merge master: Fast-forward”, `pull`) i reset się nie liczą, a reset (`reset: moving to`, np. `reset --hard master` po commicie) liczenie zeruje — taki worktree wraca do okna 24 h. Bez reflogu (wyłączony, repozytorium bare, reftable, wygasły) obowiązuje samo okno 24 h.

Wynik `Remove` i `RemoveForced` dla każdej ścieżki: `path`, `removed`, `status` (`removed` — worktree i jego gałąź, jeśli była, zniknęły; `branch-kept` — worktree usunięty, gałąź została, powód w `branchMessage`; `refused` — nic nie usunięto, powód w `reason` i `message`), `reason` (pusty albo powód), `message` (po polsku), `branch`, `branchDeleted`, `branchMessage`, `freed` (bajty z ostatniego pomiaru), `forced` (czy to `RemoveForced`).

Powody pominięcia (w tej kolejności): `main`, `protected`, `missing`, `locked`, `agent`, `nested` (w katalogu leżą inne worktrees, np. ignorowane `.claude/worktrees/` — `git worktree remove` bez `--force` skasowałby je razem z ich niezacommitowanymi plikami), `dirty`, `no-main` (brak lokalnej gałęzi głównej), `unmerged`, a przy samym usuwaniu także `unknown` (ścieżka spoza listy), `git` (git odmówił — komunikat w `message`), `error` (wyjątek w usłudze), przy `RemoveForced` także `changed` (stan gorszy niż w potwierdzeniu) i `bad-request` (niepoprawne `confirmed`). `RemoveForced` pomija tylko twarde blokady: `main`, `protected`, `missing`, `locked`, `agent`, `nested`.

Zasady sprzątania (`Remove`):

- **Bez sieci.** Żadnego `fetch`/`pull` — „scalony” znaczy scalony z *lokalną* gałęzią główną. Gałąź wypchnięta i scalona tylko na serwerze liczy się jako niescalona.
- **Bez wymuszania.** Tylko `git worktree remove <ścieżka>` bez `--force`; git sam odmówi usunięcia worktree ze zmianami albo plikami nieśledzonymi (pliki ignorowane, np. `node_modules`, znikają razem z katalogiem).
- **Świeży stan.** Przed usunięciem repozytorium jest czytane na nowo (status, scalenie, agenci z chwili wywołania), więc decyzja nie opiera się na liście sprzed minuty.
- **Gałąź tylko bezpiecznie.** Po usunięciu worktree gałąź znika wyłącznie przez `git branch -d` (nigdy `-D`). Git porównuje wtedy z HEAD głównego katalogu albo z gałęzią śledzoną; gdy odmówi, gałąź zostaje, a wynik mówi dlaczego.

Usuwanie wymuszone (`RemoveForced`, decyzja 41):

- **Tylko po potwierdzeniu.** Okno woła je wyłącznie po mocniejszym potwierdzeniu, które wymienia dla każdego worktree, co przepadnie, i ma czerwony przycisk „Usuń mimo to”.
- **Twarde blokady bez wyjątków.** Główny katalog repozytorium, worktree na gałęzi głównej (`master`, `main`, `develop` albo lokalna główna), brakujący katalog, zablokowany (`git worktree lock`), ten, w którym pracuje agent, i ten, w którego katalogu leżą inne worktrees (`nested`; stan zagnieżdżenia czytany na nowo tuż przed usunięciem) — odmowa niezależnie od flag. Gałęzi głównych nie kasuje ani `-d`, ani `-D` (druga linia obrony po nazwie gałęzi).
- **Jedno `--force`.** `git worktree remove --force <ścieżka>`: zmiany i pliki nieśledzone idą razem z katalogiem; blokady (`-f -f`) nie zdejmujemy.
- **Gałąź.** Scalona z lokalną gałęzią główną → `git branch -d`; niescalona albo bez gałęzi głównej → zostaje (`status: branch-kept`, `branchMessage` np. „gałąź feat/x zostaje (2 commity poza master)”), chyba że `deleteUnmergedBranches` → `git branch -D`.
- **Świeży stan i potwierdzenie.** Repozytorium jest czytane na nowo jak przy sprzątaniu; jeśli od potwierdzenia przybyło zmian, niescalonych commitów albo commitów tylko tutaj, albo zmieniła się gałąź — odmowa `changed`, nic nie jest usuwane.

Skąd lista:

- repozytoria: katalogi z `.git` (albo repozytoria bare) w katalogu projektów (`--workspace`, `$TIDE_WORKSPACE`, domyślnie `~/DevWorkspace`), do 3 poziomów w głąb (`--worktree-depth`), z pominięciem katalogów ukrytych i zależności (`node_modules`, `vendor`, `bin`, `obj`, `target`, `build`, `dist`…); wewnątrz repozytorium nie szukamy dalej,
- worktrees: `git worktree list --porcelain` każdego repozytorium — także te, które leżą poza katalogiem projektów (np. `~/.codex/worktrees`),
- git działa z `GIT_OPTIONAL_LOCKS=0` (status nie zakłada `index.lock`), `GIT_NO_LAZY_FETCH=1` i `GIT_TERMINAL_PROMPT=0`.

Powiązanie z sesjami (`worktrees/sessions.py`, best effort, tylko odczyt): pliki z ostatnich 30 dni — Claude Code `<claude>/projects/<katalog>/<sesja>.jsonl` (nazwa katalogu to ścieżka z każdym znakiem spoza `[A-Za-z0-9]` zamienionym na `-`, więc nieodwracalna — katalog bierzemy z pola `cwd` wpisów) i Codex `<codex>/sessions/**/rollout-*.jsonl` (`session_meta.payload.cwd`, `turn_context.payload.cwd`; wątki podagentów pomijane, tak samo wątki zaimportowane przez Codex Desktop z Claude Code — tury `external-import-turn-*` bez żadnego `turn_context`, kopia sesji Claude ze sztucznymi czasami importu, przez którą worktree Claude dostawał znaczek Codexa; zaimportowany i kontynuowany w Codexie liczy się normalnie). Tytuł: Claude — `custom-title`, `ai-title`, `summary`, inaczej pierwsze polecenie użytkownika; Codex — `thread_name` z `<codex>/session_index.jsonl`, inaczej pierwsze polecenie; najwyżej 80 znaków. Z pliku czytamy tylko początek i koniec (po 192 KB), a wynik trzymamy do zmiany rozmiaru albo mtime (pierwszy przegląd ok. 0,2 s, kolejne kilka ms). W pamięci są tylko metadane (narzędzie, identyfikator, tytuł, katalogi, czas), nic z rozmów nie trafia do dziennika usługi. Sesja uruchomiona w głównym katalogu, która zakłada worktree poleceniem powłoki, nie zostanie powiązana — liczy się katalog roboczy.

Widok „Worktrees” okna Tide (`apps/worktrees`, okno `apps/tide`): `ShowWindow()` uruchamia `/usr/bin/python3 -s $XDG_DATA_HOME/tide/tide-window/tide.py --view worktrees` (albo `$TIDE_WINDOW`) przez `systemd-run --user --scope`; bez okna Tide albo przy `$TIDE_WORKTREES_WINDOW` — dawny launcher `tide/worktrees-window/tide-worktrees.py` (po instalacji okna Tide to przekierowanie do niego). Okno rejestruje `org.tide.Window` (obiekt `/Window`, `Show(s widok, s element) → b`), więc kolejne uruchomienie tylko pokazuje już otwarte na widoku; dawne `org.tide.WorktreesWindow` `Raise()` też działa.

Odświeżanie: co **60 s** w tle (jeden wątek roboczy: odczyt, potem rozmiary), na żądanie przez `Refresh()` (okno woła je przy starcie i przyciskiem „Odśwież”) i po sprzątaniu. Rozmiary są w pamięci podręcznej i przeliczane najwyżej co **30 min**. Zmiana sesji agentów przelicza tylko flagę `hasAgent`, bez gita. Wątek główny (D-Bus) nigdy nie czeka na gita ani na dysk.

Otwieranie: Konsole przez `konsole --workdir`, Rider przez skrypt z JetBrains Toolbox (`~/.local/share/JetBrains/Toolbox/scripts/rider`), `rider` z `PATH` albo `$TIDE_RIDER`; bez Ridera przycisk znika. Programy startują przez `systemd-run --user --scope`, żeby restart usługi nie zamykał otwartych okien.

## Nowe zadanie

Okno „Tide: nowe zadanie” (`apps/newtask`, skrót Meta+Alt+A, kafelek „＋ Zadanie” w docku, „＋ Nowe zadanie” w widżecie agentów) zbiera repozytorium, model (chmura albo lokalny), narzędzie (w chmurze Claude Code albo Codex; przy modelu lokalnym program z Tide → Modele), polecenie, zrzuty ekranu i decyzję „Nowy worktree”, a usługa zakłada worktree i otwiera kartę Konsoli z agentem. Uzasadnienia: decyzje 47, 70 i 72 w `docs/decisions.md`.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Tasks` |
| Interfejs | `org.tide.Daemon.Tasks` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Repos()` | `→ s` | Repozytoria z katalogu projektów jako JSON, od ostatnio aktywnych (najświeższa aktywność któregokolwiek worktree: zmiany, commity, sesje agentów). Z listy obszaru Worktrees; tuż po starcie usługi — jednorazowy przegląd w tle (odpowiedź po zakończeniu) |
| `Defaults()` | `→ s` | JSON: zapamiętany wybór `repoPath`, `tool` (przełącznik chmury), `newWorktree`, `model` (`cloud` albo `local`), dostępne programy `tools` (`{"claude": bool, "codex": bool}`), `terminal` (czy jest Konsole), `imagesDir`, `promptLimit`, `imageLimit` |
| `SuggestBranch(s repoPath, s tool, s prompt)` | `→ s` | JSON `{ok, branch, path, base, slug}` albo `{ok: false, error, message}`: gałąź z polecenia i katalog worktree; zajęta nazwa dostaje `-2`, `-3`… |
| `Start(s request)` | `→ s` | Prośba `{repoPath, tool, prompt, newWorktree, branch, images, addDirs, model}` (`model`: `cloud` — domyślnie, albo `local`; przy `local` usługa pomija `tool` i bierze program z `agentProgram` w `models.json`), wynik `{ok, error, message, cwd, branch, worktree, base, title, repoPath, tool, mode, model, local}` (`mode`: `tab` albo `window`; `local`: `{endpoint, model, server}` albo `null`). Odpowiedź przychodzi po starcie agenta albo po wycofaniu |
| `ShowDialog()` | `→ b` | Otwiera okno „Tide: nowe zadanie” (drugie wywołanie tylko je pokazuje). `false`, gdy okno nie jest zainstalowane |

Pola repozytorium: `name`, `path`, `branch` (gałąź głównego katalogu), `mainBranch` (lokalna master/main/develop jak w Worktrees), `dirty`, `lastActivityTs`, `worktreeDir` (gdzie powstanie nowy worktree).

**Nazwa gałęzi** (`worktrees/naming.py`, `branch_from_prompt`): `<narzędzie>/<opis>` — pierwsze sześć znaczących słów polecenia, bez polskich znaków i słów funkcyjnych, najwyżej 48 znaków („Popraw liczenie VIN-ów w wyszukiwarce” → `codex/popraw-liczenie-vin-ow-wyszukiwarce`). Ta sama konwencja co gałęzie agentów, które okno Worktrees już zna: znacznik narzędzia z prefiksu, narzędzie zgadywane najpierw z prefiksu (`guess_tool`). W oknie nazwę można zmienić.

**Worktree.** Katalog: `<repo>-worktrees/<nazwa>`, jeśli obok repozytorium jest już taki katalog, inaczej `<repo>/.claude/worktrees/<nazwa>` (konwencja `claude --worktree`, ta sama dla obu narzędzi); nazwa katalogu to gałąź bez prefiksu narzędzia. Gdy git nie ignoruje `.claude/worktrees`, usługa dopisuje `/.claude/worktrees/` do lokalnego `.git/info/exclude` (nie do śledzonego `.gitignore`), żeby główny katalog nie zrobił się „brudny”. Polecenie: `git worktree add -b <gałąź> <katalog> <commit lokalnej gałęzi głównej>` — gałąź główna nie jest zmieniana ani przełączana, bez `fetch` (stan lokalny, jak w całym obszarze Worktrees). Wyłączony „Nowy worktree” — agent startuje w głównym katalogu, na tym, co tam jest.

**Dodatkowe katalogi** (`addDirs`, uruchomienie z karty tablicy): katalog dokumentów AI karty
projektu z `ai-docs: tide` i repozytoria dodatkowych projektów karty (PT-28), które agent dostaje przez
`--add-dir` (oba narzędzia). Najwyżej 6, sprawdzane po rozwinięciu dowiązań: istniejący katalog pod
`<tablica>/docs/` (tablica z `--board-dir`, inaczej `$TIDE_BOARD_DIR` albo `~/DevWorkspace/tide-board`)
albo główny katalog repozytorium z katalogu projektów, które jest projektem tablicy (`projects/*.md`,
`repo`) — nic innego z tablicy, żadne repozytorium spoza katalogu projektów ani niebędące projektem
(inaczej `bad-add-dir`). Repozytorium samego zadania i powtórzenia pomijamy.

**Model lokalny** (`"model": "local"`, `tasks/local.py`). Model bierze się z roli `agent` w `models.json` (`punkt:model` albo `{"model": "punkt:model", "ctx": N}`; ustawia ją widok „Modele” w oknie Tide). Okno pokazuje obok repozytorium wybór „Chmura” / „Lokalny” ze stanu `Status()` obiektu `/Models` (rola `agent`: `endpoint`, `model`, `available`, `error`; `agentProgram`; odświeżane po `Changed`), a pod nim „Agent”: w chmurze przełącznik Claude Code / Codex, przy „Lokalny” — zamiast niego jedna linia „Codex na qwen3-coder:30b · Ten komputer” (program, model i serwer nazwany jak w widoku „Modele”: „Ten komputer” dla adresu lokalnego, inaczej nazwa z ustawień, np. „Mac-Studio”) z odnośnikiem „zmień w Tide → Modele”. **Program agenta lokalnego** (PT-39, decyzja 72) to `agentProgram` w `models.json` (`codex` — domyślnie — albo `claude`), ustawiany raz w widoku „Modele” przy roli Agent: przy `local` usługa bierze program stąd, a `tool` z prośby pomija (okno wysyła ten sam), a start lokalny zapamiętuje „Lokalny”, ale nie nadpisuje zapamiętanego wyboru przełącznika chmury (`tool` w `new-task.json`). Bez roli, z serwerem, który nie odpowiada, albo bez modelu na serwerze opcja jest wyłączona z krótkim powodem i odnośnikiem „ustaw w Tide → Modele” (okno Tide na widoku `models`; przy wybranym „Lokalny” odnośnik jest w linii agenta). Zapamiętany „Lokalny” zostaje wybrany zawsze — okno nigdy samo nie przechodzi na chmurę: do pierwszej odpowiedzi `Status()` (stan `{"pending": true}`, różny od `null` — „usługa nie podaje stanu”) albo póki usługa nie odczytała serwera start blokuje „Sprawdzam serwer modeli…”, a gdy model jest niedostępny (także przy błędzie `Status()`) — „Model lokalny niedostępny: <powód>”; na chmurę przełącza tylko użytkownik. Rola `agent` z modelem Ollama Cloud (`…:cloud`, `…:120b-cloud`) wyłącza opcję („działa w chmurze Ollamy”), a usługa odmawia jej przy starcie (`local-cloud`). Przy starcie usługa czyta `models.json` i sprawdza punkt jeszcze raz (online, model pobrany), a kontekst bierze z roli (`ctx`), inaczej z serwera, gdy model jest załadowany.

| Narzędzie, serwer | argv (przed `--add-dir`, `--image`, `--`) | Środowisko |
| --- | --- | --- |
| Codex, Ollama na tym komputerze, bez tokenu | `--oss --local-provider ollama --model=<model> [-c model_context_window=<ctx>]` | `CODEX_OSS_BASE_URL=<adres>/v1` |
| Codex, inny serwer (Mac, `openai`) albo z tokenem | `-c model_provider="tide-<punkt>" -c model_providers.tide-<punkt>.name="Tide: <punkt>" -c ….base_url="<adres>/v1" -c ….wire_api="responses"`, przy tokenie `-c ….env_key="TIDE_MODEL_TOKEN" -c shell_environment_policy.exclude=["TIDE_MODEL_TOKEN"] -c features.shell_snapshot=false`, potem `--model=<model> [-c model_context_window=<ctx>]` | przy tokenie `TIDE_MODEL_TOKEN` z pliku (tylko dla Codexa — polecenia, które uruchamia, go nie dostają) |
| Claude Code, każdy serwer | `--model=<model>`, przy tokenie `--settings {"apiKeyHelper": "cat -- <tokenFile>"}` | `ANTHROPIC_BASE_URL=<adres>`, bez tokenu `ANTHROPIC_AUTH_TOKEN=ollama` (z tokenem zmienna usunięta — ma pierwszeństwo przed apiKeyHelper), `ANTHROPIC_API_KEY=` (pusty), `ANTHROPIC_MODEL` i `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL=<model>`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, przy `ctx` ≥ 32768 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=<ctx>` i `CLAUDE_CODE_MAX_OUTPUT_TOKENS=<ctx/4, najwyżej 32000>` |

Model idzie jednym argumentem (`--model=<model>`), więc nazwa z „-” na początku nie stanie się opcją. Codex 0.114 obsługuje tylko Responses API (`wire_api = "chat"` odrzuca), więc serwer innego rodzaju niż Ollama musi mieć `/v1/responses`; Claude Code potrzebuje `/v1/messages` (Ollama ma oba). Okna mniejszego niż 32k Claude Code nie dostaje: przy 16k (ten komputer) sam prompt Claude Code (ok. 11k) z rezerwą na odpowiedź nie mieści się i Claude Code kompaktuje bez końca albo odmawia — bez ustawienia zakłada 200k i krótkie zadania działają, a dłuższą rozmowę serwer przytnie. Tytuł karty: „projekt · gałąź · lokalnie”.

**Token** (`tokenFile` punktu) nie trafia do argv, tytułu karty, dziennika, pliku zadania ani do poleceń, które agent uruchamia (sprawdzone: `env` w sesji obu narzędzi nie pokazuje tokenu, a serwer dostaje `Authorization: Bearer`). Plik zadania (0600) ma tylko ścieżki: `secretEnv` (`{"TIDE_MODEL_TOKEN": "<tokenFile>"}` — `launch.py` czyta token w chwili startu i wstawia go do środowiska Codexa), `tokenFiles` (sprawdzane przed startem) i `unsetEnv` (zmienne usuwane ze środowiska agenta). Codex wyklucza token z poleceń przez `shell_environment_policy.exclude` (ta opcja z `-c` zastępuje listę `exclude` z `config.toml` użytkownika na czas tej sesji), a migawka powłoki (`shell_snapshot`) jest wyłączona, bo bez tego `env` w sesji 0.114 pokazywał token mimo wykluczenia. Claude Code dostaje token z `apiKeyHelper`, więc nie jest on zmienną środowiska; `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` odpada — na tym komputerze uruchamia Bash przez `bwrap` z przestrzenią użytkownika, której zabrania AppArmor (`kernel.apparmor_restrict_unprivileged_userns=1`), i każde polecenie kończy się „bwrap: setting up uid map: Permission denied” (do tego wymusza tryb uprawnień `default`). Nieczytelny albo pusty plik tokenu albo ścieżka względna — agent nie startuje, w karcie zostaje powłoka z komunikatem bez treści tokenu. Zadanie z modelem lokalnym `launch.py` uruchamia sam (proces potomny ze środowiskiem agenta), a potem zamienia się w powłokę z pierwotnym środowiskiem karty: zmienne dodane dla agenta znikają, a te, które użytkownik miał wcześniej, wracają z jego wartościami — `claude` wpisany później w tej karcie nie pójdzie po cichu na serwer lokalny.

**Odmowy** (pole `error`, po polsku w `message`; nic nie zostaje utworzone): `bad-model` (inny model niż `cloud`/`local`), `local-unset` (brak roli `agent` — „ustaw go w Tide → Modele”), `local-cloud` (rola `agent` z modelem Ollama Cloud — „model … działa w chmurze Ollamy — wybierz model lokalny w Tide → Modele”), `local-unavailable` (rola z błędem, serwer nie odpowiada, modelu nie ma na serwerze), `bad-request`, `bad-tool`, `missing-cli` (programu nie ma w PATH usługi ani w `~/.local/bin`), `no-terminal`, `unknown-repo` (tylko repozytoria z katalogu projektów, bez bare), `empty-prompt`, `one-word` (jedno słowo mogłoby zostać wzięte za podpolecenie, np. `claude doctor`), `prompt-too-long` (32 000 znaków), `bad-prompt` (NUL), `bad-image`, `bad-add-dir`, `no-base` (brak master/main/develop), `bad-branch` (`git check-ref-format --branch`), `protected-branch` (master, main, develop, gałąź główna, HEAD), `branch-exists`, `path-exists`, `git`, `launch` (Konsola nie uruchomiła zadania — worktree i gałąź wycofane).

**Polecenie agenta** (`tasks/agentcmd.py`: `interactive()` i `agent_argv()`; `tasks/core.py` tylko je woła) — lista argumentów, nigdy napis dla powłoki; polecenie to jeden element, po `--`:

- Claude Code: `claude [--add-dir <katalog z addDirs>] [--add-dir <katalog zrzutów>] -- "<polecenie>[\n\nZałączone zrzuty ekranu (przeczytaj je narzędziem Read):\n<ścieżki>]"` — Claude Code nie ma opcji na obrazy, więc ścieżki idą na koniec polecenia, a `--add-dir` pozwala je przeczytać bez pytania o zgodę,
- Codex: `codex [--add-dir <katalog z addDirs>] --image <plik> … -- "<polecenie>"` (`-i/--image` dołącza obrazy do pierwszej wiadomości, `--add-dir` daje prawo zapisu w katalogu poza katalogiem roboczym).

**Jedno miejsce, dwie odmiany** (`tasks/agentcmd.py`, PT-76). Polecenie i środowisko Claude Code i Codexa (chmura, model lokalny, inny punkt) składa jeden moduł: `interactive()` — sesja w Konsoli, opisana wyżej (okno „Nowe zadanie” i zlecenie z Tablicy idą tą samą drogą, `Planner.prepare`), oraz `background()` — krok w tle, `claude -p` albo `codex exec`, według wzoru z PT-75 (specyfikacja przepływu kart, „Wzór ustawień piaskownicy Claude Code dla kroku w tle”). Krok w tle:

- polecenie idzie przez stdin (plik `<krok>/in/prompt.txt` otwarty jako stdin programu), nigdy w argv; Claude Code: `-p --setting-sources= --settings <krok>/in/settings.json --permission-prompts none --strict-mcp-config --output-format stream-json --verbose --session-id <uuid>` (albo `--resume`), ustawienia piaskownicy (`failIfUnavailable`, `allowUnsandboxedCommands: false`, `denyRead: ["~"]` z `allowRead` dla worktree, wspólnego `.git`, `in` i `out`, `allowWrite` tylko `out`, `allowedDomains: []`); Codex: `exec --json --cd <worktree> --sandbox workspace-write -c sandbox_workspace_write.writable_roots=[<.git/worktrees/wt>, objects, refs, logs, <krok>/out] -c approval_policy="never" -`,
- środowisko budowane od zera (nic się nie dziedziczy): `HOME`, `USER`, `PATH`, `LANG`, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS` i `SSH_ASKPASS` na `/bin/false`, `GIT_SSH_COMMAND` bez agenta i kluczy, pusty `credential.helper` i `core.askPass`, `GH_CONFIG_DIR` na pusty katalog, martwy `DBUS_SESSION_BUS_ADDRESS`, `GIT_CONFIG_GLOBAL=<krok>/in/gitconfig` (autor „Krok”, `core.excludesFile` z atrapami piaskownicy), Claude: `CLAUDE_CODE_TMPDIR` poza `~`, Codex: `GIT_CONFIG_NOSYSTEM=1`. Katalog kroku nie leży w `/tmp` ani w `~/.local/share` (Codex ze snapa ich nie widzi), Codex: ani w ukrytym katalogu domowym; worktree nie pod `/tmp/claude-<uid>`. To druga warstwa — twardą granicą jest piaskownica,
- token punktu (decyzja 70) jak w oknie zadań: Claude Code przez `apiKeyHelper` dopisany do pliku ustawień kroku, Codex przez `secret_env` (zmienna → plik), który `Background.start()` czyta dopiero przy starcie i wstawia wyłącznie do środowiska programu; w złożonym poleceniu (`argv`, `env`, pliki) tokena nie ma,
- `Background.prepare()` zakłada katalogi i pliki kroku, `start()` uruchamia program w jednostce `systemd-run --user --scope --unit=tide-step-<id>` (`/usr/bin/env -u XDG_RUNTIME_DIR -u INVOCATION_ID <program>`; `XDG_RUNTIME_DIR` tylko dla `systemd-run`, D-Bus niepotrzebny): krok przeżywa restart usługi, token zostaje w środowisku procesu (nie we właściwościach jednostki), wyjście idzie do `<krok>/out/step.log`, nie do rury usługi. `tasks/stepstop.py`: `stop_step()` zatrzymuje jednostkę (`systemctl --user kill` SIGTERM, po `grace` SIGKILL całej cgroup — także wnuki po `setsid`), robi commit WIP na gałęzi kroku i zwraca ostatnie wiadomości z dziennika sesji (Claude Code `projects/*/<id>.jsonl`, Codex najnowszy `rollout-*.jsonl` z `cwd` kroku). Git usługi działa na treści napisanej przez agenta, więc **w piaskownicy `bwrap`** (decyzja 8): `--ro-bind / /`, pusty `tmpfs` na `$HOME` i `/tmp`, zapis tylko w worktree i w `.git/worktrees/<wt>`, `objects`, `refs`, `logs` (katalog gita worktree ustala usługa przed krokiem z repozytorium, nie z pliku `.git` w worktree), reszta `<repo>/.git` do odczytu, bez sieci, IPC i cudzych procesów, czyste środowisko; bez `bwrap` `git_snapshot` odmawia (`no-sandbox`) i zmiany zostają w worktree. Dodatkowo `--git-dir`/`--work-tree`/`GIT_COMMON_DIR`, `core.hooksPath=/dev/null`, bez atrybutów systemu i użytkownika, przywrócony plik `commondir` worktree (git bierze z niego ścieżkę refów mimo `GIT_COMMON_DIR`); odmawia też, gdy HEAD nie jest gałęzią kroku (`wrong-branch`), jest odłączony albo to gałąź główna. Zagnieżdżone repozytoria w worktree są pomijane z notatką, bity `assume-unchanged`/`skip-worktree` na istniejących plikach zdejmowane, po zabitym kroku usuwane są jego blokady (`index.lock`, `HEAD.lock`, `refs/heads/<gałąź>.lock`), pliki z `.gitignore` trafiają do `notes`. Zatrzymanie bez pewności (`systemctl` nie odpowiada) to `not-stopped`, bez ruszania worktree. `step_id`: `[A-Za-z0-9_-]{1,64}`. Krok Codexa: wszystkie serwery MCP z `codex mcp list --json` wyłączone (`-c mcp_servers.<nazwa>.enabled=false`; błąd odczytu listy odmawia startu), opcje przed `resume <id>`. Bez D-Bus i bez Tablicy — to podłączy PT-58.

Program to bezwzględna ścieżka z PATH usługi (bez rozwiązywania dowiązań: `/snap/bin/codex` wskazuje na `/usr/bin/snap`, który rozpoznaje program po nazwie). Codex ze snapa zapisuje sesję w `~/snap/codex/<rewizja>`, nie w `~/.codex` — usługa czyta oba katalogi (Agents, „Katalogi danych Codexa”), więc zadanie z tego okna ma stan w widżecie, powiadomienia i Meta+A.

**Karta Konsoli** (`tasks/terminal.py`, `tasks/launch.py`). Konsole 26.08 uruchamia każde okno jako osobny proces (`org.kde.konsole-<pid>`), więc `konsole --new-tab` otwiera nowe okno, a `runCommand`/`sendText` są domyślnie zablokowane („API DBus zagrażające bezpieczeństwu”; sprawdzone na izolowanej magistrali). Dlatego:

1. usługa zapisuje plik zadania (`argv`, katalog, tytuł) w `$XDG_RUNTIME_DIR/tide/tasks/<czas>-<los>.task` (katalog 0700, plik 0600),
2. nowa karta: okno Konsoli, w którym ostatnio otwarto kartę (najmłodszy proces potomny procesu `konsole`, z jego `KONSOLE_DBUS_SERVICE` i `KONSOLE_DBUS_WINDOW`), `org.kde.konsole.Window.newSession("Tide: zadanie agenta", katalog)` — profil, który instaluje `make install-new-task` (`TideTask.profile`, `Command=` launch.py usługi, wygląd po domyślnym profilu z `konsolerc`); tytuł karty „projekt · gałąź” przez `setTitle`/`setTabTitleFormat` (jak znacznik stanu), potem przejście do okna,
3. bez profilu, bez Konsoli na magistrali albo gdy Konsole odmówi — nowe okno: `konsole --workdir <katalog> -p tabtitle=<tytuł> -e python3 launch.py --task <plik>` (przez `systemd-run --user --scope`),
4. `launch.py` w karcie przejmuje plik zadania zmianą nazwy na `.claimed` (bez `--task`: najstarsze zadanie dla katalogu karty), usuwa go i uruchamia `/bin/sh -c '"$0" "$@"; exec "${SHELL:-/bin/sh}"' <argv agenta>` — po zakończeniu agenta zostaje zwykła powłoka,
5. usługa co 100 ms sprawdza przejęcie; po 15 s (`TIDE_TASK_CLAIM_TIMEOUT`) anuluje plik zmianą nazwy na `.cancelled` — jeśli launcher nie był szybszy, worktree jest usuwany (`git worktree remove`, przy odmowie `--force` — to świeży katalog bez pracy agenta), a gałąź `git branch -D` tylko wtedy, gdy nadal wskazuje commit, od którego wyszła. Atomowa zmiana nazwy gwarantuje, że wycofane zadanie nie wystartuje później.

Udany start zapamiętuje wybór (`$XDG_STATE_HOME/tide/new-task.json`: repozytorium, narzędzie przełącznika chmury — start lokalny zostawia poprzednie, „Nowy worktree”, model), odświeża listę worktrees, a nowa sesja przez 10 min ma w `/Agents` pole `newTask`. Zrzuty ekranu zapisuje okno w `$XDG_DATA_HOME/tide/tasks/<czas>-<los>/` (`imagesDir`); `Start` przyjmuje tylko pliki obrazów z tego katalogu (najwyżej 10). Okno zamknięte bez startu usuwa swój katalog; po starcie zostaje dla agenta.

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
| Sesja (`session:<narzędzie>:<id>`) | `Codex · LogViewer · <tytuł>` / `<gałąź> · 3 dni temu` | okno sesji, jeśli jeszcze działa (jak `ActivateSession`); wątek z Codex Desktop — otwarty w aplikacji (`codex://threads/<id>`, podpowiedź „otwórz w Codex”); inaczej nowe okno Konsoli w katalogu sesji z `claude --resume <id>` albo `codex resume <id>`, a po wyjściu z agenta zostaje powłoka. Codex do wznowienia to ten, który widzi katalog danych sesji: sesja z `~/snap/codex/…` — pierwszy `codex` z katalogu `…/snap/bin` w PATH, zapasowo `/snap/bin/codex`; inna — pierwszy `codex` spoza snapa (PATH, potem `~/.local/bin`); bez pasującego — `codex` z PATH |
| Projekt (`project:<ścieżka>`) | nazwa repozytorium / `Otwórz projekt · ~/DevWorkspace/…` | Konsole w katalogu; akcja `rider` |
| Worktree (`worktree:<ścieżka>`) | gałąź / `Worktree · <repozytorium> · <ścieżka>` | Konsole w katalogu; akcja `rider` |
| Tide Sesje (`sessions:window`, `coordination:<id>`) | „sesje” (od „ses”): `Tide Sesje` / liczba koordynatorów; „sesje <słowa>”: `Sesje · <projekt> · <tytuł>` / `TOM-20 · 8/13 · 1 czeka · 3 w toku · 5 min temu` | okno Tide Sesje (z wybranym koordynatorem); tylko gdy okno jest zainstalowane |

- Zapytanie musi mieć co najmniej 3 znaki. Każde słowo musi trafić w któreś pole (sesja: projekt, gałąź, nazwa katalogu, tytuł, narzędzie; projekt: nazwa; worktree: gałąź, katalog, repozytorium), bez wielkości liter i polskich znaków. Sesje są dodatkowo ważone świeżością.
- `ai <słowa>` pokazuje tylko sesje (do 20); zwykłe zapytanie — do 8 sesji, 5 projektów i 6 worktrees.
- Sesje, których katalogu już nie ma (np. usunięty worktree), nie są pokazywane — nie da się ich wznowić tam, gdzie były. `Run` przyjmuje tylko id z bieżącego indeksu i ścieżki z bieżącej listy worktrees.
- Ikony z motywu: `utilities-terminal` (Claude Code), `openai` (Codex), `folder-git` (projekt), `vcs-branch` (worktree), `jetbrains-rider` (akcja).

**Indeks sesji** (`krunner/index.py`): sesje z ostatnich **30 dni** (czas modyfikacji pliku jako wstępny filtr, potem ostatni wpis w dzienniku). Dla każdej sesji tylko: id, narzędzie, katalog, projekt, gałąź (z dziennika, zapasowo bieżąca), tytuł skrócony do 100 znaków i czas ostatniej aktywności. Tytuł:

- Claude Code: `custom-title` (nadany przez użytkownika), inaczej ostatni `ai-title`, inaczej pierwsza wiadomość użytkownika (bez wstawek poleceń i wpisów `isMeta`),
- Codex: `thread_name` z `session_index.jsonl`, inaczej `threads.title` z bazy stanu `state_<n>.sqlite` — z każdego katalogu danych Codexa (`~/.codex`, `~/snap/codex/current`) (otwieranej tylko do odczytu, `mode=ro`; inny schemat albo błąd — pomijamy), inaczej pierwsza wiadomość użytkownika (`user_message`, `UserMessage` aplikacji albo wiadomość `user` bez kontekstu środowiska i `AGENTS.md`). Wątki podagentów są pomijane.

Indeks powstaje leniwie: pierwsze `Match` po starcie usługi czeka na zbudowanie (u nas ok. 2 s przy 0,6 GB dzienników Claude i 1,9 GB Codexa), w wątku roboczym, bez blokowania D-Bus. Później odświeża się w tle najwyżej co 10 s i tylko przy zapytaniach — czyta wyłącznie pliki ze zmienionym rozmiarem albo czasem modyfikacji, od miejsca, w którym skończył (te same czytniki co obszar Agents: `ClaudeLog`, `CodexLog`, `JsonlTail`). Przy pierwszym odczycie dużego pliku: początek (256 KB, pierwsza wiadomość) i ostatni 1 MB (najnowszy tytuł, gałąź). Wynik zapytania wraca w kilka milisekund.

**Prywatność.** Indeks sesji żyje wyłącznie w pamięci usługi: nie ma pliku pamięci podręcznej, a z rozmów zostaje tylko skrócony tytuł i początek pierwszej wiadomości (100 znaków). Wyszukiwanie po znaczeniu (niżej) wysyła te metadane — tytuł, początek pierwszej wiadomości, projekt, gałąź, tytuły ticketów planu — i dłuższe zapytania KRunnera do serwera roli `embed`: domyślnie Ollamy na tym komputerze, ale po przełączeniu na Maca (albo przypięciu roli do innego serwera) idą do tamtego serwera w sieci; do chmury nigdy (model chmurowy Ollamy jest odrzucany). Na dysku zostają tylko wektory, bez tekstu; wyłączenie w Tide → Modele usuwa je i niczego już nie wysyła. Testy używają wyłącznie syntetycznych dzienników (`daemon/tests/fixtures/history`).

**Wyszukiwanie po znaczeniu** (PT-19, `krunner/semantic.py`, decyzja 75). Zapytanie od 3 słów i 12 znaków (bez `ai `) dostaje oprócz trafień słownych sesje podobne znaczeniowo: „ta sesja o dockerze i portach” znajdzie „Kontenery i mapowanie portów”, choć słowa się nie pokrywają.

- **Model**: rola `embed` w `models.json` (domyślnie `bge-m3` bez punktu — idzie za `default`, więc przełączenie na Maca ją przenosi), wywołanie `models.embed` (Ollama `POST /api/embed` z `truncate` i `keep_alive: 2m`, serwer zgodny z OpenAI `POST /v1/embeddings`) przez tę samą kolejkę co raport i karty; model chmurowy Ollamy odrzucony jak wszędzie.
- **Co trafia do modelu**: tylko metadane z indeksu KRunnera — tytuł, początek pierwszej wiadomości (gdy inny niż tytuł), projekt, gałąź słowami, a dla sesji koordynatora tytuły ticketów jego planu (tablica „Tide Sesje”, najwyżej 12). Nigdy treść rozmowy. Tekst przechodzi przez `strip_secrets` z raportu porannego (klucze, tokeny, linie `.env`, hasła w adresach, katalog domowy → `~`) i jest przycinany do 700 znaków.
- **Indeks**: `$XDG_DATA_HOME/tide/search/sessions.json` (`$TIDE_SEARCH_INDEX` w testach), plik 0600 w katalogu 0700, zapis atomowy. Bez tekstów: przy sesji skrót tekstu, czas ostatniej aktywności i znormalizowany wektor (float16, base64 — ok. 2,7 KB na sesję przy 1024 wymiarach). Nagłówek ma model (`bge-m3:latest`) i liczbę wymiarów — inny model roli `embed` albo inne wymiary to indeks od nowa. Granice: sesje z okna KRunnera (30 dni), najwyżej 2000 najświeższych.
- **Kiedy liczy**: wątek `tide-search-index` — pierwszy przebieg 60 s po starcie (`--search-delay`), potem co 5 min i od razu po każdym zapisie ustawień modeli (włączenie, wyłączenie, inny model). Przyrostowo: tylko nowe i zmienione sesje, partiami po 16, z priorytetem `queue.IDLE` (najniższy — rusza, gdy na serwerze nic innego nie czeka). Na karcie 8 GB załadowanie bge-m3 wypycha qwen3:8b (sprawdzone na Ollamie tego komputera), więc przebieg z czymś do policzenia — i każda jego kolejna partia — czeka też na 3 min ciszy po ostatnim wywołaniu innym modelem — inaczej następny opis karty ładowałby swój model od nowa. Partia, która czeka w kolejce dłużej niż 5 min — następna próba za minutę.
- **Zapytanie**: tylko od 3 słów i 12 znaków i nie wyglądające na polecenie powłoki, adres ani ścieżkę (`://`, `/…`, `~/…`, `./…`, `-flaga`, znaki powłoki `|`, `<`, `>`, `$`, `=` i odwrotny apostrof) — KRunner bywa też terminalem i paskiem adresu. Przechodzi przez `strip_secrets`; gdy coś w nim wygląda na sekret albo ścieżkę domową, nie idzie do modelu wcale. Po 0,3 s ciszy (KRunner pyta przy każdym naciśnięciu klawisza; nowsze zapytanie unieważnia starsze, zanim cokolwiek pójdzie do serwera), z pamięcią 64 ostatnich wektorów zapytań, z priorytetem `queue.HIGH` i limitem czekania w kolejce 2 s. KRunner czeka na wektor najwyżej 1,5 s — potem dostaje same słowa, a wywołanie kończy się w tle (zimny model ładuje się kilka sekund; następne zapytanie trafia już na załadowany, ok. 20 ms). Serwer nie odpowiada albo modelu nie ma — przez 30 s bez prób; zajęty serwer (raport) — bez przerwy. Żaden błąd nie trafia do KRunnera.
- **Ranking**: podobieństwo kosinusowe. Wynik musi mieć co najmniej 0,42, być o 0,06 ponad średnią wszystkich sesji (tło tego zapytania) i najwyżej 0,08 poniżej najlepszego; najwyżej 5 (w ramach limitu sesji: 8, przy `ai ` 20). Progi z pomiaru bge-m3 na przykładowych sesjach: niezwiązane teksty mają 0,3–0,4, pasujące 0,45–0,6.
- **Łączenie**: słowa mają pierwszeństwo — sesja znaleziona słowami nie powtarza się, a znaczeniowe mają kategorię 30 (LOW), trafność poniżej najsłabszej sesji ze słów (najwyżej 0,5) i dopisek w podpisie: `feat/login · 3 dni temu · podobne znaczeniowo`. Uruchomienie jak każdej sesji.
- **Wyłączenie**: `semanticSearch: false` w `models.json` (Tide → Modele, wiersz „Wyszukiwanie”, `SetSemanticSearch("off")`) — nic nie jest liczone, partia czekająca w kolejce albo trwająca jest przerywana, a plik indeksu znika od razu, w chwili zapisu ustawienia (także w usłudze z `--no-krunner`, która indeksu nie prowadzi, ale mogła go zostawić wcześniej). Stan indeksu: `Status().search` obiektu `/Models` (niżej).

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

Raz na dobę (o 07:00) usługa składa raport z tego, co działo się od poprzedniego: stan prac na dzień — „Dziś w skrócie” (3–5 zwykłych zdań o całym dniu), strona HTML na każdy projekt-repozytorium z ruchem albo z pracą w toku — z jego własnym „Na daily” na górze (co zrobiono, co trwa, co czeka na ciebie, co dalej, osobno porządki) — i indeks, który jest przeglądem projektów. Raport bieżącej doby żyje: odświeża się sam po zmianach w pracy, „czeka” jest przeliczane na żywo, a po każdym skończonym tickecie dochodzi notka „Skończone dziś” z powiadomieniem ([Żywy raport](#żywy-raport)). To raport dla ludzi: bez żargonu pracy agentów, ścieżek plików i numerów ticketów w treści. Dane są lokalne — git, dzienniki agentów, plik tablicy koordynacji, plany w repozytoriach, Limity AI z `127.0.0.1` i (tylko odczyt) opisy ticketów z YouTracka w sieci lokalnej; jedyne, co może wyjść z komputera, to zwięzłe fakty do opisu „Na daily” modelem przez lokalny Claude Code (domyślnie, `"summary": "claude"`). Z `"summary": "local"` opis pisze model lokalny z `models.json` (sekcja „Modele lokalne”) i nic nie wychodzi z komputera — chyba że rola `summary` wskazuje serwer w sieci (np. przyszły Mac Studio), wtedy fakty idą tylko do niego; Claude nie jest wtedy wołany nigdy, także gdy serwer lokalny nie działa. `"summary": "rules"` — bez żadnego modelu. Uzasadnienie: decyzje 42, 52, 53 i 64 w `docs/decisions.md`.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Reports` |
| Interfejs | `org.tide.Daemon.Reports` |
| Pliki | `$XDG_DATA_HOME/tide/reports/RRRR-MM-DD/index.html`, `<projekt>.html` i `report.json` — dane dla widoku „Raporty” okna Tide (katalog 0700, pliki 0600) |
| Stan | `$XDG_STATE_HOME/tide/report-state.json` (doba, okres, doba powiadomienia, doba otwartego raportu i liczby projektów/czekających ostatniego raportu) |
| Ustawienia | `$XDG_CONFIG_HOME/tide/report.json` (opcjonalny): `{"at": "07:00", "dayStart": "04:00", "keepDays": 30, "enabled": true, "summary": "claude", "summaryModel": "sonnet"}`; `summary`: `claude`, `local` albo `rules` (też dla opisów kart tablicy); `summaryModel` liczy się tylko przy `claude` — w trybie `local` model biorą role `summary` i `describe` z `models.json`. Brak klucza `summary` — `claude`; nieznana wartość (np. literówka `"Local"`) — `rules` z ostrzeżeniem w dzienniku, żeby pomyłka nie wysyłała faktów do Anthropic. Plik czytany przy starcie usługi; `summary` przełącza się też z okna Tide (`/Models SetReportSummary`, sekcja „Modele lokalne”) — zapis w pliku i od razu w działającej usłudze |
| Pamięć opisu | `<raport>/summary.json` (0600, wersja 2): tożsamość modelu (`identity`), nazwa i rodzaj (`claude`/`local`), per projekt klucz jego faktów i odpowiedź modelu (albo `null` — reguły) oraz zdania „Dziś w skrócie” z kluczem wszystkich projektów (tylko `claude`) |
| Notki | `$XDG_STATE_HOME/tide/report-notes.json` (0600): opisane tickety (`seen`, 21 dni) i notki „Skończone dziś” po dobach (`days`, tyle dób co raporty) |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Generate()` | `→ s` | Składa raport bieżącej doby teraz (ten sam początek okresu co poranny, nowy koniec) i zwraca ścieżkę `index.html`; pusty napis przy błędzie. Bez powiadomienia. Odpowiedź po zakończeniu (praca w tle) |
| `Latest()` | `→ s` | `index.html` najnowszego raportu albo pusty napis |
| `Status()` | `→ s` | JSON najnowszego raportu dla widżetów: `{path, day, generatedAt, projects, waiting, unread}` — `generatedAt` w sekundach epoki, `projects` to projekty w raporcie (z ruchem albo z pracą w toku), `waiting` — „czeka na ciebie”, `unread` — raportu tej doby jeszcze nie otwarto. Raport bieżącej doby ma `waiting` przeliczone na żywo (bez pozycji, na które agent już nie czeka) i dodatkowo `waitingNow` (sesje, które czekają teraz) i `finished` (notki „Skończone dziś”). Bez raportu `path` i `day` puste, `unread` false; liczby `null`, gdy raport nie przeszedł przez usługę (np. `--report`) |
| `Open()` | `→ b` | Otwiera najnowszy raport w przeglądarce (`xdg-open` przez `systemd-run --user --scope`) i oznacza go jako przeczytany; False, gdy raportu nie ma albo nie da się go otworzyć |
| `Days()` | `→ s` | JSON dób z raportem dla okna Tide: `{root, today, keepDays, latest, unread, days: [{day, generatedAt, projects, waiting, data}]}` od najnowszej; `today` to doba raportu według `dayStart`, `waiting` — pozycje „Czeka na mnie” ze wszystkich projektów (bieżąca doba — tylko aktualne), `data` false dla raportu sprzed `report.json` (wtedy `projects` to liczba stron projektów, `waiting` `null`) |
| `Day(s day)` | `→ s` | `report.json` doby z polami `ok`, `latest`, `unread`, `legacy` i `index`, z notkami „Skończone dziś” (`finished` przy projekcie) i — dla bieżącej doby — stanem na żywo: `stale` przy nieaktualnych pozycjach „Czeka na mnie”, `now` (co czeka teraz: `id`, `project`, `label` — nazwa z tablicy pracy, `title`, `text`, `reason`, `since`), `nowText`, przeliczone liczniki i teksty do schowka; raport sprzed `report.json` — `legacy: true` z nazwami stron projektów; zła albo nieistniejąca doba — `{ok: false, error: "missing"}` |
| `OpenDay(s day, s project)` | `→ b` | Strona doby w przeglądarce: `index.html` (pusty `project`) albo `<slug>.html` (tylko `[a-z0-9._-]`, istniejący plik, nie dowiązanie). Strona najnowszego raportu oznacza go jako przeczytany, starszego — nie zmienia kropki |
| `MarkRead(s day)` | `→ b` | Najnowszy raport obejrzany w oknie Tide — przeczytany jak po `Open()` (z `Changed`); False dla starszej doby albo gdy już był przeczytany |
| `Ready` | sygnał `(s path)` | Powstał nowy raport |
| `Changed` | sygnał | Nowy (także żywy) raport, nowa notka „Skończone dziś”, zmiana tego, co czeka, albo zmiana przeczytania — widżety pobierają wtedy `Status()`, okno Tide `Days()` i `Day()` |

```bash
qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Generate   # raport teraz
xdg-open "$(qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Latest)"
```

**Kiedy.** Doba raportu zaczyna się o `dayStart` (04:00) — praca po północy należy jeszcze do poprzedniego dnia. Raport doby powstaje raz: przy pierwszym logowaniu w tej dobie (start usługi; sprawdzenie po 2 min, żeby nie spowalniać logowania) albo o `at` (07:00), jeśli użytkownik jest już zalogowany. Zegar ścienny sprawdzamy co minutę, więc wybudzenie po uśpieniu też łapie zaległy raport. Logowanie o 05:30 daje raport od razu, o 01:00 — nic (doba ma już raport), a o 07:00 przychodzi następny. `--report-at GG:MM` nadpisuje `at`, `--no-report` albo `"enabled": false` wyłącza termin (`Generate()` działa dalej).

**Okres.** Od kotwicy poprzedniego raportu do teraz, najwyżej 7 dni; pierwszy raport — ostatnia doba. Kotwica raportu to jego godzina z terminu (doba + `at`, np. wt. 07:00), a gdy powstał wcześniej (logowanie o 05:30) — koniec jego okresu; nigdy później niż godzina z terminu. Dlatego `Generate()` wieczorem (np. o 23:54, jeszcze w tej samej dobie) nie przesuwa granicy następnego dnia: raport środy obejmuje wt. 07:00 → śr. 07:00, a nie pustą noc 23:54 → 07:00. Weekend i kilka dni przerwy są w całości — od kotwicy ostatniego raportu. `Generate()` w tej samej dobie zachowuje początek okresu, więc nie gubi niczego z porannego raportu. Początek okresu doby D nigdy nie wypada później niż godzina z terminu doby D-1 — tak naprawia się też stan zapisany przed tą zasadą (`report-state.json` z początkiem 27.10 23:54 dla doby 28.10: ponowne wygenerowanie obejmuje 27.10 07:00 → teraz). `generatedAt` w `Status()` to nadal prawdziwa chwila wygenerowania.

**Stan prac na dzień.** Raport mówi nie tylko, co nowego w okresie, ale też, co trwa teraz — także po cichej nocy. Projekt-repozytorium jest w raporcie, gdy ma w okresie commit albo sesję, **albo** pracę w toku: sesję, która według obszaru Agents pracuje albo czeka na ciebie (nawet bez wpisu w dzienniku w okresie — wtedy z samego stanu obszaru Agents), worktree z niescalonymi commitami zmieniony w ostatnich 7 dniach („zmiany czekają na scalenie”; najwyżej 5 najświeższych na projekt), plan zaktualizowany w ostatnich 7 dniach z ticketem w toku, w review albo czekającym (albo aktywny plan zaczęty i nieskończony) albo pracujący koordynator, kartę tablicy pracy w „W toku”, „Czeka na mnie” lub „Do sprawdzenia” (w projekcie głównym karty, gdy nie opisuje jej już inna praca z raportu; karty — bez limitu wieku, bo tablica to twoja lista). Porzucona gałąź albo plan sprzed tygodni nie wisi w „W toku” codziennie (`STANDING_MAX` w `reports/collect.py`). Taki projekt dostaje w „Na daily” linie *W toku* (i *Czeka na mnie*, gdy agent stoi teraz — karta w „Czeka na mnie” bez czekającego agenta to *W toku* z dopiskiem, decyzja 36). Liczba w nagłówku to „N projektów w raporcie”. Zdanie „W projektach nic się nie dzieje — nie ma nowej pracy ani pracy w toku.” pojawia się tylko wtedy, gdy naprawdę nic nie trwa. Żywy raport w ciągu dnia (niżej) składa to ponownie po zmianach w pracy.

**Powiadomienie.** Po raporcie z terminu, najwyżej jedno na dobę (doba w pliku stanu, więc restart usługi go nie powtórzy): „Raport poranny gotowy — 3 projekty”, w treści liczba commitów, sesji i czekających, przycisk „Otwórz” (także kliknięcie) → `xdg-open index.html` przez `systemd-run --user --scope`. `--no-notifications` je wyłącza.

**Przeczytany.** Raport doby jest nieprzeczytany, dopóki go nie otworzysz: link „Raport” w widżecie (`Open()`), „Otwórz” w powiadomieniu albo „raport” w KRunnerze — wszystkie trzy idą przez usługę i zapisują dobę w `opened` pliku stanu, a potem wysyłają `Changed`. Nowa doba to nowy, nieprzeczytany raport; `Generate()` w tej samej dobie nie zapala kropki ponownie. Okno Tide (widok „Raporty”) liczy się tak samo: najnowszy nieotwarty raport na wierzchu aktywnego okna przez 1,5 s → `MarkRead(doba)`, a „Otwórz w przeglądarce” i „Pełna strona” idą przez `OpenDay()`; przeglądanie starszych dób niczego nie gasi. „Otwórz” w starym powiadomieniu (raport sprzed nowszego) otwiera stary raport, ale nie gasi kropki przy nowym. Plik stanu sprzed tej zmiany (bez `opened`) traktujemy jako przeczytany, żeby aktualizacja nie zapalała kropki przy dawno obejrzanym raporcie. Widżety: nagłówek paska bocznego — „Raport · dziś 07:00” (albo „wczoraj”, „24.10”) z brzoskwiniową kropką obok „Tide”; popup agentów w docku — ten sam link w dolnym wierszu (nagłówek popupu nie ma wolnego miejsca, a popup ma stałą szerokość); kafelek w docku bez zmian. Bez raportu link znika. Decyzja 51.

**Powiadomienie po tickecie.** Notka „Skończone dziś” przychodzi z powiadomieniem „LogTool: skończone — <jedno zdanie>” (aplikacja „Tide”, ikona `view-calendar-day`), w treści 2–3 zdania notki i ewentualnie „Wymaga sprawdzenia ręcznego”; „Otwórz” (także kliknięcie) → okno Tide na widoku „Raporty” z dobą i projektem (`--view reports --select RRRR-MM-DD/<slug>`, drugie uruchomienie prosi działające okno o `Show`). Kilka ticketów jednego projektu w ciągu 5 min to jedno powiadomienie, które się aktualizuje (`replaces_id`: „LogTool: skończone 2 zadania” i zdania w punktach). Ticket raz opisany nie dostaje drugiej notki ani powiadomienia — także po restarcie usługi (plik notek); pierwsze uruchomienie tylko zapamiętuje to, co już jest gotowe. `--no-notifications` wyłącza powiadomienia (notki zostają).

**Retencja.** Po każdym raporcie znikają katalogi `RRRR-MM-DD` starsze niż `keepDays` (30) dób. Usuwamy tylko katalogi o takiej nazwie, nie dowiązania.

**Co jest w raporcie.**

- **Dziś w skrócie** (na górze indeksu i widoku w oknie Tide): 3–5 zdań o wszystkich projektach razem, które da się powiedzieć na daily — co skończone i dlaczego to ważne, co trwa i jak daleko. Numery ticketów tylko jako drobne odnośniki, bez żargonu („koordynator”, „podagent”, „sesja … pyta”) i ścieżek. Model pisze je w tym samym wywołaniu co „Na daily”; zapas z reguł: zdanie na projekt z pracą („LogTool — skończone: …; w toku: …”) i jedna linia „Same porządki: …”. To, co czeka na ciebie, dokłada widok na żywo („Teraz czeka na ciebie: …”), nie model.
- **Indeks — przegląd projektów**: karta na projekt-repozytorium z 1–2 zdaniami „co się działo” (`headline`), licznikami *zrobione* / *w toku* / *czeka* / *porządki*, postępem planów („TOM-20 · 8/13”), pozycjami „Czeka na mnie” i odnośnikiem „Na daily i szczegóły”. Projekty, w których coś czeka, są pierwsze i wyróżnione (brzoskwiniowa karta). Projekty z samymi porządkami (instrukcje agentów, tracker, konfiguracja) nie są kartami — jedna zwinięta linia „Porządki: QuotaApp, BO, dev-dashboard” z ich pozycjami po rozwinięciu (`choreProjects` w `report.json`). „Kopiuj wszystko jako tekst” kopiuje standup wszystkich projektów. Pod kartami zwinięta sekcja **„Poza projektami”**: sesje spoza repozytoriów git (np. w korzeniu przestrzeni roboczej albo w katalogu tymczasowym) — stan, tytuł, zadanie, wynik albo pytanie, „Wznów”; bez „Na daily” i bez własnych stron (rozwinięta, gdy któraś czeka).
- **Na daily** (na górze strony projektu): *Skończone dziś* (notki po tickecie, niżej), *Praca* — *Zrobione*, *W toku*, *Czeka na mnie*, *Uwaga* (tylko gdy jest), *Dalej* i „Na czym polega” — oraz zwinięte **„Porządki”** (tracker, plany i instrukcje agentów, konfiguracja, narzędzia bez zmian w produkcie), z przyciskiem „Kopiuj jako tekst” tylko dla tego projektu (porządki na końcu tekstu). Wszystko inne poniżej jest zwinięte w „Szczegóły”.
- Indeks (w „Szczegółach”): liczby (czeka na ciebie, projekty w raporcie, commity z liniami, sesje, przebiegi zaplanowanych zadań), lista „Czeka na ciebie” z odnośnikami do sesji (sesja spoza repozytoriów — do kotwicy w „Poza projektami”), zwinięta lista projektów bez ruchu z tym, co w nich zostało (worktrees do sprzątnięcia, zmiany), migawka Limitów AI (poziomy kolorami jak w widżecie) i miejsce na prognozę (`reports/limits.py: forecast` — na razie nic nie zwraca), odnośnik do poprzedniego raportu.
- Strona projektu: sesje agentów z okresu (czeka / pracuje / skończył / przerwana) z tytułem, gałęzią, worktree, czasem trwania, ostatnią aktywnością i **ostatnią wiadomością agenta** z dziennika (to ona mówi, co zrobił; wycinek do granicy akapitu, całość pod „Cała wiadomość”), przyciskiem „Wznów” (kopiuje `cd … && claude --resume …`, `codex resume …`, a dla sesji Codexa ze snapa `/snap/bin/codex resume …`); dla wątków Codexa z zaplanowanym zadaniem — nazwa i rytm zadania (`automation.toml`) oraz przebiegi z okresu z wynikami. Commity z okresu po gałęziach (gałąź główna, gałęzie z commitami poza główną, odłączone HEAD-y worktrees; każdy commit raz) z autorem, godziną, tematem, liczbą plików i linii oraz znacznikiem „agent: Claude/Codex”, gdy wskazuje na to stopka Co-Authored-By albo adres autora, gałąź/worktree agenta albo commit powstał w czasie sesji agenta w tym katalogu. Worktrees do sprzątnięcia, worktrees z pracą tylko tutaj (commity bez innej gałęzi i serwera, zmiany) i niezacommitowane zmiany w głównym katalogu.
- Projekt to repozytorium z katalogu projektów razem z worktrees (także tymi w `~/.codex/worktrees`). Własną stronę dostaje tylko repozytorium z commitem albo sesją w okresie albo z pracą w toku (wyżej, „Stan prac na dzień”); sesje spoza repozytoriów trafiają do „Poza projektami”. `Status()` i powiadomienie liczą tylko projekty-repozytoria.

**Czytanie dzienników.** Pliki zmienione od początku okresu; stan, tytuł i ostatnia wiadomość z ogona pliku (2 MB) przez te same parsery co obszar Agents, pierwszy wpis i pierwsze polecenie z początku (256 KB). Przebiegi zaplanowanych zadań: od miejsca w pliku, w którym zaczyna się okres (wyszukiwanie binarne po znacznikach czasu), JSON parsowany tylko w liniach z początkiem albo końcem tury. Stan sesji z ostatniej doby bierzemy z obszaru Agents; starsze — z dziennika (skończona tura to „skończył”). Worktrees z obszaru Worktrees (z rozmiarami), a przed jego pierwszym odczytem raport przegląda repozytoria sam.

**Strony.** Samodzielne pliki: CSS z palety (`palette.json` obok usługi, w repozytorium `theme/palette/mist.json`), Geist i Geist Mono z systemu (`@font-face` z `local()`, zapasowo czcionka systemowa), bez sieci i zewnętrznych plików, ciemny Mist, układ od wąskiego okna (jedna kolumna, 16 px marginesu) do 2560 px (dwie kolumny na stronie projektu). Jedyny skrypt to „Kopiuj”, a `Content-Security-Policy` dopuszcza tylko jego skrót SHA-256. Każdy tekst z gita i dzienników jest escapowany (także w atrybutach). Zamiast schematu `tide://` są ścieżki z „Kopiuj”, polecenia do skopiowania (Konsole, Rider, wznowienie sesji) i zwykły link `file://` do katalogu.

**KRunner.** „raport” (albo „rap…”, „raport poranny”) pokazuje najnowszy raport; Enter otwiera go w przeglądarce.

**Okno Tide.** Widok „Raporty” (u góry doby „Dziś w skrócie” z „Czeka teraz” — na żywo, jak widżet agentów; na kartach „Skończone dziś: …”; w panelu „Na daily” notki z ręcznym sprawdzeniem i nieaktualne pozycje „Czeka na mnie” przekreślone z dopiskiem „już nieaktualne”; projekty z samymi porządkami w jednej linii; odświeża się sam po `Changed`, „Wygeneruj teraz” zostaje) (`apps/reports`, `--view reports`, `--select RRRR-MM-DD[/slug]`) pokazuje raporty bez przeglądarki: doby z lewej, przegląd projektów i „Na daily” wybranego projektu z „Kopiuj jako tekst”. Nie czyta ani nie renderuje HTML-a: przy każdym raporcie `reports/data.py` zapisuje obok stron `report.json` — kolejność kart jak w indeksie (czekające pierwsze, same porządki na końcu), „Na daily” każdego projektu (`done`, `doing`, `waiting`, `next`, `chores`, `theme`, `headline`, liczniki), `since` („Od poniedziałku 07:00”), `period`, `source`, liczby, „Poza projektami” (stan, tytuł, zadanie, wynik albo pytanie) i gotowe teksty do schowka (`copyText` na projekt, `copyAll`) z tego samego generatora co przyciski na stronie (`render.daily_text`). Napisy w `report.json` są surowe — widok pokazuje je jako zwykły tekst. Okno czyta go przez `Days()` i `Day()` (usługa sprawdza nazwę doby i rozmiar pliku), a tagi projektów bierze z `/Board Snapshot()`, jeśli tablica działa. Plakietka „nowy” w pasku okna pochodzi ze `Status()`.

**Na daily** (`reports/standup.py`, `reports/summarize.py`).

- *Fakty* (`standup.facts`, tylko projekty-repozytoria): tickety z pliku tablicy koordynacji i planów w repozytoriach (ID, tytuł z planu albo YouTracka, początek opisu z YouTracka bez Markdownu i metryczek migracji, początek polecenia pierwszego podagenta, uwagi z planu, stan, notatka, wynik review w liczbach, pierwsza linia wyniku pracy, pytanie bez odpowiedzi) — skończone tylko z okresu, `todo` do „Dalej”; cel planu (akapit pod nagłówkiem „Cel…”/„Zakres”/„Kontekst”, inaczej pierwszy akapit); jednostki pracy: scalenie w gałęzi głównej (commity drugiego rodzica, `git rev-list P1..P2`), bezpośrednie commity gałęzi głównej pogrupowane po ID ticketu albo zakresie z `feat(zakres):`, gałąź scalona do gałęzi integracyjnej (wszystkie jej commity z okresu są w większej gałęzi poza główną — przy łańcuchu gałęzi wskazujemy największą), gałąź poza główną, sama sesja; przy jednostce tematy commitów, obszary zmian (katalog najwyższego poziomu z liczbą plików z `--numstat`, dla `src/`, `tests/`, `apps/` itp. dwa człony) i sesje z **pierwszym poleceniem użytkownika** (zadanie, do 1500 znaków z początku dziennika). Wewnętrzne gałęzie podagentów `worktree-agent-*` pomijamy (też w liście niescalonych). Sesja trafia do jednostki po gałęzi albo katalogu worktree, ticket do jednostki po gałęzi z tablicy albo ID w nazwie gałęzi (także `la035` przy znanym `LA-035`). Sesja koordynatora nie jest osobną pracą — jej polecenie idzie jako kontekst projektu.
- *Praca czy porządki* (`category`: `work` albo `chores`, podpowiedź z deterministycznych sygnałów; model może ją poprawić): commit to porządek, gdy ma typ `chore`/`ci`/`build`, `docs(plan|agents|tracker…)`, temat o trackerze (YouTrack, Jira, Linear, tracker, CLAUDE.md, AGENTS.md, MyAI, instrukcje/plan pracy agentów) albo zmienia wyłącznie `CLAUDE.md`, `AGENTS.md`, `docs/agents/**`, `.claude/**`, `.codex/**`, `.agents/**`, `.mcp.json` i pliki trackera; jednostka — gdy wszystkie jej commity to porządki, a bez commitów — gdy polecenie albo tytuł sesji mówi o trackerze lub instrukcjach; ticket — gdy tytuł albo plan mówi o trackerze lub cała jego praca to porządki. Pytanie do użytkownika i test, który nie przechodzi, zawsze idą do „Czeka na mnie”.
- *Opis z reguł* (`standup.rules`, zapas i `"summary": "rules"`): tickety po stanach (gotowe → Zrobione z wynikiem albo opisem, `w-toku`/`review` i review „do poprawy” → W toku z postępem z notatki, `czeka` → Czeka na mnie — tylko gdy sesja koordynatora jest w raporcie i stoi według obszaru Agents; bez koordynatora (plan, którego tablica nie zna) albo bez jego czekającej sesji → W toku „zostało pytanie bez odpowiedzi” (tak samo pytanie tablicy bez koordynatora), `todo` → Dalej, porządki → Porządki), potem jednostki bez ticketu: scalona gałąź (także do gałęzi integracyjnej) → Zrobione (pierwsze zdania wiadomości agenta albo „Zmiany są już w głównej wersji, m.in.: …”), gałąź niescalona → W toku („zmiany czekają na scalenie”, dalej: „przejrzeć i scalić”), więcej niż dwie takie gałęzie bez sesji — jedna pozycja zbiorcza („N rozpoczętych zmian czeka na przejrzenie”); porządki → Porządki; sesja, która czeka teraz → Czeka na mnie z jej `waitingText` („agent pyta: „…”” — także `reply`, pytanie na końcu tury, PT-29; „agent czeka na twoją zgodę (…)”); pytanie zostawione w tekście skończonej tury, która nie czeka (np. tura zaplanowanego zadania) → W toku („agent zostawił pytanie: …”, tylko bez odpowiedzi użytkownika); testy, które nie przechodzą (heurystyka w wiadomości agenta), i konflikty numerów → osobna mała sekcja **„Uwaga”** (`attention`, `kind: attention`) — nikt na nie nie czeka, więc nie są „Czeka na mnie” i nie liczą się do plakietek, `Status().waiting` ani powiadomień; sekcja jest w HTML (po „Czeka na mnie”), w `report.json` (`attention`, licznik `counts.attention`), w oknie Tide (tylko gdy niepusta) i w tekście do schowka („Uwaga:”), zawsze z reguł (model jej nie pisze); zaplanowane zadanie → liczba przebiegów i wynik ostatniego. Numer z nazwy gałęzi („tide-9 · export”) idzie do `ref`, nie do treści. Zdanie do karty w indeksie: „Zrobione: …; W toku: …”. Najwyżej 8 pozycji w grupie.
- *Czeka na mnie — jedna definicja* (PT-23): pozycje składają zawsze reguły z obszaru Agents (stan `waiting` i `waitingText`, pytanie koordynatora z pliku tablicy z `questionTs`), z `kind: blocked`, `sessionId` i `since` (testy i konflikty numerów są w „Uwaga”, nie tutaj). Model może tylko przeredagować pozycję z tym samym `ref`; jego pozostałe pozycje „Czeka na mnie” znikają, a fakty dla modelu nie zawierają ostatniego tekstu sesji, która czeka (`agentSaid`, `said`), ani pytania z notatki ticketu.
- *Opis modelem* (`"summary": "claude"`, domyślnie): jedno wywołanie na raport, w wątku roboczym usługi:
  `claude -p --model <summaryModel> --output-format json --no-session-persistence --safe-mode --tools "" --disallowedTools <wbudowane narzędzia, mcp__*> --strict-mcp-config --mcp-config '{"mcpServers":{}}' --permission-mode dontAsk --disable-slash-commands --system-prompt <instrukcje>`,
  fakty na stdin, katalog roboczy to pusty katalog tymczasowy, własna grupa procesów (po 180 s `SIGKILL` całej grupy). `--safe-mode` zamiast `--bare`, bo `--bare` czyta wyłącznie `ANTHROPIC_API_KEY` i pomija logowanie subskrypcją. Program: `$TIDE_CLAUDE_BIN`, `PATH`, `~/.local/bin/claude`, `~/.claude/local/claude` (usługa systemd ma krótki `PATH`).
- *Model lokalny* (`"summary": "local"`, PT-15): ten sam wybór modelu co dla kart (`summarize.Model` — jedno miejsce, które wybiera między `claude -p` a `models.chat`), te same fakty po `strip_secrets`, te same instrukcje i walidacja, a do tego schemat odpowiedzi wyprowadzony z przykładu na końcu instrukcji (`format` w Ollamie, `response_format` na serwerze `openai`). Model z roli `summary` w `models.json` (domyślnie `qwen3:8b` z kontekstem 8192), `summaryModel` jest pomijany. Jedno wywołanie **na projekt**, nie na raport: kontekst 8192 tokenów nie mieści instrukcji, faktów wszystkich projektów (w prawdziwym raporcie ok. 41 tys. znaków) i odpowiedzi naraz. Fakty projektu są przycinane jak dla Claude, tylko do mniejszego limitu z kontekstu roli (ok. 2,5 znaku na token, minus instrukcje i 2048 tokenów na odpowiedź — przy 8192 ok. 11 tys. znaków; przy większym kontekście, np. na Macu, do zwykłych 16 000). Wywołania idą przez kolejkę modeli (sekcja „Modele lokalne”, „Kolejka”) z niskim priorytetem — opisy kart wchodzą między projekty raportu, zamiast czekać na całość. Limit 180 s na wywołanie liczy się od jego startu (bez czekania w kolejce), a 600 s na cały raport — razem z czekaniem (projekty po terminie — reguły; gdy termin minie w kolejce: „Model lokalny niedostępny — opis bez modelu (kolejka: czekał N s — serwer zajęty: opisy kart, 127.0.0.1:11434)”). Rola z kontekstem za małym na instrukcje, odpowiedź i choć 1000 znaków faktów nie jest pytana wcale („kontekst roli „summary” za mały (2048 tokenów) — potrzeba co najmniej …”), bo Ollama obcięłaby instrukcje. W wątku roboczym raportu, jak wywołanie Claude; zamykanie usługi (`ReportMonitor.stop()`) przerywa trwające wywołanie od razu (`models.Cancel` zamyka gniazdo) i kolejnych projektów już nie pyta.
  **Gdy model lokalny zawiedzie** (serwer nie odpowiada, rola niedostępna, przekroczony czas, błąd serwera, zła odpowiedź), raport bierze reguły i w nagłówku sekcji (a też w `report.json` → widok „Raporty”) oraz w dzienniku usługi pokazuje komunikat `ModelError` po polsku: „Model lokalny niedostępny — opis bez modelu (serwer nie odpowiada, 127.0.0.1:11434)”, przy złej odpowiedzi „Model lokalny dał złą odpowiedź — opis bez modelu (…)”. Po błędzie serwera kolejnych projektów już nie pytamy; zła odpowiedź o jednym projekcie nie zatrzymuje pozostałych. Gdy część projektów ma opis modelu, nagłówek mówi „opis: model lokalny qwen3:8b · część projektów z reguł: … — Model lokalny niedostępny — …”. Nigdy nie przechodzimy do Claude — nie ma też ustawienia, które by to włączało (decyzja planu z 27.10). Stopka strony: „model lokalny qwen3:8b (127.0.0.1:11434) — nic nie wychodzi z tego komputera” albo przy serwerze w sieci „…idą tylko do tego serwera modeli”.
- *Wejście*: zwięzły JSON na projekt, najwyżej 16 000 znaków (coraz krótsze wycinki wiadomości, poleceń i opisów oraz mniej commitów, aż się zmieści) i 90 000 razem — projekty ponad limit dostają reguły. Tematy commitów w kolejności feat, fix, refactor, test, docs; porządki (`docs(plan)`, `chore`) pomijane. Notatka o review przy tickecie `gotowe` nie idzie (gotowe to koniec), liczby niescalonych gałęzi też nie. Przed wysłaniem z każdego napisu znika to, co wygląda na sekret: klucze `sk-…`, `ghp_…`, `github_pat_…`, `glpat-…`, `xox?-…`, `AKIA…`, `AIza…`, JWT, tokeny YouTracka `perm:…`, `Bearer …`, klucze prywatne PEM, hasła w adresach URL, przypisania `token=`/`password:`/`API_KEY=…`, linie w stylu `.env`, 40+ znaków szesnastkowych i długie losowe ciągi; katalog domowy → `~`. Instrukcje każą traktować napisy jako dane, nie polecenia.
- *Instrukcje*: raport dla ludzi, nie dla AI — tylko po polsku, bez żargonu („koordynator”, „podagent”, „sesja”, „worktree”, „commit”, „gałąź”, „merge”, „prompt”), bez ścieżek i nazw plików, numer ticketu tylko w `ref`; każda pozycja „Zrobione” mówi, CO powstało albo się zmieniło, językiem, który da się powiedzieć na daily komuś, kto nie zna kodu (nie „ticket 01 gotowy”, nie „kroki V3b1–V3b10 scalone”); `about` — po co, konkretnie, pusty, gdy fakty nic nie mówią; `brief` — 3–5 zdań o całym dniu. Najlepsze źródła treści to polecenie użytkownika, opis ticketu i cel planu, tematy feat/fix i obszary zmian. Zakaz wymyślania faktów zostaje. Wejście: `{"projects": [fakty projektów do opisania], "others": [{"name", "headline"}]}` — „others” to projekty opisane wcześniej (z pamięci), tylko do „Dziś w skrócie”.
- *Odpowiedź*: obiekt `{"brief": [zdania], "projects": [{"name", "headline", "theme", "done": [{"ref", "text", "about"}], "doing": [{"ref", "text", "about", "next"}], "next": [{"ref", "text"}], "chores": [{"ref", "text"}]}]}`. Czytelność (`standup.human_problem`): napis z żargonem, ścieżką albo nazwą pliku, po angielsku (heurystyka słów) albo z samych numerów („LA-035 gotowe”, „ticket 01 i ticket 02 …”, „kroki V3b1–V3b10”) jest odrzucany; ID ticketu na początku albo w nawiasie przechodzi do `ref` („LA-035: bazowa wersja…” → `ref` LA-035, „Bazowa wersja…”). Odrzucone `about`/`next` znikają, odrzucona pozycja liczy się w `rejected` — grupa, z której model stracił wszystko, dostaje pozycje z reguł; `headline` i `theme` z problemem zastępuje zdanie z pozycji. „Dziś w skrócie” z modelu przechodzi, gdy zostały co najmniej dwa dobre zdania (inaczej reguły). Walidacja (`standup.validate`): tylko projekty z wejścia, same napisy bez znaków sterujących, przycięte długości (tekst 260, `about` 320, akapit 600, `headline` 320), najwyżej 8 pozycji w grupie, `ref` tylko ze znanych ID (inna pisownia wraca do postaci z planu, wymyślone ID znika); pozycje-śmieci odrzucone („nic nie czeka”, „nie ma decyzji”, „N gałęzi nie jest scalonych”, „bez review”, „czeka na review”, `worktree-agent-*`), a pozycja Zrobione/W toku, która sama mówi o trackerze albo instrukcjach agentów, przechodzi do Porządków. Stare pole `why` czytamy jako `about`. Markdown w polu (PT-13: gpt-oss wkładał całe listy do jednego napisu) zamieniamy na zwykły tekst: pogrubienia, kod, nagłówki i odnośniki znikają, linie łączą się jak zdania, a lista punktów w polu `text` staje się osobnymi pozycjami z tym samym `ref` (`about`/`next` przy pierwszej); zwykłe jedno zdanie bez Markdownu zostaje bez zmian. Jedna linia to nie lista: „2. etap migracji gotowy” zostaje z numerem. Tylko w odpowiedzi modelu lokalnego (qwen3:8b robił te błędy na prawdziwych danych) dochodzą dwie poprawki: ta sama pozycja w kilku grupach zostaje tylko pierwszy raz (kolejność Zrobione, W toku, Czeka na mnie, Dalej, Porządki), a pozycja „Czeka na mnie” z ID ticketu w stanie `todo` przechodzi do „Dalej” (gotowego — znika), chyba że ticket ma w faktach pytanie albo czekającą sesję; „Dalej” z ID gotowego ticketu znika. Odpowiedź Claude tych poprawek nie dostaje. Projekt, którego model nie opisał, dostaje reguły; „Czeka na mnie” zawsze z reguł (wyżej) — także po poprawkach odpowiedzi modelu lokalnego. Każdy napis jest escapowany przy składaniu HTML-a (CSP i tak blokuje obce skrypty). Błąd na którymkolwiek etapie → reguły z notatką w nagłówku sekcji i w dzienniku usługi.
- *Pamięć per projekt*: `summary.json` trzyma odpowiedź modelu dla każdego projektu z kluczem SHA-256 jego zwięzłych faktów (z tożsamością modelu i instrukcjami; tożsamość to nazwa modelu Claude albo `local|punkt|adres|model|ctx`, więc przełączenie `claude` ↔ `local` albo zmiana modelu w `models.json` nie bierze cudzej odpowiedzi) i przechodzi do nowej wersji katalogu raportu. Ponowne złożenie w tej samej dobie (`Generate()`, żywy raport) woła model tylko z projektami, których fakty się zmieniły; niezmienione biorą opis z pamięci i idą jako „others”. Projekt, którego model nie opisał, zostaje przy regułach do zmiany faktów (bez ponownego pytania). Bez zmian w faktach — bez wywołania. Claude: jedno wywołanie ze zmienionymi projektami (plus „others” do „Dziś w skrócie”); model lokalny: jedno wywołanie na zmieniony projekt (budżet 600 s, przerwanie przy zamykaniu usługi jak wyżej), a „Dziś w skrócie” z reguł — mały model widzi jeden projekt naraz.
- *Koszt*: poranny raport — jedno wywołanie Sonneta (w prawdziwym raporcie z 9 projektami ok. 41 tys. znaków wejścia, odpowiedź w ok. 27 s; Haiku z decyzji 52 potrzebował ok. 75 s na mniejsze wejście). W ciągu dnia: najwyżej jedno żywe złożenie na 12 min, a w nim najwyżej jedno wywołanie — tylko ze zmienionymi projektami (zwykle 1–2, czyli kilka tysięcy znaków); przy ciągłej pracy to do 5 wywołań na godzinę, przy kilku skończonych ticketach dziennie — kilka do kilkunastu. Notka po tickecie: jedno małe wywołanie na paczkę ticketów z jednego odświeżenia tablicy (ok. 1–3 tys. znaków, limit 90 s). Wszystko liczy się do limitów Claude jak zwykła praca z Claude Code; dane idą do Anthropic na tych samych zasadach. `"summary": "rules"` — zero wywołań, notki i opis z reguł.

**Nazwy, karty i długości** (czytelność po PT-24 — raport „dla ludzi, nie dla AI, bez bełkotu”).

- *Nazwy projektów z tablicy pracy* (`reports/cards.py`, `collect.assign_labels`): raport dostaje migawkę tablicy (w usłudze `/Board Snapshot()` z wątku głównego, przy `--report` — tablica z `--board-dir`; `--no-board` albo błąd — bez niej) i nazywa projekt tak jak tablica: `repo` projektu → `name` („LegalApp” zamiast `agent-team`, „LogTool” zamiast `LogViewer`). Nazwa z tablicy jest na kartach indeksu i widoku, w nagłówku strony projektu, w „Dziś w skrócie” z reguł, w „Teraz czeka na ciebie”, w tekstach do schowka i na wejściu modelu (`"name"`; odpowiedź pod tą nazwą albo pod nazwą katalogu wraca do projektu — `standup.project_of`). Nazwa katalogu zostaje kluczem (pliki stron, notki, pamięć opisu) i małym, szarym dopiskiem obok nazwy z tablicy (`folder` w `report.json`), gdy się różnią. Repozytorium spoza tablicy — nazwa katalogu; dwa projekty raportu z tą samą nazwą z tablicy dostają w nawiasie katalog.
- *Karta zamiast nazwy gałęzi*: jednostka pracy należy do karty, gdy `branch` karty to jej gałąź, `session` karty to jedna z jej sesji (`claude:<id>` albo samo id), plan ticketu ma klucz `card:`, albo nazwa gałęzi ma token `k-12` — ten ostatni tylko w repozytorium projektu karty (głównego albo dodatkowego), żeby obca gałąź „k-1-…” nie udawała karty. Pozycja nazywa wtedy pracę numerem i tytułem karty: „W toku: K-1 Data ważności wiadomości — zmiany czekają na scalenie.” zamiast „k-1 · data waznosci wiadomosci”; kilka jednostek tej samej karty w projekcie to jedna pozycja. Bez karty tytuł z nazwy gałęzi jest zwykłym tekstem (`standup.humane_branch_title`: bez słowa typu „fix ·”, numer wielkimi literami, pierwsza litera wielka — polskich znaków z nazwy gałęzi nie da się odtworzyć). Model dostaje kartę w polu `card` jednostki.
- *Karta w kilku projektach* (PT-28, `projects` karty): opisana raz — w projekcie głównym karty (albo, gdy go nie ma w raporcie, w pierwszym projekcie z jej pracą) z dopiskiem „(pracuje też w: legacyinfra, mobileapp)”; w pozostałych projektach tylko wzmianka „Zmiany w ramach K-1 Data ważności wiadomości”, która nie dostaje własnego zdania w „Dziś w skrócie”. Model widzi to jako `cardAlsoIn` i `cardDescribedIn`.
- *Bez ucinania w pół zdania*: tekst dla ludzi nigdy nie kończy się „…ma jasne znaczenie w modelu i w…”. `standup.fit` bierze całe zdania od początku, póki mieszczą się w limicie; pierwsze zdanie dłuższe niż limit to pusty napis. Odpowiedź modelu: za długie zdanie „Dziś w skrócie” odpada (z dwóch zdań zostaje pierwsze), za długa pozycja jest odrzucona (grupa bez pozycji modelu bierze reguły), za długi `headline` — zdanie z pozycji, `theme`, `about` i `next` — całe zdania albo nic. Reguły: nagłówek i zdanie „Dziś w skrócie” tracą części (drugą zrobioną, potem „W toku”), a na końcu zostają liczby („Zrobione: 3 rzeczy, w toku: 1 rzecz.”); dopiski z wiadomości agenta, opisów i wyników ticketów to całe zdania; tytuł sesji ucięty przez indeks sesji składamy od nowa z pełnego polecenia; za długie pytanie w „Teraz czeka na ciebie” zastępuje „agent czeka na odpowiedź”. Limity to granice bezpieczeństwa (pozycja 360 znaków, dopisek 400, nagłówek 400, „Na czym polega” 600, zdanie „Dziś w skrócie” 320), a instrukcje modelu podają krótsze, twarde długości (zdanie skrótu do 200 znaków, nagłówek do 250, pozycja do 160, dopisek do 200) i mówią, że za długie zdanie jest odrzucane w całości.

**Plany i tickety** (`reports/plans.py`, dodatek — bez niego raport działa jak dotąd): na stronie projektu karta każdego koordynatora z okresu (z pliku tablicy `coordination.json`: tytuł, etykieta, pasek postępu, liczby po stanach, tickety od czekających z notatkami i odnośnikami do YouTracka) i każdego planu z `docs/agents/plans` (aktywnego albo zmienionego w okresie), którego tablica nie zna — wtedy stany z samego planu, z jego błędami. Karta projektu w indeksie dostaje znacznik „TOM-20 · 8/13”. Do „Na daily” plan daje cel i uwagi, a plan z `tracker: youtrack`, którego tablica nie zna, dostaje początki opisów ticketów z YouTracka (to samo zapytanie GET, serwer i token co tablica; ID przez mapę migracji, np. `LA-035` → `LEGAL-35`; `--no-youtrack` wyłącza). Wszystkie teksty escapowane jak reszta raportu.

### Żywy raport

Raport doby powstaje o 07:00 (albo przy pierwszym logowaniu), a potem odświeża się sam (`reports/live.py`, `report_monitor.py`):

- **Zdarzenia**: zmiana stanu ticketu na tablicy koordynacji (każda nowa wersja tablic z obszaru Koordynacja; pierwszy odczyt po starcie to stan wyjściowy) i agent w repozytorium, który przestał pracować (`working` → `done`/`idle`/`ended`). Zmiana tego, co czeka, nie składa raportu: Day/Days/Status przeliczają „czeka” przy każdym odczycie, a usługa wysyła tylko `Changed`.
- **Zbiorczo i tanio**: zdarzenie oznacza raport jako nieaktualny; składamy go najwcześniej 90 s po ostatnim zdarzeniu z serii (przy ciągłych zdarzeniach najpóźniej 12 min po pierwszym) i najwyżej raz na 12 min, w tym samym wątku co poranny raport, bez powiadomienia i bez zapalania kropki „nieprzeczytany” (ta sama doba). Przed porannym raportem doby nic się nie składa. Model — tylko zmienione projekty (pamięć per projekt, wyżej).
- **Tablica pracy to nie projekt**: repozytorium tablicy (znacznik `board.md` z `tide-board`, `board/marker.py`) nie jest projektem raportu — `collect.build` je pomija (sesje z jego katalogu są w „Poza projektami”), a notki „Skończone dziś” przypisane do niego (sprzed PT-28: koordynator, którego sesja weszła do katalogu dokumentów tablicy, np. DPT-1…3 karty K-1) nie robią karty „tide-board”. Przy każdej nowej wersji tablic koordynacji i przed złożeniem raportu `notes.owner_of` szuka właściwego projektu: tablica koordynacji tego koordynatora (id z id notki), a gdy ona też wskazuje tablicę albo jej nie ma — karta tablicy pracy związana z jego sesją albo planem → repozytorium projektu karty. Poprawka trafia raz do `report-notes.json`; notka bez rozstrzygnięcia jest ukryta (raport, widok, powiadomienia) do następnej próby.
- **Nazwy i karty na żywo**: każde złożenie bierze świeżą migawkę tablicy pracy, więc nowa karta albo zmieniona nazwa projektu jest w raporcie przy najbliższym odświeżeniu; projekt bez karty w porannym raporcie (tylko z notką „Skończone dziś”) ma nazwę katalogu do następnego złożenia.
- **Na żywo** (`data.apply_live`, bieżąca doba): pozycja „Czeka na mnie” z `sessionId`, której sesja już nie czeka albo czeka od później (na coś innego; 90 s zapasu), dostaje `stale` — widok ją przekreśla („już nieaktualne”), nie liczy się do plakietek (`Status().waiting`, `Days()`), znika z kart i tekstów do schowka. `now` to sesje, które czekają teraz (jak widżet agentów), `nowText` — zdanie „Teraz czeka na ciebie: …” do „Dziś w skrócie”. Starsze doby to migawki.

**Skończone dziś** (`reports/notes.py`): ticket na tablicy koordynacji przechodzi do `gotowe` (po review OK albo bez review) → notka w raporcie doby pod projektem: 2–3 zdania — co zrobiono, co to daje użytkownikowi i czy trzeba sprawdzić ręcznie. Ticket skończony bez review (notatka tablicy „bez review”, brak wyniku review) mówi to wprost: „Skończone bez przeglądu.” — w notce (z reguł i z modelu, który dostaje `reviewed: false`) i w powiadomieniu („(bez przeglądu)” przy zdaniu w zbiorczym). Ręczne sprawdzenie: `check: manual` (albo „test ręczny”) w uwagach wiersza planu, pole `check: manual` karty tablicy pracy (hak `card_check` — `ReportMonitor.card_check`, podpinany w `app.main` po starcie tablicy pracy jako `app.board_card_check`: funkcja `(board, ticket) → {"check": "manual", "spec": ścieżka} | None`, gdzie `board`/`ticket` to tablica koordynacji z `Board()` i jej ticket. Kartę znajduje `notes.card_for` w migawce `/Board Snapshot()`: najpierw klucz `card` planu, potem `session` karty równe koordynatorowi, potem `branch` karty równy gałęzi ticketu albo koordynatora; względna `spec` — od repozytorium. Hak jest wołany w wątku głównym, wątek notek dostaje gotowe wyniki; wynik czyta `notes.manual_check`) i punkty z sekcji „Co przetestować ręcznie” planu albo specyfikacji z karty (punkty z ID ticketu albo pod podnagłówkiem z ID; plik bez ID — wszystkie). Treść z modelu (to samo bezpieczne wywołanie, instrukcje `notes.NOTE_PROMPT`, walidacja czytelności) albo z reguł: opis karty („co zrobiono”) albo pierwsze zdanie wyniku pracy, pierwsze zdanie opisu ticketu, „Do sprawdzenia ręcznie: …”. Ticket musi zostać w `gotowe` 90 s, zanim dostanie notkę („gotowe bez review”, po którym zaraz rusza review, nie daje notki). Najwyżej jedna notka na ticket na dobę: ticket skończony ponownie (np. po review) zastępuje swoją notkę (po wcześniejszym „bez przeglądu” z dopiskiem „Teraz już po przeglądzie.”) i aktualizuje to samo powiadomienie (`replaces_id`), zamiast wysyłać drugie; powiadomienie idzie tylko dla notek naprawdę dodanych albo zmienionych, więc raport i powiadomienie mówią to samo. Ticket skończony, gdy usługa nie działała, dostaje notkę, jeśli jego ostatni ruch jest z bieżącej doby raportu. Notki są w pliku notek (nie w katalogu raportu), więc są widoczne od razu — `Day()` dokłada je do `report.json`, a następne złożenie raportu także do stron HTML i tekstów do schowka.

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

## Koordynacja

Sesja, która zleca pracę podagentom (koordynator), dostaje tablicę ticketów: okno **Tide Sesje**
(`apps/sessions`), postęp w widżecie agentów („TOM-20 · 8/13”), słowo „sesje” w KRunnerze i sekcję
„Plany i tickety” w raporcie porannym. Wszystko z dzienników na dysku i z planu w repozytorium —
usługa niczego nie zapisuje w repozytoriach i dziennikach, a koordynacja pyta YouTracka wyłącznie przez GET.
Stan zgłoszeń ticketów z planów `tracker: youtrack` zmienia tylko synchronizacja tablicy (PT-7, niżej
w „Tablica” — „Synchronizacja z YouTrackiem”), wyłącznie w projektach z kluczem YouTracka na tablicy.
Standard planu: [agent-plan-standard.md](agent-plan-standard.md), uzasadnienie: decyzja 50.

**Gdzie szukamy planów** (standard 1.1, `coordination/plan.py: plan_folders`, `coordination/boardplans.py`):
`docs/agents/plans/` worktree koordynatora i głównego katalogu, a dla projektu tablicy z `ai-docs: tide`,
którego `repo` to to repozytorium — także `<tablica>/docs/<projekt>/plans/` (tablica: `$TIDE_BOARD_DIR`,
inaczej `~/DevWorkspace/tide-board`; pliki projektów czytane ponownie tylko po zmianie). To samo czytają
raport poranny, kontrola numerów, `tools/check_plans.py` i `tools/next_id.py`, więc tickety takich
projektów są widoczne i numerowane razem z resztą repozytorium. Koordynator na gałęzi z ID karty
(`claude/k-12-…`, uruchomienie z tablicy) dostaje plan z `card: K-12` (także zakończony), a plany innych
kart odpadają — w katalogu projektu w tablicy leżą plany wielu kart naraz; bez karty w gałęzi wybór jak
dotąd (`session`, inaczej najświeższy aktywny plan tego narzędzia).

| Element | Wartość |
| --- | --- |
| Obiekt | `/Coordination` |
| Interfejs | `org.tide.Daemon.Coordination` |
| Plik dla raportu | `$XDG_STATE_HOME/tide/coordination.json` (0600) |
| Opisy kart (pamięć) | `$XDG_CACHE_HOME/tide/coordination-descriptions.json` (0600) |
| Ustawienia YouTracka | `$XDG_CONFIG_HOME/tide/youtrack.json` (opcjonalny; `$TIDE_YOUTRACK_CONFIG` — inny plik, testy usługi) |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Coordinators()` | `→ s` | Koordynatorzy z ostatnich 7 dni jako JSON (najpierw działający, potem od najświeższej aktywności) — pola tablicy bez ticketów i przebiegów |
| `Board(s id)` | `→ s` | Tablica koordynatora (pola niżej); `{}` dla nieznanego id |
| `RunDetail(s id, s run)` | `→ s` | Wynik podagenta („Pokaż wynik”): `description`, `label`, `model`, `role`, `rounds` (`kind`, `status`, `startTs`, `endTs`, `summary`, `durationMs`), `result` (raport), `excerpt` (ostatnie wypowiedzi i wywołania narzędzi z jego dziennika: `ts`, `kind` `text`/`tool`, `text`), `transcript` |
| `Activate(s id)` | `→ b` | Przejdź do koordynatora: okno działającej sesji (jak `ActivateSession`), inaczej wznowienie w nowym oknie Konsoli (`claude --resume` / `codex resume`) |
| `OpenDiff(s id, s ticket)` | `→ b` | `git diff --stat --patch <główna>...<gałąź ticketu>` w Konsoli w głównym katalogu repozytorium (gałąź musi istnieć lokalnie); bez Konsoli — worktree ticketu w Riderze |
| `OpenWorktree(s id, s ticket)` | `→ b` | Worktree z gałęzią ticketu w Konsoli (przycisk „Worktree”) |
| `ShowWindow(s id)` | `→ b` | Okno „Tide Sesje”, z wybranym koordynatorem (pusty id — bez wyboru); `false`, gdy okno nie jest zainstalowane |
| `Refresh(b force)` | `→` | Odświeżenie teraz; `force` pomija pamięć podręczną YouTracka |
| `Changed` | sygnał | Lista albo któraś tablica się zmieniła |

**Tablica** (`Board`): `id` (`claude:<sesja>` albo `codex:<wątek>`, jak w `/Agents`), `tool`, `sessionId`,
`project`, `repoPath` (główny katalog repozytorium), `cwd`, `branch`, `title` (z planu, inaczej tytuł
rozmowy), `sessionTitle`, `label` (ID z tytułu planu albo gałęzi, tylko ze znanym prefiksem ticketów —
„TOM-20”), `model` („Opus”), `startedTs`, `lastActivityTs`, `state` i `reason` (stan sesji z `/Agents`,
`ended`, gdy jej tam nie ma), `live`, `canActivate`, `question` (ostatnie pytanie bez odpowiedzi: `text`, `ts`,
`kind` `question`; gdy dziennik nie ma pytania, a obszar Agents mówi, że koordynator czeka — np. na zgodę —
jego `waitingText` z `kind` `approval`, tak że tablica mówi to samo co widżet agentów; pytanie bez ID ticketu
zostaje tutaj i nie trafia do żadnego ticketu),
`plan` (`path`, `title`, `tracker`, `trackerUrl`, `trackerProject`, `prefix`, `coordinator`, `session`,
`status`, `updated`, `updatedTs`, `errors`, `goal`, `card`) albo `null`, `card` (karta tablicy z planu —
`K-12` albo pusty; też w `Coordinators()`, dla PT-5), `tracker` (`kind`, `url`, `project`, `available`,
`error`, `fetchedTs`, `checkedTs`), `counts` (po stanach, `total` bez odrzuconych, `done`), `progress`
(`done`, `total`, `text`: „TOM-20 · 8/13”), `spawned`, `running`, `columns`, `tickets`, `runs`,
`warnings` (kontrola numerów ticketów w repozytorium, niżej; też w `Coordinators()`).

**Ticket:** `key` (klucz porównania: `TOM-140`, `#22` albo `~opis` dla pracy bez ID), `id` (do pokazania,
z planu albo pierwszy zapis), `title` (czym jest zadanie, po polsku — opis karty), `done` (co faktycznie
zrobiono), `now` (co teraz / czego brakuje, review słowami), `describedBy` (`model`, `model-stale` —
tytuł i „zrobione” z poprzedniego opisu modelu, „teraz” z reguł — albo `rules`), `describeNote` (tylko
w trybie `local` po nieudanym wywołaniu: „Model lokalny niedostępny — opis bez modelu (…)”; okno Sesje
pokazuje go w szczegółach ticketu zamiast „opis z reguł”), `officialTitle` i
`officialFrom` (`plan`, `tracker`, `backlog`), `officialNote` (linia „Status:” z backlogu), `taskTitle`
(z opisu zlecenia, np. „Implement ticket 05 value chain”), `state` (`todo`, `w-toku`, `czeka`, `review`, `gotowe`, `odrzucone`),
`column`, `word`, `source` (`runs`, `plan`, `tracker`), `derived` (stan z samej pracy podagentów),
`planState`, `planNote`, `planAgent`, `inPlan`, `trackerState`, `url` (YouTrack), `branch`, `worktree`,
`note` („review OK”, „gotowe, czeka na review”, „rozpoznanie skończone”, „po review: 1 MAJOR — do poprawy”,
„czeka na ciebie: „…””), `review` (`blocker`, `major`, `nit`, `ok`, `verdict`, `top`, `text`) albo `null`,
`runs`, `agents` (ostatnie trzy rundy na kartę: `label`, `role`, `word` — rola słowem: implementacja,
poprawki, review, ponowne review, rozpoznanie —, `state`, `startTs`, `endTs`, `note` „poprawki”), `running`,
`question`, `lastActivityTs`, `events` (oś czasu po polsku: `ts`, `kind` `launch`/`continue`/`finished`/
`running`/`question`, `state`, `title` („Koordynator zlecił implementację”, „Implementacja skończona”,
„Review: 1 poważna uwaga i 2 drobne”), `text` (początek raportu — dwa zdania, do 300 znaków — albo „do
poprawy”), `meta` (drobny dopisek przy zleceniu: „„Implement ticket 05 value chain” · implementer · Opus ·
tło”), `word`, `durationS`, `quote` — pierwsze BLOCKER/MAJOR z review, `review`).

**Przebieg** (`runs`): `id` (agentId Claude Code albo id wątku Codexa), `ticket`, `role` (`implementer`,
`reviewer`, `helper`), `agentType`, `label`, `description`, `nickname`, `model`, `background`, `state`
(`running`, `completed`, `failed`, `killed`, `interrupted`), `startTs`, `endTs`, `rounds`, `lastAction`,
`branch`, `summary`, `hasResult`, `review`.

**Skąd koordynatorzy** (`coordination/claude.py`, `codex.py`, tylko odczyt):

- Claude Code: `<claude>/projects/<katalog>/<sesja>.jsonl` zmieniony w ostatnich 7 dniach
  (`--coordination-days`) z katalogiem `<sesja>/subagents/`. Zlecenie to blok `tool_use` `Agent`/`Task`
  (`description`, `subagent_type`, `prompt`, `model`, `run_in_background`); wynik pierwszego planu
  przychodzi w `tool_result`, a praca w tle kończy się blokiem `<task-notification>` (`task-id` =
  agentId, `status`, `summary`, `result`) w załączniku `queued_command` albo wiadomości użytkownika —
  kopie w `queue-operation` i `api-request-blob` są pomijane. Raport oddany przez SubagentHandback
  przychodzi osobną wiadomością z `origin.kind = "peer"` (`handbackReportText`). `SendMessage`
  z `resumedAgentId` zaczyna kolejną rundę podagenta, `TaskStop` go zatrzymuje, `AskUserQuestion` bez
  wyniku to pytanie koordynatora. `subagents/agent-<id>.meta.json` uzupełnia typ, opis i model, a dziennik
  podagenta — gałąź, ostatnią czynność i „Pokaż wynik”.
- Codex: wątek główny (bez `source.subagent`) z co najmniej jednym podagentem `thread_spawn` — także
  wnuki należą do niego; recenzent zgód (`subagent.other = "guardian"`) to szum. Przebieg to wątek
  podagenta (`agent_path` → nazwa zadania, `agent_nickname`, `agent_role`), runda to jego tura
  (`task_started` → `task_complete` z `last_agent_message`), polecenie z `spawn_agent` w rodzicu,
  pytanie to `request_user_input` bez wyniku.
- Pierwszy odczyt czyta cały dziennik (prawdziwy koordynator ma 50–100 MB, ok. 0,8 s), JSON tylko
  w liniach, które mogą coś zmienić; potem wyłącznie dopisane bajty. W pamięci zostają opisy, początek
  polecenia (2,4 KB — do szukania ID), raporty podagentów (do 24 KB) i pytania bez odpowiedzi; nic z tego
  nie trafia do dziennika usługi ani do pliku dla raportu.

**Tickety** (`coordination/ids.py`, `board.py`): ID z opisu podagenta, potem z jego gałęzi (gdy inna
niż gałąź koordynatora), przydomka, a z początku polecenia tylko z prefiksem z planu (polecenie cytuje
decyzje, niezmienniki i wymagania — „ADR-0016”, „INV-001” — które ticketami nie są); prefiks z planu
(`ticket-prefix`, także `tom142`, `la_029`), bez planu — najczęstszy w opisach i nazwach zadań
(`[A-Z]{2,6}-\d+`, bez skrótów w rodzaju UTF-8, ADR, INV, REQ, NFR, AC) albo `#22`. ID samego
koordynatora (TOM-20 z gałęzi RC) nie jest ticketem, chyba że jest w planie.
Mapa migracji z repozytorium (`docs/agents/youtrack-migration/*.csv`) zamienia stare klucze na nowe
(TOM-10 → LOG-128, LA-001 → LEGAL-1). Praca bez ID grupuje się po opisie bez słów roli, a review dołącza
do wcześniejszego zadania, którego słowa zawiera („Review anon key sync change” → „Implement anon key
sync tool”).

**Stan ticketu:** z pracy podagentów — pytanie koordynatora z ID ticketu → `czeka`; działa recenzent →
`review`; działa inny podagent → `w-toku`; ostatnie review bez BLOCKER/MAJOR i bez prośby o zmiany →
`gotowe`; z nimi → `w-toku` („do poprawy”); poprawki skończone po review → `gotowe` („bez ponownego
review”); implementer skończył bez review → `review` („gotowe, czeka na review”), gdy koordynator działa
i zleca review innym ticketom — chyba że od końca pracy minęły ponad 2 h, a koordynator w tym czasie
zlecał review innym (review pominięte → `gotowe`, „bez review”); samo rozpoznanie (Explore, explorer)
→ `gotowe` („rozpoznanie skończone”); błąd, zatrzymanie albo przerwanie (koordynator nie działa,
a dziennik podagenta stoi od 2 min) → `w-toku` z powodem. Plan wygrywa przy `odrzucone`, przy ticketach
bez pracy podagentów i gdy jego `updated` jest nowszy niż ostatnie zdarzenie ticketu. YouTrack daje tytuł
(gdy plan go nie ma) i adres; jego stan liczy się bez planu i pracy albo gdy ticket jest tam zamknięty
(Fixed, Done, Verified… → `gotowe`; Won't fix, Duplicate, Obsolete… → `odrzucone`) i nikt nad nim nie pracuje.
Lokalny backlog `docs/agents/backlog/<ID>.md` (nagłówek „# LA-040: tytuł”, linia „**Status:** …”) daje
tytuł, gdy nie ma go plan ani YouTrack. Koordynator, którego katalogu już nie ma (usunięty worktree),
szuka repozytorium (plan, mapa migracji, backlog) w katalogach swoich podagentów. Wątek Codexa bez nazwy
ma tytuł z pierwszej wiadomości użytkownika.

**Opis karty** (`coordination/describe.py`, decyzja 54): `title`, `done`, `now` po polsku. Model — to samo
wywołanie co „Na daily” (`summarize.Model`; `claude -p` bez narzędzi, `--safe-mode`, sekcja „Na daily” niżej),
z własnymi instrukcjami i schematem odpowiedzi, tryb z `report.json` (`summary`, `--report-summary`
ma pierwszeństwo): `claude` — model `summaryModel`; `local` — model lokalny z roli `describe` w `models.json`
(nigdy Claude), najwyżej 8 ticketów w wywołaniu i tyle faktów, ile mieści kontekst roli (reszta w następnym
wywołaniu), przez kolejkę modeli z wysokim priorytetem (przed raportem; sekcja „Modele lokalne”), 90 s
na wywołanie liczone od jego startu, do 5 min czekania w kolejce (potem reguły z uczciwym powodem: „Model
lokalny niedostępny — opis bez modelu (kolejka: czekał 300 s — serwer zajęty: raport poranny, …)”),
a zamykanie usługi (`Describer.stop()`) przerywa trwające wywołanie i zdejmuje czekające z kolejki od
razu; `rules` — same reguły. Gdy model lokalny zawiedzie, karty koordynatora
mają reguły i `describeNote` z komunikatem `ModelError` („Model lokalny niedostępny — opis bez modelu
(serwer nie odpowiada, 127.0.0.1:11434)”), do dziennika usługi trafia ten sam tekst (raz, nie przy
każdej próbie), a po 15 min wywołanie się powtarza; udane wywołanie zdejmuje informację. Skrót faktów
zawiera tożsamość modelu (jak w „Na daily”). Poprzedni opis jako `model-stale` pokazujemy w obrębie Claude
także po zmianie `summaryModel` (jak przed PT-15), a modelu lokalnego — tylko z tego samego punktu, adresu,
modelu i kontekstu; po przełączeniu `claude` ↔ `local` karty nie pokazują opisu drugiego. Wejście: `{"coordinator": {"project", "title"}, "tickets": [{"key",
"id", "state", "status" (stan z reguł), "officialTitle", "trackerStatus", "trackerDescription", "brief"
(do 400 znaków), "planNote", "tasks" (opisy zleceń), "reports" ([{"role", "said"}], do 300 znaków, ostatnie
6), "review", "question"}]}` po `strip_secrets`; odpowiedź `{"tickets": [{"key", "title", "done", "now"}]}`
walidowana (tylko znane klucze, same napisy, bez Markdownu, tytuł 110, pozostałe 260 znaków). Kiedy:
w osobnym wątku roboczym, jedno wywołanie naraz, wsadowo per koordynator (do 20 ticketów), tylko dla
ticketów, których fakty (skrót SHA-256 z modelem i wersją instrukcji) nie mają opisu i od 2 min się nie
zmieniają; najwyżej raz na 5 min na koordynatora, po błędzie 15 min przerwy, koordynatorzy bez ruchu od
dwóch dób — same reguły. Ticket, którego model nie opisał, dostaje reguły (bez ponawiania). Zapas
z reguł: tytuł z planu/YouTracka/backlogu, inaczej pierwsze polskie zdanie polecenia bez formułek
(„Jesteś wykonawcą…”, „Rola: …”, „Model: …”, „Implementujesz zadanie 05 funkcji „X” …: reszta” → „X:
reszta”), inaczej opis zlecenia; `done` — początek ostatniego raportu implementacji; `now` — stan, review
słowami, pytanie, zdanie o testach, które nie przechodzą. `--print-coordination` pokazuje opisy z pamięci
albo reguł, nigdy nie woła modelu.

**Review** (`coordination/review.py`): recenzent to podagent z „review” w typie albo opisie (ale nie
„review fix round”). Z raportu: jawne liczby („1 MAJOR, 2 NIT”, „BLOCKER: brak”, „nie ma BLOCKER-ów ani
MAJOR-ów”), oznaczone znaleziska (`MAJOR-1`, `NIT-2`, `MAJOR B`), linie i punkty pod nagłówkami poziomów;
zdania o zamkniętych znaleziskach z poprzedniej rundy się nie liczą. Werdykt z linii „Werdykt/Verdict”
(`REQUEST CHANGES`, `CHANGES REQUIRED` albo `APPROVE`, `ACCEPT`). To heurystyka sprawdzona na kształtach
prawdziwych raportów; stan zawsze da się nadpisać planem.

**YouTrack** (`coordination/youtrack.py`): tylko dla planu z `tracker: youtrack`. Koordynacja robi
wyłącznie GET (zapis stanu — tylko synchronizacja tablicy, PT-7). Jedno zapytanie na projekt: `GET {url}/api/issues?query=project: {klucz}&fields=idReadable,summary,description,customFields(name,value(name))`
(z opisu trzymamy 1200 znaków; tablica pokazuje 600, raport poranny bierze 400)
z nagłówkiem `Authorization: Bearer <token>`, bez pośrednika, z limitem 4 s, w wątku roboczym. Wynik
5 min w pamięci; przy błędzie zostają ostatnie dobre dane, a następna próba też dopiero po 5 min (serwer
jest w sieci lokalnej — gdy go nie ma, tablica działa bez tytułów z YouTracka, z komunikatem w podpowiedzi
przycisku). Token (PT-7: plik ustawień jest opcjonalny), po kolei pierwsze miejsce, które go ma: `tokenFile`
z `youtrack.json` (`tokenVar` pusty: cały plik, podany: zmienna z pliku `.env`), `~/.config/youtrack/token`
(token serwera MCP YouTracka), `~/.config/logviewer/youtrack-mcp-token` (dawne miejsce LogViewera);
bez tokenu komunikat podaje sprawdzone ścieżki (nigdy treść). Czytany przy każdym zapytaniu, tylko w pamięci — nie trafia do dziennika, błędów, odpowiedzi
D-Bus ani plików. Token idzie wyłącznie do serwera z ustawień (`url`, domyślnie
`http://10.20.1.159:8111`); plan z innym `tracker-url` dostaje komunikat zamiast zapytania.
`--no-youtrack` wyłącza zapytania.

```json
{"url": "http://10.20.1.159:8111", "tokenFile": "~/.config/youtrack/token", "tokenVar": ""}
```

Ten sam plik niesie ustawienia synchronizacji tablicy (opisane w „Tablica” — „Synchronizacja z YouTrackiem”):
`sync`, `states`, `stateField`, `projects`, `importQueries`, `importCheckMinutes`.

**Zapis dla przepływu karty** (PT-72, część 1; klient bez podłączenia do zatwierdzania planu — to część 2). Trzy operacje
w `coordination/youtrack.py`, z tym samym tokenem, bez pośrednika i z limitem 4 s co reszta:
`create_issue` (`POST /api/issues`), `link_issues` (`POST /api/commands`, np. `relates to DEMO-1`) i `add_comment`
(`POST /api/issues/{id}/comments`). Założenie jest idempotentne: opis kończy znacznik `[//]: # (tide-ref card=<karta> n=<numer>)`
(linia definicji odnośnika Markdowna — niewidoczna po wyrenderowaniu, odporna na przepisanie opisu, bo to osobna linia
szukana w całym opisie z tolerancją odstępów); przed założeniem klient szuka zgłoszenia projektu z tym znacznikiem
(zapytanie pełnotekstowe plus dokładne sprawdzenie karty i numeru po swojej stronie) i zwraca istniejące
(`created: false`). Komentarz z notką wyniku niesie `[//]: # (tide-note card=… ticket=… event=…)` i przed dodaniem
sprawdza komentarze zgłoszenia — dokładnie jeden na (karta, ticket, zdarzenie). Błąd ma `transient` (serwer
nieosiągalny, 5xx, 401/403/408/429 — ponów) albo `permanent` (400/404/422 z walidacji, zła wartość sprawdzona przed
wysłaniem — nie ponawiaj). Nazwy karty, ticketu i zdarzenia w znaczniku muszą pasować do `[A-Za-z0-9._-]`.

**Kontrola numerów ticketów** (`coordination/numbering.py`, decyzja 56; reguły: standard planu, „Numery
ticketów: jedno źródło”). Plany ze wszystkich miejsc repozytorium (`coordination/repoplans.py`): główny
katalog i każdy worktree (`git worktree list`, także niezacommitowane pliki) oraz każda gałąź lokalna
(`git for-each-ref`, `ls-tree` tylko dla nowych commitów, `cat-file --batch` tylko dla nowych blobów;
wynik repozytorium 60 s w pamięci), deklaracja z CLAUDE.md/AGENTS.md (sekcja „Plan pracy agentów (Tide)”:
prefiks, tracker, projekt), rezerwacje numerów i — gdy repozytorium ma plany lokalne — klucze projektów
YouTracka (`GET /api/admin/projects?fields=shortName`, raz na godzinę, po błędzie po 10 min; bez YouTracka
ten punkt jest pominięty). Ostrzeżenie: `kind`, `severity` (`hard`, `soft`), `ticket`, `prefix`, `plans`
(nazwy planów), `text` (po polsku, okno pokazuje je jako zwykły tekst). Rodzaje: `ticket-title` — ten sam
klucz z różnymi tytułami w dwóch różnych planach, także zakończonych (kopie jednego planu w kilku worktree
i gałęziach, po nazwie pliku albo tytule planu, to jeden plan; para planów `youtrack` się nie liczy);
`local-prefix-tracker` — prefiks lokalny jest kluczem projektu w YouTracku; `prefix-alias` — plan
`youtrack` z `ticket-prefix` ≠ `tracker-project` bez mapy migracji; `reserved-elsewhere` — numer
zarezerwowany dla innego tytułu; `numbering-restart` (łagodne) — niezakończony plan lokalny, którego nowe
numery zaczynają się nie wyżej niż największy numer wcześniejszych planów. Okno pokazuje pasek nad
kolumnami (pastel `butter`), raport poranny — pozycję „Konflikt numerów: …” w „Uwaga” projektu
(zawsze z reguł, także przy opisie modelem). Z wiersza poleceń: `make check-plans` (`tools/check_plans.py
[workspace]`, kod wyjścia 1 przy kolizji twardej).

**Rezerwacja numerów** (`coordination/reservations.py`, `make next-id REPO=… [PREFIX=] [COUNT=] [TITLE=]
[DRY_RUN=1]`, `tools/next_id.py`): pod `flock` na `$XDG_STATE_HOME/tide/ticket-ids.json` (0600)
największy numer z rejestru (główny katalog repozytorium + prefiks) i ze wszystkich planów, kolejne N
numerów zapisane z tytułem i czasem. Pusty rejestr zasiewa się z planów; prefiks z YouTracka — odmowa
(„numer nadaje YouTrack — załóż ticket tam”); `--dry-run` niczego nie zapisuje.

**Odświeżanie** (`coordination_monitor.py`): pierwszy odczyt 1,5 s po starcie, potem co 20 s, po zmianie
w `/Agents` (najwyżej co 4 s) i na żądanie. Praca w jednym wątku roboczym, D-Bus odpowiada z gotowych
danych. Po zmianie: sygnał `Changed`, pole `coordination` przy sesji koordynatora w `/Agents`
(`label`, `done`, `total`, `text`, `working`, `waiting`, `running`) i plik dla raportu porannego
(koordynatorzy z celem planu, tickety ze stanem, tytułem, notatką, adresem i gałęzią, wynikiem review
w liczbach, pierwszą linią wyniku pracy do 220 znaków, pytaniem bez odpowiedzi do 300, uwagami z planu
do 300, początkiem opisu z YouTracka do 400 i początkiem polecenia pierwszego podagenta do 300 —
zaszyfrowane polecenia Codexa pomijamy —, opisem karty `done` i `now` do 300 znaków — z nich „Na daily”;
bez pełnych poleceń i raportów podagentów). Opisy kart dokłada ten sam wątek z pamięci albo reguł, a gdy
trzeba zapytać model (decyzja 54), robi to osobny wątek roboczy i po odpowiedzi zleca odświeżenie.

**Widok „Sesje” okna Tide** (`apps/sessions`, okno `apps/tide`, `make install-tide-window`): `ShowWindow()`
uruchamia `/usr/bin/python3 -s $XDG_DATA_HOME/tide/tide-window/tide.py --view sessions [--select id]` (albo
`$TIDE_WINDOW`) przez `systemd-run --user --scope`; bez okna Tide albo przy `$TIDE_SESSIONS_WINDOW` — dawny
launcher `tide/sessions-window/tide-sessions.py [--select id]`. Okno rejestruje `org.tide.Window` (obiekt
`/Window`, `Show(s widok, s element) → b`), więc kolejne uruchomienie tylko je pokazuje na widoku Sesje
z wybranym koordynatorem; dawne `org.tide.SessionsWindow` `Raise(s id)` też działa.
Karta: prawdziwe ID, tytuł po ludzku, linia „co zrobiono” albo „co teraz”, działający podagenci z rolą
po polsku i czasem, stan słowami; szczegóły: „Co to jest”, „Co zrobiono”, „Teraz / review” i oś czasu.
Teksty z danych zawsze jako zwykły tekst (`Text.PlainText`).

```bash
qdbus6 org.tide.Daemon /Coordination org.tide.Daemon.Coordination.Coordinators
/usr/bin/python3 daemon/tide-daemon.py --print-coordination   # dane okna jako JSON, tylko odczyt
make preview-sessions-window SANITIZE=1                        # zrzut okna bez treści pracy: build/sessions-window.png
```

## Tablica

Osobisty backlog ze wszystkich projektów: karta to „rzecz do zrobienia” (funkcja, poprawka, pomysł),
nie ticket. Dane są w lokalnym repozytorium git, które usługa zakłada przy pierwszym poprawnym
zapisie i commituje po każdej zmianie, więc historia i cofanie są za darmo. Bez remote — usługa nigdy nie
wypycha ani nie dodaje zdalnego repozytorium. Widok Tablica w oknie Tide (`apps/board`) i kafelek
`org.tide.board` (`widgets/board`) czytają `Snapshot()`, kafelek Praca `org.tide.work` (`widgets/work`, PT-40) —
`Work()`; wszyscy piszą wyłącznie przez metody niżej;
kolumny W toku, Czeka na mnie i Do sprawdzenia wynikają z prawdziwej pracy agentów (niżej, „Stan karty
z pracy agentów”, PT-5), uruchamianie agenta z karty (PT-4) opisuje „Uruchomienie z karty” niżej, a stan
zgłoszeń YouTracka i import zgłoszeń jako kart — „Synchronizacja z YouTrackiem” (PT-7). Plan: `docs/agents/plans/2026-10-27-board-and-docker.md`.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Board` |
| Interfejs | `org.tide.Daemon.Board` |
| Repozytorium | `--board-dir`, inaczej `$TIDE_BOARD_DIR`, inaczej `~/DevWorkspace/tide-board` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Snapshot()` | `→ s` | JSON całej tablicy (opis niżej); tylko odczyt — przed pierwszym zapisem niczego nie zakłada |
| `Work()` | `→ s` | lista „Praca” (PT-40, niżej „Lista Praca”): `{exists, items, counts}` — aktywne karty i żywe sesje agentów bez karty, z tego samego przeliczenia co kolumny kart; tylko odczyt |
| `CreateCard(s request)` | `→ s` | nowa karta z JSON `{title, description, project \| repo, projects, tags, column, note, youtrack, spec, session, branch, check}` → `{ok, id, commitError}` |
| `UpdateCard(s id, s request)` | `→ s` | zmiana podanych pól (te same co wyżej bez `column`) → `{ok, id, changed, commitError}` |
| `MoveCard(s id, s column, i order)` | `→ s` | ręczna kolumna (`backlog`, `todo`, `done`, `archived`) i pozycja w niej od 0; `-1` albo poza zakresem — na koniec → `{ok, id, changed, commitError}`. Każde wywołanie to jawne przeniesienie: stempluje `moved` także w tej samej kolumnie i na tym samym miejscu (wtedy `changed: true` i commit „K-12: move to todo”) — karta pokazana w Do sprawdzenia ma zwykle zapisane `todo`, a „do poprawy” do Do zrobienia musi być widać |
| `UpsertProject(s request)` | `→ s` | projekt `{slug, name, repo, tags, aiDocs, tool, entry, mode, description, youtrack, youtrackSync, assignmentRules}` albo oddzielny zapis wspólnych zasad `{globalAssignmentRules}` (`entry`: `planning` albo `spec`; sam `mode: full|light` bez `entry` znaczy Planowanie, a `mode: spec` — wejście `spec`, tak samo w UpdateCard, CreateCard, RenderLaunch i RenderFlow): istniejący (po `slug`, `repo` albo tej samej nazwie) — zmiana podanych pól, inaczej nowy → `{ok, slug, changed, commitError}` |
| `UpsertTag(s request)` | `→ s` | tag `{name, color, description, newName}`; `newName` zmienia nazwę także w kartach i projektach (również tagu bez pliku — wtedy powstaje jego plik) → `{ok, name, changed, commitError}` |
| `RenderFlow(s id, s mode)` | `→ s` | polecenie dla karty (pusty `mode` — domyślne wejście i tryb projektu; `spec` to dawny zapis wejścia Tylko specyfikacja) → `{ok, text, entry, mode, tool, branch, check, docsDir, aiDocs, repo, repoMissing, project, projectName, specFile, template}`; katalog `docsDir` w tablicy zakłada od razu |
| `RenderLaunch(s id, s request)` | `→ s` | to samo z wyborem z okna „Co ma zrobić agent?”: `{entry, mode, tool, branch, check, preferredProvider}` (wszystko opcjonalne; `entry`: `planning` albo `spec` — Tylko specyfikacja, domyślne wejście projektu; `mode`: `full` albo `light`, dotyczy tylko Planowania i przy `spec` jest pomijany; `mode: spec` bez `entry` to stare żądanie wejścia Tylko specyfikacja; `check`: `auto` — domyślnie, albo `manual`); polecenie z każdego wejścia i trybu jest takie jak przed PT-77; odpowiedź ma `entry` i `mode` (pusty przy `spec`); odpowiedź ma też `projects` (dodatkowe projekty), `extraRepos` (ich istniejące repozytoria) i `addDirs` (katalogi dla `/Tasks Start`: dokumenty w tablicy przy `ai-docs: tide` i `extraRepos`) |
| `Badge()` | `→ s` | plakietka widoku Tablica w oknie Tide: JSON `{text, tone}` — „● N” (`waiting`), gdy karty czekają na ciebie, inaczej liczba kart w grze bez Gotowych i Archiwum (`muted`); zero — pusty tekst (plakietka ukryta). Tylko odczyt, niczego nie zakłada; ta sama reguła co `badge()` w `apps/board/backend.py` |
| `ShowWindow(s id)` | `→ b` | okno Tide na widoku Tablica (`tide.py --view board`), z ID karty (`K-12`, wielkość liter bez znaczenia) od razu na niej (`--select K-12`), z `launch:K-12` — na niej z oknem „Co ma zrobić agent?” (`--select launch:K-12`, „Uruchom” w popupie kafelka); napis, który nie jest ID karty, pomijamy (samo okno); `false`, gdy okna nie ma — kafelek tablicy w docku |
| `CardDocs(s id)` | `→ s` | plan i specyfikacja karty do panelu karty (niżej, „Plan i specyfikacja na karcie”): `{ok, card, plan, spec, others, warnings}`; tylko odczyt, odpowiedź po odczycie plików w wątku roboczym |
| `YouTrackIssues(b refresh)` | `→ s` | zgłoszenia użytkownika do importu (PT-7): `{ok, issues: [{id, summary, project, state, updated, url, card, boardProject, boardProjectName, new}], error}`; odpowiedź po pobraniu w wątku roboczym (`refresh: false` — ostatnia lista z pamięci); pokazane zgłoszenia liczą się jako przejrzane |
| `ImportYouTrack(s request)` | `→ s` | karty w Backlogu z listy `{issues: ["LOG-1", …]}` (najwyżej 200, tylko z ostatniej listy `YouTrackIssues`) → `{ok, created: [{issue, card, project}], skipped: [{issue, card, reason}], commitError}`; zgłoszenie z kartą — pominięte |
| `ResolveYouTrack(s id, s choice)` | `→ s` | konflikt ze zmianą ręczną w YouTracku: karta (`K-16`) albo klucz zgłoszenia, `choice`: `youtrack` (Tide przyjmuje stan z YouTracka) albo `tide` (zapis stanu Tide mimo zmiany) → `{ok, issue, choice}`; bez konfliktu — `not-found` |
| `StartCard(s id, s request)` | `→ s` | zlecenie w przepływie karty: wejście „Od razu” `{entry: "now", executor, reviewer?, preferredProvider?, images?}` albo Planowanie Lekkie `{entry: "planning", mode: "light", executor, preferredProvider?, images?, check?}`, albo propozycja planu `{entry: "plan", planPath}`; wynik zawiera `ok` i dane zlecenia albo `error`/`errors`; walidacja i przebieg opisane poniżej |
| `ResolveCardReview(s id, s choice)` | `→ s` | odpowiedź na pytanie po trzeciej lub kolejnej nieudanej rundzie review: `retry` uruchamia kolejne poprawki i review, `reject` odrzuca ticket bez scalenia, zachowując gałąź i worktree do wglądu; decyzja trafia do dziennika |
| `SetCardPaused(s id, b paused)` | `→ s` | wstrzymuje lub wznawia nowe kroki karty; trwające kończą się normalnie; stan w dzienniku jako `card.paused` / `card.resumed` |
| `EditCardPlan(s id, s request)` | `→ s` | zdarzenie zmiany nieruszonego ticketu (`action: edit`, `ticket`, `class`, `executor`, `reviewer`, `body`), odesłanie z uwagami (`action: return`, `feedback`) albo wejście do planującego w Konsoli (`action: enter`) |
| `ApproveCardPlan(s id)` | `→ s` | zatwierdza poprawną propozycję planu karty: rezerwuje lokalne ID, zapisuje mapę `#n → ID` w dzienniku i uruchamia tickety po blokerach |
| `CardBranchDiff(s id)` | `→ s` | po domknięciu karty zwraca diff gałęzi karty względem głównej, wyłącznie do odczytu |
| `MergeCardToMain(s id)` | `→ s` | wyłącznie na kliknięcie użytkownika scala domkniętą kartę do gałęzi głównej i przenosi ją do Gotowych; odmawia przy lokalnych zmianach w głównym checkoutcie |
| `Changed` | sygnał bez argumentów | migawka tablicy się zmieniła (zapis przez D-Bus, zmiana z zewnątrz, stan kart z pracy agentów) — pobierz `Snapshot()` ponownie |
| `WorkChanged` | sygnał bez argumentów | lista „Praca” się zmieniła — pobierz `Work()` ponownie. Od razu przy zmianie grup, pytań i liczb; gdy zmienia się tylko ostatnia czynność agenta (`note`), najwyżej co 10 s (`WORK_VOLATILE_MS`) |

Każda metoda zapisu odpowiada od razu (kilka małych plików i jeden commit). Odpowiedzi mają kształt
jak w `/Tasks`: sukces to `{"ok": true, …}`, błąd — `{"ok": false, "error": "<kod>", "message": "…"}`
z komunikatem po polsku dla człowieka, np. „do kolumny „progress” nie przenosi się ręcznie”, „nie ma
projektu „app5””. Nieznane pole w żądaniu to błąd, nie cicha pomyłka.

| Kod | Kiedy |
| --- | --- |
| `bad-request` | złe dane: nieznane pole, pusty tytuł, zła kolumna, kolor, tryb, ścieżka, niepoprawny JSON |
| `not-found` | nie ma karty, projektu albo tagu (do zmiany nazwy) |
| `exists` | repozytorium ma już projekt, tag o nowej nazwie już jest |
| `broken-file` | plik karty, projektu albo tagu jest w `errors` (nie da się go przeczytać) — popraw go ręcznie |
| `git-busy` | w repozytorium tablicy trwa revert, merge, cherry-pick albo rebase z konfliktami |
| `not-a-board` | katalog tablicy istnieje i nie jest tablicą (cudzych plików nie ruszamy) |
| `git-error`, `io-error`, `internal` | `git init` albo dysk zawiodły; błąd usługi (szczegóły w dzienniku) |

Najpierw walidacja, potem zapis: żądanie z błędem (także na pustym katalogu) nie zostawia pliku,
katalogu ani commita — tablica powstaje dopiero przy pierwszym poprawnym zapisie, a projekt
zakładany przy karcie — dopiero gdy cała karta jest poprawna. Po zapisie plików plik jest źródłem
prawdy: jeśli sam commit się nie uda (np. zajęty indeks), odpowiedź to nadal `ok: true` z opisem
w `commitError`, a commit z tym samym komunikatem czeka i idzie przy następnym zapisie albo
synchronizacji (migawka: `git.pending`, wpis w `errors`). Ponowienie żądania nie jest więc potrzebne
i nie tworzy duplikatu. Kolejka jest tylko w pamięci usługi: po restarcie te zmiany trafiają do
commitu „sync external changes”, podobnie jak wpis, który pada z trwałego powodu (np. pliku już nie
ma) — kolejkę zatrzymuje wyłącznie zajęty indeks albo zawieszony git.

**Układ repozytorium.** Wszystkie pliki to Markdown z płaskim frontmatterem (te same reguły co
w planach: pary `klucz: wartość`, bez YAML), więc da się je czytać i poprawiać ręcznie.

```
tide-board/
  board.md                   znacznik `tide-board: 1` i licznik `next-card`
  .gitignore                 pliki tymczasowe edytorów i zapisu
  projects/<slug>.md         projekt
  tags/<slug>.md             tag z kolorem
  cards/K-<n>.md             karta
  flows/full.md, light.md, spec.md     szablony poleceń (full, light — Planowanie; spec — Tylko specyfikacja)
  flows/<projekt>/<klucz>.md  nadpisanie szablonu dla jednego projektu (opcjonalne)
  docs/<projekt>/            plany i specyfikacje agentów projektów z `ai-docs: tide`
```

Karta — `cards/K-12.md`, treść pod frontmatterem to opis:

```markdown
---
id: K-12
title: "Hotfix: unikalne wyszukiwanie fraz"
project: logviewer
tags: pilne
column: todo
order: 20
note: bez agenta, robię sam
created: 2026-10-27T14:00:00+01:00
updated: 2026-10-27T14:05:12+01:00
moved: 2026-10-27T14:00:00+01:00
youtrack: TOM-142
spec:
session:
branch:
check: auto
---

Wyszukiwanie zwraca tę samą frazę kilka razy…
```

| Klucz | Znaczenie |
| --- | --- |
| `id` | `K-<numer>`; obowiązuje nazwa pliku (niezgodność to błąd w migawce). Numer nigdy nie wraca: następny = max z istniejących plików + 1, `next-card` z `board.md` i znacznika poza historią `.git/tide-next-card`, który przeżywa `git revert` commita zakładającego kartę (gałęzie `k-N` łączą kartę z koordynatorem). Gdy znacznika brak (np. kopia repozytorium), pierwsze założenie karty odbudowuje go raz z najwyższego numeru dodanego kiedykolwiek w historii gita; migawka historii nie czyta |
| `title` | jedna linia, do 200 znaków |
| `project` | slug projektu albo pusty (karta bez projektu) |
| `tags` | własne tagi karty po przecinku (`[a, b]` też działa); w migawce `resolvedTags` = tagi projektu ∪ własne. Listy YAML w bloku (`tags:` i niżej `  - ui`) nie są obsługiwane — dają wpis w `errors`, a zapis tagów zastępuje taką listę jedną linią |
| `column` | tylko kolumny ręczne: `backlog`, `todo`, `done`, `archived` |
| `order` | kolejność w kolumnie (mniejsza wyżej); nowa karta trafia na górę kolumny. `MoveCard` daje karcie środek między sąsiadami (zmienia się jeden plik), a całą kolumnę numeruje co 10 dopiero wtedy, gdy między sąsiadami nie ma miejsca |
| `note` | krótka notatka widoczna na karcie (np. „bez agenta, robię sam”) |
| `created`, `updated` | ISO 8601 z czasem lokalnym; `updated` zmienia każdy zapis karty |
| `moved` | ostatnie jawne przeniesienie (ISO 8601): ustawia go tylko założenie karty i każde `MoveCard` (także w obrębie kolumny); edycja pól — nie. Karta bez klucza (sprzed PT-5) liczy się od `created`. Z niego i z `moved-from` stan z pracy agentów rozpoznaje „do poprawy” |
| `column-moved` | ostatnia prawdziwa zmiana kolumny przez `MoveCard` (PT-7, ISO 8601): kolumna docelowa inna niż wyświetlana; zmiana kolejności w kolumnie go nie stempluje. W pliku dopiero po takim przeniesieniu; w migawce `columnMoved` i `columnMovedTs` |
| `moved-from` | kolumna, w której karta była wyświetlana przy ostatnim `MoveCard` (PT-29): `boardColumn` z migawki usługi (z pracy agentów, np. `review`, choć w pliku jest `todo`), bez niej — zapisana kolumna. „Do poprawy” tylko po przeniesieniu z `review` albo `done`; karta założona do skończonej już pracy (albo połączona z nią przez `session`) nie „wraca ręcznie”. Zmiana kolejności w kolumnie, w której karta jest widoczna (np. „do poprawy” w Do zrobienia), zachowuje dotychczasowy `moved-from`. Karty przeniesione przed PT-29 nie mają klucza — reguła ich nie dotyczy |
| `youtrack`, `spec` | `youtrack` — klucz zgłoszenia w YouTracku (PT-7): zapis przyjmuje klucz albo adres zgłoszenia (`…/issue/legal-35` → `LEGAL-35`), inny tekst — `bad-request`; stan karty idzie do zgłoszenia, gdy projekt YouTracka ma synchronizację (niżej); `spec` — ścieżka gotowej specyfikacji z trybu „tylko spec” |
| `session` | sesja albo koordynator, który prowadzi kartę — id jak w `/Agents` i `/Coordination` (`claude:<id>`, `codex:<id>`, samo `<id>` też działa); pierwsze źródło stanu karty |
| `branch` | gałąź pracy w repozytorium projektu; zastępuje propozycję gałęzi w `RenderFlow`, łączy kartę z pracą na tej gałęzi i jest gałęzią do scalenia |
| `check` | sprawdzenie po skończonej pracy agenta: `auto` (domyślnie — wystarczą testy i review AI) albo `manual` (test ręczny użytkownika: karta czeka w Do sprawdzenia, aż przeniesiesz ją do Gotowe). Zła wartość w pliku — błąd i `auto`; brak klucza — `auto`. Ustawia go m.in. przełącznik „Test ręczny po zakończeniu” przy uruchomieniu agenta (PT-4) |
| `entry`, `mode`, `tool`, `worktree`, `launched` | ostatnie uruchomienie agenta z karty: wejście (`planning`, `spec`), tryb (`full`, `light`; przy `spec` pusty), narzędzie, katalog worktree (bezwzględny albo pusty — praca w katalogu projektu) i chwila (ISO 8601); w pliku tylko po uruchomieniu. Karta ze starym `mode: spec` to wejście `spec` (czytane, plik nietknięty); żądanie z `mode: spec` zapisuje `entry: spec` |
| `projects` | dodatkowe projekty karty (PT-28) — slugi po przecinku, główny zostaje w `project`; zadanie zmienia kilka repozytoriów naraz. W pliku tylko, gdy ustawione. `CreateCard`/`UpdateCard` przyjmują listę (albo napis „a, b”) slugów istniejących projektów albo bezwzględnych ścieżek repozytoriów — repozytorium bez projektu dostaje projekt osobnym commitem (jak `repo`); główny projekt i powtórzenia wypadają, najwyżej 5, nieznany slug — `not-found`. Zmiana `project` na jeden z dodatkowych usuwa go z `projects`. Nieznany slug w pliku — wpis w `errors`, karta działa |

Projekt — `projects/mobileapp.md`, treść to opcjonalny opis:

```markdown
---
name: mobileapp
repo: /home/user/DevWorkspace/mobileapp
tags: Firma
ai-docs: tide
tool: claude
mode: full
---
```

- `repo`: ścieżka bezwzględna do repozytorium albo pusty (projekt bez kodu). Repozytorium tablicy nie może być projektem.
- `ai-docs`: `repo` (plany i specyfikacje agentów w `docs/agents/` repozytorium) albo `tide` (w `tide-board/docs/<projekt>/`, nic nie trafia do repozytorium projektu). Przy zakładaniu: `repo`, gdy repozytorium ma już `docs/agents/`, inaczej `tide`; bez klucza w pliku — to samo wyliczane przy odczycie (`aiDocsDefault: true`).
- `tool`: `claude` albo `codex`, `entry`: `planning` albo `spec` (Tylko specyfikacja), `mode`: `full` albo `light` — domyślne dla uruchomień z kart projektu (domyślnie `claude`, `planning` i `full`; tryb dotyczy tylko Planowania). PT-77: „Tylko specyfikacja” jest wejściem, nie trybem — projekt ze starym `mode: spec` czytamy jako `entry: spec` (tryb `full`), bez błędu i bez przepisywania pliku; stary zapis znika dopiero, gdy użytkownik zmieni wejście albo tryb projektu. Kolejne wejścia (Mam plan, Od razu) dojdą do `entry` bez zmiany formatu.
- `youtrack`: klucz projektu YouTracka (`LOG`, `LEGAL`; wielkie litery i cyfry, unikalny na tablicy — drugi projekt z tym samym kluczem to `exists`), `youtrack-sync`: `on` (domyślnie, bez klucza w pliku) albo `off` — PT-7, „Synchronizacja z YouTrackiem” niżej. Projekt bez klucza nie synchronizuje niczego. Klucz z importu przypisuje też zaimportowane karty do projektu.
- Projekty powstają jawnie (`UpsertProject`; ta sama nazwa drugi raz to ten sam projekt, nie `foo-2`) albo przy pierwszej karcie dla repozytorium (`CreateCard` z `repo`) — osobnym commitem. Usługa nie zakłada wpisów dla wszystkich repozytoriów z katalogu projektów; migawka podaje je w `repos` jako kandydatów.

Tag — `tags/firma.md`: `name` (bez przecinka, do 40 znaków) i `color` — nazwa pastelu z palety
(`teal`, `lavender`, `peach`, `butter`, `rose`, `mint`, `sky`; lista z `pastel` w `theme/palette/`),
treść to opis. Nowa tablica dostaje „Firma” (`sky`, klient komercyjny) i „własne” (`lavender`).
Tag użyty w karcie albo projekcie bez własnego pliku też jest w migawce (`defined: false`) ze stałym
kolorem wyliczonym z nazwy. Wielkość liter w nazwach tagów nie ma znaczenia przy porównaniu.

**Szablony trybów.** `RenderFlow` zakłada katalog dokumentów, jeśli leży w tablicy (`ai-docs: tide`,
także `docs/_inbox` dla karty bez projektu) — agent dostanie go przez `--add-dir`; w repozytorium
projektu (`ai-docs: repo`) niczego nie tworzy. Pusty katalog nie trafia do gita, pliki agenta zbierze
synchronizacja. Szablon bierze `flows/<projekt>/<klucz>.md`, potem `flows/<klucz>.md` (klucz to tryb Planowania albo `spec` przy wejściu Tylko specyfikacja; `flows.flow_key`), a bez
tablicy — wbudowany szablon (`board/flows.py`, te same treści, które trafiają do nowej tablicy).
Frontmatter szablonu nie trafia do polecenia. Zastępowane są wyłącznie pola `{card_id}`, `{title}`,
`{description}`, `{project}`, `{repo}`, `{docs_dir}`, `{branch}`, `{tool}`, `{docs_note}`, `{spec}`,
`{spec_file}`, `{check}`, `{check_label}`, `{finish}`, `{spec_commit}`, `{projects}` — inne klamry zostają, a opis karty nie jest
szablonem. `{projects}` (PT-28, w nagłówku zaraz za „Plany i specyfikacje”) jest pusty przy jednym projekcie;
przy dodatkowych — lista „nazwa: ścieżka” repozytoriów i zasady: plan i specyfikacja tylko w `{docs_dir}`
głównego projektu, w każdym dodatkowym repozytorium, które agent zmienia, gałąź o tej samej nazwie `{branch}`
od lokalnej gałęzi głównej (worktree tylko, gdy wymagają tego zasady repozytorium; przy zmianach albo innej
gałęzi w głównym katalogu — pytanie do użytkownika; bez push), pliki po ścieżkach bezwzględnych i git przez
`git -C`, bez `cd`, osobne commity w każdym repozytorium, a w Uwagach planu przy tickecie — które repozytorium
zmienia; przy wejściu Tylko specyfikacja repozytoria są tylko do odczytu. Własny szablon bez `{projects}` dostaje tę listę na
końcu polecenia. Gałąź: z okna, inaczej `branch` karty albo propozycja `<narzędzie>/k-12-<opis-z-tytułu>`
(po ID karty w gałęzi daemon połączy kartę z koordynatorem; przy wejściu Tylko specyfikacja polecenie mówi „bez gałęzi”).
`{docs_dir}` to `docs/agents` (względnie — katalog roboczy agenta, także worktree) przy `ai-docs: repo`
i ścieżka bezwzględna w tablicy przy `ai-docs: tide`; `{docs_note}` mówi, kto commituje te pliki (w
tablicy — tylko usługa), a w tablicy także: pliki po ścieżkach bezwzględnych, bez `cd` do katalogu
dokumentów (katalog roboczy zostaje w repozytorium projektu — PT-28). `{spec_file}`: `{docs_dir}/specs/k-12-<opis>.md`. `{finish}`: przy `manual`
lista „Co przetestować ręcznie i jak” w planie, bez scalania i z informacją, że praca czeka na test
ręczny; przy `auto` koniec po review AI z APPROVE, scalenie tylko wtedy, gdy pozwalają na nie zasady
projektu.

Treści trybów (`board/flows.py`, polecenia po polsku): **pełny** — grilling (skill `grilling` w Claude
Code, w Codeksie te same pytania opisane słowami), specyfikacja w `{spec_file}` z frontmatterem
`card`/`status: szkic`→`gotowa`, plan według standardu 1.1 z `card: {card_id}` i `coordinator: {tool}`,
numery ticketów z zasad projektu (CLAUDE.md/AGENTS.md, `make -C ~/DevWorkspace/plasma-tide next-id`,
`check-plans`; bez zasad — `tracker: local` z prefiksem zapisanym w planie), TDD (skill `tdd`),
delegowanie i obowiązkowe review AI według standardu, pytania z ID ticketu; **lekki** — krótka
specyfikacja, plan z 1–3 ticketami, TDD, review AI; **tylko specyfikacja** — pytania i specyfikacja
z review AI, bez kodu, gałęzi i planu, na koniec `status: gotowa`; `{spec_commit}`: w tablicy nic nie
commituje (robi to usługa), a przy `ai-docs: repo` commituje wyłącznie plik specyfikacji na bieżącej
gałęzi (`docs(spec): K-12 <short English title>`, po angielsku, bez podpisów i wzmianek o AI, bez push) —
inaczej nowy worktree, który wychodzi z commita, nie zawierałby specyfikacji.

**Migracja szablonów.** Tablica założona przed PT-4 ma w `flows/` dawne zaczyny (a sprzed PT-28 —
`tide-flow: 2` bez `{projects}`; oba są w `flows.PREVIOUS_SEEDS`). Przy starcie usługi
(`migrate_flows`) plik `flows/<klucz>.md`, który jest bajt w bajt dawnym zaczynem (skróty SHA-256
w `flows.PREVIOUS_SEEDS`), dostaje nową treść jednym commitem `flows: update templates`; plik zmieniony
przez użytkownika, nadpisania projektów (`flows/<projekt>/…`) i brakujące pliki zostają. PT-77: nagłówek zaczynu `spec` zmienił się z `mode: spec` na `entry: spec` (treść polecenia ta sama), więc dotychczasowy zaczyn jest w `PREVIOUS_SEEDS` i dostaje nowy nagłówek; szablon `spec` zmieniony przez użytkownika (także nadpisanie projektu), który w nagłówku ma jeszcze `mode: spec`, zostaje nietknięty, a migawka (`errors`) niesie ostrzeżenie z prośbą o `entry: spec`. W trakcie
revertu albo merge'a migracja czeka, a `RenderFlow` i tak nigdy nie wstawia dawnego zaczynu do polecenia
(bierze wtedy wbudowaną treść).

**Gotowa specyfikacja** (`mark_ready_specs`, przy każdej synchronizacji — po zmianie w tablicy po 2 s,
inaczej co 60 s, więc dla specyfikacji w repozytorium projektu do minuty): karta z wejściem Tylko specyfikacja (`entry: spec`, także dawne `mode: spec`)
w Do zrobienia bez `spec`, której `<docs>/specs/` ma plik Markdown nazwany od ID karty (`k-12.md`,
`k-12-…`) albo z `card: K-12` we frontmatterze i ze `status: gotowa` (także `gotowy`, `ready`, `done`),
dostaje `spec: <ścieżka>` i wraca na górę Backlogu — commit `K-12: specification ready`. Na karcie
w Backlogu widać „✓ specyfikacja gotowa”. Szkic (`status: szkic`), inny tryb i karta przeniesiona ręcznie
— bez zmian. Agent niczego nie woła przez D-Bus: wystarczy zmiana linii `status` w pliku.

**Commity.** Każda zmiana z D-Bus to osobny commit tylko z plikami, które usługa zmieniła, np.
`board: create`, `K-21: create`, `K-21: update title, tags`, `K-12: move to todo`,
`K-12: reorder in todo`, `project mobileapp: create`, `tag pilne: update`,
`tag własne: rename to Własne`. Autor z konfiguracji gita użytkownika (bez niej „Tide
<tide@localhost>”), bez podpisu GPG i bez żadnych hooków (`--no-verify`, `core.hooksPath=/dev/null`)
— to zapis stanu tablicy, nie praca nad kodem.

**Git: blokady i przerwane operacje** (`board/repo.py`). Git dostaje 5 s. Zabity po czasie zostawia
`.git/index.lock` — usługa usuwa go od razu, jeśli w katalogu tablicy nie działa żaden inny proces
`git`; cudzą blokadę usuwa dopiero, gdy jest starsza niż 10 s i nie ma żywego procesu git z katalogiem
w tablicy. Gdy w repozytorium trwa revert, merge, cherry-pick albo rebase (pliki `REVERT_HEAD`,
`MERGE_HEAD`, `CHERRY_PICK_HEAD`, `rebase-*` albo pliki z konfliktami w `git status`), usługa niczego
nie commituje — commit „sync” utrwaliłby znaczniki konfliktu — zapisy z D-Bus kończą się błędem
`git-busy` („dokończ go (git revert --continue) albo przerwij (git revert --abort)”), a migawka ma
`git.busy` i wpis w `errors`. Frontmatter ze znacznikami konfliktu (`<<<<<<<`, `>>>>>>>`) to błąd
pliku; w treści znaczniki są zwykłym tekstem (opis może zawierać wklejone wyjście gita), a trwający
konflikt w treści widać po plikach niescalonych w `git status`.

**Zmiany z zewnątrz.** Agent piszący plan w `docs/`, ręczna edycja karty, `git revert` — usługa
obserwuje katalogi i pliki tablicy (`QFileSystemWatcher`), a po 2 s ciszy robi commit
`sync external changes` ze wszystkim, co się zmieniło, i wysyła `Changed`. To samo sprawdzenie idzie
przy starcie (zmiany z czasu, gdy usługa nie działała) i co 60 s (zapis bez zdarzenia, tablica
założona poza usługą), razem z zaległymi commitami naszych zapisów (te z własnymi komunikatami).
Nasze własne zapisy są już w commicie, więc nie dają drugiego.

**Błędy plików.** Zły plik nie zatrzymuje tablicy: trafia do `errors` w migawce (`{path, message}`),
a reszta działa. Pomijany jest plik bez frontmattera, ze znacznikami konfliktu we frontmatterze, nie w UTF-8, większy
niż 256 KB (czytamy najwyżej tyle, nigdy całości), niebędący zwykłym plikiem (FIFO, urządzenie) albo
dowiązanie wskazujące poza katalog tablicy (dowiązanie wewnątrz tablicy działa); numer karty takiego
pliku i tak jest zajęty. Linie frontmattera, które nie są parą `klucz: wartość`, dają wpis w `errors`; zła wartość (kolumna, kolejność, narzędzie, tryb, `ai-docs`, kolor)
daje błąd i wartość domyślną; karta z nieznanym projektem — błąd. Karty z pominiętego pliku nie da
się zmienić przez D-Bus („popraw go ręcznie”). Zapis zmienia tylko linie ustawianych kluczy:
nieznane klucze, komentarze i treść zostają co do znaku.

**Repozytorium tablicy poza listami projektów.** Skaner repozytoriów (`worktrees/scanner.py:
find_repositories`) pomija katalog z `board.md` zawierającym `tide-board` we frontmatterze — po
znaczniku, nie po nazwie — więc tablicy nie ma w Worktrees, KRunnerze, oknie „Nowe zadanie” ani
w raporcie porannym, gdziekolwiek leży.

**Migawka** (`Snapshot()`, `--print-board`):

| Pole | Opis |
| --- | --- |
| `boardDir`, `exists` | katalog tablicy i czy repozytorium już istnieje |
| `lastCommit` | `{hash, short, ts, time, subject}` albo `null` |
| `git` | `{busy, pending, error}`: trwająca operacja (`revert`, `merge`, `cherry-pick`, `rebase` albo pusty), liczba zaległych commitów i ostatni błąd commita |
| `nextId` | ID, które dostanie następna karta |
| `columns` | `[{id, label, manual, hidden?}]`: `backlog` „Backlog”, `todo` „Do zrobienia”, `progress` „W toku”, `waiting` „Czeka na mnie”, `review` „Do sprawdzenia”, `done` „Gotowe”, `archived` „Archiwum” (ukryta); `manual: false` — kolumny wyliczane z koordynatora |
| `colors` | pastele z palety `{nazwa: "#rrggbb"}` |
| `tools`, `entries`, `modes` | dozwolone narzędzia, wejścia (`planning`, `spec`) i tryby Planowania (`full`, `light`) |
| `tags` | `[{name, slug, color, description, defined, cards}]` (`cards` — karty z tym tagiem po dziedziczeniu) |
| `projects` | `[{slug, name, repo, repoMissing, tags, aiDocs, aiDocsDefault, tool, entry, mode, description, docsDir, cards, path, youtrack, youtrackSync}]` (`youtrackSync` — klucz ustawiony i synchronizacja włączona) |
| `cards` | `[{id, num, title, project, projectName, tags, resolvedTags, column, boardColumn, order, note, created, createdTs, updated, updatedTs, moved, movedTs, youtrack, spec, session, branch, check, entry, mode, tool, worktree, launched, launchedTs, branchSlug, projects, extraProjects, description, derived, path}]` w kolejności kolumn i `order`; `branchSlug` — `k-12-<opis>`, część proponowanej gałęzi bez narzędzia; `extraProjects` — dodatkowe projekty rozwiązane `[{slug, name, repo, repoMissing}]` (bez nieznanych slugów) |
| `repos` | repozytoria z katalogu projektów, które nie są jeszcze projektami: `[{name, path, aiDocs}]` (domyślne `ai-docs`) |
| `errors` | `[{path, message}]` — złe pliki; także konflikty z YouTrackiem (PT-7, z pustym `path`) |
| `youtrack` | stan synchronizacji (PT-7, tylko usługa z YouTrackiem — bez `--no-youtrack`): `{enabled, sync, url, available, error, pending, conflicts, conflictItems: [{issue, label, cards, youtrack, tide, url}], newIssues, importError, states}` |

Karta ma też `youtrackSync` (PT-7): `null` bez pola `youtrack`, inaczej `{status, text, issue, issueState, url,
via, label, conflict}` — `status`: `ok`, `pending`, `offline`, `error`, `conflict`, `off` (projekt bez
synchronizacji); `via`: `card` albo `ticket` (stan wzięty z ticketu planu, `label` mówi którego); `conflict`:
`{youtrack, tide}`.

`derived` to stan karty z pracy agentów (opis niżej) albo `null`; `boardColumn` to kolumna do
wyświetlenia: `derived.column`, a bez niego (albo przy pustej) zapisana `column`. Pliki kart nie
zmieniają się od stanu agentów — ręczna kolumna to wyłącznie decyzja użytkownika.

**Stan karty z pracy agentów** (`board/derive.py`, PT-5). Karta w Do zrobienia (w Backlogu — tylko
przy połączeniu jawnym) dostaje kolumnę z tego, co agenci naprawdę robią; Gotowe i Archiwum nigdy.

Połączenie karty z pracą, pierwsze pasujące:

1. `session` karty — sesja z `/Agents` albo koordynator z `/Coordination` (to samo id), niezależnie od
   tego, gdzie sesja jest teraz (cwd, projekt, gałąź). Przypięta sesja, która już nie działa, ustępuje
   nowszej, żywej pracy karty z reguł 2, 4, 5 (ponowne uruchomienie, nowa sesja na gałęzi karty),
2. uruchomienie z karty (PT-28): sesja tego samego narzędzia co `tool` karty, której katalog startu
   (`launchCwd`, bez niego `launchKey`) to `worktree` karty — bez worktree repozytorium projektu — i która
   zaczęła się (`startedTs`) od 2 min przed do 5 min po `launched` (`LAUNCH_SLACK`, `LAUNCH_WINDOW`); kilka —
   najbliższa chwili uruchomienia; podagenci i sesje przypięte do innych kart się nie liczą,
3. `card: K-12` we frontmatterze planu koordynatora (klucz z PT-6, czytany ostrożnie),
4. ID karty w nazwie gałęzi koordynatora, jego ticketu albo sesji (`claude/k-12-eksport` — propozycja
   `RenderFlow`), w repozytorium projektu karty albo jej dodatkowego projektu (`projects`),
5. `branch` karty równa gałęzi koordynatora, ticketu albo sesji w tych repozytoriach — także
   z ich worktrees (główny katalog repozytorium z plików `.git`, `agents/gitinfo.py: main_root`);
   gałęzi głównych (master, main, develop, HEAD) nie dopasowujemy.

Przy kilku dopasowaniach tej samej reguły wygrywa najświeższa aktywność (żywe przed zakończonymi).
Backlog to „kiedyś”: stan dostaje tylko z reguł 1–3, bo gałąź może pasować przypadkiem albo do starej
pracy.

**Trwałe połączenie** (PT-28, `Context.sticky`, `BoardMonitor`). Karta bez `session`, którą przeliczenie
połączyło regułą 2 albo jednoznacznie (jedno dopasowanie — koordynator z ticketami to jedno) regułą 4 lub 5,
dostaje tę sesję w pliku: `store.link_session`, commit `K-1: link session claude:<id> (launch)` (albo
`(card-branch)`, `(branch)`). Zapis idzie po przeliczeniu (`QTimer`), ta sama para karta–sesja najwyżej raz
na minutę. Ostrożnie przy świeżym uruchomieniu: przez 30 s po `launched` nic nie trafia do pliku
(`PERSIST_DELAY`), „launch” utrwalamy tylko dla sesji, która zaczęła się najwyżej 15 s przed `launched`
(wcześniejsza to raczej inna sesja w tym samym katalogu — w pamięci karta ją pokaże), a połączenia po gałęzi
nigdy w oknie uruchomienia (5 min — np. stara, zakończona sesja tej gałęzi po ponownym uruchomieniu). Karta
połączona z częścią ticketów koordynatora dostaje razem z `session` gałąź tych ticketów w `branch` (gdy jej
nie miała), żeby reguła 1 dalej obejmowała tylko te tickety; tickety z kilku gałęzi — nie utrwalamy. Ustawionego `session` (także ręcznie) usługa nigdy nie nadpisuje — czyści go tylko jawne ponowne
uruchomienie z okna (`UpdateCard` po starcie wysyła `session: ""`), a nową sesję przypina reguła 2 (stara
zaczęła się poza oknem nowego `launched`). Później sesja może wejść
gdzie indziej — karta jej nie zgubi. Przypadek z życia: agent K-1 zrobił `cd` do `<tablica>/docs/<projekt>/plans`,
żeby napisać plan, a karta wróciła do Do zrobienia z „Uruchom ponownie…”, choć agent dalej pracował. Koordynator z karty, który już nie działa, ustępuje nowszemu, żywemu koordynatorowi tego samego
planu (nowa sesja po wyczerpaniu kontekstu). Gdy gałąź karty jest inna niż gałąź koordynatora, a jego
tickety mają tę gałąź, karta obejmuje tylko te tickety (`tickets`) — koordynator prowadzi kilka kart naraz.

| Kolumna | Kiedy |
| --- | --- |
| `waiting` — Czeka na mnie | agent stoi w pół pracy i bez ciebie nie ruszy: sesja w stanie `waiting` (ta sama definicja co widżet agentów, `agents/state.py: waits_for_user` — także `reply`: koniec tury z pytaniem w ostatniej wiadomości bez odpowiedzi, notatka „czeka na odpowiedź: „<pytanie>””, PT-29), ticket w `czeka` z pytaniem bez odpowiedzi albo ticket w `czeka` z samego planu, gdy agent nie pracuje (bezczynny, skończył turę, sesja zakończona — notatka „czeka na twoją decyzję według planu: <Uwagi z planu>”). Pytanie albo `czeka` ticketu spoza karty nie dotyczy karty |
| `progress` — W toku | praca trwa albo stoi niedokończona: tickety niegotowe — także review AI i poprawki (`review`, `w-toku`, `todo`; `czeka` z planu, dopóki agent pracuje) —, agent pracuje albo jest bezczynny, skończył turę, sesja się zakończyła (notatka: „agent bezczynny od 16:46, w toku: LEGAL-6”, „sesja zakończona”) |
| `review` — Do sprawdzenia | praca agentów skończona (wszystkie tickety gotowe albo odrzucone, plan `done` bez ticketów; agent już nie pracuje), ale potrzebny krok człowieka: `check: manual` („do sprawdzenia ręcznie”) albo niescalona gałąź („do scalenia: feat/x”) |
| `done` — Gotowe | skończone, `check: auto` i nic do scalenia („✓ gotowe i scalone”); plik karty zostaje w Do zrobienia — przeciągnięcie do Gotowe (albo wybór w panelu karty) to zapisuje |

Zwykła sesja bez ticketów nigdy nie jest „skończona” — bez ticketów i planu nie wiadomo, czy praca
jest gotowa, więc taka karta zostaje w W toku z notatką, od kiedy agent stoi. Karta przeniesiona ręcznie po
skończonej pracy z Do sprawdzenia albo Gotowe (`moved` późniejszy niż ostatnia aktywność ticketów i `moved-from`
`review` albo `done`, np. „do poprawy” — z panelu z powrotem do Do zrobienia; sama edycja notatki, założenie
karty, ustawienie `session` i przeniesienie z innej kolumny się nie liczą — PT-29) zostaje w swojej kolumnie (`derived.column` pusty, notatka „wróci do W
toku…”), dopóki agent znów nad nią nie popracuje. Ręczne Gotowe zawsze wygrywa i potwierdza sprawdzenie.

**Scalona gałąź**: czubek gałęzi jest przodkiem lokalnej gałęzi głównej (`git merge-base --is-ancestor`,
jak w Worktrees). Gałąź, której nie ma lokalnie, jest scalona tylko wtedy, gdy w ostatnich 1000 commitach
scalenia głównej jest jej „Merge branch 'feat/x'” (usunięta po scaleniu); bez tego — nigdy jej tu nie
było albo scalenie szybkie/squash — „nie wiadomo”. Nie wiadomo (także brak repozytorium albo gałęzi
głównej) — „do scalenia”, żeby człowiek spojrzał. Sprawdzamy tylko skończoną pracę: gałąź karty, a bez
niej gałęzie ticketów i koordynatora. Karta z dodatkowymi projektami (PT-28): gałąź karty (bez niej — pierwsza
gałąź pracy) sprawdzamy też w każdym dodatkowym repozytorium; gałąź, której tam nie ma i nie widać jej scalenia
(`MISSING` — agent nie zmieniał tego repozytorium), nie jest potrzebna, a niescalona daje „do scalenia:
mobileapp feat/x” (z nazwą repozytorium przy każdej gałęzi, także głównego). Git idzie w jednym wątku roboczym (`BranchState(background=True)`),
przeliczenie odpowiada z pamięci (30 s; gałąź bez śladu — 10 min, żeby nie sprawdzać jej w kółko), a nowy
wynik zleca kolejne przeliczenie; dopóki pierwszego wyniku nie ma, skończona karta stoi w Do sprawdzenia.

`derived`: `column`, `done` i `total` (tickety karty: gotowe / wszystkie bez odrzuconych; zwykła sesja —
0), `tool` (`claude`, `codex`), `note` (jedno zdanie po polsku, do 160 znaków: co teraz, pytanie, od kiedy
stoi, czego brakuje), `nextStep` („odpowiedz agentowi”, „przetestuj ręcznie, potem scal gałąź feat/x”,
„przenieś do Gotowe”; pusty w W toku), `check`,
`branch` (gałąź karty albo pracy; w Do sprawdzenia — gałąź do scalenia), `session` (id dla „Otwórz
w Sesjach” / „Przejdź do agenta”: koordynator albo sesja), `coordinator` (id koordynatora albo pusty —
zwykła sesja), `link` (`session`, `plan`, `card-branch`, `branch`), `agentState` (stan sesji z `/Agents`),
`tickets` (ID ticketów karty, gdy obejmuje część koordynatora; pusta lista — cały), `question` (pytanie, na które
czeka ta karta: pytanie jej ticketu, koordynatora albo sesji; pusty przy zgodzie na narzędzie, czekaniu według planu
i poza „Czeka” — lista „Praca” bierze pytanie stąd, nie z dowolnego pytania koordynatora, PT-40).

**Na żywo.** `BoardMonitor` trzyma migawkę z plików w pamięci; zmiana w `/Agents` (`AgentMonitor.changed`)
albo `/Coordination` (`CoordinationMonitor.changed`) przelicza tylko `derived` i `boardColumn`, najwyżej co
500 ms (seria zmian sesji to jedno przeliczenie), bez czytania repozytorium tablicy, i wysyła `Changed`,
gdy migawka się zmieniła. Indeksy (repozytoria bez dowiązań, `(repozytorium, gałąź)` → praca, numer
karty z nazwy gałęzi) powstają raz na przeliczenie, więc karta to kilka wyszukań w słownikach (80 kart,
60 koordynatorów po 15 ticketów i 40 sesji — kilka ms; test `SpeedTest`). Plakietka (`Badge()`) i kafelek liczą z `boardColumn`, więc idą za tym same.
`--print-board` pokazuje migawkę bez stanu z pracy agentów (bez skanowania dzienników).

**Lista „Praca”** (PT-40, `board/work.py`, `Work()`, decyzja 76). Jedna prawda dla kafelka Praca, który zastąpił
kafelki Tablica i Agenci (wcześniej każdy liczył po swojemu — tablica „5 czeka”, agenci „1 czeka · 4 gotowe”).
`BoardMonitor` liczy ją w tym samym przeliczeniu co `derived` i `boardColumn`, z tego samego kontekstu derive
(te same sesje z `/Agents`, ci sami koordynatorzy), więc liczby nie mogą się rozjechać z Tablicą. Osobna metoda
i osobny sygnał `WorkChanged` zamiast pola w `Snapshot()`: kafelek nie parsuje przy każdej zmianie sesji całej
migawki z opisami kart, a okno Tide i raport nie pobierają migawki, gdy zmieniła się tylko lista (np. sesja bez
karty zaczęła czekać). Ostatnia czynność agenta zmienia się przy każdym narzędziu, więc sama jej zmiana daje
`WorkChanged` najwyżej co 10 s; `Work()` zawsze oddaje stan bieżący. Błąd w liczeniu listy nigdy nie psuje
tablicy (w ostateczności pusta lista).

- **Karty:** kolumna wyświetlana (`boardColumn`) `waiting`, `progress`, `review` albo `todo` — bez Backlogu, Gotowych
  i Archiwum; grupa = ta kolumna. Połączenie z sesją wyłącznie z `derived` karty — `work.py` nie ma własnych reguł.
- **Sesje bez karty:** żywa sesja (stan inny niż `ended`), której nie wskazuje żadna karta — ani `derived.session` /
  `derived.coordinator`, ani pole `session` karty (także w Gotowych i Archiwum, którym derive stanu nie daje;
  samo id sesji też się liczy). `waiting` → `waiting`, `working` → `progress`, `done` i `idle` → `finished`.
  Jedna sesja prowadząca dwie karty to dwie pozycje-karty i żadnej pozycji-sesji; podagenci Codexa są już zwinięci.
- **Czekająca sesja zawsze w `waiting`:** sesja w stanie `waiting`, której nie obejmuje żadna karta z grupy `waiting`
  (jej karta jest w Gotowych, a sesję wznowiono; koordynator pyta o ticket spoza karty, więc karta jest W toku),
  dostaje pozycję-sesję z `card` tej karty i notatką „karta K-2 w Gotowych” / „karta K-1 w toku” (bez `cardRequest`).
  Kafelek nie może pokazać „0 czeka”, gdy w `/Agents` ktoś czeka.
- **Kolejność:** grupy `waiting`, `progress`, `review`, `todo`, `finished`; w grupie najpierw karty (kolejność
  tablicy), potem sesje — czekające od najdłużej czekającej (jak Meta+A), pozostałe od najświeższej zmiany stanu (`sinceTs`).

```json
{"exists": true,
 "counts": {"waiting": 2, "progress": 3, "review": 2, "todo": 3, "finished": 2, "total": 10, "sessions": 2},
 "items": [
  {"kind": "card", "id": "K-20", "card": "K-20", "title": "Twitch: czat i alerty", "project": "twitch",
   "projectName": "twitch", "group": "waiting", "note": "czeka na odpowiedź: „Czy taki podział…?”",
   "question": "Czy taki podział…?", "nextStep": "odpowiedz agentowi", "agentState": "waiting",
   "session": "claude:6b1f…", "canActivate": true, "sinceTs": 1793191980,
   "done": 0, "total": 0, "tool": "claude", "branch": "feat/k-20-moduly"},
  {"kind": "session", "id": "codex:019a…", "card": "", "title": "pricingapp · feat/rabaty", "project": "",
   "projectName": "pricingapp", "group": "waiting", "note": "shell · git push", "question": "Mogę zrobić push…?",
   "nextStep": "odpowiedz agentowi", "agentState": "waiting", "reason": "reply", "session": "codex:019a…",
   "canActivate": true, "sinceTs": 1793191500, "done": 0, "total": 0, "tool": "codex",
   "branch": "feat/rabaty", "subagents": 0,
   "cardRequest": {"title": "pricingapp · feat/rabaty", "column": "todo", "session": "codex:019a…"}}]}
```

| Pole pozycji | Opis |
| --- | --- |
| `kind`, `id`, `card` | `card` (ID karty) albo `session` (ID sesji z `/Agents`; `card` pusty — sesja bez karty, inaczej karta czekającej sesji spoza „Czeka”) |
| `group` | `waiting`, `progress`, `review`, `todo`, `finished` (tylko sesje bez karty, poza `counts.total`) |
| `title`, `project`, `projectName` | tytuł karty albo tytuł sesji (Claude Code), inaczej „projekt · gałąź”; projekt tablicy (sesja: projekt jej repozytorium, jeśli jest na tablicy) |
| `note`, `nextStep` | notatka i następny krok z `derived` (sesja: ostatnia czynność; sesja karty spoza „Czeka” — gdzie jest karta) |
| `question` | tylko `waiting`: karta — `derived.question` (pytanie tej karty; przy zgodzie na narzędzie albo planie pusty, opis jest w `note`), sesja — jej `waitingText` |
| `agentState`, `sinceTs` | stan agenta (`derived.agentState` albo stan sesji) i od kiedy trwa (sesja; koordynator bez żywej sesji — jego ostatnia aktywność) |
| `session`, `canActivate` | cel „Przejdź” (`/Agents ActivateSession`; karta — `derived.session` albo koordynator) i czy da się przejść (żywa sesja ze znanym oknem — kafelek pokazuje wtedy „Przejdź” także przy kartach W toku i Do sprawdzenia) |
| `done`, `total`, `tool`, `branch` | postęp ticketów, narzędzie, gałąź |
| `cardRequest` | tylko sesja bez karty (nie osierocony podagent): gotowe żądanie `CreateCard` dla „+ karta” — tytuł, kolumna Do zrobienia, `session` (derive łączy kartę od razu, reguła 1) i `project`, gdy repozytorium sesji jest projektem tablicy; inaczej karta bez projektu (żadnego cichego zakładania projektu). `CreateCard` z sesją, którą ma już aktywna karta (pole `session`, nie w Gotowych ani Archiwum), nie zakłada drugiej — oddaje `{ok: true, id, existing: true}` |

`counts.total` to wszystko w grze bez `finished` („Praca · 10”), `counts.sessions` — ile z tego to sesje bez karty.
Bez tablicy (`exists: false`) lista ma tylko sesje bez karty; usługa bez `/Agents` — same karty.

**Synchronizacja z YouTrackiem** (PT-7, `board/ytsync.py`, `ytsync_monitor.py`; zmienia dawne „YouTrack tylko do
odczytu”). Użytkownik: „to ma się dynamicznie zmieniać, aktualizować youtracka”. Tide jest źródłem stanu,
YouTrack (prywatny: użytkownik i domownik) dostaje stan kart i ticketów.

- **Co:** karta z polem `youtrack` i ticket planu z `tracker: youtrack`, którego ID jest kluczem zgłoszenia —
  tylko w projektach YouTracka, które mają na tablicy projekt z kluczem (`youtrack: LOG`) i włączoną
  synchronizacją, i tylko przy `sync: true` w ustawieniach (domyślnie). Archiwum — bez zmian w YouTracku.
- **Mapa** (klucze w `states`, domyślne nazwy z workflow LOG i LEGAL): karta — Backlog i Do zrobienia →
  `open` (Open), W toku → `progress` (In Progress), Czeka na mnie → `waiting` (To be discussed), Do sprawdzenia →
  `review` (Fixed — krok człowieka: test ręczny albo scalenie), Gotowe → `done` (Verified); ticket — `todo` →
  Open, `w-toku` i `review` (review AI to wciąż W toku) → In Progress, `czeka` → To be discussed, `gotowe` →
  Fixed (Verified, gdy karta, która go prowadzi, stoi w Gotowe — także przeniesiona tam ręcznie: koordynatora
  karty szukamy po `derived`, `session` karty i kluczu `card` planu), `odrzucone` → `rejected` (Won't fix).
  Ticket `gotowe` z samej pracy podagentów bez przejścia review (poprawki po review skończone, ponowne review
  jeszcze się nie zaczęło) przy działającym koordynatorze, który używa review, to wciąż In Progress. Powrót do
  `open` po stanie rozwiązanym to `reopened` (Reopened), nie Open.
- **Pierwszeństwo:** ticket przed kartą tego samego zgłoszenia (dokładniejszy), chyba że karta stoi w Gotowe
  (ręczne Gotowe wygrywa) albo została ręcznie przeniesiona (`moved-from`) po ostatniej pracy nad ticketem
  i zmianie planu. Karta połączona ze zgłoszeniem-parasolem synchronizuje się sama. Ten sam ticket u kilku
  koordynatorów — najświeższa praca. Stan ticketu wzięty z samego YouTracka (`source: tracker`) się nie liczy,
  więc nic nie wraca do YouTracka, co z niego przyszło (bez ping-ponga).
- **Tylko przejścia:** plik stanu `$XDG_STATE_HOME/tide/youtrack-sync.json` (0600; bez tokenu) pamięta dla
  zgłoszenia ostatni stan zapisany albo przyjęty przez Tide (`state`), jego klucz (`key`), `updated` zgłoszenia
  z tej chwili, czas (`ts`) i ewentualny konflikt; do tego `seen` — zgłoszenia już pokazane w imporcie — i `importBaseline` (punkt odniesienia licznika nowych). Zapis
  idzie, gdy klucz, którego chce Tide, różni się od zapamiętanego i trzyma się 5 s (seria zmian to jeden zapis).
  Stany chwilowe z pracy agentów — rozwiązane z ticketów i z kolumn wyliczonych (Do sprawdzenia, Gotowe po
  scaleniu) oraz To be discussed (każde pytanie o zgodę) — muszą się trzymać `stableMinutes` (10 min); na
  karcie widać wtedy ⟳ „zapisze „Fixed” w YouTracku, gdy stan utrzyma się 10 min”. Ręczne przeniesienie do
  Gotowe i powrót „do poprawy” idą po 5 s.
  Karta, która spadła do Do zrobienia albo Backlogu tylko dlatego, że zniknęła jej praca (koordynator poza
  oknem 7 dni, start usługi — `derived` bez kolumny), nie cofa zgłoszenia: liczy się jawne przeniesienie
  (`column-moved` nie wcześniej niż ostatni zapis Tide, z dokładnością do sekundy). `column-moved` stempluje
  `MoveCard` tylko przy prawdziwej zmianie kolumny (docelowa inna niż wyświetlana); zmiana kolejności w Do
  zrobienia go nie zmienia, więc nie zwalnia wstrzymania.
- **Przed zapisem** `GET /api/issues/{id}?fields=idReadable,summary,updated,resolved,project(shortName),customFields(name,$type,value(name))`:
  stan już taki, jakiego chce Tide — tylko zapamiętanie; stan inny niż zapamiętany — **konflikt** (ktoś zmienił
  go ręcznie): bez zapisu, notka na karcie (⚠, „YouTrack: stan zmieniony ręcznie — w YouTracku „…”, Tide: „…””),
  wpis w `errors` i w „Projekty i tagi”, aż użytkownik wybierze „Weź z YouTracka” (Tide przyjmuje stan, zapis
  dopiero przy następnej zmianie karty) albo „Nadpisz z Tide” (`ResolveYouTrack`), albo YouTrack sam dojdzie do
  stanu Tide (sprawdzenie co 5 min i przy każdej nowej zmianie). Zapis:
  `POST /api/issues/{id}?fields=…` z `{"customFields": [{"name": "State", "$type": "StateIssueCustomField",
  "value": {"name": "In Progress"}}]}` — nazwa i `$type` pola z odczytu (kształt sprawdzony odczytem prawdziwego
  zgłoszenia), wynik musi mieć nowy stan.
- **Pierwsze spotkanie** zgłoszenia (brak wpisu w pliku): zapis tylko wtedy, gdy Tide wie coś o pracy (nie
  `open`), zgłoszenie jest nierozwiązane (`resolved` puste), w innym stanie i nikt go nie zmieniał po połączeniu
  (`updated` najwyżej minutę po założeniu karty albo pierwszym zdarzeniu ticketu); inaczej Tide przyjmuje stan
  z YouTracka. Zaimportowana karta w Backlogu nie cofa więc „In Progress” do „Open”.
- **Bez sieci:** YouTrack w sieci lokalnej bywa wyłączony — brak odpowiedzi, 5xx, 401/403/408/429 to ponowienie po
  5, 15, 60 i co 300 s (znaczek ⟳ i notka „YouTrack niedostępny · N czeka” przy nagłówku tablicy); przejście
  czeka w pliku stanu także przez restart usługi. Odmowa (inne 4xx, np. stan spoza workflow) — ⚠ z opisem
  YouTracka i ponowienie po 30 min albo przy nowym stanie.
- **Kiedy:** zegar co 1 s pyta silnik, co dojrzało; sieć w jednym wątku roboczym (`tide-youtrack`), wątek główny
  nigdy nie czeka. Pierwszy zapis po starcie dopiero po pierwszym odczycie koordynacji i 20 s
  (`TIDE_YOUTRACK_START_GRACE` w testach) — wcześniej karty nie mają jeszcze stanu z pracy agentów. Zegar
  synchronizacji jest prawdziwy także przy `--now`.
- **Import** (`YouTrackIssues`, `ImportYouTrack`; „Importuj z YouTracka” w „Projekty i tagi” i notka „N nowych
  zgłoszeń w YouTracku” przy nagłówku): zgłoszenia użytkownika — nierozwiązane, `for: me` oraz zgłoszone przez
  niego bez przypisania (`reporter: me Assignee: Unassigned`; w tym YouTracku zgłoszenia nie mają przypisania),
  oba zapytania w `importQueries` — pole `fields=idReadable,summary,description,updated,resolved,project(shortName),customFields(name,value(name))`.
  Lista z wyborem (klucz, tytuł, projekt tablicy po kluczu projektu YouTracka, stan, „nowe”, „już jest: K-13”);
  karta: Backlog, `youtrack`, tytuł (jedna linia, do 200 znaków), opis zwykłym tekstem (do 60 KB, bez znaków
  sterujących), projekt po kluczu. Bez duplikatów: zgłoszenie z kartą (po `youtrack`, także w Archiwum) jest
  pomijane. Lista pokazuje wszystko, co nie ma karty, z filtrem
  tekstu i przełącznikiem projektów YouTracka. Co `importCheckMinutes` (15, 0 — wyłączone) usługa w tle liczy
  „nowe” zgłoszenia — niczego sama nie zakłada. Licznik ma punkt odniesienia: pierwsze udane pobranie zapisuje
  w pliku stanu `importBaseline` (najnowsze `created` z tej listy, czas serwera), a nowe są tylko zgłoszenia
  założone później, bez karty i jeszcze niepokazane na liście (pokazanie listy je przegląda).
- **Token** jak w koordynacji: z pliku przy każdym zapytaniu, tylko w nagłówku, nigdy w dzienniku, błędach,
  pliku stanu ani odpowiedziach D-Bus. `--no-youtrack` wyłącza synchronizację i import (metody odpowiadają
  `unavailable`).

```json
{"url": "http://10.20.1.159:8111", "sync": true,
 "states": {"open": "Open", "reopened": "Reopened", "progress": "In Progress", "waiting": "To be discussed",
            "review": "Fixed", "done": "Verified", "rejected": "Won't fix"},
 "stateField": "State", "projects": {"APP5": {"states": {"review": "In Review"}}}, "stableMinutes": 10,
 "importQueries": ["for: me #Unresolved", "reporter: me Assignee: Unassigned #Unresolved"], "importCheckMinutes": 15}
```

Przejście z karty do pracy: „Otwórz w Sesjach” wywołuje `/Coordination ShowWindow(id)`, a „Przejdź do agenta”
wywołuje `/Agents ActivateSession(id)`. `BoardView.qml` i backend tablicy kierują te dwie akcje do usługi.

**Uruchomienie z karty** (widok Tablica, `apps/board/qml/LaunchDialog.qml`). Okno „Co ma zrobić agent?”
pyta „Jak zacząć?”: Planowanie (Pełne albo Lekkie), Mam plan, Tylko specyfikacja albo Od razu.
Otwiera się po przeniesieniu karty z Backlogu do Do zrobienia (myszą albo Ctrl+→), z „Uruchom agenta…”
na karcie w Do zrobienia i z „Uruchom” w popupie kafelka (`ShowWindow("launch:K-12")`).

„Uruchom agenta” (Ctrl+Enter) najpierw zapisuje zmieniony opis, dodatkowe projekty i wybrany plik planu
przez `UpdateCard`, tylko jeśli dane różnią się od zapisanych. Następnie Planowanie Lekkie, Mam plan i Od razu
wywołują `/Board StartCard`: krok biegnie w tle w przepływie karty, bez otwierania Konsoli. „Mam plan”
przekazuje `entry: import` i `planPath`; usługa zapisuje migawkę źródła bez edycji pliku projektu, zachowuje
istniejące ID i stany ticketów, uzupełnia niepełny plan w tle i kieruje propozycję do review oraz zatwierdzenia.
Pełne planowanie i Tylko specyfikacja używają dotychczasowej ścieżki Konsoli: `RenderLaunch` → `/Tasks Start`
→ zapis gałęzi, worktree i sesji na karcie przez `UpdateCard`. Pełny proces planowania w przepływie karty
należy do drugiego etapu. Karta uruchomiona z Backlogu przez popup idzie do Do zrobienia; błąd odpowiedzi
zostaje w oknie, a ponowienie nie zapisuje opisu drugi raz.
„Anuluj” po przeniesieniu oddaje kartę na jej miejsce w Backlogu (`MoveCard`), „Tylko przenieś”
zostawia ją w Do zrobienia bez agenta. Karta bez projektu z repozytorium — wyjaśnienie i wybór projektu
(`UpdateCard {project}` albo `{repo}`, który zakłada projekt).

**Zrzuty ekranu i model w oknie uruchomienia** (PT-43, decyzja 73) — to samo co w oknie „Nowe zadanie”.
Pod zadaniem pasek zrzutów (`Tide.ScreenshotStrip` z `widgets/lib`): obraz ze schowka (Ctrl+V w polu
zadania albo poza polami tekstowymi okna), pliki przeciągnięte na okno, „Zrób zrzut” (Spectacle; okno Tide
chowa się na czas zaznaczania), „×” usuwa. Backend widoku (`apps/board/backend.py`, `ImageTray`
z `apps/common/taskimages.py`) zapisuje je w katalogu zadania w `imagesDir` z `/Tasks Defaults`, a ścieżki idą
w `images` żądania `/Tasks Start` dla Konsoli albo `/Board StartCard` dla przepływu karty.
Udany start zostawia katalog agentowi; Anuluj, „Tylko przenieś” i zamknięcie okna Tide go usuwają. Obok
narzędzia wybór „☁ Chmura / ⌂ Lokalny” ze `Status()` obiektu `/Models` (odświeżany po `Changed`; domyślnie
zapamiętany `model` z `Defaults()`): przy „Lokalny” zamiast przełącznika narzędzia linia „Codex na
qwen3-coder:30b · Ten komputer” z odnośnikiem do widoku Modele, `RenderLaunch` dostaje program agenta
lokalnego jako `tool` (gałąź `codex/…`), a `Start` — `model: "local"`; przy kroku karty `StartCard` dostaje
wybranego wykonawcę z katalogu modeli. Niedostępny model albo brak programu
(`tools` z `Defaults()`) blokuje start z powodem w stopce. Zrzuty nie są zapisywane w karcie.

**Dodatkowe projekty** (PT-28). Wiersz „Dodatkowe projekty” w oknie: chipy wybranych (✕ usuwa) i pole
„+ dodaj projekt albo repozytorium” z listą projektów tablicy z istniejącym repozytorium (bez głównego
i już wybranych), potem repozytoriów z katalogu projektów, które nie są jeszcze projektami (wybranie
założy projekt). Okno startuje z `projects` karty. Gdy wybór różni się od karty, pierwszy zapis to
`UpdateCard {description?, projects}` (ścieżki repozytoriów zakładają projekty). Przy wejściach Konsoli `Start`
dostaje `addDirs` z odpowiedzi `RenderLaunch`; w przepływie karty dodatkowe projekty są tylko do odczytu.
Stopka dopisuje „Pracuje też w: …”. Na karcie tablicy dodatkowe
projekty to małe chipy „+ mobileapp” obok nazwy projektu. W ścieżce Konsoli agent pracuje w worktree
głównego projektu, a w dodatkowych repozytoriach zakłada gałąź o tej samej nazwie.

**Plan i specyfikacja na karcie** (`CardDocs(id)`, `board/docs.py`, PT-5). Panel karty w widoku Tablica
ma zakładki „Opis” | „Plan” | „Specyfikacja” — dwie ostatnie tylko wtedy, gdy usługa znalazła dokument.

- **Plan** (pierwsze pasujące): plan z `card: K-12` we frontmatterze (standard 1.1; przy kilku —
  niezakończony przed zakończonym, potem świeższy `updated`), inaczej plan koordynatora połączonego
  z kartą (`derived.coordinator` → `plan.path` jego tablicy w `/Coordination`, także bez klucza `card`).
  Szukamy w `docs/agents/plans/` repozytorium projektu karty i każdego jego worktree (lista z plików
  `.git/worktrees/*/gitdir`, bez wołania gita) oraz w `<tablica>/docs/<projekt>/plans/` (karta bez
  projektu — `docs/_inbox`; tablica z `--board-dir`, inaczej `$TIDE_BOARD_DIR`). Ten sam plik w kilku
  worktree to jeden plan — wygrywa kopia z nowszym `updated`, potem nowszy plik. Karta bez repozytorium,
  połączona z koordynatorem (np. przez `session`), korzysta z repozytorium koordynatora.
- **Specyfikacja**: pole `spec` karty (bezwzględne albo względne do repozytorium, jego worktree, katalogu
  tablicy albo `docs/<projekt>` w tablicy), inaczej plik w `docs/agents/specs/` repozytorium i worktree
  albo `<tablica>/docs/<projekt>/specs/` nazwany od ID karty (`k-12.md`, `k-12-…`, `k-12_…`) albo
  z `card: K-12` — gotowa (`status` jak w „Gotowej specyfikacji”) przed szkicem, potem najnowsza.
- **Bezpieczeństwo**: tylko zwykłe pliki `.md`, `.markdown`, `.txt`, których ścieżka po realpath leży
  w repozytorium projektu, jego worktree albo w `<tablica>/docs` — dowiązanie wychodzące poza nie,
  FIFO i inne rozszerzenia odpadają (pole `spec` i plan koordynatora — z ostrzeżeniem w `warnings`).
  Z pliku najwyżej 512 KB (dłuższy — `truncated: true` i dopisek na końcu treści), z katalogu najwyżej
  400 plików. Plik czytamy ponownie tylko po zmianie rozmiaru albo czasu, listę katalogu — po zmianie
  czasu katalogu, więc odświeżanie panelu to kilka `stat()`. Odczyt idzie w jednym wątku roboczym,
  odpowiedź D-Bus jest opóźniona (jak w `/Docker`).
- **Treść do pokazania** (`markdown`, `manualTest`): bez frontmattera, bez pierwszego nagłówka `# …`
  (tytuł jest w nagłówku panelu), przy planie bez tabeli ticketów (jest w `tickets`), z sekcją
  „Co przetestować ręcznie…” wyjętą do `manualTest` (do nagłówka tego samego albo wyższego poziomu) —
  poza tym **źródłowy, nieoczyszczony** Markdown. Bezpiecznie pokazuje go dopiero okno (niżej): ten
  sam parser, który renderuje (md4c w Qt), decyduje, co jest obrazem albo HTML-em — wyrażenia
  regularne po stronie usługi rozjeżdżały się z nim (niedomknięte odwrotne apostrofy, płoty w listach,
  opis obrazu w kilku liniach). Usługa działa na `QCoreApplication`, a `QTextDocument` potrzebuje
  `QGuiApplication`, więc renderowanie jest w oknie. Wzorce w `docs.py` (i `_plain` w `plan.py`) są
  liniowe — linia 512 KB z tysiącami `![`, `[`, `<!--` to milisekundy; treść do pokazania liczona raz na
  wersję pliku. Kolejne prośby o tę samą kartę w czasie odczytu czekają na ten sam wynik.

Odpowiedź: `{ok: true, card, plan, spec, others, warnings}`; zły ID — `bad-request`, nieznana karta —
`not-found` (kształt błędów jak w zapisach).

| Pole | Opis |
| --- | --- |
| `plan` | `{path, title, status, updated, updatedTs, card, coordinator, link, where, repo, mtime, tickets, cardTickets, errors, markdown, manualTest, truncated}` albo `null`; `link`: `card` (klucz `card`) albo `coordinator`; `where`: `repo`, `worktree`, `board`; `repo` — główny katalog repozytorium projektu (pusty bez repozytorium); `tickets`: `[{id, title, state, branch, agent, notes}]` z tabeli planu; `cardTickets` — `derived.tickets` (karta obejmuje część ticketów koordynatora; pusta — całość); `errors` — błędy planu z `coordination/plan.py` |
| `spec` | `{path, title, status, ready, updated, card, link, where, mtime, markdown, manualTest, truncated}` albo `null`; `status` z frontmattera małymi literami, `ready` — gotowa; `link`: `field`, `card`, `name` |
| `others` | do 8 dalszych dokumentów karty (starsze plany z `card`, pozostałe specyfikacje), od najnowszego: `[{kind: plan\|spec, path, title, status, updated}]` |
| `warnings` | zdania po polsku: plik z pola `spec` nie istnieje albo leży poza dozwolonymi katalogami, plan koordynatora poza nimi |

`--print-card-docs K-12` wypisuje to samo bez D-Bus (bez koordynatorów — plan tylko z klucza `card`).

Panel (`apps/board/qml/CardPanel.qml`, `CardDocView.qml`, `MarkdownBlock.qml`, logika `cardDocs.js`):
ostatnio wybrana zakładka zostaje dla kolejnych kart, póki działa okno Tide (karta bez takiego dokumentu
pokazuje „Opis”); przy dokumencie panel ma 860 px (przy węższym oknie — tyle, ile jest), „Rozwiń” — szerokość okna bez
marginesów. CardDocs idzie przy otwarciu karty, po każdej nowej migawce (`Changed` — także plik
w `docs/` tablicy) i co 5 s, póki panel jest otwarty (plan w repozytorium projektu nie zmienia
migawki); treść podmienia się tylko przy innej odpowiedzi, więc przewinięcie zostaje. „Otwórz plik”
(`QDesktopServices`) i „Pokaż w katalogu” (`org.freedesktop.FileManager1.ShowItems`, bez niego —
katalog) przyjmują w backendzie wyłącznie ścieżki z odpowiedzi CardDocs; link z Markdownu otwiera się
tylko jako http(s) (inne schematy i ścieżki — sama podpowiedź). Panel nie prosi o CardDocs, póki
poprzednia odpowiedź dla karty nie wróciła.

Renderowanie (`apps/board/backend.py: render_markdown`, `renderMarkdown` dla QML, pamięć ostatnich 24
dokumentów): `QTextDocument.setMarkdown(…, MarkdownDialectGitHub | MarkdownNoHTML)` — HTML ze źródła
zostaje tekstem; każdy fragment z `QTextImageFormat` (także obraz z definicji odnośnika i opis z kilku
linii) → kursywą „(obraz: opis)”; link inny niż http(s) → zwykły tekst; nagłówki z odstępem nad nimi,
linki w sky; `toHtml()` bez stylu `<body>` (krój z TextEdit) trafia do `MarkdownBlock.qml` jako
`RichText` — jedyne miejsce widoku z RichText. Druga warstwa: silnik QML okna Tide ma menedżera sieci,
który odmawia każdego żądania poza `file:`, `qrc:`, `data:` (`apps/tide/tide.py: guard_network`), więc
nawet przeoczony obraz niczego nie pobierze.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.CardDocs K-12
/usr/bin/python3 daemon/tide-daemon.py --print-card-docs K-12 --board-dir /tmp/kopia-tablicy
```

Logika bez Qt jest w `tide_daemon/board/` (`marker.py`, `frontmatter.py`, `model.py`, `flows.py`,
`store.py`, `derive.py`, `docs.py`, git w `repo.py`), obserwacja katalogu, przeliczanie stanu kart i odpowiedzi
D-Bus w `board_monitor.py`.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.Snapshot
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.CreateCard '{"title": "Eksport do PDF", "repo": "/home/user/DevWorkspace/LogViewer"}'
/usr/bin/python3 daemon/tide-daemon.py --print-board            # migawka bez D-Bus, niczego nie zakłada
/usr/bin/python3 daemon/tide-daemon.py --print-board --board-dir /tmp/tablica-testowa
```

### Zatwierdzanie planu karty (PT-78)

`StartCard(K-12, {"entry":"plan","planPath":"/ścieżka/do/plan.md"})` przyjmuje propozycję
z repozytorium projektu, dokumentów tablicy albo katalogu wyjściowego kroku tej karty. Kartki leżą
w `tickets/1.md`, `tickets/2.md` obok planu. Usługa sprawdza standard 1.2, wykonawców, recenzentów,
kartki z kryteriami, blokery i cykle. Zapisuje `plan.checked` z listą błędów albo `plan.proposed`
z migawką w dzienniku karty; poprawny plan daje kolumnę „Czeka na mnie” i fale w panelu.
Na tym etapie numery `PT-n` nie powstają.

`ApproveCardPlan(K-12)` rezerwuje numery pod trwałymi kluczami żądania, zapisuje `plan.approved`
z mapą `#n → PT-n` i zmienia nazwy kartek w migawce. Zatwierdzony plan 1.2 i kartki są commitowane
na gałęzi karty pod blokadą repozytorium, przez piaskownicę gita. Dzięki temu istniejący odczyt
planów ze wszystkich worktree widzi nowe ID w Sesjach, raporcie i kontroli numerów. Ticket startuje
dopiero po scaleniu wszystkich swoich blokerów; każdy przechodzi review i scalenie z PT-59.
Przerwanie między rezerwacją, wpisem mapy i commitem planu odtwarza ten sam plan i numery.

### Planowanie Lekkie i zasady przydziału (PT-79)

`StartCard(K-12, {"entry":"planning","mode":"light","executor":"Opus","preferredProvider":"mixed"})`
uruchamia krok `planning` w tle. Okno zlecenia ma wybór planującego z katalogu, niezależny od preferowanego
dostawcy przyszłych ticketów; przy „Tylko lokalnie” pokazuje wyłącznie lokalnych planujących. Krok korzysta
z gałęzi i worktree karty, a wyniki zapisuje w swoim katalogu `out`. Nie otwiera Konsoli ani nie nadaje numerów.

Wejście zawiera kartę, zrzuty, katalog wykonawców z klasami, preferencję dostawcy oraz migawkę zasad przydziału.
Dodatkowe projekty są dostępne wyłącznie do odczytu; nie poszerzają katalogów zapisu piaskownicy. Szablon prowadzi
przez pełne sekcje `to-spec` i pionowe plasterki `to-tickets`, z blokerami i osobną kartką „Dla recenzenta” na ticket.
Nadpisuje publikację ze skilli: `spec.md`, `plan.md`, `tickets/1.md` itd. i `result.json` trafiają do `out`, bez GitHub
Issues, trackera i lokalnego `.scratch`. Propozycję czyta ten sam walidator co PT-78, z dodatkowym ograniczeniem
źródła do katalogu wyniku konkretnego kroku. Poprawny wynik daje plan do zatwierdzenia; zatwierdzenie prowadzi dalej
przez istniejące rezerwacje, review ticketów i scalanie. Review samego planu przed pokazaniem dodaje PT-80.

Błędny format zapisuje `plan.checked` i uruchamia `plan-fix` z błędami i poprzednią propozycją, najwyżej dwie poprawy.
Po dwóch nieudanych poprawach karta czeka na użytkownika z błędami w panelu; można zlecić nowe planowanie. Awaria
programu, pytanie i brak zgody korzystają z istniejącego kontraktu kroków (jedno ponowienie awarii, potem decyzja
użytkownika). Zapisane `card.started` i pary „zamiar → wynik” pozwalają dokończyć start po restarcie bez duplikatów.

Zasady przydziału są krótkim tekstem w klasach `lekki`, `standard`, `mocny`, `lokalny`. Wspólne mieszczą się w
`board.md`, nadpisanie projektu w `projects/<slug>.md`, pod kluczem `assignment-rules` (napis JSON w płaskim
frontmatterze, tak by zachować wiele wierszy). Nowa tablica dostaje zaczyn, starsza używa tego samego zaczynu,
dopóki użytkownik go nie zapisze. `Snapshot.assignmentRules` podaje zasady wspólne, projektowe `assignmentRules`
podaje samo nadpisanie; pusty tekst oznacza dziedziczenie. „Projekty i tagi” edytuje oba zakresy przez `UpsertProject`:
`{globalAssignmentRules:"…"}` zapisuje wyłącznie wspólne, `{slug:"…",assignmentRules:"…"}` nadpisanie projektu.
Limit tekstu wynosi 8000 znaków. Wyczyszczenie nadpisania przywraca dziedziczenie wspólnych zasad.

### Fale i równoległość kroków (PT-62)

Fala jest poziomem grafu blokerów i za każdym razem wynika z tabeli planu; dziennik nie zapisuje numeru fali.
Gotowe do pracy tickety ruszają równolegle, kiedy **wszystkie ich blokery zostały scalone** do gałęzi karty.
Review, poprawki i ponowienia też korzystają ze slotów dostawcy, wspólnych dla wszystkich kart.
Operacje gita nadal wykonuje jedna blokada repozytorium z PT-59.

Domyślnie Anthropic i OpenAI (oraz inni dostawcy) mają po trzy sloty. Opcjonalne pole
`providerSlots` w `$XDG_CONFIG_HOME/tide/cardflow.json` pozwala nadpisać dodatnią liczbę slotów per dostawca,
np. `{"providerSlots":{"anthropic":3,"openai":2}}`. Niepoprawne wartości zachowują domyślny limit.
Lokalnie zawsze pracuje najwyżej jeden krok: zajmuje tor kolejki modeli na czas całej sesji.
Czekające wywołania raportu i opisów kart ruszają przed kolejnym krokiem; trwającego kroku nie przerywamy.
Po restarcie zajęty slot jest odtwarzany z dziennika.

Brak slotu zapisuje `step.waiting` z nazwanym powodem; start kroku usuwa ten powód z projekcji.
`derived.tickets` daje stany mini-paska i fale, `derived.flow.waveLabel` — znacznik „fala N z M”,
`derived.flow.waiting` — powody czekania, `derived.flow.steps` — przebieg z wykonawcami,
a `derived.flow.timeline` — uporządkowaną oś zdarzeń. Oś zawiera pola do prezentacji, bez konfiguracji punktu.
Pytanie jednego ticketu jest widoczne i można na nie odpowiedzieć, gdy inne tickety pracują.

Panel woła `/Board SetCardPaused(id, true)` dla „Wstrzymaj kartę” i tę samą metodę z `false` dla „Wznów”.
Wstrzymanie nie zmienia ręcznej kolumny ani nie przerywa trwających kroków; blokuje start nowych kroków.

### Przepływ karty: „Od razu” (PT-58)

Pierwszy plasterek przepływu karty (`docs/agents/specs/2026-10-28-przeplyw-kart.md`): wejście „Od razu” w oknie
zlecenia z wykonawcą z katalogu (Claude, Codex, lokalny albo inny dostawca). Okno woła
`/Board StartCard(id, {"entry": "now", "executor": "Sonnet", "preferredProvider": "anthropic", "images": [...]})` zamiast RenderLaunch → /Tasks Start;
odpowiedź przychodzi po starcie kroku (opóźniona, praca w wątku roboczym `cardflow_monitor.py`).

Zlecenie (`cardflow/engine.py`): najpierw sprawdzenie bez zapisu (karta z projektem i repozytorium, wykonawca
z katalogu, dostępny program i model, dla lokalnego także bieżący stan serwera i obecność modelu,
prefiks ticketów projektu przez `reserve(dry_run=True)`, gałąź główna, brak trwającego kroku karty
— błąd tu nie zostawia niczego), potem zdarzenia w dzienniku: `card.started`, pary „zamiar → wynik” dla rezerwacji
numeru (`coordination/reservations.py` — kontrola numerów widzi ticket), gałęzi i worktree karty
(`card/k-12-<opis>` od gałęzi głównej), gałęzi i worktree ticketu (`ticket/pt-7-<opis>` od gałęzi karty), startu
kroku, na końcu `step.started`. Worktree leżą w `<repo>-worktrees/`, katalog kroku obok worktree (`<worktree>.step`,
`in` — kartka, ustawienia, gitconfig; `out` — plik wyniku i notki). Krok to `agentcmd.background()` +
`Background.start()` (PT-76): `claude -p` albo `codex exec` w jednostce `systemd-run --user --scope`
`tide-step-<K-12>-<PT-7>-<n>`, z piaskownicą i kartką na stdin. Claude dostaje
`--permission-mode acceptEdits`, Codex opcjonalny `model_reasoning_effort`. Inny dostawca dostaje token
z pliku poza argumentami programu i dziennikiem; lokalny nie przechodzi do chmury. Bez Konsoli.

Kartka (`cardflow/handoff.py`): tytuł, opis i zrzuty karty, gałąź i katalog roboczy, zakazy (push, scalanie, zapis
poza katalogiem roboczym i wyjściowym) i plik wyniku `<krok>/out/result.json`:
`{"result": "done"|"question"|"denied"|"error", "question", "command", "reason", "notes": ["note.md"]}`.

Co chwilę (5 s) `tick()`: jednostka kroku nieaktywna → koniec kroku z pliku wyniku (brak pliku → błąd `no-result`,
zły → `bad-result`; usługa nie zgaduje wyniku z rozmowy), wynik i notki do `flow/<K-12>/steps/<krok>/` w tablicy;
„gotowe” → automatyczny krok review u innego modelu (PT-59), a dopiero werdykt APPROVE → scalenie ticketu do gałęzi karty (para „zamiar → wynik”, `cardflow/gitops.py`: `merge-tree --write-tree`,
`commit-tree`, `update-ref` z porównaniem — bez wykonywania treści agenta; worktree karty dostaje nowe drzewo przez
`read-tree -m -u`). Strażnik kroku: brak zmian plików w worktree (bez `.git`) i w dzienniku sesji przez
`stepGuardMinutes` (domyślnie 15, `$XDG_CONFIG_HOME/tide/cardflow.json`) → `stepstop.stop_step` (commit WIP gitem
w piaskownicy, a bez `bwrap` zmiany zostają w worktree) i błąd kroku `no-progress`.

Dziennik karty (`cardflow/journal.py`): `flow/<K-12>/journal.jsonl` w repozytorium tablicy, linia na zdarzenie
(`v` — wersja schematu, `seq`, `ts`, `card`, `type`), dopisuje wyłącznie usługa. Kopia w
`$XDG_STATE_HOME/tide/cardflow/<skrót>/<K-12>.jsonl` zapisywana pierwsza: dziennik cofnięty revertem albo zmieniony
z zewnątrz wraca z kopii z ostrzeżeniem w `errors` migawki. Zmiany z jednej chwili to jeden commit „K-12: card flow”;
`BoardMonitor.sync()` commituje je przed „sync external changes”.

Stan karty (`cardflow/state.py`): karta z dziennikiem dostaje `derived` tylko z dziennika (z polem `flow`: ticket,
krok, wykonawca, wynik, gałąź karty), karta bez dziennika — jak dotąd z `board/derive.py`. Ręczne kolumny wygrywają:
Gotowe i Archiwum bez stanu, Backlog — „wstrzymana”. W Do zrobienia: krok trwa albo scalanie czeka → W toku; pytanie,
brak zgody, błąd kroku, nieudane scalenie → Czeka na mnie; ticket na gałęzi karty → Do sprawdzenia. MoveCard karty
z dziennikiem: Backlog dopisuje `card.paused` (trwający krok się kończy), Gotowe i Archiwum zatrzymują krok z zapisem
zmian, odrzucają niescalony ticket (`ticket.rejected`) i kończą kartę (`card.finished`). Widok pozwala przeciągnąć
pracującą kartę z dziennikiem do Backlogu i Gotowych.

Restart usługi: kroki żyją we własnych jednostkach; po starcie `tick()` czyta dziennik, sprawdza jednostki
(`systemctl --user is-active`), kończy zaczęte akcje według par „zamiar → wynik” (scalenie — idempotentnie: ticket
już w gałęzi karty to sukces bez drugiego scalenia) i nigdy nie dopisuje zdarzenia drugi raz.

Sesja kroku nie jest wierszem agenta: `AgentMonitor.hidden` (`engine.is_hidden` — identyfikator sesji albo katalog
w worktree kroku) usuwa ją z /Agents Sessions(), kafelka Praca i powiadomień; KRunner ma własny indeks i ją znajduje.
Worktree trwających kroków i kart w toku są dla okna Worktrees „z agentem” (nie do usunięcia).

Podgląd karty: kafelek Praca pokazuje liczbę trwających kroków, a Tide Sesje rozwija kartę do ticketów, kroków i osi
zdarzeń. Panel karty podaje początek każdego trwającego kroku i ostatnią odnotowaną zmianę jego plików albo dziennika
sesji (przy pracy najczęściej raz na minutę). Co pięć sekund usługa sprawdza karty; po trzech minutach bez kroku,
otwartej akcji i nazwanego powodu czekania zapisuje `card.stalled`, pokazuje „stoi” i powiadamia raz. Pytanie, zgoda,
limit, bloker, zatwierdzenie planu i karta do sprawdzenia są nazwanymi stanami czekania. Powiadomienia przepływu
obejmują te decyzje oraz ponowiony błąd kroku; zakończone wcześniej pytanie nie powiadamia ponownie po restarcie.
Raport poranny czyta dzienniki kart, także kart przeniesionych do Gotowych, i wymienia scalone w okresie tickety
wraz z wykonawcą.

### Review i gałąź karty (PT-59)

`StartCard` przyjmuje nazwę recenzenta z katalogu (`reviewer`); okno „Od razu” pokazuje wykonawców o innym modelu.
Bez wyboru usługa wybiera domyślnego recenzenta z katalogu. W trybie `preferredProvider: "mixed"` dostawca recenzenta
musi być inny niż dostawca autora. Nazwa, program i model recenzenta trafiają do `card.started`.

Po wyniku `done` kroku ticketu usługa sprawdza czystość jego worktree i niezmienność refu, oblicza diff względem
gałęzi karty i uruchamia recenzenta w tym samym worktree pod nadzorem kroku. Kartka review zawiera opis karty,
diff, notkę wyniku ticketu oraz AGENTS.md/CLAUDE.md z repozytorium. Recenzent zapisuje `review.md` i JSON z werdyktem
`APPROVE` albo `REQUEST_CHANGES` oraz liczbami `blockers`, `majors`, `nits`. Usługa akceptuje APPROVE tylko przy zerze
BLOCKER i MAJOR, ponownie sprawdza worktree i ref, a werdykt zapisuje jako `review.verdict` w dzienniku. Werdykt i
liczby widać w panelu karty.

Przy REQUEST_CHANGES autor dostaje krok poprawek z uwagami i następuje ponowne review. Po trzeciej nieudanej rundzie
karta zapisuje `review.question` i czeka na decyzję użytkownika w panelu. `retry` zapisuje `review.decision` i pozwala
na jedną dalszą rundę; następna nieudana runda ponownie pyta. `reject` zapisuje decyzję i `ticket.rejected`, bez scalenia.
Jeżeli restart rozdzieli te dwa zdarzenia, usługa dopisuje brakujące odrzucenie przy następnym przebiegu; ta sama
odpowiedź `reject` może być bezpiecznie ponowiona.
APPROVE uruchamia scalenie tylko zatwierdzonego commita ticketu (ref jest sprawdzany pod blokadą gita repozytorium)
i usuwa worktree ticketu
pod tą samą blokadą. Katalogi wejścia i wyniku kroków leżą obok worktree i zostają w repozytorium tablicy jako kopie
wyniku. Błąd przygotowania kolejnego etapu jest zdarzeniem `flow.error` i pokazuje kartę jako oczekującą; usługa
ponawia etap w kolejnych przebiegach.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.StartCard K-12 '{"entry": "now", "executor": "Sonnet"}'
```

### Decyzje po kroku w tle (PT-65, PT-66, PT-68)

Wynik `question` z `result.json` pokazuje pytanie w „Czeka na mnie”. Odpowiedź z panelu zapisuje zdarzenie
`step.decision` i wznawia ticket w tym samym worktree oraz sesji. Krok dostaje kartkę z pytaniem, odpowiedzią i nową
ścieżką wyniku. Powiadomienie powstaje raz na zakończony pytaniem krok (`step.notice` w dzienniku); kliknięcie otwiera
tablicę. Po wznowieniu pozostają commity i zmiany z wcześniejszego podejścia.

Wynik `denied` zawiera dokładne polecenie. „Zezwól i wznów” dopisuje tę postać polecenia do listy projektu i wznawia
krok; „Odmów” wznawia go z informacją o odmowie. Listy `allowed-commands` i `denied-commands` są zapisane w nagłówku
pliku projektu tablicy jako tablice JSON i edytowalne w „Projekty i tagi”. Claude dostaje z nich reguły
`--allowedTools` i `--disallowedTools`, a odmowa Bash z końcowego wyniku CLI staje się `denied`, gdy brak własnego
pliku wyniku. Codex działa w piaskownicy `workspace-write`, lecz listy poleceń dostaje tylko w kartce jako wskazówkę:
CLI nie blokuje automatycznie każdej komendy spoza listy. Domyślna lista dopuszcza częste odczyty
oraz pracę lokalną, a dla projektu Tide zabrania `make install`. Reguła zabroniona ma pierwszeństwo; panel nie może
zatwierdzić takiego polecenia. Zgoda dotyczy pełnego polecenia, bez rozszerzenia na cały program; panel odmawia
składni złożonej operatorami powłoki oraz poleceń dłuższych niż 300 znaków, które pokazuje bez skrócenia.

Błąd kroku (`error`, brak wyniku, przekroczony czas lub brak postępu) zapisuje `step.retry` i powoduje jedno
automatyczne ponowienie. Drugi błąd pokazuje „Ponów” i „Zmień wykonawcę”. Obie decyzje zachowują worktree;
zmiana wykonawcy zakłada nową sesję i jest używana także w kolejnych poprawkach po review. Krok lokalny nie
przełącza się sam do chmury. `stepTimeoutMinutes` w `$XDG_CONFIG_HOME/tide/cardflow.json` ogranicza jedną próbę
(domyślnie 240 minut); po limicie usługa zatrzymuje jednostkę przez `stepstop.stop_step` i zachowuje zmiany.
Strażnik braku postępu podaje w karcie czas, od którego krok stał.

Zgoda na łańcuch poleceń (PT-124): przy wyniku `denied` usługa rozkłada polecenie (`board/chain.py`) na
pojedyncze polecenia — po `&&`, `||`, `|`, `|&`, `;`, `&`, nowej linii, grupach `( … )` i `{ …; }`, z wnętrzem
`$( … )`, odwrotnych apostrofów, `<( … )` (także w cudzysłowie podwójnym) i skryptu `sh -c`/`bash -c`/`eval` jako
osobnymi poleceniami. Lista trafia do `step.finished.commands` i do decyzji `permission` w `flow.decisions[]` jako
`commands[] = {text, readOnly, stepDir, forbidden, reason, canAllow, preselected}`. `forbidden` wynika wyłącznie
z `is_forbidden` i listy `denied-commands` projektu (bez reguł wbudowanych), a `reason` podaje pasującą regułę albo
powód, dla którego polecenia nie da się zatwierdzić. Odczyt własnego `<krok>/in` i `<krok>/out` jest zawsze dozwolony
(Claude dostaje dokładne wzorce `ls`/`cat` tych katalogów) i nie trafia na listę. „Zezwól na wybrane i wznów”
wysyła `allow: [indeksy]`; usługa liczy listę od nowa z bieżącymi zakazami i odrzuca indeks spoza zakresu
(`bad-allow`) albo wskazujący polecenie zabronione (`forbidden-command`), dopisuje do listy projektu dokładnie
wybrane polecenia, a wznowiony krok dostaje listę dozwolonych i pominiętych. „Kontynuuj bez tych poleceń” to `deny`,
„Zrób zamiast tego…” — `deny` z `instead`. Łańcuch odrzucany jest automatycznie tylko wtedy, gdy na żadne z jego
poleceń nie da się zezwolić.

Decyzje przekazuje metoda D-Bus `/Board ResolveCardStep(id, request_json)` z `choice` równym `answer`, `allow`,
`deny`, `retry` lub `change-executor`; odpowiednio podaje się `answer` albo `executor`, a przy łańcuchu `allow`
(z `choice=allow`) albo `instead` (z `choice=deny`). Ponowione kroki dostają
osobne katalogi `.resume-<step>.step` i pliki wyniku; dziennik pozwala odtworzyć decyzję po restarcie usługi.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.ResolveCardStep K-12 '{"choice": "answer", "answer": "ISO 8601"}'
```

### Czyja wina błędu kroku i „Opisz problem i ponów” (PT-125)

Zakończenie kroku z wynikiem `error` albo `stopped` ma w `step.finished` pola `fault` (`tide`, `model`, `user`,
`provider`, `unknown`) i `faultCause`, liczone w chwili wyniku z powodu i z dziennika tego kroku (`out/step.log`,
`cardflow/faults.py`). `tide` daje tylko twarda przesłanka: brak pliku wyniku (`no-result`) i odmowa zapisu w katalogu
wyjściowym tego kroku w wyniku narzędzia albo polecenia (odmowa `Write`/`Edit` w `permission_denials`, „Read-only file
system”, „Permission denied” przy tej ścieżce) — tekst pisany przez agenta nie jest dowodem — albo powody ustawiane
przez Tide (`start`, `lost`). Kod limitu (429, zdarzenie limitu) daje `provider`/`limit`, jawny błąd API autoryzacji
(401, 403) albo awarii (5xx, przeciążenie) — `provider`/`auth` albo `outage`, zły plik wyniku albo werdykt — `model`.
Gdy dziennik nie rozstrzyga, `unknown`, a okno mówi wprost, że nie wiadomo, czyja to wina. Limit nigdy nie tworzy
decyzji w `flow.decisions[]` (D3); starych pól panelu (`flow.action`, `flow.question`, `flow.command`,
`flow.canAllow`, `flow.error`, `flow.limit`, `flow.fault`) już nie ma (PT-135) — `derived.flow` niesie decyzje
wyłącznie w `decisions[]`; `derived.question` i `note` zostają na poziomie karty. Kafelek błędu z winy
Tide pisze „błąd Tide w kroku X”.

Błąd `tide` albo `unknown` ma w decyzji pole „Opisz problem i ponów” (`reply` z `method: "ReportStepFault"`,
`preview: "PreviewStepFault"`, `field: "description"`). Okno najpierw pokazuje podgląd karty, potem zapisuje:

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `PreviewStepFault(s id, s request)` | `→ s` | `{"step", "description"}` → `{ok, preview: true, action: "create"\|"append", card, project, title, description, signature, fault}` bez żadnego zapisu |
| `ReportStepFault(s id, s request)` | `→ s` | zakłada kartę w Backlogu projektu `plasma-tide` (`create_card`) albo dopisuje wystąpienie do otwartej karty o tym samym podpisie (`update_card`), zapisuje `step.reported` i ponawia krok przez `ResolveCardStep` (`retry`); odpowiedź jak `ResolveCardStep` z `reported`, `action`, `project` |

Projekt docelowy jest stały (`plasma-tide`) — żądanie go nie podaje; bez tego projektu na tablicy zgłoszenie kończy
się błędem `no-report-project`. Podpis błędu to rodzaj kroku, `fault` i przyczyna (`plan-review/tide/write-denied`),
zapisany w treści karty linią „Podpis błędu: `…`”. Karta otwarta to karta nie zarchiwizowana i nie scalona (jej
przepływ bez `card.main-merged`); dla zarchiwizowanej albo scalonej powstaje nowa. Wycinek pochodzi wyłącznie
z dziennika kroku (ostatnie 30 zdarzeń po ludzku) i przed zapisem jest maskowany: wartości zmiennych środowiskowych
(`NAZWA=…`), znane tokeny (`sk-…`, `ghp_…`, `github_pat_…`, `xox…`, `perm:…`, JWT, `Bearer …`), pary klucz–sekret
i wartości sekretnych zmiennych środowiska usługi. Ten sam opis użytkownika też przechodzi maskowanie.

### Pełna karta: fazy, powody czekania i trwające kroki (PT-128)

Karta z dziennikiem dostaje w `derived` pola pełnej karty (`cardflow/fullcard.py`); widok
`apps/common/qml/card/CardFullView.qml` tylko je wyświetla:

- `phases[7]` — `{id, label, state, executor, ts, note}` w stałej kolejności: `order` (Zlecenie), `planning`,
  `plan-review`, `plan-approval`, `tickets`, `closing` (Domknięcie), `main-merge` (Scalenie do master). `state`:
  `done`, `now`, `you` (czeka na decyzję z `flow.decisions[]`), `hold` (czeka na limit, nie na użytkownika), `todo`,
  `skipped` (planowanie przy wejściu „Od razu”, review planu zatwierdzonego bez review). `executor` to nazwa
  wykonawcy albo `user` dla faz użytkownika, `ts` — chwila zdarzenia fazy (ISO 8601), `note` — dopisek
  (np. „fala 2 z 4”, „prosi o zmiany”). Karta w „Czeka na mnie” bez decyzji w żadnej fazie ma `you` w bieżącej fazie.
- `tickets[].waitingOn` — `{kind: "user"}`, `{kind: "blocker", ticket}` (pierwszy niescalony bloker) albo
  `{kind: "limit", until}` (godzina odnowienia, gdy znana) albo `null`; przy kilku powodach naraz wygrywa użytkownik,
  potem bloker, potem limit. Przed zatwierdzeniem planu zawsze `null`.
- `tickets[].status` i `statusLabel` — ludzki stan (`w planie`, `w kolejce`, `pracuje`, `review`, `poprawki`,
  `scalanie`, `czeka na Ciebie`, `czeka na limit do 16:30`, `czeka na PT-1`, `scalony`, `odrzucony`), do tego
  `executor` i `reviewer` z przydziału albo z planu.
- `flow.running[]` — tylko trwające kroki (`id`, `ticket`, `kind`, `kindLabel`, `executor`, `mode`, `startedTs`,
  `lastActivityTs`, `canEnter`); `flow.headline` — jedno zdanie do „Twój ruch”, gdy `flow.decisions[]` jest puste.

Zdarzenie `step.waiting` niesie `cause` (`quota` — okno limitu, `slots` — kroki dostawcy naraz, `local` — kolejka
modeli lokalnych, `paused` — karta wstrzymana) i `until` (godzina odnowienia przy `quota`). Starsze zdarzenia bez
`cause` rozpoznawane są po treści powodu. Tickety i fale planu liczą się także wtedy, gdy krok stoi na limicie.

### Limit dostawcy: zatrzymane kroki i przeniesienie na Codex (PT-130)

Limit nie jest decyzją (D3): nie ma go w `flow.decisions[]`. **Zmiana reguły kolumn** (jedyna w tym tickecie):
karta z krokiem zatrzymanym przez limit (`step.finished` z `reason: limit`) stoi w „W toku” ze znacznikiem „czeka
na limit do HH:MM”, nie w „Czeka na mnie”. Wyjątek: gdy zapis zmian (WIP) wymaga naprawy, karta zostaje w „Czeka na
mnie” — to praca dla użytkownika, nie limit. Bez wyboru użytkownika Tide czeka na odnowienie sam: po odnowieniu
wznawia krok tym samym dostawcą (jak po wyborze `wait-limit`), a kolumnę liczą dotychczasowe reguły. Tryb
`auto` z ustawionym wykonawcą nadal przenosi krok sam.

Pola w `derived.flow`:

- `stoppedByLimit[]` — `{step, ticket, wipFiles, provider, until}` dla każdego zatrzymanego kroku; `wipFiles` to
  liczba plików w commicie WIP.
- `otherProviders[]` — `{provider, remaining}`: pozostały limit (%, najniższe świeże okno) innych dostawców
  chmurowych z odczytu Limitów AI (`cardflow/limits.py`); dostawca bez świeżego odczytu nie trafia na listę.
- `limitTransfer` — `null` bez zatrzymanych kroków, inaczej `{available, provider, label, text, executor, cost,
  reason}`. Dostępne (`available: true`), gdy inny dostawca ma limit powyżej progu i wykonawcę w katalogu
  (preferowany: wykonawca z ustawień przekazania, potem ta sama klasa co zatrzymany krok). `cost` to koszt
  przeniesienia: „2 zatrzymane kroki i 17 plików WIP do przekazania; po przeniesieniu Codex ma jeszcze 91%
  limitu.” — liczby kroków i plików WIP oraz pozostały limit docelowego dostawcy, bez przeliczeń w walucie.
  Gdy nie ma dostawcy z wolnym limitem: `available: false` i jedno zdanie w `reason`.

„Przenieś na Codex” w pełnej karcie wysyła istniejące `ResolveCardStep` (`choice: transfer`, `step`, `executor`)
po jednym razie na zatrzymany krok; koszt jest widoczny nad przyciskiem przed kliknięciem.

## Opiekun karty (K-37)

Opiekun karty to krok w tle rodzaju `caretaker` („opiekun”), który Tide uruchamia sam przy problemie karty, zanim
zapyta użytkownika (PT-143). Cały opiekun bez Qt i bez dysku jest w `daemon/tide_daemon/cardflow/caretaker.py`:
publiczne `problems()`, `owned()`, `brief()`, `parse_result()`, `problem_id()` i rejestry `TRIGGERS`, `ACTIONS`,
`HISTORY_LABELS`, `BRIEF_SECTIONS`. Kolejne tickety tylko dopisują wpisy w rejestrach i własne `####` w podsekcjach
poniżej. Silnik ma jedno wejście: `FlowEngine._caretake(card, st)` na końcu `_tick_card` — po istniejących
automatach (ponowienie, mocniejszy wykonawca, odpowiedź o środowisko, auto-odmowa) i przed `_queue_notices`;
krok opiekuna kończy się istniejącym `_poll`/`_finish` (gałąź `_finish_caretaker`).

Przebieg: wyzwalacz daje problem → `caretaker.started` (`problem`, `key` = `caretaker:<problem>:<n>`, `n`, `step`,
`target` — krok problemu, `root`, `ticket`, `trigger`, `watermark` — ostatni `seq` dziennika w chwili startu) →
istniejąca para „zamiar → wynik” `step-start` i `step.started` z `kind: caretaker` → wynik opiekuna w
`<krok>/out/result.json` → `step.finished` z `outcome` (wynik `parse_result`) → akcja z rejestru albo nieudane
działanie → `caretaker.escalated`. Schemat `caretaker.action` (`problem, step, target, ticket, action, params, ok,
error`) jest zdefiniowany na wykonane akcje inne niż oddanie decyzji; zapisują go kolejne tickety.

Stan i widok:

- Kroki opiekuna nie są w `FlowState.steps` (osobno `caretakers`, `caretaker_runs`, `caretaker_outcomes`,
  `caretaker_escalations`, `caretaking`), więc żaden wyzwalacz, „ostatni krok ticketu” ani decyzja ich nie widzą —
  bez rekursji. `running()` je obejmuje (sloty dostawcy, zatrzymanie przy Gotowe/Archiwum, `busy_reason`).
- Dopóki opiekun pracuje, decyzja problemu jest ukryta z `flow.decisions[]`, karta stoi w „W toku” ze znacznikiem
  „opiekun sprawdza <krok>”, a `_queue_notices` nie powiadamia (także w przebiegu, w którym krok skończył się błędem,
  zanim opiekun wystartował). `ResolveCardStep` dla tego ticketu odmawia (`no-step-question`, trwa krok tego ticketu).
- `derived.flow.caretaker` — `{enabled, working[], escalated[]}`: `working[]` to `{problem, step, target, ticket,
  label, startedTs}`, `escalated[]` — problemy oddane użytkownikowi, których decyzja czeka: `{problem, step,
  target, ticket, summary, recommendation, optionId, failed, error}`. `flow.running[]` ma krok opiekuna z
  `kind: caretaker`, `kindLabel: "opiekun"` (`labels.KIND_LABELS`), `canEnter: false`.
- Eskalacje są przechowywane według id problemu, także gdy `target` jest pusty. Widok i adnotacja decyzji
  korzystają z cyklu życia w `owned()`: pokazują tylko eskalację ostatniego działania przy otwartym problemie.
- Oddana decyzja błędu ma pole `caretaker{summary, recommendation, tried[], optionId, failed}` (`tried[]` —
  nieudane działania od ostatniego zamknięcia: `{step, action, ok, text}`); możliwość z `optionId` jest polecona
  i pierwsza, żadna nie jest wybrana (D10). Renderowanie w oknie decyzji to #15. Eskalacja powiadamia raz
  (zwykłe `step.notice` kroku problemu).
- Historia (`flow.history[]`, rodzaj `caretaker`, `actor: "caretaker"`): „Opiekun: oddaje decyzję — <streszczenie>”
  i „Opiekun: nie udało się wykonać działania „<akcja>” — <błąd>”.

**Id problemu i liczenie działań.** `problem_id` jest jedynym miejscem budowy id (bez `seq`): wyzwalacz z krokiem —
`<wyzwalacz>:<korzeń łańcucha parentStep>` (`error:K-37-PT-7-1`), powtórzenie przyczyny `repeat:<ticket>:<przyczyna>`,
bez kroku `<wyzwalacz>:<etap>` (`flow-error:<etap>`, `stalled:card`), czekająca decyzja `waiting:<id decyzji>`.
Jeden łańcuch kroków ma naraz najwyżej jeden problem — kolejny wyzwalacz na nim dołącza (`triggers[]`). `owned()`
jest jedynym liczeniem: kroki opiekuna z danym id od ostatniego zamknięcia (decyzja użytkownika bez pola `caretaker`
albo krok łańcucha zakończony `done`); zwraca `count`, `runs`, `next` (numer do klucza, nigdy się nie powtarza),
`open` i `handed`. Kolejne błędy ponowionego kroku to ten sam problem; decyzja użytkownika zeruje licznik bez
zmiany id. Dla problemu bez kroku decyzja użytkownika albo zwykły krok zakończony `done` zamyka problem
w obrębie wskazanego ticketu, a bez ticketu — całej karty. Wynik kroku opiekuna i decyzje automatów nie zamykają
problemu. Ponowne wystąpienie ma to samo id, licznik od zera i kolejny, niepowtarzalny numer `next`.

**Restart.** `caretaker.started` bez `step.started`: wynik `step-start` jest — `step.started` dopisuje
`_recover_started_steps`; zamiar bez wyniku rozstrzyga `_reconcile`; bez zamiaru — `_caretake` startuje krok z tym
samym id. Nieudany start (także „przerwane restartem”) jest nieudanym działaniem. Krok nigdy nie rusza drugi raz.

**Kiedy opiekuna nie ma** (dzisiejsza decyzja bez kroku opiekuna): karta wstrzymana, w Backlogu, Gotowych albo
Archiwum, błąd `login`, limit dostawcy, wina `user` i karta „Tylko lokalnie” (`preferredProvider: local`; decyzja
z dopiskiem „opiekun niedostępny dla pracy tylko lokalnej”, `flow.caretaker.enabled: false`).

### Wyzwalacze

`TRIGGERS` to lista funkcji `(st) -> [kandydat]`, kandydat `{trigger, step?, ticket?, stage?, cause?, decision?}`.
Do startu opiekuna wystarcza wpis w rejestrze, także dla problemu bez kroku (`flow-error:<etap>`, `stalled:card`):
`caretaker.started` ma wtedy pusty `target`, a krok opiekuna pracuje w worktree ticketu (gdy kandydat ma `ticket`)
albo w katalogu roboczym karty (`card-branch`); aktualność problemu przed startem sprawdza zawsze `problems()`.

#### Błąd kroku ticketu (`error`, PT-143)

Ostatni krok ticketu (`ticket`, `fix`, `review`, `conflict`) zakończony `error` po automatycznym ponowieniu
(`attempt ≥ 1`) i ewentualnym przejściu na mocniejszego wykonawcę, bez decyzji, bez kroku-następcy, bez
odrzucenia ticketu; bez `login`, limitu dostawcy i winy `user`. Id: `error:<korzeń łańcucha>`.

### Akcje

`ACTIONS` to `nazwa → Action(kinds, params, apply, description)`; `kinds` — wyzwalacze, przy których akcja jest
dozwolona (`"*"` — każdy), `params` — pola wyniku (`Param(required, minimum, maximum, hint, choices)`), `apply(port,
problem, params)` działa przez port silnika (`engine._CaretakerPort`). Walidacja jest w Tide (`parse_result`), nie
tylko w kartce: nieznana nazwa (`approve-plan`, `allow`, `merge`, `push`, `reject`, …) → `bad-caretaker-action`;
brak pliku albo zły format → `bad-caretaker-result`; `result` inny niż `done` → nieudane działanie (`question` niesie
treść pytania jako streszczenie, jak `escalate`; inne — `caretaker-result`); zatrzymany albo niewystartowany krok —
`caretaker-stopped`, `caretaker-not-started`. Długości: instrukcja/odpowiedź/uwaga do 4000 znaków, powód 1–600,
streszczenie do 1200. W PT-143 każde nieudane działanie od razu oddaje problem użytkownikowi (limit i ponawianie
dokłada #8).

#### `escalate` (PT-143)

Oddaje decyzję użytkownikowi: `summary` (wymagane, 1–1200 znaków), `recommendation` (wymagane, 1–600), `optionId`
(opcjonalne, id możliwości decyzji problemu). Jedyna akcja w rejestrze po PT-143 — nie ma akcji zatwierdzenia planu,
zgody, uruchomienia poza piaskownicą, scalenia, push ani odrzucenia ticketu.

### Kontekst

Kartka (`brief`) zaczyna się od „Jesteś opiekunem karty <K-…> w Tide.” i zawiera: przesłankę z `BRIEF_SECTIONS`
według wyzwalacza (zamaskowaną `faults.mask`), listę plików kontekstu, listę dozwolonych akcji z polami, zakazy i ścieżkę wyniku
(„Plik wyniku: …/out/result.json”). Krok opiekuna pracuje w worktree kroku problemu (tylko do odczytu) i ma osobny
katalog `<worktree>.caretaker-<krok>.step`.

#### Opis problemu z decyzji (PT-143)

Sekcja `error`: krok, wykonawca, próba, „co się stało” i „dlaczego” z decyzji błędu, powód zakończenia kroku i czyja
wina według dziennika kroku. Dziennik kroku i pozostałe pliki są w katalogu kontekstu (niżej).

#### Katalog `in/context/` (PT-153)

Przy starcie kroku opiekuna (w parze „zamiar → wynik” `step-start`, po przyjęciu przez `_admit`, więc nie przy każdym
obrocie pętli w kolejce do dostawcy) Tide buduje `<krok>/in/context/` modułem
`daemon/tide_daemon/cardflow/caretakerctx.py` (`build(problem, st, events, …)`; `collect` — to samo w pamięci, dla
rozmowy z opiekunem w #7). Kartka dostaje sekcję „## Kontekst” z bezwzględną ścieżką i opisem każdego pliku
(„(przycięty do limitu)” przy przyciętych). Pliki wspólne dla każdego wyzwalacza:

- `history.md` — historia karty po ludzku (`history.build`, od najstarszego wpisu, z próbami kroków),
- `journal.jsonl` — ostatnie 200 zdarzeń dziennika karty (JSON Lines, od najstarszego),
- `plan/plan.md`, `plan/source.md`, `plan/tickets/<id>.md` z katalogu karty w tablicy (bez planu — `plan/README.md`
  z dopiskiem) i `steps/<krok problemu>/prompt.txt` — kartka kroku problemu,
- dla każdego kroku problemu (łańcuch `parentStep` problemu i wszystkie kroki jego ticketu): `steps/<krok>/note.md`,
  `review.md`, `handoff.md`, `result.json` (z katalogu karty), `plan-review.json` (z `<krok>/out`) i `step-log.md` —
  ostatnie 300 zdarzeń `out/step.log` po ludzku (`faults.excerpt(…, limit=300)`, jak wycinek PT-125),
- `attempts.md` — próby kroków problemu (wykonawca, wynik, powód, czyja wina, decyzja) i poprzednie działania
  opiekuna przy tym samym id problemu (wynik, streszczenie, rekomendacja, błąd, `note.md` i `result.json` kroku
  opiekuna) — bez bieżącego kroku,
- `docs/agent-plan-standard.md`, `docs/sandbox-and-permissions.md` i `docs/AGENTS-przeplyw-karty.md` (sekcja
  „Przepływ karty” z `AGENTS.md`) — migawki dokumentów działającej usługi Tide: `tide-docs/` obok pakietu
  (`daemon/install.sh` kopiuje tam te trzy pliki) albo repozytorium Tide przy uruchomieniu ze źródeł
  (`caretakerctx.tide_docs_dir()`), więc dostają je też karty innych projektów; dopiero bez nich — z worktree kroku
  opiekuna, a bez pliku i tam — dopisek „(pominięto: …)”. Katalog dokumentów jest osobnym korzeniem tylko dla tych
  stałych nazw (inne źródła nie czytają z niego przez `ctx.read_text`).

**Rejestr źródeł.** `caretakerctx.SOURCES` — wyzwalacz → lista `Source(name, build)`; `"*"` — źródła każdego
problemu. Problem dostaje `"*"` i źródła wszystkich swoich wyzwalaczy (`triggers[]`). `build(ctx)` zwraca
`File(path, title, text=…)` albo `File(path, title, items=[…], head=…, trim=n)` (zdarzenia od najstarszego,
przycinane) i czyta pliki wyłącznie przez `ctx.read_text` / `ctx.read_tail`. Kolejne tickety dopisują własne wpisy
(np. błędy sprawdzania formatu planu) bez edycji wspólnych źródeł; wyjątek w źródle daje `errors/<źródło>.md`
zamiast przerwania startu.

**Granice.** Maskowanie wyłącznie `faults.mask`: każdy plik w całości, zdarzenia każde osobno przed liczeniem
rozmiaru, a w `journal.jsonl` każda wartość osobno (linie pozostają poprawnym JSON-em): pole, które `faults.mask`
uznaje za sekretne na próbce `nazwa=x` (`token`, `password`, `apiKey`…), traci całą wartość — także napis ze spacjami
i liczbę; pozostałe napisy przechodzą przez `faults.mask`, a liczba zmieniona przez `faults.mask` (np. równa wartości
sekretnej zmiennej środowiska) staje się `•••`. Końcówka `out/step.log` czytana od środka pliku zaczyna się od
pierwszej pełnej linii — sekret przecięty granicą odczytu nie traci nazwy zmiennej ani prefiksu tokenu przed
maskowaniem. Odczyt tylko z korzeni: katalog karty w tablicy (`flow/<K-…>`), worktree kroku opiekuna oraz worktree
i katalogi kroków problemu z dziennika. Korzeń, który sam jest dowiązaniem (np. cały `<worktree>.step` podmieniony na
dowiązanie do `$HOME`), odpada (`lstat`); przy odczycie korzeń jest otwierany z `O_NOFOLLOW` i porównywany
z tożsamością (`st_dev`, `st_ino`) z chwili sprawdzenia, a każdy składnik ścieżki — względem poprzedniego (`dir_fd`)
z `O_NOFOLLOW`; plik musi być zwykłym plikiem. Dowiązanie na dowolnym poziomie daje dopisek „(pominięto: dowiązanie
symboliczne / plik spoza katalogu karty i worktree)”, nie treść. Całość ma najwyżej 400 kB (`MAX_BYTES = 400 000`
bajtów): przycinane są najpierw najstarsze zdarzenia `journal.jsonl`, potem dzienników kroków, potem historii,
z pierwszą linią „(przycięto N najstarszych zdarzeń — limit kontekstu opiekuna 400 kB)”; gdyby to nie wystarczyło,
końcówki największych plików z dopiskiem „… (koniec pliku przycięty …)”, a plik, z którego zostałby sam dopisek,
wypada w całości — zamiast nich jeden `przycieto.md` („(pominięto N plików kontekstu w całości …)”), na który
budżet jest zarezerwowany z góry, więc limit obowiązuje przy dowolnej liczbie plików. Katalog kontekstu jest budowany
od nowa przy każdym starcie.

### Ustawienia

#### Na stałe (PT-143, do #9)

Opiekun jest włączony na stałe, wykonawca to najmocniejsza klasa preferowanego dostawcy karty (w trybie mieszanym
— dostawcy wykonawcy karty; domyślnie Opus albo Codex mocny). Piaskownica do #16: krok opiekuna nie ma żadnych
dozwolonych poleceń (`agentcmd.Step.write_out_only`: Claude — `--disallowedTools Bash` i zakaz edycji worktree
i repozytorium, bez `--allowedTools`; Codex — katalog roboczy i jedyny zapisywalny katalog to `<krok>/out`, a
narzędzia poleceń i kodu są wyłączone: `-c features.shell_tool=false`, `unified_exec`, `code_mode`, `js_repl`
— `agentcmd.CODEX_NO_COMMANDS`; plik wyniku Codex zapisuje łatką `apply_patch`; `/tmp` i `$TMPDIR` są jawnie
wykluczone z zapisu: `-c sandbox_workspace_write.exclude_slash_tmp=true`, `exclude_tmpdir_env_var=true`, bo
`writable_roots` tylko dopisuje katalogi do domyślnych), zapis tylko do własnego `out`
(`allowWrite` piaskownicy kroku). Opiekun czekający na slot dostawcy (`step.waiting` rodzaju `caretaker`) nie daje
powiadomienia o limicie — problem nadal należy do opiekuna, powiadamia dopiero eskalacja.
Ponieważ `out` leży poza repozytorium, tylko `write_out_only` dodaje `--skip-git-repo-check` do uruchomienia
Codexa (także wznowienia); katalog roboczy i ograniczenia piaskownicy pozostają takie jak powyżej.

## Docker

Podgląd i obsługa kontenerów zamiast Docker Desktop — dane dla widoku „Docker” w oknie Tide (`apps/docker`) i kafelka `org.tide.docker` w docku (`widgets/docker`). Usługa rozmawia z Docker Engine API przez lokalny socket (sama biblioteka standardowa, bez `docker` CLI i bez `docker compose`). Socket z `$DOCKER_HOST` (`unix:///ścieżka`), domyślnie `/var/run/docker.sock`; `tcp://`, `ssh://` i inne → stan `unsupported`, bez łączenia się przez sieć.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Docker` |
| Interfejs | `org.tide.Daemon.Docker` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Snapshot()` | `→ s` | JSON migawki kontenerów (opis niżej); od razu, z pamięci |
| `Stats()` | `→ s` | CPU i pamięć działających kontenerów; liczone tylko na żądanie (okno woła, gdy jest widoczne), pamiętane 5 s; odpowiedź po odczycie (łącznie najwyżej 8 s) |
| `Disk()` | `→ s` | kategorie porządków z miejscem do odzyskania; pamiętane 30 s, kasowane po akcji i po zdarzeniu, które zmienia, co jest do odzyskania (kontener `create`/`destroy`/`start`/`die`, wolumen `create`/`destroy`/`prune`); odpowiedź po odczycie (`/system/df` najwyżej 20 s) |
| `DiskNow()` | `→ s` | to samo, ale zawsze świeży odczyt: bez pamięci i bez odczytu, który zaczął się przed wywołaniem. Woła je potwierdzenie porządków — `volumes/prune all=true` usuwa wszystkie wolumeny bez kontenera, więc lista musi być z tej chwili (np. po `docker compose down` gdzie indziej) |
| `Start(s id)`, `Stop(s id)`, `Restart(s id)` | `→ s` | `{ok, error, id, name, note}`; `stop` i `restart` z `t=10` (potem Docker zabija); `note`: „już działał”, „już był zatrzymany”. Zapytanie do Dockera najwyżej 20 s |
| `Remove(s id)` | `→ s` | usuwa tylko zatrzymany kontener, bez `force` i bez wolumenów; sprawdzenie stanu (≤ 5 s) i usunięcie (≤ 15 s) |
| `GroupAction(s project, s action)` | `→ s` | **zadanie w tle**: `start`/`stop`/`restart` całego projektu compose przez API. Od razu `{ok: true, error: "", job}` albo odmowa `{ok: false, error, job: ""}` (nieznana akcja, pusta nazwa); wynik `{ok, error, job, project, done, failed, note}` sygnałem `ActionFinished` |
| `Prune(s categories)` | `→ s` | **zadanie w tle** dla listy JSON z id kategorii, np. `["cache", "images"]`. Od razu `{ok: true, error: "", job}`; cokolwiek innego niż lista znanych id (obiekt, napis, `null`, nieznane id, pusta lista) → `{ok: false, error, job: ""}` i żadnego wywołania. Wynik `{ok, error, job, reclaimedBytes, details}` sygnałem `ActionFinished` |
| `Logs(s id, i lines)` | `→ s` | ostatnie linie logów `{ok, error, id, name, lines: [{text, stderr}]}`; `lines ≤ 0` → 200, najwyżej 5000; najwyżej 15 s |
| `OpenLogs(s id)` | `→ b` | karta Konsoli z `docker -H <socket usługi> logs -f --tail 200 <pełne ID>` |
| `OpenShell(s id)` | `→ b` | karta Konsoli z `docker -H <socket usługi> exec -it <pełne ID> sh -c 'command -v bash >/dev/null && exec bash \|\| exec sh'`; `false` dla zatrzymanego |
| `Badge()` | `→ s` | plakietka widoku Docker w pasku okna Tide: `{text, tone}` — kontenery, które padły, jako `"● N"` z tonem `negative` (róż), inaczej liczba działających z tonem `muted`; zero, brak Dockera i stan przed pierwszym odczytem — pusty tekst (plakietka ukryta). Testcontainers się nie liczą. Te same reguły liczy backend widoku z migawki (`apps/docker/backend.py`) |
| `ShowWindow(s id)` | `→ b` | okno Tide na widoku Docker (`tide.py --view docker [--select <element>]`); `id` — kontener z ostatniej migawki (pełne ID, początek ID od 12 znaków, nazwa — do okna trafia zawsze pełne ID; widok otwiera mu szufladę logów) albo grupa (`compose:<projekt>`, `standalone`, `testcontainers`), którą widok odsłoni, przewinie i podświetli. Nieznany element nie trafia do polecenia — okno otwiera się na samym widoku. `false`, gdy okna nie ma |
| `Changed` | sygnał bez argumentów | migawka się zmieniła — pobierz `Snapshot()` ponownie |
| `ActionFinished(s job, s json)` | sygnał | koniec zadania `Prune` albo `GroupAction`; `json` to wynik z tym samym `job` |

`id` w akcjach to pełne ID, początek ID od 12 znaków albo nazwa — wyłącznie kontenera z ostatniej migawki (inny → `{ok: false, error: "nie ma takiego kontenera"}` bez żadnego wywołania API). Błędy są po polsku; komunikat Dockera (po angielsku) idzie po dwukropku: „Docker nie pozwolił zatrzymać: …”.

**Odpowiedzi i zadania.** Metody z odpowiedzią „po odczycie” odpowiadają z opóźnieniem (praca w wątku, pętla D-Bus obsługuje w tym czasie inne wywołania). `Start`, `Stop`, `Restart`, `Remove` i `Logs` mają jeden termin na całą akcję: 20 s od chwili wywołania D-Bus, a w nim czekanie w kolejce wątków (cztery naraz), łączenie z ponowieniami i wszystkie zapytania akcji (`Remove` to sprawdzenie stanu i usunięcie); termin ogranicza każdy odczyt, nie tylko pojedyncze `recv`. Akcja, której kolej przyszła po terminie, nie woła Dockera i odpowiada „Tide nie zdążyło — za dużo akcji naraz, spróbuj ponownie”; gdy Docker nie zdąży w trakcie — „Docker nie odpowiada na czas” (operacja w Dockerze mogła się mimo to dokończyć — pokaże to następna migawka). Najgorszy przypadek to `Stop`/`Restart`: Docker czeka do 10 s na zamknięcie, potem zabija. `Stats` ma łączny limit 8 s, `Disk` 20 s na `/system/df` (oba co najwyżej jeden odczyt naraz). Odpowiedź przychodzi więc po ok. 20 s najpóźniej — poniżej domyślnego limitu klienta D-Bus (25 s), o ile pętla usługi nie jest zablokowana czymś innym. `Prune` (duże obrazy i cache potrafią się usuwać minutami, limit 300 s na kategorię) i `GroupAction` (kolejne `stop` po 10 s) to zadania: wywołanie od razu oddaje `job` (`prune-<n>-<los>`, `group-<n>-<los>`), a wynik przychodzi sygnałem `ActionFinished(job, json)`. Zadania idą po kolei w osobnym wątku, więc nie blokują pojedynczych akcji ani odświeżania. Klient łączy się z sygnałem przed wywołaniem i dopasowuje wynik po `job`.

**Migawka.**

```json
{"available": true, "state": "ok", "error": "", "host": "unix:///var/run/docker.sock",
 "engine": {"version": "29.9.2", "apiVersion": "1.56"}, "live": true,
 "summary": {"total": 16, "running": 9, "stopped": 4, "failed": 1, "projects": 6, "projectsTotal": 10,
             "testcontainers": 3, "testcontainersRunning": 3},
 "groups": [{"id": "compose:cookapp", "kind": "compose", "project": "cookapp", "label": "cookapp",
             "workingDir": "/home/…/CookApp", "repo": {"name": "CookApp", "path": "/home/…/CookApp", "worktree": ""},
             "running": 3, "total": 3, "stopped": 0, "failed": 0, "containers": [{…}]}]}
```

| Pole | Opis |
| --- | --- |
| `available`, `state`, `error` | `state`: `ok`, `starting` (przed pierwszym odczytem), `unavailable` (brak socketu, odmowa połączenia, brak odpowiedzi), `denied` (brak uprawnień do socketu), `unsupported` (`DOCKER_HOST` inny niż unix), `error` (Docker odpowiedział błędem); `error` to powód po polsku, np. „Docker nie działa — brak socketu /var/run/docker.sock”, „brak uprawnień do /var/run/docker.sock — dodaj się do grupy docker i zaloguj ponownie”. Bez Dockera `groups` jest puste, a usługa próbuje dalej |
| `live` | strumień zdarzeń jest połączony (zmiany przychodzą od razu) |
| `summary` | `running`, `stopped`, `failed` bez testcontainers; `projects` — projekty compose z co najmniej jednym działającym kontenerem, `projectsTotal` — wszystkie |
| grupa `kind` | `compose` (`id` = `compose:<projekt>`), `standalone` (`label` „bez compose”), `testcontainers` (etykieta `org.testcontainers`; okno domyślnie je chowa). Kolejność: compose z działającymi (alfabetycznie), compose zatrzymane, „bez compose”, testcontainers |
| grupa `repo` | repozytorium z etykiety `com.docker.compose.project.working_dir`: od katalogu w górę do `.git`; dla worktree (plik `.git`) — repozytorium główne, a katalog worktree w `worktree`. Tylko repozytorium z katalogu projektów (`--workspace`), inaczej `null` |

Pola kontenera:

| Pole | Opis |
| --- | --- |
| `id`, `shortId`, `name` | pełne ID, 12 znaków, pełna nazwa (argument akcji) |
| `displayName` | nazwa wygenerowana przez compose bez prefiksu projektu („app4-release-localhost-web-1” → „web-1”); własna `container_name` bez zmian |
| `image`, `project`, `service` | obraz i etykiety compose |
| `state`, `running`, `health` | stan Dockera (`running`, `exited`, `created`, `paused`, `restarting`, `dead`), zdrowie `healthy`/`unhealthy`/`starting` albo pusty |

| `stateText` | stan słowami: „działa · zdrowy”, „zakończony”, a dla awarii (i zatrzymania z Tide zakończonego zabiciem) powód. Tekstu Dockera „Up 14 hours” w migawce nie ma — zmienia się sam z czasem, więc dawałby `Changed` bez zmiany; wiek okno liczy z czasów |
| `startedTs`, `finishedTs`, `createdTs` | sekundy epoki (0 — nigdy) — okno liczy z nich „13 h”, „2 dni temu” |
| `exitCode`, `failed`, `reason` | `failed`: zakończony kodem spoza 0/143 (143 to zwykłe `docker stop`), zabity przez OOM, `dead`, restartujący się po błędzie albo `created` po nieudanym pierwszym starcie (Docker zostawia `State.Error` i kod 128: „nie wystartował: port is already allocated”). `reason`: „zabity (kod 137), możliwy brak pamięci”, „zabity przez brak pamięci (OOM, kod 137)”, „padł (kod 1)”, „padł z naruszeniem pamięci (kod 139)”… Kod 137 po `Stop` z Tide (zabicie po 10 s czekania) to nie awaria: `failed: false` i neutralny `reason` „zatrzymany z Tide — nie zamknął się w 10 s, więc Docker go zabił (kod 137)”; Tide pamięta chwilę wysłania `stop` i zdejmuje znacznik dopiero, gdy kontener wystartował po niej (`StartedAt` późniejszy) albo zniknął — odświeżenie w trakcie zatrzymywania (Docker od razu zgłasza `kill`) go nie kasuje; znacznik żyje tylko w pamięci usługi |
| `oomKilled`, `restartCount`, `tty`, `oneoff` | ze szczegółów kontenera; `oneoff` — `docker compose run` |
| `ports` | opublikowane porty bez powtórek IPv4/IPv6: `{hostIp, hostPort, containerPort, proto, likelyHttp, url}`; `likelyHttp` false dla UDP, znanych portów baz, kolejek i pamięci podręcznych (5432, 3306, 6379, 27017, 5672…) i obrazów baz (postgres, pgvector, redis, mongo, rabbitmq, testcontainers/ryuk…) poza ich panelami WWW (porty 15672… i obrazy paneli: mongo-express, kafka-ui, redis-commander, redisinsight, pgadmin, adminer, phpmyadmin, nazwy z `-ui`, `-admin`, `commander`, `insight`, `-exporter`…). `url` — `http(s)://localhost:<port>` albo pusty |
| `testcontainers` | etykieta `org.testcontainers` |

**Odświeżanie.** Osobny wątek trzyma `GET /events` (kontenery i wolumeny; zdarzenia wolumenów — tylko `create`, `destroy`, `prune` — kasują pamięć `Disk` bez odświeżania migawki). Zdarzenie (bez `exec_*` — to m.in. każde sprawdzenie zdrowia) → po 300 ms odświeżenie migawki → `Changed`, gdy JSON się zmienił. Zerwany strumień → ponowne połączenie co 1, 2, 4… do 30 s, a w tym czasie odświeżanie co 10 s; przy działającym strumieniu i tak pełne odświeżenie co 5 min. Odświeżenie to lista kontenerów i `inspect` tylko tych, których stan albo zdrowie z listy się zmieniły (zdarzenie dla kontenera też wymusza jego `inspect`); bez strumienia i przy pełnym odświeżeniu co 5 min — `inspect` wszystkich, bo szybki restart nie zmienia stanu z listy, a zmienia czas startu. Wynik `Stats`/`Disk` policzony przed końcem akcji albo przed zdarzeniem nie trafia do pamięci (licznik zmian).

**Statystyki.** `{ok, error, generatedTs, containers: {id: {id, name, cpuPercent, cpus, memBytes, memLimitBytes, memPercent}}, total: {cpuPercent, hostCpuPercent, cpus, memBytes, containers}, missing: [id]}`. `GET /containers/{id}/stats?stream=false` dla wszystkich działających równolegle, łącznie najwyżej 8 s — kto nie zdążył, trafia do `missing`. CPU jak `docker stats` (100% = jeden rdzeń), `hostCpuPercent` — udział w całym procesorze (dla kafelka); pamięć to zużycie bez nieaktywnej pamięci podręcznej (`inactive_file`).

**Porządki.** `Disk()` → `{ok, error, generatedTs, categories, totalReclaimableBytes, layersBytes}` z `GET /system/df`; kategoria to `{id, label, note, count, total, reclaimableBytes, totalBytes, warn}`:

| `id` | Co | Do odzyskania |
| --- | --- | --- |
| `cache` | „Pamięć podręczna budowania” | wpisy nieużywane w tej chwili i niewspółdzielone |
| `images` | „Nieużywane obrazy” | obrazy bez żadnego kontenera (także zatrzymanego): rozmiar minus część współdzielona |
| `volumes` | „Wolumeny bez kontenera” | wolumeny z `RefCount` 0; `warn: true`, notka „mogą trzymać dane baz” |
| `stopped` | „Zatrzymane kontenery” | warstwa zapisu zatrzymanych; ich wolumeny zostają. `warn` i `anonymousVolumes`, gdy któryś ma wolumen anonimowy (etykieta `com.docker.volume.anonymous` albo nazwa z 64 znaków hex) — po usunięciu kontenera dane, np. bazy z `VOLUME` w obrazie, zostaną bez nazwy |

Nic, czego używa działający kontener, nie trafia do żadnej kategorii. `Prune` woła endpointy Dockera, które same omijają rzeczy w użyciu — `images/prune` z `dangling=false`, `volumes/prune` z `all=true` (od API 1.42 bez tego tylko anonimowe), `build/prune?all=true`, `containers/prune` — wyłącznie dla wymienionych kategorii, zawsze w kolejności obrazy, wolumeny, cache, kontenery. Kontenery na końcu: usunięcie zatrzymanych przed obrazami i wolumenami odsłoniłoby rzeczy, których nie było na liście. Lista JSON to jedyny przyjmowany kształt (obiekt `{"cache": true}` celowo nie — wcześniej każda „prawdziwa” wartość, np. napis `"false"`, wybierała kategorię); pusta lista, nieznane id albo inny kształt → odmowa bez żadnego wywołania. 404 z endpointu prune to „ten Docker nie zna tej operacji porządków (za stary silnik?)”, 409 — „Docker właśnie porządkuje”. Wynik: `{ok, error, reclaimedBytes, details: [{id, label, ok, reclaimedBytes, removed, error}]}`.

**Projekt compose.** `GroupAction` bierze kontenery z etykietą projektu (`GET /containers/json?filters={"label":["com.docker.compose.project=…"]}`) i układa je według `com.docker.compose.depends_on`: `start` — niedziałające (bez jednorazowych `run`) od zależności, `stop` — działające od końca, `restart` — działające: zatrzymanie od końca, potem start od zależności. Kontener, który przy `restart` nie dał się zatrzymać, nie dostaje ponownego startu i trafia do `failed`; `done` to kontenery, którym udały się wszystkie kroki.

**Logi i powłoka w Konsoli** idą tym samym mechanizmem co „Nowe zadanie”: plik zadania z argv w `$XDG_RUNTIME_DIR/tide/tasks` i karta z profilem „Tide: zadanie agenta” w ostatnio używanym oknie Konsoli albo nowe okno z `launch.py --task` ([Nowe zadanie](#nowe-zadanie)). Tytuł karty „docker · <nazwa>”. W argv zawsze pełne ID (nazwę można nadać innemu kontenerowi) i `-H unix://<socket usługi>` (karta nie dziedziczy `DOCKER_HOST` usługi, a powłoka może mieć inny `docker context`); żadnego sklejania poleceń. Każda karta otwiera się we własnym, pustym katalogu w `$XDG_RUNTIME_DIR/tide/docker-tabs/` — karta z profilu przejmuje najstarsze zadanie dla swojego katalogu, więc przy wspólnym katalogu dwa szybkie `OpenShell` mogłyby zamienić się kontenerami. Plik zadania ma też `workdir` (katalog domowy): po przejęciu `launch.py` przechodzi tam, więc polecenie i powłoka, która zostaje po `docker logs -f`, są w `~`, a katalog karty od razu jest wolny. Katalogi kart starsze niż 10 min znikają przy kolejnej karcie, nieprzejęty plik zadania jest wycofywany po 20 s.

Logika bez Qt jest w `tide_daemon/docker/` (`client.py`, `snapshot.py`, `repos.py`, `disk.py`, `stats.py`, `engine.py`), wątki i pamięć podręczna w `docker_monitor.py`.

**Klienci.** Widok „Docker” w oknie Tide bierze na starcie tylko `Snapshot()` (plakietka w pasku okna), `Stats()` woła co 4 s wyłącznie wtedy, gdy jest na wierzchu, okno nie jest zminimalizowane i coś działa, `Disk()` — przy pierwszym pokazaniu i co 2 min (też tylko na wierzchu i bez minimalizacji), a `Logs(id, 200)` co 3 s, dopóki szuflada logów jest otwarta. Potwierdzenie porządków woła `DiskNow()` i do odpowiedzi nowszej niż jego otwarcie (`generatedTs`) przycisk „Wyczyść” jest wyłączony („liczę…”); lista pokazuje dopiero świeży odczyt. Kafelek w docku woła `Stats()` co 5 s tylko przy otwartym popupie i `Disk()` przy jego otwarciu. Oba łączą się z `ActionFinished` przed wywołaniem zadania i dopasowują wynik po `job`; zadanie bez wyniku (usługa zniknęła) zwalnia przyciski po 10 min (widok: projekt 10 min, porządki 30 min), a wynik zadania, na które nikt nie czeka (cudze albo wyprzedzające odpowiedź z `job`), trzymają najwyżej minutę.

```bash
qdbus6 org.tide.Daemon /Docker org.tide.Daemon.Docker.Snapshot
/usr/bin/python3 daemon/tide-daemon.py --print-docker   # migawka i miejsce na dysku, wyłącznie GET do Engine API
```

## Modele lokalne

Jedno miejsce w usłudze, przez które Tide rozmawia z serwerem modeli lokalnych: teraz Ollama na tym komputerze (`http://127.0.0.1:11434`), później Mac Studio w sieci — zmianą adresu w ustawieniach, bez zmian w kodzie (plan `docs/agents/plans/2026-10-27-local-models.md`, PT-14). Usługa odczytuje stan serwerów i wystawia go przez D-Bus; przez wywołanie modelu (`tide_daemon.models.chat`) idą „Na daily” i opisy kart w trybie `"summary": "local"` (PT-15, sekcje „Raport poranny” i „Koordynacja”), zawsze przez wspólną kolejkę (PT-16, „Kolejka” niżej).

| Element | Wartość |
| --- | --- |
| Obiekt | `/Models` |
| Interfejs | `org.tide.Daemon.Models` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Status()` | `→ s` | JSON `{checked, default, config, endpoints, roles, agentProgram, search, report, pulls, queue}` (opis niżej), bez tokenów i treści poleceń |
| `Refresh()` | | odczyt stanu teraz, bez czekania na kolejny cykl (wynik przyjdzie sygnałem `Changed`, jeśli coś się zmieniło) |
| `SetEndpoint(s json)` | `→ s` | nowy albo zmieniony punkt `{name, url, kind, token?, clearToken?}` → `{ok, error}` (zapis ustawień niżej) |
| `RemoveEndpoint(s name)` | `→ s` | usunięcie punktu → `{ok, error}`; odmowa dla domyślnego, jedynego i punktu z przypiętymi rolami |
| `SetDefault(s name)` | `→ s` | domyślny punkt — przełącznik „ten komputer / Mac”: role bez punktu idą za nim → `{ok, error}` |
| `SetRole(s json)` | `→ s` | rola `{role, model, endpoint?, ctx?}` → `{ok, error}` |
| `SetAgentProgram(s program)` | `→ s` | program agenta na modelu lokalnym: `codex` albo `claude` (`agentProgram` w `models.json`) → `{ok, error}` |
| `SetSemanticSearch(s state)` | `→ s` | wyszukiwanie po znaczeniu w KRunnerze: `on` albo `off` (`semanticSearch` w `models.json`; wyłączone — indeks usunięty) → `{ok, error}` |
| `SetReportSummary(s mode)` | `→ s` | `summary` w `report.json` (`claude`, `local`, `rules`), działa od razu w usłudze → `{ok, error}` |
| `SetExecutor(s json)` | `→ s` | wykonawca w katalogu `{name, program, model, provider, endpoint?, classes, effort?, oldName?}` → `{ok, error}` (katalog wykonawców niżej) |
| `RemoveExecutor(s name)` | `→ s` | usunięcie wykonawcy → `{ok, error}` |
| `SetProvider(s json)` | `→ s` | inny dostawca `{name, label?, url, api, token?, clearToken?}`, `api` to `openai` albo `anthropic`; token do `tokens/providers/<nazwa>` (0600), nigdy w odpowiedzi → `{ok, error}` |
| `RemoveProvider(s name)` | `→ s` | usunięcie dostawcy i jego pliku tokenu; odmowa, gdy ma wykonawców → `{ok, error}` |
| `ResolveExecutor(s json)` | `→ s` | klasa na wykonawcę `{provider, class[, author]}` → `{ok, error, executor, provider, class, local, suggested, candidates}`; z `author` — recenzent |
| `TestEndpoint(s json)` | `→ s` | próba połączenia bez zapisu: `{name}` albo `{url, kind, token?}` → `{ok, error, version, models}`; odpowiedź po odczycie (do 2 × 3 s), pętla usługi nie czeka |
| `PullModel(s json)` | `→ s` | pobieranie modelu na punkt Ollamy `{endpoint, model}` → od razu `{ok, error, key}`; postęp w `Status().pulls[key]` |
| `Changed` | sygnał bez argumentów | stan serwerów, modeli, ustawień, pobierania albo kolejki się zmienił — pobierz `Status()` ponownie; przychodzi też po każdym udanym zapisie (z powodu postępu pobierania i kolejki razem najwyżej raz na sekundę) |

```json
{
  "checked": true,
  "default": "local",
  "config": {"path": "/home/user/.config/tide/models.json", "exists": false, "error": "", "warnings": []},
  "endpoints": {
    "local": {"name": "local", "url": "http://127.0.0.1:11434", "kind": "ollama", "auth": false,
              "online": true, "error": "", "version": "0.40.11",
              "models": [{"name": "qwen3:8b", "size": 5225388164, "family": "qwen3", "parameterSize": "8.2B",
                          "quantization": "Q4_K_M"}],
              "loaded": [{"name": "qwen3:8b", "size": 6000000000, "sizeVram": 6000000000, "gpuPercent": 100,
                          "contextLength": 8192, "expiresAt": "2026-10-27T17:05:00+01:00"}]}
  },
  "roles": {
    "summary": {"endpoint": "local", "model": "qwen3:8b", "ctx": 8192, "installed": true, "available": true,
                "error": "", "pinned": false}
  },
  "agentProgram": "codex",
  "search": {"enabled": true, "state": "ready", "items": 412, "pending": 0, "updated": "2026-10-28T10:02:41+01:00",
             "error": "", "model": "bge-m3:latest", "dims": 1024},
  "report": {"summary": "local", "configured": "local", "summaryModel": "sonnet", "raw": null, "pinned": false,
             "path": "/home/user/.config/tide/report.json", "warnings": []},
  "pulls": {"local/bge-m3": {"endpoint": "local", "model": "bge-m3", "status": "pulling 24d7…", "completed": 512000000,
                             "total": 1157672605, "running": true, "error": ""}},
  "queue": {
    "local": {"running": {"endpoint": "local", "role": "summary", "model": "qwen3:8b",
                          "since": "2026-10-28T07:00:12+01:00"},
              "waiting": [{"endpoint": "local", "role": "describe", "model": "qwen3:8b",
                           "since": "2026-10-28T07:00:15+01:00"}]}
  }
}
```

| Pole | Opis |
| --- | --- |
| `checked` | `false` tylko przed pierwszym odczytem po starcie usługi (wtedy wszystkie punkty `online: false` bez błędu) |
| `config` | plik ustawień: ścieżka, czy istnieje, `error` (plik nie do użycia — wszystko domyślne), `warnings` (pominięte punkty i role) |
| `endpoints.*.online`, `error` | czy serwer odpowiada; przy `false` krótki powód po polsku, np. „Model lokalny niedostępny: serwer nie odpowiada (127.0.0.1:11434)” |
| `endpoints.*.auth` | punkt wysyła token (`tokenFile`) — sam token nigdy nie wychodzi z usługi |
| `endpoints.*.version` | wersja Ollamy; `null` dla serwera `openai` |
| `endpoints.*.models` | pobrane modele: `name`, a dla Ollamy też `size`, `family`, `parameterSize`, `quantization`; model chmurowy Ollamy (`…:cloud`, `…:120b-cloud`) ma dodatkowo `cloud: true` (lokalne — bez tego pola) |
| `endpoints.*.loaded` | modele w pamięci (Ollama `/api/ps`): rozmiar, `sizeVram`, `gpuPercent` (część na karcie — poniżej 100 model pisze kilkanaście razy wolniej), `contextLength`, `expiresAt`; `null` dla serwera `openai`, który tego nie podaje |
| `roles.*` | punkt, model, `ctx` (`null` — kontekst serwera), `installed` (model jest na serwerze; `null`, gdy serwer nie odpowiada), `available` (serwer odpowiada i model jest), `error` (rola niedostępna, np. jej punkt jest pominięty; zwykle pusty), `pinned` (rola przypięta jawnie — `"punkt:model"` w pliku — także gdy to obecny `default`; `false` — idzie za `default`, więc `SetRole` bez `endpoint` odpina rolę przypiętą) |
| `agentProgram` | program agenta na modelu lokalnym (okno „Nowe zadanie”, tryb „Lokalny”): `codex` albo `claude`; brak klucza albo zła wartość w pliku — `codex` (zła z ostrzeżeniem w `config.warnings`) |
| `search` | wyszukiwanie po znaczeniu w KRunnerze (sekcja „KRunner”): `enabled` — `semanticSearch` z `models.json`; stan indeksu: `state` — `off` (wyłączone), `waiting` (czeka na pierwszy przebieg albo na wolny serwer), `indexing`, `ready`, `error` (z `error`, np. „serwer odpowiedział 404: model "bge-m3" not found”), `inactive` (usługa bez KRunnera, `--no-krunner`); `items` — sesje z wektorem, `pending` — do policzenia, `updated` — ostatni pełny przebieg (ISO 8601), `model`, `dims`. Bez tekstów i wektorów. Zmiana stanu indeksu daje `Changed` (najwyżej raz na sekundę, jak kolejka) |
| `report` | tryb „Na daily” i opisów kart (`report.json`, sekcja „Raport poranny”): `summary` — tryb, którego usługa używa teraz, `configured` — z pliku po walidacji (nieznana wartość → `rules`), `raw` — wpisana nieznana wartość (inaczej `null`), `pinned` — tryb wymuszony opcją `--report-summary` (wtedy `SetReportSummary` zmienia tylko plik), `summaryModel` (liczy się w trybie `claude`), `warnings` z odczytu pliku |
| `pulls` | pobierania modeli (`PullModel`) po kluczu `"<punkt>/<model>"`: `status` (etap z Ollamy: `start`, `pulling manifest`, `pulling <skrót>`, `verifying sha256 digest`, …, na koniec `success` albo `error`), `completed` i `total` w bajtach (suma warstw; `total` 0, dopóki serwer go nie poda), `running`, `error` (komunikat po polsku). Zakończone znikają po 15 s, nieudane po 60 s; pusty obiekt, gdy nic się nie pobiera |
| `queue` | kolejka wywołań modeli (PT-16) na każdy punkt z konfiguracji (i punkt, którego już nie ma w `models.json`, a coś na nim jeszcze trwa): `running` — trwające wywołanie `{endpoint, role, model, since}` (`endpoint` — punkt, z którego przyszło; `since` — początek wywołania, ISO 8601) albo `null`; `waiting` — czekające `{endpoint, role, model, since}` (`since` — dołączenie do kolejki) w kolejności, w jakiej ruszą. Kolejka liczy miejsca na serwer, nie na nazwę, więc punkty z tym samym adresem pokazują ten sam stan (z wpisami obu). Wywołanie na starym adresie punktu (adres zmieniony w trakcie) nie znika: jest obok bieżącego stanu punktu w liście `other` — `[{lane, running, waiting}]`, `lane` to stary adres `scheme://host:port`; pole tylko wtedy, gdy coś takiego jest. Bez treści poleceń. Pole opcjonalne: klient (kafelek „Modele”) działa bez niego |

**Ustawienia.** `~/.config/tide/models.json` (`$XDG_CONFIG_HOME/tide/models.json`; `$TIDE_MODELS_CONFIG` podmienia ścieżkę — testy, diagnostyka), czytany przy każdym odczycie i wywołaniu — zmiana działa bez restartu usługi (`Refresh()` od razu). Plik jest opcjonalny; bez niego jest jeden punkt `local` (`http://127.0.0.1:11434`, `ollama`), role `summary`, `describe` → `qwen3:8b` z kontekstem 8192 i `embed` → `bge-m3` (kontekst serwera; wyszukiwanie po znaczeniu w KRunnerze).

```json
{
  "default": "local",
  "endpoints": {
    "local": {"url": "http://127.0.0.1:11434", "kind": "ollama"},
    "mac":   {"url": "http://mac-studio:11434", "kind": "ollama", "tokenFile": "~/.config/tide/mac.token"},
    "lm":    {"url": "http://mac-studio:1234", "kind": "openai"}
  },
  "roles": {
    "summary":  "local:qwen3:8b",
    "describe": {"model": "local:qwen3:8b", "ctx": 8192},
    "embed":    "local:bge-m3",
    "agent":    "local:qwen3-coder:30b",
    "polish":   "local:hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M"
  },
  "agentProgram": "codex",
  "semanticSearch": true
}
```

- `kind`: `ollama` (domyślnie) albo `openai` (MLX, LM Studio, llama.cpp — dowolny serwer z `/v1/chat/completions`); adres bez `/v1` (podany z `/v1` też działa).
- Rola to `"punkt:model"` albo `{"model": "punkt:model", "ctx": N}`. Nazwy modeli mają dwukropki, więc napis dzielimy tylko na pierwszym — i tylko wtedy, gdy przed nim stoi nazwa znanego punktu. Inaczej cały napis to model na punkcie `default` (`"qwen3:8b"` = `default:qwen3:8b`). Brakujące role `summary`, `describe` i `embed` dochodzą na punkcie `default`, więc przepięcie na Maca to zmiana `default` albo przedrostka w rolach.
- `ctx` to kontekst (`num_ctx` w Ollamie). Role `summary` i `describe` bez `ctx` dostają 8192: z 16k model 8B nie mieści się na karcie 8 GB i odpowiedź trwa 165 s zamiast 8 s (pomiary PT-13). Inne role bez `ctx` i `ctx: 0` — kontekst serwera (16k w nakładce usługi Ollamy, dla agentów).
- `agentProgram`: program, w którym działa agent na modelu lokalnym z okna „Nowe zadanie” — `codex` (domyślnie; Ollama obsługuje go wprost) albo `claude` (Claude Code: bez konektorów claude.ai, gorzej znosi mały kontekst). Program to nie model: model daje rola `agent`, program — ten klucz, jeden dla wszystkich serwerów, więc przełączenie na Maca go nie zmienia (PT-39, decyzja 72). Nieznana wartość (np. `"Claude"`) — ostrzeżenie „agentProgram: nieznany program „Claude” (codex albo claude) — agent lokalny działa w Codexie” i Codex; reszta pliku działa.
- `semanticSearch`: wyszukiwanie po znaczeniu w KRunnerze (PT-19) — `true` albo `false`; brak klucza — włączone (działa, gdy model roli `embed` jest na serwerze). Zła wartość — ostrzeżenie „semanticSearch: oczekiwano true albo false, jest „nie” — wyszukiwanie po znaczeniu włączone”. Klucz najwyższego poziomu, jak `agentProgram`: przełączenie na Maca go nie zmienia.
- `tokenFile` (ścieżka bezwzględna albo z `~`; względna — serwer pominięty z ostrzeżeniem, bo zależałaby od katalogu, z którego wystartowała usługa): plik z tokenem, wysyłanym jako `Authorization: Bearer …` — na Maca za pośrednikiem z tokenem (PT-21). Czytany przy każdym zapytaniu, tylko w pamięci; nie trafia do dziennika, komunikatów ani `Status()`.
- Zepsuty plik (nie JSON albo nie obiekt) → ustawienia domyślne i `config.error`; zły punkt albo zła rola są pomijane z ostrzeżeniem w `config.warnings`, reszta działa. Nieznany, pominięty albo nie-napisowy `default` nie przenosi ról bez punktu na inny serwer: te role (także `summary` i `describe`) są niedostępne z powodem w `roles.*.error`, a `default` w stanie to zapasowy `local` (albo pierwszy punkt) — tylko do odczytu stanu; role z przedrostkiem poprawnego punktu działają. Żaden kształt pliku nie zatrzymuje usługi ani `--print-models`.
- Zły punkt to: adres spoza `http(s)://`, adres, którego nie da się rozebrać na host i port (port nieliczbowy, 0 albo powyżej 65535, IPv6 bez nawiasów — poprawnie `http://[fd7a:115c::1]:11434`), adres z danymi logowania (`http://user:hasło@…` — hasło trafiłoby do stanu; od tego jest `tokenFile`), nieznany `kind`.
- Rola z przedrostkiem pominiętego punktu (`"agent": "mac:qwen3-coder:30b"`, gdy `mac` ma zły adres) nie przechodzi cicho na `default`: zostaje przy `mac` jako niedostępna (`roles.agent.error`, ostrzeżenie), a wywołanie tej roli kończy się `ModelError`.

**Zapis ustawień z okna Tide** (PT-25, `tide_daemon/models/settings.py`). Ustawienia zmienia się w oknie Tide (widok „Modele”, PT-26) — bez edycji plików; przepięcie na Maca to `SetDefault("mac")`. Każda metoda zapisu odpowiada `{"ok": true, "error": ""}` albo `{"ok": false, "error": "<komunikat po polsku>"}`; po udanym zapisie `Status()` od razu pokazuje nowe ustawienia (punkt nowy albo zmieniony przez `SetEndpoint` — także sam token — jest „nie sprawdzony”: `online: false` bez `error`), przychodzi `Changed`, a odczyt serwerów rusza zaraz po nim (drugi `Changed`, gdy stan się zmieni). Piszemy tylko na wywołanie z D-Bus — nigdy przy starcie usługi.

- **Słowo „serwer”.** Komunikaty dla użytkownika (odmowy zapisu, ostrzeżenia `config.warnings`, błędy ról i `ModelError`) mówią „serwer”, jak widok „Modele” — w kodzie i JSON-ie zostaje `endpoint`/`endpoints`, a w tym opisie „punkt”.
- **Jedna walidacja.** Punkt i rola przechodzą przez ten sam kod co odczyt pliku (`config.check_endpoint`, `config.check_role`), więc odmowa ma ten sam komunikat co ostrzeżenie w `config.warnings`, bez dopisku „— pominięty”, np. „serwer „lm”: nieznany rodzaj „llamacpp” (ollama albo openai)”. Na koniec cały plik po zmianie czytamy jeszcze raz: zmiana, która dałaby nowe ostrzeżenie, nie jest zapisywana; wcześniejsze ostrzeżenia z ręcznie edytowanego pliku zapisu nie blokują (i zostają w pliku). Adres i nazwa modelu — najwyżej 512 znaków, token — 4096; wpisane wartości w komunikatach są przycinane.
- **Plik.** Zapis atomowy (plik tymczasowy w tym samym katalogu, `fsync`, `rename`, `fsync` katalogu), `models.json` zawsze 0600, nieznane klucze i pozostałe ustawienia zostają (plik jest przepisywany z wcięciem 2), dowiązanie symboliczne zostaje dowiązaniem. Bez `models.json` pierwszy zapis zaczyna od wartości domyślnych (`default: local`, punkt `local`, role `summary` i `describe` → `qwen3:8b` bez punktu), więc niczego poza samą zmianą nie przestawia; brak `default` w pliku jest zapisywany wprost (zapasowy punkt nie przesunie się po dodaniu innego). `null` w `endpoints` albo `roles` to brak klucza, jak przy odczycie. Pliku, którego nie umiemy przeczytać (nie JSON, nie obiekt, `endpoints` albo `roles` innego rodzaju niż obiekt), nie nadpisujemy: odmowa z prośbą o poprawienie albo usunięcie. Zapisy są szeregowane blokadą.
- **`SetEndpoint`** tworzy albo zmienia punkt (zmiana nazwy to nowy punkt i usunięcie starego). Nowy punkt nie może zmienić znaczenia istniejących ról: punkt `qwen3` sprawiłby, że rola bez punktu `"qwen3:8b"` czytałaby się jako punkt `qwen3` i model `8b` — odmowa „nazwa „qwen3” zmieniłaby role describe, summary (model „qwen3:8b” czytany jako serwer „qwen3”) — wybierz inną nazwę serwera” (role przypięte, `"local:qwen3:8b"`, są odporne). Zmiana adresu albo rodzaju punktu, na którym trwa pobieranie modelu, i jego usunięcie — odmowa „na serwerze „local” trwa pobieranie modelu qwen3:8b — poczekaj, aż się skończy”. `kind` bez wartości — dotychczasowy albo `ollama`. Token (`token`) trafia tylko do pliku `tokens/<punkt>` obok `models.json` (`$XDG_CONFIG_HOME/tide/tokens/`, katalog 0700, plik 0600), a w `models.json` jako `tokenFile` (pełna ścieżka). Pusty albo brakujący `token` — token zostaje (także cudzy `tokenFile`), `clearToken: true` — punkt bez tokenu (nasz plik `tokens/<punkt>` znika); oba naraz — odmowa. Token musi być drukowalnym ASCII bez spacji. Nigdy nie wraca w odpowiedzi, komunikacie ani dzienniku; gdy `models.json` nie da się zapisać, plik tokenu wraca do poprzedniej treści.
- **`RemoveEndpoint`** odmawia dla punktu domyślnego („najpierw ustaw inny serwer jako domyślny”), jedynego poprawnego i takiego, do którego są przypięte role („serwer „mac” jest używany przez role: agent, embed — przepnij je na inny serwer albo na domyślny, zanim go usuniesz”). Wybraliśmy odmowę zamiast ról z błędem: rola `"mac:model"` po usunięciu `mac` byłaby już nazwą modelu na punkcie `default` — po cichu poszłaby na inny serwer. Nasz plik tokenu usuniętego punktu znika.
- **`SetDefault`** — tylko poprawny punkt („default: nieznany serwer „x””, „default: serwer „x” jest pominięty w models.json — popraw go najpierw”).
- **`SetRole`**: `model` — nazwa modelu bez przedrostka punktu; `endpoint` pusty albo brak — rola zapisana bez punktu (`"agent": "qwen3-coder:30b"`), więc idzie za `default` i przełączenie domyślnego punktu ją przenosi; nazwa punktu — rola przypięta (`"local:qwen3:8b"`), zostaje przy nim. Model, którego nazwa zaczyna się od nazwy punktu i dwukropka (`local:x`), bez `endpoint` — odmowa („wybierz serwer jawnie”), bo plik czytałby go jako przypięty. `ctx`: brak klucza — bez zmiany, `null` — domyślny (8192 dla `summary` i `describe`, inaczej kontekst serwera; w pliku znika), liczba 0…1048576 (0 — kontekst serwera). Rola może mieć dowolną poprawną nazwę; `summary`, `describe`, `agent` (model sesji agentów, PT-17) i `embed` (wyszukiwanie po znaczeniu w KRunnerze, PT-19) to te, których używa Tide.
- **`SetAgentProgram`** zapisuje `agentProgram` (`codex` albo `claude`) — klucz najwyższego poziomu, niezależny od roli `agent`, więc da się go ustawić także przed wyborem modelu; inne zapisy go nie ruszają. Inna wartość — odmowa z tym samym komunikatem co ostrzeżenie przy odczycie („agentProgram: nieznany program „opencode” (codex albo claude)”).
- **`SetSemanticSearch`** zapisuje `semanticSearch` (`on` → `true`, `off` → `false`) i budzi indeks KRunnera: wyłączenie przerywa partię czekającą w kolejce i usuwa plik wektorów od razu (usługa z `--no-krunner` usuwa go sama). Inna wartość — odmowa „semanticSearch: oczekiwano on albo off, jest „tak””. Każdy inny udany zapis też budzi indeks (np. inny model roli `embed` — indeks od nowa).
- **`SetReportSummary`** zapisuje `summary` w `$XDG_CONFIG_HOME/tide/report.json` (pozostałe klucze i prawa pliku zostają; bez pliku — nowy z samym `summary`) i od razu przełącza raport oraz opisy kart w działającej usłudze, bez restartu. Wyjątek: usługa uruchomiona z `--report-summary` — wtedy zmienia się tylko plik, a `report.pinned` jest `true`. Nieznany tryb — odmowa z komunikatem jak przy odczycie („raport: summary musi być jednym z claude, local, rules, jest 'Local'”).
- **`TestEndpoint`** — jeden odczyt bez zapisu: Ollama `GET /api/version` i `/api/tags`, serwer `openai` — `GET /v1/models`, 3 s na zapytanie. `{name}` sprawdza zapisany punkt; `{name, url?, kind?, token?}` — zapisany punkt z podmienionymi polami (edycja przed zapisem; bez nowego tokenu idzie zapisany); `{url, kind?, token?}` — nowy punkt. Token podany tutaj jest tylko w pamięci tej próby. Najwyżej 4 próby naraz — kolejna dostaje od razu „za dużo prób połączenia naraz — spróbuj za chwilę”. Odpowiedź D-Bus przychodzi po odczycie (osobna pula, pętla usługi nie czeka); wynik `{ok, error, version, models}` — `models` to liczba modeli na serwerze, `version` `null` dla `openai`, przy błędzie `error` to komunikat `ModelError`, a `version` i `models` `null`.
- **`PullModel`** — tylko punkt `ollama` (serwer zgodny z OpenAI pobiera modele sam), nazwa modelu z liter, cyfr i `._:/@+-`. Od razu `{ok, error, key}` (`key` = `"<punkt>/<model>"`), samo pobieranie (`POST /api/pull` ze `stream: true`) idzie w osobnym wątku. Jedno pobieranie naraz na punkt („na serwerze „local” trwa już pobieranie qwen3:8b — poczekaj, aż się skończy”). Postęp w `Status().pulls`, `Changed` najwyżej raz na sekundę (koniec — od razu). Bez terminu całości (duży model to nawet godzina), za to z limitem ciszy 300 s i jednym terminem 10 s na połączenie i nagłówki odpowiedzi; zamknięcie usługi przerywa pobieranie od razu (`models.Cancel`). Po sukcesie odczyt stanu (nowy model na liście). `DeleteModel` nie ma.

**Katalog wykonawców (PT-60).** Wykonawca to program (`claude` albo `codex`) z modelem i punktem pod jedną nazwą; katalog żyje w `models.json` (klucze `executors` — lista `{name, program, model, provider, endpoint, classes, effort}` — `effort` to opcjonalny wysiłek rozumowania Codexa (`low`, `medium`, `high`, `xhigh`; dla Claude pusty) — i `providers` — `{label, url, api, tokenFile}`), zgodnie wstecz: bez `executors` działa katalog domyślny (Haiku, Sonnet, Opus, Codex lekki / standard / mocny, Codex Astra bez klas, Lokalny), a pierwszy zapis z okna zapisuje cały katalog. Dostawca to `anthropic`, `openai`, `local` albo inny z `providers`; API Anthropic obsługuje tylko program `claude`, API OpenAI tylko `codex`, lokalny oba. Wykonawca lokalny z pustym `model`, `program` i `endpoint` idzie za rolą `agent` i `agentProgram`. Token innego dostawcy leży w `tokens/providers/<nazwa>` (0600, osobny podkatalog, więc nie zderza się z tokenem punktu; nazwa punktu `providers` jest zarezerwowana); w `models.json` jest tylko ścieżka, w `Status()` tylko `auth`, nigdy w odpowiedziach D-Bus, dziennikach ani poleceniach agenta (decyzja 70). `Status()` ma dodatkowo `executors` (lista z `where` = `local`/`cloud`, `available` dla lokalnych, `error`), `providers` i `catalog` (`stored`, `warnings`). Moduł `models/executors.py` (bez Qt i sieci): `resolve(ctx, provider, class)` zamienia preferowanego dostawcę (`anthropic`, `openai`, `local` — tylko lokalnie, `mixed` albo nazwę innego dostawcy) i klasę (`lekki`, `standard`, `mocny`, `lokalny`) na wykonawcę według tabeli ze specyfikacji; w trybie mieszanym wynik to podpowiedź (`suggested`) z listą `candidates` z całego katalogu. `reviewer(ctx, provider, class, author)` daje recenzenta innego modelu niż autor (ten sam model z innym wysiłkiem to ten sam model), w trybie mieszanym u innego dostawcy. Praca lokalna nigdy nie przechodzi do chmury: klasa `lokalny`, „tylko lokalnie” i autor lokalny dają wykonawcę lokalnego, a gdy jego serwer nie odpowiada albo nie ma na nim modelu, wynik to `ok: false, local: true` z komunikatem „… Praca lokalna nie przejdzie do chmury.” — bez zastępstwa. `StartCard` rozwiązuje wykonawcę z katalogu przed zapisem dziennika, a okno zlecenia filtruje listę „Od razu” według wybranego dostawcy. `RenderLaunch` przekazuje preferencję planującemu. Zamiana klasy na wykonawcę i dobór recenzenta pozostają dostępnymi funkcjami katalogu oraz metodą `ResolveExecutor`.

**Stan.** Co 15 s (`--models-poll`) w wątku roboczym, z limitem 3 s na całe zapytanie, więc serwer, który wisi, sączy bajty albo nie istnieje, nie blokuje D-Bus ani kolejnych odczytów. Przeoczony błąd w jednym punkcie daje ten punkt `online: false` („błąd odczytu stanu”), pozostałe odczytują się normalnie. Dla Ollamy `GET /api/version`, `/api/tags` i `/api/ps` (żadne nie ładuje modelu ani nie przedłuża jego trzymania w pamięci), dla `openai` — `GET /v1/models`. Punkt, który nie odpowiada na pierwsze zapytanie, nie dostaje kolejnych. JSON nie ma czasu odczytu, więc `Changed` przychodzi tylko przy prawdziwej zmianie (serwer wstał albo padł, model załadowany albo zwolniony, nowy model, zmiana ustawień). Do dziennika usługi trafiają tylko zmiany: „Modele lokalne: local: odpowiada (wersja 0.40.11)”, powód niedostępności, błąd i ostrzeżenia z `models.json`.

**Wywołanie modelu** (`tide_daemon/models/client.py`, bez Qt, do użycia w wątku roboczym): `chat(polecenie, role="summary", system=…, schema=…, timeout=180)` albo `chat(…, endpoint="mac", model="qwen3:8b")`. Wynik to tekst z punktem, adresem (`url`), modelem i kontekstem (`ctx`), którymi naprawdę odpowiedziano, z miarami (`promptTokens`, `outputTokens`, `wallS` — samo wywołanie, `queueS` — czekanie w kolejce, a z Ollamy też `loadS`, `promptS`, `outputS`) i `json()` dla odpowiedzi według schematu. `cancel=models.Cancel()` pozwala przerwać wywołanie z innego wątku (zamykanie usługi): `set()` zamyka gniazdo trwającego zapytania i blokuje nowe — „Model lokalny: wywołanie przerwane — usługa się zamyka”. Adapter według `kind`:

- `ollama` — natywne `POST /api/chat` z `stream: false`, `think: false`, `options.num_ctx` z roli i `format` = schemat JSON. Nie zgodny z OpenAI `/v1/chat/completions` Ollamy, bo ten pomija `num_ctx`, a kontekst decyduje, czy model mieści się na karcie. Model bez przełącznika myślenia (odpowiedź 400 z „think”) — ponowienie bez `think`.
- `openai` — `POST /v1/chat/completions` z `response_format: {type: "json_schema", json_schema: {name, strict, schema}}`; kontekst ustawia serwer.

**Kolejka** (`tide_daemon/models/queue.py`, PT-16). Ollama na tym komputerze obsługuje jedno zapytanie naraz (`OLLAMA_NUM_PARALLEL=1`, karta 8 GB, jeden model w pamięci). Bez kolejki raport i opisy kart czekały na siebie wewnątrz serwera, a czas czekania liczył się do limitu wywołania — karta dostawała „nie odpowiedział w 90 s”, choć model był tylko zajęty raportem. Kolejka siedzi w `chat()`, więc przechodzi przez nią każde wywołanie modelu w usłudze, także przyszłe (PT-17, PT-19) — bez pamiętania o tym w każdym miejscu. Jedna na proces, osobny tor na każdy serwer — po znormalizowanym adresie `scheme://host:port` (`queue.lane_of`), więc dwie nazwy punktu na ten sam serwer dzielą jedno miejsce:
- na serwerze najwyżej jedno wywołanie naraz (liczba miejsc na tor jest w kodzie — `Queue(slots=…)`; ustawienie dla Maca później), różne serwery pracują równolegle;
- kolejność: priorytet (`queue.HIGH` — opisy kart, na które użytkownik patrzy, i zapytanie KRunnera po znaczeniu; `queue.NORMAL` — domyślnie; `queue.LOW` — raport; `queue.IDLE` — indeks wyszukiwania po znaczeniu, gdy nic innego nie czeka), w nim kolejność przyjścia. Limit czekania w kolejce kończy się wyjątkiem `queue.Busy` (podklasa `ModelError`) — wywołujący odróżnia zajęty serwer od niedziałającego. Domyślny priorytet wynika z roli (`describe` wysoki, `summary` niski, reszta zwykły), raport i karty podają go też jawnie. Trwającego wywołania nic nie przerywa;
- grupowanie modeli: w obrębie tego samego priorytetu pierwszeństwo ma wywołanie tym samym modelem, którym serwer pracował ostatnio (bez przerzucania modeli między kartą a pamięcią), dopóki najstarsze czekające nie czeka dłużej niż 60 s. Priorytet jest ważniejszy niż grupowanie: karta z innym modelem i tak idzie przed raportem (przy domyślnych rolach oba to `qwen3:8b`, więc nic się nie przeładowuje);
- czas: `timeout` liczy się od chwili, gdy wywołanie naprawdę ruszyło; czekanie ma własny limit `wait` (domyślnie 600 s; karty 300 s), a `deadline` (`time.monotonic()`) zamyka oba razem — tak raport trzyma 600 s na całość z czekaniem. Po limicie czekania `ModelError` z uczciwym powodem: „Model lokalny: kolejka: czekał 300 s — serwer zajęty: raport poranny (127.0.0.1:11434)”;
- następnego wybiera jedno miejsce, pod blokadą, przy każdej zmianie stanu (dołączenie, start, koniec, wyjście z kolejki), i zapisuje wybór; czekający porównują się z zapisanym wyborem, a zmiana wyboru budzi wszystkich. Gdy grupowanie trzyma najstarsze czekające, czekający śpią najwyżej do końca okna grupowania i wybór liczy się od nowa (review PT-16: bez tego dwaj czekający mogli przy granicy 60 s uznać, że następny jest ten drugi, i spać przy wolnym miejscu do limitu czekania);
- cel (punkt, adres, model, kontekst) `chat()` ustala w chwili, gdy wywołanie dostaje kolej — `models.json` czytany wtedy, chyba że wywołujący podał `config`. Po zmianie ustawień w czasie czekania (inny `default` albo adres, np. z okna Tide) wywołanie idzie już na nowy serwer, a gdy to inny tor — przechodzi do jego kolejki z zachowaniem kolejności przyjścia, chwili dołączenia (`since`) i limitu czekania. Raport przypina ustawienia: jeden przebieg = jedne ustawienia (`summarize.Model(pin=True)` podaje `chat()` konfigurację z początku przebiegu), więc stopka („nic nie wychodzi z tego komputera”), pamięć `summary.json` i przycięcie do kontekstu zgadzają się z tym, dokąd naprawdę poszły fakty. Opisy kart nie przypinają: `chat(expect=…)` dostaje punkt, adres, model i kontekst założone na początku partii i porównuje je z celem z ustawień w chwili startu — przy różnicy wywołanie nie wychodzi do serwera (`TargetChanged`), więc fakty nie trafiają na serwer, którego partia nie zakładała, a nic nie trafia do pamięci pod złą tożsamością (odpowiedź, `Answer` z punktem, adresem, modelem i kontekstem, sprawdzamy jeszcze raz). Karta ma reguły i „Model lokalny niedostępny — opis bez modelu (ustawienia modeli zmieniły się w trakcie wywołania)”, a następna partia (bez 15 min przerwy po błędzie) bierze już nowe ustawienia;
- tor to adres bez rozwiązywania nazw: `http://localhost:11434` i `http://127.0.0.1:11434` to dwa osobne tory (dwa miejsca na tym samym serwerze) — w `models.json` lepiej trzymać jeden zapis adresu;
- głodzenie: raport (`LOW`) czeka, dopóki ktoś z wyższym priorytetem stoi w kolejce, ale w praktyce to najwyżej jedna partia kart naraz (opisy kart idą z jednego wątku, jedno wywołanie naraz, z odstępami), więc raport rusza zaraz po niej; w razie czego trzyma go budżet 600 s;
- `cancel` (`models.Cancel`) zdejmuje czekające wywołanie z kolejki od razu (bez dotarcia do serwera), a trwające traci gniazdo jak dotąd — zamykanie usługi nie czeka na kolej;
- kolejka nie ma własnych wątków: wywołanie czeka i biegnie w wątku wywołującego (wątek raportu, wątek opisów kart), a miejsce zwalnia się także po błędzie.
Poza kolejką: odczyt stanu (`/api/version`, `/api/tags`, `/api/ps` — krótkie GET-y, które nie ładują modelu), pobieranie modeli (`PullModel` z PT-25 — długi transfer na dysk, bez GPU) i sesje agentów z modelem lokalnym (Codex `--oss`, Claude Code przez `ANTHROPIC_BASE_URL` — rozmawiają z serwerem bezpośrednio, Ollama kolejkuje je sama; wywołanie Tide może wtedy czekać w serwerze i dostać zwykłe „nie odpowiedział w N s”). Na później: ciężka praca wsadowa (raport) mogłaby czekać na bezczynność użytkownika (`idle.py`) — dziś raport rusza o swojej porze.

**Modele chmurowe Ollamy.** Ollama ma modele w chmurze (`kimi-k2:cloud`, `gpt-oss:120b-cloud` — etykieta po ostatnim „:” kończy się na `cloud` po „:” albo „-”, bez względu na wielkość liter): po `pull` są w `/api/tags` jak lokalne, ale lokalny serwer tylko przekazuje wywołanie na ollama.com. Tryb lokalny ich nie używa — inaczej fakty z raportu wyszłyby z komputera, a stopka mówiłaby „nic nie wychodzi z tego komputera”. Jedno miejsce rozpoznaje taki model (`tide_daemon/modelnames.py`: `is_cloud_model`, wspólne z agentami z PT-17; w oknach i kafelku ta sama reguła w `widgets/lib/modelnames.js`): rola z nim jest niedostępna (`roles.*.available: false`, `error`: „model „gpt-oss:120b-cloud” działa w chmurze Ollamy, nie lokalnie — wybierz model lokalny w Tide → Modele”, ostrzeżenie w `config.warnings`), `chat()` odmawia także jawnego modelu i sprawdza to jeszcze raz przy starcie wywołania (ustawienia zmienione w czasie czekania), więc raport i karty dostają reguły z tą informacją, a do serwera nic nie wychodzi; `SetRole` odmawia z tym samym komunikatem; w `Status()` takie modele mają `cloud: true`. Pobranie (`PullModel`) usługa przyjmuje — ostrzega okno.

**Gdy serwer nie działa.** Każdy błąd — serwer nie odpowiada, nie ma takiej nazwy, limit czasu, błąd HTTP (z treścią błędu serwera, np. „model 'x' not found”), nieczytelna albo pusta odpowiedź, przekierowanie, nieczytelny plik tokenu (token musi być drukowalnym ASCII), rola na pominiętym punkcie — to jeden wyjątek `ModelError` z krótkim komunikatem po polsku do pokazania użytkownikowi. Tryb lokalny nigdy nie przechodzi do chmury: klient nie zna Claude ani żadnego zapasowego wywołania, a wywołujący pokazuje informację zamiast opisu (decyzja z 27.10 w planie). Zapytania idą bez pośredników z `$http_proxy`. Limit czasu (`timeout` wywołania, 3 s dla stanu) obejmuje całe zapytanie: szukanie nazwy, łączenie, wysłanie i cały odczyt odpowiedzi — po terminie połączenie jest zamykane. Przekierowań (3xx) nie śledzimy: „Model lokalny: serwer odpowiedział 302 — przekierowanie nie jest obsługiwane”, bo przekierowanie zabrałoby token do innego hosta, a POST po 302 stałby się GET-em.

Logika bez Qt jest w `tide_daemon/models/` (`config.py`, `transport.py`, `client.py`, `queue.py` — kolejka wywołań, `status.py`, `settings.py` — zapis ustawień, `pull.py` — pobieranie), odpytywanie, zapis, pobieranie i pole `queue` w wątkach — w `models_monitor.py`. Embeddingi (`models.embed`, PT-19) — w `client.py` obok `chat()`, z tą samą kolejką. Uzasadnienie: decyzje 60, 66, 67, 72 i 75 w `docs/decisions.md`.

```bash
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.Status
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.Refresh
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.TestEndpoint '{"url": "http://127.0.0.1:11434", "kind": "ollama"}'
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.SetDefault mac      # przepięcie ról bez punktu na Maca
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.SetAgentProgram claude   # agent lokalny w Claude Code
qdbus6 org.tide.Daemon /Models org.tide.Daemon.Models.SetSemanticSearch off     # KRunner bez wyszukiwania po znaczeniu
/usr/bin/python3 daemon/tide-daemon.py --print-models   # jeden odczyt stanu, tylko GET, niczego nie zapisuje
```

## Wygląd aktywności

Po każdej zmianie aktywności usługa nakłada cały wygląd docelowej aktywności (PT-35, decyzja 78), publikuje wygląd bieżącej aktywności dla widżetów i w Stream filtruje powiadomienia (PT-36, decyzja 80). Usługa sama słucha:

| Element | Wartość |
| --- | --- |
| Usługa | `org.kde.ActivityManager` |
| Obiekt | `/ActivityManager/Activities` |
| Sygnał | `org.kde.ActivityManager.Activities.CurrentActivityChanged(s activity)` |

Każda droga przełączenia (skrót, `Meta+Q`, widżet, KDE Connect) kończy się tym sygnałem. `tide_daemon/activity_look_monitor.py` uruchamia wtedy silnik w osobnym procesie: `python3 -s $XDG_DATA_HOME/tide/activity-look/scripts/activity_look.py apply --activity ID --source daemon` (kopia z `make install-activity-look`). Silnik porównuje schemat kolorów, styl Plasmy, tapetę pulpitów tej aktywności, wariant ekranu blokady i układ paneli z opisem `theme/activities/<wygląd>.json` i nakłada tylko różnice, w tej kolejności. Wygląd `stream` dostaje aktywność przypisana w `$XDG_CONFIG_HOME/tide/activities.json`, każda inna — `work`.

- **Kolejka.** Jedno nałożenie naraz. Zdarzenia z okna 400 ms i te, które przyszły w trakcie nałożenia, zlewają się w jedno — wygrywa ostatnia aktywność, bez równoległych nałożeń. Instalatory (`make install-*`) czekają na usługę i odwrotnie (`flock` na `$XDG_STATE_HOME/tide/activity-look.lock`).
- **Start usługi (logowanie).** `apply` bez aktywności: silnik pyta o bieżącą, porównuje i naprawia różnice. Tym samym dokańcza przełączenie przerwane przed wylogowaniem (dziennik `$XDG_STATE_HOME/tide/activity-look.json` ze stanem `applying`).
- **Błędy.** Kod 75 — plasmashell albo ActivityManager jeszcze nie odpowiada — to ponowienie po 2 s, potem coraz rzadziej, do 60 s. Inny błąd (np. „wariant niezainstalowany”) trafia do dziennika usługi i czeka na następne zdarzenie. Wyjście silnika jest w `journalctl --user -u tide-daemon` z przedrostkiem „wygląd aktywności:”.
- **Praca i Stream.** W wyglądzie pracy błąd jednego składnika nie blokuje pozostałych (błędy na końcu, kod 1); Stream jest sprawdzany w całości przed pierwszym zapisem. Spóźnione zdarzenie dla aktywności, która już nie jest bieżąca, zmienia tylko jej pulpity — składniki wspólne zostają przy bieżącej.
- **Kopia silnika.** Obecność kopii usługa sprawdza przy każdym uruchomieniu (brak — komunikat raz, żądanie przepada do następnego zdarzenia). Instalatory odświeżają nieaktualną kopię same, `make activity-look-check` i `make activity-look-status` ją zgłaszają. Zatrzymanie usługi zabija trwające nałożenie; dokończy je następny start.
- **Wyłączenie.** `--no-activity-look` albo pusty `$TIDE_ACTIVITY_LOOK_ENGINE` (tak robią testy usługi). Bez zainstalowanego silnika usługa tylko o tym informuje. Bez działającej usługi przełączenie aktywności nie zmienia wyglądu do jej następnego startu.

```bash
make activity-look-check     # pulpit vs opis bieżącej aktywności, tylko odczyt (kod 1 przy różnicach)
make activity-look-status    # przypisania aktywności, dostępne wyglądy, dziennik ostatniego przełączenia
```

### Wygląd bieżącej aktywności dla widżetów (/ActivityLook)

Panele Plasmy są wspólne dla wszystkich aktywności, więc kafelki pracy w Stream chowają się same
(`Plasmoid.status = HiddenStatus`, `widgets/lib/ActivityLook.qml`). Skąd wiedzą, że jest Stream, mówi usługa
(`tide_daemon/activity_state.py`): bieżąca aktywność z `CurrentActivity()` przy starcie i z sygnału
`CurrentActivityChanged`, jej wygląd z `$XDG_CONFIG_HOME/tide/activities.json` (czytane przy każdej zmianie, więc
`make install-stream-activity` działa bez restartu usługi), opis z `$TIDE_ACTIVITY_LOOKS` (domyślnie
`$XDG_DATA_HOME/tide/activity-look/theme/activities`, kopia silnika). Bez opisu albo przypisania — praca.

| Element | Wartość |
| --- | --- |
| Obiekt | `/ActivityLook` |
| Interfejs | `org.tide.Daemon.ActivityLook` |
| `State() -> s` | JSON `{"activity", "look", "workPanels", "notificationsFiltered"}`; `workPanels` fałszywe, gdy opis wyglądu ma `"panels"` inne niż `"work"` (Stream) |
| `Refresh()` | ponowne odczytanie przypisań i opisów (woła je `make uninstall-activity-look` po usunięciu kopii silnika — usługa wraca wtedy do pracy) |
| sygnał `Changed()` | zmieniła się bieżąca aktywność albo jej wygląd |

Bez usługi (albo przed pierwszą odpowiedzią) widżety zostają przy pracy — kafelki pracy widać.

### Filtr powiadomień Stream

Gdy opis wyglądu ma `notifications.allow` (`stream.json`: aplikacje `org.kde.kdeconnect.daemon`, usługi `kdeconnect`
i `streamer`), usługa:

1. wywołuje `org.freedesktop.Notifications.Inhibit("tide", "Aktywność Stream", {})` i trzyma ciasteczko — to ten sam
   stan „Nie przeszkadzać”, co z apletu powiadomień: wyskakują tylko krytyczne (`CriticalInDndMode`, domyślnie
   włączone) i dozwolone, reszta trafia do historii;
2. ustawia w `plasmanotifyrc` `ShowPopupsInDndMode=true` w `[Applications][<desktop entry>]` i
   `[Services][<x-kde-appname>]` dozwolonych (`kwriteconfig6 --notify`, Plasma czyta zmianę od razu). Poprzednie
   wartości — także „brak klucza” — zapisuje przedtem w `$XDG_STATE_HOME/tide/stream-notifications.json`.

Przy wyjściu ze Stream i przy zatrzymaniu usługi wartości wracają dokładnie (surowy napis sprzed Stream, brakujący
klucz jest usuwany, plik wraca co do bajtu; zapis znika po odczycie kontrolnym), potem `UnInhibit(cookie)`.
Inhibicja żyje tyle, co serwer powiadomień: usługa obserwuje nazwę `org.freedesktop.Notifications` — po restarcie
plasmashell prosi o nową i sprawdza wyjątki, a nieudane `Inhibit` ponawia co 2 s, coraz rzadziej, do 60 s. Gdy usługa zginie, Plasma zwalnia inhibicję sama (pilnuje nazwy
wołającego na magistrali), a ustawienia przywraca następny start usługi, jeśli bieżąca aktywność nie jest Stream.
Klucz `ShowPopupsInDndMode` jest globalny — działa przy każdym „Nie przeszkadzać”, także włączonym w pracy — więc
trzymamy go tylko na czas Stream (decyzja 80). Rdzeń Streamer wysyła dziś `notify-send -a Streamer` bez wskazówek,
więc Plasma liczy go do „@other” (razem z powiadomieniami agentów) — do przepuszczenia potrzebuje
`-h string:x-kde-appname:streamer` (zgłoszone do repozytorium streamer).

## Monitory PC ⇄ Xbox

Wejście prawego monitora dla aktywności Stream (PT-38, decyzja 81): `tide_daemon/monitors.py` (logika i polecenie
`monitors.py status|pc|xbox [--json]`, kody 0/1/2), `monitors.toml` (złącze i kody wejść VCP 0x60),
`monitors_service.py` (obiekt D-Bus, wątek roboczy, powrót na PC po wyjściu ze Streamu). Układ ekranów w KWin
się nie zmienia — numeracja ekranów w Plasmie idzie za priorytetem, a jego zmiana przenosiła dock Stream.

| Element | Wartość |
| --- | --- |
| Obiekt | `/Monitors` |
| Interfejs | `org.tide.Daemon.Monitors` |

| Metoda / sygnał | Sygnatura | Opis |
| --- | --- | --- |
| `Status()` | `→ s` | `{layout, target, available, error, interrupted, since, monitor, switching, result}` z pliku stanu (bez DDC); `layout` — `pc` albo `xbox` (przerwane przełączenie liczy się jak `xbox`), `available` — jest `ddcutil`, `result` — ostatnie przełączenie `{ok, layout, changed, error, note, auto, at}` albo `null` (`auto`: powrót na PC po wyjściu ze Streamu) |
| `SetLayout(s layout)` | `→ s` | `pc` albo `xbox`; od razu `{accepted, error}` (odmowa: nieznany układ, przełączenie już trwa), samo przełączenie w wątku roboczym |
| sygnał `Changed()` | | przełączenie w toku albo jego wynik się zmienił |

- **Przełączenie:** `ddcutil detect` (magistrala I²C po złączu DRM, bloki „Invalid display” pominięte), odczyt
  `getvcp 60` i — gdy wejście jest inne — `setvcp 60 0x11|0x0f --noverify`, do trzech prób co 1 s. Przed zapisem
  stan dostaje `pending`; stan na dysku (`$XDG_STATE_HOME/tide/monitors.json`) to ostatnie potwierdzone wejście,
  bo na HDMI monitor zwykle nie odpowiada przez DisplayPort.
- **Błędy:** brak `ddcutil` albo brak odpowiedzi DDC — błąd z prośbą o przełączenie wejścia przyciskiem na monitorze,
  stan zostaje przy poprzednim wejściu. Przełączenia z usługi i z polecenia — po kolei pod `monitors.lock`.
- **Praca:** wyjście ze Streamu i start usługi w pracy ze stanem `xbox` (albo przerwanym) przełączają na PC
  (źródło „czy Stream” — [Skróty Stream](#skróty-stream)); wyjście w trakcie przełączania na Xbox — po jego końcu.
  Nieudany automatyczny powrót to powiadomienie „Prawy monitor został na Xboxie” (bez `--no-notifications`).
- **Wynik:** `{ok, layout, changed, error, note}`; `note`, gdy wejścia nie dało się odczytać i liczy się ostatnio
  potwierdzone.
- **Testy i wyłączenie:** `--no-monitors`; `$TIDE_MONITORS_BIN` wskazuje katalog z `ddcutil` zamiast PATH (testy:
  atrapy z `tests/fake_screens.py`, usługa w testach — nieistniejący katalog).

## Skróty Stream

Pause, Meta+Pause i Meta+B działają tylko w aktywności Stream (PT-38, decyzja 81, `tide_daemon/stream_shortcuts.py`).
Usługa nie wystawia obiektu. „Czy Stream” mówi `tide_daemon/current_activity.py`: słucha
`org.kde.ActivityManager.Activities.CurrentActivityChanged`, przy starcie pyta o `CurrentActivity` (odpowiedź
starsza niż ostatni sygnał przepada) i wysyła `changed(aktywność, stream)`; podpięcie odbiorców jest w `app.py`.
Stream to aktywność przypisana do wyglądu `stream` w `$XDG_CONFIG_HOME/tide/activities.json`.

- **Wejście do Streamu:** najpierw plik stanu ze znacznikiem `entered` (zawsze, także gdy nic nie zabieramy),
  potem klawisz zajęty przez inną akcję (dziś Meta+B — `org_kde_powerdevil/powerProfile`) jest
  jej odbierany `setForeignShortcut`, a pełna lista jej klawiszy trafia wcześniej do
  `$XDG_STATE_HOME/tide/stream-shortcuts.json`; potem `doRegister` i `setShortcut(…, SetPresent|NoAutoloading)` dla
  `tide_stream/mic` (Pause), `tide_stream/privacy` (Meta+Pause) i `tide_stream/brb` (Meta+B).
- **Naciśnięcie:** sygnał `org.kde.kglobalaccel.Component.globalShortcutPressed` na `/component/tide_stream` →
  `kamp mic.toggle`, `kamp privacy.toggle`, `kamp brb.toggle` w osobnym procesie (`$TIDE_KAMP`, PATH albo
  `~/.local/bin/kamp`); kod 2 to „rdzeń Streamer nie działa” w dzienniku.
- **Wyjście ze Streamu, zatrzymanie usługi, start w pracy z pozostałym plikiem:** `unregister` akcji `tide_stream`
  i oddanie odebranych klawiszy (zapisana lista, a gdy akcja zmieniła się w trakcie — tylko dopisany klawisz). Tylko
  gdy pliku nie ma, a nasze skróty są w rejestrze — klawisz wraca do akcji, która ma go w `defaultShortcut`
  (kandydaci z `allMainComponents` i `allActionsForComponent`); z plikiem klawisz zdjęty przed Streamem zostaje zdjęty. W pracy bez niczego do oddania usługa tylko czyta rejestr.
- **Wątek i ponowienia:** wywołania KGlobalAccel idą po kolei w jednym wątku roboczym (pętla usługi nie czeka),
  każde zadanie bierze ostatni żądany stan; błąd KGlobalAccel to ponowienie co 5 s (najwyżej 12 razy na zdarzenie).
- **Awaria w Stream:** gdy usługa padnie w aktywności Stream, Pause, Meta+Pause i Meta+B zostają przechwycone przez
  `tide_stream` (bez reakcji) do jej restartu — systemd podnosi ją po 5 s, a start w pracy oddaje klawisze.
- **Kopia zapasowa w trakcie Streamu:** `make install` (i `make backup`) kopiuje `kglobalshortcutsrc`; kopia
  zrobiona w Stream ma Meta+B przy `tide_stream`, a profil zasilania tylko z Battery. Po przywróceniu takiej kopii
  (`make restore`, działa od następnego logowania) usługa przy starcie w pracy widzi skróty `tide_stream` bez pliku
  stanu: usuwa je i oddaje Meta+B akcji, która ma go w domyślnych. Bezpieczniej jednak robić kopie w pracy.
- **Wyłączenie:** `--no-stream-shortcuts`; pusty `$TIDE_KAMP` — naciśnięcia nic nie wołają (tak robią testy).

## Uruchamianie

```bash
make install-daemon          # kopia do $XDG_DATA_HOME/tide/daemon, usługa systemd, start
make uninstall-daemon        # zatrzymanie i usunięcie
journalctl --user -u tide-daemon -f

# Podgląd bez D-Bus (jednorazowy JSON):
/usr/bin/python3 daemon/tide-daemon.py --print
/usr/bin/python3 daemon/tide-daemon.py --print-worktrees      # tylko odczyt, niczego nie usuwa
/usr/bin/python3 daemon/tide-daemon.py --print-coordination   # koordynatorzy i tablice (YouTrack tylko GET)
/usr/bin/python3 daemon/tide-daemon.py --print-board          # tablica pracy (tylko odczyt)
/usr/bin/python3 daemon/tide-daemon.py --print-docker         # kontenery i miejsce na dysku (tylko GET)
/usr/bin/python3 daemon/tide-daemon.py --print-models         # serwery modeli lokalnych i role (tylko GET)
# Jednorazowy raport poranny (bez D-Bus, powiadomienia i pliku stanu) do wskazanego katalogu:
/usr/bin/python3 daemon/tide-daemon.py --report --report-days 1 --report-dir /tmp/raport
# Opcje diagnostyczne: --claude-dir, --codex-dir (można powtórzyć), --workspace, --worktree-depth, --no-processes,
#                      --no-notifications, --no-krunner, --search-delay 60, --no-tab-marks,
#                      --now 2026-10-26T12:00:00Z, --idle-after 30, --no-idle,
#                      --report-dir, --report-at 07:00, --no-report, --report-delay 120,
#                      --report-summary claude|local|rules,
#                      --limits-poll 120, --no-limits, --print-limits, --no-tasks,
#                      --coordination-days 7, --no-coordination, --no-youtrack (też bez synchronizacji tablicy),
#                      --board-dir DIR, --no-board, --print-card-docs K-12, --no-docker,
#                      --models-poll 15, --no-models, --no-activity-look
```

Pliki instalacji: `$XDG_DATA_HOME/tide/daemon/` (z kopią palety `palette.json`, z której raport poranny bierze kolory i czcionki), `$XDG_CONFIG_HOME/systemd/user/tide-daemon.service`, `$XDG_DATA_HOME/dbus-1/services/org.tide.Daemon.service` (start na żądanie, gdy widżet zapyta, a usługa nie działa) i `org.tide.Daemon.KRunner.service` (to samo dla KRunnera). Usługa używa `/usr/bin/python3` z systemowym PyQt6 — `python3` z pyenv go nie ma.

## Testy

`make test` (unittest, `/usr/bin/python3`):

- `daemon/tests/test_agents_core.py` — rdzeń na syntetycznych dziennikach z `daemon/tests/fixtures/` (zamrożony zegar, procesy podawane jawnie),
- `daemon/tests/test_dbus_contract.py` — usługa na prywatnej magistrali (własny `dbus-daemon` z konfiguracją bez katalogów usług, `privatebus.py`): te same dane co z rdzenia, oraz zmiana stanu w ciągu 3 s po dopisaniu wpisu; bez `dbus-daemon` test jest pomijany,
- `daemon/tests/test_agents_reply.py` — PT-29: heurystyka pytania na końcu tury (grilling „Pytanie 10: …?”, emoji, pogrubienie, lista wariantów, lista pytań, krótkie zdanie po pytaniu, „Czekam na odpowiedź”; pytanie retoryczne w podsumowaniu, kod i tabele — nie), Claude Code (czeka także po 2 h, odpowiedź użytkownika, narzędzie po tekście, przerwanie; kompaktowanie bezczynnej sesji, także większe niż ogon, i status „idle” — dalej czeka, prawdziwa wiadomość po nim kasuje; echo `/model` nie kasuje, skill z polecenia tak; `AskUserQuestion` po kompaktowaniu; ogon rośnie do ostatniej tury, z górną granicą), karta K-20 z koordynacją 1/1 scaloną i czekającą sesją — Czeka na mnie, nie Gotowe, dopisek pod pytaniem, status `shell` z pytaniem (`reply`) i z `AskUserQuestion` — czeka, bez pytania — pracuje w tle do `BACKGROUND_STALE`, potem `idle`, Codex (`task_complete`, tura zaplanowanego zadania, wiadomość użytkownika), jedno powiadomienie na pytanie z jego treścią, Meta+A, karta K-20 założona i połączona ze skończoną sesją (W toku, z pytaniem — Czeka na mnie; skończony koordynator — Do sprawdzenia, „do poprawy” tylko po przeniesieniu z Do sprawdzenia, `moved-from` z `BoardMonitor`),
- `daemon/tests/test_attention.py` — wybór najdłużej czekającej sesji i powiadomienia bez powtórzeń na fixtures; `Notifier` na prywatnej magistrali z atrapą serwera powiadomień (`fake_notifications.py`: Notify, przycisk „Przejdź”, zamknięcie); cała usługa: nowa czekająca sesja → powiadomienie,
- `daemon/tests/test_tabmarks.py` — stan sesji → znacznik karty (z tabelą procesów), oznaczanie, zmiana stanu, przywracanie, zmiana tytułu przez użytkownika, zamknięta karta, restart z plikiem stanu i bez niego — na atrapie Konsole w pamięci; zaplecze D-Bus na atrapie Konsole na prywatnej magistrali (`fake_konsole.py`),
- `daemon/tests/test_windows.py` — przejście do okna przy kilku oknach Konsole (PT-33): okno karty z `/Windows/*` zamiast nieaktualnego `KONSOLE_DBUS_WINDOW` (nieistniejące okno, karta przeciągnięta do innego okna, zapas ze środowiska), wybór okna przez skrypt KWin wykonany w `QJSEngine` z atrapą `workspace` (dowolna kolejność okien, tytuł będący prefiksem innego, okno pokazujące inną kartę, tytuł docierający do KWin po starcie skryptu, okna innych procesów i okna nienormalne), chwilowy znacznik przy identycznych tytułach (przywrócenie, zmiana użytkownika, drugie przejście i zegary poprzedniego przejścia odpalane osobno, nieudany skrypt, błąd w zegarze, częściowo nieudane dopisanie, zamknięcie usługi), czekanie na znacznik i skrypt kończący, zegar usuwania skryptu (`KWinScripts` na atrapie magistrali) i współpraca ze znacznikiem stanu karty; metody okien zaplecza D-Bus na atrapie Konsole z oknami (`fake_konsole.py`),
- `kwin/tests/` — skrypt KWin wykonany w `QJSEngine` z atrapami `registerShortcut`/`callDBus`, wykrywanie kolizji skrótu, instalacja i odinstalowanie z atrapami `kpackagetool6`, `kwriteconfig6`, `dbus-send`,
- `daemon/tests/test_worktrees_core.py` — rdzeń worktrees na prawdziwych, tymczasowych repozytoriach (`gitrepos.py`: scalony, ze zmianami, z niescalonymi commitami, z agentem, odłączony, bez gałęzi głównej, główny); git owinięty skryptem zapisującym wywołania, żeby potwierdzić brak `--force`, `-D` i operacji sieciowych,
  Do tego grupy (`active`, `clean`, `decide`, `main`, `missing`) przy zamrożonym zegarze, usuwanie tylko wybranych ścieżek i `prune` wyłącznie dla brakujących katalogów,
- `daemon/tests/test_worktrees_sessions.py` — tytuły z nazw gałęzi i powiązanie z sesjami na syntetycznych dziennikach (najświeższa i najgłębsza, rodzaje tytułów, podagenci, pliki starsze niż 30 dni, duże pliki, pamięć podręczna),
- `daemon/tests/test_tasks_core.py` — nowe zadanie bez D-Bus na prawdziwych, tymczasowych repozytoriach: argv obu narzędzi (cudzysłowy, `$(…)`, nowe linie, emoji, myślnik na początku, zrzuty) sprawdzone tak, jak dociera do atrapy agenta przez `launch.py`, nazwa gałęzi z polecenia, worktree od gałęzi głównej (master nietknięty, wpis w `info/exclude`), katalog `<repo>-worktrees`, odmowy bez żadnych zmian (gałąź główna, istniejąca, zła nazwa, zmiany na master, brak gałęzi głównej, brak programu, repozytorium spoza katalogu projektów, zrzut spoza katalogu zadań, jedno słowo, `addDirs` spoza `<tablica>/docs/` i repozytoriów projektów tablicy), `--add-dir` obu narzędzi, wycofanie (gałąź z nowymi commitami zostaje), przejęcie i anulowanie pliku zadania, wybór okna Konsoli i tryb karta/okno na atrapach, znacznik `newTask`,
- `daemon/tests/test_tasks_local.py` — nowe zadanie na modelu lokalnym bez prawdziwego serwera i agentów: argv i środowisko Codexa i Claude Code dla Ollamy na tym komputerze, na innym komputerze i serwera `openai`, z tokenem i bez (token nigdy w argv ani w pliku zadania), kontekst (z roli, z załadowanego modelu, za mały dla Claude Code), `launch.py` z atrapą agenta zapisującą środowisko (token z pliku tylko w środowisku Codexa, u Claude Code przez apiKeyHelper — nigdy w środowisku, powłoka po agencie z pierwotnymi wartościami użytkownika, nieczytelny token — agent nie startuje), model z „-” w jednym argumencie, Planner z rolą `agent` i atrapą odczytu serwera (odmowy `local-unset`, `local-unavailable`, `bad-model`, tytuł „· lokalnie”, zapamiętany model), wykrywanie sesji na modelu lokalnym z listy dopuszczonych (Codex `model_provider`: ollama, lmstudio, oss, `tide-`; Claude Code `message.model`: znany z `/Models` albo `nazwa:tag`; Azure, Bedrock, bramki — nie) i dopisek w tytule karty,
- `daemon/tests/test_tasks_dbus.py` — `/Tasks` przez prywatną magistralę z prawdziwą usługą i atrapami `konsole` (uruchamia polecenie z `-e`), `systemd-run`, `claude` i `codex` (zapisują argv): `Repos`, `Defaults`, `SuggestBranch`, `Start` z dokładnym poleceniem w agencie, wycofanie, gdy Konsola nie uruchomi zadania, odmowy i praca w głównym katalogu, `addDirs` z tablicy dla Codeksa (worktree) i Claude Code (katalog projektu) oraz odmowa `bad-add-dir`,
- `daemon/tests/test_worktrees_dbus.py` — `List()`, `Remove()`, `Refresh()`, `Prune()` i `ShowWindow()` przez prywatną magistralę; agent symulowany sesją Claude Code w worktree,
- `daemon/tests/test_krunner_index.py` — indeks historii na syntetycznych dziennikach (`history.py`, `fixtures/history`): tytuły obu narzędzi (także z bazy stanu Codexa zbudowanej w teście), pierwsza wiadomość z początku dużego pliku, odcięcie po 30 dniach, odczyt tylko zmienionych plików,
- `daemon/tests/test_codex_homes.py` — kilka katalogów danych Codexa na świecie w katalogu tymczasowym (`~/.codex`, `~/snap/codex/33`, `34`, `current` → `34`; nigdy prawdziwe katalogi — `tests/__init__.py` ustawia też nieistniejący `$CODEX_HOME` dla usług uruchamianych bez `--codex-dir`): domyślna lista i `$CODEX_HOME`, `--codex-dir` powtarzane, rozwiązywanie `current` (także po odświeżeniu snapa), starsza rewizja pomijana, kopia tego samego pliku w dwóch katalogach liczona raz (Agents, KRunner, worktrees, raport, koordynacja), segmenty wątku jako jedna pozycja w Agents i KRunnerze, a osobno w raporcie i worktrees, przepięcie obserwacji na nową rewizję po odświeżeniu snapa, „Wznów” w raporcie dla sesji ze snapa, `is_snap` tylko pod `~/snap/codex`, przypisanie procesu ze ścieżką z numerem rewizji i Meta+A do karty Konsole, nowe pliki w obu katalogach zauważone przez obserwację katalogów (bez przeglądu co 30 s), nazwy wątków i baza stanu ze snapa w KRunnerze, wznowienie Codexem ze snapa albo spoza niego,
- `daemon/tests/test_codex_desktop.py` — Codex Desktop: odnośnik tylko z UUID, linia `Exec` (kody pól, cudzysłowy, brak programu), szukanie pliku `.desktop` i `$TIDE_CODEX_DESKTOP`, polecenie jako lista argumentów w `systemd-run --scope`, zmienne ekranu z `systemctl --user show-environment`; uruchomienie z atrapą `chatgpt` (`fakebin.py`) — nigdy prawdziwej aplikacji; wątek główny podagenta (kilka poziomów, nieznany rodzic, pętla), zwijanie podagentów i stan najpilniejszy, podagent bez rodzica, `AgentMonitor.activate` i Meta+A (otwarcie wątku głównego, podniesienie okna `ChatGPT`) z atrapą aplikacji,
- `daemon/tests/test_krunner_core.py` — dopasowanie i ranking, `ai `, teksty po polsku, polecenia `Run` (wznowienie Claude/Codex w Konsoli, przejście do okna żywej sesji, wątek Codex Desktop w aplikacji, projekt, worktree, Rider) z zapisem zamiast uruchamiania; leniwe budowanie indeksu,
- `daemon/tests/test_krunner_dbus.py` — `Match`, `Actions`, `Run` przez prywatną magistralę z sygnaturami KRunnera 6; programy to atrapy (`fakebin.py`: `systemd-run`, `konsole`, `rider`, `claude`, `codex` tylko zapisują wywołanie),
- `daemon/tests/test_krunner_semantic.py` — wyszukiwanie po znaczeniu (PT-19) z atrapą modelu („pojęcia” zamiast bge-m3): tekst sesji (metadane, tickety, `strip_secrets`, przycięcie), plik indeksu (0600/0700, bez tekstów, zepsuty albo obcy — od nowa), indeksowanie przyrostowe, okno 30 dni i limit pozycji, przebudowa po zmianie modelu i wymiarów, wyłączenie (plik znika od razu, czekająca partia przerwana), błędy jako stan, `queue.IDLE` i cisza po innym modelu przed każdą partią, ranking, zapytanie (bez poleceń, adresów, ścieżek i sekretów, `HIGH`, pamięć, nowsze unieważnia starsze, serwer nie odpowiada, budżet czasu), łączenie w `KRunnerCore` (słowa pierwsze, bez powtórzeń, dopisek) oraz `models.embed` na atrapie serwera HTTP (Ollama, OpenAI, złe odpowiedzi, chmura, rola za `default`); w `test_krunner_dbus.py` — usługa z atrapą Ollamy (`--search-delay 0`): indeks na dysku, wynik znaczeniowy w `Match`, a po wyłączeniu serwera same słowa w mniej niż 3 s.
- `apps/newtask/tests/` — okno nowego zadania: logika formularza w `qml6`, zrzuty okna z `--data --grab`, zrzuty ekranu (przeciągnięte, ze schowka, atrapa Spectacle, sprzątanie), backend na prywatnej magistrali z prawdziwą usługą, drugie uruchomienie, instalacja z profilem Konsoli; `kwin/tests/test_new_task_shortcut.py` — skrót Meta+Alt+A,
- `apps/board/tests/test_view.py` (PT-43) — okno uruchomienia z karty z tym, co okno nowego zadania: zrzuty ekranu (przeciąganie, Ctrl+V, „Zrób zrzut” z atrapą Spectacle, usuwanie, sprzątanie po Anuluj, katalog zostaje po starcie) w `images` prośby `Start`, model chmura/lokalny z `/Models` (wyłączony z powodem, linia agenta, gałąź i `tool` za programem agenta lokalnego, blokady), `imagesDir` i stan modeli z prawdziwej usługi,
- `apps/worktrees/tests/` — okno: QML na przykładowych danych (`qml6`, offscreen), launcher z `--data --grab`, backend okna na prywatnej magistrali z prawdziwą usługą (Remove tylko dla zaznaczonych), drugie uruchomienie, instalacja z atrapami `update-desktop-database` i `kbuildsycoca6`,
- `daemon/tests/test_idle.py` — bezczynność na atrapie kompozytora Wayland (`fake_wayland.py`: prawdziwe gniazdo i format wiadomości, zamrożony zegar): próg, wejście liczy czas od nowa, wybór protokołu (v2, v1, `org_kde_kwin_idle`), brak protokołu, błąd protokołu, zerwane połączenie i ponowne połączenie; `IdleState` na zamrożonym zegarze; obiekt `/Idle` usługi na prywatnej magistrali (właściwości, `IsIdle`, sygnał `IdleChanged`, próg z palety, brak kompozytora, `--no-idle`). Usługi w testach dostają nieistniejące gniazdo Wayland, więc nigdy nie łączą się z kompozytorem pulpitu,
- `daemon/tests/test_report_schedule.py` — termin raportu (pierwsze logowanie w dobie, 07:00, praca po północy, zmiana czasu, wyłączenie), okres (pierwszy, od kotwicy poprzedniego — godzina z terminu albo wcześniejszy koniec, wieczorne `Generate()` o 23:54 nie przesuwa następnej doby, weekend, inna godzina `at`, najwyżej 7 dni, ponowne wygenerowanie, naprawa stanu sprzed tej zasady), jedno powiadomienie na dobę, ustawienia z pliku i opcji, stan, retencja 30 dni (tylko katalogi raportów, bez dowiązań),
- `daemon/tests/test_report_generate.py` — raport z syntetycznego świata (`reportfix.py`: repozytoria z datowanymi commitami, dzienniki Claude Code i Codexa, zaplanowane zadanie, podagent, sesje sprzed okresu) przy zamrożonym zegarze: projekty z ruchem i bez, commity po gałęziach, znacznik agenta, ostatnie wiadomości, przebiegi zadań, worktrees, pliki i uprawnienia, podmiana katalogu, stan prac na dzień (cicha noc z czekającą sesją, niescaloną gałęzią i kartą tablicy w toku — raport niepusty; naprawdę nic — zdanie „nic się nie dzieje”), escapowanie (skrypt w temacie commita, tytule sesji, wiadomości agenta i nazwie pliku), CSP, brak zasobów z sieci; odczyt Limitów AI z atrapy API (token tylko w nagłówku, nigdy w błędzie),
- `daemon/tests/test_report_standup.py` — „Na daily”: fakty z syntetycznego świata z tablicą koordynacji (tickety we wszystkich stanach, ticket sprzed okresu, gałąź scalona w okresie, gałąź scalona do gałęzi integracyjnej, gałąź z samymi instrukcjami agentów, wewnętrzna gałąź `worktree-agent-*` w osobnym worktree, sesja spoza repozytoriów, sesje przy gałęziach z pierwszym poleceniem), praca czy porządki (sygnały commitów, ścieżek i tekstów, obszary zmian, ticket o trackerze z pytaniem w „Czeka na mnie”), plan z YouTrackiem bez tablicy (cel, uwagi, opis przez mapę migracji), śmieci w odpowiedzi modelu („nie ma decyzji”, liczniki gałęzi, „bez review”, gotowe ≠ review), reguły (grupowanie po ticketach i gałęziach, zbiorcza pozycja, limit pozycji), wejście dla modelu (usuwanie sekretów, `~`, limity na projekt i całość), walidacja odpowiedzi (HTML w tekście, wymyślone ID, nieznany projekt, brakujące pola, znaki sterujące, długości), atrapa `claude` przez `$TIDE_CLAUDE_BIN` (gotowy JSON i argv bez narzędzi, kod błędu, śmieci, `is_error`, zawieszenie z zabiciem po czasie, brak programu, pamięć `summary.json`), strony (indeks jako przegląd z kartami, licznikami i wyróżnieniem czekających, „Poza projektami” bez stron, strona projektu z własnym „Na daily”, kopiowaniem tylko tego projektu i zwiniętymi porządkami, escapowanie, stopka), ustawienia `summary`/`summaryModel`. Prawdziwego `claude` testy nie wołają: `daemon/tests/__init__.py` i usługi w testach mają pusty `TIDE_CLAUDE_BIN`,
- `daemon/tests/test_report_dbus.py` — cała usługa na prywatnej magistrali: raport przy starcie, jedno powiadomienie „Raport poranny gotowy” z „Otwórz” (atrapy `systemd-run`, `xdg-open`; „Otwórz” oznacza przeczytany), `Generate()`, `Latest()`, `Status()`, `Open()` i sygnał `Changed`, `Days()`, `Day()`, `OpenDay()` i `MarkRead()` dla okna Tide (starsza doba nie gasi kropki), nowa doba po restarcie znowu nieprzeczytana, introspekcja, brak powtórki po restarcie, wyłączony termin,
- `daemon/tests/test_report_data.py` — `report.json` z syntetycznego świata (kolejność kart, „Na daily”, liczby, teksty do schowka równe `render.daily_text`, surowe napisy bez encji, 0600), kolejność czekające → zwykłe → same porządki, `since` (także przez zmianę czasu), lista dób (najnowsze pierwsze, raport sprzed `report.json`, zepsuty plik, dowiązania, złe nazwy), `Day()` (brak, złe nazwy, legacy, najnowszy i nieprzeczytany), ścieżki stron (`index`, `../`, dowiązanie), `ReportMonitor.mark_read` i `open_day` (najnowsza doba gasi kropkę, starsza i inny katalog — nie),
- `apps/reports/tests/` — widok „Raporty” okna Tide: pakiet bez kolorów na sztywno i z każdym tekstem jako zwykły tekst, logika `reports.js`, `ReportsView` w `qml6` offscreen z atrapą backendu (doby i kropka, karty, panel, kopiowanie, przeczytanie tylko na wierzchu aktywnego okna, postęp „Wygeneruj teraz”, pusty stan, `showItem`, raport sprzed `report.json`, HTML w tekstach raportu dosłownie), backend z `--data` (tagi tablicy, plakietka, schowek) i na prywatnej magistrali z prawdziwą usługą (`Days`, `Day`, `MarkRead`, `OpenDay` przez atrapę `xdg-open`, `Generate` z postępem), zrzuty z `--data`,
- `daemon/tests/test_report_read.py` — przeczytanie bez D-Bus: `Status()` (bez raportu, bez stanu, stary plik stanu), przejścia nowy → przeczytany → ten sam dzień po `Generate()` → nowa doba, nieudane otwarcie, „Otwórz” w starym powiadomieniu, KRunner przez `ReportMonitor`,
- `widgets/tests/test_widget_report.py` — link „Raport” w nagłówku paska i w popupie agentów w `qml6` offscreen (nowy, przeczytany, brak raportu, wczoraj, starszy, usługa nie działa), zrzuty, wysokość nagłówka i szerokość popupu oraz kafelka w docku bez zmian, kliknięcie,
- `daemon/tests/test_limits_core.py` — Limity AI bez Qt: odczyty z `/status` i etykiety resetu (także przy zmianie czasu), okna i historia (dopisywanie, restart, 14 dni, zepsute linie), prognoza na syntetycznych seriach (równe tempo, zrywami, przerwa, długi postój, przejście przez reset, tydzień z nocami, za mało danych, nieaktualne dane), powiadomienia raz na okno i po restarcie, konfiguracja z palety, klient API na atrapie QuotaApp (token tylko w nagłówku, nigdy w błędach), `--print-limits` bez zapisu,
- `daemon/tests/test_limits_dbus.py` — obiekt `/Limits` na prywatnej magistrali z atrapą API QuotaApp (HTTP w wątku testu) i atrapą serwera powiadomień: `Forecast()`, sygnał `Changed`, powiadomienia, historia w `$XDG_STATE_HOME`, brak tokenu w plikach, restart bez powtórek, usługa niedostępna, `--no-limits`. Usługi w testach dostają nieistniejący `LIMITY_CONFIG`, więc nigdy nie pytają prawdziwej QuotaApp,
- `daemon/tests/test_coordination_core.py` — koordynacja bez Qt na syntetycznym świecie o kształcie prawdziwych dzienników (`coordfix.py`): koordynator Claude Code (zlecenia w tle i na pierwszym planie, powiadomienia z kolejki i z wiadomości, szum kolejki i kopii dla API, raport przez SubagentHandback, SendMessage, TaskStop, AskUserQuestion, odczyt przyrostowy), drzewo wątków Codexa (wnuk, recenzent zgód), plany poprawne i błędne, ID ticketów (prefiks, nazwy zadań, mapa migracji, ID koordynatora), formaty review, reguły stanów (praca, plan, YouTrack, przerwanie, grupowanie bez ID), YouTrack na atrapie serwera HTTP (tylko GET, token tylko w nagłówku, nigdy w błędach, pamięć 5 min, inny serwer, zmienna z pliku `.env`), adnotacje `/Agents`, plik dla raportu (0600, bez raportów i poleceń), cel diffu i worktree,
- `daemon/tests/test_coordination_dbus.py` — `/Coordination` na prywatnej magistrali z atrapą YouTracka i atrapami programów: `Coordinators`, `Board`, `RunDetail`, sygnał `Changed` po dopisaniu powiadomienia, `Activate` (wznowienie w Konsoli), `ShowWindow`, pole `coordination` w `/Agents`, plik dla raportu bez tokenu, wyłącznie GET do YouTracka, `--no-coordination`, `--no-youtrack`,
- `daemon/tests/test_coordination_describe.py` — opisy kart: reguły (formułki poleceń, stan i review słowami, raport implementacji), fakty bez sekretów i bez całych poleceń, walidacja odpowiedzi modelu (tylko znane tickety, same napisy, przycięcia), pamięć podręczna i odstępy (SETTLE, GAP, RETRY, stare koordynatory, partie), plik 0600 i jego walidacja, tryb `rules` bez wywołań, monitor z opisem w tle — model to zawsze atrapa,
- `daemon/tests/test_coordination_integration.py` — KRunner („sesje”, filtr słowami, uruchomienie tylko znanych), „Plany i tickety” w raporcie (tablica z pliku, plany z repozytoriów, escapowanie, brak pliku i błędy), `Launchers.show_sessions`,
- `apps/sessions/tests/` — okno Tide Sesje: pakiet bez kolorów na sztywno, logika `sessions.js` w QJSEngine, zrzuty offscreen z `--data` (tablica, Codex, wynik podagenta) bez ostrzeżeń QML, dane po `tools/sessions_preview.py --sanitize` bez treści, launcher na prywatnej magistrali z prawdziwą usługą i drugie uruchomienie, instalacja,
- `daemon/tests/test_board_core.py` — tablica bez Qt na tymczasowych repozytoriach z własną konfiguracją gita: frontmatter (nieznane klucze, komentarze i treść po zapisie, cudzysłowy, nowe linie), założenie przy pierwszym zapisie (znacznik, tagi, szablony, brak remote, autor z konfiguracji i „Tide” bez niej, cudzy katalog nietknięty), dokładny format pliku karty, ID (max + 1, bez ponownego użycia, zepsuty plik zajmuje numer), kolejność i przenoszenie z komunikatami commitów, odmowy kolumn wyliczanych, walidacja pól i ID (także `../`), projekty zakładane przy karcie z domyślnym `ai-docs`, tagi (dziedziczenie, kolory z palety, zmiana nazwy w kartach i projektach), szablony trybów (pola, nadpisanie projektu, nieznane klamry), commit zmian z zewnątrz osobno od naszych, złe pliki w `errors`, pomijanie repozytorium tablicy w skanerze po znaczniku,
- `daemon/tests/test_board_robustness.py` — trudne przypadki: nieudany commit (karta zostaje, `commitError`, bez duplikatu, zaległy commit z własnym komunikatem), osierocona blokada indeksu (git zabity po czasie, stara blokada, blokada żywego gita na atrapie `/proc`), revert z konfliktem (bez commita „sync”, `git-busy`, wpisy w `errors`, znaczniki konfliktu jako błąd pliku), numery kart po revercie (licznik poza historią; historia gita tylko raz, gdy licznika brak, nigdy w migawce), znaczniki konfliktu w opisie jako zwykły tekst, zaległy commit usuniętego pliku i trwały błąd bez blokowania kolejki, błąd bez pliku, katalogu i commita (pusty katalog i istniejąca tablica), dowiązania poza tablicę, FIFO i `/dev/zero`, listy YAML i pliki z Windows, kolejność środkiem i przenumerowanie bez miejsca, brak hooków (także post-commit), projekt po nazwie bez `foo-2`, domyślny tryb `full`, zmiana nazwy tagu bez pliku, katalog dokumentów z `RenderFlow`,
- `daemon/tests/test_board_dbus.py` — `BoardMonitor` w procesie (zmiana z zewnątrz → commit i `Changed`, nasz zapis bez drugiego commita, błędy jako JSON) i `/Board` na prywatnej magistrali: introspekcja, `Snapshot`, `CreateCard` z repozytorium, `UpsertProject`, `UpsertTag`, `UpdateCard`, `MoveCard`, `RenderFlow`, sygnał `Changed`, historia commitów, edycja pliku z zewnątrz, błędy `{ok: false, error, message}` z kodami i bez commita, `--no-board`, `--print-board` bez zapisu. Usługi w testach dostają tymczasowy `TIDE_BOARD_DIR` (`privatebus.py`), więc nigdy nie dotykają prawdziwej tablicy,
- `daemon/tests/test_cardflow_dbus.py` — przepływ karty „Od razu” (PT-58) przez D-Bus na prywatnej magistrali z atrapami `claude -p`, `systemd-run` i `systemctl` (`tests/flowfakes.py`): StartCard → dziennik, gałęzie, kolumna W toku → Do sprawdzenia, brak pliku wyniku, pytanie, strażnik kroku, restart usługi w trakcie kroku, Backlog i Gotowe, dziennik usunięty z zewnątrz, sesja kroku poza /Agents i kafelkiem Praca, worktree kroku zajęty w /Worktrees; katalog testu poza `/tmp/claude-<uid>` (`TIDE_TEST_TMPDIR`, inaczej `build/test-tmp`),
- `daemon/tests/test_cardflow.py` — ten sam przebieg w procesie (`BoardMonitor` + `CardFlowMonitor`, prawdziwy skaner dzienników dla listy agentów), także tam, gdzie prywatna magistrala nie startuje; dodatkowo scalenie zaczęte przed restartem kończy się raz, odmowy bez śladu (wykonawca, wejście, brak prefiksu) i karta bez dziennika bez zdarzeń,
- `daemon/tests/test_board_launch.py` — uruchamianie z karty po stronie tablicy: `RenderLaunch` z prawdziwymi szablonami (pełny, lekki, spec; dokumenty w tablicy i w repozytorium; narzędzie, gałąź i test ręczny z okna; błędy), migracja szablonów (dawny zaczyn bajt w bajt z `fixtures/flows-pt1/` podmieniony, zmieniony przez użytkownika i nadpisanie projektu zostają, zajęte repozytorium), pola uruchomienia karty z walidacją, znacznik „specyfikacja gotowa” (szkic, inny tryb, inna karta, specyfikacja w repozytorium, klucz `card`), `BoardMonitor` (migracja przy starcie, znacznik przy synchronizacji, `ShowWindow("launch:K-12")`, `RenderLaunch` jako JSON),
- `daemon/tests/test_board_multiproject.py` — PT-28 na tymczasowych repozytoriach (toolssuite z worktree karty, mobileapp, legacyinfra) i syntetycznych dziennikach: katalog pracy sesji w tablicy (`board/place.py`: katalog startu, `docs/<slug>/`, `docs/_inbox`), `/Agents` (Claude Code przed i po `cd` do katalogu planów tablicy, Claude Code bez wpisu z katalogiem startu, Codex z `session_meta`), KRunner, raport poranny i koordynator w tablicy (repozytorium, gałąź i plan karty), reguła „uruchomienie z karty” (okno czasu, narzędzie, katalog bez worktree, `launchKey`, najbliższy start, sesje innych kart, przypięta sesja po zmianie cwd/projektu/gałęzi, zakończona przypięta ustępuje nowej), utrwalanie (`sticky`: uruchomienie, jednoznaczna gałąź, dwa dopasowania — nie), `BoardMonitor` z odtworzeniem K-1 (commit „K-1: link session …”, karta w W toku po `cd`, usługa widząca sesję dopiero po `cd`, ręczny `session` bez nadpisania i commita), pole `projects` (zapis, projekt z repozytorium, migawka, walidacja, nieznany slug w pliku), `RenderLaunch` (zdanie o ścieżkach bezwzględnych, lista repozytoriów i zasady, tryb spec, brakujące repozytorium, własny szablon bez `{projects}`, migracja szablonów `tide-flow: 2`), `/Tasks` `addDirs` z repozytoriami projektów (oba narzędzia, dowiązanie, odmowy) i stan karty po gałęzi we wszystkich repozytoriach,
- `daemon/tests/test_youtrack_sync.py` — synchronizacja z YouTrackiem bez Qt (PT-7) na atrapie REST API (`fake_youtrack.py`: GET i POST zgłoszenia z kształtem prawdziwego pola State, wyszukiwanie, dziennik wywołań): zapis przy każdym przejściu karty (kształt POST), bez zapisu i bez sieci bez przejścia, odliczanie 5 s, Archiwum i projekt bez synchronizacji, wyłącznik globalny, przyjęcie stanu przy pierwszym spotkaniu (zmiana po połączeniu, rozwiązane, `open`), plik stanu 0600 przez restart, konflikt (bez nadpisania, notka, `errors`, bez odpytywania w kółko, „weź z YouTracka”, „nadpisz z Tide”, samo wygaszenie), Reopened zamiast Open i brak cofnięcia po zniknięciu pracy, pierwszeństwo ticketu i ręcznego Gotowe bez ping-ponga, parasol, stan z YouTracka i plan lokalny pominięte, Verified z karty koordynatora, Won't fix, najświeższy koordynator, kolejka bez sieci z ponowieniem i przez restart, 503 i odmowa stanu, token nigdy w dzienniku, pliku, migawce i adresie, mapa stanów z ustawień i per projekt, klucze zgłoszeń, lista importu (przypisane, zgłoszone bez przypisania, rozwiązane i cudze poza listą, karta już jest, „nowe” przez restart, tytuł i opis karty),
- `daemon/tests/test_youtrack_board.py` — PT-7 w tablicy: klucz projektu YouTracka i przełącznik (plik, migawka, unikalność, walidacja), pole `youtrack` karty (adres → klucz, walidacja), `BoardMonitor` z `YouTrackSyncMonitor` na atrapie (Gotowe → Verified, powrót → Reopened, konflikt w migawce i rozstrzygnięcie po karcie, import do Backlogu z projektem i bez duplikatów, metody bez YouTracka), `/Board` na prywatnej magistrali z `TIDE_YOUTRACK_CONFIG` (lista, import, przeniesienie → Verified, `ResolveYouTrack`). Usługi w pozostałych testach dostają `fixtures/youtrack-closed.json` (zamknięty port) — nigdy prawdziwy YouTrack,
- `daemon/tests/test_boardplans.py` — standard planu 1.1: klucz `card` (walidacja, ID karty z gałęzi, wybór planu po karcie), plany projektów z `ai-docs: tide` w tymczasowej tablicy (`TIDE_BOARD_DIR`) w `find_plans`, skanerze, tablicy i podsumowaniu koordynatora (`card`), kontroli numerów, raporcie porannym, `tools/next_id.py` i `tools/check_plans.py`; projekty `ai-docs: repo` bez zmian. `daemon/tests/__init__.py` kieruje `TIDE_BOARD_DIR` na nieistniejący katalog — testy nie czytają prawdziwej tablicy,
- `daemon/tests/test_docker_core.py` — Docker bez Qt na atrapie Engine API (`fake_docker.py`: prawdziwy socket unix w wątku testu, kształty odpowiedzi jak w Dockerze 29, odpowiedzi chunked pocięte w środku znaków, strumień `/events`, logi w ramkach, zapis każdego zapytania): `DOCKER_HOST` (unix, tcp/ssh → `unsupported`), brak socketu, odmowa, brak uprawnień, zawieszony Docker; grupy compose, repozytorium z worktree (także spoza katalogu projektów), „bez compose”, testcontainers, kod 137 i reguły awarii (0, 143, OOM, `dead`, restart, `created` po nieudanym starcie, 137 po `Stop` z Tide), porty bez powtórek i `likelyHttp`, czasy z nanosekundami, `inspect` tylko po zmianie stanu (nie tekstu „Up 3 hours”) i pełne odświeżenie; panele WWW baz w `likelyHttp`; kategorie dysku (nic w użyciu), wolumeny anonimowe zatrzymanych, prune tylko z listy id w bezpiecznej kolejności (regresja: `{"volumes": "false"}` bez żadnego wywołania), 404 z prune, dokładne metody, ścieżki i zapytania akcji, `Remove` działającego bez wywołania, kolejność zależności w projekcie, częściowa porażka `restart` projektu, limity pojedynczych akcji poniżej 25 s, jeden termin na zapytanie (odpowiedź sącząca się po bajcie) i na akcję (po terminie bez wywołań), znacznik „zatrzymany z Tide” przy odświeżeniu w trakcie `stop` i przy równoczesnym dopisywaniu, demultipleksowanie logów (stderr, TTY, linie i znaki UTF-8 rozcięte między ramkami, ucięta ostatnia ramka, zepsuty nagłówek w środku), statystyki równolegle z łącznym limitem czasu, filtr zdarzeń,
- `daemon/tests/test_docker_monitor.py` — `DockerMonitor` w pętli Qt na atrapie: zdarzenie → `Changed`, zdarzenia `exec_*` bez odświeżenia, seria zdarzeń w jednym odświeżeniu, zerwany strumień i ponowne połączenie, odpytywanie bez Dockera aż się pojawi, zniknięcie Dockera, pamięć `Stats`/`Disk` (bez wyniku sprzed akcji), odświeżenie bez zmiany bez `Changed`, zadania `Prune`/`GroupAction` z `actionFinished` i odmowa bez zadania, karty z logami i powłoką (argv z ID i `-H` w pliku zadania, osobny katalog każdej karty — dwa szybkie `OpenShell` nie zamieniają się kontenerami, `workdir` w katalogu domowym, wycofanie przy nieudanej Konsoli), akcja czekająca w zajętej puli dostaje odmowę na czas,
- `daemon/tests/test_docker_dbus.py` — `/Docker` na prywatnej magistrali z prawdziwą usługą i atrapą Engine API: introspekcja, `Snapshot`, sygnał `Changed` po zdarzeniu, akcje z dokładnymi wywołaniami, zadania `GroupAction` i `Prune` z sygnałem `ActionFinished` (tylko wskazana kategoria, odmowy bez zadania i bez wywołań), `Logs`, `Stats`, `Disk`, `OpenLogs`/`OpenShell` aż do atrapy `docker` uruchomionej przez `launch.py` w atrapie Konsoli (w katalogu domowym), niedostępny socket, `--no-docker`. Testy nigdy nie dotykają prawdziwego Dockera: `daemon/tests/__init__.py` i usługi w testach (`privatebus.py`) dostają nieistniejący `DOCKER_HOST`,
- `daemon/tests/test_models_core.py` — modele lokalne bez Qt na atrapie serwera HTTP w wątku testu: `models.json` (bez pliku, przykład z planu, dwukropki w nazwach modeli i podział tylko przy znanym punkcie, `default` dla ról bez punktu, kontekst ról, zepsuty plik, pominięte punkty i role z ostrzeżeniami, token z `~` jako `Authorization: Bearer` i nigdzie poza nagłówkiem, dane logowania w adresie), klient Ollamy (polecenie systemowe, schemat w `format`, `num_ctx`, `think: false` i ponowienie bez niego, inne 400 bez ponowienia, 404/500/502, nieczytelna i pusta odpowiedź, zamknięty port, limit czasu, `$http_proxy` pomijany, ustawienia czytane przy każdym wywołaniu) i serwera `openai` (`response_format` ze schematem, bez `num_ctx`), stan obu rodzajów (wersja, modele, część na karcie, role `installed`/`available`, tylko GET, serwer niedostępny, wiszący i padający po `/api/version`, nieprzewidziany błąd jednego punktu), adresy nie do rozebrania i z danymi logowania, `default` nie będący nazwą albo wskazujący pominięty punkt (role bez punktu niedostępne, nie na `local`), rola na pominiętym punkcie, token spoza ASCII, przekierowanie (druga atrapa nie dostaje zapytania ani tokenu), jeden termin na całe zapytanie (serwer sączący bajty, wolne szukanie nazwy). Prawdziwej Ollamy testy nie pytają: `daemon/tests/__init__.py` ustawia `TIDE_MODELS_CONFIG` na `fixtures/models-closed.json` (zamknięty port),
- `daemon/tests/test_models_settings.py` — zapis ustawień bez Qt (`models/settings.py`): pierwszy zapis od wartości domyślnych (role działają jak bez pliku), zmiana z zachowaniem nieznanych kluczy i 0600, walidacja punktu i roli z tymi samymi komunikatami co odczyt, plik tokenu 0600 w katalogu 0700 i token nigdzie poza nim, pusty token zostaje, `clearToken`, wycofanie tokenu przy nieudanym zapisie (stary plik nietknięty, bez plików tymczasowych), zepsuty plik nigdy nie nadpisany, zasady usuwania (domyślny, jedyny, przypięte role), przełączenie `default` przenosi role bez punktu, a przypięte zostają, `ctx` (bez zmiany, `null`, 0, złe), ostrzeżenia z ręcznie edytowanego pliku nie blokują zapisu, dowiązanie symboliczne; `report.json` (inne klucze i prawa zostają, nowy plik, nieznany tryb, zepsuty plik, `report` w stanie); `TestEndpoint` bez zapisu (token tylko w pamięci, na atrapie i zamkniętym porcie); `PullModel` na atrapie strumienia (postęp sumą warstw, błąd w strumieniu, przerwany strumień, 500, limit ciszy, `Cancel`),
- `daemon/tests/test_models_monitor.py` — `ModelsMonitor` w pętli Qt z atrapą odczytu: `Refresh()` w trakcie odczytu daje jeden odczyt zaraz po nim, `Changed` tylko przy zmianie, błąd odczytu zostawia ostatni stan, nieczytelne ustawienia przy starcie nie zatrzymują usługi; zapis: odczyt sprzed zapisu nie cofa widoku, odmowa bez `Changed`; pobieranie: `Changed` z postępem najwyżej raz na sekundę, wpis znika po chwili, `stop()` przerywa pobieranie; kolejka: pole `queue` (bezczynne punkty, trwające i czekające z wątków roboczych, bez treści poleceń) i `Changed` z powodu kolejki najwyżej raz na odstęp, odłączenie od kolejki przy `stop()`,
- `daemon/tests/test_models_queue.py` — kolejka modeli: jedno wywołanie naraz na punkt i dwa punkty równolegle, więcej miejsc na punkt, priorytety i kolejność przyjścia bez przerywania trwającego, grupowanie modeli (i jego limit, i pierwszeństwo priorytetu), koniec okna grupowania między ocenami dwóch czekających (podmieniony zegar — nikt nie śpi przy wolnym miejscu), limit czekania osobno od limitu wywołania z powodem w komunikacie, błąd zwalnia punkt, `Cancel` zdejmuje czekające od razu i przerywa trwające, brak własnych wątków i haków w `Cancel`; `chat()` na atrapie obsługującej jedno zapytanie naraz (`timeout` od startu, `deadline` dla czekania i wywołania razem, `queueS`, dwie nazwy punktu na jednym serwerze dzielą miejsce, cel z `models.json` zmienionego w czasie czekania); raport i opisy kart naraz — karta czeka w kolejce zamiast w serwerze i dostaje opis modelu (bez kolejki ten test kończy się mylącym „nie odpowiedział”), karta przed resztą raportu, budżet raportu z czekaniem, uczciwa informacja na karcie po limicie czekania, `Describer.stop()` zdejmuje czekające,
- `daemon/tests/test_models_dbus.py` — `/Models` na prywatnej magistrali z atrapą Ollamy i własnym `models.json`: `Status()` (punkt działający z tokenem i niedziałający), `Changed` tylko po zmianie (załadowany model), `Refresh()` po zmianie ustawień bez restartu, token tylko w nagłówku, introspekcja, `--no-models`, `--print-models` (tylko GET, niczego nie zapisuje); zapis z D-Bus we własnym `XDG_CONFIG_HOME` (start niczego nie zapisuje, `SetEndpoint` z tokenem i od razu sprawdzony punkt, `SetRole`, `SetDefault` przenosi role, odmowy `RemoveEndpoint`, `SetReportSummary` działa od razu i zachowuje inne klucze, `--report-summary` zmienia tylko plik), `TestEndpoint` (atrapa, zamknięty port, wolny serwer nie blokuje `Status()`), `PullModel` (postęp w `pulls`, jedno pobieranie na punkt, odmowa dla `openai`). Usługi w testach dostają `models.json` z zamkniętym portem (`privatebus.py`), więc nigdy nie pytają Ollamy z pulpitu,
- `daemon/tests/test_activity_look.py` — wygląd aktywności (PT-35) z atrapą uruchamiania silnika i zegarami sterowanymi z testu (bez zależności od czasu): kontrola przy starcie bez aktywności, szybkie przełączenia (jedno nałożenie, ostatnia aktywność), przełączenia w trakcie nałożenia (czekają, nic równolegle), ponowienie po kodzie 75 z tą samą aktywnością i rosnącą przerwą, inne błędy czekają na zdarzenie, nowe przełączenie zastępuje ponowienie, obecność silnika przy każdym uruchomieniu, zabicie nałożenia przy zatrzymaniu, brak silnika; prawdziwy `QProcess` z atrapą silnika (kod i wyjście); `CurrentActivityChanged` od atrapy ActivityManagera na prywatnej magistrali. Silnik sam: `scripts/tests/test_activity_look.py`,
- `daemon/tests/test_install.py`, `daemon/tests/test_krunner_install.py`, `widgets/tests/` — instalacja i odinstalowanie na tymczasowym HOME z atrapami `systemctl`, `kpackagetool6` i `qdbus6`.

Fixtures są pisane ręcznie i naśladują tylko strukturę wpisów — nie zawierają prawdziwych rozmów.


### Review i poprawianie propozycji planu (PT-80, PT-63)

Po `plan.proposed` plan czeka na krok `plan-review` u innego wykonawcy i modelu niż planujący.
Recenzent dostaje pełną zachowaną specyfikację, opis karty i kartki oraz kryteria pionowości,
rozmiaru, mierzalności kryteriów, blokerów i przydziałów. Ocenia też treść części „Dla recenzenta”.
Zapisuje `out/plan-review.json` z `verdict` (`APPROVE` albo `REQUEST_CHANGES`) i `findings`:

```json
{"verdict": "REQUEST_CHANGES",
 "findings": [{"severity": "major", "text": "#7 i #8 zmieniają to samo okno w jednej fali.", "tickets": ["#7", "#8"]}]}
```

`severity` ∈ `blocker` (plan nie może ruszyć bez poprawy), `major` (ważna zmiana przed startem), `nit`
(drobiazg); `tickets` to numery ticketów z planu, których uwaga dotyczy (może być pusta). Walidator
(`cardflow/findings.py`) przyjmuje też stary format — jeden napis w `findings` (karty w toku): staje się jedną
uwagą bez wagi (`severity: ""`, bez ticketów). Format błędny (nieznana waga, uwaga bez tekstu, `tickets` nie
będące listą napisów) kończy krok błędem `bad-plan-review` z powodem; review nie zostaje zapisane.
`plan.reviewed` przechowuje listę uwag, a stan karty daje `flow.plan.review{verdict, reviewer, findings[]}`
(`verdict` pusty, gdy go brak lub jest nieznany).

Uwagi uruchamiają jedną rundę `plan-revise`; bez człowieka w pętli poprawka dostaje to, co okno zaznacza
domyślnie (`blocker` i `major`, bez `nit`; gdy są same drobne — wszystkie). Po poprawce plan jest pokazany
z wynikiem review, bez kolejnego review w pętli. `ApproveCardPlan` przed końcem review zwraca `review-pending`.

Okno decyzji (`plan-approval`) pokazuje werdykt i `findings[]` (waga, tekst, tickety, `selected`).
Polecona akcja zależy od werdyktu: brak uwag → „Zatwierdź plan”; blocker albo `REQUEST_CHANGES` → „Odeślij
uwagi do poprawy”; nieznany werdykt → żadna nie jest polecona. Zatwierdzenie mimo uwag działa zawsze, a jego
skutek jest opisany („Mimo N blokujących uwag … uwagi nie zostaną poprawione”). `EditCardPlan` z `action: return`
przyjmuje `findings` (zaznaczone uwagi) i/lub `feedback` (własny tekst); do planującego trafia tylko to.

`EditCardPlan` zapisuje zmiany przydziału i treści kartki jako `plan.ticket-edited`.
Nie przyjmuje zmian struktury planu tą drogą. Ticket z rozpoczętą gałęzią lub krokiem jest chroniony
przed edycją również po restarcie. Taka zmiana zwraca `ticket-started`.
Ponowienie identycznej edycji nie dopisuje drugiego zdarzenia.

`action: return` zapisuje `plan.returned` z uwagami i bazą porównania. Krok `plan-amend` przygotowuje
nową propozycję; usługa oblicza dodane, usunięte i zmienione tickety oraz pola zmian.
`plan.revision-ready` przechowuje różnicę; panel pokazuje ją przed zatwierdzeniem. Nowa propozycja
przechodzi review. `ApproveCardPlan` zatwierdza różnicę (`plan.revision-approved`), zachowuje
istniejące ID i rezerwuje numery tylko dla dodanych ticketów. Usługa ponownie sprawdza ochronę
rozpoczętych ticketów przy akceptacji. Trwające kroki nie są zatrzymywane.

`derived.flow.plan` zawiera `review`, `revision`, `plannerStep` oraz `body` i `editable` dla wierszy.
Stan `reviewing` ukrywa propozycję w panelu do zakończenia review i ewentualnej jednej poprawy.
„Wejdź w planowanie” wykorzystuje istniejącą sesję planującego i wspólny mechanizm Konsoli.

Błędna poprawka planu daje `plan.revision-failed`; panel udostępnia ponowne odesłanie z uwagami
albo `EditCardPlan` z `action: discard-revision`. Odrzucenie zapisuje `plan.revision-discarded`
i zachowuje aktywny plan. Pytanie, odmowa albo drugi błąd kroku poprawy/review pozostaje dostępny
przez `ResolveCardStep`, także gdy w innym worktree pracuje ticket. Oczekująca różnica ma własne
proponowane fale i utrzymuje kartę w „Czeka na mnie” również po zakończeniu starego planu.

### Konflikty i domknięcie karty (PT-70)

Konflikt `merge-tree` uruchamia krok `conflict` w worktree karty. Agent scala zatwierdzony commit
ticketu i rozwiązuje pliki; usługa przyjmuje wynik tylko wtedy, gdy dokładny commit ticketu i wcześniejszy
commit karty są przodkami czystej gałęzi karty. Pytanie albo błąd kroku trafia do panelu; nieudanej próby usługa nie
zgaduje ani nie scala ponownie sama. Worktree z lokalnymi zmianami nie jest usuwany.

Po scaleniu albo odrzuceniu wszystkich ticketów i sprzątnięciu worktree scalonych ticketów krok
`close` zapisuje `summary.md`. Przy `check: manual` wymaga sekcji „Co przetestować ręcznie i jak”;
przy `check: auto` pomija tę listę. Zdarzenie `card.closed` pokazuje podsumowanie w „Do sprawdzenia”.
`check: auto` nie scala do gałęzi głównej. Panel daje jawne „Diff gałęzi”, „Do poprawy…” oraz
„Scal do gałęzi głównej”. Odesłanie z uwagami uruchamia poprawę planu na tej samej gałęzi karty;
dla wejścia „Od razu” usługa tworzy bazę jednoticketowego planu dopiero przy tym żądaniu.
Scalenie do głównej działa w piaskownicy gita, sprawdza czystość checkoutu i zapisuje
`card.main-merged`, a następnie przenosi kartę do Gotowych. Wcześniejsze kroki nie przesuwają
gałęzi głównej.
